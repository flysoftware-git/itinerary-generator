"""One place, one name.

The Old Hickory trip carried "The Hermitage" as an en-route stop on one leg
and "Andrew Jackson's Hermitage" on another. Both resolve to place_id
ChIJf2rnpoJqZIgRQyx--6HBumM -- the same historic site under two names --
while the same itinerary also lists "The Hermitage Hotel", a different place
in downtown Nashville with its own place_id. A reader seeing "The Hermitage"
could not tell which was meant.

Verified in a browser: the route link's waypoint resolved to "Andrew
Jackson's Hermitage, 4580 Rachels Ln, Hermitage, TN" while the place link
from the same trip opened "The Hermitage Hotel, 231 6th Ave N, Nashville".
Both links were correct; the names were not.
"""

from generator.main import _place_id_of, _unify_names_sharing_a_place_id

SITE = "ChIJf2rnpoJqZIgRQyx--6HBumM"
HOTEL = "ChIJhdOH0PdmZIgRVSci-4ShivQ"


def _trip(stops=(), attractions=(), restaurants=()):
    return {"destinations": [{
        "name": "Old Hickory, Tennessee",
        "ai_content": {
            "getting_here": {"en_route_stops": list(stops)},
            "top_attractions": list(attractions),
            "dinner_recommendations": list(restaurants),
        },
    }]}


def test_two_names_for_one_place_become_the_specific_one():
    stops = [{"name": "The Hermitage", "place_id": SITE},
             {"name": "Andrew Jackson's Hermitage", "place_id": SITE}]
    renamed = _unify_names_sharing_a_place_id(_trip(stops=stops))
    assert renamed == [("The Hermitage", "Andrew Jackson's Hermitage")]
    assert {s["name"] for s in stops} == {"Andrew Jackson's Hermitage"}


def test_a_different_place_keeps_its_own_name():
    """The hotel is not the historic site and must not be renamed to it."""
    stops = [{"name": "The Hermitage", "place_id": SITE},
             {"name": "Andrew Jackson's Hermitage", "place_id": SITE}]
    attractions = [{"name": "The Hermitage Hotel", "place_id": HOTEL}]
    _unify_names_sharing_a_place_id(_trip(stops=stops, attractions=attractions))
    assert attractions[0]["name"] == "The Hermitage Hotel"


def test_nothing_is_merged_or_removed():
    """The same site genuinely appears on two legs; both cards stay."""
    stops = [{"name": "The Hermitage", "place_id": SITE},
             {"name": "Andrew Jackson's Hermitage", "place_id": SITE}]
    trip = _trip(stops=stops)
    _unify_names_sharing_a_place_id(trip)
    assert len(trip["destinations"][0]["ai_content"]["getting_here"]["en_route_stops"]) == 2


def test_it_spans_sections():
    """An attraction and an en-route stop can be the same place."""
    stops = [{"name": "The Hermitage", "place_id": SITE}]
    attractions = [{"name": "Andrew Jackson's Hermitage", "place_id": SITE}]
    _unify_names_sharing_a_place_id(_trip(stops=stops, attractions=attractions))
    assert stops[0]["name"] == "Andrew Jackson's Hermitage"


def test_items_without_a_place_id_are_left_alone():
    stops = [{"name": "The Hermitage"}, {"name": "Andrew Jackson's Hermitage"}]
    assert _unify_names_sharing_a_place_id(_trip(stops=stops)) == []


def test_the_id_is_read_from_a_maps_url_when_not_a_field():
    """Attractions carry theirs inside the resolver-built link."""
    assert _place_id_of({"maps_url": f"https://x/?api=1&query=y&query_place_id={SITE}"}) == SITE
    assert _place_id_of({"place_id": SITE, "maps_url": "https://x/?query_place_id=OTHER"}) == SITE
    assert _place_id_of({}) == ""


def test_a_single_name_is_not_touched():
    stops = [{"name": "Two Rivers Mansion", "place_id": "ChIJ_29"}]
    assert _unify_names_sharing_a_place_id(_trip(stops=stops)) == []
