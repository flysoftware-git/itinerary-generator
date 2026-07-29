"""
url_discovery.py — Per-item URL discovery via Grok semantic search.

AI NEVER generates URLs. This module discovers URLs for every named
attraction, restaurant, scenic drive, and en-route stop after AI
content generation is complete.

Two-pass restaurant strategy:
  Pass 1: Google Maps domain filter (top-rated, accurate hours)
  Pass 2: TripAdvisor domain filter (local favorites, cuisine diversity)

Search API history:
  v1.0: Bing Search API v7 (retired August 11, 2025)
  v1.1: Google Custom Search (deprecated full-web search, unusable)
  v1.2: Brave Search API (retired in favour of Azure AI Services)
  v1.3: Bing Web Search API (deprecated, limited availability)
  v1.4: Google Programmable Search Engine (rate-limited, prohibitive costs)
  v1.5: xAI Grok semantic search (current)
        api.x.ai/v1/chat/completions
"""
from __future__ import annotations
import logging
import time
from pathlib import Path
import re
from math import ceil
from urllib.parse import quote, unquote, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Any
from generator.grok_search import GrokSearch
from generator.llm_client import MultiLLMClient
from generator.url_validator import URLValidator

logger = logging.getLogger(__name__)
MAX_FALLBACK_ATTEMPTS = 4
ALLTRAILS_404_MARKERS = (
    "we've reached the end of the trail",
    "the page you're looking for either doesn't exist",
)
GENERIC_BAD_URL_MARKERS = (
    "404errorpage",
    "/assetdetail/",
    "/assetdetail",
    "/404",
    "/things2do",
    "/things-to-do",
    "/plan-your-visit",
    "/planyourvisit",
    "/explore",
    "/about",
    "/index.htm",
    "/index.html",
)
SAFE_FALLBACK_URL_PREFIXES = (
    "https://www.google.com/maps/search/",
    "https://www.google.com/maps/dir/",
    "https://www.google.com/search",
)
POSITIVE_DOMAIN_HINTS = (
    "visit",
    "explore",
    "travel",
    "tourism",
    "tourisme",
    "turismo",
    "turizam",
    "turist",
    "arts",
    "culture",
    "gallery",
    "museum",
)
NEGATIVE_DOMAIN_HINTS = (
    "preserve",
    "wildlife",
    "nature",
    "conservation",
    "hospital",
    "government",
    "council",
    "realestate",
)
POSITIVE_PATH_HINTS = (
    "/arts/",
    "/culture/",
    "/gallery/",
    "/district/",
    "/visit/",
    "/explore/",
)
COUNTRY_TLD_HINTS: dict[str, tuple[str, ...]] = {
    "croatia": ("hr",),
    "italy": ("it",),
    "france": ("fr",),
    "spain": ("es",),
    "portugal": ("pt",),
    "germany": ("de",),
    "austria": ("at",),
    "switzerland": ("ch",),
    "netherlands": ("nl",),
    "belgium": ("be",),
    "czech": ("cz",),
    "czechia": ("cz",),
    "hungary": ("hu",),
    "slovenia": ("si",),
    "slovakia": ("sk",),
    "poland": ("pl",),
    "greece": ("gr",),
    "turkey": ("tr",),
    "norway": ("no",),
    "sweden": ("se",),
    "denmark": ("dk",),
    "finland": ("fi",),
    "ireland": ("ie",),
    "united kingdom": ("uk", "co.uk"),
    "england": ("uk", "co.uk"),
    "scotland": ("uk", "co.uk"),
    "wales": ("uk", "co.uk"),
    "serbia": ("rs",),
    "bosnia": ("ba",),
    "montenegro": ("me",),
    "albania": ("al",),
    "romania": ("ro",),
    "bulgaria": ("bg",),
}
LOCATION_CUE_TERMS = (
    "national park",
    "state park",
    "downtown",
    "historic district",
    "visitor center",
    "utah",
    "colorado",
    "arizona",
    "new mexico",
    "nevada",
    "california",
)

# ── URL Search Cache ────────────────────────────────────────────────────
_url_cache: dict[tuple[str, str, str], str | None] = {}

DEFAULT_UNINTERESTED_ATTRACTION_KEYWORDS = (
    "golf course",
    "country club",
)
DEFAULT_SKI_ATTRACTION_KEYWORDS = (
    "ski",
    "ski-",
    "snowboard",
    "snowboarding",
    "chairlift",
)
DEFAULT_SKI_IN_SEASON_MONTHS = (11, 12, 1, 2, 3, 4)
DEFAULT_MAX_TRAIL_MILES = 3.0
DEFAULT_ALLOW_BLOCKED_ALLTRAILS = True
DEFAULT_ALLTRAILS_MIN_CONFIDENCE_FOR_PUBLISH = "high"
DEFAULT_ALLTRAILS_REQUEST_DELAY_SECONDS = 0.8
DEFAULT_ALLTRAILS_BLOCK_COOLDOWN_SECONDS = 8.0
DEFAULT_ENABLE_FILTERED_ALLTRAILS_SELECTION = False
DEFAULT_STRICT_FILTERED_ALLTRAILS_NAMES = ("angels landing", "the narrows")
DEFAULT_ALLTRAILS_FILTER_MAX_MILES = 3.0
DEFAULT_ALLTRAILS_FILTER_MAX_GAIN_FEET = 300
DEFAULT_ALLTRAILS_FILTER_MIN_REVIEWS = 5
DEFAULT_ALLTRAILS_FILTER_ALLOWED_DIFFICULTIES = ("easy", "moderate", "moderately challenging")
DEFAULT_ALLTRAILS_RATING_MIN = 4.5
DEFAULT_ALLTRAILS_RATING_MIN_VOTES = 200
DEFAULT_ALLTRAILS_RATING_BOOST = 8
DEFAULT_RESTAURANT_RATING_MIN = 4.4
DEFAULT_RESTAURANT_RATING_MIN_VOTES = 100
DEFAULT_RESTAURANT_RATING_BOOST = 6
DEFAULT_RESTAURANT_NAME_DENYLIST: tuple[str, ...] = ()
RESTAURANT_CLOSURE_MARKERS: tuple[str, ...] = (
    "permanently closed",
    "this business is permanently closed",
    "closed permanently",
    "no longer in business",
    "this location is closed",
    "this restaurant is closed",
    "this place is closed",
)
RESTAURANT_PRE_OPENING_MARKERS: tuple[str, ...] = (
    "opening soon",
    "coming soon",
    "not yet open",
    "grand opening coming",
)
DEFAULT_URL_POLICY_MODE = "monitor"
DEFAULT_ALLTRAILS_SLUG_DENYLIST: tuple[str, ...] = ()
DEFAULT_URL_POLICY_BLOCKED_CLASSES = (
    "google_search",
    "google_maps_search",
    "google_maps_dir",
)
DEFAULT_URL_POLICY_ALLOWLIST_PATH = "docs/policy/url_policy_allowlist.txt"
DEFAULT_URL_POLICY_AUTO_ALLOW_FROM_OUTPUT = True
DEFAULT_URL_POLICY_OUTPUT_PATH = "output/index.html"

MONTH_NAME_TO_NUMBER = {
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


def _build_query_variants(name: str, destination: str, category: str) -> list[str]:
    """Return progressively broader, concise query strings for a named item."""
    category_terms = re.findall(r"[a-z0-9]+", (category or "").lower())
    category_compact = " ".join(category_terms[:2]).strip()

    quoted_name = f'"{name}"'
    base = f"{name} {destination}".strip()
    quoted_base = f"{quoted_name} {destination}".strip()

    if not category_compact:
        return [quoted_base, base, quoted_name, name]

    return [
        f"{quoted_base} {category_compact}",
        quoted_base,
        f"{base} {category_compact}",
        base,
    ]


def _build_alltrails_query_variants(name: str, destination: str) -> list[str]:
    """Focused AllTrails-first query variants to reduce search token overhead."""
    quoted_name = f'"{name}"'
    quoted_dest = f'"{destination}"'
    return [
        f"{quoted_name} {quoted_dest} alltrails",
        f"{name} {destination} alltrails trail",
        f"{quoted_name} alltrails",
        f"{quoted_name} alltrails trail",
        f"{name} alltrails",
        f"{name} trail",
        name,
    ]


def _build_restaurant_query_variants(name: str, destination: str) -> list[str]:
    """Focused restaurant query variants tuned for maps/directory lookup."""
    quoted_name = f'"{name}"'
    quoted_dest = f'"{destination}"'
    return [
        f"{quoted_name} {quoted_dest} restaurant",
        f"{name} {destination} restaurant",
        f"{quoted_name} {destination}",
    ]


class URLDiscoverer:
    def __init__(self, config_path: str | Any = "config.yaml", llm_client: MultiLLMClient | None = None) -> None:
        self._llm = llm_client or MultiLLMClient(config_path)
        self._search = GrokSearch(
            usage_tracker=self._llm.usage_tracker,
            usage_operation_prefix="url_discovery",
        )
        self._url_validator = URLValidator()

        self._uninterested_keywords: tuple[str, ...] = DEFAULT_UNINTERESTED_ATTRACTION_KEYWORDS
        self._seasonal_ski_keywords: tuple[str, ...] = DEFAULT_SKI_ATTRACTION_KEYWORDS
        self._ski_in_season_months: tuple[int, ...] = DEFAULT_SKI_IN_SEASON_MONTHS
        self._max_trail_miles: float = DEFAULT_MAX_TRAIL_MILES
        self._allow_blocked_alltrails: bool = DEFAULT_ALLOW_BLOCKED_ALLTRAILS
        self._alltrails_min_confidence_for_publish: str = DEFAULT_ALLTRAILS_MIN_CONFIDENCE_FOR_PUBLISH
        self._alltrails_request_delay_seconds: float = DEFAULT_ALLTRAILS_REQUEST_DELAY_SECONDS
        self._alltrails_block_cooldown_seconds: float = DEFAULT_ALLTRAILS_BLOCK_COOLDOWN_SECONDS
        self._enable_filtered_alltrails_selection: bool = DEFAULT_ENABLE_FILTERED_ALLTRAILS_SELECTION
        self._strict_filtered_alltrails_names: tuple[str, ...] = DEFAULT_STRICT_FILTERED_ALLTRAILS_NAMES
        self._alltrails_filter_max_miles: float = DEFAULT_ALLTRAILS_FILTER_MAX_MILES
        self._alltrails_filter_max_gain_feet: int = DEFAULT_ALLTRAILS_FILTER_MAX_GAIN_FEET
        self._alltrails_filter_min_reviews: int = DEFAULT_ALLTRAILS_FILTER_MIN_REVIEWS
        self._alltrails_filter_allowed_difficulties: tuple[str, ...] = DEFAULT_ALLTRAILS_FILTER_ALLOWED_DIFFICULTIES
        self._alltrails_filtered_selection_cache: dict[tuple[str, str], str | None] = {}
        self._alltrails_fetch_cache: dict[str, tuple[bool, int | str, str]] = {}
        self._alltrails_last_request_ts: float = 0.0
        self._alltrails_blocked_until_ts: float = 0.0
        self._alltrails_fetch_lock: Lock = Lock()
        self._max_alltrails_query_attempts: int = 5
        self._max_restaurant_query_attempts: int = 3
        self._alltrails_rating_min: float = DEFAULT_ALLTRAILS_RATING_MIN
        self._alltrails_rating_min_votes: int = DEFAULT_ALLTRAILS_RATING_MIN_VOTES
        self._alltrails_rating_boost: int = DEFAULT_ALLTRAILS_RATING_BOOST
        self._restaurant_rating_min: float = DEFAULT_RESTAURANT_RATING_MIN
        self._restaurant_rating_min_votes: int = DEFAULT_RESTAURANT_RATING_MIN_VOTES
        self._restaurant_rating_boost: int = DEFAULT_RESTAURANT_RATING_BOOST
        self._restaurant_name_denylist: frozenset[str] = frozenset(DEFAULT_RESTAURANT_NAME_DENYLIST)
        self._url_policy_mode: str = DEFAULT_URL_POLICY_MODE
        self._url_policy_blocked_classes: set[str] = set(DEFAULT_URL_POLICY_BLOCKED_CLASSES)
        self._url_policy_allowlist_path: str = DEFAULT_URL_POLICY_ALLOWLIST_PATH
        self._url_policy_auto_allow_from_output: bool = DEFAULT_URL_POLICY_AUTO_ALLOW_FROM_OUTPUT
        self._url_policy_output_path: str = DEFAULT_URL_POLICY_OUTPUT_PATH
        self._url_policy_allowlisted_urls: set[str] = set()
        self._alltrails_slug_denylist: frozenset[str] = frozenset(DEFAULT_ALLTRAILS_SLUG_DENYLIST)
        self._fetch_final_url_cache: dict[str, str] = {}
        self._load_interest_filters(config_path)
        self._load_url_policy_allowlist()

    def _load_interest_filters(self, config_path: str | Any) -> None:
        try:
            import yaml

            with Path(config_path).open(encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            url_cfg = cfg.get("url_discovery", {}) or {}

            raw_keywords = url_cfg.get("uninterested_attraction_keywords", []) or []
            normalized_keywords = tuple(
                kw.strip().lower() for kw in raw_keywords if str(kw or "").strip()
            )
            if normalized_keywords:
                self._uninterested_keywords = normalized_keywords

            ski_cfg = url_cfg.get("seasonal_uninterested", {}).get("ski", {}) or {}
            raw_ski_keywords = ski_cfg.get("keywords", []) or []
            normalized_ski_keywords = tuple(
                kw.strip().lower() for kw in raw_ski_keywords if str(kw or "").strip()
            )
            if normalized_ski_keywords:
                self._seasonal_ski_keywords = normalized_ski_keywords

            raw_months = ski_cfg.get("in_season_months", []) or []
            normalized_months: list[int] = []
            for m in raw_months:
                try:
                    m_i = int(m)
                except (TypeError, ValueError):
                    continue
                if 1 <= m_i <= 12:
                    normalized_months.append(m_i)
            if normalized_months:
                self._ski_in_season_months = tuple(normalized_months)

            max_trail_miles = url_cfg.get("max_trail_miles", DEFAULT_MAX_TRAIL_MILES)
            try:
                parsed_max = float(max_trail_miles)
                if parsed_max > 0:
                    self._max_trail_miles = parsed_max
            except (TypeError, ValueError):
                self._max_trail_miles = DEFAULT_MAX_TRAIL_MILES

            allow_blocked = url_cfg.get("allow_blocked_alltrails", DEFAULT_ALLOW_BLOCKED_ALLTRAILS)
            self._allow_blocked_alltrails = bool(allow_blocked)

            min_conf = str(
                url_cfg.get(
                    "alltrails_min_confidence_for_publish",
                    DEFAULT_ALLTRAILS_MIN_CONFIDENCE_FOR_PUBLISH,
                )
                or DEFAULT_ALLTRAILS_MIN_CONFIDENCE_FOR_PUBLISH
            ).strip().lower()
            if min_conf in {"low", "medium", "high"}:
                self._alltrails_min_confidence_for_publish = min_conf
            else:
                self._alltrails_min_confidence_for_publish = DEFAULT_ALLTRAILS_MIN_CONFIDENCE_FOR_PUBLISH

            request_delay_seconds = url_cfg.get(
                "alltrails_request_delay_seconds",
                DEFAULT_ALLTRAILS_REQUEST_DELAY_SECONDS,
            )
            try:
                parsed_delay = float(request_delay_seconds)
                if parsed_delay >= 0:
                    self._alltrails_request_delay_seconds = parsed_delay
            except (TypeError, ValueError):
                self._alltrails_request_delay_seconds = DEFAULT_ALLTRAILS_REQUEST_DELAY_SECONDS

            block_cooldown_seconds = url_cfg.get(
                "alltrails_block_cooldown_seconds",
                DEFAULT_ALLTRAILS_BLOCK_COOLDOWN_SECONDS,
            )
            try:
                parsed_cooldown = float(block_cooldown_seconds)
                if parsed_cooldown >= 0:
                    self._alltrails_block_cooldown_seconds = parsed_cooldown
            except (TypeError, ValueError):
                self._alltrails_block_cooldown_seconds = DEFAULT_ALLTRAILS_BLOCK_COOLDOWN_SECONDS

            enable_filtered_alltrails = url_cfg.get(
                "enable_filtered_alltrails_selection",
                DEFAULT_ENABLE_FILTERED_ALLTRAILS_SELECTION,
            )
            self._enable_filtered_alltrails_selection = bool(enable_filtered_alltrails)

            raw_strict_names = url_cfg.get(
                "strict_filtered_alltrails_names",
                list(DEFAULT_STRICT_FILTERED_ALLTRAILS_NAMES),
            )
            if isinstance(raw_strict_names, list):
                normalized_strict_names = tuple(
                    str(v or "").strip().lower() for v in raw_strict_names if str(v or "").strip()
                )
                if normalized_strict_names:
                    self._strict_filtered_alltrails_names = normalized_strict_names

            filter_max_miles = url_cfg.get("alltrails_filter_max_miles", DEFAULT_ALLTRAILS_FILTER_MAX_MILES)
            try:
                parsed_filter_miles = float(filter_max_miles)
                if parsed_filter_miles > 0:
                    self._alltrails_filter_max_miles = parsed_filter_miles
            except (TypeError, ValueError):
                self._alltrails_filter_max_miles = DEFAULT_ALLTRAILS_FILTER_MAX_MILES

            filter_max_gain = url_cfg.get("alltrails_filter_max_gain_feet", DEFAULT_ALLTRAILS_FILTER_MAX_GAIN_FEET)
            try:
                parsed_filter_gain = int(filter_max_gain)
                if parsed_filter_gain >= 0:
                    self._alltrails_filter_max_gain_feet = parsed_filter_gain
            except (TypeError, ValueError):
                self._alltrails_filter_max_gain_feet = DEFAULT_ALLTRAILS_FILTER_MAX_GAIN_FEET

            filter_min_reviews = url_cfg.get("alltrails_filter_min_reviews", DEFAULT_ALLTRAILS_FILTER_MIN_REVIEWS)
            try:
                parsed_filter_reviews = int(filter_min_reviews)
                if parsed_filter_reviews >= 0:
                    self._alltrails_filter_min_reviews = parsed_filter_reviews
            except (TypeError, ValueError):
                self._alltrails_filter_min_reviews = DEFAULT_ALLTRAILS_FILTER_MIN_REVIEWS

            raw_difficulties = url_cfg.get(
                "alltrails_filter_allowed_difficulties",
                list(DEFAULT_ALLTRAILS_FILTER_ALLOWED_DIFFICULTIES),
            )
            if isinstance(raw_difficulties, list):
                normalized_difficulties = tuple(
                    str(v or "").strip().lower() for v in raw_difficulties if str(v or "").strip()
                )
                if normalized_difficulties:
                    self._alltrails_filter_allowed_difficulties = normalized_difficulties

            max_alltrails_attempts = url_cfg.get("max_alltrails_query_attempts", 5)
            try:
                parsed_alltrails_attempts = int(max_alltrails_attempts)
                if parsed_alltrails_attempts > 0:
                    self._max_alltrails_query_attempts = parsed_alltrails_attempts
            except (TypeError, ValueError):
                self._max_alltrails_query_attempts = 5

            max_restaurant_attempts = url_cfg.get("max_restaurant_query_attempts", 3)
            try:
                parsed_restaurant_attempts = int(max_restaurant_attempts)
                if parsed_restaurant_attempts > 0:
                    self._max_restaurant_query_attempts = parsed_restaurant_attempts
            except (TypeError, ValueError):
                self._max_restaurant_query_attempts = 3

            alltrails_rating_min = url_cfg.get("alltrails_rating_min", DEFAULT_ALLTRAILS_RATING_MIN)
            try:
                parsed_alltrails_rating_min = float(alltrails_rating_min)
                if 0.0 <= parsed_alltrails_rating_min <= 5.0:
                    self._alltrails_rating_min = parsed_alltrails_rating_min
            except (TypeError, ValueError):
                self._alltrails_rating_min = DEFAULT_ALLTRAILS_RATING_MIN

            alltrails_rating_min_votes = url_cfg.get("alltrails_rating_min_votes", DEFAULT_ALLTRAILS_RATING_MIN_VOTES)
            try:
                parsed_alltrails_min_votes = int(alltrails_rating_min_votes)
                if parsed_alltrails_min_votes >= 0:
                    self._alltrails_rating_min_votes = parsed_alltrails_min_votes
            except (TypeError, ValueError):
                self._alltrails_rating_min_votes = DEFAULT_ALLTRAILS_RATING_MIN_VOTES

            alltrails_rating_boost = url_cfg.get("alltrails_rating_boost", DEFAULT_ALLTRAILS_RATING_BOOST)
            try:
                parsed_alltrails_boost = int(alltrails_rating_boost)
                if parsed_alltrails_boost >= 0:
                    self._alltrails_rating_boost = parsed_alltrails_boost
            except (TypeError, ValueError):
                self._alltrails_rating_boost = DEFAULT_ALLTRAILS_RATING_BOOST

            restaurant_rating_min = url_cfg.get("restaurant_rating_min", DEFAULT_RESTAURANT_RATING_MIN)
            try:
                parsed_restaurant_rating_min = float(restaurant_rating_min)
                if 0.0 <= parsed_restaurant_rating_min <= 5.0:
                    self._restaurant_rating_min = parsed_restaurant_rating_min
            except (TypeError, ValueError):
                self._restaurant_rating_min = DEFAULT_RESTAURANT_RATING_MIN

            restaurant_rating_min_votes = url_cfg.get("restaurant_rating_min_votes", DEFAULT_RESTAURANT_RATING_MIN_VOTES)
            try:
                parsed_restaurant_min_votes = int(restaurant_rating_min_votes)
                if parsed_restaurant_min_votes >= 0:
                    self._restaurant_rating_min_votes = parsed_restaurant_min_votes
            except (TypeError, ValueError):
                self._restaurant_rating_min_votes = DEFAULT_RESTAURANT_RATING_MIN_VOTES

            restaurant_rating_boost = url_cfg.get("restaurant_rating_boost", DEFAULT_RESTAURANT_RATING_BOOST)
            try:
                parsed_restaurant_boost = int(restaurant_rating_boost)
                if parsed_restaurant_boost >= 0:
                    self._restaurant_rating_boost = parsed_restaurant_boost
            except (TypeError, ValueError):
                self._restaurant_rating_boost = DEFAULT_RESTAURANT_RATING_BOOST

            policy_mode = str(
                url_cfg.get("url_policy_mode", DEFAULT_URL_POLICY_MODE)
                or DEFAULT_URL_POLICY_MODE
            ).strip().lower()
            if policy_mode in {"off", "monitor", "enforce"}:
                self._url_policy_mode = policy_mode
            else:
                self._url_policy_mode = DEFAULT_URL_POLICY_MODE

            raw_blocked_classes = url_cfg.get(
                "url_policy_blocked_classes",
                list(DEFAULT_URL_POLICY_BLOCKED_CLASSES),
            )
            if isinstance(raw_blocked_classes, list):
                normalized_blocked = {
                    str(v or "").strip().lower()
                    for v in raw_blocked_classes
                    if str(v or "").strip()
                }
                if normalized_blocked:
                    self._url_policy_blocked_classes = normalized_blocked

            allowlist_path = str(
                url_cfg.get("url_policy_allowlist_path", DEFAULT_URL_POLICY_ALLOWLIST_PATH)
                or DEFAULT_URL_POLICY_ALLOWLIST_PATH
            ).strip()
            if allowlist_path:
                self._url_policy_allowlist_path = allowlist_path

            auto_allow_from_output = url_cfg.get(
                "url_policy_auto_allow_from_output",
                DEFAULT_URL_POLICY_AUTO_ALLOW_FROM_OUTPUT,
            )
            self._url_policy_auto_allow_from_output = bool(auto_allow_from_output)

            output_path = str(
                url_cfg.get("url_policy_output_path", DEFAULT_URL_POLICY_OUTPUT_PATH)
                or DEFAULT_URL_POLICY_OUTPUT_PATH
            ).strip()
            if output_path:
                self._url_policy_output_path = output_path

            raw_slug_denylist = url_cfg.get("alltrails_slug_denylist", [])
            if isinstance(raw_slug_denylist, list):
                self._alltrails_slug_denylist = frozenset(
                    str(v or "").strip().lower()
                    for v in raw_slug_denylist
                    if str(v or "").strip()
                )

            raw_restaurant_denylist = url_cfg.get("restaurant_name_denylist", [])
            if isinstance(raw_restaurant_denylist, list):
                self._restaurant_name_denylist = frozenset(
                    str(v or "").strip().lower()
                    for v in raw_restaurant_denylist
                    if str(v or "").strip()
                )
        except Exception:
            # Keep defaults when config loading is unavailable in tests or runtime.
            return

    def _load_url_policy_allowlist(self) -> None:
        self._url_policy_allowlisted_urls = set()
        path = Path(self._url_policy_allowlist_path)
        if path.exists():
            try:
                for raw in path.read_text(encoding="utf-8").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    self._url_policy_allowlisted_urls.add(line)
            except Exception as exc:
                logger.warning("Failed to load URL policy allowlist '%s': %s", path, exc)

        auto_allow_from_output = bool(
            getattr(self, "_url_policy_auto_allow_from_output", DEFAULT_URL_POLICY_AUTO_ALLOW_FROM_OUTPUT)
        )
        if not auto_allow_from_output:
            return

        output_path = Path(getattr(self, "_url_policy_output_path", DEFAULT_URL_POLICY_OUTPUT_PATH))
        if not output_path.exists():
            return
        try:
            for url in self._extract_urls_from_html(output_path):
                self._url_policy_allowlisted_urls.add(url)
        except Exception as exc:
            logger.warning("Failed to load URL policy URLs from output '%s': %s", output_path, exc)

    @staticmethod
    def _extract_urls_from_html(path: Path) -> set[str]:
        urls: set[str] = set()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r'href\s*=\s*["\']([^"\']+)["\']', text, flags=re.IGNORECASE):
            candidate = match.group(1).strip()
            if candidate.lower().startswith(("http://", "https://")):
                urls.add(candidate)
        return urls

    # ── Public entry point ───────────────────────────────────────────────────

    def discover_all(self, trip: dict[str, Any]) -> None:
        destinations = trip.get("destinations", [])

        def _discover_one(dest: dict) -> None:
            name = dest["name"]
            ai = dest.get("ai_content", {})
            nps_code = dest.get("nps_park_code")
            logger.info("URL discovery for '%s'…", name)
            # Parallelise the four independent URL categories within each destination
            with ThreadPoolExecutor(max_workers=4) as inner:
                futs = [
                    inner.submit(self._discover_attractions, ai, name, nps_code, dest.get("dates")),
                    inner.submit(self._discover_restaurants, ai, name),
                    inner.submit(self._discover_en_route_stops, ai, name),
                    inner.submit(self._discover_scenic_drives, dest, name),
                ]
                for f in as_completed(futs):
                    f.result()

        with ThreadPoolExecutor(max_workers=min(len(destinations), 3)) as pool:
            futures = [pool.submit(_discover_one, d) for d in destinations]
            for f in as_completed(futures):
                f.result()

    def audit_discovered_urls(self, trip: dict[str, Any]) -> None:
        """Strip low-confidence discovered URLs before HTML assembly.

        This is a final safety pass. Discovery should already be strict, but
        we remove anything that still looks weak so bad links do not reach the
        generated itinerary.
        """
        for dest in trip.get("destinations", []):
            dest_name = dest.get("name", "")
            ai = dest.get("ai_content", {}) if isinstance(dest.get("ai_content", {}), dict) else {}

            for attr in ai.get("top_attractions", []) or []:
                attr_name = str(attr.get("name", "") or "")
                attr_type = str(attr.get("type", "attraction") or "attraction").lower()
                attr_context = self._attraction_trail_context(attr)
                trail_like = self._is_trail_like_attraction(attr_name, attr_type, attr_context)
                url = str(attr.get("url", "") or "").strip()
                if trail_like and url and not self._is_alltrails_trail_url(url):
                    lower = url.lower()
                    if not any(lower.startswith(prefix) for prefix in SAFE_FALLBACK_URL_PREFIXES):
                        self._log_rejected_url("attraction", dest_name, attr_name, url)
                        attr.pop("url", None)
                        continue
                cleaned = self._retain_discovered_url(
                    url,
                    attr_name,
                    dest_name,
                    allow_alltrails=trail_like,
                    kind="attraction",
                )
                if cleaned != url:
                    self._log_rejected_url("attraction", dest_name, attr_name, url)
                    if cleaned:
                        attr["url"] = cleaned
                    else:
                        attr.pop("url", None)

            for stop in ai.get("getting_here", {}).get("en_route_stops", []) or []:
                stop_name = str(stop.get("name", "") or "")
                url = str(stop.get("url", "") or "").strip()
                cleaned = self._retain_discovered_url(
                    url,
                    stop_name,
                    dest_name,
                    allow_alltrails=False,
                    kind="en-route stop",
                )
                if cleaned != url:
                    self._log_rejected_url("en-route stop", dest_name, stop_name, url)
                    if cleaned:
                        stop["url"] = cleaned
                    else:
                        stop.pop("url", None)

            eligible_restaurants: list[dict[str, Any]] = []
            for rest in ai.get("dinner_recommendations", []) or []:
                rest_name = str(rest.get("name", "") or "")
                url = str(rest.get("url", "") or "").strip()
                cleaned = self._retain_discovered_url(
                    url,
                    rest_name,
                    dest_name,
                    allow_alltrails=False,
                    kind="restaurant",
                )
                if cleaned != url:
                    self._log_rejected_url("restaurant", dest_name, rest_name, url)
                    if cleaned:
                        rest["url"] = cleaned
                    else:
                        rest.pop("url", None)
                if self._is_restaurant_ineligible(rest, dest_name):
                    logger.info(
                        "  Restaurant freshness gate removed '%s' in '%s'",
                        rest_name, dest_name,
                    )
                    continue
                eligible_restaurants.append(rest)
            if len(eligible_restaurants) != len(ai.get("dinner_recommendations", []) or []):
                ai["dinner_recommendations"] = eligible_restaurants

            for drive in dest.get("scenic_drives", []) or []:
                drive_name = str(drive.get("title", "") or "")
                url = str(drive.get("url", "") or "").strip()
                cleaned = self._retain_discovered_url(
                    url,
                    drive_name,
                    dest_name,
                    allow_alltrails=False,
                    kind="scenic drive",
                )
                if cleaned != url:
                    self._log_rejected_url("scenic drive", dest_name, drive_name, url)
                    if cleaned:
                        drive["url"] = cleaned
                    else:
                        drive.pop("url", None)

            events = dest.get("cultural_events", {})
            if isinstance(events, dict):
                for event in events.get("events", []) or []:
                    event_name = str(event.get("name", "") or "")
                    url = str(event.get("url", "") or "").strip()
                    cleaned = self._retain_discovered_url(
                        url,
                        event_name,
                        dest_name,
                        allow_alltrails=False,
                        kind="event",
                    )
                    if cleaned != url:
                        self._log_rejected_url("event", dest_name, event_name, url)
                        if cleaned:
                            event["url"] = cleaned
                        else:
                            event.pop("url", None)

            self._deduplicate_within_destination(dest)

    def _retain_discovered_url(
        self,
        url: str,
        item_name: str,
        dest_name: str,
        *,
        allow_alltrails: bool,
        kind: str = "generic",
    ) -> str:
        if not url:
            return ""
        lower = url.lower()
        allowlisted_urls = getattr(self, "_url_policy_allowlisted_urls", set())
        if url in allowlisted_urls:
            return url
        is_safe_fallback = any(lower.startswith(prefix) for prefix in SAFE_FALLBACK_URL_PREFIXES)
        if self._is_obviously_generic_url(lower):
            return ""
        if not allow_alltrails and self._is_alltrails_trail_url(url):
            return ""
        # AllTrails slug denylist: fast-reject known-invalid slugs before any fetch.
        if self._is_alltrails_trail_url(url):
            slug = urlparse(url).path.rsplit("/", 1)[-1].lower()
            if slug in getattr(self, "_alltrails_slug_denylist", frozenset()):
                logger.info("AllTrails slug denylist hit for %s '%s': %s", kind, item_name, url)
                return ""
        # Wikipedia entity-path check: wiki page name is deterministic in the URL.
        # Reject when no item token appears in the wiki slug (catches wrong-entity links).
        if not is_safe_fallback and "wikipedia.org/wiki/" in lower:
            wiki_slug = lower.split("/wiki/")[-1].split("?")[0].replace("_", " ").replace("-", " ")
            item_tokens = self._significant_tokens(item_name)
            if item_tokens and not any(t in wiki_slug for t in item_tokens):
                logger.info(
                    "Wikipedia entity-path mismatch for %s '%s': %s",
                    kind, item_name, url,
                )
                return ""
        # Compound entity check: a URL cannot be entity-specific for a name
        # that joins multiple distinct POIs with ' & '.
        if " & " in (item_name or ""):
            logger.info("Compound entity name rejected URL for %s '%s': %s", kind, item_name, url)
            return ""
        if allow_alltrails and self._is_alltrails_trail_url(url):
            if not self._meets_alltrails_publish_confidence(url, item_name, dest_name):
                return ""
        if not is_safe_fallback and not self._is_relevant_result(url, item_name, dest_name):
            return ""

        policy_class = self._classify_url_policy_class(url)
        blocked_classes = getattr(self, "_url_policy_blocked_classes", set(DEFAULT_URL_POLICY_BLOCKED_CLASSES))
        policy_mode = getattr(self, "_url_policy_mode", DEFAULT_URL_POLICY_MODE)
        blocked = policy_class in blocked_classes
        if blocked and policy_mode == "enforce":
            logger.info(
                "URL policy rejected [%s] for %s '%s' (%s): %s",
                policy_class,
                kind,
                item_name or "unknown",
                dest_name or "unknown destination",
                url,
            )
            return ""
        if blocked and policy_mode == "monitor":
            logger.info(
                "URL policy monitor hit [%s] for %s '%s' (%s): %s",
                policy_class,
                kind,
                item_name or "unknown",
                dest_name or "unknown destination",
                url,
            )
        return url

    @staticmethod
    def _classify_url_policy_class(url: str) -> str:
        lower = (url or "").lower()
        if "google.com/maps/dir/" in lower or "maps.google.com/maps/dir/" in lower:
            return "google_maps_dir"
        if "google.com/maps/search" in lower:
            return "google_maps_search"
        if "google.com/search" in lower:
            return "google_search"
        if any(domain in lower for domain in ("facebook.com", "instagram.com", "tiktok.com", "x.com", "twitter.com")):
            return "social_media"
        if "alltrails.com" in lower:
            return "alltrails"
        return "general"

    def _meets_alltrails_publish_confidence(self, url: str, item_name: str, dest_name: str) -> bool:
        threshold = str(
            getattr(
                self,
                "_alltrails_min_confidence_for_publish",
                "low",
            )
            or "low"
        ).strip().lower()
        ranks = {"low": 1, "medium": 2, "high": 3}
        confidence = self._alltrails_confidence_level(url, item_name, dest_name)
        return ranks.get(confidence, 1) >= ranks.get(threshold, 3)

    def _alltrails_confidence_level(self, url: str, item_name: str, dest_name: str) -> str:
        if not self._is_alltrails_trail_url(url):
            return "high"
        if not self._alltrails_slug_matches_item(url, item_name):
            return "low"
        if self._alltrails_slug_has_numbered_suffix(url):
            return "low"

        item_tokens = self._significant_tokens(item_name)
        slug_extra_terms = self._alltrails_slug_extra_term_count(url, item_name)

        ok, status, text = self._fetch_page_text(url, timeout=8)
        if ok:
            lower_text = (text or "").lower()
            if any(marker in lower_text for marker in ALLTRAILS_404_MARKERS):
                return "low"
            if self._text_matches_item_tokens(lower_text, item_tokens):
                return "high"
            if slug_extra_terms == 0:
                return "medium"
            return "low"

        if isinstance(status, int) and status in (404, 410):
            return "low"

        blocked = isinstance(status, int) and status in (401, 403)
        if blocked:
            # Blocked fetches are common; only strict slug matches qualify as medium.
            if slug_extra_terms == 0:
                return "medium"
            return "low"

        return "low"

    def _is_restaurant_ineligible(self, rest: dict[str, Any], dest_name: str) -> bool:
        """Return True when a restaurant entry should be excluded from recommendations.

        Checks, in order:
        1. Name-based denylist (known-closed / pre-opening venues from config)
        2. Page-text closure and pre-opening markers from the discovered URL
        """
        name = str(rest.get("name", "") or "").strip()
        name_lower = name.lower()

        if name_lower in getattr(self, "_restaurant_name_denylist", frozenset()):
            logger.info("  Restaurant name denylist hit: '%s' (%s)", name, dest_name)
            return True

        url = str(rest.get("url", "") or "").strip()
        if not url or any(url.lower().startswith(p) for p in SAFE_FALLBACK_URL_PREFIXES):
            return False  # Cannot check status from a fallback/search URL

        try:
            ok, _status, text = self._fetch_page_text(url, timeout=6)
            if not ok or not text:
                return False
            text_lower = text.lower()
            for marker in RESTAURANT_CLOSURE_MARKERS:
                if marker in text_lower:
                    logger.info(
                        "  Restaurant closure marker '%s' found for '%s' (%s): %s",
                        marker, name, dest_name, url,
                    )
                    return True
            for marker in RESTAURANT_PRE_OPENING_MARKERS:
                if marker in text_lower:
                    logger.info(
                        "  Restaurant pre-opening marker '%s' found for '%s' (%s): %s",
                        marker, name, dest_name, url,
                    )
                    return True
        except Exception:
            pass
        return False

    def _deduplicate_within_destination(self, dest: dict[str, Any]) -> None:
        """Remove scenic_drives entries whose name tokens duplicate a top_attractions entry.

        When the same geographic entity appears in both top_attractions and scenic_drives
        (e.g., Dead Horse Point State Park as both an attraction and a viewpoint card),
        the scenic_drives entry is the weaker representation and is removed.
        """
        ai = dest.get("ai_content", {}) if isinstance(dest.get("ai_content", {}), dict) else {}
        attractions = ai.get("top_attractions", []) or []
        drives = dest.get("scenic_drives", []) or []
        if not attractions or not drives:
            return

        attr_token_sets: list[frozenset[str]] = []
        for attr in attractions:
            name = str(attr.get("name", "") or "")
            tokens = frozenset(self._significant_tokens(name))
            if len(tokens) >= 2:
                attr_token_sets.append(tokens)

        if not attr_token_sets:
            return

        kept_drives: list[dict[str, Any]] = []
        for drive in drives:
            title = str(drive.get("title", "") or "")
            drive_tokens = frozenset(self._significant_tokens(title))
            if not drive_tokens:
                kept_drives.append(drive)
                continue

            duplicate = False
            for attr_tokens in attr_token_sets:
                if not attr_tokens:
                    continue
                overlap = len(attr_tokens & drive_tokens)
                min_len = min(len(attr_tokens), len(drive_tokens))
                if min_len >= 2 and overlap / min_len >= 0.8:
                    logger.info(
                        "  Within-destination dedup: removing scenic drive '%s' "
                        "(duplicates attraction in '%s')",
                        title,
                        dest.get("name", ""),
                    )
                    duplicate = True
                    break

            if not duplicate:
                kept_drives.append(drive)

        dest["scenic_drives"] = kept_drives

    @staticmethod
    def _log_rejected_url(kind: str, dest_name: str, item_name: str, url: str) -> None:
        lower_url = (url or "").lower()
        expected_policy_rejection = (
            "alltrails.com" in lower_url
            and kind in {"scenic drive", "en-route stop", "restaurant", "event"}
        )
        level = logging.INFO if (kind == "scenic drive" and url == "[removed]") or expected_policy_rejection else logging.WARNING
        logger.log(
            level,
            "Rejected %s URL for '%s' (%s): %s",
            kind,
            item_name or "unknown",
            dest_name or "unknown destination",
            url or "(empty)",
        )

    # ── Attractions ──────────────────────────────────────────────────────────

    def _discover_attractions(
        self,
        ai: dict[str, Any],
        dest_name: str,
        nps_code: str | None,
        dest_dates: str | None = None,
    ) -> None:
        for attr in ai.get("top_attractions", []):
            attr_name = attr.get("name", "")
            attr_type = str(attr.get("type", "attraction") or "attraction").lower()
            attr_desc = str(attr.get("description", "") or "")
            attr_context = self._attraction_trail_context(attr)

            if self._is_uninterested_attraction(attr_name, attr_type, attr_desc, dest_dates):
                attr["url"] = ""
                logger.info("  attraction link skipped by interest filter: %s", attr_name)
                continue

            trail_like = self._is_trail_like_attraction(attr_name, attr_type, attr_context)

            ai_candidate_url = self._resolve_ai_candidate_url(
                item=attr,
                item_name=attr_name,
                dest_name=dest_name,
                allow_alltrails=trail_like,
                trail_like=trail_like,
                kind="attraction",
            )
            if ai_candidate_url:
                attr["url"] = ai_candidate_url
                logger.info("  attraction link (ai-candidate): %s -> %s", attr_name, ai_candidate_url)
                continue

            # Trail-like items should prefer AllTrails first.
            if trail_like:
                url = self._search_alltrails_for_trail(attr_name, dest_name)
                if url and not self._meets_alltrails_publish_confidence(url, attr_name, dest_name):
                    logger.info(
                        "  trail-like link (alltrails) downgraded by confidence gate: %s -> %s",
                        attr_name,
                        url,
                    )
                    url = None
                if url:
                    attr["url"] = url
                    logger.info("  trail-like link (alltrails): %s -> %s", attr_name, url)
                    continue
                q = self._maps_fallback_query_text(attr_name, dest_name)
                attr["url"] = f"https://www.google.com/maps/search/?api=1&query={quote(q)}"
                logger.info("  trail-like link (fallback maps): %s -> (alltrails none)", attr_name)
                continue

            # For NPS parks, prefer nps.gov results
            site_hint = f"site:nps.gov/{nps_code}" if nps_code else None
            url = self._search_first(
                _build_query_variants(attr_name, dest_name, "attraction landmark museum viewpoint"),
                site_filter="nps.gov" if nps_code else None,
                site_hint=site_hint,
                item_name=attr_name,
                dest_name=dest_name,
                allow_alltrails=trail_like,
            )
            # Fallback: broad search; keep AllTrails allowed for trail-like items.
            if not url:
                url = self._search_first(
                    _build_query_variants(attr_name, dest_name, "attraction landmark museum viewpoint"),
                    item_name=attr_name,
                    dest_name=dest_name,
                    allow_alltrails=trail_like,
                )
            if url:
                attr["url"] = url
            else:
                q = self._maps_fallback_query_text(attr_name, dest_name)
                attr["url"] = f"https://www.google.com/maps/search/?api=1&query={quote(q)}"
            logger.info("  attraction link: %s -> %s", attr_name, (url or "(none)"))

    def _is_uninterested_attraction(
        self,
        attr_name: str,
        attr_type: str,
        description: str,
        dest_dates: str | None,
    ) -> bool:
        haystack = f" {attr_name} {attr_type} {description} ".lower()

        uninterested_keywords = getattr(
            self,
            "_uninterested_keywords",
            DEFAULT_UNINTERESTED_ATTRACTION_KEYWORDS,
        )
        if any(keyword in haystack for keyword in uninterested_keywords):
            return True

        ski_keywords = getattr(
            self,
            "_seasonal_ski_keywords",
            DEFAULT_SKI_ATTRACTION_KEYWORDS,
        )
        ski_signal = False
        for keyword in ski_keywords:
            k = str(keyword or "").strip().lower()
            if not k:
                continue
            if k == "ski":
                if re.search(r"\bski(?:ing|er|ers)?\b", haystack):
                    ski_signal = True
                    break
                continue
            if k in haystack:
                ski_signal = True
                break

        if not ski_signal:
            return False

        months = self._extract_month_numbers(dest_dates or "")
        if not months:
            return False

        in_season = set(getattr(self, "_ski_in_season_months", DEFAULT_SKI_IN_SEASON_MONTHS))
        return not any(month in in_season for month in months)

    @staticmethod
    def _extract_month_numbers(text: str) -> set[int]:
        out: set[int] = set()
        lowered = (text or "").lower()
        for month_name, month_number in MONTH_NAME_TO_NUMBER.items():
            if re.search(rf"\b{month_name}\b", lowered):
                out.add(month_number)
        return out

    def _search_alltrails_for_trail(self, item_name: str, dest_name: str) -> str | None:
        """Exhaust high-signal AllTrails queries before non-AllTrails fallback."""
        alltrails_variants: list[str] = _build_alltrails_query_variants(item_name, dest_name)

        # Preserve order while removing duplicates.
        seen: set[str] = set()
        deduped_variants: list[str] = []
        for q in alltrails_variants:
            key = (q or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped_variants.append(q)

        if bool(getattr(self, "_enable_filtered_alltrails_selection", DEFAULT_ENABLE_FILTERED_ALLTRAILS_SELECTION)):
            # Use constrained selection first. For known high-risk trails we fail
            # closed; for other trails we can still fall back to broader AllTrails
            # matching when snippets lack complete metadata.
            filtered = self._get_filtered_alltrails_selection(
                item_name=item_name,
                dest_name=dest_name,
                query_variants=deduped_variants,
            )
            if filtered:
                return filtered
            if self._requires_strict_filtered_alltrails(item_name):
                return None

        resolved = self._search_first(
            deduped_variants,
            site_filter="alltrails.com",
            item_name=item_name,
            dest_name=dest_name,
            max_attempts=min(len(deduped_variants), int(getattr(self, "_max_alltrails_query_attempts", 5) or 5)),
        )
        return self._prefer_canonical_alltrails_url(resolved, item_name)

    def _requires_strict_filtered_alltrails(self, item_name: str) -> bool:
        name_l = str(item_name or "").strip().lower()
        if not name_l:
            return False
        strict_names = getattr(self, "_strict_filtered_alltrails_names", DEFAULT_STRICT_FILTERED_ALLTRAILS_NAMES)
        return name_l in set(strict_names)

    def _get_filtered_alltrails_selection(
        self,
        *,
        item_name: str,
        dest_name: str,
        query_variants: list[str] | None = None,
    ) -> str | None:
        if not hasattr(self, "_alltrails_filtered_selection_cache"):
            self._alltrails_filtered_selection_cache = {}
        cache_key = ((item_name or "").strip().lower(), (dest_name or "").strip().lower())
        if cache_key in self._alltrails_filtered_selection_cache:
            return self._alltrails_filtered_selection_cache[cache_key]

        variants = query_variants if query_variants is not None else _build_alltrails_query_variants(item_name, dest_name)
        filtered = self._search_alltrails_for_trail_filtered(
            item_name=item_name,
            dest_name=dest_name,
            query_variants=variants,
        )
        self._alltrails_filtered_selection_cache[cache_key] = filtered
        return filtered

    @staticmethod
    def _same_alltrails_trail(url_a: str, url_b: str) -> bool:
        if not url_a or not url_b:
            return False
        a = urlparse(url_a)
        b = urlparse(url_b)
        if "alltrails.com" not in (a.netloc or "").lower() or "alltrails.com" not in (b.netloc or "").lower():
            return False
        return unquote((a.path or "").rstrip("/")).lower() == unquote((b.path or "").rstrip("/")).lower()

    def _search_alltrails_for_trail_filtered(
        self,
        *,
        item_name: str,
        dest_name: str,
        query_variants: list[str],
    ) -> str | None:
        max_attempts = min(
            len(query_variants),
            int(getattr(self, "_max_alltrails_query_attempts", 5) or 5),
        )
        allowed_difficulties = set(
            getattr(
                self,
                "_alltrails_filter_allowed_difficulties",
                DEFAULT_ALLTRAILS_FILTER_ALLOWED_DIFFICULTIES,
            )
        )
        max_miles = float(getattr(self, "_alltrails_filter_max_miles", DEFAULT_ALLTRAILS_FILTER_MAX_MILES) or 0)
        max_gain = int(getattr(self, "_alltrails_filter_max_gain_feet", DEFAULT_ALLTRAILS_FILTER_MAX_GAIN_FEET) or 0)
        min_reviews = int(getattr(self, "_alltrails_filter_min_reviews", DEFAULT_ALLTRAILS_FILTER_MIN_REVIEWS) or 0)

        best: tuple[float, int, float, int, str] | None = None

        for query in query_variants[:max_attempts]:
            full_query = f"site:alltrails.com {query}"
            candidates = self._search.search(full_query, count=10)
            for candidate in candidates:
                url = str(candidate.get("url", "") or "")
                if not self._is_alltrails_trail_url(url):
                    continue
                if not self._alltrails_slug_matches_item(url, item_name):
                    continue
                if self._alltrails_slug_has_numbered_suffix(url):
                    continue

                meta = self._extract_alltrails_candidate_metadata(candidate)
                difficulty = str(meta.get("difficulty") or "").lower()
                if difficulty not in allowed_difficulties:
                    continue
                miles = meta.get("miles")
                if miles is None or (max_miles > 0 and float(miles) > max_miles):
                    continue
                gain_feet = meta.get("gain_feet")
                if gain_feet is None or (max_gain >= 0 and int(gain_feet) > max_gain):
                    continue
                reviews = meta.get("reviews")
                if reviews is None or int(reviews) < min_reviews:
                    continue
                rating = meta.get("rating")
                if rating is None:
                    continue

                # Highest rating first, then review volume, then shorter/easier trail.
                rank = (
                    float(rating),
                    int(reviews),
                    -float(miles),
                    -int(gain_feet),
                    url,
                )
                if best is None or rank > best:
                    best = rank

        if not best:
            return None

        return self._prefer_canonical_alltrails_url(best[-1], item_name)

    def _extract_alltrails_candidate_metadata(self, candidate: dict[str, Any] | None) -> dict[str, Any]:
        text = self._candidate_text_blob(candidate)
        rating, reviews = self._extract_rating_votes(text)
        difficulty = self._extract_alltrails_difficulty(text)
        miles = self._extract_trail_miles(text)
        gain_feet = self._extract_elevation_gain_feet(text)
        return {
            "rating": rating,
            "reviews": reviews,
            "difficulty": difficulty,
            "miles": miles,
            "gain_feet": gain_feet,
        }

    @staticmethod
    def _extract_alltrails_difficulty(text: str) -> str | None:
        t = str(text or "").lower()
        if "moderately challenging" in t:
            return "moderately challenging"
        if re.search(r"\bmoderate\b", t):
            return "moderate"
        if re.search(r"\beasy\b", t):
            return "easy"
        if re.search(r"\b(hard|difficult|challenging)\b", t):
            return "hard"
        return None

    @staticmethod
    def _extract_elevation_gain_feet(text: str) -> int | None:
        t = str(text or "").lower()

        patterns = (
            r"(?:elevation\s*gain|gain)\s*[:\-]?\s*(\d{1,3}(?:,\d{3})?|\d+)\s*(?:ft|feet|foot)\b",
            r"(\d{1,3}(?:,\d{3})?|\d+)\s*(?:ft|feet|foot)\s*(?:elevation\s*gain|gain)\b",
        )
        for pattern in patterns:
            m = re.search(pattern, t, flags=re.IGNORECASE)
            if not m:
                continue
            try:
                return int(str(m.group(1)).replace(",", ""))
            except (TypeError, ValueError):
                continue
        return None

    def _prefer_canonical_alltrails_url(self, url: str | None, item_name: str) -> str | None:
        """Prefer verified canonical AllTrails slug over broader '/via-' variants."""
        if not url or not self._is_alltrails_trail_url(url):
            return url

        url = self._strip_alltrails_tracking(url)

        parsed = urlparse(url)
        slug = unquote(parsed.path.rsplit("/", 1)[-1]).lower()
        if not self._is_noisy_alltrails_slug(url, item_name):
            return url

        parent = parsed.path.rsplit("/", 1)[0]
        name_tokens = [t for t in re.findall(r"[a-z0-9]+", (item_name or "").lower()) if t]
        if not name_tokens:
            return url
        base_slug = "-".join(name_tokens)

        candidates: list[str] = []
        if not base_slug.endswith("-trail"):
            candidates.append(f"{parsed.scheme}://{parsed.netloc}{parent}/{base_slug}-trail")
        candidates.append(f"{parsed.scheme}://{parsed.netloc}{parent}/{base_slug}")

        item_tokens = self._significant_tokens(item_name)
        fallback_canonical: str | None = None
        for candidate in candidates:
            if candidate == url:
                continue
            if not self._alltrails_slug_matches_item(candidate, item_name):
                continue
            ok, status, text = self._fetch_page_text(candidate, timeout=8)
            if not ok:
                # Canonical pages are frequently bot-protected for scripted fetches.
                # Keep the canonical candidate as fallback when it is a strict
                # token match with no extra slug terms.
                if self._alltrails_slug_extra_term_count(candidate, item_name) == 0:
                    fallback_canonical = fallback_canonical or candidate
                continue
            lower_text = (text or "").lower()
            if any(marker in lower_text for marker in ALLTRAILS_404_MARKERS):
                continue
            if item_tokens and not self._text_matches_item_tokens(lower_text, item_tokens):
                continue
            return candidate

        if fallback_canonical:
            return fallback_canonical

        return url

    @staticmethod
    def _strip_alltrails_tracking(url: str) -> str:
        parsed = urlparse(url)
        if "alltrails.com" not in (parsed.netloc or "").lower():
            return url
        cleaned = parsed._replace(query="", fragment="")
        return cleaned.geturl()

    # ── Restaurants — two-pass ───────────────────────────────────────────────

    def _discover_restaurants(self, ai: dict[str, Any], dest_name: str) -> None:
        for rest in ai.get("dinner_recommendations", []):
            rest_name = rest.get("name", "")
            maps_fallback_url = (
                f"https://www.google.com/maps/search/?api=1&query={quote(self._restaurant_maps_query_text(rest_name, dest_name))}"
            )
            restaurant_variants = _build_restaurant_query_variants(rest_name, dest_name)

            ai_candidate_url = self._resolve_ai_candidate_url(
                item=rest,
                item_name=rest_name,
                dest_name=dest_name,
                allow_alltrails=False,
                trail_like=False,
                kind="restaurant",
                normalize_restaurant=True,
            )
            if ai_candidate_url:
                rest["url"] = ai_candidate_url
                rest["maps_url"] = maps_fallback_url
                logger.info("  restaurant link (ai-candidate): %s -> %s", rest_name, ai_candidate_url)
                continue

            # Pass 1: Google Maps
            url = self._search_first(
                restaurant_variants,
                site_filter="google.com/maps",
                item_name=rest_name,
                dest_name=dest_name,
                max_attempts=int(getattr(self, "_max_restaurant_query_attempts", 3) or 3),
            )
            # Pass 2: TripAdvisor
            if not url:
                url = self._search_first(
                    restaurant_variants,
                    site_filter="tripadvisor.com",
                    item_name=rest_name,
                    dest_name=dest_name,
                    max_attempts=int(getattr(self, "_max_restaurant_query_attempts", 3) or 3),
                )
            url = self._normalize_restaurant_url(url)
            rest["url"] = url or maps_fallback_url
            rest["maps_url"] = maps_fallback_url
            logger.info("  restaurant link: %s -> %s", rest_name, (url or "(none)"))

    @staticmethod
    def _normalize_restaurant_url(url: str | None) -> str:
        if not url:
            return ""
        normalized = str(url).strip()
        lower = normalized.lower()
        if "google.com/maps/dir/" in lower or "maps.google.com/maps/dir/" in lower:
            return ""
        parsed = urlparse(normalized)
        if "google.com" in parsed.netloc.lower():
            path_l = parsed.path.lower()
            # /maps/place links are frequently fabricated or over-normalized into
            # non-resolving slugs; prefer query-based maps/search fallbacks instead.
            if path_l.startswith("/maps/place/"):
                return ""
            if path_l.startswith("/maps/@"):
                return ""
            if path_l.rstrip("/") in {"/maps", ""}:
                return ""
        return normalized

    @classmethod
    def _restaurant_maps_query_text(cls, rest_name: str, dest_name: str) -> str:
        name = str(rest_name or "").strip()
        dest = str(dest_name or "").strip()
        query = cls._maps_fallback_query_text(name, dest)

        # Always keep destination context for restaurant lookups, even when
        # the place name already contains overlapping tokens (e.g., "Zion ...").
        if name and dest and dest.lower() not in query.lower():
            query = f"{name} {dest}".strip()

        lowered = query.lower()
        if not any(term in lowered for term in ("restaurant", "cafe", "diner", "bistro", "grill", "saloon")):
            query = f"{query} restaurant".strip()

        return query

    def _resolve_ai_candidate_url(
        self,
        *,
        item: dict[str, Any],
        item_name: str,
        dest_name: str,
        allow_alltrails: bool,
        trail_like: bool,
        kind: str,
        normalize_restaurant: bool = False,
    ) -> str | None:
        candidates = item.get("url_candidates", []) if isinstance(item, dict) else []
        if not isinstance(candidates, list):
            return None
        parsed_candidates = [str(c or "").strip() for c in candidates if str(c or "").strip()]
        if not parsed_candidates:
            return None

        # For trail-like attractions, evaluate AllTrails candidates first.
        if trail_like:
            parsed_candidates.sort(key=lambda u: 0 if self._is_alltrails_trail_url(u) else 1)

        for candidate in parsed_candidates:
            candidate_url = candidate
            lower = candidate_url.lower()
            if lower.startswith("www."):
                candidate_url = "https://" + candidate_url
            if not candidate_url.lower().startswith(("http://", "https://")):
                continue
            if self._is_alltrails_trail_url(candidate_url):
                candidate_url = self._prefer_canonical_alltrails_url(candidate_url, item_name) or ""
                if trail_like and bool(getattr(self, "_enable_filtered_alltrails_selection", DEFAULT_ENABLE_FILTERED_ALLTRAILS_SELECTION)):
                    filtered_selected = self._get_filtered_alltrails_selection(item_name=item_name, dest_name=dest_name)
                    if not filtered_selected:
                        if self._requires_strict_filtered_alltrails(item_name):
                            logger.warning(
                                "  %s alltrails candidate failed metadata constraints: %s -> %s",
                                kind,
                                item_name,
                                candidate_url,
                            )
                            continue
                    elif not self._same_alltrails_trail(candidate_url, filtered_selected):
                        logger.warning(
                            "  %s alltrails candidate mismatched filtered selection: %s -> %s (selected: %s)",
                            kind,
                            item_name,
                            candidate_url,
                            filtered_selected,
                        )
                        continue
            if normalize_restaurant:
                candidate_url = self._normalize_restaurant_url(candidate_url)
            cleaned = self._retain_discovered_url(
                candidate_url,
                item_name,
                dest_name,
                allow_alltrails=allow_alltrails,
                kind=kind,
            )
            if cleaned and trail_like and self._is_alltrails_trail_url(cleaned):
                if not self._meets_alltrails_publish_confidence(cleaned, item_name, dest_name):
                    logger.info(
                        "  %s ai-candidate downgraded by confidence gate: %s -> %s",
                        kind,
                        item_name,
                        cleaned,
                    )
                    cleaned = ""
            if cleaned:
                return cleaned

            logger.info("  %s ai-candidate rejected: %s -> %s", kind, item_name, candidate_url)

        return None

    # ── En-Route Stops ───────────────────────────────────────────────────────

    def _discover_en_route_stops(self, ai: dict[str, Any], dest_name: str) -> None:
        for stop in ai.get("getting_here", {}).get("en_route_stops", []):
            stop_name = stop.get("name", "")
            url = self._search_first(
                _build_query_variants(stop_name, dest_name, "attraction stop"),
                item_name=stop_name,
                dest_name=dest_name,
                allow_alltrails=False,
            )
            if url:
                stop["url"] = url
            else:
                q = self._maps_fallback_query_text(stop_name, dest_name)
                stop["url"] = f"https://www.google.com/maps/search/?api=1&query={quote(q)}"
            logger.info("  en-route link: %s -> %s", stop_name, (url or "(none)"))

    # ── Scenic Drives ────────────────────────────────────────────────────────

    def _discover_scenic_drives(self, dest: dict[str, Any], dest_name: str) -> None:
        for drive in dest.get("scenic_drives", []):
            drive_name = drive.get("title", "")
            url = self._search_first(
                _build_query_variants(drive_name, dest_name, "scenic drive viewpoint"),
                item_name=drive_name,
                dest_name=dest_name,
                allow_alltrails=False,
            )
            # Keep scenic-drive/day-trip popup links optional: only include when discovery
            # produces a verified relevant URL.
            drive["url"] = url or ""
            logger.info("  scenic drive link: %s -> %s", drive_name, (url or "(none)"))

    # ── Bing Search helpers ──────────────────────────────────────────────────

    def _search_first(
        self,
        query_variants: list[str],
        site_filter: str | None = None,
        site_hint: str | None = None,
        item_name: str = "",
        dest_name: str = "",
        allow_alltrails: bool = True,
        max_attempts: int = MAX_FALLBACK_ATTEMPTS,
    ) -> str | None:
        # Check cache first
        cache_key = (item_name, dest_name, site_filter or "", "alltrails" if allow_alltrails else "no-alltrails")
        if cache_key in _url_cache:
            logger.info("  cache hit: %s (%s) -> %s", item_name, site_filter or "any", _url_cache[cache_key] or "(none)")
            return _url_cache[cache_key]
        
        # Search and cache result
        result = self._search_first_strict(
            query_variants=query_variants,
            site_filter=site_filter,
            site_hint=site_hint,
            item_name=item_name,
            dest_name=dest_name,
            allow_alltrails=allow_alltrails,
            max_attempts=max_attempts,
        )
        _url_cache[cache_key] = result
        logger.info("  resolved: %s (%s) -> %s", item_name, site_filter or "any", result or "(none)")
        return result

    def _search_first_strict(
        self,
        *,
        query_variants: list[str],
        site_filter: str | None,
        site_hint: str | None,
        item_name: str,
        dest_name: str,
        allow_alltrails: bool,
        max_attempts: int = MAX_FALLBACK_ATTEMPTS,
    ) -> str | None:
        best: tuple[int, str] | None = None

        for query in query_variants[:max_attempts]:
            full_query = f"{site_hint} {query}" if site_hint else (f"site:{site_filter} {query}" if site_filter else query)
            candidates = self._search.search(full_query, count=10)

            # Pass 1: specific pages only
            for item in candidates:
                url = item.get("url", "")
                if not url:
                    continue
                candidate_url = url
                if site_filter == "google.com/maps":
                    candidate_url = self._normalize_restaurant_url(candidate_url)
                    if not candidate_url:
                        continue
                if site_filter and site_filter not in url:
                    continue
                if site_filter == "alltrails.com" and not self._is_alltrails_trail_url(candidate_url):
                    continue
                if not allow_alltrails and "alltrails.com" in candidate_url.lower():
                    continue
                if not self._is_specific_result_url(candidate_url, item_name, dest_name):
                    continue
                scored_item = dict(item)
                scored_item["url"] = candidate_url
                if self._is_alltrails_trail_url(candidate_url):
                    if self._is_relevant_result(candidate_url, item_name, dest_name, candidate=scored_item):
                        score = self._score_candidate_result(
                            scored_item,
                            item_name,
                            dest_name,
                            specific=True,
                            site_filter=site_filter,
                        )
                        best = self._pick_better_candidate(best, score, candidate_url)
                    continue
                ok, _ = self._url_validator.verify_url(candidate_url)
                if ok and self._is_relevant_result(candidate_url, item_name, dest_name):
                    score = self._score_candidate_result(
                        scored_item,
                        item_name,
                        dest_name,
                        specific=True,
                        site_filter=site_filter,
                    )
                    best = self._pick_better_candidate(best, score, candidate_url)

            # Pass 2: any live URL for this variant as fallback
            item_tokens = self._significant_tokens(item_name)
            for item in candidates:
                url = item.get("url", "")
                if not url:
                    continue
                candidate_url = url
                if site_filter == "google.com/maps":
                    candidate_url = self._normalize_restaurant_url(candidate_url)
                    if not candidate_url:
                        continue
                if site_filter and site_filter not in url:
                    continue
                if self._is_obviously_generic_url(candidate_url.lower()):
                    continue
                if site_filter == "alltrails.com" and not self._is_alltrails_trail_url(candidate_url):
                    continue
                # Keep broad-pass matches from degenerating into generic pages.
                if site_filter == "nps.gov":
                    if not self._candidate_text_matches_item_tokens(item, item_tokens) and not any(
                        token in candidate_url.lower() for token in item_tokens
                    ):
                        continue
                if not allow_alltrails and "alltrails.com" in candidate_url.lower():
                    continue
                scored_item = dict(item)
                scored_item["url"] = candidate_url
                if self._is_alltrails_trail_url(candidate_url):
                    if self._is_relevant_result(candidate_url, item_name, dest_name, candidate=scored_item):
                        score = self._score_candidate_result(
                            scored_item,
                            item_name,
                            dest_name,
                            specific=False,
                            site_filter=site_filter,
                        )
                        best = self._pick_better_candidate(best, score, candidate_url)
                    continue
                ok, _ = self._url_validator.verify_url(candidate_url)
                if ok and self._is_relevant_result(candidate_url, item_name, dest_name):
                    score = self._score_candidate_result(
                        scored_item,
                        item_name,
                        dest_name,
                        specific=False,
                        site_filter=site_filter,
                    )
                    best = self._pick_better_candidate(best, score, candidate_url)

        if best:
            logger.debug("  URL selected by score=%s -> %s", best[0], best[1][:120])
            return best[1]
        return None

    @staticmethod
    def _pick_better_candidate(current: tuple[int, str] | None, score: int, url: str) -> tuple[int, str]:
        if current is None or score > current[0]:
            return score, url
        return current

    def _score_candidate_result(
        self,
        item: dict[str, Any],
        item_name: str,
        dest_name: str,
        *,
        specific: bool,
        site_filter: str | None = None,
    ) -> int:
        url = str(item.get("url", "") or "")
        title = str(item.get("name", "") or "")
        snippet = str(item.get("snippet", "") or "")

        lower_url = url.lower()
        parsed = urlparse(url)
        host_path = f"{parsed.netloc}{parsed.path}".lower()
        text = f"{title} {snippet}".lower()

        score = 0
        item_tokens = self._significant_tokens(item_name)
        dest_tokens = self._significant_tokens(dest_name)

        item_overlap_url = sum(1 for t in item_tokens if t in host_path)
        item_overlap_text = sum(1 for t in item_tokens if t in text)
        dest_overlap_url = sum(1 for t in dest_tokens if t in host_path)
        dest_overlap_text = sum(1 for t in dest_tokens if t in text)

        score += item_overlap_url * 6
        score += item_overlap_text * 4
        score += dest_overlap_url * 4
        score += dest_overlap_text * 3

        if self._is_alltrails_trail_url(url):
            slug = unquote(parsed.path.rsplit("/", 1)[-1]).replace("-", " ")
            slug_tokens = self._significant_tokens(slug)
            if slug_tokens and item_tokens:
                item_set = set(item_tokens)
                extra_slug_terms = [t for t in slug_tokens if t not in item_set]
                # Prefer canonical trail page slugs over broader route variants.
                score -= len(extra_slug_terms) * 3
            if "-via-" in parsed.path.lower() or " via " in slug.lower():
                # "via" pages are often broader route variants than the canonical trail page.
                score -= 4

        if dest_name and dest_name.lower() in text:
            score += 10

        for hint in POSITIVE_DOMAIN_HINTS:
            if hint in parsed.netloc.lower():
                score += 4
        for hint in NEGATIVE_DOMAIN_HINTS:
            if hint in parsed.netloc.lower():
                score -= 5

        inferred_tlds = self._destination_country_tlds(dest_name)
        netloc = parsed.netloc.lower()
        for tld in inferred_tlds:
            if tld == "co.uk":
                if netloc.endswith(".co.uk") or netloc == "co.uk":
                    score += 5
                continue
            if netloc.endswith(f".{tld}") or netloc == tld:
                score += 5

        path_l = parsed.path.lower()
        for hint in POSITIVE_PATH_HINTS:
            if hint in path_l:
                score += 3

        if specific:
            score += 2

        rating, votes = self._extract_rating_votes(f"{title} {snippet}")
        if self._is_alltrails_trail_url(url) or site_filter == "alltrails.com":
            score += self._rating_priority_boost(
                rating=rating,
                votes=votes,
                min_rating=float(getattr(self, "_alltrails_rating_min", DEFAULT_ALLTRAILS_RATING_MIN)),
                min_votes=int(
                    getattr(self, "_alltrails_rating_min_votes", DEFAULT_ALLTRAILS_RATING_MIN_VOTES)
                ),
                boost=int(getattr(self, "_alltrails_rating_boost", DEFAULT_ALLTRAILS_RATING_BOOST)),
            )
        elif site_filter in {"google.com/maps", "tripadvisor.com"}:
            score += self._rating_priority_boost(
                rating=rating,
                votes=votes,
                min_rating=float(getattr(self, "_restaurant_rating_min", DEFAULT_RESTAURANT_RATING_MIN)),
                min_votes=int(
                    getattr(self, "_restaurant_rating_min_votes", DEFAULT_RESTAURANT_RATING_MIN_VOTES)
                ),
                boost=int(getattr(self, "_restaurant_rating_boost", DEFAULT_RESTAURANT_RATING_BOOST)),
            )

        return score

    @staticmethod
    def _rating_priority_boost(
        *,
        rating: float | None,
        votes: int | None,
        min_rating: float,
        min_votes: int,
        boost: int,
    ) -> int:
        # High ratings are prioritized only when supported by sufficient votes.
        if rating is None or votes is None:
            return 0
        if rating < min_rating:
            return 0
        if votes < min_votes:
            return 0
        return max(0, int(boost))

    @staticmethod
    def _extract_rating_votes(text: str) -> tuple[float | None, int | None]:
        body = str(text or "")

        rating_match = re.search(
            r"(?:^|[^0-9])([0-4](?:\.\d+)?|5(?:\.0+)?)\s*(?:/\s*5|stars?)\b",
            body,
            flags=re.IGNORECASE,
        )
        rating = float(rating_match.group(1)) if rating_match else None

        votes: int | None = None
        k_votes_match = re.search(
            r"(\d+(?:\.\d+)?)\s*k\s*(?:reviews?|ratings?|votes?)\b",
            body,
            flags=re.IGNORECASE,
        )
        if k_votes_match:
            votes = int(float(k_votes_match.group(1)) * 1000)
        else:
            votes_match = re.search(
                r"(\d{1,3}(?:,\d{3})+|\d+)\s*(?:reviews?|ratings?|votes?)\b",
                body,
                flags=re.IGNORECASE,
            )
            if votes_match:
                votes = int(votes_match.group(1).replace(",", ""))

        return rating, votes

    def _is_specific_result_url(self, url: str, item_name: str, dest_name: str) -> bool:
        lower = url.lower()
        if self._is_obviously_generic_url(lower):
            return False
        if "google.com/search" in lower or "/search?" in lower:
            return False
        if "nps.gov" in lower and "/search" in lower:
            return False

        # Reject attribution/media pages that are not "more info" landing pages.
        if "commons.wikimedia.org" in lower or "wikipedia.org/wiki/file:" in lower:
            return False

        # Reject obvious generic landing pages.
        generic_patterns = [
            "/plan-your-visit",
            "/visit",
            "/things-to-do",
            "/things2do",
            "/explore",
            "/about",
            "/home",
            "/index.htm",
            "/index.html",
        ]
        if any(pattern in lower for pattern in generic_patterns):
            return False

        item_tokens = self._significant_tokens(item_name)
        if item_tokens and not any(token in lower for token in item_tokens):
            return False

        # If destination tokens exist in URL it's usually a much better match.
        dest_tokens = self._significant_tokens(dest_name)
        if dest_tokens and any(token in lower for token in dest_tokens):
            return True

        return True

    def _is_relevant_result(
        self,
        url: str,
        item_name: str,
        dest_name: str,
        candidate: dict[str, Any] | None = None,
    ) -> bool:
        """Lightweight relevance gate: avoid live but useless links."""
        if self._is_obviously_generic_url(url.lower()):
            return False
        if self._is_alltrails_trail_url(url):
            item_tokens = self._significant_tokens(item_name)
            if not self._alltrails_slug_matches_item(url, item_name):
                return False
            # Slug denylist fast-reject (known-invalid/dead slugs from config).
            _slug = urlparse(url).path.rsplit("/", 1)[-1].lower()
            if _slug in getattr(self, "_alltrails_slug_denylist", frozenset()):
                return False
            if self._alltrails_slug_has_numbered_suffix(url):
                return False
            max_trail_miles = float(getattr(self, "_max_trail_miles", DEFAULT_MAX_TRAIL_MILES) or DEFAULT_MAX_TRAIL_MILES)
            if max_trail_miles > 0:
                candidate_miles = self._extract_trail_miles(self._candidate_text_blob(candidate))
                if candidate_miles is not None and candidate_miles > max_trail_miles:
                    return False
            metadata_ok = self._candidate_text_matches_item_tokens(candidate, item_tokens)
            destination_ok = self._candidate_text_matches_destination_tokens(candidate, dest_name)
            listing_signal_ok = self._candidate_has_alltrails_listing_signal(candidate)
            slug_extra_terms = self._alltrails_slug_extra_term_count(url, item_name)
            try:
                ok, status, text = self._fetch_page_text(url, timeout=8)
                if not ok:
                    blocked_status = isinstance(status, int) and status in (401, 403)
                    if blocked_status and not bool(getattr(self, "_allow_blocked_alltrails", DEFAULT_ALLOW_BLOCKED_ALLTRAILS)):
                        return False
                    if candidate is not None:
                        if not metadata_ok:
                            return False
                        if len(item_tokens) <= 1 and not destination_ok:
                            return False
                        if slug_extra_terms >= 2 and not listing_signal_ok:
                            return False
                        return True
                    # Audit paths do not provide a search-result candidate. When
                    # AllTrails page text fetch fails (bot protection, transient
                    # network), keep strong slug matches by default. A separate
                    # liveness probe is often blocked and causes false negatives.
                    # Only reject explicit not-found statuses.
                    if candidate is None:
                        if isinstance(status, int) and status in (404, 410):
                            return False
                        return True
                    if isinstance(status, int) and status in (404, 410):
                        return False
                    # Search candidates may sometimes provide only URL with no
                    # snippet/title metadata and still be valid; slug match is
                    # already enforced above, so keep as fallback.
                    return True
                text = (text or "").lower()
                if any(marker in text for marker in ALLTRAILS_404_MARKERS):
                    return False
                # Redirect entity check: if AllTrails silently redirected to a
                # different trail, the final URL slug won't match the item.
                final_url = getattr(self, "_fetch_final_url_cache", {}).get(url, url)
                if final_url != url and self._is_alltrails_trail_url(final_url):
                    if not self._alltrails_slug_matches_item(final_url, item_name):
                        logger.info(
                            "AllTrails redirect entity mismatch: %s -> %s (item: %s)",
                            url, final_url, item_name,
                        )
                        return False
                if max_trail_miles > 0:
                    fetched_miles = self._extract_trail_miles(text)
                    if fetched_miles is not None and fetched_miles > max_trail_miles:
                        return False
                if not self._text_matches_item_tokens(text, item_tokens):
                    # Some AllTrails pages are bot-protected and may return sparse
                    # HTML despite a correct trail URL/result card. Keep strict slug
                    # matching and allow strong result metadata as a fallback signal.
                    if candidate is None:
                        return True
                    return metadata_ok
                # Destination names for gateway towns (e.g., Telluride) are often
                # omitted from AllTrails page copy even when the trail is correct.
                # Slug + on-page item-token matching is the primary relevance gate.
                return True
            except Exception:
                return metadata_ok
        try:
            ok, status, text = self._fetch_page_text(url, timeout=8)
            if not ok:
                return False
            text = (text or "").lower()
            item_tokens = self._significant_tokens(item_name)
            dest_tokens = self._significant_tokens(dest_name)
            if not self._text_matches_item_tokens(text, item_tokens):
                return False
            if dest_tokens and not any(t in text for t in dest_tokens[:2]):
                return False
            return True
        except Exception:
            return False

    def _fetch_page_text(self, url: str, timeout: int = 8) -> tuple[bool, int | str, str]:
        if self._is_alltrails_trail_url(url):
            return self._fetch_alltrails_text(url, timeout=timeout)
        return self._fetch_page_text_uncached(url, timeout=timeout)

    def _fetch_page_text_uncached(self, url: str, timeout: int = 8) -> tuple[bool, int | str, str]:
        if not hasattr(self, "_url_validator"):
            return False, "no_validator", ""
        get_text = getattr(self._url_validator, "get_text", None)
        if callable(get_text):
            try:
                out = get_text(url, timeout=timeout)
                if isinstance(out, tuple) and len(out) == 3:
                    return bool(out[0]), out[1], str(out[2] or "")
            except Exception:
                pass

        # Backward-compat fallback for tests/mocks that only expose session.get.
        try:
            resp = self._url_validator.session.get(url, timeout=timeout)
            # Track final URL after any redirect for entity-match verification.
            final_url = str(getattr(resp, "url", None) or url)
            if hasattr(self, "_fetch_final_url_cache") and final_url != url:
                self._fetch_final_url_cache[url] = final_url
            return resp.status_code < 400, resp.status_code, resp.text or ""
        except Exception as exc:
            return False, str(exc), ""

    def _fetch_alltrails_text(self, url: str, timeout: int = 8) -> tuple[bool, int | str, str]:
        # Some tests build URLDiscoverer via __new__; lazily initialize fields.
        if not hasattr(self, "_alltrails_fetch_cache"):
            self._alltrails_fetch_cache = {}
        if not hasattr(self, "_alltrails_fetch_lock"):
            self._alltrails_fetch_lock = Lock()
        if not hasattr(self, "_alltrails_last_request_ts"):
            self._alltrails_last_request_ts = 0.0
        if not hasattr(self, "_alltrails_blocked_until_ts"):
            self._alltrails_blocked_until_ts = 0.0

        delay_seconds = float(
            getattr(
                self,
                "_alltrails_request_delay_seconds",
                DEFAULT_ALLTRAILS_REQUEST_DELAY_SECONDS,
            )
            or 0.0
        )
        cooldown_seconds = float(
            getattr(
                self,
                "_alltrails_block_cooldown_seconds",
                DEFAULT_ALLTRAILS_BLOCK_COOLDOWN_SECONDS,
            )
            or 0.0
        )

        with self._alltrails_fetch_lock:
            cached = self._alltrails_fetch_cache.get(url)
            if cached is not None:
                return cached

            now = time.monotonic()
            blocked_until = float(getattr(self, "_alltrails_blocked_until_ts", 0.0) or 0.0)
            if blocked_until > now:
                time.sleep(blocked_until - now)

            if delay_seconds > 0:
                last_request = float(getattr(self, "_alltrails_last_request_ts", 0.0) or 0.0)
                elapsed = time.monotonic() - last_request
                if elapsed < delay_seconds:
                    time.sleep(delay_seconds - elapsed)

            self._alltrails_last_request_ts = time.monotonic()
            result = self._fetch_page_text_uncached(url, timeout=timeout)

            status = result[1]
            if isinstance(status, int) and status in (401, 403) and cooldown_seconds > 0:
                self._alltrails_blocked_until_ts = time.monotonic() + cooldown_seconds

            self._alltrails_fetch_cache[url] = result
            return result

    @staticmethod
    def _is_alltrails_trail_url(url: str) -> bool:
        lower = (url or "").lower()
        return "alltrails.com" in lower and "/trail/" in lower

    @classmethod
    def _alltrails_slug_matches_item(cls, url: str, item_name: str) -> bool:
        item_tokens = cls._significant_tokens(item_name)
        if not item_tokens:
            return True
        slug = unquote(urlparse(url).path.rsplit("/", 1)[-1]).replace("-", " ")
        slug_tokens = cls._significant_tokens(slug)
        if not slug_tokens:
            return False
        overlap = len(set(item_tokens) & set(slug_tokens))
        required = cls._required_alltrails_token_matches(len(item_tokens))
        return overlap >= required

    @staticmethod
    def _required_alltrails_token_matches(token_count: int) -> int:
        if token_count <= 1:
            return 1
        return max(2, ceil(token_count / 2))

    @staticmethod
    def _required_general_token_matches(token_count: int) -> int:
        if token_count <= 1:
            return 1
        return max(2, ceil(token_count / 2))

    @classmethod
    def _text_matches_item_tokens(cls, text: str, item_tokens: list[str]) -> bool:
        if not item_tokens:
            return True
        overlap = sum(1 for token in item_tokens if token in text)
        return overlap >= cls._required_general_token_matches(len(item_tokens))

    @classmethod
    def _candidate_text_matches_item_tokens(
        cls,
        candidate: dict[str, Any] | None,
        item_tokens: list[str],
    ) -> bool:
        if not candidate:
            return False
        text = cls._candidate_text_blob(candidate)
        return cls._text_matches_item_tokens(text, item_tokens)

    @classmethod
    def _candidate_text_matches_destination_tokens(
        cls,
        candidate: dict[str, Any] | None,
        dest_name: str,
    ) -> bool:
        if not candidate:
            return False
        dest_tokens = cls._significant_tokens(dest_name)
        if not dest_tokens:
            return False
        text = cls._candidate_text_blob(candidate)
        return any(token in text for token in dest_tokens[:2])

    @classmethod
    def _candidate_has_alltrails_listing_signal(cls, candidate: dict[str, Any] | None) -> bool:
        if not candidate:
            return False
        text = cls._candidate_text_blob(candidate)
        if "alltrails" not in text:
            return False
        return any(marker in text for marker in ("review", "rating", "map", "photos"))

    @classmethod
    def _is_noisy_alltrails_slug(cls, url: str, item_name: str) -> bool:
        parsed = urlparse(url)
        slug = unquote(parsed.path.rsplit("/", 1)[-1]).lower()
        if "-via-" in slug:
            return True
        if cls._alltrails_slug_has_numbered_suffix(url):
            return True
        # Destination-suffixed slugs like "...-zion-national-park" are often
        # non-canonical variants of the same trail page and can 401/403.
        return cls._alltrails_slug_extra_term_count(url, item_name) >= 1

    @staticmethod
    def _alltrails_slug_has_numbered_suffix(url: str) -> bool:
        slug = unquote(urlparse(url).path.rsplit("/", 1)[-1]).lower()
        return bool(re.search(r"--\d+$", slug))

    @staticmethod
    def _alltrails_slug_extra_term_count(url: str, item_name: str) -> int:
        slug = unquote(urlparse(url).path.rsplit("/", 1)[-1]).replace("-", " ")
        slug_tokens = URLDiscoverer._significant_tokens(slug)
        item_tokens = URLDiscoverer._significant_tokens(item_name)
        if not slug_tokens or not item_tokens:
            return 0
        item_set = set(item_tokens)
        return sum(1 for token in slug_tokens if token not in item_set)

    @staticmethod
    def _extract_trail_miles(text: str) -> float | None:
        if not text:
            return None
        match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:mile|miles|mi)\b", text.lower())
        if not match:
            return None
        try:
            return float(match.group(1))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _candidate_text_blob(candidate: dict[str, Any] | None) -> str:
        if not candidate:
            return ""
        return " ".join(
            [
                str(candidate.get("name", "") or ""),
                str(candidate.get("title", "") or ""),
                str(candidate.get("snippet", "") or ""),
                str(candidate.get("description", "") or ""),
            ]
        ).lower()

    @staticmethod
    def _is_obviously_generic_url(lower_url: str) -> bool:
        return any(marker in lower_url for marker in GENERIC_BAD_URL_MARKERS)

    @staticmethod
    def _is_trail_like_attraction(name: str, attr_type: str, description: str = "") -> bool:
        type_norm = (attr_type or "").strip().lower()
        name_l = (name or "").lower()
        haystack = f"{name} {description}".lower()
        normalized = re.sub(r"[^a-z0-9\s]", "", haystack)

        # Place-level attractions (parks/monuments/districts) often mention trails in
        # their description, but should not be forced into AllTrails-first routing.
        place_level_name = any(
            cue in name_l
            for cue in (
                "state park",
                "national park",
                " park",
                "desert reserve",
                "national monument",
                "historic district",
                "downtown",
                "visitor center",
                "petroglyph",
                "overlook",
                "viewpoint",
                "homestead",
            )
        )
        name_has_trail_cue = bool(re.search(r"\b(trail|hike|hiking|loop|walk|trek|path|summit)\b", name_l))
        if place_level_name and not name_has_trail_cue:
            return False

        trail_types = {
            "hike",
            "hiking",
            "trail",
            "trek",
            "walk",
        }
        if type_norm in trail_types:
            return True

        trail_substrings = (
            "this trail",
            "hiking trail",
            "trailhead",
            "switchback",
            "backcountry",
            "slot canyon",
            "angels landing",
            "emerald pool",
            "observation point",
            "riverside walk",
            " narrows",
        )
        if any(marker in normalized for marker in trail_substrings):
            return True

        # Catch common trail phrasing even when type is labeled as generic attraction.
        return bool(re.search(r"\b(trail|hike|hiking|loop|walk|trek|path|summit)\b", normalized))

    @staticmethod
    def _attraction_trail_context(attr: dict[str, Any]) -> str:
        return " ".join(
            [
                str(attr.get("description", "") or ""),
                str(attr.get("practical_note", "") or ""),
                str(attr.get("difficulty", "") or ""),
                str(attr.get("duration", "") or ""),
            ]
        )

    @staticmethod
    def _canonical_token(token: str) -> str:
        t = (token or "").lower()
        if t.endswith("ies") and len(t) > 4:
            return t[:-3] + "y"
        if t.endswith("s") and len(t) > 4 and not t.endswith("ss"):
            return t[:-1]
        return t

    @staticmethod
    def _significant_tokens(text: str) -> list[str]:
        tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
        stop = {
            "the", "and", "for", "with", "from", "near", "park", "national", "state", "trail",
            "road", "drive", "point", "restaurant", "cafe", "grill", "utah", "colorado", "new", "mexico",
        }
        out: list[str] = []
        seen: set[str] = set()
        for t in tokens:
            if len(t) < 4 or t in stop:
                continue
            canonical = URLDiscoverer._canonical_token(t)
            if len(canonical) < 4 or canonical in stop or canonical in seen:
                continue
            seen.add(canonical)
            out.append(canonical)
        return out

    @staticmethod
    def _destination_country_tlds(dest_name: str) -> set[str]:
        lowered = (dest_name or "").lower()
        out: set[str] = set()
        for key, tlds in COUNTRY_TLD_HINTS.items():
            if key in lowered:
                out.update(tlds)
        return out

    @classmethod
    def _looks_location_qualified(cls, text: str) -> bool:
        lowered = (text or "").lower().strip()
        if not lowered:
            return False
        if "," in lowered:
            return True
        if any(term in lowered for term in LOCATION_CUE_TERMS):
            return True
        if re.search(r"\bst\.?\s+[a-z]", lowered):
            return True
        return False

    @classmethod
    def _name_mentions_destination(cls, name: str, dest_name: str) -> bool:
        name_tokens = set(cls._significant_tokens(name))
        dest_tokens = set(cls._significant_tokens(dest_name))
        if not name_tokens or not dest_tokens:
            return False
        return bool(name_tokens & dest_tokens)

    @classmethod
    def _maps_fallback_query_text(cls, item_name: str, dest_name: str) -> str:
        item = str(item_name or "").strip()
        dest = str(dest_name or "").strip()
        if not item:
            return dest
        if cls._name_mentions_destination(item, dest):
            return item
        if cls._looks_location_qualified(item):
            return item
        return f"{item} {dest}".strip()
