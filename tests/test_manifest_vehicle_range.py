"""A manifest can state how far its vehicle goes on a tank.

The en-route stop already fires on whichever limit a leg reaches first --
continuous driving time or a tank -- but the only place to say what the tank
was `en_route_stops.vehicle_range_miles` in config.yaml, which is shared by
every trip run from that checkout and ships commented out, correctly: a number
invented there would put a fuel stop on a leg that never needed one.

The person who knows the range is the person writing the manifest. So
`trip.vehicle_range_miles` states it for that trip, and the order is:

  1. the manifest, when it states one;
  2. otherwise config.yaml;
  3. otherwise None -- no fuel guidance, exactly as before the key existed.
"""

from __future__ import annotations

import pytest
import yaml

from generator.ai_content import AIContentGenerator as Gen
from generator.manifest_parser import ManifestParser


# ------------------------------------------------------------------- the parser


def _manifest(tmp_path, **trip_extra):
    path = tmp_path / "trip_manifest.yaml"
    path.write_text(yaml.safe_dump({
        "trip": {"title": "T", "subtitle": "S", "theme_color": "#123456", **trip_extra},
        "destinations": [{"id": "a", "name": "Somewhere", "dates": "2 nights",
                          "planning_links": [{"label": "Map", "url": "https://example.com"}]}],
    }), encoding="utf-8")
    return path


@pytest.mark.parametrize("stated", [350, 212.5])
def test_the_parser_accepts_a_positive_range(tmp_path, stated):
    trip = ManifestParser().parse(_manifest(tmp_path, vehicle_range_miles=stated))
    assert trip["trip"]["vehicle_range_miles"] == stated


def test_a_manifest_without_the_key_still_parses(tmp_path):
    """Additive: every existing manifest is unaffected."""
    trip = ManifestParser().parse(_manifest(tmp_path))
    assert "vehicle_range_miles" not in trip["trip"]


@pytest.mark.parametrize("bad", [0, -50, "350 miles", "far", True])
def test_the_parser_refuses_a_range_that_is_not_a_positive_number(tmp_path, bad):
    """A tank of no miles is a mistake, not a vehicle, and a string or a
    boolean is not a distance. Refused with the parser's own message, naming
    the key, rather than quietly read as 'unknown'."""
    with pytest.raises(ValueError, match=r"trip\.vehicle_range_miles"):
        ManifestParser().parse(_manifest(tmp_path, vehicle_range_miles=bad))


# ------------------------------------------------------------------ precedence


class _Gen(Gen):
    """The generator with only the two settings the en-route stop reads."""

    def __init__(self, threshold: int, config_range: float | None) -> None:  # noqa: D107
        self._lunch_stop_min_drive_minutes = threshold
        self._vehicle_range_miles = config_range


def test_the_manifest_range_wins_over_config() -> None:
    trip = {"trip": {"vehicle_range_miles": 250}}
    assert _Gen(180, 400)._resolve_vehicle_range_miles(trip) == 250.0


def test_a_manifest_that_says_nothing_falls_back_to_config() -> None:
    assert _Gen(180, 400)._resolve_vehicle_range_miles({"trip": {}}) == 400
    assert _Gen(180, 400)._resolve_vehicle_range_miles({}) == 400


def test_with_neither_stated_there_is_no_range() -> None:
    """The default, and the one that matters most: nothing is invented."""
    assert _Gen(180, None)._resolve_vehicle_range_miles({"trip": {}}) is None


@pytest.mark.parametrize("unstated", [0, None, "", "far", False])
def test_a_manifest_value_that_is_not_a_range_does_not_override_config(unstated) -> None:
    """For a trip dict that did not come through the parser: a zero or a
    non-number is not a statement, so config still applies."""
    trip = {"trip": {"vehicle_range_miles": unstated}}
    assert _Gen(180, 400)._resolve_vehicle_range_miles(trip) == 400


# -------------------------------------------------------- what the guide says


def _trip(trip_meta: dict, travel_time: str, miles: float) -> dict:
    return {
        "trip": trip_meta,
        "destinations": [{
            "name": "Chattanooga",
            "ai_content": {
                "getting_here": {
                    "travel_time": travel_time,
                    "distance_miles": miles,
                    "en_route_stops": [{"name": "Oak Ridge", "route_progress_ratio": 0.5}],
                },
                "possible_daily_schedule": [
                    {"periods": [{"summary": "Set out after breakfast."}]}
                ],
            },
        }],
    }


def _summary(trip: dict) -> str:
    return trip["destinations"][0]["ai_content"]["possible_daily_schedule"][0]["periods"][0]["summary"]


def test_a_range_stated_only_in_the_manifest_reaches_the_arrival_note() -> None:
    """Four and a half hours of interstate, 360 miles: inside a five-hour
    driving limit, past a 350-mile tank. With config silent, only the
    manifest can make this leg say fuel."""
    trip = _trip({"vehicle_range_miles": 350}, "4 hr 30 min", 360)
    _Gen(300, None)._inject_lunch_stop_suggestions(trip)
    assert "Stop for fuel around Oak Ridge" in _summary(trip)


def test_the_manifest_range_replaces_a_config_range_that_would_have_fired() -> None:
    """Config says a short tank; this trip's vehicle goes further. The leg is
    within the manifest's range and inside the time limit, so it earns no stop."""
    trip = _trip({"vehicle_range_miles": 500}, "4 hr 30 min", 360)
    _Gen(300, 200)._inject_lunch_stop_suggestions(trip)
    assert _summary(trip) == "Set out after breakfast."


def test_a_trip_with_no_range_anywhere_is_told_nothing_about_fuel() -> None:
    trip = _trip({}, "4 hr 30 min", 360)
    _Gen(300, None)._inject_lunch_stop_suggestions(trip)
    assert _summary(trip) == "Set out after breakfast."
