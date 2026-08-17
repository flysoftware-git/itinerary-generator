"""
ai_content.py — Multi-provider LLM content generation.

CRITICAL: AI must NEVER generate URLs. This module produces names,
descriptions, schedules, and structured content only. All URLs are
discovered separately by url_discovery.py after this stage completes.
"""
from __future__ import annotations
from datetime import datetime
from difflib import SequenceMatcher
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
import requests
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential
from generator.llm_client import MultiLLMClient, LLMCircuitOpenError

logger = logging.getLogger(__name__)
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# Retrying a KeyError/TypeError/AttributeError/IndexError just burns up to
# ~14s of exponential backoff on a bug that identical inputs will never fix on
# a later attempt -- narrow retries to genuinely transient conditions (network
# errors, malformed LLM output) and let real bugs surface immediately.
# LLMCircuitOpenError is excluded too: once the breaker is open, retrying
# immediately just burns tenacity's own backoff sleeps against a call that's
# guaranteed to fail fast anyway -- let it propagate so the caller's failure
# handling (and the next destination in the queue) sees it right away.
_NON_RETRYABLE_LLM_EXCEPTIONS = (KeyError, TypeError, AttributeError, IndexError, LLMCircuitOpenError)
_retry_transient_llm_errors = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_not_exception_type(_NON_RETRYABLE_LLM_EXCEPTIONS),
)


class AIContentGenerator:
    _CHAIN_NAME_TOKENS = {
        "mcdonald",
        "burger king",
        "chick-fil-a",
        "chick fil a",
        "taco bell",
        "subway",
        "kfc",
        "wendy",
        "domino",
        "pizza hut",
        "papa john",
        "little caesars",
        "starbucks",
        "dunkin",
        "chipotle",
        "panera",
        "arby",
        "sonic",
        "jack in the box",
        "dairy queen",
        "popeyes",
    }

    _FAST_FOOD_TOKENS = {
        "fast food",
        "quick service",
        "drive-thru",
        "drive thru",
        "fried chicken chain",
        "chain restaurant",
    }

    # Mirrors HTMLAssembler._first_sentence's abbreviation list (html_assembler.py)
    # so schedule-text sentence splitting doesn't mistake "St.", "Mt.", "Dr.",
    # etc. for a sentence boundary the way a naive `re.split(r"(?<=[.!?])\s+")`
    # does.
    _SENTENCE_BOUNDARY_ABBREVIATIONS = {
        "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "ft", "vs",
        "etc", "e.g", "i.e", "am", "pm", "us", "uk", "no", "fig",
    }

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences without breaking on abbreviations.

        A period only ends a sentence when the token immediately before it
        is not a known abbreviation (e.g. "St." in "St. George Dinosaur
        Discovery Site") -- otherwise "St." gets counted as its own
        one-word "sentence", which throws off anything that counts or caps
        sentences (see _cap_period_sentences).
        """
        raw = str(text or "").strip()
        if not raw:
            return []

        sentences: list[str] = []
        start = 0
        for index, ch in enumerate(raw):
            if ch not in ".!?":
                continue
            prefix = raw[:index].rstrip()
            last_token = (
                prefix.rsplit(" ", 1)[-1].lower().strip(" ").strip(".,;:!?()[]{}\"'")
                if prefix
                else ""
            )
            if last_token in AIContentGenerator._SENTENCE_BOUNDARY_ABBREVIATIONS:
                continue
            # A real sentence boundary is followed by whitespace or the end
            # of the string (matches the legacy regex's behavior for the
            # non-abbreviation case).
            if index + 1 < len(raw) and not raw[index + 1].isspace():
                continue
            sentence = raw[start : index + 1].strip()
            if sentence:
                sentences.append(sentence)
            start = index + 1

        remainder = raw[start:].strip()
        if remainder:
            sentences.append(remainder)
        return sentences

    # Venues whose name strongly implies fixed daytime operating hours (indoor
    # exhibits, staffed front desk) rather than something realistically open
    # for an after-dinner visit. This is a narrow, name-based heuristic --
    # not a real hours-of-operation model (no such data exists anywhere in
    # the schedule pipeline; see docs/design/schedule-normalization.md's
    # "Physical Reality Model" section) -- so it only guards against the
    # clearest cases rather than attempting general Evening-suitability
    # judgment.
    _EVENING_UNSUITABLE_VENUE_KEYWORDS = (
        "museum",
        "discovery site",
        "visitor center",
        "visitors center",
        "science center",
    )

    @staticmethod
    def _is_evening_unsuitable_venue(attraction: dict[str, Any]) -> bool:
        name = str(attraction.get("name", "") or "").lower()
        attr_type = str(attraction.get("type", "") or "").lower()
        if attr_type == "museum":
            return True
        return any(
            keyword in name for keyword in AIContentGenerator._EVENING_UNSUITABLE_VENUE_KEYWORDS
        )

    def __init__(
        self,
        config_path: Path | str = "config.yaml",
        llm_client: MultiLLMClient | None = None,
    ) -> None:
        import yaml
        with Path(config_path).open() as f:
            self._config = yaml.safe_load(f)
        self._llm = llm_client or MultiLLMClient(config_path)
        self._system_prompt = (PROMPTS_DIR / "system_prompt.txt").read_text(encoding="utf-8")
        self._dest_template = (PROMPTS_DIR / "destination_content.txt").read_text(encoding="utf-8")
        self._drives_template = (PROMPTS_DIR / "scenic_drives.txt").read_text(encoding="utf-8")
        self._what_to_know_template = (PROMPTS_DIR / "what_to_know.txt").read_text(encoding="utf-8")
        self._weather_cache: dict[tuple[float, float, int], tuple[int, int] | None] = {}
        # Cumulative across every normalize_trip_content() call this run --
        # main.py calls it twice (once after initial generation, again
        # after the selective-retry pass regenerates a subset of
        # destinations' content). Overwriting here instead of accumulating
        # meant runtime_metrics["banned_phrase_violations"] (frozen right
        # after the FIRST call) went stale the moment retry ran a second
        # pass, showing neither the first pass's real findings nor the
        # second's -- found 2026-08-15 comparing a real run's console log
        # (from the second call) against its persisted runtime_metrics
        # (from the first): completely different phrase sets and counts
        # for content that was, in the end, genuinely clean either way.
        self.last_banned_phrase_violations: dict[str, int] = {}
        self._enable_url_candidate_experiment = bool(
            self._config.get("ai", {}).get("enable_url_candidate_experiment", False)
        )
        ai_cfg = self._config.get("ai", {}) or {}
        try:
            self._max_concurrent_destinations = max(1, int(ai_cfg.get("max_concurrent_destinations", 4)))
        except (TypeError, ValueError):
            self._max_concurrent_destinations = 4
        try:
            self._grok_max_concurrent_destinations = max(
                1,
                int(ai_cfg.get("grok_max_concurrent_destinations", 1)),
            )
        except (TypeError, ValueError):
            self._grok_max_concurrent_destinations = 1

    def _llm_stage_max_workers(self, destination_count: int) -> int:
        provider = str(getattr(self._llm, "provider", "") or "").strip().lower()
        if provider == "grok":
            return max(1, min(destination_count, self._grok_max_concurrent_destinations))
        return max(1, min(destination_count, self._max_concurrent_destinations))

    def generate_destination_content(self, trip: dict[str, Any]) -> None:
        """Generate AI content for every destination. Attaches 'ai_content',
        'what_to_know', and 'scenic_drives' in-place (one combined LLM call
        per destination — see _generate_destination_bundle)."""
        destinations = trip.get("destinations", [])
        prev_names = ["none"] + [d["name"] for d in destinations[:-1]]
        next_names = [d["name"] for d in destinations[1:]] + [""]

        def _one(args: tuple[int, dict]) -> None:
            i, dest = args
            logger.info("Generating AI content for '%s'…", dest["name"])
            bundle = self._generate_destination_bundle(dest, trip["trip"], prev_names[i], next_names[i])
            dest["ai_content"] = bundle["destination_content"]
            dest["what_to_know"] = bundle["what_to_know"]
            dest["scenic_drives"] = bundle["scenic_drives"]
            logger.debug(f"  Set scenic_drives for {dest['name']}: {len(bundle['scenic_drives'])} drives")

        with ThreadPoolExecutor(max_workers=self._llm_stage_max_workers(len(destinations))) as pool:
            futures = [pool.submit(_one, (i, d)) for i, d in enumerate(destinations)]
            for f in as_completed(futures):
                f.result()

    def generate_all(self, trip: dict[str, Any]) -> None:
        self.generate_destination_content(trip)

    # Marketing-cliché phrases banned from generated prose (system_prompt.txt's
    # "Avoid without exception" list, kept in sync with prompts/scenic_drives.txt's
    # own list -- the two had drifted apart before this was unified). The prompt
    # instruction alone is routinely violated: an observed real run had 28
    # occurrences despite "without exception" phrasing, with zero downstream
    # enforcement existing before this. "must-see" is deliberately excluded --
    # it's a structured badge label gated on verified rating data elsewhere
    # (html_assembler.py), not a subjective prose claim.
    BANNED_MARKETING_PHRASES = (
        "hidden gem",
        "off the beaten path",
        "world-class",
        "iconic",
        "stunning",
        "breathtaking",
        "charming",
        "nestled",
        "boasts",
        "spectacular",
        "majestic",
    )
    _BANNED_PHRASE_PATTERN = re.compile(
        r"\b(?:" + "|".join(re.escape(p) for p in BANNED_MARKETING_PHRASES) + r")\b",
        re.IGNORECASE,
    )
    # Safety net for the common sentence-final predicate pattern these clichés
    # often appear in ("is a hidden gem.", "is off the beaten path.") -- after
    # the phrase itself is removed, this drops the now-dangling copula rather
    # than leaving "is a." or "is." Known gap: a mid-sentence verb usage of
    # "boasts" (e.g. "the inn boasts a patio") would leave a dangling subject
    # with no verb -- not observed in practice yet, so not specially handled.
    _DANGLING_COPULA_PATTERN = re.compile(
        r"\b(?:is|are|was|were|remains?|becomes?)\s+(?:an?\s*)?(?=[.,;:!?]|$)",
        re.IGNORECASE,
    )
    # Only these field names hold free-form prose; everything else (name,
    # title, url, type, difficulty, cuisine, price_range, numeric fields...)
    # must never be touched -- a restaurant genuinely named "The Charming
    # Cafe" must survive intact.
    _PROSE_FIELD_NAMES = frozenset(
        {
            "description",
            "practical_note",
            "summary",
            "local_customs",
            "best_times_of_day",
            "transportation_quirks",
            "route_summary",
            "best_time",
        }
    )

    @classmethod
    def _strip_banned_marketing_language(cls, text: str, violation_counts: dict[str, int] | None = None) -> str:
        raw = str(text or "")
        if not raw or not cls._BANNED_PHRASE_PATTERN.search(raw):
            return raw
        if violation_counts is not None:
            for match in cls._BANNED_PHRASE_PATTERN.finditer(raw):
                key = match.group(0).lower()
                violation_counts[key] = violation_counts.get(key, 0) + 1
        cleaned = cls._BANNED_PHRASE_PATTERN.sub("", raw)
        cleaned = cls._DANGLING_COPULA_PATTERN.sub("", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
        return cleaned.strip()

    @classmethod
    def _scrub_banned_language_in_place(cls, obj: Any, violation_counts: dict[str, int]) -> None:
        """Recursively walk a dict/list structure, rewriting only allowlisted
        prose fields in place. Structural fields (name, url, type, ...) are
        never visited for rewriting, only traversed into."""
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in cls._PROSE_FIELD_NAMES and isinstance(value, str):
                    obj[key] = cls._strip_banned_marketing_language(value, violation_counts)
                else:
                    cls._scrub_banned_language_in_place(value, violation_counts)
        elif isinstance(obj, list):
            for item in obj:
                cls._scrub_banned_language_in_place(item, violation_counts)

    def _enforce_banned_marketing_language(self, trip: dict[str, Any]) -> dict[str, int]:
        if not hasattr(self, "last_banned_phrase_violations"):
            self.last_banned_phrase_violations = {}
        violation_counts: dict[str, int] = {}
        for dest in trip.get("destinations", []) or []:
            if not isinstance(dest, dict):
                continue
            for key in ("ai_content", "what_to_know", "scenic_drives"):
                if key in dest:
                    self._scrub_banned_language_in_place(dest[key], violation_counts)
        if violation_counts:
            total = sum(violation_counts.values())
            logger.info(
                "Banned marketing-cliche enforcement: removed %d occurrence(s) across %d phrase(s): %s",
                total,
                len(violation_counts),
                violation_counts,
            )
        # Accumulate, don't overwrite -- see last_banned_phrase_violations'
        # own comment for why (this method runs more than once per real run).
        for phrase, count in violation_counts.items():
            self.last_banned_phrase_violations[phrase] = (
                self.last_banned_phrase_violations.get(phrase, 0) + count
            )
        return violation_counts

    def normalize_trip_content(self, trip: dict[str, Any]) -> None:
        """Post-generation normalization: cross-section and cross-destination dedup.

        Runs after all parallel stages (events, images, URL discovery) complete
        so that both what_to_know and cultural_events data are available.
        """
        self._enforce_banned_marketing_language(trip)
        self._deduplicate_cross_section_tips(trip)
        self._deduplicate_cross_destination_what_to_know(trip)
        self._filter_oversized_scenic_drives(trip)
        self._filter_departure_aligned_drives(trip)
        self._deduplicate_cross_destination_scenic_drives(trip)

    def _deduplicate_cross_destination_scenic_drives(self, trip: dict[str, Any]) -> None:
        """Keep duplicate scenic drives only under the most relevant destination.

        Regression guard: a destination-specific drive (e.g., Zion Canyon Scenic
        Drive) should not remain duplicated under an unrelated destination.
        """
        destinations = trip.get("destinations", [])
        if not isinstance(destinations, list) or len(destinations) < 2:
            return

        def _norm_title(title: str) -> str:
            return re.sub(r"[^a-z0-9]+", " ", str(title or "").lower()).strip()

        def _dest_tokens(name: str) -> set[str]:
            raw = re.findall(r"[a-z0-9]+", str(name or "").lower())
            stop = {"national", "state", "park", "city", "county", "town", "the", "and"}
            return {t for t in raw if len(t) >= 3 and t not in stop}

        owners: dict[str, list[tuple[int, int, dict[str, Any]]]] = {}
        for dest_idx, dest in enumerate(destinations):
            drives = dest.get("scenic_drives", []) if isinstance(dest.get("scenic_drives", []), list) else []
            for drive_idx, drive in enumerate(drives):
                if not isinstance(drive, dict):
                    continue
                title = _norm_title(str(drive.get("title", "") or ""))
                if not title:
                    continue
                owners.setdefault(title, []).append((dest_idx, drive_idx, drive))

        for title_key, entries in owners.items():
            if len(entries) < 2:
                continue

            best: tuple[int, int, int] | None = None  # (score, -dest_idx, entry_pos)
            for entry_pos, (dest_idx, _drive_idx, drive) in enumerate(entries):
                dest_name = str(destinations[dest_idx].get("name", "") or "")
                tokens = _dest_tokens(dest_name)
                drive_blob = (
                    str(drive.get("title", "") or "") + " " +
                    str(drive.get("description", "") or "")
                ).lower()
                score = sum(1 for token in tokens if token in drive_blob)
                candidate = (score, -dest_idx, entry_pos)
                if best is None or candidate > best:
                    best = candidate

            if best is None:
                continue
            keep_entry_pos = best[2]

            for entry_pos, (dest_idx, _drive_idx, drive) in enumerate(entries):
                if entry_pos == keep_entry_pos:
                    continue
                dest = destinations[dest_idx]
                drives = dest.get("scenic_drives", []) if isinstance(dest.get("scenic_drives", []), list) else []
                if drive in drives:
                    drives.remove(drive)
                    dest["scenic_drives"] = drives
                    logger.info(
                        "  Cross-destination scenic dedup: removed '%s' from '%s'",
                        drive.get("title", ""),
                        dest.get("name", ""),
                    )

    def _filter_oversized_scenic_drives(self, trip: dict[str, Any]) -> None:
        """Remove scenic drive entries that exceed daily time budget (PR-024).

        Drives labeled 'full day' or 'all day' are removed unless they are the
        only drive for the destination. A configurable mile cap is also applied.
        """
        import re as _re
        try:
            cfg = self._config.get("url_discovery", {}) or {}
            max_miles = float(cfg.get("max_scenic_drive_miles", 150) or 0)
        except (TypeError, ValueError):
            max_miles = 150.0

        full_day_keywords = ("full day", "all day", "full-day", "all-day")

        for dest in trip.get("destinations", []):
            drives = dest.get("scenic_drives", []) or []
            if not drives:
                continue
            eligible = []
            for drive in drives:
                dist = str(drive.get("distance_or_duration", "") or "").lower()
                if any(kw in dist for kw in full_day_keywords):
                    logger.info(
                        "  Oversized drive filter: removed '%s' in '%s' (full-day keyword)",
                        drive.get("title", ""), dest.get("name", ""),
                    )
                    continue
                if max_miles > 0:
                    m = _re.search(r"(\d+(?:\.\d+)?)[\s-]*(miles|mi)", dist)
                    if m and float(m.group(1)) > max_miles:
                        logger.info(
                            "  Oversized drive filter: removed '%s' in '%s' (%.0f mi > %.0f mi)",
                            drive.get("title", ""), dest.get("name", ""),
                            float(m.group(1)), max_miles,
                        )
                        continue
                eligible.append(drive)
            dest["scenic_drives"] = eligible

    def _filter_departure_aligned_drives(self, trip: dict[str, Any]) -> None:
        """Remove one-way scenic drives from the last destination when they align
        with the return route rather than being in-stay activities (PR-029)."""
        destinations = trip.get("destinations", [])
        if not destinations:
            return
        last_dest = destinations[-1]
        return_name = str(trip.get("trip", {}).get("return", "") or "").strip()
        if not return_name:
            return

        return_tokens = {
            t for t in re.findall(r"[a-z]{4,}", return_name.lower())
            if t not in {"national", "state", "city", "town"}
        }
        if not return_tokens:
            return

        drives = last_dest.get("scenic_drives", []) or []
        departure_options: list[dict[str, Any]] = []
        eligible = []
        for drive in drives:
            dist = str(drive.get("distance_or_duration", "") or "").lower()
            is_one_way = "one-way" in dist or "one way" in dist
            if not is_one_way:
                eligible.append(drive)
                continue
            title_and_desc = (
                str(drive.get("title", "") or "") + " " +
                str(drive.get("description", "") or "")
            ).lower()
            if any(token in title_and_desc for token in return_tokens):
                logger.info(
                    "  Departure-aligned drive moved to getting_there: '%s' in '%s' (one-way toward '%s')",
                    drive.get("title", ""), last_dest.get("name", ""), return_name,
                )
                departure_options.append(drive)
            else:
                eligible.append(drive)
        last_dest["scenic_drives"] = eligible

        if departure_options:
            ai = last_dest.get("ai_content", {}) if isinstance(last_dest.get("ai_content", {}), dict) else {}
            getting_there = ai.get("getting_there", {}) if isinstance(ai.get("getting_there", {}), dict) else {}
            existing_options = getting_there.get("route_options", []) if isinstance(getting_there.get("route_options", []), list) else []

            seen_titles = {str(opt.get("title", "") or "").strip().lower() for opt in existing_options}
            merged_options = list(existing_options)
            for option in departure_options:
                option = dict(option)
                registry_meta = option.get("_registry", {}) if isinstance(option.get("_registry", {}), dict) else {}
                registry_meta.update({
                    "ownership_type": "transfer_leg",
                    "section_target": "getting_there.route_options",
                    "validation_status": "accepted",
                })
                option["_registry"] = registry_meta
                key = str(option.get("title", "") or "").strip().lower()
                if key and key not in seen_titles:
                    merged_options.append(option)
                    seen_titles.add(key)

            getting_there["route_options"] = merged_options
            if return_name and not str(getting_there.get("route_summary", "") or "").strip():
                getting_there["route_summary"] = f"Departure leg toward {return_name}."
            ai["getting_there"] = getting_there
            last_dest["ai_content"] = ai

    @staticmethod
    def _cap_period_sentences(
        days: list[dict[str, Any]],
        max_sentences: int = 3,
    ) -> list[dict[str, Any]]:
        r"""Truncate each schedule period to at most max_sentences sentences (PR-005).

        Prevents over-packed AI periods from surfacing as unrealistic activity lists.

        Uses the abbreviation-aware _split_sentences so a mid-summary "St.",
        "Mt.", "Dr.", etc. isn't miscounted as its own sentence -- a naive
        `re.split(r"(?<=[.!?])\s+")` would inflate the sentence count and
        wrongly truncate real trailing content that was well within the cap.
        """
        if max_sentences <= 0:
            return days
        for day in days:
            for period in day.get("periods", []) or []:
                summary = str(period.get("summary", "") or "").strip()
                if not summary:
                    continue
                sentences = AIContentGenerator._split_sentences(summary)
                if len(sentences) > max_sentences:
                    period["summary"] = " ".join(sentences[:max_sentences]).strip()
        return days

    def _deduplicate_cross_section_tips(self, trip: dict[str, Any]) -> None:
        """Strip Cultural Events prose echoes from what_to_know fields.

        Policy: keep cultural_events as canonical event context and remove the
        duplicated text from what_to_know instead of deleting event prose.
        """
        import re as _re

        fallback_defaults = {
            "local_customs": "Follow posted rules, respect quiet areas, and support local businesses with patience during peak windows.",
            "best_times_of_day": "Early morning and late afternoon usually provide easier parking and calmer conditions.",
            "transportation_quirks": "Plan for limited parking in core areas and possible shuttle, permit, or reservation constraints.",
            "safety_considerations": "Carry water, layers, and navigation backup; check alerts and avoid pushing exposure during heat or storms.",
            "crowd_patterns": "Midday is often busiest near major trailheads and viewpoints; quieter windows are usually early and late.",
            "local_etiquette": "Yield politely on narrow paths, keep noise low at viewpoints, and pack out all trash.",
        }

        def _normalize_space(text: str) -> str:
            return " ".join(str(text or "").split()).strip()

        def _strip_duplicate_block(base: str, duplicate: str) -> str:
            if not duplicate:
                return base
            pattern = _re.compile(_re.escape(duplicate), _re.IGNORECASE)
            return _normalize_space(pattern.sub(" ", base))

        for dest in trip.get("destinations", []):
            what_to_know = dest.get("what_to_know") if isinstance(dest.get("what_to_know"), dict) else {}
            cultural_events = dest.get("cultural_events") if isinstance(dest.get("cultural_events"), dict) else {}
            if not what_to_know or not cultural_events:
                continue

            # Prevent the Cultural Events assessment/tip block from being echoed
            # in What to Know fields (e.g., as an extra paragraph after Local etiquette).
            duplicate_blocks = [
                _normalize_space(cultural_events.get("honest_assessment", "")),
                _normalize_space(cultural_events.get("local_tip", "")),
            ]
            duplicate_blocks = [blk for blk in duplicate_blocks if len(blk) >= 30]

            for field in (
                "summary",
                "local_customs",
                "best_times_of_day",
                "transportation_quirks",
                "safety_considerations",
                "crowd_patterns",
                "local_etiquette",
            ):
                current = _normalize_space(what_to_know.get(field, ""))
                if not current:
                    continue
                cleaned = current
                for duplicate in duplicate_blocks:
                    cleaned = _strip_duplicate_block(cleaned, duplicate)
                if cleaned != current:
                    if not cleaned and field in fallback_defaults:
                        cleaned = fallback_defaults[field]
                    what_to_know[field] = cleaned
                    logger.info(
                        "  Cross-section dedup: stripped cultural-events duplicate from what_to_know.%s for '%s'",
                        field,
                        dest.get("name", ""),
                    )

    def _deduplicate_cross_destination_what_to_know(self, trip: dict[str, Any]) -> None:
        """Replace what_to_know fields whose value is verbatim-identical across 2+ destinations."""
        destinations = trip.get("destinations", [])
        if len(destinations) < 2:
            return

        fallback_defaults = {
            "local_customs": "Follow posted rules, respect quiet areas, and support local businesses with patience during peak windows.",
            "best_times_of_day": "Early morning and late afternoon usually provide easier parking and calmer conditions.",
            "transportation_quirks": "Plan for limited parking in core areas and possible shuttle, permit, or reservation constraints.",
            "safety_considerations": "Carry water, layers, and navigation backup; check alerts and avoid pushing exposure during heat or storms.",
            "crowd_patterns": "Midday is often busiest near major trailheads and viewpoints; quieter windows are usually early and late.",
            "local_etiquette": "Yield politely on narrow paths, keep noise low at viewpoints, and pack out all trash.",
        }

        for field, fallback in fallback_defaults.items():
            value_counts: dict[str, int] = {}
            for dest in destinations:
                wk = dest.get("what_to_know") if isinstance(dest.get("what_to_know"), dict) else {}
                val = str((wk or {}).get(field, "") or "").strip()
                if val and val != fallback:
                    value_counts[val] = value_counts.get(val, 0) + 1

            repeated = {v for v, c in value_counts.items() if c >= 2}
            if not repeated:
                continue

            for dest in destinations:
                wk = dest.get("what_to_know") if isinstance(dest.get("what_to_know"), dict) else {}
                if not wk:
                    continue
                val = str(wk.get(field, "") or "").strip()
                if val in repeated:
                    wk[field] = fallback
                    logger.info(
                        "  Cross-destination dedup: reset repeated what_to_know.%s for '%s'",
                        field,
                        dest.get("name", ""),
                    )

    @staticmethod
    def _render_prompt_template(template: str, **values: Any) -> str:
        """Replace only known {placeholders} and leave literal JSON braces intact."""
        rendered = template
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", str(value))
        return rendered

    def _normalize_what_to_know(self, payload: Any, dest: dict[str, Any]) -> dict[str, str]:
        if not isinstance(payload, dict):
            payload = {}

        required_fields = [
            "summary",
            "local_customs",
            "best_times_of_day",
            "transportation_quirks",
            "safety_considerations",
            "crowd_patterns",
            "local_etiquette",
        ]

        normalized: dict[str, str] = {}
        for field in required_fields:
            normalized[field] = str(payload.get(field, "") or "").strip()

        if not normalized["summary"]:
            season = self._season_from_dates(dest.get("dates", ""))
            normalized["summary"] = (
                f"{dest.get('name', 'This destination')} in {season} can vary by hour and conditions; "
                "confirm weather, trail status, and operating hours the day before arrival."
            )

        fallback_defaults = {
            "local_customs": "Follow posted rules, respect quiet areas, and support local businesses with patience during peak windows.",
            "best_times_of_day": "Early morning and late afternoon usually provide easier parking and calmer conditions.",
            "transportation_quirks": "Plan for limited parking in core areas and possible shuttle, permit, or reservation constraints.",
            "safety_considerations": "Carry water, layers, and navigation backup; check alerts and avoid pushing exposure during heat or storms.",
            "crowd_patterns": "Midday is often busiest near major trailheads and viewpoints; quieter windows are usually early and late.",
            "local_etiquette": "Yield politely on narrow paths, keep noise low at viewpoints, and pack out all trash.",
        }
        for key, fallback in fallback_defaults.items():
            if not normalized[key]:
                normalized[key] = fallback

        return normalized

    def _season_from_dates(self, dates: str) -> str:
        month = self._extract_month_index(dates)
        if month in (12, 1, 2):
            return "winter"
        if month in (3, 4, 5):
            return "spring"
        if month in (6, 7, 8):
            return "summer"
        if month in (9, 10, 11):
            return "fall"
        return "the current season"

    def _nearby_day_window(self, dates: str) -> str:
        inferred = self._infer_day_count(dates)
        if inferred <= 1:
            return "single-day stop"
        if inferred == 2:
            return "two-day stay"
        return f"{inferred}-day stay"

    @staticmethod
    def _infer_region_for_destination(name: str) -> str:
        region_map = {
            "utah": "Utah", "colorado": "Colorado", "new mexico": "New Mexico",
            "arizona": "Arizona", "nevada": "Nevada", "california": "California",
        }
        name_lower = str(name or "").lower()
        return next((v for k, v in region_map.items() if k in name_lower), "Western United States")

    @_retry_transient_llm_errors
    def _generate_destination_bundle(
        self, dest: dict[str, Any], trip_meta: dict[str, Any], prev: str, next_dest: str
    ) -> dict[str, Any]:
        seeds = dest.get("seeds", [])
        destination_prompt = self._dest_template.format(
            destination_name=dest["name"],
            dates=dest["dates"],
            trip_title=trip_meta["title"],
            previous_destination=prev,
            next_destination=next_dest or "none",
            budget_guidance=self._build_budget_guidance(trip_meta),
            seeds="\n  ".join(f"- {s}" for s in seeds) if seeds else "  (none — generate full recommendations)",
        )
        what_to_know_prompt = self._render_prompt_template(
            self._what_to_know_template,
            destination_name=dest.get("name", ""),
            dates=dest.get("dates", ""),
            season=self._season_from_dates(dest.get("dates", "")),
            nearby_days=self._nearby_day_window(dest.get("dates", "")),
            trip_type=str(trip_meta.get("subtitle", "") or trip_meta.get("title", "") or "road trip").strip(),
            previous_destination=prev,
            next_destination=next_dest or "none",
            budget_guidance=self._build_budget_guidance(trip_meta),
        )
        # Folded in from what used to be a separate scenic-drives call/stage
        # (generate_scenic_drive_descriptions) -- combining all three into one
        # LLM call per destination halves Stage 3's call count and removes an
        # entire second serialized pass over every destination.
        drives_prompt = self._drives_template.format(
            destination_name=dest["name"],
            dates=dest["dates"],
            region=self._infer_region_for_destination(dest["name"]),
        )
        timing_context = self._build_trip_timing_context(
            trip_meta=trip_meta,
            destination_name=str(dest.get("name", "") or ""),
            destination=dest,
            previous_destination=prev,
            next_destination=next_dest,
        )
        timing_note = ""
        if timing_context:
            timing_note = (
                "\n\nTrip timing anchors (enforce workable sequencing):\n"
                + timing_context
                + "\nKeep Day 1 and final-day activities feasible against these anchors."
            )
        prompt = (
            "Return JSON only with exactly three top-level keys: destination_content, "
            "what_to_know, and scenic_drives.\n\n"
            "DESTINATION CONTENT REQUEST:\n"
            f"{destination_prompt}{timing_note}\n\n"
            "WHAT TO KNOW REQUEST:\n"
            f"{what_to_know_prompt}{timing_note}\n\n"
            "SCENIC DRIVES REQUEST:\n"
            f"{drives_prompt}"
        )
        configured_max_tokens = self._config.get("ai", {}).get(
            "max_tokens", self._config.get("azure_openai", {}).get("max_tokens", 4096)
        )
        result = self._llm.generate_json(
            system_prompt=self._system_prompt,
            user_prompt=prompt,
            operation=f"destination_bundle:{dest['id']}",
            temperature=self._config.get("ai", {}).get("temperature", self._config.get("azure_openai", {}).get("temperature", 0.7)),
            # +2048 covers the scenic-drives section that used to have its own
            # dedicated 2048-token budget as a separate call.
            max_tokens=int(configured_max_tokens) + 2048,
        )
        destination_content = result.get("destination_content", {}) if isinstance(result, dict) else {}
        what_to_know = result.get("what_to_know", {}) if isinstance(result, dict) else {}
        scenic_drives = result.get("scenic_drives", []) if isinstance(result, dict) else []
        if not isinstance(destination_content, dict):
            destination_content = {}
        if not isinstance(what_to_know, dict):
            what_to_know = {}
        if not isinstance(scenic_drives, list):
            scenic_drives = []
        if self._enable_url_candidate_experiment:
            destination_content = self._augment_with_url_candidates(destination_content, dest)
        return {
            "destination_content": self._normalize_destination_content(
                destination_content,
                dest.get("dates", ""),
                dest,
                trip_meta,
                prev,
                next_dest,
            ),
            "what_to_know": self._normalize_what_to_know(what_to_know, dest),
            "scenic_drives": scenic_drives,
        }

    @staticmethod
    def _build_trip_timing_context(
        *,
        trip_meta: dict[str, Any],
        destination_name: str,
        destination: dict[str, Any],
        previous_destination: str,
        next_destination: str,
    ) -> str:
        lines: list[str] = []
        departure_name = str(trip_meta.get("departure", "") or "").strip()
        departure_dt = str(trip_meta.get("departure_datetime", "") or "").strip()
        return_name = str(trip_meta.get("return", "") or "").strip()
        return_dt = str(trip_meta.get("return_datetime", "") or "").strip()

        if departure_name or departure_dt:
            lines.append(f"- Trip departure anchor: {departure_name or 'departure location TBD'} @ {departure_dt or 'time TBD'}")
        if return_name or return_dt:
            lines.append(f"- Trip return anchor: {return_name or 'return location TBD'} @ {return_dt or 'time TBD'}")

        lodging = destination.get("lodging", {}) if isinstance(destination.get("lodging", {}), dict) else {}
        lodging_location = str(lodging.get("location", "") or "").strip()
        lodging_checkin = str(lodging.get("checkin_time", "") or "").strip()
        if lodging_location or lodging_checkin:
            lines.append(
                f"- Lodging anchor for {destination_name}: {lodging_location or 'lodging TBD'} @ {lodging_checkin or 'check-in time TBD'}"
            )

        if previous_destination == "none" and departure_dt:
            lines.append(
                f"- {destination_name}: do not schedule first-day activities before the departure anchor travel window."
            )
        if lodging_checkin:
            lines.append(
                f"- {destination_name}: keep first-day activities feasible around arrival, with lodging check-in near {lodging_checkin}."
            )
        if (not next_destination or next_destination == "none") and return_dt:
            lines.append(
                f"- {destination_name}: reserve realistic buffer for return travel before the return anchor."
            )

        return "\n".join(lines)

    def _build_budget_guidance(self, trip_meta: dict[str, Any]) -> str:
        budget = trip_meta.get("budget")
        if budget in (None, "", {}):
            return "No explicit trip budget provided."

        if isinstance(budget, str):
            return f"Budget preference: {budget}"

        if isinstance(budget, (int, float)):
            return f"Budget cap noted: {budget}"

        if isinstance(budget, dict):
            parts: list[str] = []
            for key, value in budget.items():
                label = str(key).replace("_", " ")
                parts.append(f"{label}={value}")
            if parts:
                return "Budget guidance: " + "; ".join(parts)

        return f"Budget guidance: {budget}"

    def _normalize_destination_content(
        self,
        payload: dict[str, Any],
        dates: str,
        dest: dict[str, Any],
        trip_meta: dict[str, Any],
        previous_destination: str,
        next_destination: str,
    ) -> dict[str, Any]:
        seed_names = [str(s or "").strip() for s in (dest.get("seeds", []) or []) if str(s or "").strip()]
        payload["expected_environment"] = self._normalize_environment(
            payload.get("expected_environment", {}),
            dates,
            dest,
        )
        payload["getting_here"] = self._normalize_getting_here(
            payload.get("getting_here", {}),
            dest.get("name", ""),
        )
        normalized_attractions = self._normalize_attractions(payload.get("top_attractions", []))
        normalized_attractions = self._ensure_seed_attractions(normalized_attractions, seed_names)
        normalized_attractions = self._remove_enroute_stops_from_attractions(
            normalized_attractions,
            payload.get("getting_here", {}),
            protected_names=seed_names,
        )
        payload["top_attractions"] = self._apply_manifest_attraction_target(
            normalized_attractions,
            dates=dates,
            attractions_per_day=self._resolve_attraction_target(dest, trip_meta),
            protected_names=seed_names,
        )
        payload["possible_daily_schedule"] = self._normalize_schedule(
            payload.get("possible_daily_schedule", {}),
            payload.get("dinner_recommendations", []),
            dates,
            payload.get("top_attractions", []),
            payload.get("getting_here", {}),
            previous_destination,
            next_destination,
            str(trip_meta.get("departure", "") or "").strip(),
            str(trip_meta.get("return", "") or "").strip(),
            str(trip_meta.get("departure_datetime", "") or "").strip(),
            str(trip_meta.get("return_datetime", "") or "").strip(),
            str((dest.get("lodging", {}) or {}).get("location", "") or "").strip(),
            str((dest.get("lodging", {}) or {}).get("checkin_time", "") or "").strip(),
            str(trip_meta.get("default_day_start_time", "") or "").strip(),
            str(dest.get("schedule_start_time", "") or "").strip(),
            trip_meta.get("default_daily_activity_hours", 5),
            dest.get("daily_activity_hours", None),
        )
        payload["dinner_recommendations"] = self._normalize_restaurants(
            payload.get("dinner_recommendations", []),
            trip_meta.get("budget"),
        )
        return payload

    def _remove_enroute_stops_from_attractions(
        self,
        attractions: list[dict[str, Any]],
        getting_here: dict[str, Any],
        protected_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        stops = getting_here.get("en_route_stops", []) if isinstance(getting_here, dict) else []
        stop_names = [str(s.get("name", "") or "").strip() for s in stops if isinstance(s, dict)]
        stop_names = [s for s in stop_names if s]
        if not attractions or not stop_names:
            return attractions

        def norm(text: str) -> str:
            n = text.lower().strip()
            n = re.sub(r"[^a-z0-9\s]", " ", n)
            n = re.sub(r"\b(trail|road|highway|route|state\s+park|national\s+park|park|overlook|viewpoint)\b", " ", n)
            n = re.sub(r"\s+", " ", n).strip()
            return n

        stop_norm = [norm(name) for name in stop_names]
        stop_norm = [s for s in stop_norm if s]
        if not stop_norm:
            return attractions

        protected = {
            self._canonical_seed_name(name)
            for name in (protected_names or [])
            if self._canonical_seed_name(name)
        }

        filtered: list[dict[str, Any]] = []
        for attraction in attractions:
            attr_name = str(attraction.get("name", "") or "").strip()
            if self._canonical_seed_name(attr_name) in protected:
                filtered.append(attraction)
                continue
            attr_norm = norm(attr_name)
            if not attr_norm:
                filtered.append(attraction)
                continue

            is_enroute_match = False
            for stop_name in stop_norm:
                if attr_norm == stop_name:
                    is_enroute_match = True
                    break
                # Substring containment is only trustworthy when the shorter
                # name is a substantial fraction of the longer one -- e.g. an
                # en-route stop like "Overlook Point" reduces to just "point"
                # after norm() strips the generic word "overlook", and a bare
                # "point" is a substring of nearly any attraction with "Point"
                # in its name ("Sunset Point", "Inspiration Point", ...),
                # producing false-positive removals unrelated to the actual
                # stop. Require at least half the longer name's length to
                # overlap before trusting containment alone.
                shorter, longer = sorted((attr_norm, stop_name), key=len)
                if shorter and shorter in longer and len(shorter) >= len(longer) * 0.5:
                    is_enroute_match = True
                    break
                if SequenceMatcher(None, attr_norm, stop_name).ratio() >= 0.9:
                    is_enroute_match = True
                    break

            if not is_enroute_match:
                filtered.append(attraction)

        return filtered

    @staticmethod
    def _canonical_seed_name(text: str) -> str:
        n = (text or "").lower().strip()
        n = re.sub(r"[^a-z0-9\s]", " ", n)
        n = re.sub(r"\b(trail|road|highway|route|state\s+park|national\s+park|park|overlook|viewpoint)\b", " ", n)
        n = re.sub(r"\s+", " ", n).strip()
        return n

    def _ensure_seed_attractions(self, attractions: list[dict[str, Any]], seed_names: list[str]) -> list[dict[str, Any]]:
        if not seed_names:
            return attractions

        existing = {
            self._canonical_seed_name(str(item.get("name", "") or ""))
            for item in attractions
            if isinstance(item, dict)
        }

        out = list(attractions)
        for seed in seed_names:
            if self._canonical_seed_name(seed) in existing:
                continue

            seed_lower = seed.lower()
            inferred_type = "hike" if re.search(r"\b(trail|hike|loop|arch|falls|summit|narrows)\b", seed_lower) else "attraction"
            out.append(
                {
                    "name": seed,
                    "type": inferred_type,
                    "difficulty": "N/A",
                    "duration": "",
                    "must_see": False,
                    "description": "Traveler-specified seed attraction; details will be refined by linked references.",
                    "practical_note": "",
                }
            )
            existing.add(self._canonical_seed_name(seed))

        return self._normalize_attractions(out)

    def _normalize_getting_here(self, getting_here: Any, dest_name: str) -> dict[str, Any]:
        if not isinstance(getting_here, dict):
            return {}
        out = dict(getting_here)
        normalized_stops: list[dict[str, Any]] = []
        for stop in out.get("en_route_stops", []) or []:
            if not isinstance(stop, dict):
                continue
            item = dict(stop)
            if item.get("detour_distance_miles") in (None, ""):
                item["detour_distance_miles"] = 0
            if item.get("detour_time_minutes") in (None, ""):
                item["detour_time_minutes"] = 0
            normalized_stops.append(item)
        out["en_route_stops"] = normalized_stops
        if not out.get("route_summary") and out.get("drive_time"):
            out["route_summary"] = f"Arrival leg into {dest_name} typically takes about {out.get('drive_time')}."
        return out

    def _normalize_environment(self, environment: Any, dates: str, dest: dict[str, Any]) -> Any:
        if not isinstance(environment, dict):
            return environment

        month = self._extract_month_index(dates)
        if month is None:
            return environment

        normals = self._get_monthly_temperature_normals(dest.get("lat"), dest.get("lng"), month)
        if not normals:
            return environment

        high_f, low_f = normals
        environment["temperature_high_f"] = high_f
        environment["temperature_low_f"] = low_f

        month_name = datetime(2000, month, 1).strftime("%B")
        grounded_sentence = f"Typical {month_name} temperatures are around {high_f}°F daytime and {low_f}°F overnight."
        summary = str(environment.get("summary", "") or "").strip()

        if summary:
            summary_without_temp = self._remove_temperature_claims(summary)
            environment["summary"] = (
                grounded_sentence if not summary_without_temp
                else f"{grounded_sentence} {summary_without_temp}"
            )
        else:
            environment["summary"] = grounded_sentence

        return environment

    def _get_monthly_temperature_normals(
        self,
        lat: Any,
        lng: Any,
        month: int,
    ) -> tuple[int, int] | None:
        try:
            lat_f = float(lat)
            lng_f = float(lng)
        except (TypeError, ValueError):
            return None

        key = (round(lat_f, 2), round(lng_f, 2), month)
        if key in self._weather_cache:
            return self._weather_cache[key]

        try:
            resp = requests.get(
                "https://archive-api.open-meteo.com/v1/archive",
                params={
                    "latitude": lat_f,
                    "longitude": lng_f,
                    "start_date": "2014-01-01",
                    "end_date": "2023-12-31",
                    "daily": "temperature_2m_max,temperature_2m_min",
                    "temperature_unit": "fahrenheit",
                    "timezone": "UTC",
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json().get("daily", {})
            times = data.get("time", [])
            max_vals = data.get("temperature_2m_max", [])
            min_vals = data.get("temperature_2m_min", [])

            month_max: list[float] = []
            month_min: list[float] = []
            for day, max_v, min_v in zip(times, max_vals, min_vals):
                if not day or max_v is None or min_v is None:
                    continue
                if int(day[5:7]) != month:
                    continue
                month_max.append(float(max_v))
                month_min.append(float(min_v))

            if not month_max or not month_min:
                self._weather_cache[key] = None
                return None

            result = (round(sum(month_max) / len(month_max)), round(sum(month_min) / len(month_min)))
            self._weather_cache[key] = result
            return result
        except Exception as exc:
            logger.warning("Weather normals lookup failed for %.3f, %.3f month=%s: %s", lat_f, lng_f, month, exc)
            self._weather_cache[key] = None
            return None

    def _extract_month_index(self, dates: str) -> int | None:
        month_lookup = {
            "january": 1,
            "february": 2,
            "march": 3,
            "april": 4,
            "may": 5,
            "june": 6,
            "july": 7,
            "august": 8,
            "september": 9,
            "october": 10,
            "november": 11,
            "december": 12,
        }
        for token in re.findall(r"[A-Za-z]+", dates or ""):
            idx = month_lookup.get(token.lower())
            if idx:
                return idx
        return None

    def _remove_temperature_claims(self, text: str) -> str:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        temp_pattern = re.compile(
            r"(°\s*[FC]|fahrenheit|celsius|temperature|temperatures|\bhighs?\b|\blows?\b|\b\d+\s*[-–]\s*\d+\s*°?F\b)",
            flags=re.IGNORECASE,
        )
        kept = [s.strip() for s in sentences if s.strip() and not temp_pattern.search(s)]
        return " ".join(kept).strip()

    def _normalize_attractions(self, attractions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        must_see_budget = 2
        difficulty_rank = {"strenuous": 0, "moderate": 1, "easy": 2, "n/a": 3, "": 4}
        for attraction in attractions:
            item = dict(attraction)
            item_type = str(item.get("type", "attraction") or "attraction").lower()
            item["type"] = item_type
            item_name = str(item.get("name", "") or "").strip()
            item_description = str(item.get("description", "") or "").strip().lower()
            non_tourist_markers = (
                "not a tourist attraction",
                "not tourist attraction",
                "hospital",
                "medical center",
                "urgent care",
                "emergency room",
                "clinic",
            )
            if any(marker in f"{item_name.lower()} {item_description}" for marker in non_tourist_markers):
                continue
            item["url_candidates"] = self._normalize_url_candidates(item.get("url_candidates", []))
            if item.get("must_see") and must_see_budget > 0:
                must_see_budget -= 1
                item["must_see"] = True
            else:
                item["must_see"] = False
            normalized.append(item)

        normalized = self._dedupe_attractions(normalized)

        # Keep genuinely highlighted items first, then order by challenge level.
        normalized.sort(
            key=lambda x: (
                0 if x.get("must_see") else 1,
                difficulty_rank.get(str(x.get("difficulty", "")).lower(), 4),
                str(x.get("name", "")).lower(),
            )
        )
        return normalized

    def _dedupe_attractions(self, attractions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def canonical(name: str) -> str:
            n = (name or "").lower()
            n = n.replace("kolb", "kolob")
            n = re.sub(r"\b(road|rd|trail|hike|loop|route|area|point)\b", " ", n)
            n = re.sub(r"\s+", " ", n).strip()
            return n

        deduped: list[dict[str, Any]] = []
        for item in attractions:
            name = str(item.get("name", "") or "")
            key = canonical(name)
            merged = False
            for existing in deduped:
                existing_key = canonical(str(existing.get("name", "") or ""))
                if not key or not existing_key:
                    continue
                sim = SequenceMatcher(None, key, existing_key).ratio()
                if key == existing_key or sim >= 0.92:
                    if len(str(item.get("description", ""))) > len(str(existing.get("description", ""))):
                        existing["description"] = item.get("description", "")
                    if item.get("must_see"):
                        existing["must_see"] = True
                    if not existing.get("duration") and item.get("duration"):
                        existing["duration"] = item.get("duration")
                    if not existing.get("practical_note") and item.get("practical_note"):
                        existing["practical_note"] = item.get("practical_note")
                    merged = True
                    break
            if not merged:
                deduped.append(item)
        return deduped

    def _normalize_schedule(
        self,
        schedule: Any,
        restaurants: list[dict[str, Any]],
        dates: str,
        attractions: list[dict[str, Any]] | None = None,
        getting_here: dict[str, Any] | None = None,
        previous_destination: str = "",
        next_destination: str = "",
        trip_origin: str = "",
        trip_return: str = "",
        trip_departure_datetime: str = "",
        trip_return_datetime: str = "",
        lodging_location: str = "",
        lodging_checkin_time: str = "",
        default_day_start_time: str = "",
        destination_day_start_time: str = "",
        default_daily_activity_hours: Any = 5,
        destination_daily_activity_hours: Any = None,
    ) -> list[dict[str, Any]]:
        getting_here = getting_here or {}
        restaurant_names = [r.get("name", "") for r in restaurants if r.get("name")]

        def clean_text(text: str) -> str:
            cleaned = str(text)
            cleaned = re.sub(r"^\s*[🌅☀️🌙🗺️]\s*", "", cleaned)
            cleaned = re.sub(r"^\s*(morning|afternoon|evening|plan)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"^\s*\d{1,2}:\d{2}\s*(?:am|pm)?\s*[—-]\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if not cleaned:
                return ""
            if "dinner" in cleaned.lower() and restaurant_names:
                mentions_restaurant = any(name.lower() in cleaned.lower() for name in restaurant_names)
                if not mentions_restaurant:
                    cleaned = re.sub(
                        r"dinner[^.]*",
                        f"dinner at {restaurant_names[0]}",
                        cleaned,
                        count=1,
                        flags=re.IGNORECASE,
                    )
            return cleaned

        if isinstance(schedule, list):
            normalized_days: list[dict[str, Any]] = []
            for index, day in enumerate(schedule, start=1):
                if isinstance(day, dict) and day.get("periods"):
                    periods = []
                    for period in day.get("periods", []):
                        label = str(period.get("period", "")).title()
                        summary = clean_text(period.get("summary", ""))
                        if label and summary:
                            periods.append({"period": label, "summary": summary})
                    if periods:
                        normalized_days.append({
                            "day_label": day.get("day_label") or f"Day {index}",
                            "periods": periods,
                        })
                elif isinstance(day, str):
                    normalized_days.append({
                        "day_label": f"Day {index}",
                        "periods": [{"period": "Plan", "summary": clean_text(day)}],
                    })
            day_count = self._infer_day_count(dates)
            normalized_days = self._expand_days(normalized_days, day_count)
            normalized_days = self._ensure_day_period_coverage(normalized_days, restaurant_names)
            normalized_days = self._cap_period_sentences(normalized_days)
            return self._inject_travel_realism(
                normalized_days,
                getting_here,
                previous_destination,
                next_destination,
                trip_origin,
                trip_return,
                trip_departure_datetime,
                trip_return_datetime,
                lodging_location,
                lodging_checkin_time,
                default_day_start_time,
                destination_day_start_time,
                attractions,
                default_daily_activity_hours,
                destination_daily_activity_hours,
                restaurants=restaurants,
            )

        if isinstance(schedule, dict):
            periods = []
            for key in ["morning", "afternoon", "evening"]:
                value = clean_text(schedule.get(key, ""))
                if value:
                    periods.append({"period": key.title(), "summary": value})
            if periods:
                expanded = self._expand_days([{"day_label": "Day 1", "periods": periods}], self._infer_day_count(dates))
                expanded = self._ensure_day_period_coverage(expanded, restaurant_names)
                return self._inject_travel_realism(
                    expanded,
                    getting_here,
                    previous_destination,
                    next_destination,
                    trip_origin,
                    trip_return,
                    trip_departure_datetime,
                    trip_return_datetime,
                    lodging_location,
                    lodging_checkin_time,
                    default_day_start_time,
                    destination_day_start_time,
                    attractions,
                    default_daily_activity_hours,
                    destination_daily_activity_hours,
                    restaurants=restaurants,
                )

        return []

    def _ensure_day_period_coverage(
        self,
        days: list[dict[str, Any]],
        restaurant_names: list[str],
    ) -> list[dict[str, Any]]:
        if not days:
            return days

        required = ["Morning", "Afternoon", "Evening"]
        fallback_by_period: dict[str, str] = {}
        for day in days:
            for period in day.get("periods", []) or []:
                label = str(period.get("period", "")).title()
                summary = str(period.get("summary", "") or "").strip()
                if label in required and summary and label not in fallback_by_period:
                    fallback_by_period[label] = summary

        dinner_name = restaurant_names[0] if restaurant_names else "a listed local restaurant"
        generic = {
            "Morning": "Start early at a priority attraction to avoid midday crowds and parking pressure.",
            "Afternoon": "Continue with a second major stop and keep transition time for parking and trailhead logistics.",
            "Evening": f"Wrap with sunset viewpoints, then dinner at {dinner_name}.",
        }

        out: list[dict[str, Any]] = []
        for idx, day in enumerate(days, start=1):
            existing: dict[str, str] = {}
            for period in day.get("periods", []) or []:
                label = str(period.get("period", "")).title()
                summary = str(period.get("summary", "") or "").strip()
                if label in required and summary:
                    existing[label] = summary

            completed_periods: list[dict[str, str]] = []
            for label in required:
                summary = existing.get(label) or fallback_by_period.get(label) or generic[label]
                completed_periods.append({"period": label, "summary": summary})

            out.append({
                "day_label": day.get("day_label") or f"Day {idx}",
                "periods": completed_periods,
            })

        return self._dedupe_schedule_day_content(out)

    def _dedupe_schedule_day_content(self, days: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Ensure each day has at least one meaningful content difference from prior days.

        Runs before _inject_travel_realism's attraction-name rotation, so this
        is the only defense against duplication in generic fallback text that
        carries no canonical attraction/restaurant name for that later pass to
        rotate in (e.g. _ensure_day_period_coverage's identical filler string
        reused verbatim across every day missing a period).
        """
        if len(days) <= 1:
            return days

        period_variation_suffix = {
            "Morning": "Prioritize a different trailhead or district than previous days.",
            "Afternoon": "Shift focus to a different area to avoid repeating the same loop.",
            "Evening": "Choose a different sunset zone or dining pocket than earlier nights.",
        }

        # Detect and fix duplication per period, not only when an entire day's
        # periods are all duplicates of prior days -- a day with 2 of 3
        # periods repeated (but one genuinely new) previously triggered
        # nothing at all.
        seen_summaries: set[str] = set()
        for day in days:
            periods = day.get("periods", []) or []
            for period in periods:
                label = str(period.get("period", "")).title()
                summary = str(period.get("summary", "") or "").strip()
                if not summary:
                    continue
                normalized = summary.lower()
                if normalized in seen_summaries:
                    suffix = period_variation_suffix.get(label, "Vary stops and pacing from previous days.")
                    if suffix.lower() not in normalized:
                        summary = f"{summary} {suffix}".strip()
                        period["summary"] = summary
                        normalized = summary.lower()
                seen_summaries.add(normalized)

        return days

    def _inject_travel_realism(
        self,
        days: list[dict[str, Any]],
        getting_here: dict[str, Any],
        previous_destination: str,
        next_destination: str,
        trip_origin: str = "",
        trip_return: str = "",
        trip_departure_datetime: str = "",
        trip_return_datetime: str = "",
        lodging_location: str = "",
        lodging_checkin_time: str = "",
        default_day_start_time: str = "",
        destination_day_start_time: str = "",
        attractions: list[dict[str, Any]] | None = None,
        default_daily_activity_hours: Any = 5,
        destination_daily_activity_hours: Any = None,
        restaurants: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if not days:
            return days

        attractions = attractions or []

        def _is_heavy_activity_block(summary: str) -> bool:
            text = str(summary or "").lower()
            if not text:
                return False
            return bool(
                re.search(
                    r"\b(hike|trail|summit|strenuous|full[- ]day|all[- ]day|backcountry|multi-hour|long\s+drive)\b",
                    text,
                )
            )

        def _is_arrival_logistics_summary(summary: str) -> bool:
            text = str(summary or "").strip().lower()
            if not text:
                return False
            if re.search(r"\b(check[- ]?in|lodging)\b", text):
                return True
            if re.search(r"\btravel from\b.*\barrival\b", text):
                return True
            if re.search(r"\bdrive from\b", text):
                return True
            return bool(re.search(r"\b(arrive|arrival)\b", text))

        is_first_destination = str(previous_destination or "").strip().lower() in {"", "none"}
        is_last_destination = not str(next_destination or "").strip()

        def _set_period_summary(day: dict[str, Any], label: str, summary: str) -> None:
            periods = day.get("periods", []) or []
            for period in periods:
                if str(period.get("period", "")).title() == label:
                    period["summary"] = summary
                    return

        def _extract_hour(raw_dt: str) -> int | None:
            text = str(raw_dt or "").strip().replace("T", " ")
            match = re.match(r"^\d{4}-\d{2}-\d{2}(?:\s+(\d{1,2}):(\d{2})(?:\s*([APap][Mm]))?)?", text)
            if not match or match.group(1) is None:
                return None
            hour = int(match.group(1))
            ampm = str(match.group(3) or "").upper()
            if ampm == "PM" and hour < 12:
                hour += 12
            if ampm == "AM" and hour == 12:
                hour = 0
            return max(0, min(23, hour))

        def _format_anchor_time(raw_dt: str) -> str:
            text = str(raw_dt or "").strip().replace("T", " ")
            match = re.match(r"^\d{4}-\d{2}-\d{2}(?:\s+(\d{1,2}):(\d{2})(?:\s*([APap][Mm]))?)?", text)
            if not match or match.group(1) is None:
                return ""
            hour = int(match.group(1))
            minute = int(match.group(2) or "0")
            ampm = str(match.group(3) or "").upper()
            if ampm:
                return f"{hour}:{minute:02d} {ampm}"
            suffix = "AM" if hour < 12 else "PM"
            hour12 = hour % 12
            if hour12 == 0:
                hour12 = 12
            return f"{hour12}:{minute:02d} {suffix}"

        def _parse_clock_minutes(raw: str) -> int | None:
            text = str(raw or "").strip()
            if not text:
                return None
            match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*([APap][Mm])?$", text)
            if not match:
                return None
            hour = int(match.group(1))
            minute = int(match.group(2) or "0")
            ampm = str(match.group(3) or "").upper()
            if ampm:
                if hour == 12:
                    hour = 0
                if ampm == "PM":
                    hour += 12
            if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                return None
            return hour * 60 + minute

        def _format_minutes_as_time(total_minutes: int) -> str:
            clamped = max(0, min((24 * 60) - 1, total_minutes))
            hour24 = clamped // 60
            minute = clamped % 60
            suffix = "AM" if hour24 < 12 else "PM"
            hour12 = hour24 % 12
            if hour12 == 0:
                hour12 = 12
            return f"{hour12}:{minute:02d} {suffix}"

        def _parse_duration_minutes(raw: str) -> int:
            text = str(raw or "").lower().strip()
            if not text:
                return 0
            total = 0
            found = False

            hr_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)", text)
            if hr_match:
                total += int(round(float(hr_match.group(1)) * 60))
                found = True

            min_match = re.search(r"(\d+)\s*(?:m|min|mins|minute|minutes)", text)
            if min_match:
                total += int(min_match.group(1))
                found = True

            range_match = re.search(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)", text)
            if range_match:
                low = float(range_match.group(1))
                high = float(range_match.group(2))
                total = int(round(((low + high) / 2.0) * 60))
                found = True

            if found:
                return max(0, total)

            bare_hours = re.search(r"\b(\d+(?:\.\d+)?)\b", text)
            if bare_hours:
                return int(round(float(bare_hours.group(1)) * 60))

            return 0

        def _parse_hours_limit(value: Any, fallback_hours: float = 5.0) -> int:
            try:
                hours = float(value)
                if hours <= 0:
                    raise ValueError()
                return int(round(hours * 60))
            except Exception:
                return int(round(fallback_hours * 60))

        def _format_duration_compact(minutes: int) -> str:
            mins = max(0, int(minutes))
            if mins < 60:
                return f"{mins}m"
            h = mins // 60
            m = mins % 60
            if m == 0:
                return f"{h}h"
            return f"{h}h {m}m"

        def _build_multi_activity_afternoon_summary(
            activity_budget_minutes: int, *, start_offset: int = 0, arrival_day: bool = True
        ) -> str:
            if activity_budget_minutes <= 0 or not attractions:
                return ""

            # start_offset rotates which attractions are considered first, so
            # Day 2+ (which shares the same attractions list as Day 1) doesn't
            # greedily pick the exact same set every time it's called.
            ordered = attractions[start_offset:] + attractions[:start_offset] if start_offset else attractions

            picked: list[tuple[str, int]] = []
            remaining = activity_budget_minutes
            for attr in ordered:
                if not isinstance(attr, dict):
                    continue
                name = str(attr.get("name", "") or "").strip()
                if not name:
                    continue
                duration_raw = str(attr.get("duration", "") or "").strip()
                duration_minutes = _parse_duration_minutes(duration_raw)
                if duration_minutes <= 0:
                    duration_minutes = 90
                if duration_minutes > remaining:
                    continue
                picked.append((name, duration_minutes))
                remaining -= duration_minutes
                if len(picked) >= 3 or remaining < 45:
                    break

            if len(picked) < 2:
                return ""

            total_minutes = sum(item[1] for item in picked)
            parts = [f"{name} ({_format_duration_compact(minutes)})" for name, minutes in picked]
            if arrival_day:
                return (
                    f"After arrival, consider one or more of the following, within about {_format_duration_compact(total_minutes)}: "
                    + ", ".join(parts)
                    + ". Keep the afternoon realistic after travel."
                )
            return (
                f"Consider one or more of the following, within about {_format_duration_compact(total_minutes)}: "
                + ", ".join(parts)
                + ". Keep transfer/parking buffers between stops."
            )

        attraction_names: list[str] = []
        seen_attraction_names: set[str] = set()
        for attr in attractions:
            if not isinstance(attr, dict):
                continue
            name = str(attr.get("name", "") or "").strip()
            key = name.lower()
            if not name or key in seen_attraction_names:
                continue
            seen_attraction_names.add(key)
            attraction_names.append(name)

        def _day_focus_name(day_index: int, offset: int = 0) -> str:
            if not attraction_names:
                return ""
            return attraction_names[(day_index - 1 + offset) % len(attraction_names)]

        def _pick_non_repeating_focus(day_index: int, offset: int, recent_focuses: list[str]) -> str:
            base_focus = _day_focus_name(day_index, offset)
            if not base_focus or len(attraction_names) <= 1:
                return base_focus
            if base_focus.lower() not in recent_focuses:
                return base_focus

            start = (day_index - 1 + offset) % len(attraction_names)
            for step in range(1, len(attraction_names)):
                candidate = attraction_names[(start + step) % len(attraction_names)]
                if candidate.lower() not in recent_focuses:
                    return candidate
            return base_focus

        def _replace_first_attraction_mention(summary: str, replacement_name: str) -> str:
            if not summary or not replacement_name:
                return summary
            for name in attraction_names:
                if not name:
                    continue
                if re.search(re.escape(name), summary, re.IGNORECASE):
                    return re.sub(re.escape(name), replacement_name, summary, count=1, flags=re.IGNORECASE)
            return summary

        def _record_focus_mentions(summary: str, recent_focuses: list[str]) -> None:
            text = str(summary or "")
            if not text:
                return
            for name in attraction_names:
                if not name:
                    continue
                if re.search(re.escape(name), text, re.IGNORECASE):
                    recent_focuses.append(name.lower())

        def _rotate_restaurant_summary(summary: str, restaurant_name: str) -> str:
            text = str(summary or "").strip()
            if not text or not restaurant_name:
                return text
            low = text.lower()
            if "dinner" in low:
                # Normalize all dinner mentions to the day-assigned restaurant --
                # but skip if that name is already present anywhere in the
                # sentence (e.g. "Head to Red Fort Cuisine for dinner...").
                # Otherwise this duplicates the name ("Head to Red Fort Cuisine
                # for dinner at Red Fort Cuisine"). If a DIFFERENT restaurant
                # name is present (e.g. a stale/mismatched mention), the
                # substitution below still corrects it as intended.
                if restaurant_name.lower() in low:
                    return text
                return re.sub(
                    r"dinner(?:\s+at\s+[^.,;]+)?",
                    f"dinner at {restaurant_name}",
                    text,
                    count=1,
                    flags=re.IGNORECASE,
                )
            if re.search(rf"\b(dine|eat)\s+at\s+{re.escape(restaurant_name.lower())}\b", low):
                return re.sub(
                    rf"\s*Plan dinner at\s+{re.escape(restaurant_name)}\.?\s*$",
                    "",
                    text,
                    flags=re.IGNORECASE,
                ).strip()
            if restaurant_name.lower() in low and re.search(r"\b(dine|eat)\b", low):
                return re.sub(
                    r"\s*Plan dinner at\s+[^.]+\.?\s*$",
                    "",
                    text,
                    flags=re.IGNORECASE,
                ).strip()
            if low.startswith("reserved for return travel") or "onward drive" in low:
                return text
            return f"{text} Plan dinner at {restaurant_name}."

        effective_start_minutes = (
            _parse_clock_minutes(destination_day_start_time)
            or _parse_clock_minutes(default_day_start_time)
            or (10 * 60)
        )
        effective_activity_budget_minutes = (
            _parse_hours_limit(destination_daily_activity_hours, fallback_hours=5.0)
            if destination_daily_activity_hours is not None
            else _parse_hours_limit(default_daily_activity_hours, fallback_hours=5.0)
        )

        if is_first_destination:
            origin_label = trip_origin or "trip origin"
            departure_hour = _extract_hour(trip_departure_datetime)
            departure_time_label = _format_anchor_time(trip_departure_datetime)
            travel_period = "Morning"
            if departure_hour is not None and departure_hour >= 17:
                travel_period = "Evening"
            elif departure_hour is not None and departure_hour >= 11:
                travel_period = "Afternoon"

            travel_summary = f"Travel from {origin_label}."
            if departure_time_label:
                travel_summary = f"Travel from {origin_label} (depart around {departure_time_label})."

            if travel_period != "Morning":
                _set_period_summary(
                    days[0],
                    "Morning",
                    "Departure prep, airport transfer, and logistics before the main travel leg.",
                )

            _set_period_summary(days[0], travel_period, travel_summary)

            # Keep first-day arrival plans realistic by avoiding heavy activity
            # blocks immediately after transit from origin.
            first_after_travel = ""
            follow_period = {"Morning": "Afternoon", "Afternoon": "Evening", "Evening": ""}[travel_period]
            for period in days[0].get("periods", []) or []:
                if str(period.get("period", "")).title() == follow_period:
                    first_after_travel = str(period.get("summary", "") or "")
                    break
            if follow_period and _is_heavy_activity_block(first_after_travel):
                arrival_phrase = "After arriving"
                if lodging_location:
                    arrival_phrase += f" near {lodging_location}"
                if lodging_checkin_time:
                    arrival_phrase += f" around {lodging_checkin_time}"
                _set_period_summary(
                    days[0],
                    follow_period,
                    f"{arrival_phrase}, take a meal break and a short orientation stop; keep activity light after travel.",
                )

        drive_time = str(getting_here.get("drive_time", "") or "").strip()
        drive_minutes = _parse_duration_minutes(drive_time)
        first = days[0]
        first_periods = first.get("periods", [])
        if first_periods and not is_first_destination and (drive_time or previous_destination.lower() != "none"):
            existing_morning_summary = ""
            for period in first_periods:
                if str(period.get("period", "")).title() == "Morning":
                    existing_morning_summary = str(period.get("summary", "") or "")
                    break
            morning_already_arrival_aware = bool(
                re.search(r"\b(arrive|arrival|drive|driving|route|i-\d+|us-\d+)\b", existing_morning_summary, re.IGNORECASE)
            )
            if drive_minutes > 0:
                if not morning_already_arrival_aware:
                    arrival_minutes = effective_start_minutes + drive_minutes
                    arrival_label = _format_minutes_as_time(arrival_minutes)
                    _set_period_summary(
                        first,
                        "Morning",
                        f"Travel from {previous_destination} (depart around {_format_minutes_as_time(effective_start_minutes)}); arrival around {arrival_label}.",
                    )
                # The activity budget represents willingness/time to spend on
                # activities in a normal full day -- on an arrival day, the
                # drive itself eats directly into that allotment rather than
                # being free time on top of it, so it must be subtracted
                # before deciding what else fits in the afternoon.
                packed_afternoon = _build_multi_activity_afternoon_summary(
                    max(0, effective_activity_budget_minutes - drive_minutes)
                )
                if packed_afternoon:
                    _set_period_summary(first, "Afternoon", packed_afternoon)

            existing = str(first_periods[0].get("summary", "") or "")
            if re.search(r"\b(arrive|arrival|drive|driving|route|i-\d+|us-\d+)\b", existing, re.IGNORECASE):
                arrival_note = ""
            else:
                arrival_note = f"Travel from {previous_destination}"
            if arrival_note:
                first_periods[0]["summary"] = f"{arrival_note}. {existing}".strip()

        # Extend capacity-aware Afternoon packing beyond the single arrival-day
        # case above: any additional in-stay day (Day 2+) has the full activity
        # budget available with no transit friction to subtract, so it's an
        # even better candidate for a duration-aware multi-activity plan than
        # a plain AI-written summary or a same-name-swapped rotation. Day
        # index offsets which attractions are considered first so consecutive
        # days don't greedily pick the identical set.
        if len(days) > 1 and attractions:
            for day_index, day in enumerate(days[1:], start=2):
                if not (day.get("periods", []) or []):
                    continue
                packed = _build_multi_activity_afternoon_summary(
                    effective_activity_budget_minutes,
                    start_offset=(day_index - 1) % len(attractions),
                    arrival_day=False,
                )
                if packed:
                    _set_period_summary(day, "Afternoon", packed)

        if is_last_destination:
            return_label = trip_return or "base"
            return_time_label = _format_anchor_time(trip_return_datetime)
            return_time_suffix = f" around {return_time_label}" if return_time_label else ""
            last = days[-1]
            _set_period_summary(
                last,
                "Afternoon",
                f"Reserved for return travel to {return_label}{return_time_suffix}; begin checkout and departure logistics.",
            )
            _set_period_summary(
                last,
                "Evening",
                f"Reserved for return travel to {return_label}; plan buffer time for traffic, stops, and arrival.",
            )
        elif len(days) > 1 and next_destination:
            last = days[-1]
            last_periods = last.get("periods", [])
            if last_periods:
                last_periods[-1]["summary"] = (
                    f"Wrap key stops early and prepare for onward drive to {next_destination}; "
                    "skip new sunset commitments and keep departure buffers."
                ).strip()

        if len(days) == 1:
            day_one_periods = days[0].get("periods", []) or []
            has_arrival_or_checkin = any(
                _is_arrival_logistics_summary(str(period.get("summary", "") or ""))
                for period in day_one_periods
            )
            has_explicit_checkin = any(
                bool(re.search(r"\b(check[- ]?in|lodging)\b", str(period.get("summary", "") or ""), re.IGNORECASE))
                for period in day_one_periods
            )
            existing_afternoon_summary = ""
            for period in day_one_periods:
                if str(period.get("period", "")).title() == "Afternoon":
                    existing_afternoon_summary = str(period.get("summary", "") or "").strip()
                    break
            if day_one_periods and not has_explicit_checkin and not is_first_destination and not existing_afternoon_summary:
                _set_period_summary(
                    days[0],
                    "Afternoon",
                    "After arriving, check in and settle logistics before one short nearby highlight.",
                )

        # Expanded multi-day schedules can accidentally clone Day 1 arrival/check-in
        # text into later mornings. Keep Day 1 arrival context, but remove it from
        # Day 2+ so only the first day carries arrival logistics.
        if len(days) > 1:
            for day_index, day in enumerate(days[1:], start=2):
                periods = day.get("periods", []) or []
                for period in periods:
                    label = str(period.get("period", "")).title()
                    summary = str(period.get("summary", "") or "").strip()
                    low_summary = summary.lower()
                    if low_summary.startswith("reserved for return travel"):
                        continue
                    if label == "Morning" and _is_arrival_logistics_summary(summary):
                        focus_name = _day_focus_name(day_index)
                        if focus_name:
                            period["summary"] = (
                                f"Start with {focus_name}, then pivot to a different nearby area before midday crowds."
                            )
                        else:
                            period["summary"] = (
                                "Start with a different priority trailhead or district than Day 1, "
                                "and keep parking buffers before midday crowds."
                            )
                    elif label == "Afternoon":
                        if low_summary.startswith("after arrival"):
                            focus_name = _day_focus_name(day_index, offset=1) or "one nearby highlight"
                            period["summary"] = (
                                f"After the morning start, allocate this block to {focus_name} "
                                "and one nearby stop, keeping transfer buffers between activities."
                            )
                        elif re.search(r"\bcheck[- ]?in\b", low_summary) or re.search(r"\blodging\b", low_summary):
                            focus_name = _day_focus_name(day_index, offset=1) or "one or two nearby highlights"
                            period["summary"] = (
                                f"After the morning start, focus on {focus_name} "
                                "and a second nearby stop without repeating Day 1 transfer logistics."
                            )
                    elif _is_arrival_logistics_summary(summary):
                        focus_name = _day_focus_name(day_index, offset=2)
                        if focus_name:
                            period["summary"] = (
                                f"Keep this block destination-focused around {focus_name} "
                                "without repeating arrival or check-in logistics."
                            )
                            continue
                        period["summary"] = (
                            "Keep this block destination-focused without repeating arrival or check-in logistics."
                        )

        # Day-level allocation pass: rotate attraction focus and dinner targets to
        # prevent repeated day cards from reusing the same entities.
        restaurant_names = [
            str(r.get("name", "") or "").strip()
            for r in (restaurants if isinstance(restaurants, list) else [])
            if isinstance(r, dict) and str(r.get("name", "") or "").strip()
        ]
        recent_focuses: list[str] = []
        if days:
            for day_index, day in enumerate(days, start=1):
                periods = day.get("periods", []) or []
                dinner_name = restaurant_names[(day_index - 1) % len(restaurant_names)] if restaurant_names else ""
                for period in periods:
                    label = str(period.get("period", "")).title()
                    summary = str(period.get("summary", "") or "").strip()
                    if not summary:
                        continue
                    low_summary = summary.lower()
                    if low_summary.startswith("reserved for return travel"):
                        continue

                    if label == "Morning":
                        focus = _pick_non_repeating_focus(day_index, offset=0, recent_focuses=recent_focuses[-2:])
                        if focus:
                            updated = _replace_first_attraction_mention(summary, focus)
                            period["summary"] = updated
                            summary = updated
                            low_summary = summary.lower()
                            recent_focuses.append(focus.lower())

                    elif label == "Afternoon":
                        if "consider one or more of the following" in low_summary:
                            _record_focus_mentions(summary, recent_focuses)
                            continue
                        focus = _pick_non_repeating_focus(day_index, offset=1, recent_focuses=recent_focuses[-2:])
                        if focus:
                            updated = _replace_first_attraction_mention(summary, focus)
                            period["summary"] = updated
                            summary = updated
                            low_summary = summary.lower()
                            recent_focuses.append(focus.lower())

                    if len(recent_focuses) > 6:
                        recent_focuses = recent_focuses[-6:]

                    _record_focus_mentions(summary, recent_focuses)

                    if label == "Evening" and dinner_name:
                        period["summary"] = _rotate_restaurant_summary(summary, dinner_name)

        # Evening periods shouldn't send travelers to museums, discovery
        # sites, or visitor centers -- these are indoor, staffed venues that
        # realistically close in the late afternoon, not places open for an
        # after-dinner visit. No real operating-hours data exists to check
        # against (see docs/design/schedule-normalization.md), so this only
        # strips the specific sentence mentioning a name-recognizable
        # closes-early venue rather than trying to validate Evening content
        # generally.
        unsuitable_evening_names = [
            str(attr.get("name", "") or "").strip()
            for attr in attractions
            if isinstance(attr, dict)
            and AIContentGenerator._is_evening_unsuitable_venue(attr)
            and str(attr.get("name", "") or "").strip()
        ]
        if unsuitable_evening_names:
            for day in days:
                for period in day.get("periods", []) or []:
                    if str(period.get("period", "")).title() != "Evening":
                        continue
                    summary = str(period.get("summary", "") or "")
                    if not summary or summary.lower().startswith("reserved for return travel"):
                        continue
                    matched_name = next(
                        (
                            name
                            for name in unsuitable_evening_names
                            if re.search(re.escape(name), summary, re.IGNORECASE)
                        ),
                        None,
                    )
                    if not matched_name:
                        continue
                    sentences = AIContentGenerator._split_sentences(summary)
                    kept = [
                        s
                        for s in sentences
                        if not re.search(re.escape(matched_name), s, re.IGNORECASE)
                    ]
                    cleaned = " ".join(kept).strip()
                    if not cleaned:
                        cleaned = "Enjoy a relaxed evening back at your lodging after dinner."
                    if cleaned != summary:
                        period["summary"] = cleaned
                        logger.info(
                            "  Evening schedule: removed likely-closed venue mention '%s' "
                            "(indoor/exhibit-type venue unsuitable for an evening visit)",
                            matched_name,
                        )

        return days

    def _infer_day_count(self, dates: str) -> int:
        text = (dates or "").replace("–", "-")
        # "October 17-21, 2026" or "October 17, 2026"
        m = re.search(r"[A-Za-z]+\s+(\d{1,2})(?:\s*-\s*(\d{1,2}))?(?:,\s*\d{4})?", text)
        if m:
            start = int(m.group(1))
            end = int(m.group(2) or m.group(1))
            if end >= start:
                return max(1, min(5, end - start + 1))
            return 1
        # ISO range fallback: 2026-10-17 to 2026-10-21
        iso = re.findall(r"(\d{4}-\d{2}-\d{2})", text)
        if len(iso) >= 2:
            try:
                from datetime import datetime as _dt
                d0 = _dt.strptime(iso[0], "%Y-%m-%d")
                d1 = _dt.strptime(iso[1], "%Y-%m-%d")
                if d1 >= d0:
                    return max(1, min(5, (d1 - d0).days + 1))
            except ValueError:
                return 1
        return 1

    def _expand_days(self, days: list[dict[str, Any]], day_count: int) -> list[dict[str, Any]]:
        if not days:
            return days
        if day_count <= 1:
            return days[:1]
        if len(days) > day_count:
            return days[:day_count]
        if day_count <= 1:
            return days
        if len(days) >= day_count:
            return days

        base_periods = days[0].get("periods", [])
        if not base_periods:
            return days

        expanded = []
        for idx in range(day_count):
            if idx < len(days):
                expanded.append(days[idx])
                continue
            # Spread existing period ideas across additional days with lightweight variation.
            period_template = base_periods[idx % len(base_periods)]
            expanded.append({
                "day_label": f"Day {idx + 1}",
                "periods": [{
                    "period": period_template.get("period", "Plan"),
                    "summary": period_template.get("summary", "Continue with priority attractions and logistics.")
                }],
            })
        return expanded

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _resolve_attraction_target(self, dest: dict[str, Any], trip_meta: dict[str, Any]) -> int:
        default_target = 2
        trip_value = trip_meta.get("attractions_per_day") if isinstance(trip_meta, dict) else None
        dest_value = dest.get("attractions_per_day") if isinstance(dest, dict) else None
        candidate = dest_value if dest_value is not None else trip_value
        try:
            parsed = int(candidate)
        except (TypeError, ValueError):
            parsed = default_target
        if parsed < 1:
            parsed = default_target
        return parsed

    def _apply_manifest_attraction_target(
        self,
        attractions: list[dict[str, Any]],
        *,
        dates: str,
        attractions_per_day: int,
        protected_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(attractions, list):
            return []
        if not attractions:
            return []

        protected = {
            self._canonical_seed_name(str(name or ""))
            for name in (protected_names or [])
            if self._canonical_seed_name(str(name or ""))
        }

        def score(item: dict[str, Any]) -> tuple[int, float, int, str]:
            name = str(item.get("name", "") or "")
            must_see = 1 if bool(item.get("must_see")) else 0
            rating = self._coerce_float(item.get("rating"))
            if rating is None:
                rating = 0.0
            votes = item.get("votes")
            try:
                vote_count = int(votes)
            except (TypeError, ValueError):
                vote_count = 0
            difficulty = str(item.get("difficulty", "") or "").lower()
            difficulty_rank = {"strenuous": 0, "moderate": 1, "easy": 2, "n/a": 3, "": 4}.get(difficulty, 4)
            return (must_see, rating, vote_count, f"{difficulty_rank}:{name.lower()}")

        ranked = sorted(attractions, key=score, reverse=True)
        if protected:
            protected_items = [
                item for item in ranked
                if self._canonical_seed_name(str(item.get("name", "") or "")) in protected
            ]
            remaining = [
                item for item in ranked
                if self._canonical_seed_name(str(item.get("name", "") or "")) not in protected
            ]
        else:
            protected_items = []
            remaining = ranked

        day_count = max(1, self._infer_day_count(dates))
        target = max(1, int(attractions_per_day) * day_count)
        if len(ranked) <= target:
            return ranked

        selected = list(protected_items)
        selected.extend(remaining[: max(0, target - len(selected))])
        return selected

    @staticmethod
    def _canonical_restaurant_name(name: str) -> str:
        text = str(name or "").lower().strip()
        if not text:
            return ""
        text = text.replace("&", " and ")
        text = re.sub(r"[’']", "", text)
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        text = re.sub(r"\b(restaurant|restaurants|cafe|cafes|café|bar|bars|grill|grills|bistro|diner|kitchen|house)\b", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _normalize_restaurants(self, restaurants: list[dict[str, Any]], budget: Any = None) -> list[dict[str, Any]]:
        normalized = []
        price_rank = {"$": 0, "$$": 1, "$$$": 2, "$$$$": 3}
        budget_text = str(budget or "").lower()
        low_budget = any(k in budget_text for k in ["budget", "cheap", "economy", "value", "frugal"])
        high_budget = any(k in budget_text for k in ["luxury", "premium", "high", "splurge", "upscale"])

        for restaurant in restaurants:
            item = dict(restaurant)
            item["price_range"] = item.get("price_range") or item.get("price") or ""
            item["url_candidates"] = self._normalize_url_candidates(item.get("url_candidates", []))
            name = str(item.get("name", "") or "")
            cuisine = str(item.get("cuisine", "") or "")
            description = str(item.get("description", "") or "")
            if self._is_chain_or_fast_food(name, cuisine, description):
                continue

            # AI-side closure signal: skip if description explicitly references closed status.
            desc_lower = description.lower()
            if any(phrase in desc_lower for phrase in (
                "permanently closed", "has closed", "is closed", "no longer open", "closed its doors"
            )):
                continue

            canonical_key = self._canonical_restaurant_name(name)
            existing_idx = None
            for idx, existing in enumerate(normalized):
                if self._canonical_restaurant_name(str(existing.get("name", "") or "")) == canonical_key:
                    existing_idx = idx
                    break

            if existing_idx is not None:
                existing = normalized[existing_idx]
                existing_desc = str(existing.get("description", "") or "")
                candidate_desc = str(item.get("description", "") or "")
                if description and (not existing_desc or len(candidate_desc) > len(existing_desc)):
                    existing["description"] = description
                if not existing.get("cuisine") and item.get("cuisine"):
                    existing["cuisine"] = item.get("cuisine")
                if not existing.get("price_range") and item.get("price_range"):
                    existing["price_range"] = item.get("price_range")
                if not existing.get("url") and item.get("url"):
                    existing["url"] = item.get("url")
                if not existing.get("maps_url") and item.get("maps_url"):
                    existing["maps_url"] = item.get("maps_url")
                continue

            tier = str(item.get("price_range", "")).strip()

            if low_budget and tier in {"$$$", "$$$$"}:
                # Keep at most one splurge option for low-budget trips.
                if any(str(r.get("price_range", "")).strip() in {"$$$", "$$$$"} for r in normalized):
                    continue

            if high_budget and tier in {"$", "$$"}:
                # Keep at most one casual option for high-budget trips.
                if any(str(r.get("price_range", "")).strip() in {"$", "$$"} for r in normalized):
                    continue

            normalized.append(item)

        # Sort from inexpensive to expensive and keep cuisine variety visible.
        normalized.sort(
            key=lambda r: (
                price_rank.get(str(r.get("price_range", "")).strip(), 99),
                str(r.get("cuisine", "")).lower(),
                str(r.get("name", "")).lower(),
            )
        )
        return normalized

    @staticmethod
    def _normalize_url_candidates(raw: Any) -> list[str]:
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in raw:
            candidate = str(item or "").strip()
            if not candidate:
                continue
            lower = candidate.lower()
            if lower.startswith("www."):
                candidate = "https://" + candidate
                lower = candidate.lower()
            if not lower.startswith(("http://", "https://")):
                continue
            if lower in seen:
                continue
            seen.add(lower)
            out.append(candidate)
            if len(out) >= 3:
                break
        return out

    def _augment_with_url_candidates(self, payload: dict[str, Any], dest: dict[str, Any]) -> dict[str, Any]:
        attractions = payload.get("top_attractions", []) if isinstance(payload.get("top_attractions", []), list) else []
        restaurants = payload.get("dinner_recommendations", []) if isinstance(payload.get("dinner_recommendations", []), list) else []
        if not attractions and not restaurants:
            return payload

        attraction_lines: list[str] = []
        for idx, attraction in enumerate(attractions, start=1):
            if not isinstance(attraction, dict):
                continue
            name = str(attraction.get("name", "") or "").strip()
            if not name:
                continue
            item_type = str(attraction.get("type", "attraction") or "attraction").strip()
            desc = str(attraction.get("description", "") or "").strip()
            attraction_lines.append(f"{idx}. {name} | type={item_type} | {desc}")

        restaurant_lines: list[str] = []
        for idx, restaurant in enumerate(restaurants, start=1):
            if not isinstance(restaurant, dict):
                continue
            name = str(restaurant.get("name", "") or "").strip()
            if not name:
                continue
            cuisine = str(restaurant.get("cuisine", "") or "").strip()
            restaurant_lines.append(f"{idx}. {name} | cuisine={cuisine}")

        if not attraction_lines and not restaurant_lines:
            return payload

        user_prompt = (
            "Suggest URL candidates for these destination items.\n"
            f"Destination: {dest.get('name', '')}\n"
            "Rules:\n"
            "- Return JSON only with exact keys: attraction_url_candidates, restaurant_url_candidates.\n"
            "- Each item: {name: string, url_candidates: [up to 3 absolute URLs]}.\n"
            "- Use canonical, directly relevant URLs only.\n"
            "- For hike/trail-like attractions, prefer alltrails.com/trail/... URLs.\n"
            "- For restaurants, prefer official site or google maps place/search or tripadvisor.\n"
            "- If uncertain, return an empty url_candidates array.\n\n"
            "Attractions:\n"
            + ("\n".join(attraction_lines) if attraction_lines else "(none)")
            + "\n\nRestaurants:\n"
            + ("\n".join(restaurant_lines) if restaurant_lines else "(none)")
        )
        system_prompt = (
            "You produce structured URL candidate lists for known place names. "
            "Return valid JSON only, no markdown. Do not include explanatory text."
        )

        try:
            candidate_payload = self._llm.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                operation=f"url_candidates:{dest.get('id', dest.get('name', 'destination'))}",
                temperature=0.1,
                max_tokens=1800,
            )
        except Exception as exc:
            logger.warning("URL candidate augmentation failed for '%s': %s", dest.get("name", ""), exc)
            return payload

        if not isinstance(candidate_payload, dict):
            return payload

        attr_map: dict[str, list[str]] = {}
        for row in candidate_payload.get("attraction_url_candidates", []) or []:
            if not isinstance(row, dict):
                continue
            key = self._canonical_seed_name(str(row.get("name", "") or ""))
            if not key:
                continue
            attr_map[key] = self._normalize_url_candidates(row.get("url_candidates", []))

        rest_map: dict[str, list[str]] = {}
        for row in candidate_payload.get("restaurant_url_candidates", []) or []:
            if not isinstance(row, dict):
                continue
            key = self._canonical_seed_name(str(row.get("name", "") or ""))
            if not key:
                continue
            rest_map[key] = self._normalize_url_candidates(row.get("url_candidates", []))

        for attraction in attractions:
            if not isinstance(attraction, dict):
                continue
            key = self._canonical_seed_name(str(attraction.get("name", "") or ""))
            if key in attr_map and attr_map[key]:
                attraction["url_candidates"] = attr_map[key]

        for restaurant in restaurants:
            if not isinstance(restaurant, dict):
                continue
            key = self._canonical_seed_name(str(restaurant.get("name", "") or ""))
            if key in rest_map and rest_map[key]:
                restaurant["url_candidates"] = rest_map[key]

        payload["top_attractions"] = attractions
        payload["dinner_recommendations"] = restaurants
        return payload

    def _is_chain_or_fast_food(self, name: str, cuisine: str, description: str) -> bool:
        hay = " ".join([name, cuisine, description]).lower()
        if any(token in hay for token in self._FAST_FOOD_TOKENS):
            return True
        if any(token in hay for token in self._CHAIN_NAME_TOKENS):
            return True
        return False
