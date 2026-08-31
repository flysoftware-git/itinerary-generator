"""Transit estimates replace fabricated driving figures on booked non-road legs.

The Europe output claimed 95 mi and 2 hrs 15 min for the Brussels airport
train, which is about 10 mi and half an hour. See
docs/design/destination-type-coverage.md and cost-accounting-and-reduction.md
section 6.4 for the terms trade-off accepted here.
"""
import pytest

from generator.transit_estimate import TRANSIT_MODES, TransitEstimator, format_duration


class TestDurationFormatting:
    @pytest.mark.parametrize(
        "minutes, expected",
        [(136, "2 hrs 16 min"), (60, "1 hr"), (29, "29 min"), (125, "2 hrs 5 min"), (61, "1 hr 1 min")],
    )
    def test_matches_the_existing_travel_time_shape(self, minutes, expected):
        assert format_duration(minutes) == expected

    @pytest.mark.parametrize("minutes", [0, None, -5])
    def test_no_duration_renders_empty_not_zero(self, minutes):
        """An unavailable figure must be omitted, never shown as 0 min."""
        assert format_duration(minutes) == ""


class TestRouteParsing:
    def test_parses_a_real_response_shape(self):
        route = {"duration": "8182s", "distanceMeters": 213681}
        out = TransitEstimator._parse_route(route)
        assert out == {"minutes": 136, "miles": 133, "estimated": True}

    @pytest.mark.parametrize(
        "route",
        [{}, {"duration": ""}, {"duration": "abc"}, {"duration": "0s"}, {"duration": "-1s"}],
    )
    def test_unusable_durations_return_none(self, route):
        assert TransitEstimator._parse_route(route) is None

    def test_missing_distance_still_yields_a_duration(self):
        """Duration is the figure that was wrong; distance is a bonus."""
        out = TransitEstimator._parse_route({"duration": "1800s"})
        assert out["minutes"] == 30 and out["miles"] is None


class TestNoKeyIsSafe:
    def test_without_a_key_it_reports_unavailable_and_never_calls(self):
        est = TransitEstimator(api_key="")
        assert est.available is False
        assert est.estimate("A", "B") is None
        assert est.call_count == 0

    @pytest.mark.parametrize("origin, destination", [("", "B"), ("A", ""), ("", "")])
    def test_blank_endpoints_are_refused(self, origin, destination):
        est = TransitEstimator(api_key="fake-key")
        assert est.estimate(origin, destination) is None
        assert est.call_count == 0


def test_transit_modes_exclude_car():
    """A car leg is exactly when the existing drive figures are correct."""
    assert "car" not in TRANSIT_MODES
    assert {"train", "bus", "ferry", "ship", "shuttle"} <= TRANSIT_MODES
