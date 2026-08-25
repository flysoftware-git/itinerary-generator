"""Road distance and drive-time estimation from straight-line coordinates.

The engine has no routing API and should not need one: an estimate that is
honest about being an estimate is fine for a trip guide, and a hard dependency
on a metered routing vendor is not. What it does need is an estimate without a
*systematic* lean, and the flat 60 mph assumption had one.

## What was measured

24 legs spanning 5 to 470 miles, across interstate, mountain, park-access and
around-water geography, checked against a real routing engine:

| model | median error | mean absolute | legs >25% off |
|---|---|---|---|
| flat 60 mph (previous) | **-12.8%** | 21.5% | 8 of 24 |
| distance-banded (this) | **-0.0%** | 17.7% | 6 of 24 |

The distance side needed no change: `straight x 1.30` measured **+4.7% median**,
mean absolute 12.8%, with only 3 of 24 legs off by more than a quarter. The
1.30 factor is sound and is kept.

## Why banding, rather than a lower constant

Effective speed rises with trip length, because short trips are mostly local
roads and long ones are mostly highway. Measured effective speeds ran ~30 mph
over a 5-mile park-access leg and ~65-70 mph over 300-470 mile interstate runs.
No single constant serves both: re-centring at a flat 45 mph fixes the median
for short legs and makes long ones *worse* (median +16.3%, 12 of 24 legs off by
over a quarter -- materially worse than the 60 mph it would replace).

## What this deliberately does not fix

After the speed bias is removed, the residual error is dominated by **distance**,
not time: the legs still worst-estimated are the ones where a straight line is a
bad model of the road at all -- a plateau crossed by a canyon detour, a bay
driven around, a peninsula approached the long way. No speed model addresses
that, and closing it properly needs real routing. This change removes a
consistent lean; it does not claim per-leg accuracy.
"""

from __future__ import annotations

# Real roads are longer than straight lines. Measured +4.7% median against a
# routing engine over 24 legs, which is close enough to leave alone.
ROAD_DISTANCE_FACTOR = 1.30

# (upper bound in estimated road miles, average mph below that bound).
# Ordered, and the final entry is the open-ended top band. Deliberately coarse:
# four round numbers that can be explained in a sentence beat a fitted curve
# over 24 samples, and the residual is distance error rather than speed anyway.
SPEED_BANDS_MPH: tuple[tuple[float, float], ...] = (
    (20.0, 35.0),      # park access, in-town hops: mostly local roads
    (75.0, 48.0),      # regional: a mix of two-lane and highway
    (200.0, 55.0),     # inter-city: mostly highway, some approach
    (float("inf"), 60.0),  # long haul: interstate-dominated
)


def road_speed_mph(distance_miles: float) -> float:
    """Average speed to assume for a road leg of roughly `distance_miles`.

    Keyed on the *estimated road distance*, since that is what a caller has
    before any routing happens.
    """
    try:
        miles = float(distance_miles)
    except (TypeError, ValueError):
        return SPEED_BANDS_MPH[-1][1]
    for upper, mph in SPEED_BANDS_MPH:
        if miles < upper:
            return mph
    return SPEED_BANDS_MPH[-1][1]


def road_distance_miles(straight_miles: float, *, road_factor: float = ROAD_DISTANCE_FACTOR) -> float:
    """Inflate a straight-line distance to an estimated road distance."""
    return float(straight_miles) * float(road_factor)


def drive_minutes(road_miles: float, *, avg_speed_mph: float | None = None) -> float:
    """Estimated drive time in minutes for an already-inflated road distance.

    `avg_speed_mph` overrides the banded model; passing it is how a caller with
    better information (a real routing result, a known slow road) keeps control.
    """
    miles = float(road_miles)
    speed = float(avg_speed_mph) if avg_speed_mph else road_speed_mph(miles)
    return (miles / speed) * 60.0 if speed > 0 else 0.0


def format_drive_time(total_minutes: float) -> str:
    """Render minutes as the engine's usual `2 hr 15 min` / `45 min` string."""
    total = max(0, int(round(float(total_minutes))))
    hrs, mins = divmod(total, 60)
    if hrs and mins:
        return f"{hrs} hr {mins} min"
    return f"{hrs} hr" if hrs else f"{mins} min"
