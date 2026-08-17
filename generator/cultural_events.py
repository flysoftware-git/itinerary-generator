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
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential
from generator.llm_client import MultiLLMClient
from generator.search_provider import build_search_client

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
        self._search = build_search_client(
            config_path,
            config_section="cultural_events",
            provider_override=search_provider_override,
            usage_tracker=self._llm.usage_tracker,
            usage_operation_prefix="cultural_events",
        )
        self._template = (PROMPTS_DIR / "cultural_events.txt").read_text(encoding="utf-8")
        self._system_prompt = (PROMPTS_DIR / "system_prompt.txt").read_text(encoding="utf-8")

    def discover(self, trip: dict[str, Any]) -> None:
        destinations = trip.get("destinations", [])

        def _one(dest: dict) -> None:
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
            result = self._verify_event_urls(result)

            result = self._drop_events_before_arrival(result, dest.get("dates", ""))
            result = self._sanitize_local_tip_by_itinerary_days(result, dest.get("dates", ""))
            return result
        except Exception as e:
            logger.error("Exception in _discover_for_dest for '%s': %s", dest["name"], e, exc_info=True)
            return {"has_events": False, "honest_assessment": "Event discovery encountered an error."}

    def _verify_event_urls(self, result: dict[str, Any]) -> dict[str, Any]:
        """Strip event URLs that are dead OR merely a generic/fallback page.

        Previously this only checked HTTP reachability (URLValidator.verify_url),
        which passes plenty of URLs that are "live" but useless -- e.g. a
        destination's generic /things-to-do listing page or a stale /404
        redirect target presented as if it were the event's own page. Real
        dipstick60 example: Moab's "Field of Screams Softball Tournament" and
        "Canyonlands Ultra" cultural events. Reuses URLDiscoverer's generic-URL
        detector (the same one attractions/restaurants rely on) instead of
        duplicating that pattern list here.
        """
        if not isinstance(result, dict) or not result.get("has_events") or not result.get("events"):
            return result

        from generator.url_discovery import URLDiscoverer
        from generator.url_validator import URLValidator

        uv = URLValidator()
        for event in result["events"]:
            url = event.get("url")
            if not url:
                continue
            if URLDiscoverer._is_obviously_generic_url(str(url).lower()):
                event.pop("url", None)
                continue
            ok, _ = uv.verify_url(url)
            if not ok:
                event.pop("url", None)

        return result

    def _grok_search(self, destination: str, dates: str) -> list[dict[str, Any]]:
        month = dates.split()[0] if dates else "October"
        # Collapsed from 3 separate live-search queries (festivals / cultural
        # events+concerts / general "events near") to 1 combined query.
        # Each query is now a full real search call (post the /v1/responses
        # live-search migration, not the old training-data-only path), so the
        # 3x call count was a 3x cost multiplier for marginal recall gain --
        # a single broad query covers the same intent in practice.
        query = f"{destination} festivals events concerts {month} 2026"
        return self._search.search(query, count=20)[:20]

    def _classify_destination(self, name: str) -> str:
        lower = name.lower()
        if any(k in lower for k in ("national park", "national monument", "canyon", "reef")):
            return "national_park"
        if any(k in lower for k in ("telluride", "aspen", "vail", "park city")):
            return "resort_town"
        if any(k in lower for k in ("santa fe", "albuquerque", "denver", "phoenix")):
            return "city"
        return "small_town"

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
            return result

        # Omit tips that reference any weekday not present in the itinerary window.
        if not mentioned_days.issubset(trip_days):
            result.pop("local_tip", None)
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
