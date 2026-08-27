"""
cultural_events.py — Auto-discover cultural events via Grok semantic search + AI synthesis.

NEVER invents events. Uses has_events decision tree:
  Format A: real events discovered, with venue, dates, admission
  Format B: honest fallback — no invented events

Search API history:
  v1.0: Bing Search API v7 (retired August 11, 2025)
  v1.1: Google Custom Search (deprecated full-web search, unusable)
  v1.2: Brave Search API (retired in favour of Azure AI Services)
  v1.3: Bing Web Search API (deprecated, limited availability)
  v1.4: Google Programmable Search Engine (rate-limited, prohibitive costs)
  v1.5: xAI Grok semantic search via chat-completions + live_search
        (superseded 2026-08-14: live_search tool deprecated, returns 410)
  v1.6: xAI Grok via /v1/responses + web_search tool, SSE streaming
        (current default). The search provider is pluggable -- see
        generator/search_provider.py for Claude/OpenAI alternatives.
"""
from __future__ import annotations
import json, logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential
from generator.llm_client import MultiLLMClient
from generator.search_provider import build_search_client
from generator.multi_site_grouping import DEFAULT_BASE_OWNED_CATEGORIES, category_deferred_to_base

logger = logging.getLogger(__name__)
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# See generator/ai_content.py for rationale: retrying a KeyError/TypeError/
# AttributeError/IndexError just burns exponential backoff on a bug that
# won't fix itself -- narrow retries to genuinely transient conditions.
_NON_RETRYABLE_LLM_EXCEPTIONS = (KeyError, TypeError, AttributeError, IndexError)
_retry_transient_llm_errors = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_not_exception_type(_NON_RETRYABLE_LLM_EXCEPTIONS),
)


class CulturalEventsDiscoverer:
    def __init__(
        self,
        config_path: Path | str = "config.yaml",
        llm_client: MultiLLMClient | None = None,
        *,
        search_provider_override: str | None = None,
    ) -> None:
        self._llm = llm_client or MultiLLMClient(config_path)
        # cultural_events.search_provider (config.yaml) selects grok or
        # claude independently of ai.provider/url_discovery.search_provider,
        # defaulting to grok -- unchanged behavior from before this
        # selection existed. See search_provider.py and claude_search.py.
        # search_provider_override (--search-provider CLI flag) forces a
        # single provider for a clean per-provider cost/behavior comparison
        # run -- see URLDiscoverer.__init__ for the fuller rationale.
        #
        # MODEL PINNING (2026-08-23). This call site passed no model at all,
        # so GrokSearch fell through to os.environ XAI_MODEL / "grok-latest"
        # -- the exact disconnection issue #65/#64 normalised away, fixed for
        # url_discovery.py and missed here. Measured on runs 3 and 4: URL
        # discovery ran grok-4.3 while cultural events ran grok-4-fast in the
        # same process, because only one of the two call sites was pinned.
        #
        # Resolution mirrors URLDiscoverer.__init__ exactly: the content
        # model when the content provider matches, and url_discovery's
        # search_model override when set, since this is discovery-shaped work
        # billed at discovery rates rather than content generation.
        # getattr rather than attribute access: this class accepts an
        # externally supplied llm_client, including minimal ad-hoc ones in
        # tests, and an unpinned model is a cost-reporting problem while a
        # crash here is a broken run.
        _provider = getattr(self._llm, "provider", None)
        _model = getattr(self._llm, "model", None)
        grok_model = _model if _provider == "grok" else None
        claude_model = _model if _provider == "anthropic" else None
        search_model_override = self._read_search_model_override(config_path)
        if search_model_override:
            grok_model = search_model_override
        self._search = build_search_client(
            config_path,
            config_section="cultural_events",
            provider_override=search_provider_override,
            grok_model=grok_model,
            claude_model=claude_model,
            usage_tracker=self._llm.usage_tracker,
            usage_operation_prefix="cultural_events",
        )
        self._template = (PROMPTS_DIR / "cultural_events.txt").read_text(encoding="utf-8")
        self._system_prompt = (PROMPTS_DIR / "system_prompt.txt").read_text(encoding="utf-8")
        # GH #68 multi-site grouping (config.yaml multi_site_grouping.base_owned_categories)
        # -- see generator/multi_site_grouping.py for the resolution rule this feeds.
        # Loaded independently of url_discovery.py's own copy since
        # CulturalEventsDiscoverer is constructed and run separately (see
        # main.py) and has no other config.yaml dependency today.
        self._multi_site_base_owned_categories: frozenset[str] = frozenset(DEFAULT_BASE_OWNED_CATEGORIES)
        self._load_multi_site_grouping_config(config_path)

    @staticmethod
    def _read_search_model_override(config_path) -> str:
        """`url_discovery.search_model`, shared so discovery-shaped work is
        billed at one rate rather than two. Read defensively: a bad config
        must leave the model unpinned rather than fail the run."""
        try:
            import yaml

            with open(str(config_path), "r", encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
            return str(((cfg.get("url_discovery") or {}).get("search_model") or "")).strip()
        except Exception:
            return ""

    def _load_multi_site_grouping_config(self, config_path: Path | str) -> None:
        try:
            import yaml

            with Path(config_path).open(encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            multi_site_cfg = cfg.get("multi_site_grouping", {}) or {}
            raw_base_owned = multi_site_cfg.get("base_owned_categories", DEFAULT_BASE_OWNED_CATEGORIES)
            if isinstance(raw_base_owned, list):
                self._multi_site_base_owned_categories = frozenset(
                    str(c or "").strip().lower() for c in raw_base_owned if str(c or "").strip()
                )
        except Exception as e:
            logger.warning("Could not load multi_site_grouping config from %s: %s", config_path, e)

    def discover(self, trip: dict[str, Any]) -> None:
        destinations = trip.get("destinations", [])

        def _one(dest: dict) -> None:
            # GH #68 multi-site grouping §5: a grouped entry can defer
            # cultural-events discovery to its group base entirely, mirroring
            # url_discovery.py's restaurant/scenic-drive skip-gates. Unlike
            # those two (bundled into one combined ai_content.py LLM call
            # that can't be cheaply skipped per-category), cultural events
            # are discovered via their own dedicated search + synthesis call
            # per destination -- so this skips the real API cost entirely
            # instead of generating-then-hiding at render time. Real
            # validation-run finding (dipstick67): a grouped child
            # (Canyonlands) independently generated its own Cultural Events
            # section that was actually about the group base's town (Moab).
            if category_deferred_to_base(dest, "cultural_events", self._multi_site_base_owned_categories):
                dest["cultural_events"] = {}
                logger.info(
                    "  Cultural events discovery skipped for '%s' -- category deferred to group base",
                    dest.get("name", ""),
                )
                return
            try:
                logger.info("Discovering cultural events for '%s'…", dest["name"])
                result = self._discover_for_dest(dest)
                dest["cultural_events"] = result
                logger.info("  → Cultural events for '%s': has_events=%s, count=%d",
                           dest["name"], result.get("has_events", False), len(result.get("events", [])))
            except Exception as e:
                logger.error("Failed to discover cultural events for '%s': %s", dest["name"], e, exc_info=True)
                # Return honest fallback on any error
                dest["cultural_events"] = {
                    "has_events": False,
                    "honest_assessment": f"Unable to discover events at this time."
                }

        with ThreadPoolExecutor(max_workers=min(len(destinations), 4)) as pool:
            futures = [pool.submit(_one, d) for d in destinations]
            for f in as_completed(futures):
                f.result()

    def _discover_for_dest(self, dest: dict[str, Any]) -> dict[str, Any]:
        try:
            raw_results = self._grok_search(dest["name"], dest["dates"])
            dest_type = self._classify_destination(dest["name"])
            result = self._synthesize(dest["name"], dest["dates"], dest_type, raw_results)
            
            # Ensure result is a dict with expected structure
            if not isinstance(result, dict):
                logger.warning("_synthesize returned non-dict for '%s': %s", dest["name"], type(result))
                return {"has_events": False, "honest_assessment": "Unable to parse events."}
            
            # Verify any event URLs that came back
            result = self._verify_event_urls(result, dest["name"])

            result = self._drop_events_before_arrival(result, dest.get("dates", ""))
            result = self._sanitize_local_tip_by_itinerary_days(result, dest.get("dates", ""))
            result = self._verify_local_tip_url(result, dest["name"])
            return result
        except Exception as e:
            logger.error("Exception in _discover_for_dest for '%s': %s", dest["name"], e, exc_info=True)
            return {"has_events": False, "honest_assessment": "Event discovery encountered an error."}

    def _verify_event_urls(self, result: dict[str, Any], dest_name: str = "") -> dict[str, Any]:
        """Strip event URLs that are dead OR merely a generic/fallback page,
        then give any event left without a link a Google Maps search fallback.

        Previously this only checked HTTP reachability (URLValidator.verify_url),
        which passes plenty of URLs that are "live" but useless -- e.g. a
        destination's generic /things-to-do listing page or a stale /404
        redirect target presented as if it were the event's own page. Real
        dipstick60 example: Moab's "Field of Screams Softball Tournament" and
        "Canyonlands Ultra" cultural events. Reuses URLDiscoverer's generic-URL
        detector (the same one attractions/restaurants rely on) instead of
        duplicating that pattern list here.

        dipstick62 follow-up: verification stripping (or the synthesis prompt
        never receiving) an event-specific URL left the event rendering with
        no link at all -- unlike every other content type (attractions,
        restaurants, en-route stops), which always falls back to a Google
        Maps search link when no real URL survives (see url_discovery.py's
        "*_maps_fallback_assigned" decisions). Real confirmed cause for
        Canyonlands Ultra: the search results only ever contained one bundled
        Moab-wide events-calendar URL covering a dozen unrelated events, not
        a per-event page, so the synthesis prompt correctly omitted the url
        field per its own "omit if no URL appears in results" instruction.
        Mirrors that same maps-fallback pattern here instead of leaving
        real, dated events with no way to look them up.
        """
        if not isinstance(result, dict) or not result.get("has_events") or not result.get("events"):
            return result

        from generator.url_discovery import URLDiscoverer
        from generator.url_validator import URLValidator

        uv = URLValidator()
        for event in result["events"]:
            url = event.get("url")
            if url:
                if URLDiscoverer._is_obviously_generic_url(str(url).lower()):
                    event.pop("url", None)
                else:
                    ok, _ = uv.verify_url(url)
                    if not ok:
                        event.pop("url", None)

            if not event.get("url"):
                fallback = self._event_maps_fallback_url(event, dest_name)
                if fallback:
                    event["url"] = fallback

        return result

    @staticmethod
    def _event_maps_fallback_url(event: dict[str, Any], dest_name: str) -> str:
        """Build a Google Maps search link scoped to the event/venue name.

        Matches the fallback already given to attractions, restaurants, and
        en-route stops when no real URL survives verification (see
        url_discovery.py's "trail_maps_fallback_assigned" and sibling
        decisions) -- cultural events had no equivalent, so a verified-away
        or never-found event URL previously left the event with no link at
        all instead of at least a map lookup.
        """
        name = str(event.get("name", "") or "").strip()
        if not name:
            return ""
        venue = str(event.get("venue", "") or "").strip()
        dest = str(dest_name or "").strip()
        scope = venue or dest
        query = f"{name} {scope}".strip() if scope and scope.lower() not in name.lower() else name
        return f"https://www.google.com/maps/search/?api=1&query={quote(query)}"

    # Anchors a specific place name commonly follows in local_tip prose --
    # "Check out live music at Alloy Kitchen, which hosts..." or "Check out
    # Moab Farmers Market" (both real, project-owner-flagged examples of
    # local_tip_name being omitted despite the tip clearly naming one
    # findable place). Deliberately conservative: only fires on an explicit
    # anchor phrase immediately followed by Title Case words, terminated by
    # punctuation or a small set of connector words -- a miss here just
    # means no link (matching this pipeline's existing fail-closed
    # default), not a wrong one.
    _LOCAL_TIP_VENUE_ANCHOR_RE = re.compile(
        r"(?i:\b(?:at|check out|visit|try|head to|stop by)\s+)"
        r"(?:the\s+|an?\s+)?"
        r"([A-Z][\w'&.-]*(?:\s+[A-Z][\w'&.-]*){0,5})"
        r"(?=[,.;:!?]|\s+[a-z]|$)"
    )

    @classmethod
    def _extract_local_tip_venue_name(cls, tip_text: str) -> str:
        match = cls._LOCAL_TIP_VENUE_ANCHOR_RE.search(str(tip_text or ""))
        return match.group(1).strip() if match else ""

    def _verify_local_tip_url(self, result: dict[str, Any], dest_name: str = "") -> dict[str, Any]:
        """Verify (or maps-fallback) the local_tip's link, mirroring _verify_event_urls.

        Format B's local_tip is free-text prose with zero link capability by
        default -- the project owner flagged this twice (e.g. "Check out Moab
        Farmers Market" naming a real, findable place with no way to click
        through). The synthesis prompt now asks for an optional
        local_tip_name (the specific place the tip names) and local_tip_url
        (verbatim from search results, same discipline as Format A's event
        url field). This mirrors _verify_event_urls' exact behavior for that
        pair: strip a synthesis-supplied URL that is dead or merely a
        generic/fallback page, then fall back to a Google Maps search scoped
        to the named place -- reusing _event_maps_fallback_url exactly as
        events do -- rather than leaving a real, nameable place with no link.
        If local_tip doesn't clearly name one specific place, local_tip_name
        is omitted and no link is forced onto generic advice.
        """
        if not isinstance(result, dict) or result.get("has_events") or not result.get("local_tip"):
            return result

        tip_name = str(result.get("local_tip_name", "") or "").strip()
        if not tip_name:
            # AI synthesis omits local_tip_name more often than the prompt's
            # instructions alone can prevent (project-owner flagged this
            # exact failure mode repeatedly -- "Moab Farmers Market", then
            # "Alloy Kitchen" -- despite both being unambiguously one named,
            # findable place). Try a conservative text-extraction fallback
            # before giving up on a link entirely.
            tip_name = self._extract_local_tip_venue_name(result.get("local_tip", ""))
            if tip_name:
                result["local_tip_name"] = tip_name
            else:
                result.pop("local_tip_url", None)
                result.pop("local_tip_name", None)
                return result

        from generator.url_discovery import URLDiscoverer
        from generator.url_validator import URLValidator

        url = result.get("local_tip_url")
        if url:
            if URLDiscoverer._is_obviously_generic_url(str(url).lower()):
                result.pop("local_tip_url", None)
            else:
                uv = URLValidator()
                ok, _ = uv.verify_url(url)
                if not ok:
                    result.pop("local_tip_url", None)

        if not result.get("local_tip_url"):
            fallback = self._event_maps_fallback_url({"name": tip_name}, dest_name)
            if fallback:
                result["local_tip_url"] = fallback

        return result

    def _grok_search(self, destination: str, dates: str) -> list[dict[str, Any]]:
        month = self._months_in_range(dates)
        # Collapsed from 3 separate live-search queries (festivals / cultural
        # events+concerts / general "events near") to 1 combined query.
        # Each query is now a full real search call (post the /v1/responses
        # live-search migration, not the old training-data-only path), so the
        # 3x call count was a 3x cost multiplier for marginal recall gain --
        # a single broad query covers the same intent in practice.
        query = f"{destination} festivals events concerts {month} 2026"
        return self._search.search(query, count=20)[:20]

    _MONTH_NAMES = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )

    @classmethod
    def _months_in_range(cls, dates: str) -> str:
        """Every month the stay touches, not just the first word of the string.

        `dates.split()[0]` took the first token, so "August 31 - September 1"
        searched AUGUST for a visit that is almost entirely September. Those
        results were then dropped by _drop_events_before_arrival for falling
        before the arrival date, and the destination reported no events at all.
        A stay spanning a month boundary is normal, not an edge case.
        """
        text = str(dates or "")
        found = [m for m in cls._MONTH_NAMES if m.lower() in text.lower()]
        if not found:
            return "October"
        return " ".join(found)

    def _classify_destination(self, name: str) -> str:
        lower = name.lower()
        if any(k in lower for k in ("national park", "national monument", "canyon", "reef")):
            return "national_park"
        if any(k in lower for k in ("telluride", "aspen", "vail", "park city")):
            return "resort_town"
        if any(k in lower for k in ("santa fe", "albuquerque", "denver", "phoenix")):
            return "city"
        # Everything unmatched used to fall through to "small_town", which the
        # prompt treats as near-evidence of no events ("ALL national_park and
        # most small_town destinations"). That made every destination outside a
        # four-city US allowlist self-fulfilling: Brussels, Amsterdam, Berlin
        # and Prague all classified as small towns and all reported no events.
        #
        # "unknown" is the honest answer. Guessing "city" instead would just
        # invert the bias, and there is no population data here to do better.
        return "unknown"

    def _sanitize_local_tip_by_itinerary_days(self, result: dict[str, Any], dates: str) -> dict[str, Any]:
        if not isinstance(result, dict) or result.get("has_events"):
            return result

        tip = str(result.get("local_tip", "") or "").strip()
        if not tip:
            return result

        mentioned_days = self._extract_mentioned_weekdays(tip)
        if not mentioned_days:
            return result

        trip_days = self._extract_trip_weekdays(dates)
        # If we cannot determine trip weekdays with confidence, omit weekday-specific tips.
        if not trip_days:
            result.pop("local_tip", None)
            result.pop("local_tip_name", None)
            result.pop("local_tip_url", None)
            return result

        # Omit tips that reference any weekday not present in the itinerary window.
        if not mentioned_days.issubset(trip_days):
            result.pop("local_tip", None)
            result.pop("local_tip_name", None)
            result.pop("local_tip_url", None)
        return result

    def _extract_mentioned_weekdays(self, text: str) -> set[str]:
        day_tokens = {
            "monday": "monday",
            "mon": "monday",
            "tuesday": "tuesday",
            "tue": "tuesday",
            "tues": "tuesday",
            "wednesday": "wednesday",
            "wed": "wednesday",
            "thursday": "thursday",
            "thu": "thursday",
            "thurs": "thursday",
            "friday": "friday",
            "fri": "friday",
            "saturday": "saturday",
            "sat": "saturday",
            "sunday": "sunday",
            "sun": "sunday",
        }
        words = re.findall(r"[A-Za-z]+", text.lower())
        result = {day_tokens[w] for w in words if w in day_tokens}

        if "weekend" in words or "weekends" in words:
            result.update({"saturday", "sunday"})
        return result

    def _extract_trip_weekdays(self, dates: str) -> set[str]:
        parsed = self._parse_date_range(dates)
        if not parsed:
            return set()
        start_date, end_date = parsed
        names = [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ]
        out: set[str] = set()
        cursor = start_date
        while cursor <= end_date:
            out.add(names[cursor.weekday()])
            cursor += timedelta(days=1)
        return out

    def _parse_date_range(self, dates: str) -> tuple[datetime, datetime] | None:
        if not dates:
            return None

        normalized = dates.replace("–", "-")
        m = re.search(
            r"([A-Za-z]+)\s+(\d{1,2})(?:\s*-\s*(\d{1,2}))?,\s*(\d{4})",
            normalized,
        )
        if m:
            month_name = m.group(1)
            day_start = int(m.group(2))
            day_end = int(m.group(3) or m.group(2))
            year = int(m.group(4))
            try:
                start = datetime.strptime(f"{month_name} {day_start} {year}", "%B %d %Y")
                end = datetime.strptime(f"{month_name} {day_end} {year}", "%B %d %Y")
            except ValueError:
                return None
            if end < start:
                return None
            return start, end

        # Support explicit start/end dates like "2026-10-07 to 2026-10-09".
        iso = re.findall(r"(\d{4}-\d{2}-\d{2})", normalized)
        if len(iso) >= 2:
            try:
                start = datetime.strptime(iso[0], "%Y-%m-%d")
                end = datetime.strptime(iso[1], "%Y-%m-%d")
            except ValueError:
                return None
            if end < start:
                return None
            return start, end

        return None

    _MONTH_NAME_TO_NUMBER = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }

    def _drop_events_before_arrival(self, result: dict[str, Any], dates: str) -> dict[str, Any]:
        """Filter out events dated entirely before the destination's arrival date.

        The synthesis prompt deliberately allows the LLM to surface events
        "within 7 days" of the travel dates for search-recall purposes, but a
        rendered event that starts before the traveler ever arrives is never
        useful and reads as a mistake (e.g. an event dated Oct 12 shown on a
        destination whose stay starts Oct 17). Only drops events whose parsed
        start date is strictly before the destination's own date-range start;
        unparseable/recurring event dates ("Every Friday evening in October")
        are left alone since there is no reliable date to compare.
        """
        if not isinstance(result, dict) or not result.get("has_events") or not result.get("events"):
            return result

        dest_range = self._parse_date_range(dates)
        if not dest_range:
            return result
        dest_start, _dest_end = dest_range

        kept: list[dict[str, Any]] = []
        for event in result["events"]:
            event_date_text = str(event.get("dates_in_range", "") or event.get("date", "") or "")
            event_start = self._parse_event_start_date(event_date_text, fallback_year=dest_start.year)
            if event_start is not None and event_start.date() < dest_start.date():
                logger.info(
                    "  Dropping cultural event before arrival: '%s' (%s) precedes destination start %s",
                    event.get("name", ""), event_date_text, dest_start.date(),
                )
                continue
            kept.append(event)

        result["events"] = kept
        if not kept:
            result["has_events"] = False
        return result

    def _parse_event_start_date(self, date_text: str, fallback_year: int) -> datetime | None:
        text = (date_text or "").replace("–", "-").strip()
        if not text:
            return None
        m = re.search(r"([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*-\s*\d{1,2})?(?:,?\s*(\d{4}))?", text)
        if not m:
            return None
        month = self._MONTH_NAME_TO_NUMBER.get(m.group(1).lower())
        if not month:
            return None
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else fallback_year
        try:
            return datetime(year, month, day)
        except ValueError:
            return None

    @_retry_transient_llm_errors
    def _synthesize(
        self, name: str, dates: str, dest_type: str, search_results: list[dict[str, Any]]
    ) -> dict[str, Any]:
        search_context = json.dumps(search_results[:15], indent=2)
        prompt = self._template.format(
            destination_name=name,
            dates=dates,
            destination_type=dest_type,
            search_results=search_context,
        )
        return self._llm.generate_json(
            system_prompt=self._system_prompt,
            user_prompt=prompt,
            operation=f"cultural_events:{name}",
            temperature=0.3,
            max_tokens=1500,
        )
