"""A day out can go from one place to another.

A `group_with` entry has meant a there-and-back day trip from a shared base
since GH #68, and that is one shape of outing rather than the only one. A ride
along a rail-trail is dropped off at one end and finishes at the other; so is a
paddle down a river, a one-way walk, and a lift to the top of a hill.

Four things follow from saying so in the manifest, and each is held here:

* the outing's own journey is measured from where it BEGINS, not from the bed
  it slept in;
* the next stop's journey begins where the outing ENDED, because a traveller
  who finished at the far end of a trail does not drive back to the base in
  order to leave from it;
* the map link opens the point the author named, where they named one -- a name
  several places share resolves by importance, and no spelling fixes that;
* the overview map draws the outing, which it could not while every grouped
  entry was excluded for sitting on top of its base.

The fifth is the mode. `transport_mode` is meaningless on a grouped entry and
is warned about, because a day out has no arriving relocation leg. It has a
journey of its own, though, and `mode` is how the manifest says a ride is
ridden rather than driven.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from generator.html_assembler import HTMLAssembler
from generator.main import place_destination
from generator.manifest_parser import ManifestParser
from generator.multi_site_grouping import (
    is_point_to_point,
    side_trip_end,
    side_trip_mode,
    side_trip_start,
    stated_coordinates,
)
from generator.transit_routing import resolved_mode, stamp_resolved_modes
from generator.url_discovery import URLDiscoverer

#: The two ends of a real one-way ride, and a base nowhere near either.
TRAILHEAD = {"lat": 48.10726, "lng": -123.28210}
BEACH = {"lat": 48.11959, "lng": -123.42922}
BASE = {"lat": 47.46258, "lng": -123.11590}


def _ride(**overrides):
    entry = {
        "id": "the_ride",
        "name": "Olympic Discovery Trail",
        "group_with": "base",
        "mode": "bike",
        "start": {"name": "Siebert Creek", "coordinates": dict(TRAILHEAD)},
        "end": {"name": "Hollywood Beach", "coordinates": dict(BEACH)},
    }
    entry.update(overrides)
    return entry


def _trip(*destinations):
    """The stops, with each leg's mode stamped the way parsing stamps it.

    `resolved_mode` reads that stamp rather than the manifest, so a fixture
    that skipped it would test a trip no build ever produces -- and the whole
    point of `mode` is what the stamp does with it.
    """
    stops = [dict(d) for d in destinations]
    stamp_resolved_modes({"trip": {}, "destinations": stops})
    return stops


# ── what the manifest may say ───────────────────────────────────────────────


def test_the_fields_are_only_read_on_an_entry_that_is_a_day_out():
    """`start` without `group_with` describes nothing -- there is no base for
    the outing to leave from -- and the parser warns about exactly that. A
    second, silent reading here would accept what the first one rejected."""
    loose = {"id": "somewhere", "name": "Somewhere", "start": {"name": "A"},
             "end": {"name": "B"}, "mode": "bike"}

    assert side_trip_start(loose) is None
    assert side_trip_end(loose) is None
    assert side_trip_mode(loose) == ""
    assert not is_point_to_point(loose)


def test_one_named_end_is_enough_to_make_it_one_way():
    """A ride that starts at a trailhead and finishes back at the base is
    still a one-way journey out. Calling it there-and-back would describe a
    return leg nobody rides."""
    assert is_point_to_point(_ride(end=None))
    assert is_point_to_point(_ride(start=None))
    assert not is_point_to_point({"id": "x", "group_with": "base"})


def test_an_end_with_no_name_is_not_an_end():
    assert side_trip_end(_ride(end={"coordinates": dict(BEACH)})) is None
    assert side_trip_end(_ride(end={"name": "   "})) is None


@pytest.mark.parametrize("block, expected", [
    ({"coordinates": {"lat": 48.1, "lng": -123.4}}, (48.1, -123.4)),
    ({"coordinates": {"lat": "48.1", "lng": "-123.4"}}, (48.1, -123.4)),
    ({"coordinates": {"lat": 48.1}}, None),
    ({"coordinates": "48.1,-123.4"}, None),
    ({}, None),
    (None, None),
])
def test_a_stated_point_is_read_or_it_is_nothing(block, expected):
    """None means *look it up*, which is what every manifest written before
    this field existed says. A malformed block is None too: the schema has
    already refused anything that is not two numbers in range, so reaching
    here with something else means a caller built the dict, and no build is
    worth failing over that."""
    assert stated_coordinates(block) == expected


def test_the_schema_accepts_the_new_fields_and_still_refuses_nonsense(tmp_path):
    """Through the real parser, because a schema that accepts a shape the
    helpers cannot read is two documents rather than one."""
    parser = ManifestParser()
    manifest = {
        "trip": {"title": "A ride", "subtitle": "Out along the trail",
                 "theme_color": "#C0623E"},
        "destinations": [
            {"id": "base", "name": "Lilliwaup, Washington",
             "dates": "September 12-14, 2026",
             "coordinates": dict(BASE),
             "planning_links": [{"label": "Map", "url": "https://example.com/map"}]},
            dict(_ride(), dates="September 13, 2026",
                 planning_links=[{"label": "Trail", "url": "https://example.com/trail"}]),
        ],
    }
    path = tmp_path / "trip.yaml"
    import yaml
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    loaded = parser.load(str(path))
    ride = loaded["destinations"][1]
    assert side_trip_start(ride)["name"] == "Siebert Creek"
    assert side_trip_end(ride)["name"] == "Hollywood Beach"
    assert side_trip_mode(ride) == "bike"

    # A latitude off the globe is still refused, which is the half of the
    # schema that earns the field's existence: a typed coordinate is only
    # worth believing because something checked it.
    manifest["destinations"][0]["coordinates"] = {"lat": 148.0, "lng": 0.0}
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(Exception):
        parser.load(str(path))


# ── the point is used, not re-guessed ───────────────────────────────────────


def test_a_stated_point_is_used_and_nothing_is_looked_up():
    """The reason the field exists. 'Hollywood Beach' is a park in Port
    Angeles and a hamlet 190 km east, both in Washington, and a gazetteer
    answers with the one it ranks higher -- confidently, and with no spelling
    that would have asked the other question."""
    asked: list[str] = []
    dest = _ride(coordinates=dict(BEACH))

    place_destination(dest, lambda name: asked.append(name) or (0.0, 0.0))

    assert asked == [], asked
    assert (dest["lat"], dest["lng"]) == (BEACH["lat"], BEACH["lng"])
    assert (dest["start"]["lat"], dest["start"]["lng"]) == (TRAILHEAD["lat"], TRAILHEAD["lng"])


def test_a_stop_with_no_stated_point_is_geocoded_exactly_as_before():
    asked: list[str] = []
    dest = {"id": "moab", "name": "Moab, Utah"}

    place_destination(dest, lambda name: asked.append(name) or (38.57, -109.55))

    assert asked == ["Moab, Utah"]
    assert (dest["lat"], dest["lng"]) == (38.57, -109.55)


def test_an_outing_end_is_never_geocoded():
    """Placed from stated coordinates only. These are, by their nature, the
    small ambiguous names a gazetteer is worst at, and two extra lookups per
    grouped entry is the cost of asking it anyway."""
    asked: list[str] = []
    dest = _ride(start={"name": "Siebert Creek"}, coordinates=dict(BEACH))

    place_destination(dest, lambda name: asked.append(name) or (0.0, 0.0))

    assert asked == []
    assert "lat" not in dest["start"]


# ── the outing's own mode ───────────────────────────────────────────────────


def test_the_outing_is_ridden_rather_than_driven():
    """`transport_mode` is forced to `auto` on a grouped entry because there is
    no arriving relocation leg for it to describe. That left a bike ride
    offering driving directions, which is the other half of the same
    sentence."""
    base = {"id": "base", "name": "Lilliwaup, Washington"}
    _, ride = _trip(base, _ride())
    assert resolved_mode(ride) == "bike"

    _, plain = _trip(base, _ride(mode=None))
    assert resolved_mode(plain) == "auto"

    _, arches = _trip({"id": "moab", "name": "Moab, Utah"},
                      {"id": "arches", "name": "Arches National Park", "group_with": "moab"})
    assert resolved_mode(arches) == "auto"


# ── where each leg starts ───────────────────────────────────────────────────


def _origins(destinations):
    """Run url_discovery's origin resolution and read back what it decided."""
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    URLDiscoverer._resolve_en_route_origins(discoverer, destinations)
    return {d["id"]: d.get("_en_route_origin") for d in destinations}


def test_the_outing_is_measured_from_where_it_begins():
    stops = _trip(
        {"id": "base", "name": "Lilliwaup, Washington", **BASE},
        _ride(),
        {"id": "port_townsend", "name": "Port Townsend, Washington"},
    )

    origins = _origins(stops)

    assert origins["the_ride"] == "Siebert Creek"


def test_the_next_stop_leaves_from_where_the_ride_finished():
    """The owner's case. Measuring the next leg from the base describes a
    drive nobody makes, and on a long trail it is the wrong side of a
    mountain."""
    stops = _trip(
        {"id": "base", "name": "Lilliwaup, Washington", **BASE},
        _ride(),
        {"id": "port_townsend", "name": "Port Townsend, Washington"},
    )

    origins = _origins(stops)

    assert origins["port_townsend"] == "Hollywood Beach"


def test_a_there_and_back_day_trip_leaves_the_next_stop_alone():
    """GH #68's own rule, untouched: a run of day trips does not advance the
    physical base, so the stop after them still leaves from the bed."""
    stops = _trip(
        {"id": "moab", "name": "Moab, Utah"},
        {"id": "arches", "name": "Arches National Park", "group_with": "moab"},
        {"id": "canyonlands", "name": "Canyonlands National Park", "group_with": "moab"},
        {"id": "telluride", "name": "Telluride, Colorado"},
    )

    origins = _origins(stops)

    assert origins["arches"] == "Moab, Utah"
    assert origins["canyonlands"] == "Moab, Utah"
    assert origins["telluride"] == "Moab, Utah"


def test_only_the_stop_straight_after_the_ride_leaves_from_its_end():
    """A ride's end is where the traveller stands for one leg. The stop after
    that one leaves from the bed it slept in, like every other."""
    stops = _trip(
        {"id": "base", "name": "Lilliwaup, Washington", **BASE},
        _ride(),
        {"id": "port_townsend", "name": "Port Townsend, Washington"},
        {"id": "oak_harbor", "name": "Oak Harbor, Washington"},
    )

    origins = _origins(stops)

    assert origins["oak_harbor"] == "Port Townsend, Washington"


# ── what the page renders ───────────────────────────────────────────────────


def _rendered_leg(previous_name, dest, previous_point=None):
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._config = {}
    ai = {"getting_here": {"route_summary": "Along the water.",
                           "distance_miles": "24", "travel_time": "2h"}}
    return assembler._build_getting_here(
        ai, dest, previous_name, previous_route_target=previous_name,
        current_route_target=str(dest.get("name", "")),
        previous_point=previous_point,
    )


def test_the_leg_is_labelled_by_the_two_ends_of_the_ride():
    _, ride = _trip({"id": "base", "name": "Lilliwaup, Washington"}, _ride())
    html = _rendered_leg("Siebert Creek", ride)

    assert "Siebert Creek → Hollywood Beach" in html
    # Still a day out, and said with the word that is true of a one-way one.
    assert "Day Out — One Way" in html
    assert ">Day Trip<" not in html


def test_a_there_and_back_day_trip_is_still_called_one():
    html = _rendered_leg("Moab, Utah", {"id": "arches", "name": "Arches National Park",
                                        "group_with": "moab"})

    assert "Day Trip" in html
    assert "One Way" not in html


def test_the_map_link_opens_the_points_the_author_named():
    """A link built from a name opens whichever place Google ranks highest,
    which is the coin toss the field exists to settle."""
    _, ride = _trip({"id": "base", "name": "Lilliwaup, Washington"}, _ride())
    html = _rendered_leg("Siebert Creek", ride,
                         previous_point=(TRAILHEAD["lat"], TRAILHEAD["lng"]))

    assert f"origin=48.10726%2C-123.2821" in html
    assert f"destination=48.11959%2C-123.42922" in html
    # And it is ridden, not driven.
    assert "travelmode=bicycling" in html


def test_a_stop_with_no_stated_point_still_links_by_name():
    html = _rendered_leg("Moab, Utah", {"id": "arches", "name": "Arches National Park",
                                        "group_with": "moab"})

    assert "destination=Arches%20National%20Park" in html
    assert "origin=Moab%2C%20Utah" in html


# ── what the overview map draws ─────────────────────────────────────────────


def _legs(destinations):
    return HTMLAssembler._build_side_trip_legs(
        HTMLAssembler.__new__(HTMLAssembler), destinations)


def test_the_overview_map_draws_the_ride():
    legs = _legs(_trip({"id": "base", "name": "Lilliwaup, Washington", **BASE}, _ride()))

    assert len(legs) == 1
    assert legs[0]["a"] == [TRAILHEAD["lat"], TRAILHEAD["lng"]]
    assert legs[0]["b"] == [BEACH["lat"], BEACH["lng"]]
    assert legs[0]["name"] == "Siebert Creek → Hollywood Beach"
    assert legs[0]["mode"] == "bike"


def test_an_outing_with_one_named_end_runs_from_the_base():
    """That is the journey: the traveller was at the base and the ride ended
    somewhere else. Drawing only the outings with both ends named would leave
    out the commonest shape of one."""
    legs = _legs(_trip({"id": "base", "name": "Lilliwaup, Washington", **BASE},
                       _ride(start=None)))

    assert len(legs) == 1
    assert legs[0]["a"] == [BASE["lat"], BASE["lng"]]
    assert legs[0]["name"] == "Lilliwaup, Washington → Hollywood Beach"


def test_a_there_and_back_day_trip_draws_nothing():
    """The exclusion this keeps: a grouped entry shares its base's lodging, so
    its marker lands on top of the base's own. Nothing about a ride that goes
    somewhere else is like that, and nothing about this changes it."""
    legs = _legs(_trip({"id": "moab", "name": "Moab, Utah", "lat": 38.57, "lng": -109.55},
                       {"id": "arches", "name": "Arches National Park",
                        "group_with": "moab", "lat": 38.73, "lng": -109.59}))

    assert legs == []


def test_an_outing_whose_ends_land_in_one_place_draws_nothing():
    """A zero-length line is a smudge, and says nothing the base's own marker
    does not."""
    legs = _legs(_trip({"id": "base", "name": "Lilliwaup, Washington", **BASE},
                       _ride(start={"name": "Here", "coordinates": dict(BEACH)})))

    assert legs == []


def test_an_unplaceable_end_draws_nothing():
    legs = _legs(_trip({"id": "base", "name": "Lilliwaup, Washington"},
                       _ride(start={"name": "Siebert Creek"}, end={"name": "Hollywood Beach"})))

    assert legs == []


def test_the_template_has_somewhere_to_draw_them():
    """The payload and the placeholder are two halves of one thing, and a
    replace() against a placeholder that is not there fails silently -- the
    page renders, with a JavaScript syntax error where the array should be."""
    template = (Path(__file__).resolve().parent.parent
                / "templates" / "v2.5_template.html").read_text(encoding="utf-8")

    assert "'<!--SIDE_TRIPS_JSON-->'" in template
    assert template.count("'<!--SIDE_TRIPS_JSON-->'") == 1
