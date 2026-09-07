"""The en-route stop fires on whichever limit is reached first.

Owner, 2026-09-06: *"en-route lunch / refuel stop should be the shortest of
continuous time driving parameter and vehicle range"*.

That reframes the feature rather than adding one. The stop already exists and
already fires on continuous driving time; a tank is a **second bound on the same
stop**, and the traveler needs it at whichever comes first.

**The two bounds are in different units, so the range is converted at the leg's
own speed.** 350 miles is under five hours of interstate and most of a day on a
mountain road. A fixed conversion would put the fuel stop too early on one and
too late on the other, and too late is the one that strands somebody.

**Unset by default.** Not every trip is driven and not every car's range is
known, so a number invented here would put a fuel stop on a leg that never
needed one. `None` rather than `0`: a range of no miles fires on every leg.

**Which limit fired decides the sentence.** *You will want lunch* and *you will
need fuel before here* are different facts, and the leg that outruns the tank is
where saying the wrong one has a consequence.
"""

from __future__ import annotations

from generator.ai_content import (
    DEFAULT_LUNCH_STOP_MIN_DRIVE_MINUTES,
    AIContentGenerator as Gen,
)


def _leg(travel_time: str, miles: float, stops: list | None = None) -> dict:
    return {
        "travel_time": travel_time,
        "distance_miles": miles,
        "en_route_stops": stops if stops is not None else [
            {"name": "Oak Ridge", "route_progress_ratio": 0.5},
        ],
    }


# ------------------------------------------------------------- which bound wins


def test_with_no_range_stated_the_time_limit_decides() -> None:
    """The behaviour every existing trip has, unchanged."""
    assert Gen._stop_threshold_minutes(_leg("5 hr", 300), 180, None) == (180, "time")


def test_a_range_of_zero_is_not_a_range() -> None:
    """`0` would be a tank of no miles, which fires on every leg. Absent and
    zero must not mean the same thing."""
    assert Gen._stop_threshold_minutes(_leg("5 hr", 300), 180, 0) == (180, "time")


def test_on_a_fast_road_the_tank_runs_out_first() -> None:
    """400 miles in five hours is 80 mph, so 350 miles is 4 hr 22 min -- sooner
    than a five-hour driving limit."""
    assert Gen._stop_threshold_minutes(_leg("5 hr", 400), 300, 350) == (262, "range")


def test_on_a_slow_road_the_clock_runs_out_first() -> None:
    """The same tank, the same distance, half the speed: 350 miles is now seven
    hours and the three-hour driving limit bites long before it. This is the
    pair that a fixed miles-to-minutes conversion would get wrong."""
    assert Gen._stop_threshold_minutes(_leg("8 hr", 400), 180, 350) == (180, "time")


def test_a_leg_with_no_distance_falls_back_to_time() -> None:
    """An unmeasured leg is not evidence of a short one, and there is nothing to
    convert the range against."""
    assert Gen._stop_threshold_minutes(_leg("5 hr", 0), 180, 350) == (180, "time")
    assert Gen._stop_threshold_minutes(_leg("", 300), 180, 350) == (180, "time")


def test_a_range_that_is_not_a_number_falls_back_to_time() -> None:
    assert Gen._stop_threshold_minutes(_leg("5 hr", 400), 180, "half a tank") == (180, "time")


def test_a_tie_reads_as_time() -> None:
    """Exactly equal bounds are the time limit's, because that is the one every
    trip has and the one the traveler set deliberately."""
    assert Gen._stop_threshold_minutes(_leg("5 hr", 300), 300, 300)[1] == "time"


# --------------------------------------------------------------- what it picks


def test_a_leg_inside_both_limits_earns_no_stop() -> None:
    assert Gen._pick_lunch_stop(_leg("2 hr", 120), 180, 350) is None


def test_a_leg_past_the_time_limit_earns_one() -> None:
    assert Gen._pick_lunch_stop(_leg("4 hr", 200), 180, None)["name"] == "Oak Ridge"


def test_a_leg_past_the_tank_earns_one_the_time_limit_would_have_missed() -> None:
    """The whole point of the second bound. Four and a half hours of interstate
    is inside a five-hour driving limit and past a 350-mile tank."""
    assert Gen._pick_lunch_stop(_leg("4 hr 30 min", 360), 300, None) is None
    assert Gen._pick_lunch_stop(_leg("4 hr 30 min", 360), 300, 350)["name"] == "Oak Ridge"


def test_it_still_never_invents_a_place() -> None:
    """The rule that made the original safe: it only ever returns a stop that
    already survived en-route discovery and verification. A range cannot
    conjure a candidate where there is none."""
    assert Gen._pick_lunch_stop(_leg("9 hr", 600, stops=[]), 180, 350) is None


# --------------------------------------------------------------- what it says


class _Gen(Gen):
    """The generator with only the two settings this behaviour reads."""

    def __init__(self, threshold: int, range_miles: float | None) -> None:  # noqa: D107
        self._lunch_stop_min_drive_minutes = threshold
        self._vehicle_range_miles = range_miles


def _trip(travel_time: str, miles: float) -> dict:
    return {"destinations": [{
        "name": "Chattanooga",
        "ai_content": {
            "getting_here": _leg(travel_time, miles),
            "possible_daily_schedule": [
                {"periods": [{"summary": "Set out after breakfast."}]}
            ],
        },
    }]}


def _summary(trip: dict) -> str:
    periods = trip["destinations"][0]["ai_content"]["possible_daily_schedule"][0]["periods"]
    return periods[0]["summary"]


def test_a_long_slow_leg_is_told_about_lunch() -> None:
    trip = _trip("5 hr", 250)
    _Gen(180, 350)._inject_lunch_stop_suggestions(trip)
    assert "Break for lunch around Oak Ridge" in _summary(trip)
    assert "fuel" not in _summary(trip).lower()


def test_a_leg_longer_than_a_tank_is_told_about_fuel() -> None:
    """And still about eating -- the traveler stopping for fuel is also the
    traveler who has been driving for four hours."""
    trip = _trip("4 hr 30 min", 360)
    _Gen(300, 350)._inject_lunch_stop_suggestions(trip)
    said = _summary(trip)
    assert "Stop for fuel around Oak Ridge" in said
    assert "longer than a tank" in said
    assert "Somewhere to eat" in said


def test_the_stop_carries_which_limit_fired() -> None:
    """Marked on the stop as well as in the prose, the way `is_lunch_stop`
    already is: a renderer should not have to parse a sentence to find out."""
    trip = _trip("4 hr 30 min", 360)
    _Gen(300, 350)._inject_lunch_stop_suggestions(trip)
    stop = trip["destinations"][0]["ai_content"]["getting_here"]["en_route_stops"][0]
    assert stop["stop_reason"] == "range"
    assert stop["is_lunch_stop"] is True


def test_a_trip_with_no_range_set_reads_exactly_as_before() -> None:
    """The control, and the one that matters most: every existing manifest must
    produce the sentence it produced yesterday."""
    trip = _trip("5 hr", 250)
    _Gen(DEFAULT_LUNCH_STOP_MIN_DRIVE_MINUTES, None)._inject_lunch_stop_suggestions(trip)
    assert "Break for lunch around Oak Ridge, roughly the midpoint." in _summary(trip)

# ------------------------------------------------ where the stop actually goes


def _stops(*pairs):
    return [{"name": n, "route_progress_ratio": r} for n, r in pairs]


def test_the_fuel_stop_is_within_the_tank_that_has_to_reach_it() -> None:
    """The bound decided WHETHER to suggest a stop and nothing decided WHERE.

    A 900-mile leg with a 200-mile tank recommended the town nearest the
    midpoint -- mile 450 -- and passed over one at mile 180. The stop that
    exists so nobody is stranded was itself 250 miles past the fuel needed to
    reach it. The tank is full at the start of the leg (every day starts with a
    topoff), so reach is measured from mile 0.
    """
    leg = _leg("12 hr", 900, _stops(("Fuelville", 0.20), ("Midtown", 0.50)))
    assert Gen._pick_lunch_stop(leg, 180, 200)["name"] == "Fuelville"


def test_the_cap_binds_even_when_the_clock_is_the_shorter_bound() -> None:
    """A leg can earn its stop on time and still be longer than a tank. The
    reason that fired says which sentence to write; it does not decide whether
    the driver can get there."""
    leg = _leg("12 hr", 800, _stops(("Town A", 0.40), ("Town B", 0.50), ("Town C", 0.90)))
    assert Gen._stop_threshold_minutes(leg, 180, 350)[1] == "time"
    assert Gen._pick_lunch_stop(leg, 180, 350)["name"] == "Town A"


def test_with_no_range_set_the_midpoint_still_wins() -> None:
    """The cap must not exist when nobody has said what the tank is."""
    leg = _leg("12 hr", 800, _stops(("Town A", 0.40), ("Town B", 0.50), ("Town C", 0.90)))
    assert Gen._pick_lunch_stop(leg, 180, None)["name"] == "Town B"


def test_a_tank_that_covers_the_leg_does_not_move_the_stop() -> None:
    leg = _leg("6 hr", 300, _stops(("Town A", 0.40), ("Town B", 0.50), ("Town C", 0.90)))
    assert Gen._pick_lunch_stop(leg, 180, 500)["name"] == "Town B"


def test_nothing_within_reach_says_nothing() -> None:
    """It never names a place the pipeline has not verified, and a stop the
    traveler cannot reach is not a recommendation. Silence is the same answer
    this function already gives when en-route discovery is off."""
    leg = _leg("12 hr", 900, _stops(("FarTown", 0.80)))
    assert Gen._pick_lunch_stop(leg, 180, 200) is None
