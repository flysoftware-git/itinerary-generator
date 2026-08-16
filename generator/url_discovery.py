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
  v1.5: xAI Grok semantic search via chat-completions + live_search
        (superseded 2026-08-14: live_search tool deprecated, returns 410)
  v1.6: xAI Grok via /v1/responses + web_search tool, SSE streaming
        (current default). The search provider is pluggable -- see
        generator/search_provider.py for Claude/OpenAI alternatives.
"""
from __future__ import annotations
import html as html_lib
import json
import logging
import os
import time
from html.parser import HTMLParser
from pathlib import Path
import re
from math import asin, ceil, cos, radians, sqrt
from urllib.parse import parse_qs, quote, unquote, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Any
from generator.llm_client import MultiLLMClient
from generator.search_provider import build_search_client
from generator.url_validator import URLValidator

logger = logging.getLogger(__name__)
MAX_FALLBACK_ATTEMPTS = 4
ALLTRAILS_404_MARKERS = (
    "we've reached the end of the trail",
    "the page you're looking for either doesn't exist",
)
ALLTRAILS_CLOSURE_MARKERS = (
    "this trail is closed",
    "trail is closed",
    "temporarily closed",
    "closed due to",
    "trail closure",
)
ATTRACTION_CLOSURE_MARKERS = (
    "currently closed",
    "closed for safety reasons",
    "closed for safety",
    "temporarily closed",
    "closed due to",
    "permanently closed",
    "this location is closed",
    "this place is closed",
    "this business is closed",
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
# Titles/names that identify a search-result or listing page rather than a
# specific named place -- e.g. "THE 10 BEST Restaurants in St. George -
# Tripadvisor" or "Things to Do in Moab 2024". Harvested rows carrying one of
# these as their only "name" must never be used to synthesize a new
# attraction/restaurant item: both the listing title and its accompanying
# listing-page URL get attached together, producing a card whose displayed
# name is a listicle headline instead of an actual place.
GENERIC_LISTING_TITLE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^\s*(the\s+)?\d+\s+best\b",
        r"\bbest\s+restaurants?\s+(in|near)\b",
        r"\bthings\s+to\s+do\s+in\b",
        r"\btop\s+\d+\s+(restaurants|things\s+to\s+do|attractions|places)\b",
        r"[-|]\s*(tripadvisor|trip\s*advisor|yelp)\s*$",
    )
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
    "junction",
    "utah",
    "colorado",
    "arizona",
    "new mexico",
    "nevada",
    "california",
)
TEXT_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


class _DirectBatchHTMLListParser(HTMLParser):
    """Parse <li> blocks from HTML payloads and extract item names plus href links."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []
        self._in_li = False
        self._li_text_parts: list[str] = []
        self._li_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = str(tag or "").lower()
        if t == "li":
            self._in_li = True
            self._li_text_parts = []
            self._li_urls = []
            return
        if t == "a" and self._in_li:
            for key, value in attrs:
                if str(key or "").lower() != "href":
                    continue
                href = str(value or "").strip()
                if href:
                    self._li_urls.append(href)

    def handle_data(self, data: str) -> None:
        if not self._in_li:
            return
        text = str(data or "").strip()
        if text:
            self._li_text_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if str(tag or "").lower() != "li" or not self._in_li:
            return
        raw_text = " ".join(self._li_text_parts).strip()
        name = re.sub(r"\s+", " ", raw_text)
        name = re.sub(r"\bsource\b.*$", "", name, flags=re.IGNORECASE).strip()
        name = re.sub(r"\balltrails\b.*$", "", name, flags=re.IGNORECASE).strip()
        name = re.sub(r"^(?:google\s+maps|maps?|map)\s*:\s*", "", name, flags=re.IGNORECASE).strip()
        name = re.sub(r"^(?:source|official\s+site)\s*:\s*", "", name, flags=re.IGNORECASE).strip()
        name = re.sub(r"(?:\s+(?:source|maps?|alltrails))+\s*$", "", name, flags=re.IGNORECASE).strip()
        if not name and raw_text:
            name = raw_text
        self.records.append({"name": name, "urls": list(self._li_urls), "raw_text": raw_text})
        self._in_li = False
        self._li_text_parts = []
        self._li_urls = []

# ── URL Search Cache ────────────────────────────────────────────────────
_url_cache: dict[tuple[str, str, str], str | None] = {}

DEFAULT_UNINTERESTED_ATTRACTION_KEYWORDS = (
    "golf course",
    "country club",
    "golf club",
    " golf ",
    "bike trail",
    "bicycle trail",
    "cycling trail",
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
# Generic per-domain equivalent of the AllTrails-specific cooldown above,
# applied to any domain (TripAdvisor and beyond) that returns a 401/403 to a
# generic page-text fetch -- avoids re-probing a domain that just blocked us
# for every subsequent distinct URL on that same domain.
DEFAULT_DOMAIN_BLOCK_COOLDOWN_SECONDS = 8.0
# Route distance/time already has a solid Haversine-estimate fallback that
# costs zero network calls -- the live Google Maps directions HTML scrape is
# a pure accuracy enhancement on top of it, not a correctness gate, and (like
# any scrape) is a real future bot-block risk. Default keeps current
# fetch-then-fallback behavior; set False to skip the live fetch entirely.
DEFAULT_ROUTE_DISTANCE_LIVE_FETCH_ENABLED = True
# _search_cached previously cached an empty search result permanently for the
# run, with no distinction between "genuinely no results" and "the request
# failed" (GrokSearch.search() swallows exceptions and returns [] either
# way). A single transient failure would poison that query for the rest of
# the run with zero chance of recovery. This cooldown makes an empty result
# "sticky" for a bounded window (so a real outage doesn't cause repeated
# re-probing) but still allows a fresh attempt once it expires.
DEFAULT_SEARCH_FAILURE_COOLDOWN_SECONDS = 180.0
DEFAULT_ENABLE_FILTERED_ALLTRAILS_SELECTION = False
DEFAULT_ALLTRAILS_FILTER_MAX_MILES = 3.0
DEFAULT_ALLTRAILS_FILTER_MAX_GAIN_FEET = 300
DEFAULT_ALLTRAILS_FILTER_MIN_REVIEWS = 5
DEFAULT_ALLTRAILS_FILTER_ALLOWED_DIFFICULTIES = ("easy", "moderate", "moderately challenging")
DEFAULT_ALLTRAILS_RATING_MIN = 4.5
DEFAULT_ALLTRAILS_RATING_MIN_VOTES = 200
DEFAULT_ALLTRAILS_RATING_BOOST = 8
DEFAULT_RESTAURANT_RATING_MIN = 4.4
DEFAULT_RESTAURANT_RATING_MIN_VOTES = 100
DEFAULT_RESTAURANT_PREFER_OFFICIAL_SITE_OVER_TRIPADVISOR = True
DEFAULT_RESTAURANT_RATING_BOOST = 6
DEFAULT_PLACE_INTEREST_MIN_RATING = 4.0
DEFAULT_PLACE_INTEREST_MIN_VOTES = 10
DEFAULT_PLACE_INTEREST_REQUIRE_METADATA = False
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
DEFAULT_ALLTRAILS_SLUG_DENYLIST: tuple[str, ...] = (
    "dixie-sugarloaf-trail",
)
DEFAULT_ALLTRAILS_SOURCE = "direct_link_batch"
DEFAULT_ATTRACTION_SOURCE = "direct_link_batch"
DEFAULT_RESTAURANT_SOURCE = "direct_link_batch"
DEFAULT_EN_ROUTE_SOURCE = "search"
DEFAULT_DIRECT_LINK_BATCH_COUNT = 20
DEFAULT_DIRECT_BATCH_AUTHORITATIVE = True
DEFAULT_RESTAURANT_DIRECT_BATCH_ITEM_COUNT = 4
DEFAULT_EN_ROUTE_DIRECT_BATCH_ITEM_COUNT = 4
DEFAULT_ATTRACTION_DIRECT_BATCH_ITEMS_PER_DAY = 3
DEFAULT_TRAIL_DIRECT_BATCH_ITEMS_PER_DAY = 2
# Direct-batch destination grouping (2026-08-15): how many destinations'
# worth of a given kind (attraction/restaurant/trail) get asked for in a
# single direct-batch harvest call, instead of one call per destination.
# Real-call evidence: 2 destinations in one call completed in ~90s (in line
# with a single destination's own 68-150s range) with correct, cleanly
# separated results; 3 destinations never converged across 2 full retry
# attempts (300s+, zero output). 1 disables grouping entirely (the original
# one-call-per-destination behavior) -- the safe rollback value. Deliberately
# NOT raised above 2 by default until more evidence justifies it; the knob
# exists so that can happen later without further code changes.
DEFAULT_DIRECT_BATCH_GROUP_SIZE = 2
# Generic descriptive suffixes AI-generated attraction names often append to a
# place name (e.g. harvest row "Bryce Point" vs AI-generated item name "Bryce
# Point Overlook") that carry no matching-relevant meaning of their own.
GENERIC_VIEWPOINT_SUFFIX_TOKENS = frozenset({"overlook", "view", "viewpoint", "vista"})
_MATCH_STOPWORDS = frozenset({"the", "a", "an", "of", "and", "st"})
DEFAULT_RESTAURANT_DIRECT_BATCH_MIN_RESULTS = 3
DEFAULT_EN_ROUTE_DIRECT_BATCH_MIN_RESULTS = 2
DEFAULT_EN_ROUTE_DETOUR_MAX_MINUTES = 20
DEFAULT_EN_ROUTE_DETOUR_MAX_MILES = 0.0
DEFAULT_EN_ROUTE_REQUIRE_DETOUR_METADATA = True
DEFAULT_DIRECT_BATCH_HTML_CAPTURE_ENABLED = True
DEFAULT_DIRECT_BATCH_HTML_CAPTURE_SUBDIR = "dev/url_discovery_direct_batch_html"
DEFAULT_ALLTRAILS_APIFY_ACTOR_ID = "MMQdritoUWpzUVbah"
DEFAULT_ALLTRAILS_APIFY_TOKEN_ENV = "APIFY_API_TOKEN"
DEFAULT_ALLTRAILS_APIFY_MAX_ITEMS = 80
DEFAULT_ALLTRAILS_APIFY_WITHIN_MILES = 30.0
DEFAULT_ALLTRAILS_APIFY_DESTINATION_TOKEN_OVERLAP_MIN = 1
DEFAULT_URL_POLICY_BLOCKED_CLASSES = (
    "google_search",
    "google_maps_search",
    "google_maps_dir",
)
DEFAULT_URL_DOMAIN_DENYLIST: tuple[str, ...] = ()
DEFAULT_URL_POLICY_ALLOWLIST_PATH = "docs/policy/url_policy_allowlist.txt"
DEFAULT_URL_POLICY_AUTO_ALLOW_FROM_OUTPUT = True
DEFAULT_URL_POLICY_OUTPUT_PATH = "output/index.html"
DEFAULT_PERSISTENT_CACHE_ENABLED = True
DEFAULT_PERSISTENT_CACHE_PATH = ".cache/url_discovery/persistent_cache.json"
DEFAULT_PERSISTENT_SEARCH_CACHE_TTL_HOURS = 72
DEFAULT_PERSISTENT_PAGE_TEXT_CACHE_TTL_HOURS = 24
DEFAULT_PERSISTENT_VERIFY_CACHE_TTL_HOURS = 12
DEFAULT_PERSISTENT_PAGE_TEXT_MAX_CHARS = 120000
# A named place's coordinates don't change; a long TTL is safe and avoids
# re-geocoding (and re-throttling against) Nominatim across runs.
DEFAULT_PERSISTENT_GEOCODE_CACHE_TTL_HOURS = 720
# AllTrails page content/block-state is more volatile than coordinates -- keep
# this short so a transient DataDome block or stale trail status doesn't get
# frozen in across runs.
DEFAULT_PERSISTENT_ALLTRAILS_CACHE_TTL_HOURS = 12
# Direct-batch harvest rows (the per-destination-per-kind Grok HTML-list
# responses for attractions/restaurants/trails/en-route stops) are the most
# expensive part of URL discovery and the most repeated across same-day
# iterative validation runs of an unchanged manifest -- a full day's TTL lets
# repeat runs skip re-harvesting entirely while still refreshing ratings/links
# often enough to catch closures.
DEFAULT_PERSISTENT_HARVEST_CACHE_TTL_HOURS = 24
# A failed/empty direct-batch HTML harvest is deliberately never cached (an
# empty result isn't authoritative), but multiple items at the same
# destination each independently call the same per-destination-per-kind
# harvest -- under a sustained provider-side outage that means every one of
# them re-pays a full multi-attempt timeout cycle for a call that just failed
# seconds ago. This cooldown makes that failure "sticky" in memory for a
# short window so concurrent/sequential callers share one failure instead of
# each re-triggering the network call from scratch.
DEFAULT_DIRECT_BATCH_HTML_FAILURE_COOLDOWN_SECONDS = 180.0

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


def _build_nps_activity_query_variants(name: str, destination: str) -> list[str]:
    """Build NPS-focused activity variants for category-style attractions."""
    lowered = f"{name} {destination}".lower()
    theme = "activity program"
    if "stargazing" in lowered or "dark sky" in lowered or "night sky" in lowered:
        theme = "night sky astronomy"
    elif "bird" in lowered:
        theme = "wildlife birding"
    elif "fishing" in lowered:
        theme = "fishing"

    quoted_name = f'"{name}"'
    quoted_dest = f'"{destination}"'
    return [
        f"{quoted_dest} {theme}",
        f"{destination} {theme} nps",
        f"{quoted_name} {quoted_dest} nps",
        f"{name} {destination} nps",
    ]


def _build_attraction_maps_query_variants(name: str, destination: str) -> list[str]:
    """Build attraction queries tuned for Google Maps Things to do results."""
    quoted_name = f'"{name}"'
    quoted_dest = f'"{destination}"'
    return [
        f"{quoted_name} {quoted_dest} things to do",
        f"{name} {destination} things to do",
        f"{quoted_name} {destination} attraction",
    ]


class URLDiscoverer:
    def __init__(
        self,
        config_path: str | Any = "config.yaml",
        llm_client: MultiLLMClient | None = None,
        *,
        disable_trails: bool = False,
        alltrails_source: str | None = None,
        alltrails_apify_actor_id: str | None = None,
        attraction_source: str | None = None,
        restaurant_source: str | None = None,
        en_route_source: str | None = None,
        output_dir: str | Path | None = None,
        search_provider_override: str | None = None,
    ) -> None:
        self._llm = llm_client or MultiLLMClient(config_path)
        # When the canonical content-generation provider matches the search
        # provider, search shares its exact model instead of independently
        # falling back to its own env var default -- otherwise the two could
        # silently diverge (config.yaml says one model, search quietly uses
        # another). This only applies when they match; url_discovery.
        # search_provider (config.yaml) selects grok or claude independently
        # of ai.provider, defaulting to grok -- unchanged behavior from
        # before this selection existed. See search_provider.py and
        # claude_search.py (docs/design/search-provider-capability-probe.md).
        grok_model = self._llm.model if self._llm.provider == "grok" else None
        claude_model = self._llm.model if self._llm.provider == "anthropic" else None
        self._search = build_search_client(
            config_path,
            config_section="url_discovery",
            provider_key="search_provider",
            provider_override=search_provider_override,
            grok_model=grok_model,
            claude_model=claude_model,
            usage_tracker=self._llm.usage_tracker,
            usage_operation_prefix="url_discovery",
        )
        # search_provider_override (2026-08-15, --search-provider CLI flag)
        # forces a single provider with no fallback at all -- for a clean
        # per-provider cost/behavior comparison, uncontaminated by the
        # cross-provider batch retry or the non-batch fallback both
        # independently attempting a second provider. Every call site that
        # reads self._search_fallback already treats None as "no fallback
        # available" (see _fetch_direct_batch_html_rows, _search_cached),
        # so this doesn't need special-casing beyond just not building one.
        if search_provider_override:
            self._search_fallback = None
        else:
            # Separate client for the per-item search fallback (_search_cached,
            # used by _search_first/_search_first_strict) -- a lower-volume,
            # different-shaped call than the direct-batch HTML harvest above, and
            # independently pinned via nonbatch_search_provider (config.yaml).
            # See that key's comment for the rationale.
            self._search_fallback = build_search_client(
                config_path,
                config_section="url_discovery",
                provider_key="nonbatch_search_provider",
                grok_model=grok_model,
                claude_model=claude_model,
                usage_tracker=self._llm.usage_tracker,
                usage_operation_prefix="url_discovery_fallback",
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
        self._alltrails_filter_max_miles: float = DEFAULT_ALLTRAILS_FILTER_MAX_MILES
        self._alltrails_filter_max_gain_feet: int = DEFAULT_ALLTRAILS_FILTER_MAX_GAIN_FEET
        self._alltrails_filter_min_reviews: int = DEFAULT_ALLTRAILS_FILTER_MIN_REVIEWS
        self._alltrails_filter_allowed_difficulties: tuple[str, ...] = DEFAULT_ALLTRAILS_FILTER_ALLOWED_DIFFICULTIES
        self._alltrails_filtered_selection_cache: dict[tuple[str, str], str | None] = {}
        self._alltrails_fetch_cache: dict[str, tuple[bool, int | str, str]] = {}
        self._alltrails_last_request_ts: float = 0.0
        self._alltrails_blocked_until_ts: float = 0.0
        self._alltrails_fetch_lock: Lock = Lock()
        self._max_alltrails_query_attempts: int = 2
        self._max_restaurant_query_attempts: int = 1
        self._alltrails_rating_min: float = DEFAULT_ALLTRAILS_RATING_MIN
        self._alltrails_rating_min_votes: int = DEFAULT_ALLTRAILS_RATING_MIN_VOTES
        self._alltrails_rating_boost: int = DEFAULT_ALLTRAILS_RATING_BOOST
        self._restaurant_rating_min: float = DEFAULT_RESTAURANT_RATING_MIN
        self._restaurant_rating_min_votes: int = DEFAULT_RESTAURANT_RATING_MIN_VOTES
        self._restaurant_rating_boost: int = DEFAULT_RESTAURANT_RATING_BOOST
        self._restaurant_prefer_official_site_over_tripadvisor: bool = DEFAULT_RESTAURANT_PREFER_OFFICIAL_SITE_OVER_TRIPADVISOR
        self._place_interest_min_rating: float = DEFAULT_PLACE_INTEREST_MIN_RATING
        self._place_interest_min_votes: int = DEFAULT_PLACE_INTEREST_MIN_VOTES
        self._place_interest_require_metadata: bool = DEFAULT_PLACE_INTEREST_REQUIRE_METADATA
        self._restaurant_name_denylist: frozenset[str] = frozenset(DEFAULT_RESTAURANT_NAME_DENYLIST)
        self._url_policy_mode: str = DEFAULT_URL_POLICY_MODE
        self._url_policy_blocked_classes: set[str] = set(DEFAULT_URL_POLICY_BLOCKED_CLASSES)
        self._url_domain_denylist: frozenset[str] = frozenset(DEFAULT_URL_DOMAIN_DENYLIST)
        self._url_policy_allowlist_path: str = DEFAULT_URL_POLICY_ALLOWLIST_PATH
        self._url_policy_auto_allow_from_output: bool = DEFAULT_URL_POLICY_AUTO_ALLOW_FROM_OUTPUT
        self._url_policy_output_path: str = DEFAULT_URL_POLICY_OUTPUT_PATH
        self._url_policy_allowlisted_urls: set[str] = set()
        self._alltrails_slug_denylist: frozenset[str] = frozenset(DEFAULT_ALLTRAILS_SLUG_DENYLIST)
        self._disable_trails: bool = bool(disable_trails)
        self._alltrails_source: str = DEFAULT_ALLTRAILS_SOURCE
        self._attraction_source: str = DEFAULT_ATTRACTION_SOURCE
        self._restaurant_source: str = DEFAULT_RESTAURANT_SOURCE
        self._en_route_source: str = DEFAULT_EN_ROUTE_SOURCE
        self._direct_link_batch_count: int = DEFAULT_DIRECT_LINK_BATCH_COUNT
        self._direct_batch_authoritative: bool = DEFAULT_DIRECT_BATCH_AUTHORITATIVE
        self._restaurant_direct_batch_item_count: int = DEFAULT_RESTAURANT_DIRECT_BATCH_ITEM_COUNT
        self._en_route_direct_batch_item_count: int = DEFAULT_EN_ROUTE_DIRECT_BATCH_ITEM_COUNT
        self._attraction_direct_batch_items_per_day: int = DEFAULT_ATTRACTION_DIRECT_BATCH_ITEMS_PER_DAY
        self._trail_direct_batch_items_per_day: int = DEFAULT_TRAIL_DIRECT_BATCH_ITEMS_PER_DAY
        self._direct_batch_group_size: int = DEFAULT_DIRECT_BATCH_GROUP_SIZE
        self._restaurant_direct_batch_min_results: int = DEFAULT_RESTAURANT_DIRECT_BATCH_MIN_RESULTS
        self._en_route_direct_batch_min_results: int = DEFAULT_EN_ROUTE_DIRECT_BATCH_MIN_RESULTS
        self._en_route_detour_max_minutes: int = DEFAULT_EN_ROUTE_DETOUR_MAX_MINUTES
        self._en_route_detour_max_miles: float = DEFAULT_EN_ROUTE_DETOUR_MAX_MILES
        self._en_route_require_detour_metadata: bool = DEFAULT_EN_ROUTE_REQUIRE_DETOUR_METADATA
        # Maps a normalized "known-good" URL to the set of item-name token-keys it
        # was actually validated against. Keyed by (url, item), NOT just url --
        # see _is_remembered_direct_batch_authoritative_url for why a flat url-only
        # cache is unsafe (it let a URL validated for one item silently vouch for
        # an unrelated item that happened to reuse the same URL string).
        self._direct_batch_authoritative_urls: dict[str, set[frozenset[str]]] = {}
        self._alltrails_apify_actor_id: str = DEFAULT_ALLTRAILS_APIFY_ACTOR_ID
        self._alltrails_apify_token_env: str = DEFAULT_ALLTRAILS_APIFY_TOKEN_ENV
        self._alltrails_apify_max_items: int = DEFAULT_ALLTRAILS_APIFY_MAX_ITEMS
        self._alltrails_apify_within_miles: float = DEFAULT_ALLTRAILS_APIFY_WITHIN_MILES
        self._alltrails_apify_within_miles_by_destination: dict[str, float] = {}
        self._alltrails_apify_destination_token_overlap_min: int = (
            DEFAULT_ALLTRAILS_APIFY_DESTINATION_TOKEN_OVERLAP_MIN
        )
        self._alltrails_apify_destination_cache: dict[str, list[dict[str, Any]]] = {}
        self._alltrails_apify_warned_missing_token: bool = False
        self._alltrails_direct_batch_cache: dict[str, list[dict[str, Any]]] = {}
        self._attraction_direct_batch_cache: dict[str, list[dict[str, Any]]] = {}
        self._attraction_maps_area_cache: dict[str, list[dict[str, Any]]] = {}
        self._restaurant_direct_batch_cache: dict[str, list[dict[str, Any]]] = {}
        self._en_route_direct_batch_cache: dict[str, list[dict[str, Any]]] = {}
        self._direct_batch_html_key_locks: dict[str, Lock] = {}
        self._direct_batch_html_failure_ts: dict[str, float] = {}
        self._direct_batch_html_failure_cooldown_seconds: float = float(
            DEFAULT_DIRECT_BATCH_HTML_FAILURE_COOLDOWN_SECONDS
        )
        self._maps_url_resolution_cache: dict[str, str] = {}
        self._fetch_final_url_cache: dict[str, str] = {}
        self._verify_url_cache: dict[str, tuple[bool, int | str]] = {}
        self._page_text_cache: dict[str, tuple[bool, int | str, str]] = {}
        self._domain_blocked_until_ts: dict[str, float] = {}
        self._domain_block_cooldown_seconds: float = float(DEFAULT_DOMAIN_BLOCK_COOLDOWN_SECONDS)
        self._route_distance_live_fetch_enabled: bool = DEFAULT_ROUTE_DISTANCE_LIVE_FETCH_ENABLED
        self._search_failure_ts: dict[str, float] = {}
        self._search_failure_cooldown_seconds: float = float(DEFAULT_SEARCH_FAILURE_COOLDOWN_SECONDS)
        self._search_results_cache: dict[str, list[dict[str, Any]]] = {}
        # Maps discovered URL → the search candidate dict that produced it (name/snippet)
        self._search_winner_snippets: dict[str, dict[str, Any]] = {}
        self._request_cache_lock: Lock = Lock()
        self._nominatim_rate_limit_lock: Lock = Lock()
        self._persistent_cache_enabled: bool = DEFAULT_PERSISTENT_CACHE_ENABLED
        self._persistent_cache_path: str = DEFAULT_PERSISTENT_CACHE_PATH
        self._persistent_search_cache_ttl_hours: float = float(DEFAULT_PERSISTENT_SEARCH_CACHE_TTL_HOURS)
        self._persistent_page_text_cache_ttl_hours: float = float(DEFAULT_PERSISTENT_PAGE_TEXT_CACHE_TTL_HOURS)
        self._persistent_verify_cache_ttl_hours: float = float(DEFAULT_PERSISTENT_VERIFY_CACHE_TTL_HOURS)
        self._persistent_geocode_cache_ttl_hours: float = float(DEFAULT_PERSISTENT_GEOCODE_CACHE_TTL_HOURS)
        self._persistent_alltrails_cache_ttl_hours: float = float(DEFAULT_PERSISTENT_ALLTRAILS_CACHE_TTL_HOURS)
        self._persistent_harvest_cache_ttl_hours: float = float(DEFAULT_PERSISTENT_HARVEST_CACHE_TTL_HOURS)
        self._persistent_cache_dirty: bool = False
        self._persistent_cache_write_every: int = 25
        self._persistent_cache_pending_writes: int = 0
        self._decision_stats_by_destination: dict[str, dict[str, int]] = {}
        self._decision_source_stats_by_destination: dict[str, dict[str, int]] = {}
        self._decision_threads_by_destination: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._decision_event_sequence: int = 0
        self._run_output_dir: Path | None = Path(output_dir) if output_dir else None
        self._direct_batch_html_capture_enabled: bool = DEFAULT_DIRECT_BATCH_HTML_CAPTURE_ENABLED
        self._direct_batch_html_capture_subdir: str = DEFAULT_DIRECT_BATCH_HTML_CAPTURE_SUBDIR
        self._load_interest_filters(config_path)
        source_override = str(alltrails_source or "").strip().lower().replace("-", "_")
        if source_override in {"search", "apify_single_call", "direct_link_batch"}:
            self._alltrails_source = source_override
        actor_override = str(alltrails_apify_actor_id or "").strip()
        if actor_override:
            self._alltrails_apify_actor_id = actor_override
        attraction_override = str(attraction_source or "").strip().lower().replace("-", "_")
        if attraction_override in {"search", "direct_link_batch"}:
            self._attraction_source = attraction_override
        restaurant_override = str(restaurant_source or "").strip().lower().replace("-", "_")
        if restaurant_override in {"search", "direct_link_batch"}:
            self._restaurant_source = restaurant_override
        en_route_override = str(en_route_source or "").strip().lower().replace("-", "_")
        if en_route_override in {"search", "direct_link_batch"}:
            self._en_route_source = en_route_override
        self._load_url_policy_allowlist()
        self._load_persistent_caches()

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

            max_alltrails_attempts = url_cfg.get("max_alltrails_query_attempts", 2)
            try:
                parsed_alltrails_attempts = int(max_alltrails_attempts)
                if parsed_alltrails_attempts > 0:
                    self._max_alltrails_query_attempts = parsed_alltrails_attempts
            except (TypeError, ValueError):
                self._max_alltrails_query_attempts = 2

            max_restaurant_attempts = url_cfg.get("max_restaurant_query_attempts", 1)
            try:
                parsed_restaurant_attempts = int(max_restaurant_attempts)
                if parsed_restaurant_attempts > 0:
                    self._max_restaurant_query_attempts = parsed_restaurant_attempts
            except (TypeError, ValueError):
                self._max_restaurant_query_attempts = 1

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

            prefer_official_over_tripadvisor = url_cfg.get(
                "restaurant_prefer_official_site_over_tripadvisor",
                DEFAULT_RESTAURANT_PREFER_OFFICIAL_SITE_OVER_TRIPADVISOR,
            )
            self._restaurant_prefer_official_site_over_tripadvisor = bool(prefer_official_over_tripadvisor)

            place_interest_min_rating = url_cfg.get("place_interest_min_rating", DEFAULT_PLACE_INTEREST_MIN_RATING)
            try:
                parsed_place_interest_rating = float(place_interest_min_rating)
                if 0.0 <= parsed_place_interest_rating <= 5.0:
                    self._place_interest_min_rating = parsed_place_interest_rating
            except (TypeError, ValueError):
                self._place_interest_min_rating = DEFAULT_PLACE_INTEREST_MIN_RATING

            place_interest_min_votes = url_cfg.get("place_interest_min_votes", DEFAULT_PLACE_INTEREST_MIN_VOTES)
            try:
                parsed_place_interest_votes = int(place_interest_min_votes)
                if parsed_place_interest_votes >= 0:
                    self._place_interest_min_votes = parsed_place_interest_votes
            except (TypeError, ValueError):
                self._place_interest_min_votes = DEFAULT_PLACE_INTEREST_MIN_VOTES

            self._place_interest_require_metadata = bool(
                url_cfg.get("place_interest_require_metadata", DEFAULT_PLACE_INTEREST_REQUIRE_METADATA)
            )

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

            persistent_cache_enabled = url_cfg.get(
                "persistent_cache_enabled",
                DEFAULT_PERSISTENT_CACHE_ENABLED,
            )
            self._persistent_cache_enabled = bool(persistent_cache_enabled)

            persistent_cache_path = str(
                url_cfg.get("persistent_cache_path", DEFAULT_PERSISTENT_CACHE_PATH)
                or DEFAULT_PERSISTENT_CACHE_PATH
            ).strip()
            if persistent_cache_path:
                self._persistent_cache_path = persistent_cache_path

            persistent_search_ttl = url_cfg.get(
                "persistent_search_cache_ttl_hours",
                DEFAULT_PERSISTENT_SEARCH_CACHE_TTL_HOURS,
            )
            try:
                parsed_search_ttl = float(persistent_search_ttl)
                if parsed_search_ttl > 0:
                    self._persistent_search_cache_ttl_hours = parsed_search_ttl
            except (TypeError, ValueError):
                self._persistent_search_cache_ttl_hours = float(DEFAULT_PERSISTENT_SEARCH_CACHE_TTL_HOURS)

            persistent_page_ttl = url_cfg.get(
                "persistent_page_text_cache_ttl_hours",
                DEFAULT_PERSISTENT_PAGE_TEXT_CACHE_TTL_HOURS,
            )
            try:
                parsed_page_ttl = float(persistent_page_ttl)
                if parsed_page_ttl > 0:
                    self._persistent_page_text_cache_ttl_hours = parsed_page_ttl
            except (TypeError, ValueError):
                self._persistent_page_text_cache_ttl_hours = float(DEFAULT_PERSISTENT_PAGE_TEXT_CACHE_TTL_HOURS)

            persistent_verify_ttl = url_cfg.get(
                "persistent_verify_cache_ttl_hours",
                DEFAULT_PERSISTENT_VERIFY_CACHE_TTL_HOURS,
            )
            try:
                parsed_verify_ttl = float(persistent_verify_ttl)
                if parsed_verify_ttl > 0:
                    self._persistent_verify_cache_ttl_hours = parsed_verify_ttl
            except (TypeError, ValueError):
                self._persistent_verify_cache_ttl_hours = float(DEFAULT_PERSISTENT_VERIFY_CACHE_TTL_HOURS)

            persistent_geocode_ttl = url_cfg.get(
                "persistent_geocode_cache_ttl_hours",
                DEFAULT_PERSISTENT_GEOCODE_CACHE_TTL_HOURS,
            )
            try:
                parsed_geocode_ttl = float(persistent_geocode_ttl)
                if parsed_geocode_ttl > 0:
                    self._persistent_geocode_cache_ttl_hours = parsed_geocode_ttl
            except (TypeError, ValueError):
                self._persistent_geocode_cache_ttl_hours = float(DEFAULT_PERSISTENT_GEOCODE_CACHE_TTL_HOURS)

            persistent_alltrails_ttl = url_cfg.get(
                "persistent_alltrails_cache_ttl_hours",
                DEFAULT_PERSISTENT_ALLTRAILS_CACHE_TTL_HOURS,
            )
            try:
                parsed_alltrails_ttl = float(persistent_alltrails_ttl)
                if parsed_alltrails_ttl > 0:
                    self._persistent_alltrails_cache_ttl_hours = parsed_alltrails_ttl
            except (TypeError, ValueError):
                self._persistent_alltrails_cache_ttl_hours = float(DEFAULT_PERSISTENT_ALLTRAILS_CACHE_TTL_HOURS)

            persistent_harvest_ttl = url_cfg.get(
                "persistent_harvest_cache_ttl_hours",
                DEFAULT_PERSISTENT_HARVEST_CACHE_TTL_HOURS,
            )
            try:
                parsed_harvest_ttl = float(persistent_harvest_ttl)
                if parsed_harvest_ttl > 0:
                    self._persistent_harvest_cache_ttl_hours = parsed_harvest_ttl
            except (TypeError, ValueError):
                self._persistent_harvest_cache_ttl_hours = float(DEFAULT_PERSISTENT_HARVEST_CACHE_TTL_HOURS)

            harvest_failure_cooldown = url_cfg.get(
                "direct_batch_html_failure_cooldown_seconds",
                DEFAULT_DIRECT_BATCH_HTML_FAILURE_COOLDOWN_SECONDS,
            )
            try:
                parsed_failure_cooldown = float(harvest_failure_cooldown)
                if parsed_failure_cooldown >= 0:
                    self._direct_batch_html_failure_cooldown_seconds = parsed_failure_cooldown
            except (TypeError, ValueError):
                self._direct_batch_html_failure_cooldown_seconds = float(
                    DEFAULT_DIRECT_BATCH_HTML_FAILURE_COOLDOWN_SECONDS
                )

            domain_block_cooldown = url_cfg.get(
                "domain_block_cooldown_seconds",
                DEFAULT_DOMAIN_BLOCK_COOLDOWN_SECONDS,
            )
            try:
                parsed_domain_block_cooldown = float(domain_block_cooldown)
                if parsed_domain_block_cooldown >= 0:
                    self._domain_block_cooldown_seconds = parsed_domain_block_cooldown
            except (TypeError, ValueError):
                self._domain_block_cooldown_seconds = float(DEFAULT_DOMAIN_BLOCK_COOLDOWN_SECONDS)

            self._route_distance_live_fetch_enabled = bool(
                url_cfg.get("route_distance_live_fetch_enabled", DEFAULT_ROUTE_DISTANCE_LIVE_FETCH_ENABLED)
            )

            search_failure_cooldown = url_cfg.get(
                "search_failure_cooldown_seconds",
                DEFAULT_SEARCH_FAILURE_COOLDOWN_SECONDS,
            )
            try:
                parsed_search_failure_cooldown = float(search_failure_cooldown)
                if parsed_search_failure_cooldown >= 0:
                    self._search_failure_cooldown_seconds = parsed_search_failure_cooldown
            except (TypeError, ValueError):
                self._search_failure_cooldown_seconds = float(DEFAULT_SEARCH_FAILURE_COOLDOWN_SECONDS)

            raw_slug_denylist = url_cfg.get("alltrails_slug_denylist", [])
            if isinstance(raw_slug_denylist, list):
                self._alltrails_slug_denylist = frozenset(
                    str(v or "").strip().lower()
                    for v in raw_slug_denylist
                    if str(v or "").strip()
                )

            alltrails_source = str(
                url_cfg.get("alltrails_source", DEFAULT_ALLTRAILS_SOURCE)
                or DEFAULT_ALLTRAILS_SOURCE
            ).strip().lower().replace("-", "_")
            if alltrails_source in {"search", "apify_single_call", "direct_link_batch"}:
                self._alltrails_source = alltrails_source

            attraction_source = str(
                url_cfg.get("attraction_source", DEFAULT_ATTRACTION_SOURCE)
                or DEFAULT_ATTRACTION_SOURCE
            ).strip().lower().replace("-", "_")
            if attraction_source in {"search", "direct_link_batch"}:
                self._attraction_source = attraction_source

            restaurant_source = str(
                url_cfg.get("restaurant_source", DEFAULT_RESTAURANT_SOURCE)
                or DEFAULT_RESTAURANT_SOURCE
            ).strip().lower().replace("-", "_")
            if restaurant_source in {"search", "direct_link_batch"}:
                self._restaurant_source = restaurant_source

            en_route_source = str(
                url_cfg.get("en_route_source", DEFAULT_EN_ROUTE_SOURCE)
                or DEFAULT_EN_ROUTE_SOURCE
            ).strip().lower().replace("-", "_")
            if en_route_source in {"search", "direct_link_batch"}:
                self._en_route_source = en_route_source

            direct_link_batch_count = url_cfg.get("direct_link_batch_count", DEFAULT_DIRECT_LINK_BATCH_COUNT)
            try:
                parsed_batch_count = int(direct_link_batch_count)
                if parsed_batch_count > 0:
                    self._direct_link_batch_count = parsed_batch_count
            except (TypeError, ValueError):
                self._direct_link_batch_count = DEFAULT_DIRECT_LINK_BATCH_COUNT

            attraction_direct_batch_items_per_day = url_cfg.get(
                "attraction_direct_batch_items_per_day",
                DEFAULT_ATTRACTION_DIRECT_BATCH_ITEMS_PER_DAY,
            )
            try:
                parsed_attraction_items_per_day = int(attraction_direct_batch_items_per_day)
                if parsed_attraction_items_per_day > 0:
                    self._attraction_direct_batch_items_per_day = parsed_attraction_items_per_day
            except (TypeError, ValueError):
                self._attraction_direct_batch_items_per_day = DEFAULT_ATTRACTION_DIRECT_BATCH_ITEMS_PER_DAY

            trail_direct_batch_items_per_day = url_cfg.get(
                "trail_direct_batch_items_per_day",
                DEFAULT_TRAIL_DIRECT_BATCH_ITEMS_PER_DAY,
            )
            try:
                parsed_trail_items_per_day = int(trail_direct_batch_items_per_day)
                if parsed_trail_items_per_day > 0:
                    self._trail_direct_batch_items_per_day = parsed_trail_items_per_day
            except (TypeError, ValueError):
                self._trail_direct_batch_items_per_day = DEFAULT_TRAIL_DIRECT_BATCH_ITEMS_PER_DAY

            # DIRECT_BATCH_GROUP_SIZE env var takes priority over config.yaml so
            # this can be ramped up/down for a single experimental run without
            # editing tracked config -- see DEFAULT_DIRECT_BATCH_GROUP_SIZE.
            direct_batch_group_size = os.environ.get(
                "DIRECT_BATCH_GROUP_SIZE",
                url_cfg.get("direct_batch_group_size", DEFAULT_DIRECT_BATCH_GROUP_SIZE),
            )
            try:
                parsed_group_size = int(direct_batch_group_size)
                if parsed_group_size > 0:
                    self._direct_batch_group_size = parsed_group_size
            except (TypeError, ValueError):
                self._direct_batch_group_size = DEFAULT_DIRECT_BATCH_GROUP_SIZE

            restaurant_direct_batch_item_count = url_cfg.get(
                "restaurant_direct_batch_item_count",
                DEFAULT_RESTAURANT_DIRECT_BATCH_ITEM_COUNT,
            )
            try:
                parsed_restaurant_item_count = int(restaurant_direct_batch_item_count)
                if parsed_restaurant_item_count > 0:
                    self._restaurant_direct_batch_item_count = parsed_restaurant_item_count
            except (TypeError, ValueError):
                self._restaurant_direct_batch_item_count = DEFAULT_RESTAURANT_DIRECT_BATCH_ITEM_COUNT

            en_route_direct_batch_item_count = url_cfg.get(
                "en_route_direct_batch_item_count",
                DEFAULT_EN_ROUTE_DIRECT_BATCH_ITEM_COUNT,
            )
            try:
                parsed_en_route_item_count = int(en_route_direct_batch_item_count)
                if parsed_en_route_item_count > 0:
                    self._en_route_direct_batch_item_count = parsed_en_route_item_count
            except (TypeError, ValueError):
                self._en_route_direct_batch_item_count = DEFAULT_EN_ROUTE_DIRECT_BATCH_ITEM_COUNT

            restaurant_direct_batch_min_results = url_cfg.get(
                "restaurant_direct_batch_min_results",
                DEFAULT_RESTAURANT_DIRECT_BATCH_MIN_RESULTS,
            )
            try:
                parsed_restaurant_min_results = int(restaurant_direct_batch_min_results)
                if parsed_restaurant_min_results > 0:
                    self._restaurant_direct_batch_min_results = parsed_restaurant_min_results
            except (TypeError, ValueError):
                self._restaurant_direct_batch_min_results = DEFAULT_RESTAURANT_DIRECT_BATCH_MIN_RESULTS

            en_route_direct_batch_min_results = url_cfg.get(
                "en_route_direct_batch_min_results",
                DEFAULT_EN_ROUTE_DIRECT_BATCH_MIN_RESULTS,
            )
            try:
                parsed_en_route_min_results = int(en_route_direct_batch_min_results)
                if parsed_en_route_min_results > 0:
                    self._en_route_direct_batch_min_results = parsed_en_route_min_results
            except (TypeError, ValueError):
                self._en_route_direct_batch_min_results = DEFAULT_EN_ROUTE_DIRECT_BATCH_MIN_RESULTS

            en_route_detour_max_minutes = url_cfg.get(
                "en_route_detour_max_minutes",
                DEFAULT_EN_ROUTE_DETOUR_MAX_MINUTES,
            )
            try:
                parsed_en_route_detour_max_minutes = int(en_route_detour_max_minutes)
                if parsed_en_route_detour_max_minutes >= 0:
                    self._en_route_detour_max_minutes = parsed_en_route_detour_max_minutes
            except (TypeError, ValueError):
                self._en_route_detour_max_minutes = DEFAULT_EN_ROUTE_DETOUR_MAX_MINUTES

            en_route_detour_max_miles = url_cfg.get(
                "en_route_detour_max_miles",
                DEFAULT_EN_ROUTE_DETOUR_MAX_MILES,
            )
            try:
                parsed_en_route_detour_max_miles = float(en_route_detour_max_miles)
                if parsed_en_route_detour_max_miles >= 0:
                    self._en_route_detour_max_miles = parsed_en_route_detour_max_miles
            except (TypeError, ValueError):
                self._en_route_detour_max_miles = DEFAULT_EN_ROUTE_DETOUR_MAX_MILES

            self._en_route_require_detour_metadata = bool(
                url_cfg.get(
                    "en_route_require_detour_metadata",
                    DEFAULT_EN_ROUTE_REQUIRE_DETOUR_METADATA,
                )
            )

            direct_batch_authoritative = url_cfg.get(
                "direct_batch_authoritative",
                DEFAULT_DIRECT_BATCH_AUTHORITATIVE,
            )
            self._direct_batch_authoritative = bool(direct_batch_authoritative)

            direct_batch_html_capture_enabled = url_cfg.get(
                "direct_batch_html_capture_enabled",
                DEFAULT_DIRECT_BATCH_HTML_CAPTURE_ENABLED,
            )
            self._direct_batch_html_capture_enabled = bool(direct_batch_html_capture_enabled)

            direct_batch_html_capture_subdir = str(
                url_cfg.get(
                    "direct_batch_html_capture_subdir",
                    DEFAULT_DIRECT_BATCH_HTML_CAPTURE_SUBDIR,
                )
                or DEFAULT_DIRECT_BATCH_HTML_CAPTURE_SUBDIR
            ).strip()
            if direct_batch_html_capture_subdir:
                self._direct_batch_html_capture_subdir = direct_batch_html_capture_subdir

            apify_actor = str(
                url_cfg.get("alltrails_apify_actor_id", DEFAULT_ALLTRAILS_APIFY_ACTOR_ID)
                or DEFAULT_ALLTRAILS_APIFY_ACTOR_ID
            ).strip()
            if apify_actor:
                self._alltrails_apify_actor_id = apify_actor

            apify_token_env = str(
                url_cfg.get("alltrails_apify_token_env", DEFAULT_ALLTRAILS_APIFY_TOKEN_ENV)
                or DEFAULT_ALLTRAILS_APIFY_TOKEN_ENV
            ).strip()
            if apify_token_env:
                self._alltrails_apify_token_env = apify_token_env

            apify_max_items = url_cfg.get("alltrails_apify_max_items", DEFAULT_ALLTRAILS_APIFY_MAX_ITEMS)
            try:
                parsed_apify_max_items = int(apify_max_items)
                if parsed_apify_max_items > 0:
                    self._alltrails_apify_max_items = parsed_apify_max_items
            except (TypeError, ValueError):
                self._alltrails_apify_max_items = DEFAULT_ALLTRAILS_APIFY_MAX_ITEMS

            apify_within_miles = url_cfg.get("alltrails_apify_within_miles", DEFAULT_ALLTRAILS_APIFY_WITHIN_MILES)
            try:
                parsed_apify_within_miles = float(apify_within_miles)
                if parsed_apify_within_miles > 0:
                    self._alltrails_apify_within_miles = parsed_apify_within_miles
            except (TypeError, ValueError):
                self._alltrails_apify_within_miles = DEFAULT_ALLTRAILS_APIFY_WITHIN_MILES

            raw_by_destination = url_cfg.get("alltrails_apify_within_miles_by_destination", {})
            if isinstance(raw_by_destination, dict):
                parsed_by_destination: dict[str, float] = {}
                for raw_key, raw_value in raw_by_destination.items():
                    key = self._normalize_destination_key(str(raw_key or ""))
                    if not key:
                        continue
                    try:
                        parsed_value = float(raw_value)
                    except (TypeError, ValueError):
                        continue
                    if parsed_value > 0:
                        parsed_by_destination[key] = parsed_value
                self._alltrails_apify_within_miles_by_destination = parsed_by_destination

            raw_overlap_min = url_cfg.get(
                "alltrails_apify_destination_token_overlap_min",
                DEFAULT_ALLTRAILS_APIFY_DESTINATION_TOKEN_OVERLAP_MIN,
            )
            try:
                parsed_overlap_min = int(raw_overlap_min)
                if parsed_overlap_min >= 0:
                    self._alltrails_apify_destination_token_overlap_min = parsed_overlap_min
            except (TypeError, ValueError):
                self._alltrails_apify_destination_token_overlap_min = (
                    DEFAULT_ALLTRAILS_APIFY_DESTINATION_TOKEN_OVERLAP_MIN
                )

            raw_restaurant_denylist = url_cfg.get("restaurant_name_denylist", [])
            if isinstance(raw_restaurant_denylist, list):
                self._restaurant_name_denylist = frozenset(
                    str(v or "").strip().lower()
                    for v in raw_restaurant_denylist
                    if str(v or "").strip()
                )

            raw_url_domain_denylist = url_cfg.get("url_domain_denylist", [])
            if isinstance(raw_url_domain_denylist, list):
                self._url_domain_denylist = frozenset(
                    str(v or "").strip().lower().lstrip(".")
                    for v in raw_url_domain_denylist
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

    def _persistent_cache_file(self) -> Path:
        return Path(getattr(self, "_persistent_cache_path", DEFAULT_PERSISTENT_CACHE_PATH))

    @staticmethod
    def _status_from_json(value: Any) -> int | str:
        if isinstance(value, int):
            return value
        return str(value or "")

    def _load_persistent_caches(self) -> None:
        if not bool(getattr(self, "_persistent_cache_enabled", DEFAULT_PERSISTENT_CACHE_ENABLED)):
            return

        cache_path = self._persistent_cache_file()
        if not cache_path.exists():
            return

        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return

            now = time.time()
            search_cutoff = now - (float(getattr(self, "_persistent_search_cache_ttl_hours", DEFAULT_PERSISTENT_SEARCH_CACHE_TTL_HOURS)) * 3600.0)
            page_cutoff = now - (float(getattr(self, "_persistent_page_text_cache_ttl_hours", DEFAULT_PERSISTENT_PAGE_TEXT_CACHE_TTL_HOURS)) * 3600.0)
            verify_cutoff = now - (float(getattr(self, "_persistent_verify_cache_ttl_hours", DEFAULT_PERSISTENT_VERIFY_CACHE_TTL_HOURS)) * 3600.0)

            for key, entry in (payload.get("search_results", {}) or {}).items():
                if not isinstance(key, str) or not isinstance(entry, dict):
                    continue
                ts = float(entry.get("ts", 0) or 0)
                if ts < search_cutoff:
                    continue
                rows = entry.get("results", [])
                if not isinstance(rows, list):
                    continue
                normalized = [dict(item) for item in rows if isinstance(item, dict)]
                if normalized:
                    self._search_results_cache[key] = normalized

            for key, entry in (payload.get("verify_results", {}) or {}).items():
                if not isinstance(key, str) or not isinstance(entry, dict):
                    continue
                ts = float(entry.get("ts", 0) or 0)
                if ts < verify_cutoff:
                    continue
                self._verify_url_cache[key] = (
                    bool(entry.get("ok", False)),
                    self._status_from_json(entry.get("status", "")),
                )

            for key, entry in (payload.get("page_text_results", {}) or {}).items():
                if not isinstance(key, str) or not isinstance(entry, dict):
                    continue
                ts = float(entry.get("ts", 0) or 0)
                if ts < page_cutoff:
                    continue
                self._page_text_cache[key] = (
                    bool(entry.get("ok", False)),
                    self._status_from_json(entry.get("status", "")),
                    str(entry.get("text", "") or ""),
                )
                final_url = str(entry.get("final_url", "") or "")
                if final_url:
                    self._fetch_final_url_cache[key] = final_url

            geocode_cutoff = now - (float(getattr(self, "_persistent_geocode_cache_ttl_hours", DEFAULT_PERSISTENT_GEOCODE_CACHE_TTL_HOURS)) * 3600.0)
            if not hasattr(self, "_en_route_stop_geocode_cache"):
                self._en_route_stop_geocode_cache = {}
            for key, entry in (payload.get("en_route_geocode", {}) or {}).items():
                if not isinstance(key, str) or not isinstance(entry, dict):
                    continue
                ts = float(entry.get("ts", 0) or 0)
                if ts < geocode_cutoff:
                    continue
                lat = entry.get("lat")
                lng = entry.get("lng")
                if lat is None or lng is None:
                    continue
                try:
                    self._en_route_stop_geocode_cache[key] = (float(lat), float(lng))
                except (TypeError, ValueError):
                    continue

            alltrails_cutoff = now - (float(getattr(self, "_persistent_alltrails_cache_ttl_hours", DEFAULT_PERSISTENT_ALLTRAILS_CACHE_TTL_HOURS)) * 3600.0)
            if not hasattr(self, "_alltrails_fetch_cache"):
                self._alltrails_fetch_cache = {}
            for key, entry in (payload.get("alltrails_fetch_results", {}) or {}).items():
                if not isinstance(key, str) or not isinstance(entry, dict):
                    continue
                ts = float(entry.get("ts", 0) or 0)
                if ts < alltrails_cutoff:
                    continue
                # Only successful fetches are ever persisted (see save side), but
                # guard defensively in case of a hand-edited cache file.
                if not bool(entry.get("ok", False)):
                    continue
                self._alltrails_fetch_cache[key] = (
                    True,
                    self._status_from_json(entry.get("status", "")),
                    str(entry.get("text", "") or ""),
                )

            harvest_cutoff = now - (float(getattr(self, "_persistent_harvest_cache_ttl_hours", DEFAULT_PERSISTENT_HARVEST_CACHE_TTL_HOURS)) * 3600.0)
            harvest_cache_by_section = {
                "direct_batch_harvest_alltrails": "_alltrails_direct_batch_cache",
                "direct_batch_harvest_attractions": "_attraction_direct_batch_cache",
                "direct_batch_harvest_restaurants": "_restaurant_direct_batch_cache",
                "direct_batch_harvest_en_route": "_en_route_direct_batch_cache",
            }
            for section_name, attr_name in harvest_cache_by_section.items():
                if not hasattr(self, attr_name):
                    setattr(self, attr_name, {})
                target_cache = getattr(self, attr_name)
                for key, entry in (payload.get(section_name, {}) or {}).items():
                    if not isinstance(key, str) or not isinstance(entry, dict):
                        continue
                    ts = float(entry.get("ts", 0) or 0)
                    if ts < harvest_cutoff:
                        continue
                    rows = entry.get("rows", [])
                    if not isinstance(rows, list):
                        continue
                    normalized = [dict(item) for item in rows if isinstance(item, dict)]
                    if normalized:
                        target_cache[key] = normalized
        except Exception as exc:
            logger.info("Persistent cache load skipped due to read/parse error: %s", exc)

    def _save_persistent_caches(self) -> None:
        if not bool(getattr(self, "_persistent_cache_enabled", DEFAULT_PERSISTENT_CACHE_ENABLED)):
            return
        if not bool(getattr(self, "_persistent_cache_dirty", False)):
            return

        # Tests may construct URLDiscoverer via __new__; ensure cache members
        # exist before persistence walks them.
        if not hasattr(self, "_search_results_cache"):
            self._search_results_cache = {}
        if not hasattr(self, "_verify_url_cache"):
            self._verify_url_cache = {}
        if not hasattr(self, "_page_text_cache"):
            self._page_text_cache = {}
        if not hasattr(self, "_en_route_stop_geocode_cache"):
            self._en_route_stop_geocode_cache = {}
        if not hasattr(self, "_alltrails_fetch_cache"):
            self._alltrails_fetch_cache = {}
        harvest_cache_by_section = {
            "direct_batch_harvest_alltrails": "_alltrails_direct_batch_cache",
            "direct_batch_harvest_attractions": "_attraction_direct_batch_cache",
            "direct_batch_harvest_restaurants": "_restaurant_direct_batch_cache",
            "direct_batch_harvest_en_route": "_en_route_direct_batch_cache",
        }
        for attr_name in harvest_cache_by_section.values():
            if not hasattr(self, attr_name):
                setattr(self, attr_name, {})

        cache_path = self._persistent_cache_file()
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        now = time.time()
        search_cutoff = now - (float(getattr(self, "_persistent_search_cache_ttl_hours", DEFAULT_PERSISTENT_SEARCH_CACHE_TTL_HOURS)) * 3600.0)
        page_cutoff = now - (float(getattr(self, "_persistent_page_text_cache_ttl_hours", DEFAULT_PERSISTENT_PAGE_TEXT_CACHE_TTL_HOURS)) * 3600.0)
        verify_cutoff = now - (float(getattr(self, "_persistent_verify_cache_ttl_hours", DEFAULT_PERSISTENT_VERIFY_CACHE_TTL_HOURS)) * 3600.0)
        geocode_cutoff = now - (float(getattr(self, "_persistent_geocode_cache_ttl_hours", DEFAULT_PERSISTENT_GEOCODE_CACHE_TTL_HOURS)) * 3600.0)
        alltrails_cutoff = now - (float(getattr(self, "_persistent_alltrails_cache_ttl_hours", DEFAULT_PERSISTENT_ALLTRAILS_CACHE_TTL_HOURS)) * 3600.0)
        harvest_cutoff = now - (float(getattr(self, "_persistent_harvest_cache_ttl_hours", DEFAULT_PERSISTENT_HARVEST_CACHE_TTL_HOURS)) * 3600.0)

        payload: dict[str, Any] = {
            "version": 1,
            "updated_at": now,
            "search_results": {},
            "verify_results": {},
            "page_text_results": {},
            "en_route_geocode": {},
            "alltrails_fetch_results": {},
            "direct_batch_harvest_alltrails": {},
            "direct_batch_harvest_attractions": {},
            "direct_batch_harvest_restaurants": {},
            "direct_batch_harvest_en_route": {},
        }

        for key, results in self._search_results_cache.items():
            payload["search_results"][key] = {
                "ts": now,
                "results": [dict(item) for item in results if isinstance(item, dict)],
            }

        for key, result in self._verify_url_cache.items():
            if not isinstance(result, tuple) or len(result) != 2:
                continue
            payload["verify_results"][key] = {
                "ts": now if now >= verify_cutoff else verify_cutoff,
                "ok": bool(result[0]),
                "status": result[1],
            }

        for key, result in self._page_text_cache.items():
            if not isinstance(result, tuple) or len(result) != 3:
                continue
            final_url = str(getattr(self, "_fetch_final_url_cache", {}).get(key, "") or "")
            payload["page_text_results"][key] = {
                "ts": now if now >= page_cutoff else page_cutoff,
                "ok": bool(result[0]),
                "status": result[1],
                "text": str(result[2] or "")[: int(DEFAULT_PERSISTENT_PAGE_TEXT_MAX_CHARS)],
                "final_url": final_url,
            }

        for key, result in self._en_route_stop_geocode_cache.items():
            # Only persist confirmed coordinates -- a "no result" (None) is often
            # a transient Nominatim rate-limit/timeout outcome, not a durable
            # "this place doesn't exist" answer, so it must not be frozen in.
            if not isinstance(result, tuple) or len(result) != 2:
                continue
            payload["en_route_geocode"][key] = {
                "ts": now if now >= geocode_cutoff else geocode_cutoff,
                "lat": result[0],
                "lng": result[1],
            }

        for key, result in self._alltrails_fetch_cache.items():
            # Only persist successful fetches -- caching a transient DataDome
            # block (401/403) would freeze that block state in across runs.
            if not isinstance(result, tuple) or len(result) != 3 or not result[0]:
                continue
            payload["alltrails_fetch_results"][key] = {
                "ts": now if now >= alltrails_cutoff else alltrails_cutoff,
                "ok": True,
                "status": result[1],
                "text": str(result[2] or "")[: int(DEFAULT_PERSISTENT_PAGE_TEXT_MAX_CHARS)],
            }

        for section_name, attr_name in harvest_cache_by_section.items():
            for key, rows in getattr(self, attr_name).items():
                if not isinstance(rows, list) or not rows:
                    # Never freeze in an empty harvest as a durable negative
                    # result -- an empty batch is often a transient upstream
                    # hiccup, not "this destination has no attractions".
                    continue
                payload[section_name][key] = {
                    "ts": now if now >= harvest_cutoff else harvest_cutoff,
                    "rows": [dict(item) for item in rows if isinstance(item, dict)],
                }

        tmp = cache_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
            tmp.replace(cache_path)
            self._persistent_cache_dirty = False
        except Exception as exc:
            logger.info("Persistent cache save skipped due to write error: %s", exc)

    def _mark_persistent_cache_dirty(self) -> None:
        self._persistent_cache_dirty = True
        self._persistent_cache_pending_writes = int(getattr(self, "_persistent_cache_pending_writes", 0) or 0) + 1
        write_every = max(1, int(getattr(self, "_persistent_cache_write_every", 25) or 25))
        if self._persistent_cache_pending_writes >= write_every:
            self._save_persistent_caches()
            self._persistent_cache_pending_writes = 0

    @staticmethod
    def _normalize_reason_code(reason: str) -> str:
        code = re.sub(r"[^a-z0-9_]+", "_", str(reason or "").strip().lower())
        code = re.sub(r"_+", "_", code).strip("_")
        return code or "unknown"

    def _trace_id(self, *, kind: str, dest_name: str, item_name: str) -> str:
        seed = f"{kind}|{dest_name}|{item_name}".strip().lower()
        normalized = re.sub(r"[^a-z0-9]+", "-", seed).strip("-")
        return normalized[:120] if normalized else "entity-unknown"

    @staticmethod
    def _infer_discovery_source(reason_code: str) -> str:
        code = str(reason_code or "").strip().lower()
        if code.startswith("direct_batch_item_fanout"):
            return "direct_batch_item_fanout"
        if code.startswith("direct_batch"):
            return "direct_batch"
        if code.startswith("alltrails"):
            return "alltrails"
        if code.startswith("search"):
            return "search"
        if code.startswith("maps"):
            return "maps"
        if code.startswith("ai_candidate"):
            return "ai_candidate"
        if code.startswith("interest_filter"):
            return "interest_filter"
        return "other"

    # Canonical outcome states shared across all entity kinds.
    # "accepted"  — a usable URL (or deliberate no-URL entry) was produced.
    # "rejected"  — discovery found nothing defensible; item carries no link.
    # "filtered"  — item was removed from the list by a hard rule (interest,
    #               freshness, trail-miles threshold, etc.).
    # "demoted"   — trail reclassified as attraction; may still appear in output.
    # "skipped"   — deliberately bypassed (disabled trails, seed-override, etc.).
    _ACCEPTED_REASON_SUFFIXES: frozenset[str] = frozenset({
        "accepted", "preserved", "recovered",
        "seed_ai_candidate_recovered",
        "direct_batch_source_locked_maps_fallback_assigned",
        "nps_deterministic_accepted",
        "discovery_completed",
    })
    _REJECTED_REASON_SUFFIXES: frozenset[str] = frozenset({
        "no_match", "source_locked_no_match", "maps_fallback_only",
        "direct_batch_no_accepted_candidates", "direct_batch_empty",
        "direct_batch_candidate_rejected", "direct_batch_candidate_rejected_generic",
        "url_rejected",
    })
    _FILTERED_REASON_CODES: frozenset[str] = frozenset({
        "interest_filter_skipped", "interest_filter_removed",
        "entity_removed",
        "trail_links_disabled",
    })
    _DEMOTED_REASON_CODES: frozenset[str] = frozenset({
        "threshold_demoted_to_attraction",
    })
    _SKIPPED_REASON_CODES: frozenset[str] = frozenset({
        "seed_threshold_override",
    })

    @classmethod
    def _classify_disposition_outcome(cls, reason_code: str, url: str) -> str:
        """Return a canonical outcome state for a single decision event.

        States: accepted | rejected | filtered | demoted | skipped
        """
        code = str(reason_code or "").strip().lower()
        if code in cls._FILTERED_REASON_CODES:
            return "filtered"
        if code in cls._DEMOTED_REASON_CODES:
            return "demoted"
        if code in cls._SKIPPED_REASON_CODES:
            return "skipped"
        for suffix in cls._ACCEPTED_REASON_SUFFIXES:
            if code == suffix or code.endswith(f"_{suffix}"):
                return "accepted"
        for suffix in cls._REJECTED_REASON_SUFFIXES:
            if code == suffix or code.endswith(f"_{suffix}"):
                return "rejected"
        # Fall back to URL presence: if a URL was emitted the item was accepted.
        return "accepted" if url else "rejected"

    def _record_discovery_stat(self, dest_name: str, reason_code: str, source_code: str) -> None:
        if not hasattr(self, "_decision_stats_by_destination"):
            self._decision_stats_by_destination = {}
        if not hasattr(self, "_decision_source_stats_by_destination"):
            self._decision_source_stats_by_destination = {}
        if not hasattr(self, "_request_cache_lock"):
            self._request_cache_lock = Lock()
        normalized_dest = str(dest_name or "").strip()
        if not normalized_dest:
            normalized_dest = "unknown"
        code = self._normalize_reason_code(reason_code)
        source = self._normalize_reason_code(source_code)
        with self._request_cache_lock:
            dest_bucket = self._decision_stats_by_destination.setdefault(normalized_dest, {})
            dest_bucket[code] = int(dest_bucket.get(code, 0) or 0) + 1
            source_bucket = self._decision_source_stats_by_destination.setdefault(normalized_dest, {})
            source_bucket[source] = int(source_bucket.get(source, 0) or 0) + 1

    def _record_disposition_thread_event(
        self,
        *,
        trace_id: str,
        kind: str,
        dest_name: str,
        item_name: str,
        reason_code: str,
        source_code: str,
        message: str,
        rendered_url: str,
    ) -> None:
        if not hasattr(self, "_decision_threads_by_destination"):
            self._decision_threads_by_destination = {}
        if not hasattr(self, "_request_cache_lock"):
            self._request_cache_lock = Lock()

        normalized_dest = str(dest_name or "").strip() or "unknown"
        with self._request_cache_lock:
            self._decision_event_sequence = int(getattr(self, "_decision_event_sequence", 0) or 0) + 1
            seq = self._decision_event_sequence
            by_trace = self._decision_threads_by_destination.setdefault(normalized_dest, {})
            thread = by_trace.setdefault(trace_id, [])
            thread.append(
                {
                    "seq": seq,
                    "kind": str(kind or ""),
                    "destination": normalized_dest,
                    "item": str(item_name or ""),
                    "reason": reason_code,
                    "source": source_code,
                    "message": str(message or ""),
                    "url": str(rendered_url or ""),
                }
            )

    def _log_decision(
        self,
        *,
        kind: str,
        dest_name: str,
        item_name: str,
        reason: str,
        message: str,
        url: str = "",
        level: int = logging.INFO,
    ) -> None:
        trace_id = self._trace_id(kind=kind, dest_name=dest_name, item_name=item_name)
        reason_code = self._normalize_reason_code(reason)
        source_code = self._infer_discovery_source(reason_code)
        rendered_url = url or "(none)"
        logger.log(
            level,
            "  [%s|reason=%s] %s: %s -> %s",
            trace_id,
            reason_code,
            message,
            item_name,
            rendered_url,
        )
        self._record_discovery_stat(dest_name, reason_code, source_code)
        self._record_disposition_thread_event(
            trace_id=trace_id,
            kind=kind,
            dest_name=dest_name,
            item_name=item_name,
            reason_code=reason_code,
            source_code=source_code,
            message=message,
            rendered_url=("" if rendered_url == "(none)" else rendered_url),
        )

    def _summarize_entity_dispositions(
        self,
        *,
        kind: str,
        disposition_threads: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Build a per-item disposition summary for one entity kind.

        Outcomes use canonical states: accepted | rejected | filtered | demoted | skipped.
        Each item's final_outcome is the highest-priority outcome seen across all events
        for that item (accepted > skipped > demoted > filtered > rejected).
        """
        _OUTCOME_PRIORITY = {"accepted": 5, "skipped": 4, "demoted": 3, "filtered": 2, "rejected": 1}
        item_map: dict[str, dict[str, Any]] = {}
        disposition_counts: dict[str, int] = {
            "accepted": 0, "rejected": 0, "filtered": 0, "demoted": 0, "skipped": 0,
        }
        source_counts: dict[str, int] = {}

        target_kind = str(kind or "").lower()
        # "trail" events are tagged with kind="attraction" in the current event schema;
        # support both so trail summaries work transparently.
        trail_aliases = {"trail", "attraction"}

        for events in disposition_threads.values():
            if not isinstance(events, list):
                continue
            for event in events:
                if not isinstance(event, dict):
                    continue
                event_kind = str(event.get("kind", "") or "").lower()
                if target_kind == "trail":
                    if event_kind not in trail_aliases:
                        continue
                elif event_kind != target_kind:
                    continue

                name = str(event.get("item", "") or "").strip()
                source = str(event.get("source", "") or "").strip() or "other"
                reason = str(event.get("reason", "") or "").strip()
                url = str(event.get("url", "") or "").strip()
                if not name:
                    continue

                outcome = self._classify_disposition_outcome(reason, url)
                source_counts[source] = int(source_counts.get(source, 0) or 0) + 1

                if name not in item_map:
                    item_map[name] = {
                        "name": name,
                        "final_outcome": outcome,
                        "source": source,
                        "reasons": [reason] if reason else [],
                        "urls": [url] if url else [],
                    }
                else:
                    entry = item_map[name]
                    if _OUTCOME_PRIORITY.get(outcome, 0) > _OUTCOME_PRIORITY.get(entry["final_outcome"], 0):
                        entry["final_outcome"] = outcome
                        entry["source"] = source
                    if reason and reason not in entry["reasons"]:
                        entry["reasons"].append(reason)
                    if url and url not in entry["urls"]:
                        entry["urls"].append(url)

        items = list(item_map.values())
        for entry in items:
            outcome = entry["final_outcome"]
            disposition_counts[outcome] = int(disposition_counts.get(outcome, 0) or 0) + 1

        return {
            "kind": kind,
            "total": len(items),
            "disposition_counts": disposition_counts,
            "source_counts": source_counts,
            "items": items,
        }

    # ── Public entry point ───────────────────────────────────────────────────

    def is_search_circuit_open(self) -> bool:
        """Non-raising peek at the underlying GrokSearch circuit-breaker state,
        for callers (e.g. main.py's selective-retry gate) that want to skip
        firing a whole extra pass into a known-ongoing outage rather than
        discovering that per-destination as each retry fails fast."""
        return bool(
            hasattr(self, "_search")
            and self._search is not None
            and hasattr(self._search, "is_circuit_open")
            and self._search.is_circuit_open()
        )

    def discover_all(self, trip: dict[str, Any]) -> None:
        destinations = trip.get("destinations", [])

        def _discover_one(dest: dict) -> None:
            name = dest["name"]
            ai = dest.get("ai_content", {})
            nps_code = dest.get("nps_park_code")
            origin_name = str(dest.get("_en_route_origin", "") or "")
            logger.info("URL discovery for '%s'…", name)
            # Parallelise the four independent URL categories within each destination
            with ThreadPoolExecutor(max_workers=4) as inner:
                futs = [
                    inner.submit(self._discover_attractions, ai, name, nps_code, dest.get("dates"), dest.get("seeds", []), dest=dest),
                    inner.submit(self._discover_restaurants, ai, name, dest.get("dates"), dest),
                    inner.submit(
                        self._discover_en_route_stops,
                        ai,
                        name,
                        dest.get("dates"),
                        origin_name,
                        dest.get("_en_route_origin_lat"),
                        dest.get("_en_route_origin_lng"),
                        dest.get("lat"),
                        dest.get("lng"),
                    ),
                    inner.submit(self._discover_scenic_drives, dest, name, nps_code),
                ]
                for f in as_completed(futs):
                    f.result()
            if not hasattr(self, "_request_cache_lock"):
                self._request_cache_lock = Lock()
            with self._request_cache_lock:
                decision_counts = dict(getattr(self, "_decision_stats_by_destination", {}).get(name, {}))
                source_counts = dict(getattr(self, "_decision_source_stats_by_destination", {}).get(name, {}))
                raw_threads = dict(getattr(self, "_decision_threads_by_destination", {}).get(name, {}))

            disposition_threads: dict[str, list[dict[str, Any]]] = {
                trace: [dict(event) for event in events if isinstance(event, dict)]
                for trace, events in raw_threads.items()
                if isinstance(events, list)
            }
            event_count = sum(len(events) for events in disposition_threads.values())
            dest["_url_discovery"] = {
                "reason_counts": decision_counts,
                "source_counts": source_counts,
                "thread_count": len(disposition_threads),
                "event_count": event_count,
                "disposition_threads": disposition_threads,
                "restaurant_dispositions": self._summarize_entity_dispositions(
                    kind="restaurant",
                    disposition_threads=disposition_threads,
                ),
                "attraction_dispositions": self._summarize_entity_dispositions(
                    kind="attraction",
                    disposition_threads=disposition_threads,
                ),
                "trail_dispositions": self._summarize_entity_dispositions(
                    kind="trail",
                    disposition_threads=disposition_threads,
                ),
                "en_route_stop_dispositions": self._summarize_entity_dispositions(
                    kind="en_route_stop",
                    disposition_threads=disposition_threads,
                ),
                "scenic_drive_dispositions": self._summarize_entity_dispositions(
                    kind="scenic_drive",
                    disposition_threads=disposition_threads,
                ),
            }

            top_counts = sorted(decision_counts.items(), key=lambda row: row[1], reverse=True)
            summary_bits = ", ".join(f"{k}={v}" for k, v in top_counts[:6]) if top_counts else "none"
            logger.info("URL discovery summary for '%s': %s", name, summary_bits)

        for idx, dest in enumerate(destinations):
            if not isinstance(dest, dict):
                continue
            origin_name = ""
            origin_lat = None
            origin_lng = None
            if idx > 0 and isinstance(destinations[idx - 1], dict):
                origin_name = str(destinations[idx - 1].get("name", "") or "").strip()
                origin_lat = destinations[idx - 1].get("lat")
                origin_lng = destinations[idx - 1].get("lng")
            dest["_en_route_origin"] = origin_name
            dest["_en_route_origin_lat"] = origin_lat
            dest["_en_route_origin_lng"] = origin_lng

        # Pre-populate per-destination direct-batch caches from grouped
        # multi-destination calls before the per-destination pass below runs,
        # so _discover_attractions (attractions + AllTrails trails) and
        # _discover_restaurants (via their _get_*_direct_batch_rows_for_destination
        # getters) see cache hits instead of firing individual calls -- see
        # _prefetch_grouped_direct_batch for the no-op-when-disabled and
        # fail-open-per-destination behavior.
        self._prefetch_grouped_direct_batch(destinations)

        with ThreadPoolExecutor(max_workers=min(len(destinations), 3)) as pool:
            futures = [pool.submit(_discover_one, d) for d in destinations]
            for f in as_completed(futures):
                f.result()

        for dest in destinations:
            if isinstance(dest, dict):
                dest.pop("_en_route_origin", None)
                dest.pop("_en_route_origin_lat", None)
                dest.pop("_en_route_origin_lng", None)
        self._save_persistent_caches()

    def audit_discovered_urls(self, trip: dict[str, Any]) -> None:
        """Strip low-confidence discovered URLs before HTML assembly.

        This is a final safety pass. Discovery should already be strict, but
        we remove anything that still looks weak so bad links do not reach the
        generated itinerary.
        """
        # Single-pass URL evidence prefetch: validate and fetch page text once
        # per unique non-AllTrails URL so downstream checks reuse cached results.
        self._prewarm_url_validation_cache(trip)

        for dest in trip.get("destinations", []):
            dest_name = dest.get("name", "")
            dest_dates = str(dest.get("dates", "") or "")
            seed_key_set = {
                re.sub(r"[^a-z0-9]+", " ", str(seed or "").lower()).strip()
                for seed in (dest.get("seeds", []) or [])
                if str(seed or "").strip()
            }
            ai = dest.get("ai_content", {}) if isinstance(dest.get("ai_content", {}), dict) else {}

            max_trail_miles = float(getattr(self, "_max_trail_miles", DEFAULT_MAX_TRAIL_MILES) or 0)
            eligible_attractions: list[dict[str, Any]] = []
            for attr in ai.get("top_attractions", []) or []:
                attr_name = str(attr.get("name", "") or "")
                attr_key = re.sub(r"[^a-z0-9]+", " ", attr_name.lower()).strip()
                is_seed = bool(attr_key and attr_key in seed_key_set)
                attr["is_seed"] = is_seed
                attr_type = str(attr.get("type", "attraction") or "attraction").lower()
                attr_desc = str(attr.get("description", "") or "")
                attr_context = self._attraction_trail_context(attr)
                url = str(attr.get("url", "") or "").strip()
                trail_like = self._is_trail_like_attraction(attr_name, attr_type, attr_context) or self._is_alltrails_trail_url(url)
                direct_batch_authoritative_url = self._is_remembered_direct_batch_authoritative_url(url, attr_name)
                maps_url = str(attr.get("maps_url", "") or "").strip()
                if not maps_url and self._classify_url_policy_class(url) in {"google_maps_search", "google_maps_dir"}:
                    maps_url = url

                if self._is_uninterested_attraction(attr_name, attr_type, attr_desc, dest_dates):
                    self._record_registry_entity_removal(
                        dest,
                        section_target="top_attractions",
                        entity_class="trail" if trail_like else "attraction",
                        display_name=attr_name,
                        description=attr_desc,
                        rejection_reason="interest_filter_removed",
                    )
                    continue

                # PR-028: enforce max_trail_miles from AI description when page fetch is unavailable
                if trail_like and max_trail_miles > 0:
                    desc_text = (
                        str(attr.get("description", "") or "") + " " +
                        str(attr.get("practical_note", "") or "")
                    )
                    threshold_miles = self._extract_trail_miles(desc_text)
                    if threshold_miles is None and self._is_alltrails_trail_url(url):
                        ok, _status, page_text = self._fetch_page_text(url, timeout=8)
                        if ok and page_text:
                            threshold_miles = self._extract_trail_miles(page_text)
                    if threshold_miles is not None and threshold_miles > max_trail_miles and not is_seed:
                        logger.info(
                            "  Trail miles threshold exceeded for '%s' in '%s': %.1f mi > %.1f mi",
                            attr_name, dest_name, threshold_miles, max_trail_miles,
                        )
                        attr.pop("url", None)
                        attr["type"] = "attraction"
                        threshold_note = self._build_trail_threshold_note(
                            miles=threshold_miles,
                            max_miles=max_trail_miles,
                        )
                        if threshold_note:
                            existing_note = str(attr.get("practical_note", "") or "").strip()
                            if existing_note:
                                if threshold_note.lower() not in existing_note.lower():
                                    attr["practical_note"] = f"{existing_note} {threshold_note}".strip()
                            else:
                                attr["practical_note"] = threshold_note
                        # Do not keep synthetic/non-single-target fallback links
                        # when demoting over-threshold trails.
                        attr.pop("url", None)
                        attr.pop("maps_url", None)
                        self._log_decision(
                            kind="attraction",
                            dest_name=dest_name,
                            item_name=attr_name,
                            reason="threshold_demoted_to_attraction",
                            message="trail-like attraction exceeded threshold and was demoted to non-hike attraction",
                            url="",
                        )
                        self._annotate_registry_url_decision(attr, rendered_url="", rejection_reason="threshold_demoted_to_attraction")
                        eligible_attractions.append(attr)
                        continue
                    if threshold_miles is not None and threshold_miles > max_trail_miles and is_seed:
                        self._log_decision(
                            kind="attraction",
                            dest_name=dest_name,
                            item_name=attr_name,
                            reason="seed_threshold_override",
                            message="seed attraction exceeds trail threshold but link retention allowed",
                            url=str(attr.get("url", "") or ""),
                        )

                if trail_like and url and not self._is_alltrails_trail_url(url):
                    policy_class = self._classify_url_policy_class(url)
                    if policy_class in {"google_maps_search", "google_maps_dir"}:
                        if not is_seed:
                            attr.pop("url", None)
                        if maps_url:
                            attr["maps_url"] = maps_url
                        elif not is_seed:
                            attr.pop("maps_url", None)
                        self._annotate_registry_url_decision(
                            attr,
                            rendered_url=url if is_seed else "",
                            rejection_reason="" if is_seed else "url_rejected",
                        )
                    else:
                        self._log_rejected_url("attraction", dest_name, attr_name, url)
                        attr.pop("url", None)
                        if maps_url:
                            attr["maps_url"] = maps_url
                        self._annotate_registry_url_decision(attr, rendered_url="", rejection_reason="url_rejected")
                    eligible_attractions.append(attr)
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
                        if maps_url:
                            attr["maps_url"] = maps_url
                        self._annotate_registry_url_decision(attr, rendered_url=cleaned)
                    else:
                        attr.pop("url", None)
                        if maps_url:
                            attr["maps_url"] = maps_url
                        else:
                            attr.pop("maps_url", None)
                        self._annotate_registry_url_decision(attr, rendered_url="", rejection_reason="url_rejected")
                eligible_attractions.append(attr)

            if len(eligible_attractions) != len(ai.get("top_attractions", []) or []):
                ai["top_attractions"] = eligible_attractions

            # Collect attraction URLs for PR-004: drive URL dedup
            attraction_urls: set[str] = {
                str(a.get("url", "") or "").strip()
                for a in (ai.get("top_attractions", []) or [])
                if str(a.get("url", "") or "").strip()
            }

            for stop in ai.get("getting_here", {}).get("en_route_stops", []) or []:
                stop_name = str(stop.get("name", "") or "")
                url = str(stop.get("url", "") or "").strip()
                direct_batch_authoritative_url = self._is_remembered_direct_batch_authoritative_url(url, stop_name)
                maps_url = str(stop.get("maps_url", "") or "").strip()
                if not maps_url and self._classify_url_policy_class(url) in {"google_maps_search", "google_maps_dir"}:
                    maps_url = url
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
                        if maps_url:
                            stop["maps_url"] = maps_url
                        self._annotate_registry_url_decision(stop, rendered_url=cleaned)
                    else:
                        stop.pop("url", None)
                        if maps_url:
                            stop["maps_url"] = maps_url
                        else:
                            stop.pop("maps_url", None)
                        self._annotate_registry_url_decision(stop, rendered_url="", rejection_reason="url_rejected")

            for route_opt in ai.get("getting_there", {}).get("route_options", []) or []:
                opt_name = str(route_opt.get("title", "") or route_opt.get("name", "") or "")
                url = str(route_opt.get("url", "") or "").strip()
                cleaned = self._retain_discovered_url(
                    url,
                    opt_name,
                    dest_name,
                    allow_alltrails=False,
                    kind="getting_there route option",
                )
                if cleaned != url:
                    self._log_rejected_url("getting_there route option", dest_name, opt_name, url)
                    if cleaned:
                        route_opt["url"] = cleaned
                        self._annotate_registry_url_decision(route_opt, rendered_url=cleaned)
                    else:
                        route_opt.pop("url", None)
                        self._annotate_registry_url_decision(route_opt, rendered_url="", rejection_reason="url_rejected")

            eligible_restaurants: list[dict[str, Any]] = []
            for rest in ai.get("dinner_recommendations", []) or []:
                rest_name = str(rest.get("name", "") or "")
                url = str(rest.get("url", "") or "").strip()
                direct_batch_authoritative_url = self._is_remembered_direct_batch_authoritative_url(url, rest_name)
                maps_url = str(rest.get("maps_url", "") or "").strip()
                if not maps_url and self._classify_url_policy_class(url) in {"google_maps_search", "google_maps_dir"}:
                    maps_url = url
                cleaned = self._retain_discovered_url(
                    url,
                    rest_name,
                    dest_name,
                    allow_alltrails=False,
                    kind="restaurant",
                )
                if not cleaned and direct_batch_authoritative_url and not self._is_google_maps_candidate_url(url):
                    cleaned = url
                if cleaned != url:
                    self._log_rejected_url("restaurant", dest_name, rest_name, url)
                    if not cleaned:
                        self._log_decision(
                            kind="restaurant",
                            dest_name=dest_name,
                            item_name=rest_name,
                            reason="audit_url_rejected",
                            message="restaurant URL stripped by audit retention gate",
                            url=url,
                        )
                    if cleaned:
                        rest["url"] = cleaned
                        if maps_url:
                            rest["maps_url"] = maps_url
                        self._annotate_registry_url_decision(rest, rendered_url=cleaned)
                    else:
                        rest.pop("url", None)
                        if maps_url:
                            rest["maps_url"] = maps_url
                        else:
                            rest.pop("maps_url", None)
                        self._annotate_registry_url_decision(rest, rendered_url="", rejection_reason="url_rejected")
                if (not direct_batch_authoritative_url) and self._is_restaurant_ineligible(rest, dest_name):
                    logger.info(
                        "  Restaurant freshness gate removed '%s' in '%s'",
                        rest_name, dest_name,
                    )
                    self._record_registry_entity_removal(
                        dest,
                        section_target="dinner_recommendations",
                        entity_class="restaurant",
                        display_name=rest_name,
                        description=str(rest.get("description", "") or ""),
                        rejection_reason="entity_removed",
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
                # PR-004: reject drive URL when it duplicates an attraction URL
                if cleaned and cleaned in attraction_urls:
                    logger.info(
                        "  Scenic drive URL dedup (matches attraction): '%s' in '%s': %s",
                        drive_name, dest_name, cleaned,
                    )
                    cleaned = ""
                if cleaned != url:
                    self._log_rejected_url("scenic drive", dest_name, drive_name, url)
                    if not cleaned:
                        self._log_decision(
                            kind="scenic_drive",
                            dest_name=dest_name,
                            item_name=drive_name,
                            reason="audit_url_rejected",
                            message="scenic drive URL stripped by audit retention gate",
                            url=url,
                        )
                    if cleaned:
                        drive["url"] = cleaned
                        self._annotate_registry_url_decision(drive, rendered_url=cleaned)
                    else:
                        drive.pop("url", None)
                        self._annotate_registry_url_decision(drive, rendered_url="", rejection_reason="url_rejected")

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
                            self._annotate_registry_url_decision(event, rendered_url=cleaned)
                        else:
                            event.pop("url", None)
                            self._annotate_registry_url_decision(event, rendered_url="", rejection_reason="url_rejected")

            self._deduplicate_within_destination(dest)

        self._deduplicate_cross_destination_drives(trip)
        self._deduplicate_attractions_against_en_route_stops_tripwide(trip)

    def _retain_discovered_url(
        self,
        url: str,
        item_name: str,
        dest_name: str,
        *,
        allow_alltrails: bool,
        kind: str = "generic",
        candidate: dict[str, Any] | None = None,
        allow_shallow_relevance: bool = False,
        allow_google_maps_search: bool = False,
    ) -> str:
        if not url:
            return ""
        if self._is_url_domain_denied(url):
            logger.info("URL domain denylist hit for %s '%s': %s", kind, item_name, url)
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
        if self._has_unescaped_whitespace(url):
            logger.info("URL rejected due to unescaped whitespace for %s '%s': %s", kind, item_name, url)
            return ""
        if self._is_incomplete_google_maps_place_url(url):
            logger.info("URL rejected incomplete google maps place link for %s '%s': %s", kind, item_name, url)
            return ""
        if self._is_deterministic_google_maps_place_url(url) and kind in {"attraction", "en-route stop", "en_route_stop"}:
            if self._looks_synthetic_google_maps_place_url(url):
                logger.info(
                    "Maps place URL rejected as synthetic for %s '%s': %s",
                    kind,
                    item_name,
                    url,
                )
                return ""
            # Require substantial entity-token overlap for deterministic place pages.
            item_tokens = self._significant_tokens(item_name)
            if item_tokens:
                ok, _status, page_html = self._fetch_page_text(url, timeout=8)
                if not ok or not page_html:
                    logger.info(
                        "Maps place URL rejected: unable to verify page content for %s '%s': %s",
                        kind,
                        item_name,
                        url,
                    )
                    return ""
                lower_html = page_html.lower()
                parsed = urlparse(url)
                place_label = ""
                path_l = (parsed.path or "").lower()
                if path_l.startswith("/maps/place/"):
                    place_label = unquote((parsed.path or "").split("/maps/place/", 1)[-1].split("/", 1)[0]).replace("+", " ")
                elif path_l.startswith("/place/"):
                    place_label = unquote((parsed.path or "").split("/place/", 1)[-1].split("/", 1)[0]).replace("+", " ")
                label_token_set = set(self._significant_tokens(place_label))
                required_overlap = self._required_general_token_matches(len(item_tokens))
                page_overlap = sum(1 for t in item_tokens if t in lower_html)
                label_overlap = sum(1 for t in item_tokens if t in label_token_set)
                if max(page_overlap, label_overlap) < required_overlap:
                    logger.info(
                        "Maps place URL rejected: weak entity token overlap for %s '%s': %s",
                        kind, item_name, url,
                    )
                    return ""
                generic_entity_tokens = {
                    "historic",
                    "district",
                    "museum",
                    "park",
                    "state",
                    "national",
                    "visitor",
                    "center",
                    "trail",
                    "hike",
                    "canyon",
                    "springs",
                }
                anchor_tokens = [t for t in item_tokens if t not in generic_entity_tokens]
                if anchor_tokens:
                    anchor_overlap = sum(
                        1
                        for t in anchor_tokens
                        if t in lower_html or t in label_token_set
                    )
                    if anchor_overlap < 1:
                        logger.info(
                            "Maps place URL rejected: no distinctive entity-token overlap for %s '%s': %s",
                            kind,
                            item_name,
                            url,
                        )
                        return ""
        if self._direct_batch_is_authoritative() and self._is_remembered_direct_batch_authoritative_url(url, item_name):
            logger.info(
                "Remembered authoritative direct-batch URL preserved for %s '%s' (%s): %s",
                kind,
                item_name or "unknown",
                dest_name or "unknown destination",
                url,
            )
            return url

        if (
            self._direct_batch_is_authoritative()
            and kind == "restaurant"
            and isinstance(candidate, dict)
            and self._direct_batch_row_matches_item(candidate, item_name, dest_name)
            and not self._is_google_maps_candidate_url(url)
        ):
            # This leniency is restaurant-specific per the documented direct-link-batch
            # authoritative contract (non-map snippet/source URLs may be accepted with
            # weak path tokens once the row matches). Other kinds (attraction, en-route
            # stop, ...) must still clear the dedicated generic-section-landing-page and
            # relevance gates below — those checks (not this restaurant-shaped pattern
            # list) are what catch a generic "things to do" listing page for that kind.
            parsed = urlparse(url)
            host_l = (parsed.netloc or "").lower()
            path_l = (parsed.path or "").lower()
            lower_url = (url or "").lower()
            obvious_area_listing = (
                "tripadvisor." in host_l and ("/restaurants-" in path_l or "restaurants-g" in lower_url)
                or "google.com/maps" in host_l
                or "maps.google.com" in host_l
                or "/restaurants/" in path_l
                or "restaurants-near" in lower_url
                or "best-restaurants-near" in lower_url
            )
            if not obvious_area_listing:
                # This leniency still must not bypass the hard URL-class policy gate
                # (e.g. social_media blocked in enforce mode) — item-row matching is
                # about entity relevance, not URL-class safety.
                leniency_policy_class = self._classify_url_policy_class(url)
                leniency_blocked_classes = getattr(self, "_url_policy_blocked_classes", set(DEFAULT_URL_POLICY_BLOCKED_CLASSES))
                leniency_policy_mode = getattr(self, "_url_policy_mode", DEFAULT_URL_POLICY_MODE)
                if leniency_policy_class in leniency_blocked_classes and leniency_policy_mode == "enforce":
                    logger.info(
                        "URL policy rejected [%s] for %s '%s' (%s): %s",
                        leniency_policy_class,
                        kind,
                        item_name or "unknown",
                        dest_name or "unknown destination",
                        url,
                    )
                    return ""
                # This leniency still must not publish a URL that is definitively
                # dead (404/410, or a DNS/connection failure meaning the host
                # doesn't exist at all). Relevance leniency is not a liveness
                # exemption -- a matched row pointing at a domain that fails to
                # resolve is not "close enough", it is a broken link.
                ok, fetch_status, _ = self._fetch_page_text(url, timeout=8)
                if not ok and self._is_definitively_dead_status(fetch_status):
                    logger.info(
                        "Rejected dead item-matched authoritative direct-batch URL for %s '%s' (%s): %s (%s)",
                        kind,
                        item_name or "unknown",
                        dest_name or "unknown destination",
                        url,
                        fetch_status,
                    )
                    return ""
                logger.info(
                    "Item-matched authoritative direct-batch URL preserved for %s '%s' (%s): %s",
                    kind,
                    item_name or "unknown",
                    dest_name or "unknown destination",
                    url,
                )
                return url

        if kind == "restaurant":
            if self._is_google_maps_candidate_url(url):
                logger.info(
                    "URL rejected google maps candidate for %s '%s': %s",
                    kind,
                    item_name,
                    url,
                )
                return ""
            _rest_tokens = self._restaurant_significant_tokens(item_name)
            if self._looks_like_item_specific_homepage(url, item_name, item_tokens=_rest_tokens):
                return url
            if self._is_generic_restaurant_landing_url(url, item_name, dest_name, item_tokens=_rest_tokens):
                logger.info(
                    "URL rejected generic restaurant landing page for %s '%s': %s",
                    kind,
                    item_name,
                    url,
                )
                return ""
        if kind in {"generic", "attraction", "en-route stop", "getting_there route option"}:
            if self._is_generic_section_landing_page(url):
                if not self._looks_like_item_specific_homepage(url, item_name):
                    logger.info(
                        "URL rejected generic section landing page for %s '%s': %s",
                        kind,
                        item_name,
                        url,
                    )
                    return ""
                # Falls through to the relevance gate so page text can confirm the entity.
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
            generic_wiki_tokens = {
                "historic",
                "district",
                "national",
                "register",
                "listing",
                "listings",
                "county",
                "place",
                "places",
                "state",
                "city",
                "town",
                "utah",
            }
            distinctive_item_tokens = [
                t for t in item_tokens if t not in generic_wiki_tokens and len(t) >= 4
            ]
            if distinctive_item_tokens and not any(t in wiki_slug for t in distinctive_item_tokens):
                logger.info(
                    "Wikipedia entity-path generic-only overlap rejected for %s '%s': %s",
                    kind, item_name, url,
                )
                return ""
        if kind in {"generic", "attraction"} and self._is_category_style_activity(item_name):
            if self._is_generic_geographic_url_for_category(url, item_name):
                logger.info(
                    "Category-style activity rejected generic geography URL for %s '%s': %s",
                    kind,
                    item_name,
                    url,
                )
                return ""
            if self._is_category_offer_listing_url(url):
                logger.info(
                    "Category-style activity rejected offer/listing URL for %s '%s': %s",
                    kind,
                    item_name,
                    url,
                )
                return ""
        if kind == "scenic drive" and not self._is_route_specific_scenic_drive_url(url):
            logger.info("Scenic-drive URL rejected (non-route target) for '%s': %s", item_name, url)
            return ""
        if allow_alltrails and self._is_alltrails_trail_url(url):
            if not self._meets_alltrails_publish_confidence(url, item_name, dest_name):
                return ""
            if not self._passes_alltrails_post_search_filters(url, item_name, dest_name):
                return ""
        if not is_safe_fallback:
            # Keep the direct-batch fail-closed rule narrow: only reject a curated
            # row when the URL is explicitly dead (404/410). Generic landing pages
            # and vague search results still need to clear the normal relevance gate.
            if (
                self._direct_batch_is_authoritative()
                and kind in {"attraction", "en-route stop", "en_route_stop", "restaurant"}
                and isinstance(candidate, dict)
                and not self._candidate_mentions_conflicting_destination(candidate, dest_name, item_name=item_name)
                and self._direct_batch_row_matches_item(candidate, item_name, dest_name)
            ):
                ok, status, _ = self._fetch_page_text(url, timeout=8)
                if not ok and self._is_definitively_dead_status(status):
                    logger.info(
                        "Rejected dead authoritative direct-batch candidate for %s '%s' (%s): %s",
                        kind,
                        item_name or "unknown",
                        dest_name or "unknown destination",
                        url,
                    )
                    return ""
                item_tokens = self._significant_tokens(item_name)
                if len(item_tokens) <= 1 and self._candidate_text_matches_item_tokens(candidate, item_tokens):
                    logger.info(
                        "Preserved direct-batch single-token item %s '%s' (%s): %s",
                        kind,
                        item_name or "unknown",
                        dest_name or "unknown destination",
                        url,
                    )
                    return url

            deep_check = not allow_shallow_relevance
            if not self._is_relevant_result(url, item_name, dest_name, candidate=candidate, deep_check=deep_check):
                return ""

        policy_class = self._classify_url_policy_class(url)
        blocked_classes = getattr(self, "_url_policy_blocked_classes", set(DEFAULT_URL_POLICY_BLOCKED_CLASSES))
        policy_mode = getattr(self, "_url_policy_mode", DEFAULT_URL_POLICY_MODE)
        blocked = policy_class in blocked_classes
        if blocked and policy_mode == "enforce":
            if allow_google_maps_search and policy_class in {"google_maps_search", "google_maps_dir"}:
                logger.info(
                    "URL policy exception for direct-batch harvest [%s] for %s '%s' (%s): %s",
                    policy_class,
                    kind,
                    item_name or "unknown",
                    dest_name or "unknown destination",
                    url,
                )
            else:
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

    def _is_url_domain_denied(self, url: str) -> bool:
        denylist = getattr(self, "_url_domain_denylist", frozenset())
        if not denylist:
            return False
        host = (urlparse(url).netloc or "").strip().lower()
        if not host:
            return False
        host = host.split(":", 1)[0].strip(".")
        if host.startswith("www."):
            host = host[4:]
        for blocked in denylist:
            normalized = (blocked or "").strip().lower().lstrip(".")
            if not normalized:
                continue
            if host == normalized or host.endswith(f".{normalized}"):
                return True
        return False

    @staticmethod
    def _classify_url_policy_class(url: str) -> str:
        lower = (url or "").lower()
        if "google.com/maps/dir/" in lower or "maps.google.com/maps/dir/" in lower:
            return "google_maps_dir"
        if "google.com/maps/search" in lower:
            return "google_maps_search"
        if "maps.google.com" in lower and ("?q=" in lower or "&q=" in lower or "?query=" in lower or "&query=" in lower):
            return "google_maps_search"
        if "google.com/search" in lower:
            return "google_search"
        if any(domain in lower for domain in ("facebook.com", "instagram.com", "tiktok.com", "x.com", "twitter.com")):
            return "social_media"
        if "alltrails.com" in lower:
            return "alltrails"
        return "general"

    @classmethod
    def _is_incomplete_google_maps_place_url(cls, url: str | None) -> bool:
        parsed = urlparse(str(url or "").strip())
        if not cls._is_google_maps_domain(parsed.netloc):
            return False
        path_l = (parsed.path or "").lower()
        query_l = (parsed.query or "").lower()
        if path_l.startswith("/maps/place/"):
            return not (("/data=" in path_l) or ("cid=" in query_l) or ("ftid=" in query_l))
        return False

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
                return self._boost_alltrails_confidence_via_corroboration("medium", url, item_name, dest_name)
            return "low"

        if self._is_definitively_dead_status(status):
            return "low"

        # Secondary liveness probe for bot-blocked/sparse fetches: dead slugs can
        # still return 404/410 to HEAD/GET verification even when page text fetch
        # is blocked.
        verified_ok, verified_status = self._verify_url_cached(url)
        if not verified_ok and self._is_definitively_dead_status(verified_status):
            return "low"

        blocked = isinstance(status, int) and status in (401, 403)
        if blocked:
            # Blocked fetches are common; only strict slug matches qualify as medium.
            if slug_extra_terms == 0:
                return self._boost_alltrails_confidence_via_corroboration("medium", url, item_name, dest_name)
            return "low"

        return "low"

    def _boost_alltrails_confidence_via_corroboration(
        self, base_confidence: str, url: str, item_name: str, dest_name: str
    ) -> str:
        """Corroboration signal: when the primary page-text/liveness check can only
        get to "medium" confidence (page fetch was inconclusive or blocked), an
        independent secondary lookup pointing at the exact same trail page is
        strong evidence the candidate is genuinely correct -- promote it to
        "high" rather than leaving it stuck at "medium" (or getting silently
        capped out by a stricter publish threshold). This never *lowers*
        confidence and never overrides an explicit "low" (dead/mismatched slugs
        stay rejected regardless of what a second source says) -- it only
        rescues a borderline candidate, per the "augment scoring for inclusion,
        don't replace the primary link" principle. Opt-in via the same config
        flag that already gates the (more expensive) filtered-selection search,
        since this triggers one extra search call per borderline trail.
        """
        if base_confidence != "medium":
            return base_confidence
        if not bool(getattr(self, "_enable_filtered_alltrails_selection", DEFAULT_ENABLE_FILTERED_ALLTRAILS_SELECTION)):
            return base_confidence
        try:
            corroborated_url = self._get_filtered_alltrails_selection(item_name=item_name, dest_name=dest_name)
        except Exception:
            return base_confidence
        if corroborated_url and self._same_alltrails_trail(url, corroborated_url):
            self._log_decision(
                kind="attraction",
                dest_name=dest_name,
                item_name=item_name,
                reason="alltrails_confidence_boosted_by_corroboration",
                message="alltrails confidence promoted medium->high: independent search agreed",
                url=url,
            )
            return "high"
        return base_confidence

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
        if not attractions and not drives:
            return

        attr_token_sets: list[frozenset[str]] = []
        for attr in attractions:
            name = str(attr.get("name", "") or "")
            tokens = frozenset(self._significant_tokens(name))
            if len(tokens) >= 2:
                attr_token_sets.append(tokens)

        if drives and attr_token_sets:
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
                        # Recorded so the entity registry (and, transitively,
                        # schedule reconciliation) can see this removal --
                        # without this the schedule could keep referencing a
                        # drive that silently vanished here.
                        self._record_registry_entity_removal(
                            dest,
                            section_target="scenic_drives",
                            entity_class="scenic_drive",
                            display_name=title,
                            rejection_reason="duplicate_of_attraction",
                            description=str(drive.get("description", "") or ""),
                        )
                        duplicate = True
                        break

                if not duplicate:
                    kept_drives.append(drive)

            dest["scenic_drives"] = kept_drives

        # Merge top_attractions entries that point at the exact same URL --
        # the AI-generated content and the direct-batch link harvest run
        # independently, so the same real place can surface twice under
        # different names that both resolve to the same canonical page
        # (dipstick55 Theme F: "Telluride Mountain Village" and "Telluride
        # Mountain Village Gondola" both resolved to
        # telluride.com/discover/the-gondola/; Bryce Canyon's "Inspiration
        # Point" and "Sunset and Inspiration Points via Rim Trail and Bryce
        # Canyon Path" both resolved to the same AllTrails page). An exact
        # URL match is about as high-confidence a "same place" signal as
        # exists -- unlike name-similarity heuristics, it has no false-
        # positive risk from two genuinely distinct places that just share a
        # word (e.g. a third, different "Lower, Mid, and Upper Inspiration
        # Points" AllTrails page legitimately survives this pass since its
        # URL differs).
        if attractions:
            by_url: dict[str, list[dict[str, Any]]] = {}
            for attr in attractions:
                url = str(attr.get("url", "") or "").strip()
                if not url:
                    continue
                by_url.setdefault(url, []).append(attr)

            def _keep_rank(a: dict[str, Any]) -> tuple[int, int, int]:
                has_desc = 1 if str(a.get("description", "") or "").strip() else 0
                has_rating = 1 if (a.get("rating") is not None or str(a.get("raw_rating", "") or "").strip()) else 0
                name_len = len(str(a.get("name", "") or ""))
                # Prefer richer metadata, then a shorter (more likely
                # canonical) name.
                return (has_desc, has_rating, -name_len)

            to_remove_ids: set[int] = set()
            for url, group in by_url.items():
                if len(group) < 2:
                    continue

                best = max(group, key=_keep_rank)
                for attr in group:
                    if attr is best:
                        continue
                    to_remove_ids.add(id(attr))
                    logger.info(
                        "  Within-destination dedup: removing attraction '%s' "
                        "(duplicates '%s' via shared URL %s)",
                        attr.get("name", ""),
                        best.get("name", ""),
                        url,
                    )
                    self._record_registry_entity_removal(
                        dest,
                        section_target="top_attractions",
                        entity_class=(
                            "trail"
                            if self._is_trail_like_attraction(
                                str(attr.get("name", "") or ""),
                                str(attr.get("type", "") or ""),
                                str(attr.get("description", "") or ""),
                            )
                            else "attraction"
                        ),
                        display_name=str(attr.get("name", "") or ""),
                        rejection_reason="duplicate_of_attraction_same_url",
                        description=str(attr.get("description", "") or ""),
                    )

            if to_remove_ids:
                attractions = [attr for attr in attractions if id(attr) not in to_remove_ids]
                ai["top_attractions"] = attractions

        # Remove attraction cards that duplicate a retained en-route stop.
        getting_here = ai.get("getting_here", {}) if isinstance(ai.get("getting_here", {}), dict) else {}
        en_route_stops = getting_here.get("en_route_stops", []) or []
        stop_token_sets: list[frozenset[str]] = []
        for stop in en_route_stops:
            stop_name = str(stop.get("name", "") or "")
            tokens = frozenset(self._significant_tokens(stop_name))
            if len(tokens) >= 2:
                stop_token_sets.append(tokens)

        if stop_token_sets and attractions:
            kept_attractions: list[dict[str, Any]] = []
            for attr in attractions:
                attr_name = str(attr.get("name", "") or "")
                attr_tokens = frozenset(self._significant_tokens(attr_name))
                if len(attr_tokens) < 2:
                    kept_attractions.append(attr)
                    continue

                duplicate_stop = False
                for stop_tokens in stop_token_sets:
                    overlap = len(attr_tokens & stop_tokens)
                    min_len = min(len(attr_tokens), len(stop_tokens))
                    if min_len >= 2 and overlap >= 2 and overlap / min_len >= 0.67:
                        logger.info(
                            "  Within-destination dedup: removing attraction '%s' "
                            "(duplicates en-route stop in '%s')",
                            attr_name,
                            dest.get("name", ""),
                        )
                        # Recorded so the entity registry (and, transitively,
                        # schedule reconciliation) can see this removal --
                        # without this the schedule could keep referencing an
                        # attraction that silently vanished here.
                        self._record_registry_entity_removal(
                            dest,
                            section_target="top_attractions",
                            entity_class=(
                                "trail"
                                if self._is_trail_like_attraction(
                                    attr_name, str(attr.get("type", "") or ""), str(attr.get("description", "") or "")
                                )
                                else "attraction"
                            ),
                            display_name=attr_name,
                            rejection_reason="duplicate_of_en_route_stop",
                            description=str(attr.get("description", "") or ""),
                        )
                        duplicate_stop = True
                        break

                if not duplicate_stop:
                    kept_attractions.append(attr)

            ai["top_attractions"] = kept_attractions

    def _deduplicate_cross_destination_drives(self, trip: dict[str, Any]) -> None:
        """Remove scenic drives that duplicate attractions in other destinations.

        This prevents cross-destination concept duplication like "Kolob Canyons Road"
        appearing as a scenic drive in one stop while "Kolob Canyons" is a primary
        attraction in another stop.
        """
        destinations = trip.get("destinations", []) or []
        if len(destinations) < 2:
            return

        attraction_token_sets_by_dest: list[list[frozenset[str]]] = []
        for dest in destinations:
            ai = dest.get("ai_content", {}) if isinstance(dest.get("ai_content", {}), dict) else {}
            token_sets: list[frozenset[str]] = []
            for attr in ai.get("top_attractions", []) or []:
                tokens = frozenset(self._significant_tokens(str(attr.get("name", "") or "")))
                if len(tokens) >= 2:
                    token_sets.append(tokens)
            attraction_token_sets_by_dest.append(token_sets)

        for idx, dest in enumerate(destinations):
            drives = dest.get("scenic_drives", []) or []
            if not drives:
                continue
            kept: list[dict[str, Any]] = []
            for drive in drives:
                drive_tokens = frozenset(self._significant_tokens(str(drive.get("title", "") or "")))
                if len(drive_tokens) < 2:
                    kept.append(drive)
                    continue

                duplicate_elsewhere = False
                for other_idx, token_sets in enumerate(attraction_token_sets_by_dest):
                    if other_idx == idx:
                        continue
                    for attr_tokens in token_sets:
                        overlap = len(drive_tokens & attr_tokens)
                        min_len = min(len(drive_tokens), len(attr_tokens))
                        if min_len >= 2 and overlap / min_len >= 0.8:
                            logger.info(
                                "  Cross-destination dedup: removing scenic drive '%s' in '%s' "
                                "(duplicates attraction in '%s')",
                                drive.get("title", ""),
                                dest.get("name", ""),
                                destinations[other_idx].get("name", ""),
                            )
                            duplicate_elsewhere = True
                            break
                    if duplicate_elsewhere:
                        break

                if not duplicate_elsewhere:
                    kept.append(drive)

            dest["scenic_drives"] = kept

    def _deduplicate_attractions_against_en_route_stops_tripwide(self, trip: dict[str, Any]) -> None:
        """Remove attraction cards that duplicate any retained en-route stop across the trip.

        This prevents the same entity from being presented both as a destination
        attraction and as a transfer-leg stop in another destination card.
        """
        destinations = trip.get("destinations", []) or []
        if len(destinations) < 2:
            return

        stop_token_sets: list[frozenset[str]] = []
        for dest in destinations:
            ai = dest.get("ai_content", {}) if isinstance(dest.get("ai_content", {}), dict) else {}
            getting_here = ai.get("getting_here", {}) if isinstance(ai.get("getting_here", {}), dict) else {}
            stops = getting_here.get("en_route_stops", []) if isinstance(getting_here.get("en_route_stops", []), list) else []
            for stop in stops:
                if not isinstance(stop, dict):
                    continue
                stop_name = str(stop.get("name", "") or "")
                tokens = frozenset(self._significant_tokens(stop_name))
                if len(tokens) >= 2:
                    stop_token_sets.append(tokens)

        if not stop_token_sets:
            return

        for dest in destinations:
            ai = dest.get("ai_content", {}) if isinstance(dest.get("ai_content", {}), dict) else {}
            attractions = ai.get("top_attractions", []) if isinstance(ai.get("top_attractions", []), list) else []
            if not attractions:
                continue

            kept_attractions: list[dict[str, Any]] = []
            for attr in attractions:
                if not isinstance(attr, dict):
                    kept_attractions.append(attr)
                    continue
                attr_name = str(attr.get("name", "") or "")
                attr_tokens = frozenset(self._significant_tokens(attr_name))
                if len(attr_tokens) < 2:
                    kept_attractions.append(attr)
                    continue

                duplicate_stop = False
                for stop_tokens in stop_token_sets:
                    overlap = len(attr_tokens & stop_tokens)
                    min_len = min(len(attr_tokens), len(stop_tokens))
                    if min_len >= 2 and overlap >= 2 and overlap / min_len >= 0.67:
                        logger.info(
                            "  Cross-destination dedup: removing attraction '%s' in '%s' "
                            "(duplicates en-route stop elsewhere)",
                            attr_name,
                            dest.get("name", ""),
                        )
                        duplicate_stop = True
                        break

                if not duplicate_stop:
                    kept_attractions.append(attr)

            ai["top_attractions"] = kept_attractions

    @staticmethod
    def _is_route_specific_scenic_drive_url(url: str) -> bool:
        parsed = urlparse(url or "")
        path_and_query = f"{parsed.path or ''} {(parsed.query or '')}".lower()
        route_markers = (
            "scenic-drive",
            "scenic_drives",
            "scenic-drives",
            "byway",
            "route",
            "highway",
            "road",
            "drive",
        )
        return any(marker in path_and_query for marker in route_markers)

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

    @staticmethod
    def _annotate_registry_url_decision(
        item: dict[str, Any],
        *,
        rendered_url: str,
        rejection_reason: str | None = None,
    ) -> None:
        registry_meta = item.get("_registry", {}) if isinstance(item.get("_registry", {}), dict) else {}
        registry_meta["validation_status"] = "accepted"
        registry_meta["rendered_url"] = str(rendered_url or "")
        if rejection_reason:
            existing = [
                str(reason or "")
                for reason in (registry_meta.get("rejection_reasons", []) or [])
                if str(reason or "")
            ]
            if rejection_reason not in existing:
                existing.append(rejection_reason)
            registry_meta["rejection_reasons"] = existing
        item["_registry"] = registry_meta

    @staticmethod
    def _record_registry_entity_removal(
        dest: dict[str, Any],
        *,
        section_target: str,
        entity_class: str,
        display_name: str,
        rejection_reason: str,
        description: str = "",
    ) -> None:
        decisions = dest.get("_registry_decisions", []) if isinstance(dest.get("_registry_decisions", []), list) else []
        decisions.append({
            "entity_class": entity_class,
            "display_name": display_name,
            "description": description,
            "section_target": section_target,
            "validation_status": "rejected",
            "rejection_reasons": [rejection_reason],
            "rendered_url": "",
            "metadata": {"removed": True},
        })
        dest["_registry_decisions"] = decisions

    # ── Attractions ──────────────────────────────────────────────────────────

    def _discover_attractions(
        self,
        ai: dict[str, Any],
        dest_name: str,
        nps_code: str | None,
        dest_dates: str | None = None,
        seed_names: list[str] | None = None,
        dest: dict[str, Any] | None = None,
    ) -> None:
        attraction_source_mode = str(
            getattr(self, "_attraction_source", DEFAULT_ATTRACTION_SOURCE) or DEFAULT_ATTRACTION_SOURCE
        )
        top_attractions = ai.get("top_attractions", [])
        if attraction_source_mode == "direct_link_batch":
            top_attractions = self._prioritize_direct_batch_attractions(
                top_attractions,
                dest_name,
                dest_dates,
                seed_names=seed_names,
            )
            ai["top_attractions"] = top_attractions
        alltrails_source_mode = str(
            getattr(self, "_alltrails_source", DEFAULT_ALLTRAILS_SOURCE) or DEFAULT_ALLTRAILS_SOURCE
        )
        if alltrails_source_mode == "direct_link_batch":
            top_attractions = self._prioritize_direct_batch_trails(
                top_attractions,
                dest_name,
                dest_dates,
                seed_names=seed_names,
            )
            ai["top_attractions"] = top_attractions
        seed_key_set = {
            re.sub(r"[^a-z0-9]+", " ", str(seed or "").lower()).strip()
            for seed in (seed_names or [])
            if str(seed or "").strip()
        }
        for attr in list(top_attractions):
            attr_name = attr.get("name", "")
            attr_key = re.sub(r"[^a-z0-9]+", " ", str(attr_name or "").lower()).strip()
            is_seed = bool(attr_key and attr_key in seed_key_set)
            attr_type = str(attr.get("type", "attraction") or "attraction").lower()
            attr_desc = str(attr.get("description", "") or "")
            attr_context = self._attraction_trail_context(attr)
            maps_fallback_url = f"https://www.google.com/maps/search/?api=1&query={quote(self._maps_fallback_query_text(attr_name, dest_name))}"

            if self._is_uninterested_attraction(attr_name, attr_type, attr_desc, dest_dates):
                attr["url"] = ""
                self._log_decision(
                    kind="attraction",
                    dest_name=dest_name,
                    item_name=attr_name,
                    reason="interest_filter_skipped",
                    message="attraction link skipped by interest filter",
                )
                continue

            trail_like = self._is_trail_like_attraction(attr_name, attr_type, attr_context)
            trail_direct_batch_authoritative = (
                trail_like
                and str(getattr(self, "_alltrails_source", DEFAULT_ALLTRAILS_SOURCE) or DEFAULT_ALLTRAILS_SOURCE)
                == "direct_link_batch"
                and self._direct_batch_is_authoritative()
            )

            if trail_like and bool(getattr(self, "_disable_trails", False)):
                attr["url"] = ""
                attr.pop("maps_url", None)
                self._log_decision(
                    kind="attraction",
                    dest_name=dest_name,
                    item_name=attr_name,
                    reason="trail_links_disabled",
                    message="trail-like link omitted by no-trails option",
                )
                continue

            # In direct-link batch mode for non-trail attractions, treat batch
            # results as primary and avoid overriding with separate AI
            # candidate URLs before batch evaluation.
            should_consider_ai_candidate = (
                not self._direct_batch_is_authoritative()
                and (
                    (trail_like and not trail_direct_batch_authoritative)
                    or (not trail_like and attraction_source_mode != "direct_link_batch")
                )
            )
            if should_consider_ai_candidate:
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
                    self._log_decision(
                        kind="attraction",
                        dest_name=dest_name,
                        item_name=attr_name,
                        reason="ai_candidate_accepted",
                        message="attraction link (ai-candidate)",
                        url=ai_candidate_url,
                    )
                    continue

            # Trail-like items should prefer AllTrails first.
            if trail_like:
                direct_batch_url = self._search_alltrails_for_trail_from_direct_batch(attr_name, dest_name, str(dest_dates or ""))
                if direct_batch_url and self._direct_batch_is_authoritative():
                    direct_batch_url = self._prefer_canonical_alltrails_url(direct_batch_url, attr_name)
                    if (
                        self._passes_alltrails_post_search_filters(direct_batch_url, attr_name, dest_name)
                        and (
                            self._is_remembered_direct_batch_authoritative_url(direct_batch_url, attr_name)
                            or self._meets_alltrails_publish_confidence(direct_batch_url, attr_name, dest_name)
                        )
                    ):
                        attr["url"] = direct_batch_url
                        attr.update(
                            self._direct_batch_row_quality_metadata_for_url(
                                self._get_alltrails_direct_batch_rows_for_destination(dest_name, str(dest_dates or "")),
                                direct_batch_url,
                            )
                        )
                        self._log_decision(
                            kind="attraction",
                            dest_name=dest_name,
                            item_name=attr_name,
                            reason="direct_batch_accepted",
                            message="trail-like link (direct-link batch authoritative)",
                            url=direct_batch_url,
                        )
                        continue
                    self._log_decision(
                        kind="attraction",
                        dest_name=dest_name,
                        item_name=attr_name,
                        reason="direct_batch_threshold_violation",
                        message="trail-like direct-link batch candidate rejected by threshold checks",
                        url=direct_batch_url,
                    )
                url = self._search_alltrails_for_trail(attr_name, dest_name, str(dest_dates or ""))
                if (
                    url
                    and not (
                        self._direct_batch_is_authoritative()
                        and self._is_remembered_direct_batch_authoritative_url(url, attr_name)
                    )
                    and not self._meets_alltrails_publish_confidence(url, attr_name, dest_name)
                ):
                    logger.info(
                        "  trail-like link (alltrails) downgraded by confidence gate: %s -> %s",
                        attr_name,
                        url,
                    )
                    url = None
                if url:
                    attr["url"] = url
                    self._log_decision(
                        kind="attraction",
                        dest_name=dest_name,
                        item_name=attr_name,
                        reason="alltrails_accepted",
                        message="trail-like link (alltrails)",
                        url=url,
                    )
                    continue
                if is_seed:
                    relaxed_seed_url = self._search_alltrails_for_seed_relaxed(attr_name, dest_name)
                    if relaxed_seed_url:
                        attr["url"] = relaxed_seed_url
                        self._log_decision(
                            kind="attraction",
                            dest_name=dest_name,
                            item_name=attr_name,
                            reason="seed_alltrails_relaxed_accepted",
                            message="seed trail kept via relaxed alltrails fallback",
                            url=relaxed_seed_url,
                        )
                        continue
                # Trail exhausted all AllTrails paths; try NPS before a maps fallback.
                # Trails in NPS parks often have dedicated nps.gov hike pages.
                trail_nps_code = nps_code or self._infer_item_nps_code(attr_name)
                if trail_nps_code and not self._direct_batch_is_authoritative():
                    trail_fanout_result = self._search_attraction_from_item_query_fanout(
                        item_name=attr_name,
                        dest_name=dest_name,
                        nps_code=trail_nps_code,
                    )
                    if isinstance(trail_fanout_result, tuple):
                        trail_fanout_url, trail_fanout_source = trail_fanout_result
                    elif isinstance(trail_fanout_result, str) and trail_fanout_result:
                        trail_fanout_url, trail_fanout_source = trail_fanout_result, "fanout"
                    else:
                        trail_fanout_url, trail_fanout_source = None, "no_match"
                    if trail_fanout_url and not self._is_alltrails_trail_url(trail_fanout_url):
                        attr["url"] = trail_fanout_url
                        self._log_decision(
                            kind="attraction",
                            dest_name=dest_name,
                            item_name=attr_name,
                            reason=f"trail_nps_fanout_{trail_fanout_source}_accepted",
                            message="trail NPS page recovered via fanout after AllTrails no-match",
                            url=trail_fanout_url,
                        )
                        continue
                if self._direct_batch_is_authoritative():
                    attr["url"] = ""
                    attr.pop("maps_url", None)
                    self._log_decision(
                        kind="attraction",
                        dest_name=dest_name,
                        item_name=attr_name,
                        reason="direct_batch_source_locked_no_match",
                        message="trail-like link omitted; authoritative direct-link batch had no usable result",
                    )
                    continue
                attr.pop("url", None)
                q = self._maps_fallback_query_text(attr_name, dest_name)
                attr["maps_url"] = f"https://www.google.com/maps/search/?api=1&query={quote(q)}"
                attr["url"] = attr["maps_url"]
                self._log_decision(
                    kind="attraction",
                    dest_name=dest_name,
                    item_name=attr_name,
                    reason="trail_maps_fallback_assigned",
                    message="trail-like canonical link omitted; maps fallback assigned",
                    url=attr["maps_url"],
                )
                continue

            if attraction_source_mode == "direct_link_batch":
                existing_url = str(attr.get("url", "") or "").strip()
                if existing_url:
                    cleaned_existing = self._retain_discovered_url(
                        existing_url,
                        attr_name,
                        dest_name,
                        allow_alltrails=False,
                        kind="attraction",
                    )
                    if cleaned_existing:
                        if self._direct_batch_is_authoritative() and self._is_google_maps_candidate_url(cleaned_existing):
                            cleaned_existing = ""
                        if cleaned_existing:
                            attr["url"] = cleaned_existing
                            if self._is_google_maps_candidate_url(cleaned_existing):
                                attr["maps_url"] = maps_fallback_url
                            else:
                                # This shortcut (URL already attached before this
                                # loop ran, e.g. by _prioritize_direct_batch_
                                # attractions) skipped the same row-metadata
                                # merge the fresh-lookup branch below does,
                                # silently leaving rating/votes/description
                                # empty even when the matched row had them
                                # (dipstick55 Theme D: "Red Hills Desert Garden"
                                # rendered with no rating badge and no teaser
                                # despite its harvested row having both).
                                attr.update(
                                    self._direct_batch_row_quality_metadata_for_url(
                                        self._get_attraction_direct_batch_rows_for_destination(
                                            dest_name, str(dest_dates or "")
                                        ),
                                        cleaned_existing,
                                    )
                                )
                            self._log_decision(
                                kind="attraction",
                                dest_name=dest_name,
                                item_name=attr_name,
                                reason="direct_batch_existing_url_preserved",
                                message="attraction link preserved from direct-link batch row",
                                url=cleaned_existing,
                            )
                            continue
                batch_url = self._search_attraction_from_direct_batch(attr_name, dest_name, str(dest_dates or ""))
                if batch_url:
                    attr["url"] = batch_url
                    attr.update(
                        self._direct_batch_row_quality_metadata_for_url(
                            self._get_attraction_direct_batch_rows_for_destination(dest_name, str(dest_dates or "")),
                            batch_url,
                        )
                    )
                    self._log_decision(
                        kind="attraction",
                        dest_name=dest_name,
                        item_name=attr_name,
                        reason="direct_batch_accepted",
                        message="attraction link (direct-link batch)",
                        url=batch_url,
                    )
                    continue
                if self._direct_batch_is_authoritative():
                    attr["url"] = ""
                    attr.pop("maps_url", None)
                    self._log_decision(
                        kind="attraction",
                        dest_name=dest_name,
                        item_name=attr_name,
                        reason="direct_batch_source_locked_no_match",
                        message="attraction link omitted; authoritative direct-link batch had no usable result",
                    )
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
            # Category-style park activities (e.g., stargazing) often map better
            # to thematic NPS pages than exact-name queries.
            if not url and nps_code and self._is_category_style_activity(attr_name):
                url = self._search_first(
                    _build_nps_activity_query_variants(attr_name, dest_name),
                    site_filter="nps.gov",
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
            # Non-trail attractions can optionally use deterministic Google Maps
            # Things to do/place targets when web/NPS pages are unavailable.
            if not url and not trail_like:
                maps_place_url = self._search_first(
                    _build_attraction_maps_query_variants(attr_name, dest_name),
                    site_filter="google.com/maps",
                    item_name=attr_name,
                    dest_name=dest_name,
                    allow_alltrails=False,
                )
                if maps_place_url:
                    url = maps_place_url
                    attr["maps_url"] = maps_fallback_url
                    self._log_decision(
                        kind="attraction",
                        dest_name=dest_name,
                        item_name=attr_name,
                        reason="maps_place_accepted",
                        message="attraction link (maps place)",
                        url=maps_place_url,
                    )
            if url:
                attr["url"] = url
            else:
                if self._is_category_style_activity(attr_name):
                    attr["url"] = ""
                    self._log_decision(
                        kind="attraction",
                        dest_name=dest_name,
                        item_name=attr_name,
                        reason="category_activity_fail_closed",
                        message="attraction maps fallback omitted for category-style activity",
                    )
                elif self._is_ambiguous_geographic_feature_name(attr_name):
                    attr["url"] = ""
                    self._log_decision(
                        kind="attraction",
                        dest_name=dest_name,
                        item_name=attr_name,
                        reason="maps_fallback_omitted_ambiguous_geography",
                        message="attraction maps fallback omitted for ambiguous geographic feature",
                    )
                else:
                    policy_mode = str(getattr(self, "_url_policy_mode", DEFAULT_URL_POLICY_MODE) or DEFAULT_URL_POLICY_MODE)
                    blocked_classes = set(getattr(self, "_url_policy_blocked_classes", set(DEFAULT_URL_POLICY_BLOCKED_CLASSES)) or set())
                    if policy_mode == "enforce" and "google_maps_search" in blocked_classes:
                        attr["url"] = ""
                        attr.pop("maps_url", None)
                        self._log_decision(
                            kind="attraction",
                            dest_name=dest_name,
                            item_name=attr_name,
                            reason="maps_fallback_omitted_policy_enforce",
                            message="attraction maps fallback omitted by enforce policy",
                        )
                    else:
                        attr["url"] = maps_fallback_url
                        attr["maps_url"] = attr["url"]
                        self._log_decision(
                            kind="attraction",
                            dest_name=dest_name,
                            item_name=attr_name,
                            reason="maps_fallback_assigned",
                            message="attraction maps fallback assigned",
                            url=attr["url"],
                        )

            self._log_decision(
                kind="attraction",
                dest_name=dest_name,
                item_name=attr_name,
                reason="discovery_completed",
                message="attraction link",
                url=(url or ""),
            )

        # Second pass: remove closed non-seed attractions regardless of discovery path.
        for attr in list(top_attractions):
            attr_name = str(attr.get("name", "") or "")
            attr_key = re.sub(r"[^a-z0-9]+", " ", attr_name.lower()).strip()
            is_seed = bool(attr_key and attr_key in seed_key_set)
            closure_text = " ".join(
                part for part in (
                    str(attr.get("description", "") or ""),
                    str(attr.get("practical_note", "") or ""),
                ) if part
            )
            attr_url = str(attr.get("url", "") or "").strip()
            if attr_url and not any(attr_url.lower().startswith(p) for p in SAFE_FALLBACK_URL_PREFIXES):
                ok, _status, page_text = self._fetch_page_text(attr_url, timeout=8)
                if ok and page_text:
                    closure_text = f"{closure_text} {page_text}".strip()
            if self._has_attraction_closure_marker(closure_text):
                if is_seed:
                    attr["url"] = ""
                    attr.pop("maps_url", None)
                    closure_note = "Currently closed; verify status before you go."
                    existing_note = str(attr.get("practical_note", "") or "").strip()
                    if closure_note.lower() not in existing_note.lower():
                        attr["practical_note"] = f"{existing_note} {closure_note}".strip() if existing_note else closure_note
                    self._log_decision(
                        kind="attraction",
                        dest_name=dest_name,
                        item_name=attr_name,
                        reason="closure_unlinked_seed",
                        message="seeded attraction is closed; hyperlink removed",
                    )
                    continue
                top_attractions.remove(attr)
                self._record_registry_entity_removal(
                    dest=dest if isinstance(dest, dict) else {"_registry_decisions": []},
                    section_target="top_attractions",
                    entity_class="attraction",
                    display_name=attr_name,
                    description=str(attr.get("description", "") or ""),
                    rejection_reason="closure_removed",
                )
                self._log_decision(
                    kind="attraction",
                    dest_name=dest_name,
                    item_name=attr_name,
                    reason="closure_removed",
                    message="attraction page is closed and was removed",
                )

    @staticmethod
    def _infer_item_nps_code(item_name: str) -> str | None:
        """Return an NPS park code when the item name itself names a known NPS park."""
        lower = re.sub(r"\s+", " ", (item_name or "").lower()).strip()
        hints = {
            "zion": "zion",
            "bryce canyon": "brca",
            "capitol reef": "care",
            "arches": "arch",
            "canyonlands": "cany",
            "yellowstone": "yell",
            "yosemite": "yose",
            "grand canyon": "grca",
            "rocky mountain": "romo",
            "bandelier": "band",
            "mesa verde": "meve",
            "petrified forest": "pefo",
        }
        for hint, code in hints.items():
            if hint in lower:
                return code
        return None

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

    def _direct_link_batch_limit(self) -> int:
        return max(5, int(getattr(self, "_direct_link_batch_count", DEFAULT_DIRECT_LINK_BATCH_COUNT) or DEFAULT_DIRECT_LINK_BATCH_COUNT))

    def _day_scaled_direct_batch_count(self, dates: str, *, items_per_day: int, buffer_multiplier: int = 2) -> int:
        """Right-size a trail/attraction harvest request to roughly what will
        actually be kept, instead of always asking for the flat configured
        ceiling regardless of how short the stay is.

        _prioritize_direct_batch_attractions/_trails only ever keep up to
        items_per_day * day_count rows -- a fixed count of e.g. 20 for a
        2-day stay needing 6-9 items asks the model to generate more than
        double what will ever be used, directly costing completion tokens
        and generation time. buffer_multiplier covers rows that don't survive
        rating/matching/URL-resolution filtering; capped at
        _direct_link_batch_limit() so this never asks for MORE than before,
        only less for the common case of short stays.
        """
        day_count = self._infer_destination_day_count(dates)
        target = max(1, items_per_day) * max(1, day_count)
        return min(self._direct_link_batch_limit(), max(5, target * buffer_multiplier))

    def _direct_batch_is_authoritative(self) -> bool:
        return bool(getattr(self, "_direct_batch_authoritative", DEFAULT_DIRECT_BATCH_AUTHORITATIVE))

    @staticmethod
    def _normalize_direct_batch_authoritative_url(url: str | None) -> str:
        candidate = str(url or "").strip()
        if not candidate:
            return ""
        lower = candidate.lower()
        if not (lower.startswith("http://") or lower.startswith("https://")):
            return ""
        return candidate.split("#", 1)[0].strip()

    @staticmethod
    def _direct_batch_authoritative_item_key(item_name: str | None) -> frozenset[str]:
        """Token-based identity key for an item name, used to scope the
        remembered-authoritative-URL cache to the specific item it was
        validated for (see _remember_direct_batch_authoritative_url)."""
        return frozenset(URLDiscoverer._significant_tokens(str(item_name or "")))

    def _remember_direct_batch_authoritative_url(self, url: str | None, item_name: str | None = None) -> None:
        normalized = self._normalize_direct_batch_authoritative_url(url)
        if not normalized:
            return
        if not hasattr(self, "_request_cache_lock"):
            self._request_cache_lock = Lock()
        item_key = self._direct_batch_authoritative_item_key(item_name)
        with self._request_cache_lock:
            existing = getattr(self, "_direct_batch_authoritative_urls", None)
            if not isinstance(existing, dict):
                # Upgrade a legacy/externally-assigned flat set() (or missing attr)
                # in place rather than dropping whatever it already held.
                upgraded: dict[str, set[frozenset[str]]] = {}
                if isinstance(existing, set):
                    for legacy_url in existing:
                        upgraded[legacy_url] = {frozenset()}
                self._direct_batch_authoritative_urls = upgraded
            self._direct_batch_authoritative_urls.setdefault(normalized, set()).add(item_key)
        self._record_url_recommendation_source(normalized, "direct_batch")

    def _record_url_recommendation_source(self, url: str | None, source: str) -> None:
        """Minimal provenance tracking: which independent discovery mechanism(s)
        recommended a given URL for *some* item this run. Foundational
        corroboration plumbing -- deliberately cheap (no new network calls, just
        tagging URLs a source already produced) so it can be extended to more
        consumers later without cost concerns. See
        _url_recommendation_source_count for the one consumer wired in today
        (attraction tie-breaking)."""
        normalized = str(url or "").strip()
        if not normalized:
            return
        if not hasattr(self, "_url_recommendation_sources"):
            self._url_recommendation_sources = {}
        if not hasattr(self, "_request_cache_lock"):
            self._request_cache_lock = Lock()
        with self._request_cache_lock:
            self._url_recommendation_sources.setdefault(normalized, set()).add(source)

    def _url_recommendation_source_count(self, url: str | None) -> int:
        normalized = str(url or "").strip()
        if not normalized:
            return 0
        sources = getattr(self, "_url_recommendation_sources", {})
        return len(sources.get(normalized, ()))

    def _is_remembered_direct_batch_authoritative_url(self, url: str | None, item_name: str | None = None) -> bool:
        normalized = self._normalize_direct_batch_authoritative_url(url)
        if not normalized:
            return False
        remembered = getattr(self, "_direct_batch_authoritative_urls", None)
        if isinstance(remembered, set):
            # Legacy/externally-assigned flat set of URLs with no per-item context
            # (e.g. a test fixture built before this cache carried identity info).
            return normalized in remembered
        if not isinstance(remembered, dict):
            return False
        item_keys = remembered.get(normalized)
        if not item_keys:
            return False
        if item_name is None:
            # Caller only wants to know whether this URL was validated as
            # authoritative for *some* item this run (e.g. deciding whether a
            # bulk prewarm fetch is redundant) -- not attributing it to one.
            return True
        return self._direct_batch_authoritative_item_key(item_name) in item_keys

    @staticmethod
    def _batch_cache_key(destination: str, context: str = "") -> str:
        dest = str(destination or "").strip().lower()
        ctx = str(context or "").strip().lower()
        return f"{dest}||{ctx}" if ctx else dest

    @staticmethod
    def _capture_slug(value: str) -> str:
        token = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
        return token or "unknown"

    def _direct_batch_html_capture_dir(self) -> Path | None:
        if not bool(getattr(self, "_direct_batch_html_capture_enabled", DEFAULT_DIRECT_BATCH_HTML_CAPTURE_ENABLED)):
            return None
        run_output_dir = getattr(self, "_run_output_dir", None)
        if not run_output_dir:
            return None
        subdir = str(
            getattr(
                self,
                "_direct_batch_html_capture_subdir",
                DEFAULT_DIRECT_BATCH_HTML_CAPTURE_SUBDIR,
            )
            or DEFAULT_DIRECT_BATCH_HTML_CAPTURE_SUBDIR
        ).strip()
        if not subdir:
            return None
        sub_path = Path(subdir)
        if sub_path.is_absolute():
            return sub_path
        return Path(run_output_dir) / sub_path

    def _persist_direct_batch_html_capture(
        self,
        *,
        destination: str,
        dates: str,
        kind: str,
        key: str,
        system_prompt: str,
        user_prompt: str,
        query: str,
        html: str,
        rows: list[dict[str, Any]],
        provider: str = "",
    ) -> None:
        capture_dir = self._direct_batch_html_capture_dir()
        if capture_dir is None:
            return

        dest_slug = self._capture_slug(destination)
        dates_slug = self._capture_slug(dates) if str(dates or "").strip() else "no-dates"
        kind_slug = self._capture_slug(kind)
        key_slug = self._capture_slug(key)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        base_name = f"{dest_slug}.{kind_slug}.{dates_slug}.{key_slug}.{stamp}"
        html_path = capture_dir / f"{base_name}.html"
        meta_path = capture_dir / f"{base_name}.meta.json"

        html_payload = str(html or "")
        query_text = str(query or "")
        if query_text and "<div class=\"direct_batch_query\">" not in html_payload.lower():
            html_payload = (
                "<div class=\"direct_batch_query\">"
                f"<strong>Query:</strong> {html_lib.escape(query_text)}"
                "</div>\n\n"
                f"{html_payload}"
            )

        payload = {
            "captured_at_utc": stamp,
            "destination": str(destination or ""),
            "dates": str(dates or ""),
            "kind": str(kind or ""),
            "cache_key": str(key or ""),
            "system_prompt": str(system_prompt or ""),
            "user_prompt": str(user_prompt or ""),
            "query": query_text,
            "row_count": len(rows or []),
            "rows": [dict(row) for row in rows if isinstance(row, dict)],
            "html_file": html_path.name,
            # Which client actually supplied the winning html/rows -- empty
            # when neither the primary nor the fallback produced anything
            # (a class name, e.g. "GrokSearch"/"ClaudeSearch", otherwise).
            # See _fetch_direct_batch_html_rows's cross-provider retry.
            "provider": str(provider or ""),
        }

        try:
            capture_dir.mkdir(parents=True, exist_ok=True)
            html_path.write_text(html_payload, encoding="utf-8")
            meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Direct-batch HTML capture write failed for %s (%s): %s", destination, kind, exc)

    def replay_html_capture_directory(
        self,
        capture_dir: str | Path,
        output_path: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        target = Path(capture_dir)
        if not target.exists():
            return []

        entries: list[dict[str, Any]] = []
        for html_path in sorted(target.glob("*.html")):
            meta_path = html_path.with_name(f"{html_path.stem}.meta.json")
            meta: dict[str, Any] = {}
            if meta_path.exists():
                try:
                    with meta_path.open("r", encoding="utf-8") as fh:
                        meta = json.loads(fh.read() or "{}")
                except Exception:
                    meta = {}

            try:
                html_text = html_path.read_text(encoding="utf-8")
            except Exception:
                continue

            rows = self._direct_batch_rows_from_html(html_text)
            clickable_links: list[str] = []
            for row in rows:
                label = str(row.get("title") or row.get("name") or "Item").strip() or "Item"
                source_type = str(row.get("source_type") or "official").strip() or "official"
                for url in self._direct_batch_row_url_candidates(row):
                    source_label = "Source" if url == self._normalize_direct_batch_authoritative_url(str(row.get("url") or "")) else "Maps" if self._is_google_maps_candidate_url(url) else "Link"
                    clickable_links.append(
                        f'<a href="{html_lib.escape(url, quote=True)}" title="{html_lib.escape(source_type)}">'
                        f'{html_lib.escape(label)} [{html_lib.escape(source_label)} / {html_lib.escape(source_type)}]</a>'
                    )

            entries.append(
                {
                    "destination": str(meta.get("destination") or ""),
                    "dates": str(meta.get("dates") or ""),
                    "kind": str(meta.get("kind") or ""),
                    "query": str(meta.get("query") or ""),
                    "html_file": html_path.name,
                    "meta_file": meta_path.name,
                    "rows": [dict(row) for row in rows if isinstance(row, dict)],
                    "clickable_links": clickable_links,
                }
            )

        if output_path is not None:
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            report_html = ["<html><head><meta charset='utf-8'><title>URL Discovery Replay Report</title></head><body>"]
            for entry in entries:
                destination = entry.get("destination") or "Unknown destination"
                dates = entry.get("dates") or ""
                kind = entry.get("kind") or ""
                report_html.append(f"<h2>{html_lib.escape(destination)} | {html_lib.escape(kind)} | {html_lib.escape(dates)}</h2>")
                report_html.append(f"<p><strong>Query:</strong> {html_lib.escape(entry.get('query') or '')}</p>")
                for link in entry.get("clickable_links") or []:
                    report_html.append(f"<p>{link}</p>")
                if not (entry.get("clickable_links") or []):
                    report_html.append("<p>No clickable links</p>")
            report_html.append("</body></html>")
            output_file.write_text("\n".join(report_html), encoding="utf-8")

        return entries

    def _alltrails_direct_batch_query(self, dest_name: str, dates: str = "") -> str:
        max_miles = max(0.5, float(getattr(self, "_max_trail_miles", DEFAULT_MAX_TRAIL_MILES) or DEFAULT_MAX_TRAIL_MILES))
        date_clause = f" for dates {dates}" if str(dates or "").strip() else ""
        return (
            f"Generate clickable hikes from AllTrails for {dest_name}{date_clause} with trail length {max_miles:g} miles or less. "
            "Keep only likely-open routes with strong ratings and drop any item without a reliable link."
        )

    @staticmethod
    def _attraction_direct_batch_query(dest_name: str, dates: str = "") -> str:
        date_clause = f" ({dates})" if str(dates or "").strip() else ""
        return (
            "Generate lists of local points of interest, cultural landmarks, and tourist attractions "
            f"for {dest_name}{date_clause}, excluding hikes, with clickable links to source material and corresponding Google Maps content. "
            "Keep only highly rated items (>4.3), include a mixture of experiences, and keep only places likely open on the indicated dates. "
            "Include only suggestions with reliable clickable links."
        )

    @staticmethod
    def _attraction_maps_area_query(dest_name: str, dates: str = "") -> str:
        date_clause = f" ({dates})" if str(dates or "").strip() else ""
        return (
            "Find Google Maps attractions and things-to-do entries "
            f"for {dest_name}{date_clause}. "
            "Return item-specific links only and avoid generic destination landing pages."
        )

    @staticmethod
    def _restaurant_direct_batch_query(dest_name: str, dates: str = "") -> str:
        date_clause = f" ({dates})" if str(dates or "").strip() else ""
        return (
            "Generate a list of local restaurants "
            f"for {dest_name}{date_clause} with clickable links to source material and corresponding Google Maps content. "
            "Keep only highly rated items (>4.3), include cuisine variety, and keep only places likely open on the indicated dates. "
            "Include only suggestions with reliable clickable links."
        )

    def _en_route_direct_batch_query(self, dest_name: str, dates: str = "", origin_name: str = "") -> str:
        date_clause = f" ({dates})" if str(dates or "").strip() else ""
        origin = str(origin_name or "").strip()
        route_clause = (
            f"for the drive from {origin} to {dest_name}{date_clause}"
            if origin
            else f"for the drive into {dest_name}{date_clause}"
        )
        min_rating = float(getattr(self, "_place_interest_min_rating", DEFAULT_PLACE_INTEREST_MIN_RATING))
        min_votes = int(getattr(self, "_place_interest_min_votes", DEFAULT_PLACE_INTEREST_MIN_VOTES))
        return (
            "Generate a separate en-route list of scenic stopovers, viewpoints, and quick cultural detours "
            f"{route_clause}, with clickable links and matching Google Maps references. "
            "Prefer specific official or authoritative pages for each stop over generic destination landing pages, park home pages, or visitor-center pages. "
            "Only include options with detour off route of 20 minutes or less. "
            f"Only include places rated {min_rating:g}+ by at least {min_votes} reviewers. "
            "Exclude gas stations, convenience stores, welcome centers, and rest areas. "
            "Include only suggestions with reliable clickable links."
        )

    def _get_direct_batch_rows_for_destination(
        self,
        *,
        cache: dict[str, list[dict[str, Any]]],
        destination: str,
        query: str,
        cache_context: str = "",
    ) -> list[dict[str, Any]]:
        if not hasattr(self, "_request_cache_lock"):
            self._request_cache_lock = Lock()
        if not hasattr(self, "_search"):
            return []
        key = self._batch_cache_key(destination, cache_context)
        if not key:
            return []

        with self._request_cache_lock:
            cached = cache.get(key)
            if cached is not None:
                return cached

        rows = self._search_cached(query, count=self._direct_link_batch_limit())
        normalized = [dict(row) for row in rows if isinstance(row, dict)]

        with self._request_cache_lock:
            cache[key] = normalized
        return normalized

    def _get_alltrails_direct_batch_rows_for_destination(self, dest_name: str, dates: str = "") -> list[dict[str, Any]]:
        if not hasattr(self, "_alltrails_direct_batch_cache"):
            self._alltrails_direct_batch_cache = {}
        html_rows = self._get_direct_batch_html_rows_for_destination(
            cache=self._alltrails_direct_batch_cache,
            destination=dest_name,
            dates=dates,
            kind="trail",
        )
        if html_rows:
            return html_rows

        return self._get_direct_batch_rows_for_destination(
            cache=self._alltrails_direct_batch_cache,
            destination=dest_name,
            query=self._alltrails_direct_batch_query(dest_name, dates),
            cache_context=dates,
        )

    def _get_attraction_direct_batch_rows_for_destination(self, dest_name: str, dates: str = "") -> list[dict[str, Any]]:
        if not hasattr(self, "_attraction_direct_batch_cache"):
            self._attraction_direct_batch_cache = {}
        html_rows = self._get_direct_batch_html_rows_for_destination(
            cache=self._attraction_direct_batch_cache,
            destination=dest_name,
            dates=dates,
            kind="attraction",
        )
        if html_rows:
            return html_rows
        return self._get_direct_batch_rows_for_destination(
            cache=self._attraction_direct_batch_cache,
            destination=dest_name,
            query=self._attraction_direct_batch_query(dest_name, dates),
            cache_context=dates,
        )

    def _get_restaurant_direct_batch_rows_for_destination(self, dest_name: str, dates: str = "", lodging_location: str = "") -> list[dict[str, Any]]:
        if not hasattr(self, "_restaurant_direct_batch_cache"):
            self._restaurant_direct_batch_cache = {}
        html_rows = self._get_direct_batch_html_rows_for_destination(
            cache=self._restaurant_direct_batch_cache,
            destination=dest_name,
            dates=dates,
            kind="restaurant",
            lodging_location=lodging_location,
        )
        if html_rows:
            return html_rows

        return self._get_direct_batch_rows_for_destination(
            cache=self._restaurant_direct_batch_cache,
            destination=dest_name,
            query=self._restaurant_direct_batch_query(dest_name, dates),
            cache_context=f"{dates}|search",
        )

    def _get_en_route_direct_batch_rows_for_destination(
        self,
        dest_name: str,
        dates: str = "",
        origin_name: str = "",
    ) -> list[dict[str, Any]]:
        if not hasattr(self, "_en_route_direct_batch_cache"):
            self._en_route_direct_batch_cache = {}
        html_rows = self._get_direct_batch_html_rows_for_destination(
            cache=self._en_route_direct_batch_cache,
            destination=dest_name,
            dates=dates,
            kind="en_route_stop",
            origin_name=origin_name,
        )
        if html_rows:
            return html_rows

        return self._get_direct_batch_rows_for_destination(
            cache=self._en_route_direct_batch_cache,
            destination=dest_name,
            query=self._en_route_direct_batch_query(dest_name, dates, origin_name),
            cache_context=f"{dates}|search",
        )

    def _direct_batch_html_prompt(
        self,
        *,
        kind: str,
        dest_name: str,
        dates: str = "",
        origin_name: str = "",
        lodging_location: str = "",
    ) -> tuple[str, str] | None:
        date_clause = f" ({dates})" if str(dates or "").strip() else ""
        if kind == "trail":
            items_per_day = int(
                getattr(self, "_trail_direct_batch_items_per_day", DEFAULT_TRAIL_DIRECT_BATCH_ITEMS_PER_DAY)
                or DEFAULT_TRAIL_DIRECT_BATCH_ITEMS_PER_DAY
            )
            count = self._day_scaled_direct_batch_count(dates, items_per_day=items_per_day)
            max_miles = max(0.5, float(getattr(self, "_max_trail_miles", DEFAULT_MAX_TRAIL_MILES) or DEFAULT_MAX_TRAIL_MILES))
            min_rating = float(getattr(self, "_alltrails_rating_min", DEFAULT_ALLTRAILS_RATING_MIN))
            min_votes = int(getattr(self, "_alltrails_rating_min_votes", DEFAULT_ALLTRAILS_RATING_MIN_VOTES))
            system_prompt = (
                "Return HTML only. Emit one <h2> and one <ul> with exactly "
                f"{count} <li> hike items from AllTrails for the requested destination. "
                "Each <li> must begin with the trail name and include at least one AllTrails <a href=...> link; "
                "an additional official/source link is optional when available. "
                "After the links, include the trail's rating as a clear numeric value like '4.6/5' and its "
                "round-trip distance in miles like '3.2 mi', then a short descriptive note (8-15 words) about "
                "the trail's terrain or highlights, when available. "
                f"Keep only likely-open hikes of {max_miles:g} miles or less rated {min_rating:g}+ with at least {min_votes} reviews. "
                "Exclude generic listings and drop any item without a reliable trail-specific AllTrails link."
            )
            user_prompt = (
                f"Generate clickable hikes from AllTrails for {dest_name}{date_clause}. "
                "Include a rating, distance in miles, and a short descriptive note for each item when available."
            )
            return system_prompt, user_prompt

        if kind == "attraction":
            items_per_day = int(
                getattr(self, "_attraction_direct_batch_items_per_day", DEFAULT_ATTRACTION_DIRECT_BATCH_ITEMS_PER_DAY)
                or DEFAULT_ATTRACTION_DIRECT_BATCH_ITEMS_PER_DAY
            )
            count = self._day_scaled_direct_batch_count(dates, items_per_day=items_per_day)
            system_prompt = (
                "Return HTML only. Emit one <h2> and one <ul> with exactly "
                f"{count} <li> attraction items for the requested destination. "
                "Exclude hikes and trails — those are covered separately. "
                "Each <li> must begin with the attraction name and include up to two links: "
                "<a href=...>Source</a> for the attraction's official or authoritative page, "
                "and <a href=\"https://www.google.com/maps/search/?api=1&query=Attraction+Name+Address+City+State\">Maps</a> as a precise Google Maps place or search link. "
                "Use the Maps link to target a specific place, not a generic destination overview. "
                "Include the attraction's rating as a clear numeric value like '4.7/5' or '4.7 stars' after the links, "
                "then a short descriptive note (8-15 words) about what makes the attraction worth visiting, when available. "
                "Keep only highly rated items (>4.3), include a mixture of experiences, "
                "and keep only places likely open on the indicated dates. "
                "Avoid generic destination listing pages, general travel guides, and broad area pages."
            )
            user_prompt = (
                "Generate a list of local points of interest, cultural landmarks, and tourist attractions "
                f"for {dest_name}{date_clause}, excluding hikes, "
                "with clickable links to source material and corresponding Google Maps content. "
                "Prefer specific place pages and precise Google Maps links over generic listing pages. "
                "Include a rating and a short descriptive note for each item when available, using a clear numeric format for the rating. "
                "Keep only highly rated items (>4.3), include a mixture of experiences, "
                "and keep only places likely open on the indicated dates. "
                "Include only suggestions with reliable clickable links."
            )
            return system_prompt, user_prompt

        if kind == "restaurant":
            count = int(
                getattr(
                    self,
                    "_restaurant_direct_batch_item_count",
                    DEFAULT_RESTAURANT_DIRECT_BATCH_ITEM_COUNT,
                )
                or DEFAULT_RESTAURANT_DIRECT_BATCH_ITEM_COUNT
            )
            system_prompt = (
                "Return HTML only. Emit one <h2> and one <ul> with exactly "
                f"{count} <li> restaurant items for the requested destination. "
                "Each <li> must begin with the restaurant name and include up to two links: "
                "<a href=...>Source</a> for the restaurant's own website or TripAdvisor page, "
                "and <a href=\"https://www.google.com/maps/search/?api=1&query=Restaurant+Name+Address+City+State\">Maps</a> as an address-qualified Google Maps search link. "
                "Include the restaurant's rating as a clear numeric value like '4.7/5' or '4.7 stars' and include a price indicator like '$$', '$$$', or 'moderate' when available. "
                "Keep only highly rated items (>4.3), include cuisine variety, "
                "and keep only likely-open, high-confidence options. "
                "Avoid generic destination listing pages."
            )
            location_clause = lodging_location if lodging_location else dest_name
            user_prompt = (
                f"Generate a list of local restaurants near {location_clause}{date_clause} "
                "with clickable links to source material and corresponding Google Maps content. "
                "Include a rating and price indicator for each item when available, using a clear numeric or price format. "
                "Keep only highly rated items (>4.3), include cuisine variety, "
                "and keep only places likely open on the indicated dates. "
                "Include only suggestions with reliable clickable links."
            )
            return system_prompt, user_prompt

        if kind == "en_route_stop":
            count = int(
                getattr(
                    self,
                    "_en_route_direct_batch_item_count",
                    DEFAULT_EN_ROUTE_DIRECT_BATCH_ITEM_COUNT,
                )
                or DEFAULT_EN_ROUTE_DIRECT_BATCH_ITEM_COUNT
            )
            min_rating = float(getattr(self, "_place_interest_min_rating", DEFAULT_PLACE_INTEREST_MIN_RATING))
            min_votes = int(getattr(self, "_place_interest_min_votes", DEFAULT_PLACE_INTEREST_MIN_VOTES))
            origin = str(origin_name or "").strip()
            route_clause = (
                f"for the drive from {origin} to {dest_name}{date_clause}"
                if origin
                else f"for the drive into {dest_name}{date_clause}"
            )
            system_prompt = (
                "Return HTML only. Emit one <h2> and one <ul> with exactly "
                f"{count} <li> en-route stopovers for the requested destination drive. "
                "Each <li> must begin with the stop name, include a short note, and explicitly state detour distance and detour time in plain text. "
                "Use a format like 'Stop Name - quick note - detour 8 mi / 15 min'. "
                "Also include up to two links: "
                "<a href=...>Source</a> for the stop's specific official or authoritative page, not a generic destination landing page or park home page, "
                "and <a href=\"https://www.google.com/maps/search/?api=1&query=Stop+Name+Address+City+State\">Maps</a> as an address-qualified Google Maps search link. "
                "Only include options with detour off route of 20 minutes or less. "
                f"Only include places rated {min_rating:g}+ by at least {min_votes} reviewers. "
                "Exclude gas stations, convenience stores, welcome centers, and rest areas. "
                "Keep only quick, realistic, likely-open scenic/cultural stopovers with reliable links."
            )
            user_prompt = (
                f"Generate en-route stopovers {route_clause} "
                "with clickable links and matching Google Maps references. "
                "Prefer specific official or authoritative pages for each stop over generic destination landing pages, park home pages, or visitor-center pages. "
                "Only include options with detour off route of 20 minutes or less. "
                f"Only include places rated {min_rating:g}+ by at least {min_votes} reviewers. "
                "Exclude gas stations, convenience stores, welcome centers, and rest areas. "
                "Include only suggestions with reliable clickable links."
            )
            return system_prompt, user_prompt

        return None

    def _direct_batch_html_prompt_multi(
        self, *, kind: str, destinations: list[tuple[str, str]]
    ) -> tuple[str, str] | None:
        """Multi-destination variant of _direct_batch_html_prompt: one call
        asking for several destinations at once, each its own
        <h2>Destination Name</h2><ul>...</ul> section -- see
        _prefetch_grouped_direct_batch / DEFAULT_DIRECT_BATCH_GROUP_SIZE.
        Deliberately does not cover en_route_stop: that kind depends on
        per-destination origin/route context that doesn't fit this shape
        cleanly, so it stays on the original one-call-per-destination path.
        """
        if not destinations:
            return None

        if kind == "trail":
            max_miles = max(0.5, float(getattr(self, "_max_trail_miles", DEFAULT_MAX_TRAIL_MILES) or DEFAULT_MAX_TRAIL_MILES))
            min_rating = float(getattr(self, "_alltrails_rating_min", DEFAULT_ALLTRAILS_RATING_MIN))
            min_votes = int(getattr(self, "_alltrails_rating_min_votes", DEFAULT_ALLTRAILS_RATING_MIN_VOTES))
            items_per_day = int(
                getattr(self, "_trail_direct_batch_items_per_day", DEFAULT_TRAIL_DIRECT_BATCH_ITEMS_PER_DAY)
                or DEFAULT_TRAIL_DIRECT_BATCH_ITEMS_PER_DAY
            )
            dest_lines = [
                f"- {name}{f' ({dates})' if str(dates or '').strip() else ''}: exactly "
                f"{self._day_scaled_direct_batch_count(dates, items_per_day=items_per_day)} hikes"
                for name, dates in destinations
            ]
            system_prompt = (
                "Return HTML only. For EACH destination listed below, emit one <h2>Destination Name</h2> "
                "(use the exact destination name given, nothing else in the header) followed by one <ul> "
                "with the specified number of <li> hike items from AllTrails for that destination only. "
                "Do not mix items between destinations. "
                "Each <li> must begin with the trail name and include at least one AllTrails <a href=...> link; "
                "an additional official/source link is optional when available. "
                "After the links, include the trail's rating as a clear numeric value like '4.6/5' and its "
                "round-trip distance in miles like '3.2 mi', then a short descriptive note (8-15 words) about "
                "the trail's terrain or highlights, when available. "
                f"Keep only likely-open hikes of {max_miles:g} miles or less rated {min_rating:g}+ with at least {min_votes} reviews. "
                "Exclude generic listings and drop any item without a reliable trail-specific AllTrails link."
            )
            user_prompt = (
                "Generate clickable hikes from AllTrails for these destinations:\n"
                + "\n".join(dest_lines)
                + "\nInclude a rating, distance in miles, and a short descriptive note for each item when available."
            )
            return system_prompt, user_prompt

        if kind == "attraction":
            items_per_day = int(
                getattr(self, "_attraction_direct_batch_items_per_day", DEFAULT_ATTRACTION_DIRECT_BATCH_ITEMS_PER_DAY)
                or DEFAULT_ATTRACTION_DIRECT_BATCH_ITEMS_PER_DAY
            )
            dest_lines = [
                f"- {name}{f' ({dates})' if str(dates or '').strip() else ''}: exactly "
                f"{self._day_scaled_direct_batch_count(dates, items_per_day=items_per_day)} attractions"
                for name, dates in destinations
            ]
            system_prompt = (
                "Return HTML only. For EACH destination listed below, emit one <h2>Destination Name</h2> "
                "(use the exact destination name given, nothing else in the header) followed by one <ul> "
                "with the specified number of <li> attraction items for that destination only. "
                "Do not mix items between destinations. Exclude hikes and trails — those are covered separately. "
                "Each <li> must begin with the attraction name and include up to two links: "
                "<a href=...>Source</a> for the attraction's official or authoritative page, "
                "and <a href=\"https://www.google.com/maps/search/?api=1&query=Attraction+Name+Address+City+State\">Maps</a> as a precise Google Maps place or search link. "
                "Use the Maps link to target a specific place, not a generic destination overview. "
                "Include the attraction's rating as a clear numeric value like '4.7/5' or '4.7 stars' after the links, "
                "then a short descriptive note (8-15 words) about what makes the attraction worth visiting, when available. "
                "Keep only highly rated items (>4.3), include a mixture of experiences, "
                "and keep only places likely open on the indicated dates. "
                "Avoid generic destination listing pages, general travel guides, and broad area pages."
            )
            user_prompt = (
                "Generate local points of interest, cultural landmarks, and tourist attractions "
                "for these destinations, excluding hikes:\n"
                + "\n".join(dest_lines)
                + "\nInclude clickable links to source material and corresponding Google Maps content, "
                "a rating for each item when available using a clear numeric format, "
                "and a short descriptive note for each item when available. "
                "Keep only highly rated items (>4.3), include a mixture of experiences, "
                "and keep only places likely open on the indicated dates. "
                "Include only suggestions with reliable clickable links."
            )
            return system_prompt, user_prompt

        if kind == "restaurant":
            count = int(
                getattr(self, "_restaurant_direct_batch_item_count", DEFAULT_RESTAURANT_DIRECT_BATCH_ITEM_COUNT)
                or DEFAULT_RESTAURANT_DIRECT_BATCH_ITEM_COUNT
            )
            dest_lines = [
                f"- {name}{f' ({dates})' if str(dates or '').strip() else ''}"
                for name, dates in destinations
            ]
            system_prompt = (
                "Return HTML only. For EACH destination listed below, emit one <h2>Destination Name</h2> "
                "(use the exact destination name given, nothing else in the header) followed by one <ul> "
                f"with exactly {count} <li> restaurant items for that destination only. "
                "Do not mix items between destinations. "
                "Each <li> must begin with the restaurant name and include up to two links: "
                "<a href=...>Source</a> for the restaurant's own website or TripAdvisor page, "
                "and <a href=\"https://www.google.com/maps/search/?api=1&query=Restaurant+Name+Address+City+State\">Maps</a> as an address-qualified Google Maps search link. "
                "Include the restaurant's rating as a clear numeric value like '4.7/5' or '4.7 stars' and include a price indicator like '$$', '$$$', or 'moderate' when available. "
                "Keep only highly rated items (>4.3), include cuisine variety, "
                "and keep only likely-open, high-confidence options. "
                "Avoid generic destination listing pages."
            )
            user_prompt = (
                "Generate local restaurants near these destinations:\n"
                + "\n".join(dest_lines)
                + "\nInclude clickable links to source material and corresponding Google Maps content. "
                "Include a rating and price indicator for each item when available, using a clear numeric or price format. "
                "Keep only highly rated items (>4.3), include cuisine variety, "
                "and keep only places likely open on the indicated dates. "
                "Include only suggestions with reliable clickable links."
            )
            return system_prompt, user_prompt

        return None

    @staticmethod
    def _split_multi_destination_html(html_text: str) -> list[tuple[str, str]]:
        """Split a multi-destination direct-batch HTML response into
        (destination_header_text, section_html) pairs, one per <h2> section
        -- so each section can be fed independently through the existing
        single-destination _direct_batch_rows_from_html parser."""
        text = str(html_text or "")
        parts = re.split(r"<h2[^>]*>(.*?)</h2>", text, flags=re.IGNORECASE | re.DOTALL)
        sections: list[tuple[str, str]] = []
        for i in range(1, len(parts), 2):
            header = re.sub(r"<[^>]+>", "", parts[i]).strip()
            body = parts[i + 1] if i + 1 < len(parts) else ""
            if header:
                sections.append((header, body))
        return sections

    @staticmethod
    def _match_destination_section(dest_name: str, sections: list[tuple[str, str]]) -> int | None:
        """Match a destination name to its <h2> section, tolerating minor
        formatting drift from the model (exact match first, then either
        string containing the other)."""
        target = str(dest_name or "").strip().lower()
        if not target:
            return None
        for i, (header, _body) in enumerate(sections):
            if header.strip().lower() == target:
                return i
        for i, (header, _body) in enumerate(sections):
            h = header.strip().lower()
            if h and (target in h or h in target):
                return i
        return None

    def _group_already_cached(self, kind: str, group: list[dict]) -> bool:
        """True only if EVERY destination in group already has a real
        (non-empty) cached direct-batch result for this kind.

        Without this guard, _prefetch_grouped_direct_batch fires a fresh
        grouped call for every group/kind combination on every invocation --
        harmless if it only ever runs once per URLDiscoverer instance, but
        a real, expensive bug if discover_all runs more than once against
        the same destinations in one process (e.g. main.py's selective
        retry pass re-invoking URL discovery). Found 2026-08-15: a real run
        (dipstick55) captured every single (destination, kind) combo TWICE,
        ~1 hour apart, both from the grouped path -- a full duplicate pass
        that roughly doubled url_discovery's real Grok spend for zero
        benefit, since the second pass's results were near-identical to the
        first's. The existing single-destination getters already skip
        re-fetching a cache hit; this brings the grouped path in line with
        that same behavior instead of bypassing it.
        """
        cache_attr = {
            "attraction": "_attraction_direct_batch_cache",
            "restaurant": "_restaurant_direct_batch_cache",
            "trail": "_alltrails_direct_batch_cache",
        }.get(kind)
        if cache_attr is None:
            return False
        cache = getattr(self, cache_attr, None)
        if not cache:
            return False
        for dest in group:
            dest_name = str(dest.get("name", "") or "").strip()
            dates = str(dest.get("dates", "") or "")
            if not dest_name:
                return False
            key = self._batch_cache_key(dest_name, f"{dates}|html|{kind}")
            cached = cache.get(key)
            if not cached:
                return False
        return True

    def _prefetch_grouped_direct_batch(self, destinations: list[dict]) -> None:
        """Pre-fetch attraction/restaurant/trail direct-batch rows for
        groups of destinations in a single call each, populating the same
        per-kind caches the existing single-destination getters
        (_get_attraction_direct_batch_rows_for_destination etc.) check
        first -- so every existing call site transparently gets a cache hit
        instead of re-fetching, with zero changes needed at those call
        sites. A destination whose group call fails, times out, or gets
        dropped during splitting/matching simply isn't written into the
        cache, so it falls through to the normal single-destination path
        exactly as if grouping didn't exist -- this is purely additive.

        Controlled by self._direct_batch_group_size
        (DEFAULT_DIRECT_BATCH_GROUP_SIZE / url_discovery.direct_batch_group_size
        / DIRECT_BATCH_GROUP_SIZE env var). <=1 disables this entirely.
        """
        group_size = max(1, int(getattr(self, "_direct_batch_group_size", DEFAULT_DIRECT_BATCH_GROUP_SIZE) or 1))
        if group_size <= 1:
            return

        valid_destinations = [
            d for d in destinations if isinstance(d, dict) and str(d.get("name", "") or "").strip()
        ]
        if len(valid_destinations) < 2:
            return

        groups = [
            valid_destinations[i : i + group_size] for i in range(0, len(valid_destinations), group_size)
        ]
        jobs = [
            (kind, group)
            for kind in ("attraction", "restaurant", "trail")
            for group in groups
            if len(group) >= 2 and not self._group_already_cached(kind, group)
        ]
        if not jobs:
            return

        def _run_one(kind: str, group: list[dict]) -> None:
            try:
                self._fetch_and_cache_grouped_direct_batch(kind=kind, group=group)
            except Exception:
                logger.warning(
                    "Grouped direct-batch prefetch failed for kind=%s (%d destinations); "
                    "falling back to per-destination calls for this group",
                    kind,
                    len(group),
                    exc_info=True,
                )

        with ThreadPoolExecutor(max_workers=min(len(jobs), 8)) as pool:
            futs = [pool.submit(_run_one, kind, group) for kind, group in jobs]
            for f in as_completed(futs):
                f.result()

    def _fetch_and_cache_grouped_direct_batch(self, *, kind: str, group: list[dict]) -> None:
        pairs = [(str(d.get("name", "") or ""), str(d.get("dates", "") or "")) for d in group]
        prompt_pair = self._direct_batch_html_prompt_multi(kind=kind, destinations=pairs)
        if prompt_pair is None:
            return
        system_prompt, user_prompt = prompt_pair

        search_client = getattr(self, "_search", None)
        if search_client is None:
            return
        if hasattr(search_client, "is_circuit_open") and search_client.is_circuit_open():
            return

        group_dest_names = [str(d.get("name", "") or "").strip() for d in group]
        html = str(
            search_client.chat_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.1,
                response_format=None,
                live_search=True,
            )
            or ""
        )
        if not html:
            logger.info(
                "Grouped direct-batch harvest for kind=%s destinations=%s returned an empty "
                "response; falling back to per-destination calls for this group.",
                kind,
                group_dest_names,
            )
            return

        sections = self._split_multi_destination_html(html)
        if not sections:
            # Diagnostic for the split-matching failure mode: the model
            # returned content but _split_multi_destination_html found zero
            # <h2>...</h2> sections in it -- either the model didn't use the
            # requested <h2>Destination Name</h2> heading format at all (e.g.
            # markdown "## Name" or bold text instead of an <h2> tag), or the
            # whole response is something else entirely (an apology, a
            # truncated fragment, etc). Logging a head/tail snippet here
            # (rather than the full body) keeps this readable while still
            # showing the actual heading style the model chose, which is
            # exactly what's needed to tell those cases apart.
            snippet = html[:300].replace("\n", " ")
            logger.info(
                "Grouped direct-batch harvest for kind=%s destinations=%s produced no <h2> "
                "sections (html_length=%d); falling back to per-destination calls for this "
                "group. Response head: %r",
                kind,
                group_dest_names,
                len(html),
                snippet,
            )
            return

        cache_attr = {
            "attraction": "_attraction_direct_batch_cache",
            "restaurant": "_restaurant_direct_batch_cache",
            "trail": "_alltrails_direct_batch_cache",
        }.get(kind)
        if cache_attr is None:
            return
        if not hasattr(self, cache_attr):
            setattr(self, cache_attr, {})
        if not hasattr(self, "_request_cache_lock"):
            self._request_cache_lock = Lock()
        if not hasattr(self, "_direct_batch_html_failure_ts"):
            self._direct_batch_html_failure_ts = {}
        cache = getattr(self, cache_attr)

        min_required = self._direct_batch_min_required(kind)
        remaining = list(sections)
        for dest in group:
            dest_name = str(dest.get("name", "") or "").strip()
            dates = str(dest.get("dates", "") or "")
            if not dest_name:
                continue
            match_idx = self._match_destination_section(dest_name, remaining)
            if match_idx is None:
                # Diagnostic: show what headers WERE found in this response
                # so a heading-format mismatch (e.g. the model dropping the
                # state/park suffix, or emitting a section per subcategory
                # instead of per destination) is visible without re-running
                # anything.
                logger.info(
                    "Grouped direct-batch harvest for kind=%s: no <h2> section matched "
                    "destination '%s' (group=%s). Headers found in response: %s",
                    kind,
                    dest_name,
                    group_dest_names,
                    [header for header, _body in remaining],
                )
                continue
            header, body = remaining.pop(match_idx)
            rows = self._direct_batch_rows_from_html(body)
            filtered_rows = [dict(row) for row in rows if isinstance(row, dict)]
            key = self._batch_cache_key(dest_name, f"{dates}|html|{kind}")
            if not key:
                continue
            self._persist_direct_batch_html_capture(
                destination=dest_name,
                dates=dates,
                kind=kind,
                key=key,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                query="",
                html=body,
                rows=filtered_rows,
                provider=f"{search_client.__class__.__name__}(grouped x{len(group)})",
            )
            if len(filtered_rows) < min_required:
                # Below the same bar _fetch_direct_batch_html_rows enforces --
                # leave this destination's cache unpopulated so it falls
                # through to a normal, individually-retried single-destination
                # fetch rather than locking in a too-thin result.
                logger.info(
                    "Grouped direct-batch harvest for kind=%s: matched section for '%s' "
                    "(group=%s) but only parsed %d row(s), below min_required=%d; falling "
                    "back to a per-destination call for this destination.",
                    kind,
                    dest_name,
                    group_dest_names,
                    len(filtered_rows),
                    min_required,
                )
                continue
            with self._request_cache_lock:
                cache[key] = filtered_rows
                self._direct_batch_html_failure_ts.pop(key, None)
                self._mark_persistent_cache_dirty()

    def _direct_batch_html_cache_hit_or_recent_failure(
        self, cache: dict[str, list[dict[str, Any]]], key: str
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Returns (should_short_circuit, rows). rows is only meaningful when
        should_short_circuit is True (a real cache hit); an empty list with
        should_short_circuit=True means "recent failure, return [] without
        touching the network." Caller must hold _request_cache_lock."""
        cached = cache.get(key)
        if cached is not None and len(cached) > 0:
            return True, [dict(row) for row in cached if isinstance(row, dict)]
        failed_at = self._direct_batch_html_failure_ts.get(key)
        cooldown = float(
            getattr(
                self,
                "_direct_batch_html_failure_cooldown_seconds",
                DEFAULT_DIRECT_BATCH_HTML_FAILURE_COOLDOWN_SECONDS,
            )
        )
        if failed_at is not None and (time.monotonic() - failed_at) < cooldown:
            return True, []
        return False, []

    def _get_direct_batch_html_rows_for_destination(
        self,
        *,
        cache: dict[str, list[dict[str, Any]]],
        destination: str,
        dates: str,
        kind: str,
        origin_name: str = "",
        lodging_location: str = "",
    ) -> list[dict[str, Any]]:
        if not hasattr(self, "_request_cache_lock"):
            self._request_cache_lock = Lock()
        if not hasattr(self, "_direct_batch_html_key_locks"):
            self._direct_batch_html_key_locks = {}
        if not hasattr(self, "_direct_batch_html_failure_ts"):
            self._direct_batch_html_failure_ts = {}
        key = self._batch_cache_key(destination, f"{dates}|html|{kind}")
        if not key:
            return []

        with self._request_cache_lock:
            hit, rows_or_empty = self._direct_batch_html_cache_hit_or_recent_failure(cache, key)
            if hit:
                return rows_or_empty
            key_lock = self._direct_batch_html_key_locks.setdefault(key, Lock())

        # Coalesce concurrent callers for the same (destination, kind, dates):
        # only the thread that wins this lock actually hits the network: every
        # other caller waiting on it re-checks the cache/cooldown state below
        # and reuses that outcome instead of independently re-triggering the
        # same expensive multi-attempt harvest call.
        with key_lock:
            with self._request_cache_lock:
                hit, rows_or_empty = self._direct_batch_html_cache_hit_or_recent_failure(cache, key)
                if hit:
                    return rows_or_empty

            filtered_rows = self._fetch_direct_batch_html_rows(
                key=key,
                destination=destination,
                dates=dates,
                kind=kind,
                origin_name=origin_name,
                lodging_location=lodging_location,
            )
            with self._request_cache_lock:
                if filtered_rows:
                    cache[key] = filtered_rows
                    self._direct_batch_html_failure_ts.pop(key, None)
                    # Found 2026-08-15: this write updates the in-memory cache
                    # but, without this call, never marks the persistent
                    # cache dirty -- _save_persistent_caches' harvest-cache
                    # section (see _load_persistent_caches' matching read
                    # side, already wired up and enabled by default) never
                    # actually had anything to save, so a genuinely
                    # successful harvest was silently lost the moment the
                    # process exited, for both the single-destination path
                    # here and the grouped path (_fetch_and_cache_grouped_direct_batch).
                    # Real cost: a same-run retry pass constructing a fresh
                    # URLDiscoverer had no persisted cache to load from and
                    # had to refetch everything from scratch (dipstick55:
                    # every direct-batch call repeated in full, ~doubling
                    # that run's real Grok spend for no benefit).
                    self._mark_persistent_cache_dirty()
                else:
                    if key in cache:
                        # Empty direct-batch HTML captures are not authoritative
                        # and can poison later retries for a destination; allow a
                        # new fetch attempt once the failure cooldown expires
                        # rather than freezing the result set at [] forever.
                        del cache[key]
                    self._direct_batch_html_failure_ts[key] = time.monotonic()
            return filtered_rows

    def _fetch_direct_batch_html_rows(
        self,
        *,
        key: str,
        destination: str,
        dates: str,
        kind: str,
        origin_name: str = "",
        lodging_location: str = "",
    ) -> list[dict[str, Any]]:
        prompt_pair = self._direct_batch_html_prompt(
            kind=kind,
            dest_name=destination,
            dates=dates,
            origin_name=origin_name,
            lodging_location=lodging_location,
        )
        if prompt_pair is None:
            return []
        system_prompt, user_prompt = prompt_pair

        html = ""
        provider_used = ""
        primary = getattr(self, "_search", None)
        if primary is not None:
            html = str(
                primary.chat_completion(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.1,
                    response_format=None,
                    # Real search (2026-08-14 fix): the old live_search
                    # mechanism this flag used to gate was silently
                    # deprecated by xAI -- every harvest call in production
                    # was running on the model's training-data memory, not
                    # live search, regardless of this flag's value. Probe
                    # evidence: with search genuinely enabled, 21/21 embedded
                    # URLs matched Grok's own citations across 4 real test
                    # cases (582 citations); the previous behavior produced
                    # zero citations and no way to verify provenance at all.
                    live_search=True,
                )
                or ""
            )
            if html:
                provider_used = primary.__class__.__name__

        rows = self._direct_batch_rows_from_html(html)
        min_required = self._direct_batch_min_required(kind)
        circuit_open = bool(
            primary is not None
            and hasattr(primary, "is_circuit_open")
            and primary.is_circuit_open()
        )
        if (
            primary is not None
            and min_required > 0
            and len(rows) < min_required
            and not circuit_open
        ):
            # Firing a second expensive harvest call is the worst possible
            # moment to do it while the circuit breaker is open -- that state
            # means a recent burst of transient errors, and this retry-prompt
            # would just compound it. Accept the short first-pass batch
            # instead and let a later cache-refresh pick up more rows once
            # the provider recovers.
            retry_prompt = (
                f"{user_prompt} Return at least {min_required} valid, distinct <li> items for {destination}. "
                "Do not use generic listing pages, placeholders, or duplicate entity names."
            )
            retry_html = str(
                primary.chat_completion(
                    system_prompt=system_prompt,
                    user_prompt=retry_prompt,
                    temperature=0.1,
                    response_format=None,
                    # Real search (2026-08-14 fix): the old live_search
                    # mechanism this flag used to gate was silently
                    # deprecated by xAI -- every harvest call in production
                    # was running on the model's training-data memory, not
                    # live search, regardless of this flag's value. Probe
                    # evidence: with search genuinely enabled, 21/21 embedded
                    # URLs matched Grok's own citations across 4 real test
                    # cases (582 citations); the previous behavior produced
                    # zero citations and no way to verify provenance at all.
                    live_search=True,
                )
                or ""
            )
            retry_rows = self._direct_batch_rows_from_html(retry_html)
            if len(retry_rows) > len(rows):
                html = retry_html
                rows = retry_rows
                provider_used = primary.__class__.__name__

        # Cross-provider batch retry (2026-08-15 finding): when the primary's
        # batch harvest still hasn't produced enough rows -- including
        # entirely empty, e.g. during a primary-provider outage -- retry the
        # SAME purpose-built batch list prompt through the fallback client
        # before ever dropping to the narrower single-query mode
        # (_get_direct_batch_rows_for_destination). A live run found the
        # fallback's single generic .search() query structurally can't match
        # every specific named item a batch prompt covers (it wasn't built
        # to -- it predates this class having its own working batch
        # capability at all), so items were rendering with no URL despite
        # the fallback client itself being perfectly healthy. Retrying with
        # the fallback's own chat_completion(live_search=True) gives it a
        # fair shot at the same item-specific coverage the primary gets,
        # since both providers share that same batch-capable interface.
        # One attempt only (no retry-prompt escalation on the fallback) --
        # this is already a second full harvest call; a third would be
        # excessive under conditions that are already degraded.
        fallback = getattr(self, "_search_fallback", None)
        if (
            fallback is not None
            and fallback is not primary
            and min_required > 0
            and len(rows) < min_required
            and not (hasattr(fallback, "is_circuit_open") and fallback.is_circuit_open())
        ):
            fallback_html = str(
                fallback.chat_completion(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.1,
                    response_format=None,
                    live_search=True,
                )
                or ""
            )
            fallback_rows = self._direct_batch_rows_from_html(fallback_html)
            if len(fallback_rows) > len(rows):
                html = fallback_html
                rows = fallback_rows
                provider_used = fallback.__class__.__name__

        query_text = self._direct_batch_html_prompt(
            kind=kind,
            dest_name=destination,
            dates=dates,
            origin_name=origin_name,
            lodging_location=lodging_location,
        )
        if query_text is not None:
            _system_prompt, _user_prompt = query_text
            effective_query = _user_prompt
        else:
            effective_query = ""

        filtered_rows = [dict(row) for row in rows if isinstance(row, dict)]
        self._persist_direct_batch_html_capture(
            destination=destination,
            dates=dates,
            kind=kind,
            key=key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            query=effective_query,
            html=html,
            rows=filtered_rows,
            provider=provider_used,
        )
        return filtered_rows

    def _direct_batch_min_required(self, kind: str) -> int:
        if kind in {"trail", "attraction"}:
            return 1
        if kind == "restaurant":
            return max(1, int(getattr(self, "_restaurant_direct_batch_min_results", DEFAULT_RESTAURANT_DIRECT_BATCH_MIN_RESULTS) or DEFAULT_RESTAURANT_DIRECT_BATCH_MIN_RESULTS))
        if kind == "en_route_stop":
            return max(1, int(getattr(self, "_en_route_direct_batch_min_results", DEFAULT_EN_ROUTE_DIRECT_BATCH_MIN_RESULTS) or DEFAULT_EN_ROUTE_DIRECT_BATCH_MIN_RESULTS))
        return 0

    @staticmethod
    def _sanitize_direct_batch_description_text(text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        cleaned = re.sub(r"https?://\S+", "", cleaned)
        cleaned = re.sub(r"\bLinks?\s*:.*$", "", cleaned, flags=re.IGNORECASE)
        # "Source"/"Maps"/"AllTrails" are anchor-text artifacts from the harvest
        # format (<a>Source</a> <a>Maps</a> or <a>AllTrails</a>), not organic
        # description content. They can appear anywhere in the text -- not just
        # trailing -- when rating/price/cuisine follow the links (e.g.
        # "Name - Source Maps 4.6/5 $$ Cuisine").
        cleaned = re.sub(r"\b(?:Source|Maps?|AllTrails)\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+[|/]+\s+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:|,;")
        return cleaned

    @staticmethod
    def _is_metadata_only_residual_text(text: str, name: str = "") -> bool:
        """True when text carries only the item's own name plus rating/price/
        cuisine tokens and no real prose -- i.e. nothing worth surfacing as a
        description/teaser. Some harvested rows have no separator between the
        name and its trailing metadata, so the name itself must be discounted
        before judging whether anything substantive remains."""
        cleaned = str(text or "").strip()
        if not cleaned:
            return True
        name_clean = str(name or "").strip()
        if name_clean:
            cleaned = re.sub(re.escape(name_clean), "", cleaned, count=1, flags=re.IGNORECASE).strip()
            if not cleaned:
                return True
        residual = re.sub(r"\d+(?:\.\d+)?\s*(?:/\s*5|out\s+of\s+5|stars?)", "", cleaned, flags=re.IGNORECASE)
        residual = re.sub(r"[\$#]{1,4}", "", residual)
        # Compound cuisine labels ("Thai/Japanese", "Mexican-American") are still
        # metadata, not prose -- collapse slash/hyphen/ampersand-joined alpha runs
        # to a placeholder that doesn't count as a word before counting.
        residual = re.sub(r"\b[A-Za-z]+(?:\s*[/\-&]\s*[A-Za-z]+)+\b", "x", residual)
        residual_words = re.findall(r"[A-Za-z]{3,}", residual)
        return len(residual_words) <= 1

    @staticmethod
    def _infer_direct_batch_quality_metadata(text: str, url: str) -> dict[str, Any]:
        blob = str(text or "")
        lower_url = (url or "").lower()
        out: dict[str, Any] = {}

        rating_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:/\s*5|out\s+of\s+5|stars?)", blob, flags=re.IGNORECASE)
        if rating_match:
            out["rating"] = float(rating_match.group(1))
            out["raw_rating"] = str(rating_match.group(0)).strip()
        else:
            rating_float = None
            for pattern in (r"\b(4\.[0-9]|5\.0)\b", r"\b(4\.[0-9]|5\.0)\s*/\s*5\b"):
                match = re.search(pattern, blob, flags=re.IGNORECASE)
                if match:
                    rating_float = float(match.group(1))
                    out["rating"] = rating_float
                    out["raw_rating"] = str(match.group(0)).strip()
                    break

        votes_match = re.search(r"(\d{1,3}(?:,\d{3})*|\d+)\s*(?:reviews?|ratings?|votes?)", blob, flags=re.IGNORECASE)
        if votes_match:
            out["votes"] = int(votes_match.group(1).replace(",", ""))

        if "tripadvisor" in lower_url:
            out["source_type"] = "tripadvisor"
        elif "yelp" in lower_url:
            out["source_type"] = "yelp"
        elif "google.com/maps" in lower_url:
            out["source_type"] = "google_maps"
        else:
            out["source_type"] = "official"

        return out

    @staticmethod
    def _infer_restaurant_metadata_from_text_and_url(text: str, url: str) -> dict[str, Any]:
        blob = str(text or "")
        lowered = blob.lower()
        out: dict[str, Any] = {}

        price_match = re.search(r"(?:^|\s)(\${1,4})(?:\s|$)", blob)
        if price_match:
            out["price_range"] = price_match.group(1)

        cuisine_keywords = {
            "mexican": "Mexican",
            "italian": "Italian",
            "american": "American",
            "bbq": "BBQ",
            "barbecue": "BBQ",
            "steakhouse": "Steakhouse",
            "thai": "Thai",
            "indian": "Indian",
            "japanese": "Japanese",
            "sushi": "Japanese",
            "chinese": "Chinese",
            "mediterranean": "Mediterranean",
            "greek": "Greek",
            "french": "French",
            "seafood": "Seafood",
            "vietnamese": "Vietnamese",
            "korean": "Korean",
            "pizza": "Pizza",
            "burger": "American",
        }
        for key, label in cuisine_keywords.items():
            if re.search(rf"\b{re.escape(key)}\b", lowered):
                out["cuisine"] = label
                break

        reserve_signal = bool(re.search(r"\b(reservations?|book ahead|popular|waitlist)\b", lowered))
        if reserve_signal:
            out["reserve_recommended"] = True

        # Last resort for maps links: parse query text and attempt the same
        # cuisine extraction when list text is sparse.
        if "cuisine" not in out:
            parsed = urlparse(str(url or "").strip())
            query = parse_qs(parsed.query or "")
            q_text = " ".join(
                v for key in ("query", "q", "name") for v in query.get(key, [])
            ).lower()
            for key, label in cuisine_keywords.items():
                if re.search(rf"\b{re.escape(key)}\b", q_text):
                    out["cuisine"] = label
                    break

        return out

    @classmethod
    def _direct_batch_rows_from_html(cls, html_text: str) -> list[dict[str, Any]]:
        parser = _DirectBatchHTMLListParser()
        try:
            parser.feed(str(html_text or ""))
        except Exception:
            return []

        rows: list[dict[str, Any]] = []
        for record in parser.records:
            raw_text = str(record.get("raw_text", "") or "").strip()
            # Strip anchor-label artifacts (Source/Maps/AllTrails) from anywhere
            # in the text, not just the tail end. A trailing-only strip was
            # sufficient while no prompt put real content after these labels,
            # but once a kind's format sandwiches the links between the name
            # and a rating/dash-separated note (e.g. "Name <a>Source</a>
            # <a>Maps</a> 4.8/5 - short note", used by the attraction/trail
            # prompts once they started asking for a descriptive note), a
            # trailing-only strip left "Source"/"Maps" glued into the name
            # extracted below.
            semantic_text = re.sub(r"\b(?:Source|Maps?|AllTrails)\b", "", raw_text, flags=re.IGNORECASE)
            semantic_text = re.sub(r"\s{2,}", " ", semantic_text).strip(" -:|,;")
            name = str(record.get("name", "") or semantic_text).strip()
            # Strip cuisine or type annotations Grok appends in parentheses, e.g. "Cafe X (Mexican)"
            name = re.sub(r"\s*\([^)]{1,40}\)\s*$", "", name).strip()
            urls = [
                cls._normalize_direct_batch_authoritative_url(url)
                for url in list(record.get("urls", []))
                if cls._normalize_direct_batch_authoritative_url(url)
            ]
            if not name and not urls:
                continue

            maps_urls = [url for url in urls if cls._is_google_maps_candidate_url(url)]
            non_maps_urls = [url for url in urls if not cls._is_google_maps_candidate_url(url)]
            # Prefer a direct Source link; keep Maps URL as fallback metadata only.
            preferred = non_maps_urls[0] if non_maps_urls else (maps_urls[0] if maps_urls else "")
            maps_fallback = maps_urls[0] if maps_urls else ""
            display_name = name
            detail_text = semantic_text
            parts = [part.strip() for part in re.split(r"\s+[\-\u2013\u2014]\s+", semantic_text) if part.strip()]
            if len(parts) > 1:
                display_name = parts[0]
                detail_text = " - ".join(parts[1:])
            # A rating glued directly onto the name with no dash separator
            # (e.g. "Sunset Point 4.8/5 - Iconic canyon overlook...", where the
            # dash only separates the rating from the trailing note) is never
            # part of the real name -- truncate there rather than trust the
            # dash-split alone to have found the true boundary.
            rating_leak = re.search(
                r"\d+(?:\.\d+)?\s*(?:/\s*5\b|out\s+of\s+5\b|stars?\b)", display_name, flags=re.IGNORECASE
            )
            if rating_leak and rating_leak.start() > 0:
                display_name = display_name[: rating_leak.start()].strip(" -:|,;")
            # Real Grok output for a "rating, then a short note" instruction
            # doesn't reliably use a dash separator (observed: "Name 4.4/5
            # Interactive exhibits and award-winning film..." with just a
            # space) -- the dash-split above then finds nothing and
            # detail_text is left as the whole name+rating+note blob. Locate
            # the actual boundary directly: name, then an optional rating,
            # then an optional trailing distance-in-miles (trail rows), and
            # treat everything remaining after whichever of those was found
            # last as the real detail/note text. This also correctly handles
            # the dash-separated shape (the separator is just leading
            # punctuation stripped off the front) and the has-no-rating
            # "Name - detail" shape (cursor stays right after the name).
            cursor = 0
            name_match = re.search(re.escape(display_name), semantic_text, flags=re.IGNORECASE) if display_name else None
            if name_match:
                cursor = name_match.end()
            trailing_rating_match = re.search(
                r"\d+(?:\.\d+)?\s*(?:/\s*5\b|out\s+of\s+5\b|stars?\b)", semantic_text[cursor:], flags=re.IGNORECASE
            )
            if trailing_rating_match:
                cursor += trailing_rating_match.end()
                distance_match = re.match(r"\s*\d+(?:\.\d+)?\s*mi\b\.?", semantic_text[cursor:], flags=re.IGNORECASE)
                if distance_match:
                    cursor += distance_match.end()
            cursor_detail_text = semantic_text[cursor:].strip(" -:|,;")
            if cursor_detail_text and len(cursor_detail_text) < len(detail_text):
                detail_text = cursor_detail_text
            snippet_parts = [raw_text or display_name] if (raw_text or display_name) else []
            if urls:
                snippet_parts.append("Links: " + " ".join(urls))
            description_text = str(detail_text or semantic_text or display_name or "").strip()
            practical_note = ""
            detour_miles = cls._extract_en_route_detour_miles_from_text(description_text)
            detour_minutes = cls._extract_en_route_detour_minutes_from_text(description_text)
            description_text = cls._sanitize_direct_batch_description_text(description_text)
            if detour_miles is not None:
                description_text = re.sub(r"\bdetour\b[^,.;&]*\b(?:mile|miles|mi)\b[^,.;&]*", "", description_text, flags=re.IGNORECASE).strip(" -:|,")
            if detour_minutes is not None:
                description_text = re.sub(r"\bdetour\b[^,.;&]*\b(?:minute|minutes|min)\b[^,.;&]*", "", description_text, flags=re.IGNORECASE).strip(" -:|,")
            if cls._is_metadata_only_residual_text(description_text, name=name):
                description_text = ""
            if description_text and description_text.lower() != name.lower():
                practical_note = description_text
            quality_meta = cls._infer_direct_batch_quality_metadata(
                " ".join(part for part in [semantic_text, description_text] if part),
                preferred,
            )
            restaurant_meta = cls._infer_restaurant_metadata_from_text_and_url(
                " ".join(part for part in [semantic_text, description_text] if part),
                preferred,
            )
            rows.append(
                {
                    "title": display_name,
                    "name": display_name,
                    "url": preferred,
                    "maps_url": maps_fallback,
                    "snippet": " ".join(part for part in snippet_parts if part),
                    "description": practical_note,
                    "practical_note": practical_note,
                    "detour_distance_miles": detour_miles,
                    "detour_time_minutes": detour_minutes,
                    "cuisine": restaurant_meta.get("cuisine", ""),
                    "price_range": restaurant_meta.get("price_range", ""),
                    "reserve_recommended": bool(restaurant_meta.get("reserve_recommended", False)),
                    "rating": quality_meta.get("rating"),
                    "raw_rating": quality_meta.get("raw_rating"),
                    "votes": quality_meta.get("votes"),
                    "source_type": quality_meta.get("source_type", "official"),
                }
            )
        return rows

    def _search_alltrails_for_trail_from_direct_batch(self, item_name: str, dest_name: str, dates: str = "") -> str | None:
        rows = self._get_alltrails_direct_batch_rows_for_destination(dest_name, dates)
        if not rows:
            self._log_decision(
                kind="attraction",
                dest_name=dest_name,
                item_name=item_name,
                reason="direct_batch_empty",
                message="alltrails direct-link batch empty",
            )
            return None

        if self._direct_batch_is_authoritative():
            matching_urls: list[str] = []
            for row in rows:
                if self._candidate_mentions_conflicting_destination(row, dest_name, item_name=item_name):
                    continue
                structured_url = self._normalize_direct_batch_authoritative_url(row.get("url", ""))
                for raw_url in self._direct_batch_row_url_candidates(row):
                    if raw_url != structured_url and not self._direct_batch_url_matches_item(raw_url, item_name, dest_name):
                        # Preserve raw capture URLs even when the row title is generic,
                        # provided the URL itself is item-relevant and survives the
                        # normal acceptance checks below.
                        if not self._is_alltrails_trail_url(raw_url):
                            continue
                    if not self._is_alltrails_trail_url(raw_url):
                        continue
                    cleaned = self._retain_discovered_url(
                        raw_url,
                        item_name,
                        dest_name,
                        allow_alltrails=True,
                        kind="attraction",
                        candidate=row,
                        allow_shallow_relevance=True,
                    )
                    if cleaned:
                        matching_urls.append(cleaned)

            selected = matching_urls[0] if matching_urls else ""
            if selected:
                self._remember_direct_batch_authoritative_url(selected, item_name)
                self._log_decision(
                    kind="attraction",
                    dest_name=dest_name,
                    item_name=item_name,
                    reason="direct_batch_selected_authoritative",
                    message="alltrails direct-link batch authoritative candidate selected",
                    url=selected,
                )
                return selected
            self._log_decision(
                kind="attraction",
                dest_name=dest_name,
                item_name=item_name,
                reason="direct_batch_no_match",
                message="alltrails direct-link batch had no item-matching trail",
            )
            return None

        best: tuple[int, float, int, str] | None = None
        max_miles = float(getattr(self, "_max_trail_miles", DEFAULT_MAX_TRAIL_MILES) or DEFAULT_MAX_TRAIL_MILES)
        for row in rows:
            if self._candidate_mentions_conflicting_destination(row, dest_name):
                continue
            raw_url = str(row.get("url", "") or "").strip()
            if not self._is_alltrails_trail_url(raw_url):
                continue
            normalized = self._strip_alltrails_tracking(raw_url)
            if self._alltrails_slug_has_numbered_suffix(normalized):
                continue
            if not self._alltrails_slug_matches_item(normalized, item_name):
                continue
            if self._has_alltrails_closure_marker(self._candidate_text_blob(row)):
                continue

            meta = self._extract_alltrails_candidate_metadata(row)
            miles = meta.get("miles")
            if miles is not None and max_miles > 0 and float(miles) > max_miles + 0.15:
                continue

            scored = dict(row)
            scored["url"] = normalized
            score = self._score_candidate_result(
                scored,
                item_name,
                dest_name,
                specific=True,
                site_filter="alltrails.com",
            )
            rating = float(meta.get("rating") or 0.0)
            reviews = int(meta.get("reviews") or 0)
            rank = (score, rating, reviews, normalized)
            if best is None or rank > best:
                best = rank

        if not best:
            self._log_decision(
                kind="attraction",
                dest_name=dest_name,
                item_name=item_name,
                reason="direct_batch_no_match",
                message="alltrails direct-link batch had no matching trail",
            )
            return None

        selected = self._prefer_canonical_alltrails_url(best[-1], item_name)
        self._remember_direct_batch_authoritative_url(selected, item_name)
        self._log_decision(
            kind="attraction",
            dest_name=dest_name,
            item_name=item_name,
            reason="direct_batch_selected",
            message="alltrails direct-link batch candidate selected",
            url=selected or "",
        )
        return selected

    def _search_attraction_from_direct_batch(self, item_name: str, dest_name: str, dates: str = "") -> str | None:
        rows = self._get_attraction_direct_batch_rows_for_destination(dest_name, dates)
        if not rows:
            return None

        if self._direct_batch_is_authoritative():
            matching_map_urls: list[str] = []
            matching_other_urls: list[str] = []
            row_level_non_map_urls: list[str] = []
            row_level_map_urls: list[str] = []
            saw_item_match = False
            matched_row_count = 0
            accepted_candidates: list[tuple[int, int, int, int, int, int, str]] = []
            for row in rows:
                if self._candidate_mentions_conflicting_destination(row, dest_name, item_name=item_name):
                    continue
                structured_url = self._normalize_direct_batch_authoritative_url(row.get("url", ""))
                row_match_strength = self._direct_batch_row_match_strength(row, item_name, dest_name)
                row_is_item_match = row_match_strength > 0
                saw_item_match = saw_item_match or row_is_item_match
                matched_row_count += 1 if row_is_item_match else 0
                for raw_url in self._direct_batch_row_url_candidates(row):
                    shallow_relevance = True
                    item_url_match = self._direct_batch_url_matches_item(raw_url, item_name, dest_name)
                    if not row_is_item_match and not item_url_match:
                        # A row that does not identify the requested item, and a URL
                        # that does not either, must never stand in for the item —
                        # otherwise an unrelated attraction's link can be selected
                        # below when no row in the batch actually matches.
                        continue
                    if raw_url != structured_url and not item_url_match:
                        if self._is_google_maps_candidate_url(raw_url):
                            continue
                    if self._is_alltrails_trail_url(raw_url):
                        continue
                    cleaned = self._retain_discovered_url(
                        raw_url,
                        item_name,
                        dest_name,
                        allow_alltrails=False,
                        kind="attraction",
                        candidate=row,
                        allow_shallow_relevance=shallow_relevance,
                        allow_google_maps_search=True,
                    )
                    if not cleaned:
                        self._log_decision(
                            kind="attraction",
                            dest_name=dest_name,
                            item_name=item_name,
                            reason="direct_batch_candidate_rejected",
                            message="direct-link batch candidate rejected during retention",
                            url=raw_url,
                        )
                        continue
                    is_maps = self._is_google_maps_candidate_url(cleaned)
                    is_row_primary = raw_url == structured_url
                    item_tokens = self._significant_tokens(item_name)
                    title_or_name = str(row.get("title") or row.get("name") or "").strip()
                    specificity = (
                        3 if self._direct_batch_url_matches_item(cleaned, item_name, dest_name) else 0
                    ) + (
                        2 if self._looks_like_item_specific_homepage(cleaned, item_name) else 0
                    ) + (
                        1 if bool(item_tokens and title_or_name and self._text_matches_item_tokens(title_or_name, item_tokens)) else 0
                    )
                    rank = (
                        # Corroboration signal: a row reached only through the
                        # lenient single-anchor-token fallback (strength 1) must
                        # never outrank -- or get pooled equally with -- a row
                        # that satisfies the full required token overlap
                        # (strength 2), e.g. two different "* Temple" entries in
                        # a "St. George" destination once the shared
                        # destination-name token is excluded from matching.
                        row_match_strength,
                        # A URL independently recommended by more than one
                        # discovery mechanism this run (e.g. also surfaced via
                        # AI-candidate resolution) is a corroboration signal in
                        # its own right -- prefer it in ties.
                        self._url_recommendation_source_count(cleaned),
                        specificity,
                        2 if is_maps else 0,
                        1 if is_row_primary else 0,
                        1,
                        cleaned,
                    )
                    accepted_candidates.append(rank)
                    if is_maps:
                        if is_row_primary or raw_url == self._normalize_direct_batch_authoritative_url(row.get("maps_url", "")):
                            row_level_map_urls.append(cleaned)
                        else:
                            matching_map_urls.append(cleaned)
                    else:
                        if is_row_primary:
                            row_level_non_map_urls.append(cleaned)
                        else:
                            matching_other_urls.append(cleaned)
            selected = ""
            if matched_row_count == 1:
                if row_level_non_map_urls:
                    selected = self._select_preferred_direct_batch_url(row_level_non_map_urls, kind="attraction")
                elif row_level_map_urls:
                    selected = self._select_preferred_direct_batch_url(row_level_map_urls, kind="attraction")
            elif accepted_candidates:
                best_strength = max(entry[0] for entry in accepted_candidates)
                strongest_candidates = [entry for entry in accepted_candidates if entry[0] == best_strength]
                candidate_urls = [entry[6] for entry in strongest_candidates]
                selected = self._select_preferred_direct_batch_url(candidate_urls, kind="attraction")
                if not selected:
                    selected = max(
                        strongest_candidates,
                        key=lambda entry: (entry[1], entry[2], entry[3], entry[4], entry[5]),
                    )[6]
            if selected:
                self._remember_direct_batch_authoritative_url(selected, item_name)
                self._log_decision(
                    kind="attraction",
                    dest_name=dest_name,
                    item_name=item_name,
                    reason="direct_batch_selected_authoritative",
                    message="attraction link (direct-link batch authoritative)",
                    url=selected,
                )
                return selected
            self._log_decision(
                kind="attraction",
                dest_name=dest_name,
                item_name=item_name,
                reason="direct_batch_no_accepted_candidates" if saw_item_match else "direct_batch_no_match",
                message=(
                    "direct-link batch had item matches but no accepted candidates"
                    if saw_item_match
                    else "direct-link batch had no item-matching candidates"
                ),
            )
            return None

        item_tokens = self._significant_tokens(item_name)
        ranked_maps: list[tuple[int, str]] = []
        ranked_other: list[tuple[int, str]] = []
        for row in rows:
            if self._candidate_mentions_conflicting_destination(row, dest_name):
                continue
            raw_url = str(row.get("url", "") or "").strip()
            if not raw_url or self._is_alltrails_trail_url(raw_url):
                continue
            if self._is_obviously_generic_url(raw_url.lower()):
                continue
            normalized = self._normalize_restaurant_url(raw_url)
            if not normalized:
                continue
            if item_tokens and not self._candidate_text_matches_item_tokens(row, item_tokens):
                continue
            scored = dict(row)
            scored["url"] = normalized
            score = self._score_candidate_result(
                scored,
                item_name,
                dest_name,
                specific=True,
                site_filter=None,
            )
            bucket = ranked_maps if self._is_deterministic_google_maps_place_url(normalized) else ranked_other
            bucket.append((score, normalized))

        for ranked in (ranked_maps, ranked_other):
            for _score, candidate_url in sorted(ranked, key=lambda row: row[0], reverse=True)[:4]:
                cleaned = self._retain_discovered_url(
                    candidate_url,
                    item_name,
                    dest_name,
                    allow_alltrails=False,
                    kind="attraction",
                )
                if cleaned:
                    return cleaned
        return None

    def _search_attraction_from_item_query_fanout(
        self,
        *,
        item_name: str,
        dest_name: str,
        nps_code: str | None,
    ) -> tuple[str | None, str]:
        """Per-item fallback fan-out for attraction URL discovery.

        Used only when destination-level direct-batch authoritative mode has no
        item match. We keep fail-closed semantics if this per-item fan-out also
        fails.
        """
        if self._direct_batch_is_authoritative():
            return None, "authoritative_direct_batch_lockout"
        maps_url = self._search_first(
            _build_attraction_maps_query_variants(item_name, dest_name),
            site_filter="google.com/maps",
            item_name=item_name,
            dest_name=dest_name,
            allow_alltrails=False,
        )
        if maps_url:
            return maps_url, "maps"

        maps_area_url = self._search_attraction_from_maps_area_pool(item_name, dest_name)
        if maps_area_url:
            return maps_area_url, "maps_area"

        category = "attraction landmark museum viewpoint"
        site_hint = f"site:nps.gov/{nps_code}" if nps_code else None
        nps_url = self._search_first(
            _build_query_variants(item_name, dest_name, category),
            site_filter="nps.gov" if nps_code else None,
            site_hint=site_hint,
            item_name=item_name,
            dest_name=dest_name,
            allow_alltrails=False,
        )
        if nps_url:
            return nps_url, "nps"

        if nps_code and self._is_category_style_activity(item_name):
            activity_url = self._search_first(
                _build_nps_activity_query_variants(item_name, dest_name),
                site_filter="nps.gov",
                site_hint=site_hint,
                item_name=item_name,
                dest_name=dest_name,
                allow_alltrails=False,
            )
            if activity_url:
                return activity_url, "nps_activity"

        web_url = self._search_first(
            _build_query_variants(item_name, dest_name, category),
            item_name=item_name,
            dest_name=dest_name,
            allow_alltrails=False,
        )
        if web_url:
            return web_url, "web"
        return None, "no_match"

    def _get_attraction_maps_area_rows_for_destination(self, dest_name: str, dates: str = "") -> list[dict[str, Any]]:
        if not hasattr(self, "_request_cache_lock"):
            self._request_cache_lock = Lock()
        if not hasattr(self, "_attraction_maps_area_cache"):
            self._attraction_maps_area_cache = {}

        key = self._batch_cache_key(dest_name, f"{dates}|maps_area")
        if not key:
            return []

        with self._request_cache_lock:
            cached = self._attraction_maps_area_cache.get(key)
            if cached is not None:
                return [dict(row) for row in cached if isinstance(row, dict)]

        rows = self._search_cached(
            self._attraction_maps_area_query(dest_name, dates),
            count=max(10, self._direct_link_batch_limit()),
        )
        normalized = [dict(row) for row in rows if isinstance(row, dict)]
        with self._request_cache_lock:
            self._attraction_maps_area_cache[key] = normalized
        return [dict(row) for row in normalized]

    def _search_attraction_from_maps_area_pool(self, item_name: str, dest_name: str) -> str | None:
        rows = self._get_attraction_maps_area_rows_for_destination(dest_name)
        if not rows:
            return None

        ranked_maps: list[tuple[int, str]] = []
        ranked_other: list[tuple[int, str]] = []
        for row in rows:
            if not self._direct_batch_row_matches_item(row, item_name, dest_name):
                continue

            structured_url = self._normalize_direct_batch_authoritative_url(row.get("url", ""))
            for raw_url in self._direct_batch_row_url_candidates(row):
                if raw_url != structured_url and not self._direct_batch_url_matches_item(raw_url, item_name, dest_name):
                    continue
                if self._is_alltrails_trail_url(raw_url):
                    continue

                cleaned = self._retain_discovered_url(
                    raw_url,
                    item_name,
                    dest_name,
                    allow_alltrails=False,
                    kind="attraction",
                    allow_google_maps_search=True,
                )
                if not cleaned:
                    continue
                if (
                    self._classify_url_policy_class(cleaned) == "google_maps_search"
                    and not self._direct_batch_url_matches_item(cleaned, item_name, dest_name)
                ):
                    continue

                scored = dict(row)
                scored["url"] = cleaned
                score = self._score_candidate_result(
                    scored,
                    item_name,
                    dest_name,
                    specific=True,
                    site_filter=None,
                )
                bucket = ranked_maps if self._is_google_maps_candidate_url(cleaned) else ranked_other
                bucket.append((score, cleaned))

        for ranked in (ranked_maps, ranked_other):
            if ranked:
                ranked.sort(key=lambda row: row[0], reverse=True)
                return ranked[0][1]
        return None

    def _search_restaurant_from_direct_batch(self, item_name: str, dest_name: str, dates: str = "", lodging_location: str = "") -> str | None:
        rows = self._get_restaurant_direct_batch_rows_for_destination(dest_name, dates, lodging_location=lodging_location)
        if not rows:
            return None

        if self._direct_batch_is_authoritative():
            matching_map_urls: list[str] = []
            matching_other_urls: list[str] = []
            saw_item_match = False
            for row in rows:
                if self._candidate_mentions_conflicting_destination(row, dest_name, item_name=item_name):
                    continue
                row_is_item_match = self._direct_batch_row_matches_item(row, item_name, dest_name)
                saw_item_match = saw_item_match or row_is_item_match
                structured_url = self._normalize_direct_batch_authoritative_url(row.get("url", ""))
                for raw_url in self._direct_batch_row_url_candidates(row):
                    item_url_match = self._direct_batch_url_matches_item(raw_url, item_name, dest_name)
                    if not row_is_item_match and not item_url_match:
                        # An unmatched row/URL is never treated as the requested item inline.
                        # The only sanctioned no-match fallback is the post-loop raw-capture
                        # pass below, which applies the full generic-landing-page gate
                        # (`_is_generic_restaurant_landing_url`) instead of a narrower inline
                        # heuristic that could accept an unrelated restaurant's link.
                        continue
                    is_maps = self._is_google_maps_candidate_url(raw_url)
                    shallow_relevance = self._direct_batch_authoritative and not is_maps
                    if raw_url != structured_url:
                        if is_maps and not item_url_match:
                            continue
                    if raw_url == structured_url and self._direct_batch_is_authoritative():
                        # Raw direct-batch HTML captures are the contract baseline. Preserve the
                        # primary raw URL unless it is an obvious area-list page such as a
                        # TripAdvisor restaurant index or a destination-wide dining landing page.
                        lower = (raw_url or "").lower()
                        obvious_area_listing = (
                            "tripadvisor." in lower and "/restaurants-" in lower
                            or any(marker in lower for marker in ("best-restaurants-near", "restaurants-near"))
                            or "/restaurants/" in urlparse(raw_url).path.lower()
                        )
                        if obvious_area_listing:
                            self._log_decision(
                                kind="restaurant",
                                dest_name=dest_name,
                                item_name=item_name,
                                reason="direct_batch_candidate_rejected_generic",
                                message="direct-link batch candidate rejected (generic/area landing)",
                                url=raw_url,
                            )
                            continue
                    elif self._is_generic_restaurant_landing_url(raw_url, item_name, dest_name,
                            item_tokens=self._restaurant_significant_tokens(item_name)):
                        self._log_decision(
                            kind="restaurant",
                            dest_name=dest_name,
                            item_name=item_name,
                            reason="direct_batch_candidate_rejected_generic",
                            message="direct-link batch candidate rejected (generic/area landing)",
                            url=raw_url,
                        )
                        continue
                    cleaned = self._retain_discovered_url(
                        raw_url,
                        item_name,
                        dest_name,
                        allow_alltrails=False,
                        kind="restaurant",
                        candidate=row,
                        allow_shallow_relevance=shallow_relevance,
                        allow_google_maps_search=True,
                    )
                    if not cleaned:
                        self._log_decision(
                            kind="restaurant",
                            dest_name=dest_name,
                            item_name=item_name,
                            reason="direct_batch_candidate_rejected",
                            message="direct-link batch candidate rejected during retention",
                            url=raw_url,
                        )
                        continue
                    if self._is_google_maps_candidate_url(cleaned):
                        matching_map_urls.append(cleaned)
                    else:
                        matching_other_urls.append(cleaned)
            selected = ""
            if matching_other_urls:
                selected = self._select_preferred_direct_batch_url(matching_other_urls, kind="restaurant")
            elif matching_map_urls:
                selected = self._select_preferred_direct_batch_url(matching_map_urls, kind="restaurant")
            if selected and matching_map_urls and self._is_generic_restaurant_landing_url(selected, item_name, dest_name):
                selected = self._select_preferred_direct_batch_url(matching_map_urls, kind="restaurant")
            # No unmatched-row raw-capture fallback: per the fail-closed named-entity
            # contract, a destination batch with no row/URL match for this item must
            # publish no canonical URL rather than borrow another restaurant's link.
            if selected:
                self._remember_direct_batch_authoritative_url(selected, item_name)
                return selected
            self._log_decision(
                kind="restaurant",
                dest_name=dest_name,
                item_name=item_name,
                reason="direct_batch_no_accepted_candidates" if saw_item_match else "direct_batch_no_match",
                message=(
                    "direct-link batch had item matches but no accepted candidates"
                    if saw_item_match
                    else "direct-link batch had no item-matching candidates"
                ),
            )
            return None

        item_tokens = self._significant_tokens(item_name)
        ranked_maps: list[tuple[int, str]] = []
        ranked_other: list[tuple[int, str]] = []
        for row in rows:
            if item_tokens and not self._candidate_text_matches_item_tokens(row, item_tokens):
                continue
            for raw_url in self._direct_batch_row_url_candidates(row):
                candidate_url = self._normalize_restaurant_url(raw_url)
                if not candidate_url:
                    if self._is_google_maps_candidate_url(raw_url):
                        candidate_url = raw_url.split("#", 1)[0]
                    else:
                        continue
                if self._is_generic_restaurant_landing_url(candidate_url, item_name, dest_name,
                        item_tokens=self._restaurant_significant_tokens(item_name)):
                    continue
                cleaned = self._retain_discovered_url(
                    candidate_url,
                    item_name,
                    dest_name,
                    allow_alltrails=False,
                    kind="restaurant",
                    allow_google_maps_search=True,
                )
                if not cleaned:
                    continue
                scored = dict(row)
                scored["url"] = cleaned
                score = self._score_candidate_result(
                    scored,
                    item_name,
                    dest_name,
                    specific=True,
                    site_filter=None,
                )
                bucket = ranked_maps if self._is_google_maps_candidate_url(cleaned) else ranked_other
                bucket.append((score, cleaned))

        for ranked in (ranked_maps, ranked_other):
            if ranked:
                ranked.sort(key=lambda row: row[0], reverse=True)
                return ranked[0][1]
        return None

    def _is_generic_restaurant_landing_url(self, url: str, item_name: str, dest_name: str, *, item_tokens: list[str] | None = None) -> bool:
        candidate = str(url or "").strip()
        if not candidate:
            return True

        lower_url = unquote(candidate).replace("+", " ").lower()
        parsed = urlparse(candidate)
        host = (parsed.netloc or "").lower()
        path_l = unquote(parsed.path or "").lower()

        # Area/list pages should not be linked as a specific restaurant target.
        if "tripadvisor." in host and (
            path_l.startswith("/restaurants-") or path_l.startswith("/restaurantsnear-")
        ):
            return True
        if any(marker in lower_url for marker in ("best-restaurants-near", "restaurants-near", "restaurantsnear")):
            return True
        if "/restaurants/" in path_l and not self._is_google_maps_candidate_url(candidate):
            return True

        tokens = item_tokens if item_tokens is not None else self._significant_tokens(item_name)
        required_overlap = self._required_general_token_matches(len(tokens)) if tokens else 0

        if self._is_google_maps_candidate_url(candidate):
            path_lower = (parsed.path or "").lower()
            query_lower = (parsed.query or "").lower()

            if "/maps/search" not in path_lower:
                if ("q=" in query_lower or "query=" in query_lower) and tokens:
                    overlap = sum(1 for token in tokens if token in lower_url)
                    if overlap < required_overlap:
                        return True
                return False

            if re.search(r"\brestaurants?\s+near\b", lower_url):
                return True
            if tokens:
                overlap = sum(1 for token in tokens if token in lower_url)
                if overlap < required_overlap:
                    return True
            return False

        if not tokens:
            return False

        path_tokens_text = unquote(parsed.path or "").replace("-", " ").replace("_", " ").lower()
        host_tokens_text = host.replace("-", " ")
        token_overlap = sum(
            1 for token in tokens if (token in path_tokens_text or token in host_tokens_text)
        )
        return token_overlap < required_overlap

    @staticmethod
    def _direct_batch_url_priority(url: str, *, kind: str) -> int:
        lower = (url or "").lower()
        is_maps_search = (
            "google.com/maps/search" in lower
            or "maps.google.com" in lower and ("?q=" in lower or "&q=" in lower or "?query=" in lower or "&query=" in lower)
        )
        is_maps_place = "/maps/place/" in lower or "maps.google.com/place" in lower
        is_tripadvisor = "tripadvisor" in lower

        if kind == "restaurant":
            if is_tripadvisor:
                return 30
            if is_maps_search:
                return 40
            if is_maps_place:
                return 50
            return 10

        if kind == "en_route_stop":
            if is_tripadvisor:
                return 25
            if is_maps_search:
                return 35
            if is_maps_place:
                return 45
            return 15

        if kind == "attraction":
            # Lower number wins (see _select_preferred_direct_batch_url). A specific
            # official/source page is always more useful than a Maps search query or
            # a generic listing page, so those must rank worst, not best -- this
            # block previously had maps_search/maps_place scored as the *lowest*
            # (winning) numbers, which meant a vague Maps search link was preferred
            # over the attraction's own official page whenever both were candidates.
            parsed = urlparse(url or "")
            path_l = (unquote(parsed.path or "") or "").lower()
            generic_markers = (
                "/places-to-go/",
                "/cities-and-towns/",
                "/things-to-do/",
                "/things2do/",
                "/attractions/",
                "/activities/",
                "/explore/",
                "/food/",
                "/restaurants/",
                "/dining/",
                "/visit/",
                "/about/",
                "/home",
            )
            if is_maps_search:
                return 90
            if is_tripadvisor:
                return 70
            if any(marker in path_l for marker in generic_markers):
                return 60
            if is_maps_place:
                return 30
            if "nps.gov" in lower and "/planyourvisit/" in path_l:
                return 20
            return 10

        if is_tripadvisor:
            return 25
        if is_maps_search:
            return 35
        if is_maps_place:
            return 45
        return 10

    def _select_preferred_direct_batch_url(self, candidates: list[str], *, kind: str) -> str:
        valid = [url for url in candidates if url and str(url).strip()]
        if not valid:
            return ""
        ordered = sorted(valid, key=lambda url: self._direct_batch_url_priority(url, kind=kind))
        return ordered[0]

    def _search_en_route_stop_from_direct_batch(
        self,
        item_name: str,
        dest_name: str,
        dates: str = "",
        origin_name: str = "",
    ) -> str | None:
        rows = self._get_en_route_direct_batch_rows_for_destination(dest_name, dates, origin_name)
        if not rows:
            self._log_decision(
                kind="en_route_stop",
                dest_name=dest_name,
                item_name=item_name,
                reason="direct_batch_empty",
                message="en-route direct-link batch empty",
            )
            return None

        if self._direct_batch_is_authoritative():
            matching_map_urls: list[tuple[int, str]] = []
            matching_other_urls: list[tuple[int, str]] = []
            saw_item_match = False
            for row in rows:
                if self._candidate_mentions_conflicting_destination(row, dest_name, item_name=item_name):
                    continue
                row_is_item_match = self._direct_batch_row_matches_item(row, item_name, dest_name)
                saw_item_match = saw_item_match or row_is_item_match
                structured_url = self._normalize_direct_batch_authoritative_url(row.get("url", ""))
                for raw_url in self._direct_batch_row_url_candidates(row):
                    shallow_relevance = (
                        raw_url != structured_url and not self._is_google_maps_candidate_url(raw_url)
                    )
                    if raw_url != structured_url:
                        if self._is_google_maps_candidate_url(raw_url) and not self._direct_batch_url_matches_item(raw_url, item_name, dest_name):
                            continue
                    if self._is_alltrails_trail_url(raw_url):
                        continue
                    cleaned = self._retain_discovered_url(
                        raw_url,
                        item_name,
                        dest_name,
                        allow_alltrails=False,
                        kind="en-route stop",
                        candidate=row,
                        allow_shallow_relevance=shallow_relevance,
                        allow_google_maps_search=True,
                    )
                    if not cleaned:
                        self._log_decision(
                            kind="en_route_stop",
                            dest_name=dest_name,
                            item_name=item_name,
                            reason="direct_batch_candidate_rejected",
                            message="direct-link batch candidate rejected during retention",
                            url=raw_url,
                        )
                        continue
                    scored = dict(row)
                    scored["url"] = cleaned
                    score = self._score_candidate_result(
                        scored,
                        item_name,
                        dest_name,
                        specific=True,
                        site_filter=None,
                    )
                    if self._is_google_maps_candidate_url(cleaned):
                        matching_map_urls.append((score, cleaned))
                    else:
                        matching_other_urls.append((score, cleaned))

            selected = ""
            # En-route stops are navigation waypoints — prefer Maps over source pages.
            if matching_map_urls:
                selected = sorted(matching_map_urls, key=lambda row: row[0], reverse=True)[0][1]
            elif matching_other_urls:
                selected = sorted(matching_other_urls, key=lambda row: row[0], reverse=True)[0][1]
            if selected:
                self._remember_direct_batch_authoritative_url(selected, item_name)
                self._log_decision(
                    kind="en_route_stop",
                    dest_name=dest_name,
                    item_name=item_name,
                    reason="direct_batch_selected_authoritative",
                    message="en-route link (direct-link batch authoritative)",
                    url=selected,
                )
                return selected
            self._log_decision(
                kind="en_route_stop",
                dest_name=dest_name,
                item_name=item_name,
                reason="direct_batch_no_accepted_candidates" if saw_item_match else "direct_batch_no_match",
                message=(
                    "en-route direct-link batch had item matches but no accepted candidates"
                    if saw_item_match
                    else "en-route direct-link batch had no item-matching link"
                ),
            )
            return None

        item_tokens = self._significant_tokens(item_name)
        ranked_maps: list[tuple[int, str]] = []
        ranked_other: list[tuple[int, str]] = []
        for row in rows:
            if item_tokens and not self._candidate_text_matches_item_tokens(row, item_tokens):
                continue
            for raw_url in self._direct_batch_row_url_candidates(row):
                if not raw_url or self._is_alltrails_trail_url(raw_url):
                    continue
                candidate_url = self._normalize_restaurant_url(raw_url)
                if not candidate_url:
                    if self._is_google_maps_candidate_url(raw_url):
                        candidate_url = raw_url.split("#", 1)[0]
                    else:
                        continue
                cleaned = self._retain_discovered_url(
                    candidate_url,
                    item_name,
                    dest_name,
                    allow_alltrails=False,
                    kind="en-route stop",
                )
                if not cleaned:
                    continue
                scored = dict(row)
                scored["url"] = cleaned
                score = self._score_candidate_result(
                    scored,
                    item_name,
                    dest_name,
                    specific=True,
                    site_filter=None,
                )
                bucket = ranked_maps if self._is_google_maps_candidate_url(cleaned) else ranked_other
                bucket.append((score, cleaned))

        for ranked in (ranked_maps, ranked_other):
            if ranked:
                selected = sorted(ranked, key=lambda row: row[0], reverse=True)[0][1]
                self._log_decision(
                    kind="en_route_stop",
                    dest_name=dest_name,
                    item_name=item_name,
                    reason="direct_batch_selected",
                    message="en-route link (direct-link batch)",
                    url=selected,
                )
                return selected

        self._log_decision(
            kind="en_route_stop",
            dest_name=dest_name,
            item_name=item_name,
            reason="direct_batch_no_match",
            message="en-route direct-link batch had no matching stop",
        )
        return None

    def _search_alltrails_for_trail(self, item_name: str, dest_name: str, dates: str = "") -> str | None:
        """Exhaust high-signal AllTrails queries before non-AllTrails fallback."""
        if bool(getattr(self, "_disable_trails", False)):
            return None

        source_mode = str(getattr(self, "_alltrails_source", "search") or "search")
        if source_mode == "direct_link_batch":
            selected = self._search_alltrails_for_trail_from_direct_batch(item_name, dest_name, dates)
            if selected:
                selected = self._prefer_canonical_alltrails_url(selected, item_name)
                if self._passes_alltrails_post_search_filters(selected, item_name, dest_name):
                    return selected
                self._log_decision(
                    kind="attraction",
                    dest_name=dest_name,
                    item_name=item_name,
                    reason="post_search_constraints_rejected",
                    message="alltrails direct-link batch candidate rejected by post-search constraints",
                    url=selected or "",
                )
                if self._direct_batch_is_authoritative():
                    return None
                # Do not hard-stop here: if the direct-batch candidate is weak or
                # mismatched, broader search / NPS / maps fallbacks should still be
                # allowed. Direct-batch entries remain highest priority only when
                # they are actually viable.
            elif self._direct_batch_is_authoritative():
                self._log_decision(
                    kind="attraction",
                    dest_name=dest_name,
                    item_name=item_name,
                    reason="direct_batch_no_match",
                    message="alltrails direct-link batch had no item-matching trail in authoritative mode",
                )
                return None
        if source_mode == "apify_single_call":
            selected = self._search_alltrails_for_trail_from_apify_pool(item_name, dest_name)
            selected = self._prefer_canonical_alltrails_url(selected, item_name)
            if self._passes_alltrails_post_search_filters(selected, item_name, dest_name):
                return selected
            self._log_decision(
                kind="attraction",
                dest_name=dest_name,
                item_name=item_name,
                reason="post_search_constraints_rejected",
                message="alltrails apify candidate rejected by post-search constraints",
                url=selected or "",
            )
            return None

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
            # Use constrained selection first, then allow broader AllTrails
            # matching when snippets lack complete metadata.
            filtered = self._get_filtered_alltrails_selection(
                item_name=item_name,
                dest_name=dest_name,
                query_variants=deduped_variants,
            )
            if filtered:
                return filtered

        resolved = self._search_first(
            deduped_variants,
            site_filter="alltrails.com",
            item_name=item_name,
            dest_name=dest_name,
            max_attempts=min(len(deduped_variants), int(getattr(self, "_max_alltrails_query_attempts", 5) or 5)),
        )
        selected = self._prefer_canonical_alltrails_url(resolved, item_name)
        if self._passes_alltrails_post_search_filters(selected, item_name, dest_name):
            return selected
        self._log_decision(
            kind="attraction",
            dest_name=dest_name,
            item_name=item_name,
            reason="post_search_constraints_rejected",
            message="alltrails broad search candidate rejected by post-search constraints",
            url=selected or "",
        )
        return None

    def _search_alltrails_for_seed_relaxed(self, item_name: str, dest_name: str) -> str | None:
        """Seed-only fallback: keep strong canonical AllTrails candidates."""
        if bool(getattr(self, "_disable_trails", False)):
            return None

        query_variants = _build_alltrails_query_variants(item_name, dest_name)
        seen: set[str] = set()
        deduped_variants: list[str] = []
        for q in query_variants:
            key = (q or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped_variants.append(q)

        resolved = self._search_first(
            deduped_variants,
            site_filter="alltrails.com",
            item_name=item_name,
            dest_name=dest_name,
            max_attempts=min(len(deduped_variants), int(getattr(self, "_max_alltrails_query_attempts", 5) or 5)),
        )
        selected = self._prefer_canonical_alltrails_url(resolved, item_name)
        if not selected or not self._is_alltrails_trail_url(selected):
            return None

        verified_ok, verified_status = self._verify_url_cached(selected)
        if not verified_ok and self._is_definitively_dead_status(verified_status):
            return None

        ok, _status, text = self._fetch_page_text(selected, timeout=8)
        if ok and text and self._has_alltrails_closure_marker(text):
            return None

        if not self._alltrails_slug_matches_item(selected, item_name):
            return None

        return selected

    def _passes_alltrails_post_search_filters(self, url: str | None, item_name: str, dest_name: str) -> bool:
        if not url or not self._is_alltrails_trail_url(url):
            return True

        # Dead slugs should fail closed regardless of filtered selection mode.
        verified_ok, verified_status = self._verify_url_cached(url)
        if not verified_ok and self._is_definitively_dead_status(verified_status):
            return False

        if not bool(getattr(self, "_enable_filtered_alltrails_selection", DEFAULT_ENABLE_FILTERED_ALLTRAILS_SELECTION)):
            return True

        ok, status, text = self._fetch_page_text(url, timeout=8)
        if not ok or not text:
            if self._is_definitively_dead_status(status):
                return False
            # Preserve fail-open behavior for bot-blocked/transient fetch failures.
            return True

        meta = self._extract_alltrails_candidate_metadata(
            {
                "url": url,
                "title": item_name,
                "snippet": text,
                "description": text,
            }
        )

        allowed_difficulties = {
            str(item or "").strip().lower()
            for item in getattr(
                self,
                "_alltrails_filter_allowed_difficulties",
                DEFAULT_ALLTRAILS_FILTER_ALLOWED_DIFFICULTIES,
            )
            if str(item or "").strip()
        }
        difficulty = str(meta.get("difficulty") or "").strip().lower()
        if difficulty and allowed_difficulties and difficulty not in allowed_difficulties:
            return False

        filter_max_miles = float(getattr(self, "_alltrails_filter_max_miles", DEFAULT_ALLTRAILS_FILTER_MAX_MILES) or 0)
        publish_max_miles = float(getattr(self, "_max_trail_miles", DEFAULT_MAX_TRAIL_MILES) or 0)
        max_miles_candidates = [v for v in (filter_max_miles, publish_max_miles) if v and v > 0]
        effective_max_miles = min(max_miles_candidates) if max_miles_candidates else 0.0
        miles = meta.get("miles")
        if miles is not None and effective_max_miles > 0 and float(miles) > effective_max_miles:
            return False

        max_gain = int(getattr(self, "_alltrails_filter_max_gain_feet", DEFAULT_ALLTRAILS_FILTER_MAX_GAIN_FEET) or 0)
        gain_feet = meta.get("gain_feet")
        if gain_feet is not None and max_gain >= 0 and int(gain_feet) > max_gain:
            return False

        min_reviews = int(getattr(self, "_alltrails_filter_min_reviews", DEFAULT_ALLTRAILS_FILTER_MIN_REVIEWS) or 0)
        reviews = meta.get("reviews")
        if reviews is not None and min_reviews > 0 and int(reviews) < min_reviews:
            return False

        return True

    @staticmethod
    def _apify_is_closed(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        lowered = str(value or "").strip().lower()
        return lowered in {"1", "true", "yes"}

    @staticmethod
    def _apify_numeric(value: Any, cast: type[float] | type[int]) -> float | int | None:
        try:
            return cast(value)
        except (TypeError, ValueError):
            return None

    def _search_alltrails_for_trail_from_apify_pool(self, item_name: str, dest_name: str) -> str | None:
        rows = self._get_apify_alltrails_rows_for_destination(dest_name)
        if not rows:
            self._log_decision(
                kind="attraction",
                dest_name=dest_name,
                item_name=item_name,
                reason="apify_pool_empty",
                message="alltrails apify pool empty",
            )
            return None

        item_tokens = set(self._significant_tokens(item_name))
        best: tuple[float, float, int, str] | None = None
        reason_counts: dict[str, int] = {}

        allowed_difficulties = {
            str(item or "").strip().lower()
            for item in getattr(
                self,
                "_alltrails_filter_allowed_difficulties",
                DEFAULT_ALLTRAILS_FILTER_ALLOWED_DIFFICULTIES,
            )
            if str(item or "").strip()
        }
        max_miles = float(getattr(self, "_alltrails_filter_max_miles", DEFAULT_ALLTRAILS_FILTER_MAX_MILES) or 0.0)
        max_gain = int(getattr(self, "_alltrails_filter_max_gain_feet", DEFAULT_ALLTRAILS_FILTER_MAX_GAIN_FEET) or 0)
        min_reviews = int(getattr(self, "_alltrails_filter_min_reviews", DEFAULT_ALLTRAILS_FILTER_MIN_REVIEWS) or 0)
        overlap_min = int(
            getattr(
                self,
                "_alltrails_apify_destination_token_overlap_min",
                DEFAULT_ALLTRAILS_APIFY_DESTINATION_TOKEN_OVERLAP_MIN,
            )
            or 0
        )

        def _bump(reason: str) -> None:
            reason_counts[reason] = int(reason_counts.get(reason, 0) or 0) + 1

        for row in rows:
            if not isinstance(row, dict):
                _bump("skip_non_object")
                continue

            if self._candidate_mentions_conflicting_destination(row, dest_name):
                _bump("skip_destination_conflict")
                continue

            raw_url = str(
                row.get("trailUrl")
                or row.get("url")
                or row.get("canonicalUrl")
                or row.get("alltrailsUrl")
                or ""
            ).strip()
            if not self._is_alltrails_trail_url(raw_url):
                _bump("skip_not_alltrails_trail")
                continue
            if self._alltrails_slug_has_numbered_suffix(raw_url):
                _bump("skip_numbered_slug")
                continue
            if self._apify_is_closed(row.get("isClosed")):
                _bump("skip_closed")
                continue
            if not self._alltrails_slug_matches_item(raw_url, item_name):
                _bump("skip_slug_item_mismatch")
                continue

            candidate_name = str(row.get("name") or row.get("trailName") or "").strip()
            candidate_tokens = set(self._significant_tokens(candidate_name))
            item_overlap_count = len(item_tokens & candidate_tokens) if item_tokens else 0
            overlap_ratio = (item_overlap_count / float(max(1, len(item_tokens)))) if item_tokens else 1.0
            required_item_overlap = self._required_alltrails_token_matches(len(item_tokens)) if item_tokens else 0
            slug_is_precise = self._alltrails_slug_extra_term_count(raw_url, item_name) == 0
            strong_item_alignment = bool(item_tokens) and item_overlap_count >= required_item_overlap
            if not strong_item_alignment and slug_is_precise and item_tokens:
                strong_item_alignment = True

            overlap_count = self._apify_destination_overlap_count(row, dest_name)
            if overlap_min > 0 and overlap_count < overlap_min and not strong_item_alignment:
                _bump("skip_destination_mismatch")
                continue

            rating_raw = row.get("avgRating") if row.get("avgRating") is not None else row.get("rating")
            reviews_raw = (
                row.get("numReviews")
                if row.get("numReviews") is not None
                else (row.get("reviewCount") if row.get("reviewCount") is not None else row.get("reviews"))
            )
            rating = float(self._apify_numeric(rating_raw, float) or 0.0)
            reviews = int(self._apify_numeric(reviews_raw, int) or 0)

            difficulty = str(row.get("difficulty") or "").strip().lower()
            if difficulty and allowed_difficulties and difficulty not in allowed_difficulties:
                _bump("skip_difficulty")
                continue

            miles = row.get("lengthMiles")
            if miles is None:
                miles = self._meters_to_miles(row.get("lengthMeters"))
            miles_value = float(miles) if miles is not None else None
            if miles_value is not None and max_miles > 0 and miles_value > max_miles + 0.15:
                _bump("skip_over_distance")
                continue

            gain_raw = row.get("elevationGainFt")
            if gain_raw is None and row.get("elevationGainMeters") is not None:
                gain_raw = float(row.get("elevationGainMeters")) * 3.28084
            gain_value = int(self._apify_numeric(gain_raw, int) or 0)
            if max_gain >= 0 and gain_value > max_gain:
                _bump("skip_over_gain")
                continue

            if min_reviews > 0 and reviews < min_reviews:
                _bump("skip_low_reviews")
                continue

            rank = (overlap_ratio, rating, reviews, raw_url)
            if best is None or rank > best:
                best = rank
                _bump("candidate_ranked")

        if not best:
            detail = ", ".join(f"{k}={v}" for k, v in sorted(reason_counts.items())) or "none"
            self._log_decision(
                kind="attraction",
                dest_name=dest_name,
                item_name=item_name,
                reason="apify_pool_no_match",
                message=f"alltrails apify candidate rejected ({detail})",
            )
            return None

        selected = self._prefer_canonical_alltrails_url(best[-1], item_name)
        self._log_decision(
            kind="attraction",
            dest_name=dest_name,
            item_name=item_name,
            reason="apify_pool_selected",
            message="alltrails apify candidate selected",
            url=selected or "",
        )
        return selected

    @staticmethod
    def _meters_to_miles(value: Any) -> float | None:
        try:
            meters = float(value)
        except (TypeError, ValueError):
            return None
        if meters < 0:
            return None
        return meters / 1609.344

    def _get_apify_alltrails_rows_for_destination(self, dest_name: str) -> list[dict[str, Any]]:
        cache_key = str(dest_name or "").strip().lower()
        if not cache_key:
            return []

        with self._request_cache_lock:
            cached = self._alltrails_apify_destination_cache.get(cache_key)
            if cached is not None:
                return cached

        token_env = str(getattr(self, "_alltrails_apify_token_env", DEFAULT_ALLTRAILS_APIFY_TOKEN_ENV) or DEFAULT_ALLTRAILS_APIFY_TOKEN_ENV)
        token = str(os.environ.get(token_env, "") or "").strip()
        if not token:
            if not self._alltrails_apify_warned_missing_token:
                logger.warning(
                    "AllTrails Apify source selected, but %s is not set; skipping Apify trail discovery",
                    token_env,
                )
                self._alltrails_apify_warned_missing_token = True
            with self._request_cache_lock:
                self._alltrails_apify_destination_cache[cache_key] = []
            return []

        lat_lon = self._geocode_destination_for_apify(dest_name)
        if not lat_lon:
            with self._request_cache_lock:
                self._alltrails_apify_destination_cache[cache_key] = []
            return []

        lat, lon = lat_lon
        radius_miles = self._destination_apify_radius_miles(dest_name)
        start_url = self._build_apify_explore_start_url(
            lat,
            lon,
            within_miles=radius_miles,
        )
        actor_id = str(getattr(self, "_alltrails_apify_actor_id", DEFAULT_ALLTRAILS_APIFY_ACTOR_ID) or DEFAULT_ALLTRAILS_APIFY_ACTOR_ID).strip()
        max_items = int(getattr(self, "_alltrails_apify_max_items", DEFAULT_ALLTRAILS_APIFY_MAX_ITEMS) or DEFAULT_ALLTRAILS_APIFY_MAX_ITEMS)

        payload = {
            "startUrl": start_url,
            "maxItems": max(1, max_items),
            "descriptionLanguage": "en",
            "minRating": 0,
            "minReviews": 0,
        }
        api_url = f"https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"
        rows: list[dict[str, Any]] = []

        try:
            response = self._url_validator.session.post(
                api_url,
                params={"token": token},
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
            parsed = response.json()
            if isinstance(parsed, list):
                rows = [row for row in parsed if isinstance(row, dict)]
        except Exception as exc:
            logger.warning("Apify AllTrails fetch failed for '%s': %s", dest_name, exc)
            rows = []

        with self._request_cache_lock:
            self._alltrails_apify_destination_cache[cache_key] = rows
        return rows

    @staticmethod
    def _normalize_destination_key(text: str) -> str:
        lowered = str(text or "").strip().lower()
        return re.sub(r"[^a-z0-9]+", " ", lowered).strip()

    def _destination_apify_radius_miles(self, dest_name: str) -> float:
        base = float(
            getattr(self, "_alltrails_apify_within_miles", DEFAULT_ALLTRAILS_APIFY_WITHIN_MILES)
            or DEFAULT_ALLTRAILS_APIFY_WITHIN_MILES
        )
        mapping = getattr(self, "_alltrails_apify_within_miles_by_destination", {})
        if not isinstance(mapping, dict) or not mapping:
            return base

        normalized_name = self._normalize_destination_key(dest_name)
        slug_name = re.sub(r"\s+", "-", normalized_name)

        for key in (normalized_name, slug_name):
            if key in mapping:
                try:
                    value = float(mapping[key])
                    if value > 0:
                        return value
                except (TypeError, ValueError):
                    continue
        return base

    def _apify_destination_overlap_count(self, row: dict[str, Any], dest_name: str) -> int:
        dest_tokens = set(self._significant_tokens(dest_name))
        if not dest_tokens:
            return 0
        text_parts = [
            str(row.get("areaName") or ""),
            str(row.get("cityName") or ""),
            str(row.get("locationLabel") or ""),
            str(row.get("areaSlug") or ""),
            str(row.get("cityUrl") or ""),
            str(row.get("trailUrl") or ""),
        ]
        row_tokens = set(self._significant_tokens(" ".join(text_parts)))
        return len(dest_tokens & row_tokens)

    def _geocode_destination_for_apify(self, destination: str) -> tuple[float, float] | None:
        queries = [destination, f"{destination}, USA"]
        for query in queries:
            try:
                response = self._url_validator.session.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={
                        "q": query,
                        "format": "json",
                        "limit": 1,
                        "countrycodes": "us",
                    },
                    headers={"User-Agent": "RoadTripGenerator-URLDiscovery/1.0"},
                    timeout=10,
                )
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows, list) or not rows:
                    continue
                first = rows[0] if isinstance(rows[0], dict) else {}
                lat = float(first.get("lat"))
                lon = float(first.get("lon"))
                return lat, lon
            except Exception:
                continue
        return None

    @staticmethod
    def _build_apify_explore_start_url(lat: float, lon: float, *, within_miles: float) -> str:
        radius = max(1.0, float(within_miles))
        lat_delta = radius / 69.0
        lon_delta = radius / (69.172 * max(0.25, abs(cos(radians(lat)))))
        b_br_lat = lat - lat_delta
        b_tl_lat = lat + lat_delta
        b_br_lng = lon + lon_delta
        b_tl_lng = lon - lon_delta
        return (
            "https://www.alltrails.com/explore?"
            f"b_br_lat={b_br_lat:.6f}&"
            f"b_br_lng={b_br_lng:.6f}&"
            f"b_tl_lat={b_tl_lat:.6f}&"
            f"b_tl_lng={b_tl_lng:.6f}&"
            "a[]=hiking"
        )

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
            candidates = self._search_cached(full_query, count=10)
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
        if re.search(r"\b(hard|difficult|challenging|strenuous)\b", t):
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
        for candidate in candidates:
            if candidate == url:
                continue
            if not self._alltrails_slug_matches_item(candidate, item_name):
                continue
            ok, status, text = self._fetch_page_text(candidate, timeout=8)
            if not ok:
                # A blocked or inconclusive fetch is not verification that this
                # synthesized slug actually exists. Never promote a purely
                # templated guess as canonical on unverifiable evidence alone --
                # keep the original URL, which at least came from an actual
                # search/harvest hit rather than a name-token guess.
                continue
            lower_text = (text or "").lower()
            if any(marker in lower_text for marker in ALLTRAILS_404_MARKERS):
                continue
            if item_tokens and not self._text_matches_item_tokens(lower_text, item_tokens):
                continue
            return candidate

        return url

    @staticmethod
    def _strip_alltrails_tracking(url: str) -> str:
        parsed = urlparse(url)
        if "alltrails.com" not in (parsed.netloc or "").lower():
            return url
        cleaned = parsed._replace(query="", fragment="")
        return cleaned.geturl()

    # ── Restaurants — two-pass ───────────────────────────────────────────────

    def _discover_restaurants(self, ai: dict[str, Any], dest_name: str, dest_dates: str | None = None, dest: dict[str, Any] | None = None) -> None:
        restaurant_source_mode = str(
            getattr(self, "_restaurant_source", DEFAULT_RESTAURANT_SOURCE) or DEFAULT_RESTAURANT_SOURCE
        )
        lodging = dest.get("lodging") if isinstance(dest, dict) else None
        lodging_location = str(
            (lodging.get("location") or lodging.get("name") or "") if isinstance(lodging, dict) else ""
        ).strip()
        restaurants = ai.get("dinner_recommendations", [])
        if not isinstance(restaurants, list):
            restaurants = []

        if restaurant_source_mode == "direct_link_batch":
            restaurants = self._prioritize_direct_batch_restaurants(restaurants, dest_name, dest_dates, lodging_location=lodging_location)
            ai["dinner_recommendations"] = restaurants

        for rest in restaurants:
            rest_name = rest.get("name", "")
            restaurant_variants = _build_restaurant_query_variants(rest_name, dest_name)

            # Direct-batch rows are the primary source for restaurant links.
            # If a verified candidate is found here, keep it and do not override
            # with separately discovered alternatives.
            if restaurant_source_mode == "direct_link_batch":
                existing_url = str(rest.get("url", "") or "").strip()
                if existing_url:
                    cleaned_existing = self._retain_discovered_url(
                        existing_url,
                        rest_name,
                        dest_name,
                        allow_alltrails=False,
                        kind="restaurant",
                    )
                    preserved_existing = cleaned_existing
                    normalized_existing = self._normalize_restaurant_url(cleaned_existing)
                    if normalized_existing:
                        preserved_existing = normalized_existing
                    elif not self._is_google_maps_candidate_url(cleaned_existing):
                        preserved_existing = ""
                    if self._direct_batch_is_authoritative() and self._is_google_maps_candidate_url(preserved_existing):
                        preserved_existing = ""
                    if preserved_existing:
                        rest["url"] = preserved_existing
                        rest.pop("maps_url", None)
                        # This shortcut (URL already attached before this loop
                        # ran) otherwise skips the row-metadata merge the
                        # fresh-lookup branch below does, silently leaving
                        # rating/votes/cuisine/price empty even when the
                        # matched row had them (dipstick55 Theme D: badges
                        # and title decoration going missing inconsistently
                        # depending on which branch handled a given item).
                        rest.update(
                            self._direct_batch_row_quality_metadata_for_url(
                                self._get_restaurant_direct_batch_rows_for_destination(
                                    dest_name, str(dest_dates or ""), lodging_location=lodging_location
                                ),
                                preserved_existing,
                            )
                        )
                        self._log_decision(
                            kind="restaurant",
                            dest_name=dest_name,
                            item_name=rest_name,
                            reason="direct_batch_existing_url_preserved",
                            message="restaurant link preserved from direct-link batch row",
                            url=preserved_existing,
                        )
                        continue
                batch_url = self._search_restaurant_from_direct_batch(rest_name, dest_name, str(dest_dates or ""), lodging_location=lodging_location)
                if batch_url:
                    rest["url"] = batch_url
                    rest.pop("maps_url", None)
                    rest.update(
                        self._direct_batch_row_quality_metadata_for_url(
                            self._get_restaurant_direct_batch_rows_for_destination(
                                dest_name, str(dest_dates or ""), lodging_location=lodging_location
                            ),
                            batch_url,
                        )
                    )
                    self._log_decision(
                        kind="restaurant",
                        dest_name=dest_name,
                        item_name=rest_name,
                        reason="direct_batch_accepted",
                        message="restaurant link (direct-link batch)",
                        url=batch_url,
                    )
                    continue
                if self._direct_batch_is_authoritative():
                    rest["url"] = ""
                    rest.pop("maps_url", None)
                    self._log_decision(
                        kind="restaurant",
                        dest_name=dest_name,
                        item_name=rest_name,
                        reason="direct_batch_source_locked_no_match",
                        message="restaurant link omitted; authoritative direct-link batch had no usable result",
                    )
                    continue

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
                rest.pop("maps_url", None)
                self._log_decision(
                    kind="restaurant",
                    dest_name=dest_name,
                    item_name=rest_name,
                    reason="ai_candidate_accepted",
                    message="restaurant link (ai-candidate)",
                    url=ai_candidate_url,
                )
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
            rest["url"] = url or ""
            rest.pop("maps_url", None)
            if url:
                # Populate missing metadata from the search snippet before trying page fetch.
                winner = getattr(self, "_search_winner_snippets", {}).get(url, {})
                if winner:
                    snippet_text = " ".join(filter(None, [
                        str(winner.get("snippet", "") or ""),
                        str(winner.get("name", "") or ""),
                    ]))
                    self._populate_restaurant_from_snippet(rest, snippet_text)
            if not url:
                self._log_decision(
                    kind="restaurant",
                    dest_name=dest_name,
                    item_name=rest_name,
                    reason="maps_fallback_only",
                    message="restaurant link omitted; no canonical URL found",
                    url="",
                )
            else:
                self._log_decision(
                    kind="restaurant",
                    dest_name=dest_name,
                    item_name=rest_name,
                    reason="discovery_completed",
                    message="restaurant link",
                    url=url,
                )

        # Enrich any restaurant that has a valid URL but missing metadata.
        for rest in restaurants:
            self._maybe_upgrade_tripadvisor_restaurant_link(rest, dest_name)
            self._enrich_restaurant_metadata_from_url(rest)
            self._backfill_restaurant_metadata_from_available_text(rest)

    def _maybe_upgrade_tripadvisor_restaurant_link(self, rest: dict[str, Any], dest_name: str) -> None:
        """TripAdvisor should be the exception, not the default, for a named
        restaurant's primary link -- but TripAdvisor blocks automated fetches
        (403), so its page content can't be inspected for an official-site
        link the way a normal page can. Run one independent supplementary
        search for the restaurant's own site instead, and only swap the link
        when a genuinely different, non-aggregator domain is found and clears
        the normal retention checks; otherwise TripAdvisor is kept as-is.
        """
        if not bool(
            getattr(
                self,
                "_restaurant_prefer_official_site_over_tripadvisor",
                DEFAULT_RESTAURANT_PREFER_OFFICIAL_SITE_OVER_TRIPADVISOR,
            )
        ):
            return
        url = str(rest.get("url", "") or "").strip()
        if "tripadvisor." not in url.lower():
            return
        rest_name = str(rest.get("name", "") or "").strip()
        if not rest_name:
            return

        variants = [
            f'"{rest_name}" {dest_name} official website',
            f'"{rest_name}" {dest_name} restaurant menu',
        ]
        candidate = self._search_first(
            variants,
            item_name=rest_name,
            dest_name=dest_name,
            allow_alltrails=False,
        )
        if not candidate:
            return
        lower_candidate = candidate.lower()
        aggregator_markers = (
            "tripadvisor.",
            "yelp.",
            "facebook.",
            "instagram.",
            "opentable.",
            "google.com/maps",
            "google.com/search",
        )
        if any(marker in lower_candidate for marker in aggregator_markers):
            return
        cleaned = self._retain_discovered_url(
            candidate,
            rest_name,
            dest_name,
            allow_alltrails=False,
            kind="restaurant",
        )
        if not cleaned:
            return
        rest["url"] = cleaned
        rest.pop("maps_url", None)
        self._log_decision(
            kind="restaurant",
            dest_name=dest_name,
            item_name=rest_name,
            reason="tripadvisor_upgraded_to_official_site",
            message="restaurant link upgraded from TripAdvisor to official site",
            url=cleaned,
        )

    def _enrich_restaurant_metadata_from_url(self, rest: dict[str, Any]) -> None:
        url = str(rest.get("url", "") or "").strip()
        if not url or any(url.lower().startswith(p) for p in SAFE_FALLBACK_URL_PREFIXES):
            return
        needs_desc = not str(rest.get("description", "") or "").strip()
        needs_cuisine = not str(rest.get("cuisine", "") or "").strip()
        needs_price = not str(rest.get("price_range", "") or rest.get("price", "") or "").strip()
        if not (needs_desc or needs_cuisine or needs_price):
            return
        ok, _status, html = self._fetch_page_text(url, timeout=8)
        if not ok or not html:
            return
        meta = self._extract_restaurant_meta_from_html(html)
        if needs_desc and meta.get("description"):
            rest["description"] = meta["description"]
            logger.info("  Enriched description for '%s' from page", rest.get("name", ""))
        if needs_cuisine and meta.get("cuisine"):
            rest["cuisine"] = meta["cuisine"]
            logger.info("  Enriched cuisine for '%s' from page", rest.get("name", ""))
        if needs_price and meta.get("price_range"):
            rest["price_range"] = meta["price_range"]
            logger.info("  Enriched price_range for '%s' from page", rest.get("name", ""))
        still_needs_cuisine = not str(rest.get("cuisine", "") or "").strip()
        still_needs_price = not str(rest.get("price_range", "") or rest.get("price", "") or "").strip()
        if still_needs_cuisine or still_needs_price:
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
            title_text = re.sub(r"\s+", " ", str(title_match.group(1) if title_match else "")).strip()
            text_blob = " ".join(
                part
                for part in (
                    title_text,
                    str(meta.get("description", "") or "").strip(),
                    str(rest.get("description", "") or "").strip(),
                    str(rest.get("name", "") or "").strip(),
                )
                if part
            )
            inferred = self._infer_restaurant_metadata_from_text_and_url(text_blob, url)
            if still_needs_cuisine and inferred.get("cuisine"):
                rest["cuisine"] = str(inferred.get("cuisine") or "").strip()
            if still_needs_price and inferred.get("price_range"):
                rest["price_range"] = str(inferred.get("price_range") or "").strip()

    def _populate_restaurant_from_snippet(self, rest: dict[str, Any], snippet: str) -> None:
        """Populate missing restaurant fields from a search-result snippet (plain text)."""
        text = str(snippet or "").strip()
        if not text:
            return
        name = str(rest.get("name", "") or "").strip()
        if not str(rest.get("price_range", "") or "").strip():
            # Match standalone $-strings surrounded by spaces, dots, or bullets
            m = re.search(r'(?<![^\s·,])(\${1,4})(?![^\s·,\d])', text)
            if m:
                rest["price_range"] = m.group(1)
        if not str(rest.get("cuisine", "") or "").strip():
            # Cuisine words often appear between · separators in search result titles/snippets
            # e.g. "Painted Pony · $$$ · Southwestern · American"
            parts = [p.strip() for p in re.split(r"·|,", text)]
            for part in parts:
                # Skip parts that are prices, ratings, or empty
                if not part or re.fullmatch(r'[\$\d\.\s★*]+', part):
                    continue
                if len(part.split()) <= 3 and part[0].isupper() and "review" not in part.lower():
                    rest["cuisine"] = part
                    break
        if not str(rest.get("description", "") or "").strip():
            # Use the snippet itself as description if it's meaningful
            if len(text) > 25 and name.lower() not in text[:25].lower():
                rest["description"] = text[:300]

    @classmethod
    def _backfill_restaurant_metadata_from_available_text(cls, rest: dict[str, Any]) -> None:
        """Infer missing cuisine/price fields from available restaurant text and URLs."""
        if not isinstance(rest, dict):
            return

        needs_cuisine = not str(rest.get("cuisine", "") or "").strip()
        needs_price = not str(rest.get("price_range", "") or rest.get("price", "") or "").strip()
        if not (needs_cuisine or needs_price):
            return

        text_blob = " ".join(
            part
            for part in (
                str(rest.get("name", "") or "").strip(),
                str(rest.get("description", "") or "").strip(),
                str(rest.get("practical_note", "") or "").strip(),
            )
            if part
        )
        candidate_url = str(rest.get("url", "") or rest.get("maps_url", "") or "").strip()
        inferred = cls._infer_restaurant_metadata_from_text_and_url(text_blob, candidate_url)

        if needs_cuisine and inferred.get("cuisine"):
            rest["cuisine"] = str(inferred.get("cuisine") or "").strip()
        if needs_price and inferred.get("price_range"):
            rest["price_range"] = str(inferred.get("price_range") or "").strip()

    @staticmethod
    def _extract_restaurant_meta_from_html(html: str) -> dict[str, str]:
        """Best-effort extraction of description, cuisine, and price_range from raw HTML."""
        result: dict[str, str] = {}
        text = str(html or "")
        if not text:
            return result

        # JSON-LD structured data (most reliable source)
        for ld_match in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            text,
            re.DOTALL | re.IGNORECASE,
        ):
            try:
                data = json.loads(ld_match.group(1))
                if not isinstance(data, dict):
                    continue
                if not result.get("cuisine"):
                    raw = data.get("servesCuisine", "")
                    if isinstance(raw, list):
                        raw = ", ".join(str(c) for c in raw[:2])
                    cuisine = str(raw or "").strip()
                    if cuisine:
                        result["cuisine"] = cuisine
                if not result.get("price_range"):
                    pr = str(data.get("priceRange", "") or "").strip()
                    if pr:
                        result["price_range"] = pr
                if not result.get("description"):
                    desc = str(data.get("description", "") or "").strip()
                    if len(desc) > 20:
                        result["description"] = desc[:300]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
            if result.get("cuisine") and result.get("price_range") and result.get("description"):
                break

        # OG / meta description fallback
        if not result.get("description"):
            for pattern in (
                r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\']([^"\']{20,})["\']',
                r'<meta[^>]+content=["\']([^"\']{20,})["\'][^>]+(?:name|property)=["\'](?:description|og:description)["\']',
            ):
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    result["description"] = m.group(1).strip()[:300]
                    break

        # Price range pattern in raw text as last resort
        if not result.get("price_range"):
            m = re.search(r'(?:price\s*range|price)[^\$\n]{0,20}(\${1,4})(?!\d)', text, re.IGNORECASE)
            if m:
                result["price_range"] = m.group(1)

        return result

    @staticmethod
    def _is_google_maps_domain(netloc: str) -> bool:
        host = (netloc or "").strip().lower()
        if not host:
            return False
        host = host.split(":", 1)[0]
        return (
            host == "maps.app.goo.gl"
            or host == "maps.google.com"
            or host.endswith(".google.com")
            or host.endswith(".google.co")
        )

    @classmethod
    def _is_google_maps_candidate_url(cls, url: str | None) -> bool:
        parsed = urlparse(str(url or "").strip())
        if not cls._is_google_maps_domain(parsed.netloc):
            return False
        host_l = (parsed.netloc or "").lower()
        path_l = (parsed.path or "").lower()
        return (
            "maps" in path_l
            or path_l.startswith("/place/")
            or ("maps.google.com" in host_l and bool(parsed.query))
            or "maps.app.goo.gl" in (parsed.netloc or "").lower()
        )

    def _resolve_google_maps_final_url(self, url: str) -> str:
        candidate = str(url or "").strip()
        if not candidate:
            return ""

        cache = getattr(self, "_maps_url_resolution_cache", None)
        if isinstance(cache, dict) and candidate in cache:
            return cache[candidate]

        fetch_cache = getattr(self, "_fetch_final_url_cache", None)
        if isinstance(fetch_cache, dict):
            cached_final = str(fetch_cache.get(candidate, "") or "").strip()
            if cached_final:
                if isinstance(cache, dict):
                    cache[candidate] = cached_final
                return cached_final

        try:
            resp = self._url_validator.session.get(candidate, timeout=8)
            final_url = str(getattr(resp, "url", None) or candidate).strip()
        except Exception:
            final_url = candidate

        if isinstance(fetch_cache, dict) and final_url and final_url != candidate:
            fetch_cache[candidate] = final_url
        if isinstance(cache, dict):
            cache[candidate] = final_url
        return final_url

    def _normalize_restaurant_url(self, url: str | None) -> str:
        if not url:
            return ""
        normalized = str(url).strip()
        lower = normalized.lower()
        if "google.com/maps/dir/" in lower or "maps.google.com/maps/dir/" in lower:
            return ""
        parsed = urlparse(normalized)

        if self._is_google_maps_candidate_url(normalized):
            resolved = self._resolve_google_maps_final_url(normalized)
            parsed = urlparse(resolved)
            path_l = (parsed.path or "").lower()
            query_l = (parsed.query or "").lower()

            # Query-style map/search pages remain ambiguous and should not be canonical.
            if path_l.startswith("/maps/search") or path_l.startswith("/maps/dir"):
                return ""
            if path_l.startswith("/maps/@"):
                return ""

            # Deterministic place-target variants are acceptable.
            if path_l.startswith("/maps/place/"):
                if "/data=" in path_l or "cid=" in query_l or "ftid=" in query_l:
                    cleaned = resolved.split("#", 1)[0]
                    if self._looks_synthetic_google_maps_place_url(cleaned):
                        return ""
                    return cleaned
                return ""
            if path_l.startswith("/place/"):
                cleaned = resolved.split("#", 1)[0]
                if self._looks_synthetic_google_maps_place_url(cleaned):
                    return ""
                return cleaned
            if path_l.rstrip("/") == "/maps":
                if "cid=" in query_l or "ftid=" in query_l or "place_id:" in query_l:
                    cleaned = resolved.split("#", 1)[0]
                    if self._looks_synthetic_google_maps_place_url(cleaned):
                        return ""
                    return cleaned
                return ""

            # Bare maps domains or generic query-style endpoints are ambiguous.
            if "q=" in query_l and "place_id:" not in query_l:
                return ""
            if path_l in {"", "/"}:
                return ""

            return resolved.split("#", 1)[0]

        if "maps.google.com" in parsed.netloc.lower() and parsed.query:
            query_l = parsed.query.lower()
            if "q=" in query_l:
                return ""
        return normalized

    @staticmethod
    def _looks_synthetic_google_maps_place_url(url: str) -> bool:
        lower = str(url or "").lower()
        if not lower:
            return False
        placeholder_tokens = (
            "0x1234567890abcdef",
            "0xabcdef",
            "1td_abcde",
            "1tc_xyz",
            "e0e0e0e0e0e0e0e0",
            "8e8e8e8e8e8e8e8e",
            "5e5e5e5e5e5e5e5e",
        )
        if any(token in lower for token in placeholder_tokens):
            return True
        if "/g/1tc_" in lower or "/g/1td_" in lower:
            return True
        if re.search(r"0x(?:([0-9a-f]{2})\\1{5,}|([0-9a-f]{4})\\2{2,})", lower):
            return True
        if ":0x0" in lower:
            return True
        if re.search(r"1s0x[0-9a-f]{8,}:0x0\b", lower):
            return True
        parsed = urlparse(str(url or ""))
        cid = ""
        try:
            cid = parse_qs(parsed.query).get("cid", [""])[0].strip()
        except Exception:
            cid = ""
        # Real Google CID values are long decimal identifiers; short numeric
        # placeholders (for example 1234567890) are synthetic and unreliable.
        if cid:
            if not cid.isdigit():
                return True
            if len(cid) < 16 or len(cid) > 20:
                return True
        return False

    @staticmethod
    def _has_unescaped_whitespace(url: str | None) -> bool:
        return any(ch.isspace() for ch in str(url or ""))

    @classmethod
    def _is_deterministic_google_maps_place_url(cls, url: str | None) -> bool:
        parsed = urlparse(str(url or "").strip())
        if not cls._is_google_maps_domain(parsed.netloc):
            return False
        path_l = (parsed.path or "").lower()
        query_l = (parsed.query or "").lower()

        if path_l.startswith("/maps/place/"):
            return ("/data=" in path_l) or ("cid=" in query_l) or ("ftid=" in query_l)
        if path_l.startswith("/place/"):
            return True
        if path_l.rstrip("/") == "/maps":
            return ("cid=" in query_l) or ("ftid=" in query_l) or ("place_id:" in query_l)
        return False

    def _matches_site_filter(self, candidate_url: str, site_filter: str | None) -> bool:
        if not site_filter:
            return True
        if site_filter == "google.com/maps":
            return self._is_google_maps_candidate_url(candidate_url)
        return site_filter in candidate_url

    @classmethod
    def _restaurant_maps_query_text(cls, rest_name: str, dest_name: str) -> str:
        name = str(rest_name or "").strip()
        dest = str(dest_name or "").strip()
        query = cls._maps_fallback_query_text(name, dest)

        # Keep destination context for restaurant lookups unless the candidate
        # name is already location-qualified (e.g., "Tropic Junction").
        if name and dest and dest.lower() not in query.lower() and not cls._looks_location_qualified(name):
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
                    if filtered_selected and not self._same_alltrails_trail(candidate_url, filtered_selected):
                        self._log_decision(
                            kind=kind,
                            dest_name=dest_name,
                            item_name=item_name,
                            reason="alltrails_mismatched_filtered_selection",
                            message=f"{kind} alltrails candidate mismatched filtered selection",
                            url=candidate_url,
                            level=logging.WARNING,
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
                    self._log_decision(
                        kind=kind,
                        dest_name=dest_name,
                        item_name=item_name,
                        reason="ai_candidate_downgraded_confidence_gate",
                        message=f"{kind} ai-candidate downgraded by confidence gate",
                        url=cleaned,
                    )
                    cleaned = ""
            if cleaned:
                self._record_url_recommendation_source(cleaned, "ai_candidate")
                return cleaned

            self._log_decision(
                kind=kind,
                dest_name=dest_name,
                item_name=item_name,
                reason="ai_candidate_rejected",
                message=f"{kind} ai-candidate rejected",
                url=candidate_url,
            )

        return None

    @staticmethod
    def _direct_batch_row_name(row: dict[str, Any] | None) -> str:
        if not row:
            return ""
        return str(row.get("name", "") or row.get("title", "") or "").strip()

    @staticmethod
    def _primary_list_target_count(current_count: int, fallback_count: int, available_count: int) -> int:
        # The configured item count is a floor, not a cap that gets discarded the
        # moment the AI-generated list happens to be non-empty. Locking the target
        # to whatever (often sparse) count the AI produced silently discarded
        # richer, higher-quality batch candidates even when the config explicitly
        # asked for more (e.g. restaurant_direct_batch_item_count: 8).
        if fallback_count > 0:
            configured_target = min(max(1, fallback_count), max(1, available_count))
            return max(current_count, configured_target)
        if current_count > 0:
            return current_count
        return max(0, available_count)

    def _build_primary_items_from_direct_batch(
        self,
        *,
        rows: list[dict[str, Any]],
        existing_items: list[dict[str, Any]],
        target_count: int,
        fallback_description: str,
        default_type: str | None = None,
        dest_name: str = "",
    ) -> list[dict[str, Any]]:
        if target_count <= 0:
            return [dict(item) for item in existing_items if isinstance(item, dict)]

        normalized_existing = [dict(item) for item in existing_items if isinstance(item, dict)]
        used_existing: set[int] = set()
        merged: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        for row in rows:
            row_name = self._direct_batch_row_name(row)
            if not row_name:
                continue
            key = row_name.lower()
            if key in seen_names:
                continue
            row_url = str(row.get("url", "") or "").strip()
            if self._is_generic_listing_title(row_name) or (
                row_url and self._is_obviously_generic_url(row_url.lower())
            ):
                # A listing/search-result row (title and/or URL identify a
                # generic page like a TripAdvisor "10 Best Restaurants" list,
                # not a specific place) must never be used to synthesize a new
                # item, nor overwrite an existing item's name/url below.
                continue

            matched_idx: int | None = None
            for idx, item in enumerate(normalized_existing):
                if idx in used_existing:
                    continue
                item_name = str(item.get("name", "") or item.get("title", "") or "").strip()
                if item_name and self._direct_batch_row_matches_item(row, item_name, dest_name):
                    matched_idx = idx
                    break

            if matched_idx is not None:
                merged_item = dict(normalized_existing[matched_idx])
                used_existing.add(matched_idx)
            else:
                merged_item = {}

            merged_item["name"] = row_name
            if row_url and not str(merged_item.get("url", "") or "").strip():
                merged_item["url"] = row_url
            for key in (
                "detour_distance_miles",
                "detour_time_minutes",
                "practical_note",
                "cuisine",
                "price_range",
                "price",
                "reserve_recommended",
                "rating",
                "raw_rating",
                "votes",
            ):
                if key in row and row.get(key) not in (None, ""):
                    merged_item[key] = row.get(key)
            if default_type and not str(merged_item.get("type", "") or "").strip():
                merged_item["type"] = default_type
            row_desc = self._sanitize_direct_batch_description_text(
                str(row.get("description", "") or row.get("practical_note", "") or row.get("snippet", "") or "")
            )
            existing_desc = str(merged_item.get("description", "") or "").strip()
            if row_desc and row_desc.lower() != row_name.lower():
                if not existing_desc or existing_desc == fallback_description:
                    merged_item["description"] = row_desc
            elif not existing_desc:
                merged_item["description"] = fallback_description

            merged.append(merged_item)
            seen_names.add(key)
            if len(merged) >= target_count:
                break

        for idx, item in enumerate(normalized_existing):
            if len(merged) >= target_count:
                break
            if idx in used_existing:
                continue
            item_name = str(item.get("name", "") or item.get("title", "") or "").strip()
            if item_name and item_name.lower() in seen_names:
                continue
            merged.append(dict(item))

        return merged

    def _prioritize_direct_batch_restaurants(
        self,
        restaurants: list[dict[str, Any]],
        dest_name: str,
        dest_dates: str | None,
        lodging_location: str = "",
    ) -> list[dict[str, Any]]:
        try:
            rows = self._get_restaurant_direct_batch_rows_for_destination(dest_name, str(dest_dates or ""), lodging_location=lodging_location)
        except AttributeError:
            return restaurants
        if not rows:
            return restaurants
        target_count = self._primary_list_target_count(
            len(restaurants),
            int(getattr(self, "_restaurant_direct_batch_item_count", DEFAULT_RESTAURANT_DIRECT_BATCH_ITEM_COUNT) or DEFAULT_RESTAURANT_DIRECT_BATCH_ITEM_COUNT),
            len(rows),
        )
        merged = self._build_primary_items_from_direct_batch(
            rows=rows,
            existing_items=restaurants,
            target_count=target_count,
            fallback_description="Locally surfaced dinner option.",
            dest_name=dest_name,
        )
        return self._backfill_restaurant_metadata_from_existing(merged, restaurants)

    @staticmethod
    def _infer_destination_day_count(dates: str) -> int:
        """Best-effort day count from a manifest date-range string, e.g.
        'October 19-21, 2026' -> 3, 'October 17, 2026' -> 1."""
        text = str(dates or "").replace("–", "-").replace("—", "-")
        m = re.search(r"[A-Za-z]+\s+(\d{1,2})(?:\s*-\s*(\d{1,2}))?(?:,\s*\d{4})?", text)
        if m:
            start = int(m.group(1))
            end = int(m.group(2) or m.group(1))
            if end >= start:
                return max(1, end - start + 1)
            return 1
        iso = re.findall(r"(\d{4}-\d{2}-\d{2})", text)
        if len(iso) >= 2:
            try:
                from datetime import datetime as _dt
                d0 = _dt.strptime(iso[0], "%Y-%m-%d")
                d1 = _dt.strptime(iso[1], "%Y-%m-%d")
                if d1 >= d0:
                    return max(1, (d1 - d0).days + 1)
            except ValueError:
                return 1
        return 1

    def _prioritize_direct_batch_attractions(
        self,
        attractions: list[dict[str, Any]],
        dest_name: str,
        dest_dates: str | None,
        seed_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        try:
            rows = self._get_attraction_direct_batch_rows_for_destination(dest_name, str(dest_dates or ""))
        except AttributeError:
            return attractions
        if not rows:
            return attractions

        # Existing AI-generated attractions (including seeds) are never evicted --
        # unlike restaurants/en-route stops, attractions carry user-requested seed
        # anchors that must survive regardless of batch richness. This function
        # only ever adds new, distinct candidates on top.
        existing = [dict(item) for item in attractions if isinstance(item, dict)]
        existing_keys = {
            re.sub(r"[^a-z0-9]+", " ", str(item.get("name", "") or "").lower()).strip()
            for item in existing
        }

        day_count = self._infer_destination_day_count(dest_dates or "")
        items_per_day = int(
            getattr(self, "_attraction_direct_batch_items_per_day", DEFAULT_ATTRACTION_DIRECT_BATCH_ITEMS_PER_DAY)
            or DEFAULT_ATTRACTION_DIRECT_BATCH_ITEMS_PER_DAY
        )
        target_total = max(len(existing), items_per_day * day_count)
        additional_slots = max(0, target_total - len(existing))
        if additional_slots <= 0:
            return attractions

        candidates: list[tuple[float, int, str, dict[str, Any]]] = []
        for order, row in enumerate(rows):
            row_name = self._direct_batch_row_name(row)
            if not row_name:
                continue
            key = re.sub(r"[^a-z0-9]+", " ", row_name.lower()).strip()
            if not key or key in existing_keys:
                continue
            if self._candidate_mentions_conflicting_destination(row, dest_name):
                continue
            # Defensive: the harvest prompt excludes hikes, but guard against a
            # trail-like row slipping through so it doesn't duplicate the
            # separately-sourced AllTrails batch.
            if self._is_trail_like_attraction(row_name, "attraction", str(row.get("description", "") or "")):
                continue
            rating = row.get("rating")
            try:
                rating_value = float(rating) if rating is not None else -1.0
            except (TypeError, ValueError):
                rating_value = -1.0
            candidates.append((rating_value, order, key, row))

        # Highest rating first; missing ratings (-1.0) and ties keep harvest order.
        candidates.sort(key=lambda entry: (-entry[0], entry[1]))

        added: list[dict[str, Any]] = []
        seen_new_keys: set[str] = set()
        for _rating, _order, key, row in candidates:
            if len(added) >= additional_slots:
                break
            if key in seen_new_keys:
                continue
            row_name = self._direct_batch_row_name(row)
            row_url = str(row.get("url", "") or "").strip()
            new_item: dict[str, Any] = {"name": row_name, "type": "attraction"}
            if row_url:
                new_item["url"] = row_url
            row_desc = self._sanitize_direct_batch_description_text(
                str(row.get("description", "") or row.get("practical_note", "") or "")
            )
            if row_desc and not self._is_metadata_only_residual_text(row_desc, name=row_name):
                new_item["description"] = row_desc
            added.append(new_item)
            seen_new_keys.add(key)

        if not added:
            return attractions
        return existing + added

    def _prioritize_direct_batch_trails(
        self,
        attractions: list[dict[str, Any]],
        dest_name: str,
        dest_dates: str | None,
        seed_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Trail-specific counterpart to _prioritize_direct_batch_attractions.

        Trails need their own injection pass because _discover_attractions only
        ever looks up a URL for a trail name the AI already generated -- with no
        mechanism to pull additional hikes in from the AllTrails direct-batch
        harvest, a rich batch of candidates was going entirely unused whenever
        the AI happened to generate few (or zero) trail-type attractions.
        """
        try:
            rows = self._get_alltrails_direct_batch_rows_for_destination(dest_name, str(dest_dates or ""))
        except AttributeError:
            return attractions
        if not rows:
            return attractions

        existing = [dict(item) for item in attractions if isinstance(item, dict)]
        existing_keys = {
            re.sub(r"[^a-z0-9]+", " ", str(item.get("name", "") or "").lower()).strip()
            for item in existing
        }
        existing_trail_count = sum(
            1
            for item in existing
            if self._is_trail_like_attraction(
                str(item.get("name", "") or ""),
                str(item.get("type", "attraction") or "attraction").lower(),
                self._attraction_trail_context(item),
            )
        )

        day_count = self._infer_destination_day_count(dest_dates or "")
        items_per_day = int(
            getattr(self, "_trail_direct_batch_items_per_day", DEFAULT_TRAIL_DIRECT_BATCH_ITEMS_PER_DAY)
            or DEFAULT_TRAIL_DIRECT_BATCH_ITEMS_PER_DAY
        )
        target_total = max(existing_trail_count, items_per_day * day_count)
        additional_slots = max(0, target_total - existing_trail_count)
        if additional_slots <= 0:
            return attractions

        max_miles = float(getattr(self, "_max_trail_miles", DEFAULT_MAX_TRAIL_MILES) or 0)
        slug_denylist = getattr(self, "_alltrails_slug_denylist", frozenset())

        candidates: list[tuple[float, int, str, dict[str, Any], str]] = []
        for order, row in enumerate(rows):
            row_name = self._direct_batch_row_name(row)
            if not row_name:
                continue
            key = re.sub(r"[^a-z0-9]+", " ", row_name.lower()).strip()
            if not key or key in existing_keys:
                continue
            raw_url = str(row.get("url", "") or "").strip()
            if not raw_url or not self._is_alltrails_trail_url(raw_url):
                continue
            if self._candidate_mentions_conflicting_destination(row, dest_name):
                continue
            normalized_url = self._strip_alltrails_tracking(raw_url)
            if self._alltrails_slug_has_numbered_suffix(normalized_url):
                continue
            if not self._alltrails_slug_matches_item(normalized_url, row_name):
                continue
            slug = urlparse(normalized_url).path.rsplit("/", 1)[-1].lower()
            if slug in slug_denylist:
                continue
            if self._has_alltrails_closure_marker(self._candidate_text_blob(row)):
                continue
            meta = self._extract_alltrails_candidate_metadata(row)
            miles = meta.get("miles")
            if miles is not None and max_miles > 0 and float(miles) > max_miles + 0.15:
                continue
            rating = row.get("rating") if row.get("rating") is not None else meta.get("rating")
            try:
                rating_value = float(rating) if rating is not None else -1.0
            except (TypeError, ValueError):
                rating_value = -1.0
            candidates.append((rating_value, order, key, row, normalized_url))

        # Highest rating first; missing ratings (-1.0) and ties keep harvest order.
        candidates.sort(key=lambda entry: (-entry[0], entry[1]))

        added: list[dict[str, Any]] = []
        seen_new_keys: set[str] = set()
        for _rating, _order, key, row, normalized_url in candidates:
            if len(added) >= additional_slots:
                break
            if key in seen_new_keys:
                continue
            row_name = self._direct_batch_row_name(row)
            new_item: dict[str, Any] = {"name": row_name, "type": "hike", "url": normalized_url}
            # Found 2026-08-16 (dipstick58): unlike
            # _prioritize_direct_batch_attractions just above, this trail path
            # never copied the harvested row's description/practical_note into
            # the new item -- every trail injected here rendered with a
            # permanently empty teaser regardless of whether the direct-batch
            # HTML actually contained a descriptive note (it usually did; see
            # the row parsing in _direct_batch_rows_from_html). That alone
            # accounted for 10/10 of the empty attraction/trail teasers in a
            # real run (0 attractions were affected, only trails), so it's
            # copied here the same way the attraction path already does.
            row_desc = self._sanitize_direct_batch_description_text(
                str(row.get("description", "") or row.get("practical_note", "") or "")
            )
            if row_desc and not self._is_metadata_only_residual_text(row_desc, name=row_name):
                new_item["description"] = row_desc
            added.append(new_item)
            seen_new_keys.add(key)

        if not added:
            return attractions
        return existing + added

    @staticmethod
    def _normalized_name_tokens_for_restaurant_match(name: str) -> set[str]:
        raw_tokens = re.findall(r"[a-z0-9]+", str(name or "").lower())
        stop = {
            "restaurant", "restaurante", "cafe", "grill", "kitchen", "bar", "eatery",
            "the", "and", "co", "company", "llc",
        }
        out: set[str] = set()
        for token in raw_tokens:
            if token in stop or len(token) <= 2:
                continue
            out.add(token)
        return out

    @staticmethod
    def _restaurant_metadata_missing(item: dict[str, Any]) -> bool:
        if not isinstance(item, dict):
            return True
        return not any(
            str(item.get(key, "") or "").strip()
            for key in ("cuisine", "price_range", "price")
        ) and not bool(item.get("reserve_recommended", False))

    def _backfill_restaurant_metadata_from_existing(
        self,
        merged_items: list[dict[str, Any]],
        existing_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not merged_items or not existing_items:
            return merged_items

        existing_norm: list[dict[str, Any]] = [dict(item) for item in existing_items if isinstance(item, dict)]
        for merged in merged_items:
            if not isinstance(merged, dict) or not self._restaurant_metadata_missing(merged):
                continue
            target_name = str(merged.get("name", "") or "").strip()
            if not target_name:
                continue
            target_tokens = self._normalized_name_tokens_for_restaurant_match(target_name)
            if not target_tokens:
                continue

            best_match: dict[str, Any] | None = None
            best_score = 0.0
            for source in existing_norm:
                source_name = str(source.get("name", "") or source.get("title", "") or "").strip()
                if not source_name:
                    continue
                source_tokens = self._normalized_name_tokens_for_restaurant_match(source_name)
                if not source_tokens:
                    continue
                overlap = len(target_tokens & source_tokens)
                if overlap <= 0:
                    continue
                union = len(target_tokens | source_tokens)
                score = overlap / union if union else 0.0
                if score > best_score:
                    best_score = score
                    best_match = source

            # Avoid overly loose joins; require decent token overlap.
            if not best_match or best_score < 0.45:
                continue

            for key in ("cuisine", "price_range", "price", "reserve_recommended"):
                if key in merged and merged.get(key) not in (None, "", False):
                    continue
                source_val = best_match.get(key)
                if source_val in (None, ""):
                    continue
                merged[key] = source_val

        return merged_items

    def _prioritize_direct_batch_en_route_stops(
        self,
        stops: list[dict[str, Any]],
        dest_name: str,
        dest_dates: str | None,
        origin_name: str,
    ) -> list[dict[str, Any]]:
        try:
            rows = self._get_en_route_direct_batch_rows_for_destination(dest_name, str(dest_dates or ""), origin_name)
        except AttributeError:
            return stops
        if not rows:
            return stops
        # Preserve legacy behavior when AI yields no en-route ideas:
        # keep a compact shortlist instead of surfacing the entire batch.
        if not stops:
            target_count = min(4, len(rows))
        else:
            target_count = self._primary_list_target_count(
                len(stops),
                int(getattr(self, "_en_route_direct_batch_item_count", DEFAULT_EN_ROUTE_DIRECT_BATCH_ITEM_COUNT) or DEFAULT_EN_ROUTE_DIRECT_BATCH_ITEM_COUNT),
                len(rows),
            )
        merged = self._build_primary_items_from_direct_batch(
            rows=rows,
            existing_items=stops,
            target_count=target_count,
            fallback_description="Optional stop for the inbound transfer leg.",
            dest_name=dest_name,
        )
        return self._dedupe_en_route_stops_by_specificity(merged)

    def _normalize_url_for_item_dedupe(self, url: str) -> str:
        candidate = str(url or "").strip()
        if not candidate:
            return ""
        normalized = self._normalize_direct_batch_authoritative_url(candidate)
        if not normalized:
            return ""
        if self._is_google_maps_candidate_url(normalized):
            parsed = urlparse(normalized)
            params = parse_qs(parsed.query or "")
            query = (
                (params.get("query", [""])[0] or "")
                or (params.get("q", [""])[0] or "")
            ).strip()
            if query:
                return f"maps-query:{query.lower()}"
        return normalized.lower()

    @staticmethod
    @staticmethod
    def _is_generic_en_route_stop_title(name: str) -> bool:
        text = str(name or "").strip().lower()
        if not text:
            return True
        if len(text) < 6:
            return True
        generic_patterns = (
            r"\b(top|best)\b.*\b(stops?|places?|stopovers?)\b",
            r"\bstopovers?\b",
            r"\bthings\s+to\s+do\b",
            r"\bitinerary\b",
            r"\broad\s*trip\b",
            r"\bstops?\s+along\b",
            r"\bscenic\s+drive\s+from\b.*\bto\b",
            r"\bscenic\s+route\b",
            r"\broute\s+option\b",
            r"\bscenic\s+stops?\b",
            r"\bstops?\s+on\s+the\s+(?:drive|route|road|way)\b",
            r"\bdriving?\s+from\b.*\bto\b",
        )
        return any(re.search(pattern, text) for pattern in generic_patterns)

    @staticmethod
    def _extract_named_stops_from_description(description: str) -> list[str]:
        """Extract specific named place candidates from a list-style en-route description."""
        text = str(description or "").strip()
        if not text or len(text) < 8:
            return []
        if ": " in text:
            text = text.split(": ", 1)[1]
        # Remove parenthetical annotations before splitting so decimal points
        # inside parens (e.g. "4.6 stars") don't break sentence detection.
        text = re.sub(r"\([^)]*\)", "", text)
        # Normalise common abbreviations so "Mt. Carmel" isn't split mid-name.
        text = re.sub(r"\b(Mt|St|Dr|Hwy|Rte|Rd|Blvd|Ave|Ft|Pt)\.\s+", r"\1 ", text)
        skip_starts = {
            "avoids", "no ", "note:", "skip", "google", "all ", "each ",
            "these", "this ", "also ", "and ", "the ", "a ", "an ",
            "includes", "including", "see ", "visit ", "check", "with ",
            "from ", "detour", "quick",
        }
        names: list[str] = []
        for raw in re.split(r"\.\s+|;\s*", text):
            frag = raw.strip()
            if not frag or len(frag) < 5:
                continue
            clean = frag.split(",")[0].strip()
            if len(clean) < 5 or len(clean.split()) < 2:
                continue
            lower_clean = clean.lower()
            if any(lower_clean.startswith(w) for w in skip_starts):
                continue
            if not clean[0].isupper():
                continue
            if not any(n.lower() == lower_clean for n in names):
                names.append(clean)
        return names

    def _dedupe_en_route_stops_by_specificity(self, stops: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not stops:
            return stops

        def _score(name: str) -> tuple[int, int, int]:
            generic_penalty = 0 if not self._is_generic_en_route_stop_title(name) else -100
            token_count = len(re.findall(r"[a-z0-9]+", (name or "").lower()))
            char_count = len((name or "").strip())
            return (generic_penalty, token_count, char_count)

        best_by_url: dict[str, dict[str, Any]] = {}
        ordered_without_url: list[dict[str, Any]] = []
        seen_names_without_url: set[str] = set()

        for raw in stops:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            name = str(item.get("name", "") or item.get("title", "") or "").strip()
            if not name:
                continue
            item["name"] = name
            url_key = self._normalize_url_for_item_dedupe(str(item.get("url", "") or ""))

            if url_key:
                existing = best_by_url.get(url_key)
                if existing is None:
                    best_by_url[url_key] = item
                    continue
                existing_name = str(existing.get("name", "") or existing.get("title", "") or "").strip()
                if _score(name) > _score(existing_name):
                    best_by_url[url_key] = item
                continue

            lowered_name = name.lower()
            if lowered_name in seen_names_without_url:
                continue
            if self._is_generic_en_route_stop_title(name):
                continue
            seen_names_without_url.add(lowered_name)
            ordered_without_url.append(item)

        result: list[dict[str, Any]] = []
        emitted_name_keys: set[str] = set()
        for raw in stops:
            if not isinstance(raw, dict):
                continue
            url_key = self._normalize_url_for_item_dedupe(str(raw.get("url", "") or ""))
            if url_key:
                chosen = best_by_url.get(url_key)
                if not chosen:
                    continue
                chosen_name_key = str(chosen.get("name", "") or "").strip().lower()
                if chosen_name_key and chosen_name_key not in emitted_name_keys:
                    result.append(chosen)
                    emitted_name_keys.add(chosen_name_key)
                best_by_url.pop(url_key, None)

        for item in ordered_without_url:
            name_key = str(item.get("name", "") or "").strip().lower()
            if name_key and name_key not in emitted_name_keys:
                result.append(item)
                emitted_name_keys.add(name_key)

        return result

    @staticmethod
    def _extract_en_route_detour_minutes_from_text(text: str) -> int | None:
        t = str(text or "").lower()
        if not t:
            return None
        hour_match = re.search(r"(\d+)\s*(?:hr|hrs|hour|hours)", t)
        min_match = re.search(r"(\d+)\s*(?:m|min|mins|minute|minutes)\b", t)
        if not hour_match and not min_match:
            return None
        total = 0
        if hour_match:
            total += int(hour_match.group(1)) * 60
        if min_match:
            total += int(min_match.group(1))
        return total if total > 0 else None

    @staticmethod
    def _extract_en_route_detour_miles_from_text(text: str) -> float | None:
        t = str(text or "").lower()
        if not t:
            return None
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:mi|mile|miles)\b", t)
        if not m:
            return None
        try:
            miles = float(m.group(1))
        except (TypeError, ValueError):
            return None
        return miles if miles > 0 else None

    def _en_route_stop_detour_metrics(self, stop: dict[str, Any]) -> tuple[float | None, int | None]:
        miles: float | None = None
        minutes: int | None = None

        raw_miles = stop.get("detour_distance_miles") if isinstance(stop, dict) else None
        raw_minutes = stop.get("detour_time_minutes") if isinstance(stop, dict) else None

        try:
            parsed_miles = float(raw_miles)
            if parsed_miles > 0:
                miles = parsed_miles
        except (TypeError, ValueError):
            miles = None

        try:
            parsed_minutes = int(raw_minutes)
            if parsed_minutes > 0:
                minutes = parsed_minutes
        except (TypeError, ValueError):
            minutes = None

        blob = " ".join(
            [
                str(stop.get("name", "") or ""),
                str(stop.get("description", "") or ""),
                str(stop.get("practical_note", "") or ""),
            ]
        )
        if minutes is None:
            minutes = self._extract_en_route_detour_minutes_from_text(blob)
        if miles is None:
            miles = self._extract_en_route_detour_miles_from_text(blob)
        return miles, minutes

    def _en_route_stop_within_threshold(self, stop: dict[str, Any]) -> tuple[bool, str]:
        max_minutes = int(getattr(self, "_en_route_detour_max_minutes", DEFAULT_EN_ROUTE_DETOUR_MAX_MINUTES) or 0)
        max_miles = float(getattr(self, "_en_route_detour_max_miles", DEFAULT_EN_ROUTE_DETOUR_MAX_MILES) or 0)
        require_metadata = bool(getattr(self, "_en_route_require_detour_metadata", DEFAULT_EN_ROUTE_REQUIRE_DETOUR_METADATA))

        miles, minutes = self._en_route_stop_detour_metrics(stop)
        if require_metadata and miles is None and minutes is None:
            return False, "missing_detour_metadata"
        if max_minutes > 0 and minutes is not None and minutes > max_minutes:
            return False, "detour_minutes_exceeded"
        if max_miles > 0 and miles is not None and miles > max_miles:
            return False, "detour_miles_exceeded"
        return True, "ok"

    # ── En-Route Stops ───────────────────────────────────────────────────────

    def _discover_en_route_stops(
        self,
        ai: dict[str, Any],
        dest_name: str,
        dest_dates: str | None = None,
        origin_name: str = "",
        origin_lat: Any = None,
        origin_lng: Any = None,
        dest_lat: Any = None,
        dest_lng: Any = None,
    ) -> None:
        source_mode = str(getattr(self, "_en_route_source", DEFAULT_EN_ROUTE_SOURCE) or DEFAULT_EN_ROUTE_SOURCE)
        getting_here = ai.get("getting_here", {}) if isinstance(ai.get("getting_here", {}), dict) else {}
        stops = getting_here.get("en_route_stops", []) if isinstance(getting_here.get("en_route_stops", []), list) else []

        if source_mode == "direct_link_batch":
            stops = self._prioritize_direct_batch_en_route_stops(stops, dest_name, dest_dates, origin_name)
            getting_here["en_route_stops"] = stops
            ai["getting_here"] = getting_here

        if stops and source_mode == "direct_link_batch":
            filtered_stops: list[dict[str, Any]] = []
            for stop in stops:
                keep, reason = self._en_route_stop_within_threshold(stop if isinstance(stop, dict) else {})
                if keep:
                    filtered_stops.append(stop)
                    continue
                stop_name = str((stop or {}).get("name", "") or "")
                self._log_decision(
                    kind="en_route_stop",
                    dest_name=dest_name,
                    item_name=stop_name,
                    reason="en_route_threshold_filtered",
                    message=f"en-route stop filtered by threshold ({reason})",
                )
            if len(filtered_stops) != len(stops):
                stops = filtered_stops
                getting_here["en_route_stops"] = stops
                ai["getting_here"] = getting_here

        if stops:
            filtered_generic_stops: list[dict[str, Any]] = []
            for stop in stops:
                stop_name = str((stop or {}).get("name", "") or "").strip()
                stop_url = str((stop or {}).get("url", "") or "").strip()
                has_concrete_url = bool(
                    stop_url
                    and not self._is_google_maps_candidate_url(stop_url)
                    and not self._is_generic_directions_url(stop_url)
                )
                if stop_name and self._is_generic_en_route_stop_title(stop_name) and not has_concrete_url:
                    desc = str((stop or {}).get("description", "") or "").strip()
                    mined = self._extract_named_stops_from_description(desc)
                    if mined:
                        for mined_name in mined:
                            mined_stop = dict(stop)
                            mined_stop["name"] = mined_name
                            mined_stop.pop("url", None)
                            if not str(mined_stop.get("description", "") or "").strip():
                                mined_stop["description"] = desc
                            if not str(mined_stop.get("practical_note", "") or "").strip():
                                mined_stop["practical_note"] = desc
                            if str(mined_stop.get("detour_distance_miles", "") or "").strip() == "":
                                mined_stop["detour_distance_miles"] = self._extract_en_route_detour_miles_from_text(desc)
                            if str(mined_stop.get("detour_time_minutes", "") or "").strip() == "":
                                mined_stop["detour_time_minutes"] = self._extract_en_route_detour_minutes_from_text(desc)
                            logger.info(
                                "  En-route mining: extracted '%s' from generic item '%s'",
                                mined_name, stop_name,
                            )
                            filtered_generic_stops.append(mined_stop)
                        self._log_decision(
                            kind="en_route_stop",
                            dest_name=dest_name,
                            item_name=stop_name,
                            reason="en_route_generic_title_filtered",
                            message=(
                                f"en-route stop filtered (generic heading); {len(mined)} named stops mined"
                            ),
                        )
                        continue
                    filtered_generic_stops.append(stop)
                    continue
                filtered_generic_stops.append(stop)
            if len(filtered_generic_stops) != len(stops):
                stops = filtered_generic_stops
                getting_here["en_route_stops"] = stops
                ai["getting_here"] = getting_here

        if stops:
            route_pruned_stops = self._prune_en_route_stops_by_geometry(
                stops=stops,
                origin_name=origin_name,
                dest_name=dest_name,
                origin_lat=origin_lat,
                origin_lng=origin_lng,
                dest_lat=dest_lat,
                dest_lng=dest_lng,
            )
            if len(route_pruned_stops) != len(stops):
                stops = route_pruned_stops
                getting_here["en_route_stops"] = stops
                ai["getting_here"] = getting_here

        resolved_stops: list[dict[str, Any]] = []
        for stop in stops:
            stop_name = stop.get("name", "")
            geocoded_lat = stop.get("geocode_lat")
            geocoded_lng = stop.get("geocode_lng")
            has_precise_geocode = isinstance(geocoded_lat, (int, float)) and isinstance(geocoded_lng, (int, float))
            if has_precise_geocode:
                # A coordinate query always resolves to exactly one point, unlike
                # a free-text name/address search, which can fail to resolve
                # precisely even for a real, correctly in-region place
                # (dipstick55 Theme E: "Swasey's Beach doesn't resolve to a
                # single point on Google Maps"). The geocode was already
                # verified as plausibly on-route by
                # _prune_en_route_stops_by_geometry just above, at no extra
                # cost -- prefer it as the fallback everywhere a free-text
                # query would otherwise be used below (maps_url, url, and the
                # final safety pass all route through `fallback_url`).
                fallback_url = f"https://www.google.com/maps/search/?api=1&query={geocoded_lat},{geocoded_lng}"
            else:
                q = self._en_route_maps_fallback_query_text(stop_name, origin_name, dest_name)
                fallback_url = f"https://www.google.com/maps/search/?api=1&query={quote(q)}" if str(q or "").strip() else ""
            existing_maps_url = str(stop.get("maps_url", "") or "").strip()
            if has_precise_geocode:
                stop["maps_url"] = fallback_url
            elif existing_maps_url:
                stop["maps_url"] = existing_maps_url
            elif fallback_url:
                stop["maps_url"] = fallback_url
            else:
                stop.pop("maps_url", None)
            url = None
            if source_mode == "direct_link_batch":
                existing_url = str(stop.get("url", "") or "").strip()
                if existing_url:
                    cleaned_existing = self._retain_discovered_url(
                        existing_url,
                        stop_name,
                        dest_name,
                        allow_alltrails=False,
                        kind="en_route_stop",
                    )
                    if cleaned_existing:
                        stop["url"] = cleaned_existing
                        self._log_decision(
                            kind="en_route_stop",
                            dest_name=dest_name,
                            item_name=stop_name,
                            reason="direct_batch_existing_url_preserved",
                            message="en-route link preserved from direct-link batch row",
                            url=cleaned_existing,
                        )
                        self._log_decision(
                            kind="en_route_stop",
                            dest_name=dest_name,
                            item_name=stop_name,
                            reason="discovery_completed",
                            message="en-route link",
                            url=cleaned_existing,
                        )
                        resolved_stops.append(stop)
                        continue
                url = self._search_en_route_stop_from_direct_batch(
                    stop_name,
                    dest_name,
                    str(dest_dates or ""),
                    origin_name,
                )
                if not url and self._direct_batch_is_authoritative():
                    if fallback_url:
                        stop["url"] = fallback_url
                        self._log_decision(
                            kind="en_route_stop",
                            dest_name=dest_name,
                            item_name=stop_name,
                            reason="direct_batch_source_locked_no_match",
                            message="en-route canonical URL omitted; direct-link batch authoritative had no match",
                        )
                        self._log_decision(
                            kind="en_route_stop",
                            dest_name=dest_name,
                            item_name=stop_name,
                            reason="maps_fallback_assigned",
                            message="en-route fallback maps URL assigned",
                            url=fallback_url,
                        )
                        self._log_decision(
                            kind="en_route_stop",
                            dest_name=dest_name,
                            item_name=stop_name,
                            reason="discovery_completed",
                            message="en-route link",
                            url=fallback_url,
                        )
                        resolved_stops.append(stop)
                    else:
                        stop.pop("url", None)
                        self._log_decision(
                            kind="en_route_stop",
                            dest_name=dest_name,
                            item_name=stop_name,
                            reason="en_route_removed_no_fallback",
                            message="en-route stop removed because no canonical or fallback URL was available",
                        )
                    continue
            if not url:
                url = self._search_first(
                    _build_query_variants(stop_name, dest_name, "attraction stop"),
                    item_name=stop_name,
                    dest_name=dest_name,
                    allow_alltrails=False,
                )
            if url:
                stop["url"] = url
                resolved_stops.append(stop)
            else:
                if fallback_url:
                    stop["url"] = fallback_url
                    self._log_decision(
                        kind="en_route_stop",
                        dest_name=dest_name,
                        item_name=stop_name,
                        reason="maps_fallback_assigned",
                        message="en-route fallback maps URL assigned",
                        url=fallback_url,
                    )
                    resolved_stops.append(stop)
                else:
                    stop.pop("url", None)
                    self._log_decision(
                        kind="en_route_stop",
                        dest_name=dest_name,
                        item_name=stop_name,
                        reason="en_route_removed_no_fallback",
                        message="en-route stop removed because no canonical or fallback URL was available",
                    )
            self._log_decision(
                kind="en_route_stop",
                dest_name=dest_name,
                item_name=stop_name,
                reason="discovery_completed",
                message="en-route link",
                url=str(stop.get("url", "") or ""),
            )

        if len(resolved_stops) != len(stops):
            getting_here["en_route_stops"] = resolved_stops
            ai["getting_here"] = getting_here

        # Safety pass: guarantee every surviving stop has a url regardless of which
        # discovery branch handled it (avoids silent no-link on any code path).
        for stop in getting_here.get("en_route_stops", []) or []:
            if not str(stop.get("url", "") or "").strip():
                sn = str(stop.get("name", "") or "").strip()
                if sn:
                    geocoded_lat = stop.get("geocode_lat")
                    geocoded_lng = stop.get("geocode_lng")
                    if isinstance(geocoded_lat, (int, float)) and isinstance(geocoded_lng, (int, float)):
                        stop["url"] = f"https://www.google.com/maps/search/?api=1&query={geocoded_lat},{geocoded_lng}"
                    else:
                        q = self._en_route_maps_fallback_query_text(sn, origin_name, dest_name)
                        if q:
                            stop["url"] = f"https://www.google.com/maps/search/?api=1&query={quote(q)}"
                        logger.warning("  En-route safety fallback assigned url for '%s'", sn)

        # Derive distance/time from the actual route rather than AI-generated estimates.
        self._update_route_distance_and_time(
            ai=ai,
            getting_here=getting_here,
            origin_name=origin_name,
            dest_name=dest_name,
            origin_lat=origin_lat,
            origin_lng=origin_lng,
            dest_lat=dest_lat,
            dest_lng=dest_lng,
        )

        getting_there = ai.get("getting_there", {}) if isinstance(ai.get("getting_there", {}), dict) else {}
        for option in getting_there.get("route_options", []) or []:
            option_name = str(option.get("title", "") or option.get("name", "") or "").strip()
            if not option_name:
                continue
            url = self._search_first(
                _build_query_variants(option_name, dest_name, "scenic drive byway route"),
                item_name=option_name,
                dest_name=dest_name,
                allow_alltrails=False,
            )
            if url:
                option["url"] = url
            else:
                option.pop("url", None)
            self._log_decision(
                kind="getting_there_route_option",
                dest_name=dest_name,
                item_name=option_name,
                reason="discovery_completed" if url else "no_canonical_url",
                message="departure route option link",
                url=(url or ""),
            )

    @staticmethod
    def _parse_lat_lng(lat: Any, lng: Any) -> tuple[float, float] | None:
        try:
            lat_f = float(lat)
            lng_f = float(lng)
        except (TypeError, ValueError):
            return None
        if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lng_f <= 180.0):
            return None
        return lat_f, lng_f

    @staticmethod
    def _haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
        lat1, lon1 = a
        lat2, lon2 = b
        r = 3958.8
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        s1 = radians(lat1)
        s2 = radians(lat2)
        h = (0.5 - cos(dlat) / 2.0) + cos(s1) * cos(s2) * (0.5 - cos(dlon) / 2.0)
        h = min(1.0, max(0.0, h))
        return 2.0 * r * asin(sqrt(h))

    @staticmethod
    def _route_progress_ratio(
        *,
        origin: tuple[float, float],
        dest: tuple[float, float],
        point: tuple[float, float],
    ) -> float | None:
        mid_lat = radians((origin[0] + dest[0]) / 2.0)
        scale = cos(mid_lat)
        ox, oy = origin[1] * scale, origin[0]
        dx, dy = dest[1] * scale, dest[0]
        px, py = point[1] * scale, point[0]
        abx = dx - ox
        aby = dy - oy
        denom = (abx * abx) + (aby * aby)
        if denom <= 1e-12:
            return None
        apx = px - ox
        apy = py - oy
        return ((apx * abx) + (apy * aby)) / denom

    def _geocode_en_route_stop_for_route(
        self,
        stop_name: str,
        *,
        origin_name: str,
        dest_name: str,
        origin: tuple[float, float] | None = None,
        dest: tuple[float, float] | None = None,
    ) -> tuple[float, float] | None:
        name = str(stop_name or "").strip()
        if not name:
            return None
        cache = getattr(self, "_en_route_stop_geocode_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._en_route_stop_geocode_cache = cache
        key = f"{name.lower()}|{str(origin_name or '').lower()}|{str(dest_name or '').lower()}"
        if key in cache:
            return cache[key]

        session = getattr(getattr(self, "_url_validator", None), "session", None)
        if session is None:
            cache[key] = None
            return None

        # Nominatim's free-text `q` param does not understand "near X" phrasing
        # (it always returns zero results for it) and a bare "Name, USA" query
        # resolves common place names (e.g. "Red Canyon") to whichever match
        # ranks highest by Nominatim's own global "importance" score -- which is
        # frequently a same-named place hundreds of miles from the actual route.
        # A viewbox biased to the route's own origin/destination disambiguates
        # correctly without needing exact query phrasing.
        viewbox_params: dict[str, Any] = {}
        if origin is not None and dest is not None:
            lat_pad = max(0.5, abs(origin[0] - dest[0]) * 0.25)
            lng_pad = max(0.5, abs(origin[1] - dest[1]) * 0.25)
            min_lat = min(origin[0], dest[0]) - lat_pad
            max_lat = max(origin[0], dest[0]) + lat_pad
            min_lng = min(origin[1], dest[1]) - lng_pad
            max_lng = max(origin[1], dest[1]) + lng_pad
            viewbox_params = {
                "viewbox": f"{min_lng},{max_lat},{max_lng},{min_lat}",
                "bounded": 1,
            }

        # An unrestricted (non-viewbox) fallback query can still match a
        # same-named place clear across the country (e.g. a "Glendale Town
        # Park" hundreds of miles away) when nothing exists in-region under
        # that exact query text. Sanity-bound any such match against the
        # route itself so a wrong-region hit is rejected rather than accepted.
        sanity_radius_miles: float | None = None
        route_midpoint: tuple[float, float] | None = None
        if origin is not None and dest is not None:
            leg_miles = self._haversine_miles(origin, dest)
            sanity_radius_miles = max(150.0, leg_miles * 1.5)
            route_midpoint = ((origin[0] + dest[0]) / 2.0, (origin[1] + dest[1]) / 2.0)

        queries = [f"{name}, {dest_name}", name, f"{name}, USA"]
        # Each (query, viewbox-mode) combination is a separate throttled
        # request (1.1s apart). The full cross product (3 queries x 2 viewbox
        # modes = up to 6) was rarely needed in practice -- the viewbox-biased
        # search on the first two query variants, plus one unrestricted
        # fallback, covers the same ground the existing tests exercise at
        # roughly half the worst-case cost per unresolved stop.
        if viewbox_params:
            attempts: list[tuple[str, bool]] = [(queries[0], True)]
            if len(queries) > 1:
                attempts.append((queries[1], True))
            attempts.append((queries[0], False))
        else:
            attempts = [(q, False) for q in queries]

        for query, use_viewbox in attempts:
            q = str(query or "").strip()
            if not q:
                continue
            self._respect_nominatim_rate_limit()
            try:
                params = {
                    "q": q,
                    "format": "json",
                    "limit": 1,
                    "countrycodes": "us",
                }
                if use_viewbox:
                    params.update(viewbox_params)
                response = session.get(
                    "https://nominatim.openstreetmap.org/search",
                    params=params,
                    headers={"User-Agent": "RoadTripGenerator-URLDiscovery/1.0"},
                    timeout=8,
                )
                if getattr(response, "status_code", None) == 429:
                    # Back off the shared clock and give up for this stop
                    # rather than burning through the remaining query/
                    # viewbox combinations while rate-limited -- each
                    # destination pair can call this once per stop, so
                    # retrying every combination here risks compounding
                    # the lockout across the whole run.
                    self._nominatim_last_request_ts = time.monotonic() + 5.0
                    logger.info("Nominatim rate-limited (429) geocoding en-route stop '%s'", name)
                    cache[key] = None
                    return None
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows, list) or not rows:
                    continue
                first = rows[0] if isinstance(rows[0], dict) else {}
                parsed = self._parse_lat_lng(first.get("lat"), first.get("lon"))
                if parsed is None:
                    continue
                if (
                    not use_viewbox
                    and route_midpoint is not None
                    and sanity_radius_miles is not None
                    and self._haversine_miles(route_midpoint, parsed) > sanity_radius_miles
                ):
                    # This is a real, named-place hit -- not an absence of data --
                    # that just happens to sit far outside any plausible reading
                    # of this route (e.g. a same-named trail/park hundreds of
                    # miles away in another state). That is positive evidence the
                    # stop is a wrong-geography hallucination, not merely
                    # "unconfirmed" -- record it distinctly from the plain-miss
                    # case below so _prune_en_route_stops_by_geometry can drop
                    # the stop outright instead of just deprioritizing it for
                    # waypoint ordering (see dipstick55 Theme A: "Stan's Overlook
                    # Trail, Snoqualmie, WA" / "Looking Glass Rock, Brevard, NC"
                    # appearing as en-route stops for unrelated UT/CO routes).
                    self._mark_en_route_stop_geocode_rejected_out_of_region(key)
                    continue
                cache[key] = parsed
                self._mark_persistent_cache_dirty()
                return parsed
            except Exception:
                continue

        cache[key] = None
        return None

    def _mark_en_route_stop_geocode_rejected_out_of_region(self, cache_key: str) -> None:
        if not hasattr(self, "_en_route_stop_geocode_rejected_out_of_region"):
            self._en_route_stop_geocode_rejected_out_of_region = set()
        self._en_route_stop_geocode_rejected_out_of_region.add(cache_key)

    def _en_route_stop_geocode_was_rejected_out_of_region(self, stop_name: str, *, origin_name: str, dest_name: str) -> bool:
        """True only when a prior geocode attempt for this exact stop found a
        real named-place match that was rejected specifically for being
        implausibly far from the route -- not merely "no data available"."""
        rejected = getattr(self, "_en_route_stop_geocode_rejected_out_of_region", None)
        if not rejected:
            return False
        key = f"{str(stop_name or '').strip().lower()}|{str(origin_name or '').lower()}|{str(dest_name or '').lower()}"
        return key in rejected

    def _respect_nominatim_rate_limit(self) -> None:
        """Nominatim's usage policy caps requests at 1/sec. This function can
        issue several requests per stop (query-variant x viewbox-mode
        combinations) across many stops per destination pair, so it needs its
        own throttle rather than relying on the caller to pace calls.

        Destination discovery runs on a multi-thread pool (see
        discover_all's ThreadPoolExecutor), so multiple threads can reach
        this method concurrently. Without a lock, each one independently
        reads the same last-request timestamp, decides it's safe, and fires
        at the same time -- the check-sleep-write sequence must be atomic or
        the 1 req/sec policy isn't actually enforced under concurrency, it
        just looks like it is from any single thread's perspective.
        """
        if not hasattr(self, "_nominatim_rate_limit_lock"):
            self._nominatim_rate_limit_lock = Lock()
        with self._nominatim_rate_limit_lock:
            last_ts = getattr(self, "_nominatim_last_request_ts", 0.0)
            elapsed = time.monotonic() - last_ts
            min_interval = 1.1
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            self._nominatim_last_request_ts = time.monotonic()

    def _prune_en_route_stops_by_geometry(
        self,
        *,
        stops: list[dict[str, Any]],
        origin_name: str,
        dest_name: str,
        origin_lat: Any,
        origin_lng: Any,
        dest_lat: Any,
        dest_lng: Any,
    ) -> list[dict[str, Any]]:
        origin = self._parse_lat_lng(origin_lat, origin_lng)
        dest = self._parse_lat_lng(dest_lat, dest_lng)
        if origin is None or dest is None:
            for stop in stops:
                if isinstance(stop, dict):
                    stop["route_waypoint_eligible"] = False
            return stops

        leg_miles = self._haversine_miles(origin, dest)
        if leg_miles <= 1.0:
            for stop in stops:
                if isinstance(stop, dict):
                    stop["route_waypoint_eligible"] = False
            return stops

        kept: list[dict[str, Any]] = []
        for stop in stops:
            stop_name = str((stop or {}).get("name", "") or "").strip()
            if not stop_name:
                if isinstance(stop, dict):
                    stop["route_waypoint_eligible"] = False
                kept.append(stop)
                continue

            stop_coords = self._geocode_en_route_stop_for_route(
                stop_name,
                origin_name=origin_name,
                dest_name=dest_name,
                origin=origin,
                dest=dest,
            )
            if stop_coords is None:
                # Distinguish "we have no data either way" (lenient: fall back to
                # the detour-metadata heuristic, keep the stop) from "geocoding
                # found a real named-place match for this exact name, but it was
                # implausibly far from the route" (positive evidence of a
                # wrong-geography hallucination -- drop the stop outright rather
                # than merely excluding it from waypoint ordering).
                if self._en_route_stop_geocode_was_rejected_out_of_region(
                    stop_name, origin_name=origin_name, dest_name=dest_name
                ):
                    self._log_decision(
                        kind="en_route_stop",
                        dest_name=dest_name,
                        item_name=stop_name,
                        reason="en_route_geometry_filtered_wrong_region",
                        message="en-route stop removed: only resolvable to a place far outside the route",
                    )
                    continue
                if isinstance(stop, dict):
                    keep_waypoint = False
                    if not self._is_generic_en_route_stop_title(stop_name):
                        within_threshold, _reason = self._en_route_stop_within_threshold(stop)
                        keep_waypoint = bool(within_threshold)
                    stop["route_waypoint_eligible"] = keep_waypoint
                kept.append(stop)
                continue

            progress = self._route_progress_ratio(origin=origin, dest=dest, point=stop_coords)
            if progress is None:
                if isinstance(stop, dict):
                    keep_waypoint = False
                    if not self._is_generic_en_route_stop_title(stop_name):
                        within_threshold, _reason = self._en_route_stop_within_threshold(stop)
                        keep_waypoint = bool(within_threshold)
                    stop["route_waypoint_eligible"] = keep_waypoint
                kept.append(stop)
                continue

            origin_to_stop = self._haversine_miles(origin, stop_coords)
            beyond_buffer = max(10.0, leg_miles * 0.12)
            # Remove stops that are at or past the destination: these belong as
            # destination attractions, not as route waypoints.
            at_destination = progress >= 0.93 and origin_to_stop >= leg_miles * 0.88
            if at_destination or (progress > 1.06 and origin_to_stop > leg_miles + beyond_buffer):
                self._log_decision(
                    kind="en_route_stop",
                    dest_name=dest_name,
                    item_name=stop_name,
                    reason="en_route_geometry_filtered",
                    message="en-route stop filtered as beyond destination leg",
                )
                continue

            if isinstance(stop, dict):
                stop["route_waypoint_eligible"] = True
                stop["route_progress_ratio"] = progress
                # Persist the verified coordinates so the maps_url built later in
                # _discover_en_route_stops can point at this exact point instead
                # of a free-text search -- see dipstick55 Theme E ("Swasey's
                # Beach doesn't resolve to a single point on Google Maps"): a
                # loosely-defined BLM beach/campsite is real and correctly
                # in-region, but a free-text query for it doesn't reliably
                # resolve to one place in Google's own index. A coordinate
                # query (from the same Nominatim lookup already done for route
                # pruning, at no extra cost) always resolves to exactly one
                # point.
                stop["geocode_lat"] = stop_coords[0]
                stop["geocode_lng"] = stop_coords[1]
            kept.append(stop)

        return kept

    # ── Scenic Drives ────────────────────────────────────────────────────────

    _GENERIC_SCENIC_DRIVE_NAME_TOKENS = frozenset({
        "scenic", "drive", "road", "route", "byway", "day", "trip", "loop", "tour", "highway",
    })

    @classmethod
    def _nps_deterministic_scenic_drive_page_matches(cls, drive_name: str, page_text: str | None) -> bool:
        distinctive_tokens = [
            t for t in cls._significant_tokens(drive_name)
            if t not in cls._GENERIC_SCENIC_DRIVE_NAME_TOKENS
        ]
        if not distinctive_tokens:
            # A generically-named drive ("Scenic Drive", "Park Loop Road") has
            # nothing to distinguish it from the park's own page -- trust it.
            return True
        lower_text = str(page_text or "").lower()
        if not lower_text:
            return False
        return any(token in lower_text for token in distinctive_tokens)

    @staticmethod
    def _scenic_drive_search_name(drive_name: str) -> str:
        """Strip a trailing AI-added activity-type descriptor ('Day Trip') that
        is not part of the road's actual name, so search queries target the
        real name ('Notom-Bullfrog Road') instead of a quoted phrase no real
        source uses verbatim ('Notom-Bullfrog Road Day Trip'), which wastes
        the exact-phrase query attempts before falling through to a looser
        variant."""
        cleaned = re.sub(r"\s+day\s+trip\s*$", "", str(drive_name or ""), flags=re.IGNORECASE).strip()
        return cleaned or str(drive_name or "").strip()

    def _discover_scenic_drives(self, dest: dict[str, Any], dest_name: str, nps_code: str | None = None) -> None:
        for drive in dest.get("scenic_drives", []):
            drive_name = drive.get("title", "")

            # For NPS parks, the *park's own* scenic-drive page follows a
            # deterministic pattern -- but a park can have several distinctly
            # named drives (e.g. Capitol Reef's paved "Scenic Drive" vs. the
            # separate "Notom-Bullfrog Road" backcountry route), and every one
            # of them would get this same generic URL if we only checked that
            # it returns HTTP 200. Verify the page text is actually about
            # *this* drive (or the drive's name is itself generic enough that
            # there's nothing to distinguish) before accepting the shortcut.
            nps_drive_url: str | None = None
            if nps_code:
                candidate = f"https://www.nps.gov/{nps_code}/planyourvisit/scenic-drive.htm"
                ok, _status, page_text = self._fetch_page_text(candidate, timeout=6)
                if ok and self._nps_deterministic_scenic_drive_page_matches(drive_name, page_text):
                    nps_drive_url = candidate
                    self._log_decision(
                        kind="scenic_drive",
                        dest_name=dest_name,
                        item_name=drive_name,
                        reason="nps_deterministic_accepted",
                        message="scenic drive link (NPS deterministic)",
                        url=nps_drive_url,
                    )

            url = nps_drive_url or self._search_first(
                _build_query_variants(self._scenic_drive_search_name(drive_name), dest_name, "scenic drive viewpoint"),
                item_name=drive_name,
                dest_name=dest_name,
                allow_alltrails=False,
            )
            # Keep scenic-drive/day-trip popup links optional: only include when discovery
            # produces a verified relevant URL.
            drive["url"] = url or ""
            if not nps_drive_url:
                self._log_decision(
                    kind="scenic_drive",
                    dest_name=dest_name,
                    item_name=drive_name,
                    reason="discovery_completed" if url else "no_match",
                    message="scenic drive link",
                    url=(url or ""),
                )

    # ── Search helpers ───────────────────────────────────────────────────────

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
            self._log_decision(
                kind="search",
                dest_name=dest_name,
                item_name=item_name,
                reason="search_cache_hit",
                message=f"cache hit ({site_filter or 'any'})",
                url=(_url_cache[cache_key] or ""),
            )
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
        if result:
            self._log_decision(
                kind="search",
                dest_name=dest_name,
                item_name=item_name,
                reason="search_resolved",
                message=f"resolved ({site_filter or 'any'})",
                url=result,
            )
        else:
            self._log_decision(
                kind="search",
                dest_name=dest_name,
                item_name=item_name,
                reason="search_no_match",
                message=f"rejected/no-match ({site_filter or 'any'})",
            )
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
        ranked_candidates: dict[str, tuple[int, dict[str, Any]]] = {}
        normalized_variants: list[str] = []
        seen_queries: set[str] = set()
        for raw_query in query_variants[:max_attempts]:
            key = str(raw_query or "").strip().lower()
            if not key or key in seen_queries:
                continue
            seen_queries.add(key)
            normalized_variants.append(str(raw_query))

        for query in normalized_variants:
            full_query = f"{site_hint} {query}" if site_hint else (f"site:{site_filter} {query}" if site_filter else query)
            candidates = self._search_cached(full_query, count=10)

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
                if not self._matches_site_filter(candidate_url, site_filter):
                    continue
                if site_filter == "alltrails.com" and not self._is_alltrails_trail_url(candidate_url):
                    continue
                if not allow_alltrails and "alltrails.com" in candidate_url.lower():
                    continue
                if not self._is_specific_result_url(candidate_url, item_name, dest_name):
                    continue
                scored_item = dict(item)
                scored_item["url"] = candidate_url
                if not self._meets_place_interest_threshold(scored_item, site_filter=site_filter):
                    continue
                if self._is_alltrails_trail_url(candidate_url):
                    if self._is_relevant_result(
                        candidate_url,
                        item_name,
                        dest_name,
                        candidate=scored_item,
                        deep_check=False,
                    ):
                        score = self._score_candidate_result(
                            scored_item,
                            item_name,
                            dest_name,
                            specific=True,
                            site_filter=site_filter,
                        )
                        existing = ranked_candidates.get(candidate_url)
                        if existing is None or score > existing[0]:
                            ranked_candidates[candidate_url] = (score, scored_item)
                        best = self._pick_better_candidate(best, score, candidate_url)
                    continue
                if self._is_relevant_result(
                    candidate_url,
                    item_name,
                    dest_name,
                    candidate=scored_item,
                    deep_check=False,
                ):
                    score = self._score_candidate_result(
                        scored_item,
                        item_name,
                        dest_name,
                        specific=True,
                        site_filter=site_filter,
                    )
                    existing = ranked_candidates.get(candidate_url)
                    if existing is None or score > existing[0]:
                        ranked_candidates[candidate_url] = (score, scored_item)
                    best = self._pick_better_candidate(best, score, candidate_url)

            if self._should_short_circuit_search(best, site_filter, item_name):
                break

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
                if not self._matches_site_filter(candidate_url, site_filter):
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
                if not self._meets_place_interest_threshold(scored_item, site_filter=site_filter):
                    continue
                if self._is_alltrails_trail_url(candidate_url):
                    if self._is_relevant_result(
                        candidate_url,
                        item_name,
                        dest_name,
                        candidate=scored_item,
                        deep_check=False,
                    ):
                        score = self._score_candidate_result(
                            scored_item,
                            item_name,
                            dest_name,
                            specific=False,
                            site_filter=site_filter,
                        )
                        existing = ranked_candidates.get(candidate_url)
                        if existing is None or score > existing[0]:
                            ranked_candidates[candidate_url] = (score, scored_item)
                        best = self._pick_better_candidate(best, score, candidate_url)
                    continue
                if self._is_relevant_result(
                    candidate_url,
                    item_name,
                    dest_name,
                    candidate=scored_item,
                    deep_check=False,
                ):
                    score = self._score_candidate_result(
                        scored_item,
                        item_name,
                        dest_name,
                        specific=False,
                        site_filter=site_filter,
                    )
                    existing = ranked_candidates.get(candidate_url)
                    if existing is None or score > existing[0]:
                        ranked_candidates[candidate_url] = (score, scored_item)
                    best = self._pick_better_candidate(best, score, candidate_url)

            if self._should_short_circuit_search(best, site_filter, item_name):
                break

        if not ranked_candidates:
            return None

        ranked = sorted(ranked_candidates.items(), key=lambda row: row[1][0], reverse=True)
        max_deep_checks = min(3, len(ranked))
        for idx, (candidate_url, payload) in enumerate(ranked):
            score, candidate_item = payload
            if idx >= max_deep_checks:
                break
            if self._is_relevant_result(
                candidate_url,
                item_name,
                dest_name,
                candidate=candidate_item,
                deep_check=True,
            ):
                logger.debug("  URL selected by score=%s -> %s", score, candidate_url[:120])
                if hasattr(self, "_search_winner_snippets"):
                    self._search_winner_snippets[candidate_url] = candidate_item
                return candidate_url

        # Preserve fail-closed behavior when top candidates fail deep checks.
        return None

    def _meets_place_interest_threshold(
        self,
        candidate: dict[str, Any] | None,
        *,
        site_filter: str | None = None,
    ) -> bool:
        if site_filter == "alltrails.com":
            return True
        text = self._candidate_text_blob(candidate)
        rating, votes = self._extract_rating_votes(text)

        require_metadata = bool(getattr(self, "_place_interest_require_metadata", DEFAULT_PLACE_INTEREST_REQUIRE_METADATA))
        if rating is None or votes is None:
            return not require_metadata

        min_rating = float(getattr(self, "_place_interest_min_rating", DEFAULT_PLACE_INTEREST_MIN_RATING))
        min_votes = int(getattr(self, "_place_interest_min_votes", DEFAULT_PLACE_INTEREST_MIN_VOTES))
        return rating >= min_rating and votes >= min_votes

    def _collect_discovered_urls(self, trip: dict[str, Any]) -> set[str]:
        urls: set[str] = set()
        for dest in trip.get("destinations", []) or []:
            if not isinstance(dest, dict):
                continue
            ai = dest.get("ai_content", {}) if isinstance(dest.get("ai_content", {}), dict) else {}

            for attr in ai.get("top_attractions", []) or []:
                if isinstance(attr, dict):
                    url = str(attr.get("url", "") or "").strip()
                    if url:
                        urls.add(url)

            getting_here = ai.get("getting_here", {}) if isinstance(ai.get("getting_here", {}), dict) else {}
            for stop in getting_here.get("en_route_stops", []) or []:
                if isinstance(stop, dict):
                    url = str(stop.get("url", "") or "").strip()
                    if url:
                        urls.add(url)

            for rest in ai.get("dinner_recommendations", []) or []:
                if isinstance(rest, dict):
                    url = str(rest.get("url", "") or "").strip()
                    if url:
                        urls.add(url)

            for drive in dest.get("scenic_drives", []) or []:
                if isinstance(drive, dict):
                    url = str(drive.get("url", "") or "").strip()
                    if url:
                        urls.add(url)

            events = dest.get("cultural_events", {}) if isinstance(dest.get("cultural_events", {}), dict) else {}
            for event in events.get("events", []) or []:
                if isinstance(event, dict):
                    url = str(event.get("url", "") or "").strip()
                    if url:
                        urls.add(url)

        return urls

    def _is_high_confidence_provenance_url(self, url: str) -> bool:
        """URLs whose provenance already establishes high confidence don't
        need the audit pass's proactive full-content prefetch: a harvest row
        already marked direct-batch-authoritative during discovery, or an
        official .gov domain. Skipping the prewarm doesn't skip verification
        entirely -- if some later per-item check still needs this URL's
        content, it fetches on demand at that point; this only avoids paying
        for a bulk fetch that's usually never actually needed downstream."""
        if self._is_remembered_direct_batch_authoritative_url(url):
            return True
        host = urlparse(url).netloc.lower()
        return host.endswith(".gov") or ".gov." in host

    def _prewarm_url_validation_cache(self, trip: dict[str, Any]) -> None:
        candidates = sorted(self._collect_discovered_urls(trip))
        if not candidates:
            return

        safe_prefixes = SAFE_FALLBACK_URL_PREFIXES
        to_fetch: list[str] = []
        for url in candidates:
            lower = url.lower()
            if any(lower.startswith(prefix) for prefix in safe_prefixes):
                continue
            if self._is_obviously_generic_url(lower):
                continue
            if self._is_high_confidence_provenance_url(url):
                continue
            to_fetch.append(url)

        if not to_fetch:
            return

        # Keep AllTrails lazy because it has dedicated throttling/anti-block logic.
        non_alltrails = [u for u in to_fetch if not self._is_alltrails_trail_url(u)]
        if not non_alltrails:
            return

        workers = min(8, max(1, len(non_alltrails)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self._fetch_page_text, url, 8) for url in non_alltrails]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    # Best-effort prewarm only; strict checks will still fail closed.
                    pass

    def _should_short_circuit_search(
        self,
        best: tuple[int, str] | None,
        site_filter: str | None,
        item_name: str,
    ) -> bool:
        if not best:
            return False
        score, best_url = best
        if score < 24:
            return False

        if site_filter == "alltrails.com":
            if not self._is_alltrails_trail_url(best_url):
                return False
            return self._alltrails_slug_extra_term_count(best_url, item_name) == 0

        return True

    def _search_cached(self, full_query: str, *, count: int = 10) -> list[dict[str, Any]]:
        query_key = str(full_query or "").strip()
        if not query_key:
            return []

        if not hasattr(self, "_search_results_cache"):
            self._search_results_cache = {}
        if not hasattr(self, "_request_cache_lock"):
            self._request_cache_lock = Lock()
        if not hasattr(self, "_search_failure_ts"):
            self._search_failure_ts = {}

        with self._request_cache_lock:
            cached = self._search_results_cache.get(query_key)
            if cached is not None and len(cached) > 0:
                return [dict(item) for item in cached if isinstance(item, dict)]
            failed_at = self._search_failure_ts.get(query_key)
            cooldown = float(
                getattr(self, "_search_failure_cooldown_seconds", DEFAULT_SEARCH_FAILURE_COOLDOWN_SECONDS)
            )
            if failed_at is not None and (time.monotonic() - failed_at) < cooldown:
                # An empty result is never proof this query has no answer --
                # GrokSearch.search() swallows exceptions and returns []
                # either way. Uncached, so a later call after the cooldown
                # naturally expires still gets a real attempt.
                return []

        # Falls back to self._search (the batch client) if _search_fallback
        # isn't set -- keeps ad hoc/partially-constructed instances (e.g.
        # URLDiscoverer.__new__(URLDiscoverer) in tests) working without
        # requiring every one to set both attributes.
        search_client = getattr(self, "_search_fallback", None) or getattr(self, "_search", None)
        if search_client is None:
            return []

        results = search_client.search(query_key, count=count)
        normalized = [dict(item) for item in results if isinstance(item, dict)]

        with self._request_cache_lock:
            if normalized:
                self._search_results_cache[query_key] = normalized
                self._search_failure_ts.pop(query_key, None)
            else:
                self._search_results_cache.pop(query_key, None)
                self._search_failure_ts[query_key] = time.monotonic()
        self._mark_persistent_cache_dirty()

        return [dict(item) for item in normalized]

    def _verify_url_cached(self, url: str) -> tuple[bool, int | str]:
        if not url:
            return False, "invalid_url"
        if not hasattr(self, "_url_validator"):
            return False, "no_validator"

        if not hasattr(self, "_verify_url_cache"):
            self._verify_url_cache = {}
        if not hasattr(self, "_request_cache_lock"):
            self._request_cache_lock = Lock()

        with self._request_cache_lock:
            cached = self._verify_url_cache.get(url)
        if cached is not None:
            return cached

        result = self._url_validator.verify_url(url)
        if not isinstance(result, tuple) or len(result) != 2:
            # Some tests/mocks provide a bare MagicMock for verify_url.
            # Normalize to a conservative failure tuple instead of raising
            # unpack/type errors in downstream relevance checks.
            result = (False, "invalid_verify_result")
        with self._request_cache_lock:
            self._verify_url_cache[url] = result
        self._mark_persistent_cache_dirty()
        return result

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

        # Reject generic section landing pages, but allow specific child pages
        # under those sections (e.g. /planyourvisit/kolob-canyons.htm).
        if self._is_generic_section_landing_page(url):
            return False

        item_tokens = self._significant_tokens(item_name)
        if item_tokens and not any(token in lower for token in item_tokens):
            return False

        # If destination tokens exist in URL it's usually a much better match.
        dest_tokens = self._significant_tokens(dest_name)
        if dest_tokens and any(token in lower for token in dest_tokens):
            return True

        return True

    def _looks_like_item_specific_homepage(self, url: str, item_name: str, *, item_tokens: list[str] | None = None) -> bool:
        candidate = str(url or "").strip()
        if not candidate:
            return False

        parsed = urlparse(candidate)
        if parsed.query or parsed.fragment:
            return False

        path = unquote(parsed.path or "").strip()
        # Normalize trailing index files so /band/index.htm counts as depth 1.
        norm_path = re.sub(r"/index\.html?$", "/", path, flags=re.IGNORECASE).rstrip("/")
        path_depth = len([s for s in norm_path.strip("/").split("/") if s])

        host = (parsed.netloc or "").lower()
        if not host:
            return False

        host_text = re.sub(r"[^a-z0-9]+", " ", host).lower()
        host_slug = re.sub(r"[^a-z0-9]+", "", host).lower()
        path_slug = re.sub(r"[^a-z0-9]+", "", path).lower()
        tokens = item_tokens if item_tokens is not None else self._significant_tokens(item_name)
        if not tokens:
            return False

        # For depth-1 paths, only allow when at least one item token appears in the host.
        if path_depth > 1:
            return False
        if path_depth == 1:
            if not any(token in host_slug for token in tokens if len(token) >= 3):
                return False

        item_slug = re.sub(r"[^a-z0-9]+", "", item_name).lower()
        if item_slug and (item_slug in host_slug or item_slug in path_slug):
            return True

        if any(token in host_slug for token in tokens if len(token) >= 3):
            return True
        if any(token in path_slug for token in tokens if len(token) >= 3):
            return True
        if any(token in host_text for token in tokens if len(token) >= 3):
            return True

        # Abbreviated domains: check if host word and item token share a 5+ char common prefix.
        host_words = re.findall(r"[a-z0-9]+", host)
        for hw in host_words:
            if len(hw) < 4:
                continue
            for token in tokens:
                if len(token) < 4:
                    continue
                # tok_in_hw covers e.g. "sheridan" in "newsheridan"
                if token in hw:
                    return True
                # Shared prefix covers e.g. "cosmo*" in "cosmopolitan" vs "cosmotelluride"
                common = 0
                for a, b in zip(hw, token):
                    if a == b:
                        common += 1
                    else:
                        break
                if common >= 5:
                    return True

        try:
            ok, _status, page_html = self._fetch_page_text(candidate, timeout=8)
            if ok and page_html and self._text_matches_item_tokens(page_html, tokens):
                return True
        except Exception:
            pass

        return False

    @staticmethod
    def _is_generic_section_landing_page(url: str) -> bool:
        parsed = urlparse(str(url or ""))
        host = (parsed.netloc or "").lower()
        path = (parsed.path or "").strip().lower()
        if not path:
            return True

        if "tripadvisor." in host and path.startswith("/attractions-") and "-activities-" in path:
            return True

        segments = [seg for seg in path.strip("/").split("/") if seg]
        if not segments:
            return True

        last = segments[-1]
        generic_sections = {
            "plan-your-visit",
            "planyourvisit",
            "visit",
            "things-to-do",
            "things2do",
            "explore",
            "about",
            "home",
            "restaurants",
            "restaurant",
            "dining",
            "food",
            "eat",
            "attractions",
            "attraction",
            "activities",
            "activity",
        }
        if last in {"index.htm", "index.html"}:
            return True
        if last in generic_sections:
            return True

        # Some NPS section pages use a generic filename under planyourvisit.
        if len(segments) >= 2 and segments[-2] in {"planyourvisit", "plan-your-visit"} and last.startswith("things2do"):
            return True

        return False

    def _is_relevant_result(
        self,
        url: str,
        item_name: str,
        dest_name: str,
        candidate: dict[str, Any] | None = None,
        deep_check: bool = True,
    ) -> bool:
        """Lightweight relevance gate: avoid live but useless links."""
        if self._is_obviously_generic_url(url.lower()):
            return False
        if self._is_campground_focused_result_for_noncamping_item(url, item_name, candidate_text=self._candidate_text_blob(candidate)):
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
            candidate_text_blob = self._candidate_text_blob(candidate)
            if self._has_alltrails_closure_marker(candidate_text_blob):
                return False
            slug_extra_terms = self._alltrails_slug_extra_term_count(url, item_name)

            if not deep_check:
                if candidate is not None:
                    has_candidate_metadata = bool(candidate_text_blob.strip())
                    if has_candidate_metadata:
                        if not metadata_ok:
                            return False
                        if len(item_tokens) <= 1 and not destination_ok:
                            return False
                        if slug_extra_terms >= 2 and not listing_signal_ok:
                            return False
                return True

            try:
                ok, status, text = self._fetch_page_text(url, timeout=8)
                if not ok:
                    verified_ok, verified_status = self._verify_url_cached(url)
                    if not verified_ok and self._is_definitively_dead_status(verified_status):
                        return False
                    final_url = getattr(self, "_fetch_final_url_cache", {}).get(url, url)
                    if final_url != url and self._is_alltrails_trail_url(final_url):
                        if not self._alltrails_slug_matches_item(final_url, item_name):
                            logger.info(
                                "AllTrails redirect entity mismatch (blocked fetch): %s -> %s (item: %s)",
                                url,
                                final_url,
                                item_name,
                            )
                            return False
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
                        if self._is_definitively_dead_status(status):
                            return False
                        return True
                    if self._is_definitively_dead_status(status):
                        return False
                    # Search candidates may sometimes provide only URL with no
                    # snippet/title metadata and still be valid; slug match is
                    # already enforced above, so keep as fallback.
                    return True
                text = (text or "").lower()
                if any(marker in text for marker in ALLTRAILS_404_MARKERS):
                    return False
                if self._has_alltrails_closure_marker(text):
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
            if not deep_check:
                item_tokens = self._significant_tokens(item_name)
                if item_tokens:
                    in_url = any(t in (url or "").lower() for t in item_tokens)
                    in_candidate = self._candidate_text_matches_item_tokens(candidate, item_tokens)
                    if not (in_url or in_candidate):
                        return False
                return True

            ok, status, text = self._fetch_page_text(url, timeout=8)
            if not ok:
                if self._is_definitively_dead_status(status):
                    return False
                # A blocked/transient fetch failure (403/401/timeout/5xx/SSL)
                # is not proof the URL is dead -- a bot-blocking site (e.g.
                # TripAdvisor) fails this exact same way for a perfectly live
                # page. Mirror the AllTrails branch above: try a secondary
                # liveness probe, then fall back to candidate metadata rather
                # than rejecting outright on an inconclusive fetch.
                verified_ok, verified_status = self._verify_url_cached(url)
                if not verified_ok and self._is_definitively_dead_status(verified_status):
                    return False
                item_tokens = self._significant_tokens(item_name)
                if candidate is not None:
                    return self._candidate_text_matches_item_tokens(candidate, item_tokens)
                return True
            text = (text or "").lower()
            if self._is_campground_focused_result_for_noncamping_item(url, item_name, fetched_text=text):
                return False
            item_tokens = self._significant_tokens(item_name)
            dest_tokens = self._significant_tokens(dest_name)
            if not self._text_matches_item_tokens(text, item_tokens):
                return False
            if self._looks_like_item_specific_homepage(url, item_name, item_tokens=item_tokens):
                return True
            if dest_tokens and not any(t in text for t in dest_tokens[:2]):
                return False
            return True
        except Exception:
            return False

    @staticmethod
    def _is_definitively_dead_status(status: int | str | None) -> bool:
        """True when a fetch status means the URL is genuinely gone -- an explicit
        404/410 HTTP status, or a connection-level failure meaning the host
        doesn't exist or refuses all connections (DNS resolution failure, refused
        connection). A DNS failure is at least as conclusive as a 404 -- the
        domain doesn't exist at all -- so it must not be treated differently just
        because requests/urllib3 report it as an exception string rather than a
        status code. Other failure modes (timeouts, 401/403/500/503, SSL errors)
        are deliberately NOT included: those can be transient or bot-blocking
        false positives and must keep failing open.
        """
        if isinstance(status, int):
            return status in (404, 410)
        text = str(status or "").lower()
        return any(
            marker in text
            for marker in (
                "getaddrinfo failed",
                "name or service not known",
                "nodename nor servname",
                "nameresolutionerror",
                "failed to resolve",
                "failed to establish a new connection",
                "no address associated with hostname",
                "errno 11001",
            )
        )

    def _fetch_page_text(self, url: str, timeout: int = 8) -> tuple[bool, int | str, str]:
        if self._is_alltrails_trail_url(url):
            return self._fetch_alltrails_text(url, timeout=timeout)

        if not hasattr(self, "_page_text_cache"):
            self._page_text_cache = {}
        if not hasattr(self, "_request_cache_lock"):
            self._request_cache_lock = Lock()
        if not hasattr(self, "_domain_blocked_until_ts"):
            self._domain_blocked_until_ts = {}

        with self._request_cache_lock:
            cached = self._page_text_cache.get(url)
        if cached is not None:
            return cached

        domain = urlparse(url).netloc.lower()
        if domain:
            with self._request_cache_lock:
                blocked_until = float(self._domain_blocked_until_ts.get(domain, 0.0) or 0.0)
            if blocked_until > time.monotonic():
                # Generic equivalent of the AllTrails block-cooldown: a domain
                # that just returned 401/403 to us is very likely to do so
                # again for any other distinct URL on that same domain
                # (e.g. a different TripAdvisor restaurant page). Return a
                # synthetic blocked result immediately -- uncached, so a
                # later call after the cooldown naturally expires still gets
                # a real attempt -- instead of paying a full network timeout
                # for a call very unlikely to succeed.
                return False, 403, ""

        result = self._fetch_page_text_uncached(url, timeout=timeout)
        status = result[1]
        if domain and isinstance(status, int) and status in (401, 403):
            cooldown = float(
                getattr(self, "_domain_block_cooldown_seconds", DEFAULT_DOMAIN_BLOCK_COOLDOWN_SECONDS) or 0.0
            )
            if cooldown > 0:
                with self._request_cache_lock:
                    self._domain_blocked_until_ts[domain] = time.monotonic() + cooldown

        with self._request_cache_lock:
            self._page_text_cache[url] = result
        self._mark_persistent_cache_dirty()
        return result

    def _fetch_page_text_uncached(self, url: str, timeout: int = 8) -> tuple[bool, int | str, str]:
        if not hasattr(self, "_url_validator"):
            return False, "no_validator", ""
        get_text = getattr(self._url_validator, "get_text", None)
        if callable(get_text):
            try:
                out = get_text(url, timeout=timeout)
                if isinstance(out, tuple) and len(out) == 3:
                    final_url = str(getattr(self._url_validator, "_last_final_url", "") or "")
                    if final_url and hasattr(self, "_fetch_final_url_cache") and final_url != url:
                        self._fetch_final_url_cache[url] = final_url
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
                # AllTrails' bot-detection block is DataDome-driven and tends to
                # be sustained rather than a simple time-window rate limit --
                # sleeping out the cooldown and probing again almost always
                # just fails again. Return a synthetic blocked result
                # immediately (uncached, so a later call after the cooldown
                # naturally expires still gets a real attempt) instead of
                # tying up a worker thread waiting to re-fail.
                return False, 403, ""

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
            self._mark_persistent_cache_dirty()
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
        item_set = set(item_tokens)
        slug_set = set(slug_tokens)
        overlap = len(item_set & slug_set)
        required = cls._required_alltrails_token_matches(len(item_tokens))
        if overlap < required:
            return False

        # Use raw tokens for trail/loop: _significant_tokens excludes them as stop words
        # but they carry real semantic meaning ("Bryce Point Trail" ≠ "bryce-point").
        _trail_descriptors = {"trail", "loop"}
        _item_raw = set(re.findall(r"[a-z]+", (item_name or "").lower()))
        _slug_raw = set(slug.split())
        if _trail_descriptors & _item_raw and not (_trail_descriptors & _slug_raw):
            return False

        generic_trail_tokens = {
            "trail",
            "loop",
            "falls",
            "fall",
            "river",
            "creek",
            "mountain",
            "peak",
            "point",
            "canyon",
            "overlook",
            "vista",
            "ridge",
            "lake",
            "pass",
        }
        anchor_tokens = [token for token in item_tokens if token not in generic_trail_tokens]
        if anchor_tokens and not any(token in slug_set for token in anchor_tokens):
            return False
        return True

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
    def _direct_batch_row_matches_item(
        cls,
        row: dict[str, Any] | None,
        item_name: str,
        dest_name: str = "",
    ) -> bool:
        return cls._direct_batch_row_match_strength(row, item_name, dest_name) > 0

    @classmethod
    def _direct_batch_row_match_strength(
        cls,
        row: dict[str, Any] | None,
        item_name: str,
        dest_name: str = "",
    ) -> int:
        """0 = no match, 1 = weak (single short-name anchor token only), 2 = strong
        (full required token overlap, or an AllTrails slug match).

        Corroboration signal: when a batch has multiple ambiguously-matching rows
        for the same item name (e.g. two different "* Temple" entries in a
        "St. George" destination once the shared destination-name token is
        excluded), the row reached only through the lenient single-anchor-token
        fallback must not out-rank -- or get pooled equally with -- a row that
        actually satisfies the full token-overlap bar. Selection logic should
        prefer strength-2 rows over strength-1 rows when disambiguating.
        """
        if not row:
            return 0

        item_tokens = cls._significant_tokens(item_name)
        if not item_tokens:
            return 2

        raw_url = str(row.get("url", "") or "").strip()
        if raw_url and cls._is_alltrails_trail_url(raw_url):
            return 2 if cls._alltrails_slug_matches_item(raw_url, item_name) else 0

        if cls._candidate_text_matches_item_tokens(row, item_tokens):
            return 2

        # The row's own declared name (unlike the full blob, which includes
        # address/URL text prone to destination-name pollution -- see below) is
        # a deliberate, low-risk identifier. If *every* word of the item's name
        # (minus generic descriptive suffixes like "Overlook"/"View") is
        # present in the row's own name, that is a real match even when one of
        # those words happens to coincide with the destination's own name --
        # e.g. "Bryce Point" genuinely starting with "Bryce" for a "Bryce
        # Canyon" destination; excluding "bryce" below (as a destination-name
        # token) would otherwise leave no way to match "Bryce Point Overlook"
        # at all. This uses raw words (not _significant_tokens, which drops
        # "Point") and requires the *whole* remaining phrase, not a single
        # token, precisely so a single shared word like "bryce" alone can't
        # match an unrelated row such as "Bryce Canyon Lodge".
        row_name_only = str(row.get("name") or row.get("title") or "").strip()
        if row_name_only:
            item_raw_words = set(re.findall(r"[a-z]+", item_name.lower())) - GENERIC_VIEWPOINT_SUFFIX_TOKENS - _MATCH_STOPWORDS
            row_name_raw_words = set(re.findall(r"[a-z]+", row_name_only.lower()))
            if len(item_raw_words) >= 2 and item_raw_words <= row_name_raw_words:
                return 2

        # Destination-name tokens are not distinctive of a specific item -- e.g.
        # for a "St. George, Utah" destination, "george" trivially appears in
        # almost every row's address text (via the maps query string), so the
        # short-name anchor fallback below must not treat a shared
        # destination-name word alone as proof of an item match.
        dest_tokens = set(cls._significant_tokens(dest_name)) if dest_name else set()
        anchor_tokens = [t for t in item_tokens if t not in dest_tokens]

        row_blob = cls._candidate_text_blob(row)
        if len(item_tokens) <= 2 and anchor_tokens:
            anchor_overlap = sum(1 for token in anchor_tokens if len(token) >= 5 and token in row_blob.lower())
            if anchor_overlap >= 1:
                return 1

        parsed = urlparse(raw_url)
        query = parse_qs(parsed.query)
        query_parts: list[str] = []
        for key in ("query", "q", "name", "destination"):
            query_parts.extend(query.get(key, []))

        decoded_url = unquote(raw_url).replace("+", " ").lower()
        haystack = " ".join(
            [
                decoded_url,
                unquote(parsed.path or "").replace("-", " ").replace("_", " ").lower(),
                " ".join(unquote(v).replace("+", " ") for v in query_parts).lower(),
            ]
        )
        overlap = sum(1 for token in item_tokens if token in haystack)
        if overlap >= cls._required_general_token_matches(len(item_tokens)):
            return 2
        if len(item_tokens) <= 2 and anchor_tokens:
            anchor_overlap = sum(1 for token in anchor_tokens if len(token) >= 5 and token in haystack)
            if anchor_overlap >= 1:
                return 1
        return 0

    @classmethod
    def _direct_batch_url_matches_item(cls, url: str | None, item_name: str, dest_name: str = "") -> bool:
        candidate = str(url or "").strip()
        if not candidate:
            return False

        item_tokens = cls._significant_tokens(item_name)
        if not item_tokens:
            return True

        parsed = urlparse(candidate)
        query = parse_qs(parsed.query)
        query_parts: list[str] = []
        for key in ("query", "q", "name", "destination"):
            query_parts.extend(query.get(key, []))

        decoded_url = unquote(candidate).replace("+", " ").lower()
        haystack = " ".join(
            [
                decoded_url,
                unquote(parsed.path or "").replace("-", " ").replace("_", " ").lower(),
                " ".join(unquote(v).replace("+", " ") for v in query_parts).lower(),
            ]
        )
        overlap = sum(1 for token in item_tokens if token in haystack)
        if overlap >= cls._required_general_token_matches(len(item_tokens)):
            return True
        # Destination-name tokens (e.g. "george" for "St. George, Utah") trivially
        # appear in almost every candidate URL's address query string, so they
        # must not count as the sole anchor token proving a specific-item match.
        dest_tokens = set(cls._significant_tokens(dest_name)) if dest_name else set()
        anchor_tokens = [t for t in item_tokens if t not in dest_tokens]
        if len(item_tokens) <= 2 and anchor_tokens:
            anchor_overlap = sum(1 for token in anchor_tokens if len(token) >= 5 and token in haystack)
            return anchor_overlap >= 1
        return False

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
    def _candidate_mentions_conflicting_destination(
        cls,
        candidate: dict[str, Any] | None,
        dest_name: str,
        *,
        item_name: str | None = None,
    ) -> bool:
        if not candidate:
            return False
        text = cls._candidate_text_blob(candidate)
        if not text:
            return False

        if item_name and cls._direct_batch_row_matches_item(candidate, item_name, dest_name):
            return False

        dest_tokens = set(cls._significant_tokens(dest_name))
        if not dest_tokens:
            return False
        if any(token in text for token in list(dest_tokens)[:3]):
            return False

        # If a row explicitly names a different park/monument than the active
        # destination, treat it as off-target to avoid cross-destination leakage.
        for match in re.finditer(
            r"\b([a-z][a-z'\-]*(?:\s+[a-z][a-z'\-]*){0,3})\s+(national park|state park|national monument)\b",
            text,
        ):
            mention_tokens = set(cls._significant_tokens(match.group(1)))
            if mention_tokens and mention_tokens.isdisjoint(dest_tokens):
                return True
        return False

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
        # Use raw tokens for trail/loop since _significant_tokens treats them as stop words.
        _trail_descriptors = {"trail", "loop"}
        _item_raw = set(re.findall(r"[a-z]+", (item_name or "").lower()))
        _slug_raw = set(unquote(parsed.path.rsplit("/", 1)[-1]).lower().split("-"))
        if _trail_descriptors & _item_raw and not (_trail_descriptors & _slug_raw):
            return True
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
        lower_text = text.lower()
        _word_miles = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        }
        mile_match = re.search(r"\b(\d+(?:\.\d+)?)[ \-\u2013\u2014]*(?:mile|miles|mi)\b", lower_text)
        if mile_match:
            try:
                return float(mile_match.group(1))
            except (TypeError, ValueError):
                pass
        word_match = re.search(
            r"\b(" + "|".join(_word_miles) + r")[ \-]*(?:mile|miles|mi)\b", lower_text
        )
        if word_match:
            return float(_word_miles[word_match.group(1)])
        km_match = re.search(r"\b(\d+(?:\.\d+)?)[ \-\u2013\u2014]*(?:km|kilometer|kilometers|kilometre|kilometres)\b", lower_text)
        if km_match:
            try:
                return float(km_match.group(1)) * 0.621371
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _build_trail_threshold_note(*, miles: float | None, max_miles: float | None) -> str:
        try:
            miles_f = float(miles) if miles is not None else None
        except (TypeError, ValueError):
            miles_f = None
        try:
            max_f = float(max_miles) if max_miles is not None else None
        except (TypeError, ValueError):
            max_f = None

        if miles_f is None or max_f is None or max_f <= 0:
            return ""

        miles_text = f"{miles_f:.1f}".rstrip("0").rstrip(".")
        max_text = f"{max_f:.1f}".rstrip("0").rstrip(".")
        return (
            f"Trail distance is about {miles_text} miles, which exceeds the configured {max_text}-mile threshold; "
            "review route demands and permits before committing."
        )

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

    @classmethod
    def _extract_urls_from_text(cls, text: str) -> list[str]:
        raw_text = str(text or "")
        if not raw_text:
            return []

        extracted: list[str] = []
        for match in TEXT_URL_RE.findall(raw_text):
            candidate = match.strip().rstrip(".,;:!?")
            while candidate.endswith(")") and candidate.count("(") < candidate.count(")"):
                candidate = candidate[:-1]
            while candidate.endswith("]") and candidate.count("[") < candidate.count("]"):
                candidate = candidate[:-1]
            normalized = cls._normalize_direct_batch_authoritative_url(candidate)
            if normalized:
                extracted.append(normalized)
        return extracted

    @classmethod
    def _direct_batch_row_url_candidates(cls, row: dict[str, Any] | None) -> list[str]:
        if not row:
            return []

        candidates: list[str] = []
        seen: set[str] = set()

        def remember(url: str | None) -> None:
            normalized = cls._normalize_direct_batch_authoritative_url(url)
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            candidates.append(normalized)

        remember(row.get("url", ""))
        remember(row.get("maps_url", ""))

        for key in ("name", "title", "snippet", "description"):
            for extracted in cls._extract_urls_from_text(str(row.get(key, "") or "")):
                remember(extracted)

        return candidates

    @classmethod
    def _direct_batch_row_quality_metadata_for_url(
        cls, rows: list[dict[str, Any]], url: str | None
    ) -> dict[str, Any]:
        """Carry rating/vote/cuisine/price metadata from an already-harvested
        direct-batch row onto the item whose url we just accepted -- at zero
        extra network cost, since these rows were already fetched to resolve
        that url in the first place. Without this, only items built via the
        batch-shortfall padding path (_build_primary_items_from_direct_batch)
        ever got this data; every other per-item resolution site silently
        discarded it -- including the "existing URL already attached, just
        validate it" shortcuts (direct_batch_existing_url_preserved), which
        is how e.g. "Red Hills Desert Garden" ended up with no rating badge
        at all despite its harvested row carrying a 4.8 rating (dipstick55
        Theme D)."""
        normalized_url = cls._normalize_direct_batch_authoritative_url(url or "")
        if not normalized_url:
            return {}
        for row in rows:
            if row.get("rating") is None and not row.get("raw_rating") and row.get("votes") is None:
                continue
            if normalized_url in cls._direct_batch_row_url_candidates(row):
                return {
                    key: row.get(key)
                    for key in ("rating", "raw_rating", "votes", "source_type", "cuisine", "price_range")
                    if row.get(key) not in (None, "")
                }
        return {}

    @staticmethod
    def _has_alltrails_closure_marker(text: str) -> bool:
        lower_text = str(text or "").lower()
        if not lower_text:
            return False
        return any(marker in lower_text for marker in ALLTRAILS_CLOSURE_MARKERS)

    @staticmethod
    def _has_attraction_closure_marker(text: str) -> bool:
        lower_text = str(text or "").lower()
        if not lower_text:
            return False
        return any(marker in lower_text for marker in ATTRACTION_CLOSURE_MARKERS)

    @staticmethod
    def _is_generic_listing_title(text: str) -> bool:
        candidate = str(text or "").strip()
        if not candidate:
            return False
        return any(pattern.search(candidate) for pattern in GENERIC_LISTING_TITLE_PATTERNS)

    @staticmethod
    def _is_obviously_generic_url(lower_url: str) -> bool:
        if "yelp.com/search" in lower_url:
            return True
        for marker in GENERIC_BAD_URL_MARKERS:
            if marker not in lower_url:
                continue
            # NPS detail pages often live under /planyourvisit/<topic>. Allow
            # those through; generic section roots remain blocked later.
            if marker in {"/planyourvisit", "/plan-your-visit"} and "nps.gov" in lower_url:
                continue
            return True
        return False

    @staticmethod
    def _is_generic_directions_url(url: str) -> bool:
        """Return True for pages that are navigation/directions endpoints, not specific place pages."""
        lower = str(url or "").lower()
        path = urlparse(lower).path
        # NPS /planyourvisit/directions* pages are route guides, not specific stops.
        if "nps.gov" in lower and "/planyourvisit/" in path and "direction" in path:
            return True
        generic_endings = ("/directions", "/directions.htm", "/directions.asp", "/directions.html", "/how-to-get-here")
        return any(path.rstrip("/").endswith(e) for e in generic_endings)

    def _update_route_distance_and_time(
        self,
        *,
        ai: dict[str, Any],
        getting_here: dict[str, Any],
        origin_name: str,
        dest_name: str,
        origin_lat: Any,
        origin_lng: Any,
        dest_lat: Any,
        dest_lng: Any,
    ) -> None:
        """Overwrite AI-generated distance/time with values derived from the real route."""
        fetched_miles: float | None = None
        fetched_time: str | None = None
        if bool(
            getattr(self, "_route_distance_live_fetch_enabled", DEFAULT_ROUTE_DISTANCE_LIVE_FETCH_ENABLED)
        ):
            # Build the same route URL the assembler will render so we fetch the real data.
            stops = getting_here.get("en_route_stops", []) or []
            waypoint_names = [str(s.get("name", "") or "") for s in stops[:8] if s.get("name")]
            params = [f"destination={quote(dest_name)}", "travelmode=driving", "api=1"]
            if origin_name:
                params.append(f"origin={quote(origin_name)}")
            if waypoint_names:
                params.append("waypoints=" + quote("|".join(waypoint_names), safe="|"))
            route_url = "https://www.google.com/maps/dir/?" + "&".join(params)

            fetched_miles, fetched_time = self._parse_route_info_from_maps_html(route_url)

        haversine_miles, haversine_time = self._estimate_route_from_haversine(
            origin_lat, origin_lng, dest_lat, dest_lng
        )

        # Prefer fetched data; fall back to Haversine estimate.
        best_miles = fetched_miles or haversine_miles
        best_time = fetched_time or haversine_time

        if not best_miles and not best_time:
            return

        current_miles_raw = str(getting_here.get("distance_miles", "") or "").strip()
        current_time_raw = str(getting_here.get("drive_time", "") or "").strip()

        try:
            current_miles = float(re.sub(r"[^\d.]", "", current_miles_raw)) if current_miles_raw else None
        except ValueError:
            current_miles = None

        # Overwrite when the AI value is missing or deviates >25% from our estimate.
        if best_miles:
            if not current_miles or abs(current_miles - best_miles) / best_miles > 0.25:
                getting_here["distance_miles"] = str(int(round(best_miles)))
                ai["getting_here"] = getting_here
                logger.info("  Route distance updated to %d mi (was '%s')", int(round(best_miles)), current_miles_raw)
        if best_time and (not current_time_raw or not current_miles):
            getting_here["drive_time"] = best_time
            ai["getting_here"] = getting_here
            logger.info("  Route drive_time updated to '%s' (was '%s')", best_time, current_time_raw)

    def _parse_route_info_from_maps_html(self, route_url: str) -> tuple[float | None, str | None]:
        """Fetch Google Maps directions HTML and extract distance (miles) and duration."""
        ok, _status, html = self._fetch_page_text(route_url, timeout=10)
        if not ok or not html:
            return None, None

        miles: float | None = None
        duration_text: str | None = None

        # Distance — Google embeds values like "125 mi" in various places in the HTML/JSON
        for pat in (
            r'"(\d+(?:\.\d+)?)\s*mi"',
            r'(\d+(?:\.\d+)?)\s*mi(?:les?)?(?:["\]},\s])',
            r'"distanceText"\s*:\s*"(\d+(?:\.\d+)?)\s*mi',
        ):
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                try:
                    candidate = float(m.group(1))
                    if 1 < candidate < 5000:
                        miles = candidate
                        break
                except ValueError:
                    pass

        # Duration — look for patterns like "1 hr 49 min" or "2 hours 15 minutes"
        for pat in (
            r'"(\d+)\s*hr\s*(\d+)\s*min"',
            r'"(\d+)\s*hour[s]?\s*(\d+)\s*min',
            r'"durationText"\s*:\s*"(\d+)\s*hr\s*(\d+)\s*min',
            r'(\d+)\s*hr[s]?\s*(\d+)\s*min(?:["\]},\s])',
        ):
            m = re.search(pat, html, re.IGNORECASE)
            if m and len(m.groups()) == 2:
                h, mn = int(m.group(1)), int(m.group(2))
                duration_text = f"{h} hr {mn} min" if mn else f"{h} hr"
                break

        return miles, duration_text

    def _estimate_route_from_haversine(
        self,
        origin_lat: Any,
        origin_lng: Any,
        dest_lat: Any,
        dest_lng: Any,
        *,
        road_factor: float = 1.30,
        avg_speed_mph: float = 60.0,
    ) -> tuple[float | None, str | None]:
        """Estimate driving distance and time from straight-line (Haversine) distance."""
        origin = self._parse_lat_lng(origin_lat, origin_lng)
        dest = self._parse_lat_lng(dest_lat, dest_lng)
        if not origin or not dest:
            return None, None
        straight = self._haversine_miles(origin, dest)
        if straight <= 0.5:
            return None, None
        driving = straight * road_factor
        total_hours = driving / avg_speed_mph
        h = int(total_hours)
        mn = int(round((total_hours - h) * 60))
        if mn == 60:
            h += 1
            mn = 0
        time_str = f"{h} hr {mn} min" if (h and mn) else (f"{h} hr" if h else f"{mn} min")
        return round(driving), time_str

    @classmethod
    def _is_campground_focused_result_for_noncamping_item(
        cls,
        url: str,
        item_name: str,
        *,
        candidate_text: str = "",
        fetched_text: str = "",
    ) -> bool:
        item_l = str(item_name or "").lower()
        if any(token in item_l for token in ("campground", "campsite", "camping", "rv park", "rv campground", "camp")):
            return False

        lower_url = str(url or "").lower()
        if not lower_url:
            return False

        campground_url_markers = (
            "/campground",
            "/campgrounds",
            "/camping",
            "campground=",
            "recreation.gov/camping",
            "reserveamerica",
        )
        text_blob = f" {str(candidate_text or '').lower()} {str(fetched_text or '').lower()} "
        campground_text_markers = (
            " campground",
            " campgrounds",
            " campsite",
            " campsites",
            " rv park",
            " book campsite",
            " camping reservation",
            " reserve campsite",
        )

        return any(marker in lower_url for marker in campground_url_markers) or any(
            marker in text_blob for marker in campground_text_markers
        )

    @classmethod
    def _is_category_style_activity(cls, item_name: str) -> bool:
        text = (item_name or "").lower()
        if not text:
            return False
        activity_cues = (
            "fly fishing",
            "fishing",
            "stargazing",
            "birding",
            "kayaking",
            "rafting",
            "paddleboarding",
            "wine tasting",
            "food tour",
            "photography",
        )
        return any(cue in text for cue in activity_cues)

    @classmethod
    def _is_generic_geographic_url_for_category(cls, url: str, item_name: str) -> bool:
        lower = (url or "").lower()
        if not lower:
            return False

        activity_tokens = {
            token
            for token in cls._significant_tokens(item_name)
            if token in {"fishing", "stargazing", "birding", "kayaking", "rafting", "paddleboarding", "photography"}
        }
        if activity_tokens and any(token in lower for token in activity_tokens):
            return False

        if "google.com/maps/search" in lower or "google.com/search" in lower:
            return True
        if "wikipedia.org/wiki/" in lower:
            return True

        path = (urlparse(url).path or "").lower()
        geographic_markers = (
            "river",
            "lake",
            "mountain",
            "canyon",
            "park",
            "pass",
            "valley",
            "forest",
            "reservoir",
            "byway",
        )
        return any(marker in path for marker in geographic_markers)

    @staticmethod
    def _is_category_offer_listing_url(url: str) -> bool:
        lower = (url or "").lower()
        if not lower:
            return False
        path = (urlparse(url).path or "").lower()
        markers = (
            "/things-to-do",
            "/things2do",
            "/activities",
            "/activity",
            "/listing",
            "/listings",
            "/offers",
            "/offer",
            "/experiences",
            "/experience",
            "/plan-your-visit",
            "/planyourvisit",
            "/explore",
        )
        return any(marker in path for marker in markers)

    @staticmethod
    def _is_ambiguous_geographic_feature_name(item_name: str) -> bool:
        text = str(item_name or "").strip().lower()
        if not text:
            return False

        # Preserve expected map fallback behavior for explicit park entities.
        if " park" in text or text.endswith("park"):
            return False
        if "reserve" in text:
            return False

        geographic_markers = (
            "river",
            "canyon",
            "desert",
            "reserve",
            "mountain",
            "valley",
            "forest",
            "plateau",
            "mesa",
            "basin",
            "range",
        )
        specific_landmark_cues = (
            "trail",
            "road",
            "scenic drive",
            "overlook",
            "viewpoint",
            "visitor center",
            "museum",
        )

        if any(cue in text for cue in specific_landmark_cues):
            return False

        token_count = len(re.findall(r"[a-z0-9]+", text))
        has_geo_marker = any(marker in text for marker in geographic_markers)
        return has_geo_marker and token_count <= 4

    @staticmethod
    def _is_trail_like_attraction(name: str, attr_type: str, description: str = "") -> bool:
        type_norm = (attr_type or "").strip().lower()
        name_l = (name or "").lower()
        haystack = f"{name} {description}".lower()
        normalized = re.sub(r"[^a-z0-9\s]", "", haystack)

        non_trail_place_cues = (
            "museum",
            "history museum",
            "historic museum",
            "discovery site",
            "interpretive center",
            "visitor center",
            "cultural center",
        )
        if any(cue in name_l for cue in non_trail_place_cues):
            if not re.search(r"\b(trail|hike|loop|walk|trek|path|summit)\b", name_l):
                return False

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
                "discovery site",
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
    def _restaurant_significant_tokens(text: str) -> list[str]:
        """Token extractor for restaurant names.

        Lowers the minimum length to 3 and keeps 'grill' and 'cafe' since these
        often appear verbatim in restaurant domain slugs (e.g. kipsgrill.com).
        """
        tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
        stop = {
            "the", "and", "for", "with", "from", "near", "park", "national", "state", "trail",
            "road", "drive", "point", "restaurant", "utah", "colorado", "new", "mexico",
        }
        out: list[str] = []
        seen: set[str] = set()
        for t in tokens:
            if len(t) < 3 or t in stop:
                continue
            canonical = URLDiscoverer._canonical_token(t)
            if len(canonical) < 3 or canonical in stop or canonical in seen:
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
        strong_location_terms = (
            "national park",
            "state park",
            "junction",
            "utah",
            "colorado",
            "arizona",
            "new mexico",
            "nevada",
            "california",
        )
        if any(term in lowered for term in strong_location_terms):
            return True
        if re.search(r"\b(?:st|saint)\.?\s+[a-z]", lowered):
            return True
        return False

    @classmethod
    def _en_route_maps_fallback_query_text(cls, item_name: str, origin_name: str, dest_name: str) -> str:
        base = cls._maps_fallback_query_text(item_name, dest_name)
        origin = str(origin_name or "").strip()
        dest = str(dest_name or "").strip()
        lowered = base.lower()
        if dest and dest.lower() not in lowered:
            base = f"{base} near {dest}".strip()
        if origin and origin.lower() not in lowered and origin.lower() not in base.lower():
            base = f"{base} route from {origin}".strip()
        return base

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
