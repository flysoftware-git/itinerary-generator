"""Real road routing for a leg, from OpenRouteService, when a key is configured.

`road_estimate` is honest about being an estimate, and its own docstring names
where it is wrong: *"a bay driven around, a peninsula approached the long way"*.
Straight line x 1.30 cannot know a leg crosses water. Measured 2026-09-14 on
three Puget Sound legs against OpenRouteService's `driving-car` profile:

| leg | straight x 1.30, banded speed | routed |
|---|---|---|
| Issaquah -> Port Townsend | ~69 mi, ~1.4 h | 75.8 mi, 2.5 h, 7% ferry |
| Port Townsend -> Oak Harbor | ~16 mi, ~0.4 h | 21.2 mi, 1.4 h, 27% ferry |
| Issaquah -> Lilliwaup | ~66 mi, ~1.4 h | 109.5 mi, 2.3 h, no ferry |

The estimate is off by more than an hour on two of three, and the one it gets
nearest on distance it gets wrong by the long way round a canal.

## Why OpenRouteService

OpenStreetMap data, so it covers wherever OSM does rather than one country;
the `driving-car` profile routes ferries and says so (`extra_info: waytype`,
value 9); and the free tier is enough for a trip's legs with a cache in front.
The key is optional. With no `OPENROUTESERVICE_API_KEY` nothing here makes a
request, and every caller gets exactly the estimate it got before.

## What a routed duration does not include

Time on the ferry is in the duration; **waiting for the ferry is not.** A
sailing every forty minutes can add most of an hour, and no routing engine
knows the schedule. `ferry_share` is returned so a caller can say a leg
includes a crossing rather than let the number imply it is door to door.

## Failure is a fallback, never an error

No key, a timeout, a quota refusal (HTTP 429), an unroutable pair (HTTP 404) or
a reply that does not parse: `route_leg` returns None and the caller estimates.
Only successful routes are cached, so a refusal today is asked again tomorrow.

## The line the route follows

The directions reply already carries the route's shape: `routes[0].geometry`,
an encoded polyline at precision 5 (checked against a recorded reply, whose
decoded extent matches its own `bbox` to the fifth decimal and whose 358 points
end at `way_points[-1] + 1`). It is kept as `RoutedLeg.geometry`, a tuple of
`(lat, lng)` points, simplified (Douglas-Peucker) to at most
`MAX_GEOMETRY_POINTS` so a cache entry or a page carrying it stays small. A map
can then draw the road instead of a straight line between stops.

`extras.waytype.values` is a list of `[from, to, waytype]` index ranges into
those same points, so the ferry crossings come for free: `ferry_spans` holds
them as `(from, to)` indices into the simplified geometry. The endpoints of
every span survive simplification, so a span still starts and ends at the
terminals.

A cache entry written before geometry was kept has none. It loads as it always
did, with `geometry` None: the leg is not asked again just to fetch a shape, so
an existing cache never turns into a burst of requests against the quota.
Removing the cache file is how an install opts into re-routing for shapes.
"""

from __future__ import annotations

import heapq
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from math import cos, radians, sqrt
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The environment variable holding the key. Unset means no routing.
API_KEY_ENV = "OPENROUTESERVICE_API_KEY"

ENDPOINT = "https://api.openrouteservice.org/v2/directions/driving-car"

#: Seconds for one request. A leg that cannot be routed in this long is
#: estimated instead; a slow router must not become a slow build.
TIMEOUT_S = 15

#: OpenRouteService's `waytype` value for a ferry.
FERRY_WAYTYPE = 9

#: Relative on purpose, like the URL-discovery cache: it lives beside a run.
#: `tests/conftest.py` points it at a temp file so a test run never rewrites a
#: real cache.
DEFAULT_CACHE_PATH = ".cache/routing/routes.json"

#: OpenRouteService's encoded polyline precision (decimal places), for 2D
#: geometry. Verified against a recorded reply; see the module docstring.
POLYLINE_PRECISION = 5

#: Upper bound on the points kept for one leg's geometry. A 21-mile ferry leg
#: comes back with 358; a long interstate leg with thousands. Three hundred
#: draws a smooth line at any zoom a whole-trip map is shown at.
MAX_GEOMETRY_POINTS = 300

#: A point off the simplified line by less than this, in degrees (~1 m), is
#: not worth keeping even when the bound would allow it.
_MIN_DEVIATION_DEG = 0.00001

_lock = threading.Lock()

Point = tuple[float, float]
Span = tuple[int, int]


@dataclass(frozen=True)
class RoutedLeg:
    """One routed leg. Miles and minutes are the route's, not an estimate's."""

    miles: float
    minutes: float
    #: Share of the route's distance on a ferry, 0.0 to 1.0.
    ferry_share: float = 0.0
    #: The road the route follows, as `(lat, lng)` points from origin to
    #: destination, at most `MAX_GEOMETRY_POINTS`. None when not available --
    #: including a leg served from a cache entry written before this was kept.
    geometry: tuple[Point, ...] | None = None
    #: Ferry crossings as inclusive `(from, to)` index ranges into `geometry`.
    #: Empty when there is no ferry or no geometry.
    ferry_spans: tuple[Span, ...] = ()

    @property
    def ferry_miles(self) -> float:
        return round(self.miles * self.ferry_share, 1)

    @property
    def has_ferry(self) -> bool:
        return self.ferry_share > 0.0


def api_key() -> str:
    return str(os.environ.get(API_KEY_ENV) or "").strip()


def _cache_key(origin: tuple[float, float], dest: tuple[float, float]) -> str:
    # Four decimal places is about eleven metres: the same town centre geocoded
    # twice lands in one key, and two different towns never share one.
    return f"{origin[0]:.4f},{origin[1]:.4f}>{dest[0]:.4f},{dest[1]:.4f}"


def _load_cache(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(path: str | os.PathLike[str], cache: dict[str, Any]) -> None:
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(json.dumps(cache, indent=0, sort_keys=True), encoding="utf-8")
        temp.replace(target)
    except OSError as exc:
        logger.info("Routing cache not saved (%s).", exc)


def decode_polyline(encoded: str, precision: int = POLYLINE_PRECISION) -> list[Point]:
    """`(lat, lng)` points from an encoded polyline (the Google algorithm).

    Raises ValueError on a string that ends mid-value.
    """
    factor = 10 ** precision
    points: list[Point] = []
    index = lat = lng = 0
    length = len(encoded)
    while index < length:
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                if index >= length:
                    raise ValueError("polyline ends mid-value")
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lng += deltas[1]
        points.append((round(lat / factor, precision), round(lng / factor, precision)))
    return points


def _farthest(points: list[Point], first: int, end: int) -> tuple[float, int]:
    """The point strictly between `first` and `end` farthest from the chord
    joining them, in (roughly) degrees of latitude, and its index."""
    lat1, lng1 = points[first]
    lat2, lng2 = points[end]
    # Longitude degrees shrink with latitude; scale so distance is even.
    scale = cos(radians((lat1 + lat2) / 2.0))
    ax, ay = lng1 * scale, lat1
    dx, dy = lng2 * scale - ax, lat2 - ay
    length = sqrt(dx * dx + dy * dy)
    worst, worst_at = -1.0, first
    for i in range(first + 1, end):
        px, py = points[i][1] * scale - ax, points[i][0] - ay
        dist = sqrt(px * px + py * py) if length == 0.0 else abs(px * dy - py * dx) / length
        if dist > worst:
            worst, worst_at = dist, i
    return worst, worst_at


def simplify_geometry(
    points: list[Point],
    spans: list[Span] | tuple[Span, ...] = (),
    *,
    max_points: int = MAX_GEOMETRY_POINTS,
) -> tuple[tuple[Point, ...], tuple[Span, ...]]:
    """`points` reduced to at most `max_points`, and `spans` re-indexed into it.

    Douglas-Peucker, ranked: instead of one tolerance, which either keeps too
    many points or overshoots to far too few, the point deviating most from
    the line drawn so far is added next, until `max_points` are kept or none
    deviates by more than about a metre. Iterative, so a leg of many thousand
    points cannot exhaust the recursion limit.

    Every span endpoint is kept, so a ferry span still begins and ends where
    the crossing does. The endpoints of the leg and of the spans are the only
    points that cannot be dropped, so a leg with more span endpoints than
    `max_points` is the one case that can exceed the bound.
    """
    if len(points) < 2:
        return tuple(points), ()
    last = len(points) - 1
    pinned = {0, last}
    clean_spans = []
    for start, end in spans:
        start, end = max(0, min(last, int(start))), max(0, min(last, int(end)))
        if end > start:
            clean_spans.append((start, end))
            pinned.update((start, end))
    kept = set(pinned)
    anchors = sorted(pinned)
    heap: list[tuple[float, int, int, int]] = []
    for first, end in zip(anchors, anchors[1:]):
        if end - first >= 2:
            worst, at = _farthest(points, first, end)
            heapq.heappush(heap, (-worst, at, first, end))
    while heap and len(kept) < max_points:
        negative, at, first, end = heapq.heappop(heap)
        if -negative <= _MIN_DEVIATION_DEG:
            break
        kept.add(at)
        for a, b in ((first, at), (at, end)):
            if b - a >= 2:
                worst, where = _farthest(points, a, b)
                heapq.heappush(heap, (-worst, where, a, b))
    kept = sorted(kept)
    position = {original: new for new, original in enumerate(kept)}
    return (tuple(points[i] for i in kept),
            tuple((position[a], position[b]) for a, b in clean_spans))


def _ferry_ranges(route: dict[str, Any]) -> list[Span]:
    """`(from, to)` point indices of every ferry stretch in `extras.waytype`."""
    values = ((route.get("extras") or {}).get("waytype") or {}).get("values") or []
    spans: list[Span] = []
    for entry in values:
        try:
            start, end, value = int(entry[0]), int(entry[1]), int(float(entry[2]))
        except (TypeError, ValueError, IndexError):
            continue
        if value != FERRY_WAYTYPE or end <= start:
            continue
        if spans and spans[-1][1] == start:
            spans[-1] = (spans[-1][0], end)
        else:
            spans.append((start, end))
    return spans


def _geometry(route: dict[str, Any]) -> tuple[tuple[Point, ...] | None, tuple[Span, ...]]:
    encoded = route.get("geometry")
    if not isinstance(encoded, str) or not encoded:
        return None, ()
    try:
        points = decode_polyline(encoded)
    except ValueError:
        return None, ()
    if len(points) < 2:
        return None, ()
    spans = [(a, b) for a, b in _ferry_ranges(route) if b < len(points)]
    return simplify_geometry(points, spans)


def parse_route(payload: Any) -> RoutedLeg | None:
    """A `RoutedLeg` from an OpenRouteService directions reply, or None.

    Expects `units: mi`. `summary.distance` is miles and `summary.duration`
    seconds; the ferry share is the `amount` (a percentage of the route) of
    the `waytype` entry whose value is 9. The encoded `geometry`, where the
    reply has one, becomes `geometry` and `ferry_spans`; a reply without it
    still routes, with no shape.
    """
    try:
        route = payload["routes"][0]
        summary = route["summary"]
        miles = float(summary["distance"])
        minutes = float(summary["duration"]) / 60.0
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if miles <= 0 or minutes <= 0:
        return None
    share = 0.0
    waytypes = ((route.get("extras") or {}).get("waytype") or {}).get("summary") or []
    for entry in waytypes:
        try:
            if int(float(entry.get("value"))) == FERRY_WAYTYPE:
                share += float(entry.get("amount") or 0.0) / 100.0
        except (TypeError, ValueError):
            continue
    geometry, ferry_spans = _geometry(route)
    return RoutedLeg(miles=round(miles, 1), minutes=round(minutes, 1),
                     ferry_share=round(min(1.0, max(0.0, share)), 4),
                     geometry=geometry, ferry_spans=ferry_spans)


def _leg_to_cache(leg: RoutedLeg) -> dict[str, Any]:
    entry: dict[str, Any] = {"miles": leg.miles, "minutes": leg.minutes,
                             "ferry_share": leg.ferry_share}
    if leg.geometry is not None:
        entry["geometry"] = [[lat, lng] for lat, lng in leg.geometry]
        entry["ferry_spans"] = [[a, b] for a, b in leg.ferry_spans]
    return entry


def _leg_from_cache(entry: dict[str, Any]) -> RoutedLeg:
    """Raises KeyError, TypeError or ValueError on an entry that is not a leg.

    An entry with no `geometry` -- every entry written before it was kept --
    is a leg with `geometry` None, not a miss. A malformed geometry is dropped
    the same way rather than discarding the miles and minutes with it.
    """
    geometry: tuple[Point, ...] | None = None
    spans: tuple[Span, ...] = ()
    raw = entry.get("geometry")
    if isinstance(raw, list) and len(raw) >= 2:
        try:
            geometry = tuple((float(p[0]), float(p[1])) for p in raw)
            spans = tuple((int(s[0]), int(s[1])) for s in entry.get("ferry_spans") or []
                          if 0 <= int(s[0]) < int(s[1]) < len(geometry))
        except (TypeError, ValueError, IndexError):
            geometry, spans = None, ()
    return RoutedLeg(float(entry["miles"]), float(entry["minutes"]),
                     float(entry.get("ferry_share") or 0.0),
                     geometry=geometry, ferry_spans=spans)


def route_leg(
    origin: tuple[float, float],
    dest: tuple[float, float],
    *,
    key: str | None = None,
    cache_path: str | os.PathLike[str] | None = None,
) -> RoutedLeg | None:
    """Route one driving leg between two `(lat, lng)` points, or None.

    None whenever there is no key, or the router did not give a usable answer;
    see the module docstring. Cached by rounded coordinates at `cache_path`
    (default `DEFAULT_CACHE_PATH`), successful answers only.
    """
    key = api_key() if key is None else str(key).strip()
    if not key:
        return None
    path = DEFAULT_CACHE_PATH if cache_path is None else cache_path
    cache_key = _cache_key(origin, dest)
    with _lock:
        cached = _load_cache(path).get(cache_key)
    if isinstance(cached, dict):
        try:
            return _leg_from_cache(cached)
        except (KeyError, TypeError, ValueError):
            pass

    body = json.dumps({
        # OpenRouteService takes [lng, lat].
        "coordinates": [[origin[1], origin[0]], [dest[1], dest[0]]],
        "units": "mi",
        "instructions": False,
        "extra_info": ["waytype"],
    }).encode("utf-8")
    request = urllib.request.Request(ENDPOINT, data=body, headers={
        "Authorization": key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        logger.info("Routing refused %s (HTTP %s); estimating instead.", cache_key, exc.code)
        return None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.info("Routing unavailable for %s (%s); estimating instead.", cache_key, exc)
        return None

    routed = parse_route(payload)
    if routed is None:
        logger.info("Routing reply for %s did not parse; estimating instead.", cache_key)
        return None
    with _lock:
        cache = _load_cache(path)
        cache[cache_key] = _leg_to_cache(routed)
        _save_cache(path, cache)
    return routed
