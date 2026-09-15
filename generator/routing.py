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
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
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

_lock = threading.Lock()


@dataclass(frozen=True)
class RoutedLeg:
    """One routed leg. Miles and minutes are the route's, not an estimate's."""

    miles: float
    minutes: float
    #: Share of the route's distance on a ferry, 0.0 to 1.0.
    ferry_share: float = 0.0

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


def parse_route(payload: Any) -> RoutedLeg | None:
    """A `RoutedLeg` from an OpenRouteService directions reply, or None.

    Expects `units: mi`. `summary.distance` is miles and `summary.duration`
    seconds; the ferry share is the `amount` (a percentage of the route) of
    the `waytype` entry whose value is 9.
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
    return RoutedLeg(miles=round(miles, 1), minutes=round(minutes, 1),
                     ferry_share=round(min(1.0, max(0.0, share)), 4))


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
            return RoutedLeg(float(cached["miles"]), float(cached["minutes"]),
                             float(cached.get("ferry_share") or 0.0))
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
        cache[cache_key] = {"miles": routed.miles, "minutes": routed.minutes,
                            "ferry_share": routed.ferry_share}
        _save_cache(path, cache)
    return routed
