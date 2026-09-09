"""
geocoder.py — Geocode destination names to lat/lng using Nominatim.

Nominatim is free, runs on donated hardware, and its usage policy asks for
at most one request per second from an identifying User-Agent. Two builds
started seconds apart broke that on 2026-09-06 and earned a block that
outlasted the retry ladder, so a run that had already paid for its AI
content died in stage 2 with nothing to show for it. Both halves of that
are handled here: requests are spaced, and a coordinate already known is
never asked for twice.
"""
from __future__ import annotations
import json
import logging
import threading
import time
from pathlib import Path

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderRateLimited, GeocoderServiceError, GeocoderTimedOut

logger = logging.getLogger(__name__)

# Disambiguation hints: names that Nominatim resolves to the wrong place
# Maps destination name (lowercase) → "country/region" string to append
GEOCODE_COUNTRY_HINTS: dict[str, str] = {
    "santa fe": "New Mexico, USA",
}

#: Nominatim's usage policy sets an absolute maximum of one request per
#: second. This is the floor between the START of one request and the start
#: of the next, not a sleep after each: a request that itself took two
#: seconds has already paid the interval.
MIN_REQUEST_INTERVAL_SECONDS = 1.1

#: Seconds to wait after each 429. Longer than the 15/30/45 it replaces:
#: that ladder gave up after 90 seconds, and the block that prompted this
#: took about five minutes to clear. A build that waits finishes; a build
#: that raises in stage 2 has thrown away everything it spent getting there.
RATE_LIMIT_BACKOFF_SECONDS: tuple[int, ...] = (15, 60, 150, 300)

#: Where resolved coordinates live between runs. Under `.cache/`, which is
#: gitignored, alongside the image cache.
CACHE_PATH = Path(".cache/geocode/coordinates.json")


class Geocoder:
    """Name → (lat, lng), with a cache that outlives the process.

    The cache is keyed on the QUERY actually sent rather than the
    destination name, so adding or changing a `GEOCODE_COUNTRY_HINTS` entry
    misses rather than returning a coordinate resolved under the old hint.

    Only successes are stored. "No results" is usually a name the manifest
    needs to fix, and caching it would make the fix invisible until somebody
    thought to clear the cache.
    """

    _cache: dict[str, tuple[float, float]] = {}
    _cache_lock = threading.Lock()
    _cache_loaded = False

    _throttle_lock = threading.Lock()
    #: None means "no request has gone out yet", which must not be confused
    #: with "a request went out at monotonic time 0.0" -- on a platform
    #: whose clock starts near zero that would make the first lookup of
    #: every build sleep for nothing.
    _last_request_at: float | None = None

    def __init__(self, user_agent: str = "RoadTripItineraryGenerator/1.0", timeout: int = 5) -> None:
        self.geolocator = Nominatim(user_agent=user_agent, timeout=timeout)
        self._load_cache()

    # ── Disk cache ───────────────────────────────────────────────────────

    @classmethod
    def _load_cache(cls) -> None:
        """Read the on-disk cache once per process.

        Any failure here is a miss, never an error. A corrupt or unreadable
        cache file must not be able to stop a build; the worst it can cost
        is the requests it would have saved.
        """
        with cls._cache_lock:
            if cls._cache_loaded:
                return
            cls._cache_loaded = True
            try:
                raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            if not isinstance(raw, dict):
                return
            for query, value in raw.items():
                try:
                    lat, lng = value
                    cls._cache[str(query)] = (float(lat), float(lng))
                except (TypeError, ValueError):
                    continue
            if cls._cache:
                logger.debug("Geocoder: %d cached coordinate(s) loaded", len(cls._cache))

    @classmethod
    def _remember(cls, query: str, coords: tuple[float, float]) -> None:
        with cls._cache_lock:
            cls._cache[query] = coords
            snapshot = dict(cls._cache)
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            # Written whole and moved into place. A build interrupted
            # mid-write would otherwise leave a truncated file, which the
            # next run reads as no cache at all and silently re-fetches.
            tmp = CACHE_PATH.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({k: list(v) for k, v in snapshot.items()}, indent=1, sort_keys=True),
                encoding="utf-8",
            )
            tmp.replace(CACHE_PATH)
        except OSError as exc:
            # A cache that cannot be written is slower, not broken.
            logger.debug("Geocoder: could not write cache: %s", exc)

    @classmethod
    def clear_cache(cls) -> None:
        """Forget everything, in memory and on disk.

        For tests, and for a manifest whose coordinates are believed wrong.
        """
        with cls._cache_lock:
            cls._cache = {}
            cls._cache_loaded = False
        try:
            CACHE_PATH.unlink()
        except OSError:
            pass

    # ── Rate limiting ────────────────────────────────────────────────────

    @classmethod
    def _wait_for_slot(cls) -> None:
        """Block until MIN_REQUEST_INTERVAL_SECONDS have passed since the
        last request started.

        Held on the class rather than the instance, because the limit
        belongs to the service. main.py builds one Geocoder, but nothing
        stops a second, and the interval has to cover both.
        """
        with cls._throttle_lock:
            now = time.monotonic()
            if cls._last_request_at is not None:
                wait = cls._last_request_at + MIN_REQUEST_INTERVAL_SECONDS - now
                if wait > 0:
                    time.sleep(wait)
                    now = time.monotonic()
            cls._last_request_at = now

    # ── Lookup ───────────────────────────────────────────────────────────

    def _geocode(self, name: str, retries: int | None = None) -> tuple[float, float]:
        hint = GEOCODE_COUNTRY_HINTS.get(name.lower())
        query = f"{name}, {hint}" if hint else name
        if hint:
            logger.debug("Geocoder disambiguation: '%s' → '%s'", name, query)

        with Geocoder._cache_lock:
            cached = Geocoder._cache.get(query)
        if cached is not None:
            return cached

        attempts = len(RATE_LIMIT_BACKOFF_SECONDS) if retries is None else retries
        for attempt in range(attempts + 1):
            try:
                Geocoder._wait_for_slot()
                location = self.geolocator.geocode(query)
                if location:
                    coords = (location.latitude, location.longitude)
                    Geocoder._remember(query, coords)
                    return coords
                raise ValueError(f"Nominatim returned no results for: '{query}'")
            except GeocoderRateLimited:
                if attempt >= attempts:
                    raise
                backoff = RATE_LIMIT_BACKOFF_SECONDS[
                    min(attempt, len(RATE_LIMIT_BACKOFF_SECONDS) - 1)
                ]
                logger.warning(
                    "Geocoder rate-limited (429) for '%s', backing off %ds (attempt %d of %d)",
                    name, backoff, attempt + 1, attempts,
                )
                time.sleep(backoff)
            except (GeocoderTimedOut, GeocoderServiceError) as exc:
                if attempt >= attempts:
                    raise
                logger.warning("Geocoder retry %d for '%s': %s", attempt + 1, name, exc)
                time.sleep(2)
        raise ValueError(f"Geocoding failed for: '{name}'")
