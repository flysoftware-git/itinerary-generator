"""The route panel must name the stops the itinerary names.

A coordinate waypoint resolves to the right point, but Google labels it by
reverse geocoding: the St. George -> Zion route panel read "Millcreek 2nd post
market, 5FGM+75" where the card said "Red Cliffs National Conservation Area
Overlook". Six listed stops, six pins, no correspondence a reader could see --
indistinguishable from the pins being wrong.

A place_id is precise AND labelled. The resolver already produces them (107
place_id links on the sw build) and stores each inside the item's maps_url, so
reusing them costs no additional API call.
"""

import re
import urllib.parse

from generator.html_assembler import HTMLAssembler

PID = "ChIJN1t_tDeuEmsRUsoyG83frY4"


def _url(stops):
    ai = {"getting_here": {"en_route_stops": stops, "drive_time": "2 hr", "distance_miles": 45}}
    html = HTMLAssembler._build_getting_here(
        HTMLAssembler.__new__(HTMLAssembler), ai,
        {"name": "Zion National Park, Utah"}, "St. George, Utah",
    )
    m = re.search(r'href="(https://www\.google\.com/maps/dir/\?[^"]*)"', html)
    return urllib.parse.parse_qs(urllib.parse.urlparse(m.group(1).replace("&amp;", "&")).query) if m else {}


def _stop(name, pid=None, lat=None, lng=None):
    s = {"name": name, "is_seed": True}
    if pid:
        s["maps_url"] = f"https://www.google.com/maps/place/?q=place_id:{pid}"
    if lat is not None:
        s["geocode_lat"], s["geocode_lng"] = lat, lng
    return s


class TestPlaceIdRecovery:
    def test_an_id_is_recovered_from_a_resolver_link(self):
        assert HTMLAssembler._place_id_from_maps_url(
            f"https://www.google.com/maps/place/?q=place_id:{PID}") == PID

    def test_a_coordinate_link_yields_nothing(self):
        assert HTMLAssembler._place_id_from_maps_url(
            "https://www.google.com/maps/search/?api=1&query=37.1,-113.5") == ""


class TestWaypointLabelling:
    def test_stops_with_ids_are_named_not_reverse_geocoded(self):
        q = _url([_stop("Red Cliffs National Conservation Area Overlook", pid=PID)])
        assert "Red Cliffs National Conservation Area Overlook" in q.get("waypoints", [""])[0]
        assert PID in q.get("waypoint_place_ids", [""])[0]

    def test_ids_are_all_or_nothing(self):
        """A partial list would misalign labels against points.

        waypoint_place_ids corresponds positionally to waypoints, so one stop
        without an id has to send the whole leg back to coordinates rather than
        pair the wrong id with the wrong place.
        """
        q = _url([
            _stop("Has An Id", pid=PID),
            _stop("No Id At All", lat=37.15, lng=-113.4),
        ])
        assert "waypoint_place_ids" not in q

    def test_a_leg_with_no_ids_is_unchanged(self):
        q = _url([_stop("Somewhere", lat=37.15, lng=-113.4)])
        assert "waypoint_place_ids" not in q
