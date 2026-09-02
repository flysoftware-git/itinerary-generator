"""A Maps link must say where on earth it means.

Reported on the sw build: "Rico Historic District" on the Telluride ->
Pagosa Springs leg carried a bare
`maps/search/?api=1&query=Rico%20Historic%20District` and appeared as a bare
waypoint in that leg's route URL, putting a San Juan Island pin in Washington
State on a Colorado route.

The codebase already knew this failure. Two comments record it: "Canyon
Overlook Trail" resolving to "Stan's Overlook Trail, Snoqualmie, WA", and the
literal string "optimize:true" matching "Optimize Health, a Washington-state
clinic" for a 2,196-mile route.
"""

import pytest

from generator.html_assembler import HTMLAssembler
from generator.url_discovery import URLDiscoverer

BARE = "https://www.google.com/maps/search/?api=1&query=Rico%20Historic%20District"
COORD = "https://www.google.com/maps/search/?api=1&query=37.4062558,-108.2709652"


class TestRequalification:
    def test_a_bare_query_gains_its_destination(self):
        out = URLDiscoverer.requalify_maps_query_url(BARE, "Rico Historic District", "Pagosa Springs")
        assert "Pagosa" in out

    def test_a_coordinate_query_is_never_rewritten(self):
        """Rewriting a coordinate into a name search is the documented downgrade."""
        assert URLDiscoverer.requalify_maps_query_url(COORD, "Mancos", "Pagosa Springs") == COORD

    def test_an_already_qualified_query_is_unchanged(self):
        already = "https://www.google.com/maps/search/?api=1&query=Rico%20Historic%20District%20Pagosa%20Springs"
        assert URLDiscoverer.requalify_maps_query_url(already, "Rico Historic District", "Pagosa Springs") == already

    @pytest.mark.parametrize("url", ["", "https://example.com/x", "not a url"])
    def test_non_maps_input_is_untouched(self, url):
        assert URLDiscoverer.requalify_maps_query_url(url, "X", "Y") == url


class TestRouteWaypoints:
    def _html(self, stop):
        ai = {"getting_here": {"en_route_stops": [stop], "drive_time": "3 hr", "distance_miles": 120}}
        return HTMLAssembler._build_getting_here(
            HTMLAssembler.__new__(HTMLAssembler), ai,
            {"name": "Pagosa Springs, Colorado"}, "Telluride, Colorado",
        )

    def test_a_waypoint_is_never_a_bare_place_name(self):
        """Either destination-qualified, or omitted -- never bare.

        With a location-qualified arrival scope the stop is qualified into
        "Rico Historic District Pagosa Springs, Colorado". Without one it is
        left off the route line entirely. What must never ship is the bare
        name, which is what put a Washington pin on a Colorado route.
        """
        import re

        html = self._html({"name": "Rico Historic District", "is_seed": True})
        for wp in re.findall(r"waypoints=([^&\"']*)", html):
            for one in wp.split("%7C"):
                assert one != "Rico%20Historic%20District", "bare waypoint shipped"

    def test_an_unqualifiable_stop_is_left_off_the_route(self):
        """A map missing a pin beats a map that detours to another state."""
        import re

        ai = {"getting_here": {"en_route_stops": [{"name": "Rico Historic District", "is_seed": True}],
                               "drive_time": "3 hr", "distance_miles": 120}}
        html = HTMLAssembler._build_getting_here(
            HTMLAssembler.__new__(HTMLAssembler), ai, {"name": "Rico"}, "Telluride",
        )
        for wp in re.findall(r"waypoints=([^&\"']*)", html):
            assert "Rico%20Historic%20District" not in wp.split("%7C")

    def test_the_stop_still_appears_as_a_card(self):
        """Omitted from the route line, not from the itinerary."""
        html = self._html({"name": "Rico Historic District", "is_seed": True})
        assert "Rico Historic District" in html
