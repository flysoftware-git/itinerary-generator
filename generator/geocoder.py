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

#: How a lodging coordinate was arrived at, recorded on the lodging block by
#: `Geocoder.place_lodging` so the page can say which one it is rather than
#: leaving the reader with a pin -- or no pin -- and no explanation.
#:
#: "street"   the address exactly as the confirmation prints it resolved;
#: "town"     only the reduced, town-level form resolved, so the pin is the
#:            town and is out by however far the property is from it;
#: "unplaced" nothing resolved and there are no coordinates at all.
PRECISION_STREET = "street"
PRECISION_TOWN = "town"
PRECISION_UNPLACED = "unplaced"

#: Fewer comma-separated parts than this and there is nothing safe to drop.
#: A two-part "Oak Harbor, WA" reduced by one leaves the bare state, which
#: Nominatim places happily -- in the middle of Washington. A confidently
#: wrong pin is worse than the missing one this fallback exists to fix, so
#: the reduction insists on enough address left over to still name a place.
_MIN_PARTS_TO_REDUCE = 3


def town_level_query(location: str, locality: dict | None = None) -> str | None:
    """The same stay, asked for as a place rather than as an address.

    Reservation ingestion fills `lodging.location` with the street line
    exactly as the confirmation prints it, and that turns out to be the one
    shape Nominatim is worst at. Probed directly against a real booking:

        33221 State Road 20, Oak Harbor, WA 98277 United States   nothing
        33221 State Road 20, Oak Harbor, WA 98277                 nothing
        33221 State Road 20, Oak Harbor, WA                       nothing
        Oak Harbor, WA 98277 United States                        resolves
        Oak Harbor, WA                                            resolves

    So it is the STREET LINE that defeats it -- not the country, and not the
    postcode. That is worth stating plainly, because the obvious repair is a
    ladder that strips the country, then the postcode, then gives up, and
    that ladder would have fixed nothing here: every rung of it still
    carries "33221 State Road 20". A state-route address is a shape the free
    data set does not hold, and no amount of tidying the tail helps.

    `lodging.locality` is preferred when ingestion supplied it: it is the
    town, region and country already stated as separate parts, so it needs
    no parsing at all and cannot mistake a second address line for a town.
    Failing that, the first comma-separated part is dropped, which is where
    the street line sits in every form we have seen.

    Returns None when there is nothing to reduce -- the caller then has the
    address it already tried and no second question worth asking.
    """
    if isinstance(locality, dict):
        stated = ", ".join(
            part
            for part in (
                str(locality.get(key, "") or "").strip()
                for key in ("city", "region", "country")
            )
            if part
        )
        if stated:
            return stated

    pieces = [piece.strip() for piece in str(location or "").split(",")]
    pieces = [piece for piece in pieces if piece]
    if len(pieces) < _MIN_PARTS_TO_REDUCE:
        return None
    return ", ".join(pieces[1:]) or None


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
    #: Serialises the *file* write, which `_cache_lock` never did.
    #:
    #: `_remember` took its snapshot under `_cache_lock`, released it, and then
    #: wrote -- so two threads could write in the opposite order to the one they
    #: snapshotted in, and the older snapshot landed last. Every entry it did
    #: not contain was gone from the file while sitting correctly in memory,
    #: which is why a build sees a cache smaller than the number of places it
    #: looked up and re-fetches them on the next run.
    #:
    #: A separate lock rather than holding `_cache_lock` across the I/O: reads
    #: of the in-memory cache are on the hot path of every lookup, and blocking
    #: them behind a disk write is a different bug.
    _write_lock = threading.Lock()
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
        # One writer at a time, and the snapshot taken **inside** that lock.
        #
        # Taken outside it, two threads could serialise their writes in the
        # opposite order to their snapshots and leave the older one on disk --
        # a lost update, measured at roughly one run in six with eight threads.
        # Reading the cache again here costs a dictionary copy and makes the
        # last write the newest by construction.
        #
        # It also makes the shared temporary path safe. Every thread wrote
        # `place_coords.json.tmp` -- the same file -- so two of them could
        # interleave bytes into it and `replace` could move a half-written file
        # into place, which is the corruption this function's own test is named
        # for and a worse outcome than the lost entry.
        with cls._write_lock:
            with cls._cache_lock:
                snapshot = dict(cls._cache)
            try:
                CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                # Written whole and moved into place. A build interrupted
                # mid-write would otherwise leave a truncated file, which the
                # next run reads as no cache at all and silently re-fetches.
                tmp = CACHE_PATH.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps({k: list(v) for k, v in snapshot.items()},
                               indent=1, sort_keys=True),
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

    # ── Lodging ──────────────────────────────────────────────────────────

    def place_lodging(self, lodging: dict) -> str:
        """Put coordinates on a lodging block, and say how they were got.

        Mutates `lodging` in place, setting `lat`/`lng` when something
        resolved and `location_precision` always -- one of PRECISION_STREET,
        PRECISION_TOWN or PRECISION_UNPLACED. The precision is the point:
        before it existed, a stay whose address Nominatim could not place
        left one WARNING in the build log and a page that said nothing at
        all, so the reader saw a missing pin and had no way to know whether
        the property had no location or the geocoder had merely lost.

        Both lookups go through `_geocode`, which is what makes this safe to
        call for every stop: the shared cache means a reduced form already
        resolved for a neighbouring stay costs nothing, and the class-wide
        throttle means the extra question still respects Nominatim's one
        request per second. A second call path would have had neither.

        Never raises. A stay that cannot be placed is a degraded page, not a
        dead build -- the same posture stage 2 already took, kept here so
        that moving the logic did not quietly change it.
        """
        location = str(lodging.get("location", "") or "").strip()
        if not location:
            lodging["location_precision"] = PRECISION_UNPLACED
            return PRECISION_UNPLACED

        reduced = town_level_query(location, lodging.get("locality"))
        # Ordered most precise first, and deduplicated: `town_level_query`
        # can hand back the address unchanged when `locality` restates it,
        # and asking Nominatim the same question twice is exactly what the
        # cache exists to avoid.
        attempts = [(location, PRECISION_STREET)]
        if reduced and reduced != location:
            attempts.append((reduced, PRECISION_TOWN))

        for query, precision in attempts:
            try:
                lat, lng = self._geocode(query)
            except Exception as exc:  # noqa: BLE001 -- any failure is a miss
                logger.debug("Lodging geocode miss for '%s': %s", query, exc)
                continue
            lodging["lat"] = lat
            lodging["lng"] = lng
            lodging["location_precision"] = precision
            if precision == PRECISION_TOWN:
                logger.warning(
                    "Lodging placed from the town rather than the street address: "
                    "'%s' did not resolve, '%s' did",
                    location, query,
                )
            return precision

        lodging["location_precision"] = PRECISION_UNPLACED
        logger.warning("Lodging could not be placed at all: '%s'", location)
        return PRECISION_UNPLACED
