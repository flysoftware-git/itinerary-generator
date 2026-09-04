"""transit_estimate.py — public-transport duration estimates via Google Routes.

Why this exists
---------------
`getting_here` described every leg as a drive, because the content prompt asked
for one unconditionally. On an all-rail itinerary that produced "Take the E19
from Antwerp", 95 mi and 2 hrs 15 min for what is actually an 8-mile airport
train. Suppressing the invented figures was the first fix; this supplies real
ones.

Estimates, deliberately
-----------------------
Transit duration varies by departure time, day of week and timetable season, so
a single number is an estimate and is labelled as one. The request pins a
departure time so the answer is at least reproducible rather than "now".

Terms note
----------
`docs/design/cost-accounting-and-reduction.md` §6.4 records that Google Maps
Platform results carry caching restrictions, which is why this project does not
build on them generally. Using a duration in a published static page is exactly
the case that section flagged. That trade-off was accepted by the product owner
on 2026-08-27 for transit estimates specifically.

**Scope widened 2026-08-30 (GH #2).** Callers now price a leg the manifest
DECLARES as transit, not only one with a booking attached. Same data, same
handling, no new terms question -- but it is more calls: one per declared
transit leg, on trips that previously made none. Two gates still stand in
front of it, and both are the manifest's: a leg qualifies only if it says
`transport_mode: transit` (or a `legs:` entry does), and the whole feature
is off without `transit_routing.enabled`. A road-trip manifest is unaffected.

Two consequences follow, and both are deliberate:

  * **Nothing is cached to disk.** A run makes one call per leg -- five for the
    Europe manifest -- and keeps the answer only in memory. Persisting Google's
    figures is the part the restriction is squarely about, and the spend saved
    would be fractions of a cent.
  * **Only duration and distance are taken.** No fares, no line names, no
    stop-by-stop itinerary, so a reader gets an estimate rather than a
    reproduction of Google's routing output.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

ROUTES_ENDPOINT = "https://routes.googleapis.com/directions/v2:computeRoutes"

#: Manifest leg types that should be priced as public transport.
TRANSIT_MODES = frozenset({"train", "bus", "shuttle", "ferry", "ship"})

#: Routes travelMode values this module will send. Anything else is a caller
#: bug rather than a runtime condition, so it raises rather than defaulting --
#: silently falling back to TRANSIT would price a bike ride as a bus, and
#: nothing in the output would say so.
VALID_TRAVEL_MODES = frozenset({"TRANSIT", "BICYCLE", "WALK"})

_FIELD_MASK = "routes.duration,routes.distanceMeters"

_TIMEOUT_SECONDS = 20


class TransitEstimator:
    """One instance per run. Holds no disk state."""

    def __init__(self, api_key: str | None = None, *, timeout: int = _TIMEOUT_SECONDS) -> None:
        self._key = str(api_key or os.environ.get("GOOGLE_MAPS_PLATFORM_KEY", "") or "").strip()
        self._timeout = timeout
        self._memo: dict[tuple[str, str, str], dict[str, Any] | None] = {}
        self.call_count = 0

    @property
    def available(self) -> bool:
        return bool(self._key)

    def estimate(
        self,
        origin: str,
        destination: str,
        *,
        departure_iso: str = "",
        travel_mode: str = "TRANSIT",
    ) -> dict[str, Any] | None:
        """Return {'minutes', 'miles', 'estimated': True} or None.

        None on any failure -- no key, quota, network, unroutable pair. The
        caller must treat that as "no figure available" and omit, never as
        zero, and never by falling back to a driving estimate: substituting a
        drive for a train is the defect this module exists to remove.

        `travel_mode` is a Routes travelMode: TRANSIT, BICYCLE or WALK. The
        last two arrived with the `bike`/`hike` leg modes, and they are a
        better bet than TRANSIT ever was -- a cycling duration is a fact about
        roads and gradients rather than about whose timetable Google licenses,
        so its coverage does not evaporate outside Europe and North America
        the way the Japan probe found TRANSIT's did.
        """
        origin = str(origin or "").strip()
        destination = str(destination or "").strip()
        if not (self.available and origin and destination):
            return None

        travel_mode = str(travel_mode or "TRANSIT").strip().upper()
        if travel_mode not in VALID_TRAVEL_MODES:
            raise ValueError(
                f"travel_mode {travel_mode!r} is not one of {sorted(VALID_TRAVEL_MODES)}"
            )

        # Keyed by mode too: the same pair of endpoints has a different answer
        # by bike than by train, and a memo that conflated them would hand one
        # leg the other's duration.
        memo_key = (origin.lower(), destination.lower(), departure_iso, travel_mode)
        if memo_key in self._memo:
            return self._memo[memo_key]

        body: dict[str, Any] = {
            "origin": {"address": origin},
            "destination": {"address": destination},
            "travelMode": travel_mode,
        }
        # A pinned departure time is what makes a TRANSIT answer reproducible
        # rather than "now". A bike ride does not vary by timetable, so sending
        # one would add nothing and invite a rejection on a past date.
        if departure_iso and travel_mode == "TRANSIT":
            body["departureTime"] = departure_iso

        result: dict[str, Any] | None = None
        try:
            request = urllib.request.Request(
                ROUTES_ENDPOINT,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": self._key,
                    "X-Goog-FieldMask": _FIELD_MASK,
                },
            )
            self.call_count += 1
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.load(response)
            routes = payload.get("routes") or []
            if routes:
                result = self._parse_route(routes[0])
        except urllib.error.HTTPError as exc:
            logger.warning(
                "Transit estimate failed for %s -> %s: HTTP %s", origin, destination, exc.code
            )
        except Exception as exc:  # network, JSON, anything else
            logger.warning(
                "Transit estimate failed for %s -> %s: %s", origin, destination, exc
            )

        self._memo[memo_key] = result
        return result

    @staticmethod
    def _parse_route(route: dict[str, Any]) -> dict[str, Any] | None:
        raw_duration = str(route.get("duration", "") or "").strip()
        if not raw_duration.endswith("s"):
            return None
        try:
            seconds = float(raw_duration[:-1])
        except ValueError:
            return None
        if seconds <= 0:
            return None

        metres = route.get("distanceMeters")
        miles = None
        try:
            if metres is not None:
                miles = round(float(metres) / 1609.344)
        except (TypeError, ValueError):
            miles = None

        return {
            "minutes": int(round(seconds / 60.0)),
            "miles": miles,
            # Carried through to the renderer so the figure can be presented as
            # an approximation. A transit time that varies by an hour across the
            # day should not be shown as though it were exact.
            "estimated": True,
        }


def format_duration(minutes: int | None) -> str:
    """"2 hrs 16 min", matching the existing travel_time strings."""
    if not minutes or minutes <= 0:
        return ""
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f"{hours} hr{'s' if hours != 1 else ''} {mins} min"
    if hours:
        return f"{hours} hr{'s' if hours != 1 else ''}"
    return f"{mins} min"
