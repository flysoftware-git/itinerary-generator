"""Tests for `generator/road_estimate.py`.

The point of the module is removing a *systematic* lean, so the tests assert the
shape of the model rather than exact minute counts: speed rises with distance,
the bands are continuous, and both historical call sites now agree.
"""

from __future__ import annotations

import pytest

from generator.ai_content import _estimate_haversine_route
from generator.road_estimate import (
    ROAD_DISTANCE_FACTOR,
    SPEED_BANDS_MPH,
    drive_minutes,
    format_drive_time,
    road_distance_miles,
    road_speed_mph,
)
from generator.url_discovery import URLDiscoverer


def test_speed_rises_with_distance():
    """The finding the module exists for: one constant cannot serve both."""
    speeds = [road_speed_mph(d) for d in (5, 30, 100, 300)]
    assert speeds == sorted(speeds), speeds
    assert speeds[0] < speeds[-1]


def test_short_legs_are_slow_and_long_legs_are_fast():
    assert road_speed_mph(5) == 35.0        # park access
    assert road_speed_mph(300) == 60.0      # interstate


def test_bands_are_ordered_and_open_ended():
    uppers = [u for u, _ in SPEED_BANDS_MPH]
    assert uppers == sorted(uppers)
    assert uppers[-1] == float("inf"), "the last band must catch everything"


@pytest.mark.parametrize("miles", [0, 0.1, 19.9, 20.0, 74.9, 75.0, 199.9, 200.0, 10_000])
def test_every_distance_gets_a_speed(miles):
    assert road_speed_mph(miles) > 0


def test_band_edges_do_not_gap_or_overlap():
    """A value exactly on a boundary belongs to the *upper* band."""
    assert road_speed_mph(19.999) == 35.0
    assert road_speed_mph(20.0) == 48.0


def test_bad_input_falls_back_rather_than_raising():
    assert road_speed_mph(None) > 0
    assert road_speed_mph("not a number") > 0


def test_explicit_speed_overrides_the_band():
    """A caller with better information keeps control."""
    banded = drive_minutes(100)
    forced = drive_minutes(100, avg_speed_mph=50.0)
    assert forced == pytest.approx(120.0)
    assert forced != banded


def test_road_distance_applies_the_factor():
    assert road_distance_miles(100) == pytest.approx(100 * ROAD_DISTANCE_FACTOR)


@pytest.mark.parametrize(
    "minutes,expected",
    [(0, "0 min"), (45, "45 min"), (60, "1 hr"), (135, "2 hr 15 min"), (59.6, "1 hr")],
)
def test_format_drive_time(minutes, expected):
    assert format_drive_time(minutes) == expected


def test_format_drive_time_never_emits_60_minutes():
    """The old inline formatters each carried their own 60-minute carry fix."""
    for m in range(0, 600):
        assert " 60 min" not in format_drive_time(m)


# ------------------------------------------------ the two historical callers

def _minutes(time_str: str) -> int:
    h = m = 0
    parts = time_str.split()
    if "hr" in parts:
        h = int(parts[parts.index("hr") - 1])
    if "min" in parts:
        m = int(parts[parts.index("min") - 1])
    return h * 60 + m


MOAB = (38.5733, -109.5498)
MONUMENT_VALLEY = (36.9980, -110.0985)


def test_both_estimators_agree():
    """They used to duplicate the formula by hand; now they share a module."""
    mine = _estimate_haversine_route(MOAB[0], MOAB[1], MONUMENT_VALLEY[0], MONUMENT_VALLEY[1])
    theirs = URLDiscoverer._estimate_route_from_haversine(
        URLDiscoverer.__new__(URLDiscoverer),
        MOAB[0], MOAB[1], MONUMENT_VALLEY[0], MONUMENT_VALLEY[1],
    )
    assert mine == theirs


def test_a_long_leg_is_no_longer_wildly_fast():
    """Moab to Monument Valley is ~148 road miles and takes about 3 hours.

    Under the old flat 60 mph it came out at 2 hr 27 min against a measured
    176 min -- the systematic lean this change removes.
    """
    miles, time_str = _estimate_haversine_route(
        MOAB[0], MOAB[1], MONUMENT_VALLEY[0], MONUMENT_VALLEY[1]
    )
    assert 130 <= miles <= 165
    assert 140 <= _minutes(time_str) <= 200


def test_a_short_park_leg_is_not_estimated_at_highway_speed():
    """Moab to Arches: ~5 road miles, ~11 minutes measured, not ~6."""
    arches = (38.6168, -109.6197)
    miles, time_str = _estimate_haversine_route(MOAB[0], MOAB[1], arches[0], arches[1])
    assert _minutes(time_str) >= miles, "a short leg must not be timed at 60 mph"


def test_degenerate_and_invalid_input_still_returns_none():
    assert _estimate_haversine_route(38.5, -109.5, 38.5, -109.5) == (None, None)
    assert _estimate_haversine_route("x", None, 1, 2) == (None, None)
    assert _estimate_haversine_route(999, 0, 0, 0) == (None, None)
