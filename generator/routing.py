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

## A place is not always on a road

A geocoder answers with the *place*, and plenty of places are not on roads: a
creek is its stream, a trailhead its car park, a lake its middle. Asked to
route from such a point, OpenRouteService refuses the whole leg with HTTP 404
and its own code 2010, *"Could not find routable point within a radius of N
metres"* -- and the leg then falls back to a straight line, which is a far
worse answer than the road three hundred metres away.

So every request carries a `radiuses` allowance per coordinate
(`SNAP_RADIUS_M`), letting the router start from the nearest road; and a leg
still refused for that one reason is asked **once** more at
`WIDE_SNAP_RADIUS_M`, because a rural point is exactly where the nearest road
is furthest. Only then is it a straight line. The snapped answer is cached
under the ordinary key, so the wider ask happens once per pair, ever.

## Not every leg is driven

`profile` selects the OpenRouteService profile (`PROFILES`), `driving-car`
unless a caller names another. A cycling leg routed as `cycling-regular`
follows the trail a car cannot and returns the ride's minutes, not a car's on
the highway beside it. Each profile has its own cache key -- the driving key
is unchanged, so an existing cache still hits -- and an unknown profile raises
rather than building a URL nobody meant.

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

## A leg that crosses water: the ferry, or the long way round

A router returns the fastest route it knows, and it does not know the wait at
the terminal. So a leg whose route takes a ferry is asked a second time with
ferries avoided (`options.avoid_features: ["ferries"]`), and both answers are
kept: `road_estimate.LegEstimate.ferry` and `.land`. `choose_ferry_or_land`
then picks one under a `FerryPolicy`:

- each crossing (one `ferry_spans` entry) costs `wait_minutes_per_crossing`
  (default 45) of waiting and boarding on top of the routed sailing time;
- the land route is chosen unless the ferry route, with that allowance, is
  **more than** `prefer_land_within_minutes` (default 15) faster -- exactly 15
  faster is still land;
- a per-call `ferry_preference` of `avoid` takes land whenever a land route
  exists, and `prefer` takes the ferry;
- with no land route (an island without a bridge), the ferry is chosen and the
  reason says so.

The defaults live in `config.yaml` under `routing.ferry`. The engine's job is
the two candidates and the numbers; a caller that wants to show the choice, or
let a reader switch it, reads `chosen`, `chosen_reason` and the route it did
not choose.

The ferry-avoiding answer is cached under its own key. A 404 from that request
means no land route exists; unlike a refusal of the ordinary route it is
remembered (as `{"no_route": true}`), because an island does not grow a bridge
between runs and asking again would spend quota on every build. A leg without
a ferry makes no second request. A cache entry written before alternatives
existed is still an ordinary routed leg.
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

#: Every directions endpoint, less the profile. The profile is the last path
#: segment, and the only thing that differs between them.
ENDPOINT_BASE = "https://api.openrouteservice.org/v2/directions"

#: The profile a caller that names none gets, which is what every caller got
#: before profiles were an option.
DEFAULT_PROFILE = "driving-car"

#: The profiles this module will ask OpenRouteService for. Named rather than
#: passed through, so a typo is refused here instead of becoming a 404 from a
#: URL nobody meant to build.
PROFILES = ("driving-car", "driving-hgv",
            "cycling-regular", "cycling-road", "cycling-mountain",
            "foot-walking", "foot-hiking")

#: The driving endpoint, unchanged, for callers that import it.
ENDPOINT = f"{ENDPOINT_BASE}/{DEFAULT_PROFILE}"

#: Seconds for one request. A leg that cannot be routed in this long is
#: estimated instead; a slow router must not become a slow build.
TIMEOUT_S = 15

#: How far either side of a given coordinate OpenRouteService may look for a
#: road to start or finish on, in metres. A geocoder answers with the *place*,
#: and a place is not always on a road: a creek is its stream, a trailhead its
#: car park, a lake its middle. Without this the router refuses the pair and
#: the whole leg falls back to a straight line, which is a worse answer than
#: the road a few hundred metres away.
SNAP_RADIUS_M = 350

#: The second ask, once, for a point nothing was found near. Rural coordinates
#: -- the ones a creek or a trailhead produces -- are exactly where the nearest
#: road is furthest, so one wider try is worth a request. Beyond this the point
#: really is not near a road and the straight line is the honest answer.
WIDE_SNAP_RADIUS_M = 3000

#: OpenRouteService's own code for *"Could not find routable point within a
#: radius of N metres of specified coordinate"*, returned with HTTP 404. It is
#: the one refusal that says *ask again, further out* rather than *no*.
NO_ROUTABLE_POINT = 2010

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

    @property
    def crossings(self) -> int:
        """Ferry crossings on the route: one per `ferry_spans` entry.

        A leg with a ferry share but no spans (a reply or cache entry without
        geometry) still crosses at least once, and counts as one.
        """
        if self.ferry_spans:
            return len(self.ferry_spans)
        return 1 if self.has_ferry else 0


# ── The ferry, or the land route ─────────────────────────────────────────────

#: Per-call override values for `choose_ferry_or_land`.
FERRY_PREFERENCES = ("auto", "avoid", "prefer")

#: Defaults, mirrored by `routing.ferry` in config.yaml.
DEFAULT_WAIT_MINUTES_PER_CROSSING = 45.0
DEFAULT_PREFER_LAND_WITHIN_MINUTES = 15.0

#: The config file `configured_ferry_policy` reads when not given one: the
#: repository's own, so the answer does not depend on the working directory.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@dataclass(frozen=True)
class FerryPolicy:
    """How a leg with a ferry chooses between the ferry and the land route."""

    #: Waiting and boarding per crossing, on top of the routed sailing time.
    wait_minutes_per_crossing: float = DEFAULT_WAIT_MINUTES_PER_CROSSING
    #: Land wins unless the ferry route, allowance included, is faster by
    #: more than this many minutes.
    prefer_land_within_minutes: float = DEFAULT_PREFER_LAND_WITHIN_MINUTES


def ferry_policy_from_config(config: Any) -> FerryPolicy:
    """A `FerryPolicy` from a parsed config.yaml mapping (`routing.ferry`).

    A missing section or key is the default. A value that is not a
    non-negative number is logged and replaced by the default, so a typo in
    the config changes nothing rather than failing the build.
    """
    routing_section = config.get("routing") if isinstance(config, dict) else None
    section = routing_section.get("ferry") if isinstance(routing_section, dict) else None
    if not isinstance(section, dict):
        section = {}

    def _number(name: str, default: float) -> float:
        raw = section.get(name)
        if raw is None:
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = -1.0
        if value < 0 or value != value:
            logger.warning("routing.ferry.%s=%r is not a non-negative number; using %s.",
                           name, raw, default)
            return default
        return value

    return FerryPolicy(
        wait_minutes_per_crossing=_number("wait_minutes_per_crossing",
                                          DEFAULT_WAIT_MINUTES_PER_CROSSING),
        prefer_land_within_minutes=_number("prefer_land_within_minutes",
                                           DEFAULT_PREFER_LAND_WITHIN_MINUTES),
    )


_policy_cache: dict[str, FerryPolicy] = {}


def configured_ferry_policy(config_path: str | os.PathLike[str] | None = None) -> FerryPolicy:
    """The `FerryPolicy` config.yaml sets, read once per path. The defaults when
    the file is missing or unreadable."""
    path = str(DEFAULT_CONFIG_PATH if config_path is None else config_path)
    with _lock:
        cached = _policy_cache.get(path)
    if cached is not None:
        return cached
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    except Exception:  # noqa: BLE001 -- a config problem must not stop routing
        config = {}
    policy = ferry_policy_from_config(config)
    with _lock:
        _policy_cache[path] = policy
    return policy


@dataclass(frozen=True)
class FerryChoiceReason:
    """The numbers a ferry-or-land choice was made on.

    `rule` is what decided: `auto` (the timing rule), `avoid` or `prefer` (a
    per-call override), or `no_land_route` (there was nothing to choose).
    """

    rule: str
    crossings: int
    wait_minutes_per_crossing: float
    prefer_land_within_minutes: float
    #: The ferry route's routed minutes, sailing included, waiting not.
    ferry_minutes: float
    #: `ferry_minutes` plus the allowance for every crossing.
    ferry_minutes_with_wait: float
    #: None when no land route exists.
    land_minutes: float | None
    #: `land_minutes - ferry_minutes_with_wait`: how much faster the ferry is,
    #: allowance included. Negative when land is faster. None with no land.
    difference_minutes: float | None

    @property
    def summary(self) -> str:
        wait = (f"{self.ferry_minutes:.0f} min by ferry + {self.wait_minutes_per_crossing:.0f} min "
                f"wait x {self.crossings} crossing{'s' if self.crossings != 1 else ''} "
                f"= {self.ferry_minutes_with_wait:.0f} min")
        if self.land_minutes is None:
            return f"{wait}; no land route was found, so the ferry"
        compare = f"{wait}, against {self.land_minutes:.0f} min by land"
        if self.rule == "avoid":
            return f"{compare}; ferries avoided by request, so land"
        if self.rule == "prefer":
            return f"{compare}; the ferry preferred by request"
        if self.difference_minutes is not None and self.difference_minutes > self.prefer_land_within_minutes:
            return (f"{compare}; the ferry is {self.difference_minutes:.0f} min faster, more than "
                    f"{self.prefer_land_within_minutes:.0f}, so the ferry")
        return (f"{compare}; the ferry is not more than {self.prefer_land_within_minutes:.0f} min "
                f"faster, so land")


def choose_ferry_or_land(
    ferry: RoutedLeg,
    land: RoutedLeg | None,
    *,
    policy: FerryPolicy | None = None,
    preference: str = "auto",
) -> tuple[str, FerryChoiceReason]:
    """`("ferry" | "land", reason)` for a leg whose route crosses water.

    See the module docstring for the rule. Raises ValueError for a
    `preference` not in `FERRY_PREFERENCES`: a caller's typo is a bug, not a
    choice to be guessed at.
    """
    if preference not in FERRY_PREFERENCES:
        raise ValueError(f"ferry_preference must be one of {FERRY_PREFERENCES}, not {preference!r}")
    policy = policy or FerryPolicy()
    crossings = ferry.crossings
    with_wait = round(float(ferry.minutes) + policy.wait_minutes_per_crossing * crossings, 1)
    land_minutes = None if land is None else float(land.minutes)
    difference = None if land_minutes is None else round(land_minutes - with_wait, 1)

    if land is None:
        rule, chosen = "no_land_route", "ferry"
    elif preference == "avoid":
        rule, chosen = "avoid", "land"
    elif preference == "prefer":
        rule, chosen = "prefer", "ferry"
    else:
        rule = "auto"
        chosen = "ferry" if difference > policy.prefer_land_within_minutes else "land"
    return chosen, FerryChoiceReason(
        rule=rule, crossings=crossings,
        wait_minutes_per_crossing=policy.wait_minutes_per_crossing,
        prefer_land_within_minutes=policy.prefer_land_within_minutes,
        ferry_minutes=float(ferry.minutes), ferry_minutes_with_wait=with_wait,
        land_minutes=land_minutes, difference_minutes=difference,
    )


def api_key() -> str:
    return str(os.environ.get(API_KEY_ENV) or "").strip()


def _cache_key(origin: tuple[float, float], dest: tuple[float, float],
               *, avoid_ferries: bool = False,
               profile: str = DEFAULT_PROFILE) -> str:
    # Four decimal places is about eleven metres: the same town centre geocoded
    # twice lands in one key, and two different towns never share one.
    key = f"{origin[0]:.4f},{origin[1]:.4f}>{dest[0]:.4f},{dest[1]:.4f}"
    # The ferry-avoiding answer is a different question about the same pair,
    # so it has its own entry; the ordinary key is unchanged.
    if avoid_ferries:
        key += " avoid=ferries"
    # Likewise a different profile: a bike does not take the road a car does,
    # and the two answers must not share a key. The driving key is unchanged,
    # so every cache written before profiles existed still hits.
    if profile != DEFAULT_PROFILE:
        key += f" profile={profile}"
    return key


def _error_code(exc: urllib.error.HTTPError) -> int | None:
    """OpenRouteService's own error code in a refusal's body, or None.

    The body is JSON (`{"error": {"code": 2010, "message": ...}}`), and it is
    the only place the router says *which* refusal this is: HTTP 404 is both
    *these two points do not connect* and *I could not find a road near one of
    them*, and only the second is worth asking again. Never raises: a refusal
    with no readable body is simply a refusal.
    """
    try:
        body = exc.read()
    except Exception:  # noqa: BLE001 -- an unreadable body is not an error here
        return None
    try:
        code = json.loads(body or b"{}").get("error", {}).get("code")
        return int(code)
    except (AttributeError, TypeError, ValueError):
        return None


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


def _remember_no_route(path: str | os.PathLike[str], cache_key: str) -> None:
    with _lock:
        cache = _load_cache(path)
        cache[cache_key] = {"no_route": True}
        _save_cache(path, cache)


def route_leg(
    origin: tuple[float, float],
    dest: tuple[float, float],
    *,
    key: str | None = None,
    cache_path: str | os.PathLike[str] | None = None,
    avoid_ferries: bool = False,
    profile: str = DEFAULT_PROFILE,
) -> RoutedLeg | None:
    """Route one leg between two `(lat, lng)` points, or None.

    None whenever there is no key, or the router did not give a usable answer;
    see the module docstring. Cached by rounded coordinates at `cache_path`
    (default `DEFAULT_CACHE_PATH`), successful answers only.

    `avoid_ferries` asks for the land route instead, under its own cache key.
    None then also means no land route exists: a 404, or a reply that crosses
    by ferry anyway. That answer is cached as `{"no_route": true}` so an
    island is not asked again every run; a refusal or timeout is not.

    `profile` is one of `PROFILES`, and `driving-car` unless a caller names
    another -- a bike leg routed as a bike leg takes the trail the car cannot,
    and a car's road is not the answer to *how long is the ride*. It has its
    own cache key, so the two answers about one pair never overwrite each
    other. A profile not in `PROFILES` raises ValueError: a caller's typo is a
    bug, not a URL to be built and refused.

    **A coordinate need not be on a road.** A geocoder answers with the place,
    and a creek, a trailhead or a lake is a point in water or in a field. Both
    coordinates are sent with a `radiuses` allowance (`SNAP_RADIUS_M`) so the
    router may start from the nearest road; where it still says it found none
    (`NO_ROUTABLE_POINT`), the leg is asked once more at `WIDE_SNAP_RADIUS_M`,
    which is where a rural point's nearest road actually is. Only then is it
    a straight-line fallback. The snapped answer is cached under the ordinary
    key, so the second ask happens once per pair and never again.
    """
    if profile not in PROFILES:
        raise ValueError(f"profile must be one of {PROFILES}, not {profile!r}")
    key = api_key() if key is None else str(key).strip()
    if not key:
        return None
    path = DEFAULT_CACHE_PATH if cache_path is None else cache_path
    cache_key = _cache_key(origin, dest, avoid_ferries=avoid_ferries, profile=profile)
    with _lock:
        cached = _load_cache(path).get(cache_key)
    if isinstance(cached, dict):
        if avoid_ferries and cached.get("no_route") is True:
            return None
        try:
            return _leg_from_cache(cached)
        except (KeyError, TypeError, ValueError):
            pass

    request_body: dict[str, Any] = {
        # OpenRouteService takes [lng, lat].
        "coordinates": [[origin[1], origin[0]], [dest[1], dest[0]]],
        "units": "mi",
        "instructions": False,
        "extra_info": ["waytype"],
        # One allowance per coordinate, in metres, in the same order.
        "radiuses": [SNAP_RADIUS_M, SNAP_RADIUS_M],
    }
    if avoid_ferries:
        request_body["options"] = {"avoid_features": ["ferries"]}
    endpoint = f"{ENDPOINT_BASE}/{profile}"

    def _ask(body_dict: dict[str, Any]) -> Any:
        request = urllib.request.Request(
            endpoint, data=json.dumps(body_dict).encode("utf-8"), headers={
                "Authorization": key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            })
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return json.load(response)

    try:
        try:
            payload = _ask(request_body)
        except urllib.error.HTTPError as exc:
            # The one refusal that means *look further out*, and it is asked
            # again exactly once. Every other refusal falls through unchanged.
            if exc.code != 404 or _error_code(exc) != NO_ROUTABLE_POINT:
                raise
            logger.info("No road within %sm of an end of %s; asking again at %sm.",
                        SNAP_RADIUS_M, cache_key, WIDE_SNAP_RADIUS_M)
            wider = dict(request_body)
            wider["radiuses"] = [WIDE_SNAP_RADIUS_M, WIDE_SNAP_RADIUS_M]
            payload = _ask(wider)
    except urllib.error.HTTPError as exc:
        if avoid_ferries and exc.code == 404:
            logger.info("No land route for %s; the ferry is the only way.", cache_key)
            _remember_no_route(path, cache_key)
            return None
        logger.info("Routing refused %s (HTTP %s); estimating instead.", cache_key, exc.code)
        return None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.info("Routing unavailable for %s (%s); estimating instead.", cache_key, exc)
        return None

    routed = parse_route(payload)
    if routed is None:
        logger.info("Routing reply for %s did not parse; estimating instead.", cache_key)
        return None
    if avoid_ferries and (routed.has_ferry or routed.ferry_spans):
        # Asked to avoid ferries and crossed by one anyway: not a land route.
        logger.info("Land route for %s still takes a ferry; treating as none.", cache_key)
        _remember_no_route(path, cache_key)
        return None
    with _lock:
        cache = _load_cache(path)
        cache[cache_key] = _leg_to_cache(routed)
        _save_cache(path, cache)
    return routed
