"""Opt-in filtering of attractions a traveler has said they cannot do.

Mirrors `has_high_clearance_vehicle`, which is the accepted shape for this kind
of field: the limit is optional, absence is a no-op, and what gets removed is
logged rather than silently dropped.

One rule matters more than the rest and has its own tests: **these figures come
from the model, not from a trail database.** An attraction reporting no distance
is never filtered, because "not known" and "zero miles" are different things and
only one of them is a measurement.
"""

from __future__ import annotations

import logging

import pytest

from generator.ai_content import AIContentGenerator


def _trip(limits: dict, attractions: list[dict]) -> dict:
    return {
        "trip": {"title": "T", "subtitle": "S", "theme_color": "#123456", **limits},
        "destinations": [{"id": "a", "name": "Somewhere", "top_attractions": attractions}],
    }


def _filter(trip: dict) -> list[dict]:
    AIContentGenerator._filter_attractions_beyond_traveler_limits(AIContentGenerator, trip)
    return trip["destinations"][0]["top_attractions"]


def test_no_limit_changes_nothing():
    """Absence must never change default behaviour, only explicit opt-in does."""
    attractions = [{"name": "Long one", "distance_miles": 14},
                   {"name": "Short one", "distance_miles": 0.5}]
    assert _filter(_trip({}, list(attractions))) == attractions


def test_a_walk_beyond_the_limit_is_dropped():
    kept = _filter(_trip({"max_hike_miles": 2.0}, [
        {"name": "Long one", "distance_miles": 14},
        {"name": "Short one", "distance_miles": 0.5},
    ]))
    assert [a["name"] for a in kept] == ["Short one"]


def test_a_climb_beyond_the_limit_is_dropped():
    kept = _filter(_trip({"max_hike_elevation_gain_ft": 300}, [
        {"name": "Steep", "elevation_gain_ft": 2400},
        {"name": "Flat", "elevation_gain_ft": 60},
    ]))
    assert [a["name"] for a in kept] == ["Flat"]


def test_exactly_at_the_limit_is_kept():
    """A traveler who said four miles can walk four miles."""
    kept = _filter(_trip({"max_hike_miles": 4.0}, [{"name": "Four", "distance_miles": 4.0}]))
    assert [a["name"] for a in kept] == ["Four"]


@pytest.mark.parametrize("attraction", [
    {"name": "Unknown"},
    {"name": "Empty", "distance_miles": None},
    {"name": "Blank", "distance_miles": ""},
    {"name": "Words", "distance_miles": "about four"},
    {"name": "Placeholder zero", "distance_miles": 0},
])
def test_an_attraction_that_reports_no_distance_is_never_dropped(attraction):
    """The rule the whole filter turns on.

    These numbers are produced by a language model, so a missing one means "not
    known". Filtering on absence would quietly remove real places for want of an
    estimate, and a zero is a placeholder rather than a measurement -- no walk
    worth listing is zero miles long.
    """
    kept = _filter(_trip({"max_hike_miles": 1.0}, [dict(attraction)]))
    assert [a["name"] for a in kept] == [attraction["name"]]


def test_a_museum_is_not_a_hike():
    """Most attractions carry no walking figure at all, and must survive."""
    kept = _filter(_trip({"max_hike_miles": 1.0}, [
        {"name": "County museum", "type": "attraction"},
        {"name": "Ridge trail", "type": "hike", "distance_miles": 9},
    ]))
    assert [a["name"] for a in kept] == ["County museum"]


def test_what_is_removed_is_logged(caplog):
    """A filter that silently shortens a list is indistinguishable from a model
    that found less."""
    with caplog.at_level(logging.INFO):
        _filter(_trip({"max_hike_miles": 2.0}, [{"name": "Long one", "distance_miles": 14}]))
    assert "Long one" in caplog.text
    assert "14" in caplog.text and "2" in caplog.text


def test_both_limits_apply_together():
    kept = _filter(_trip({"max_hike_miles": 5.0, "max_hike_elevation_gain_ft": 500}, [
        {"name": "Short but steep", "distance_miles": 1, "elevation_gain_ft": 2000},
        {"name": "Long but flat", "distance_miles": 9, "elevation_gain_ft": 50},
        {"name": "Both fine", "distance_miles": 2, "elevation_gain_ft": 200},
    ]))
    assert [a["name"] for a in kept] == ["Both fine"]


def test_a_destination_with_no_attractions_is_untouched():
    trip = _trip({"max_hike_miles": 1.0}, [])
    AIContentGenerator._filter_attractions_beyond_traveler_limits(AIContentGenerator, trip)
    assert trip["destinations"][0]["top_attractions"] == []


def test_the_limits_are_accepted_by_the_parser(tmp_path):
    """Additive to the schema: a manifest carrying them still validates."""
    import yaml

    from generator.manifest_parser import ManifestParser

    manifest = tmp_path / "trip_manifest.yaml"
    manifest.write_text(yaml.safe_dump({
        "trip": {"title": "T", "subtitle": "S", "theme_color": "#123456",
                 "max_hike_miles": 1.0, "max_hike_elevation_gain_ft": 200},
        "destinations": [{"id": "a", "name": "Somewhere", "dates": "2 nights",
                          "planning_links": [{"label": "Map", "url": "https://example.com"}]}],
    }), encoding="utf-8")
    assert ManifestParser().parse(manifest)["trip"]["max_hike_miles"] == 1.0


@pytest.mark.parametrize("bad", [0, -1])
def test_a_limit_of_zero_or_less_is_refused(tmp_path, bad):
    """A limit nobody can meet is a mistake, not a preference."""
    import yaml

    from generator.manifest_parser import ManifestParser

    manifest = tmp_path / "trip_manifest.yaml"
    manifest.write_text(yaml.safe_dump({
        "trip": {"title": "T", "subtitle": "S", "theme_color": "#123456",
                 "max_hike_miles": bad},
        "destinations": [{"id": "a", "name": "Somewhere", "dates": "2 nights",
                          "planning_links": [{"label": "Map", "url": "https://example.com"}]}],
    }), encoding="utf-8")
    with pytest.raises(ValueError):
        ManifestParser().parse(manifest)
