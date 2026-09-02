"""A detour figure must be bounded above as well as below.

_resolve_en_route_stop_detour_metrics_against_geometry floored an under-stated
detour and never questioned an over-stated one, so a stop sitting ON the route
kept whatever the model wrote about it. Reported on the Capitol Reef -> Moab
leg, where San Rafael Swell and the John Wesley Powell Museum -- both on I-70
-- showed detours above the inclusion threshold, and again for Mancos State
Park Entrance at "46.0 mi".

The geometry bounds both directions: a detour cannot sensibly cost several
times the round trip its own offset implies.
"""

from generator.url_discovery import URLDiscoverer


def _resolve(stop, origin, dest):
    return URLDiscoverer._resolve_en_route_stop_detour_metrics_against_geometry(
        URLDiscoverer.__new__(URLDiscoverer), stop, origin=origin, dest=dest
    )


# Capitol Reef -> Moab, roughly along I-70
ORIGIN = (38.2919, -111.2615)
DEST = (38.5733, -109.5498)


def test_an_on_route_stop_loses_an_absurd_detour():
    """A point essentially on the line cannot be a 46-mile round trip."""
    on_route = {
        "name": "John Wesley Powell Museum",
        "geocode_lat": 38.4326, "geocode_lng": -110.4056,
        "detour_distance_miles": 46.0, "detour_time_minutes": 60,
    }
    miles, minutes, overridden = _resolve(on_route, ORIGIN, DEST)
    assert overridden is True
    assert miles < 46.0


def test_a_plausible_figure_is_left_alone():
    """The tolerance is loose on purpose -- real roads bend.

    Goblin Valley sits 12.9 mi off this route, so its provable round-trip
    floor is 25.7 mi and the geometry estimate is 33.4. A text figure of 40
    is above the floor and far below the 100-mile ceiling, so it stands.
    """
    stop = {
        "name": "Goblin Valley State Park",
        "geocode_lat": 38.5733, "geocode_lng": -110.7080,
        "detour_distance_miles": 40.0, "detour_time_minutes": 55,
    }
    miles, _, overridden = _resolve(stop, ORIGIN, DEST)
    assert miles == 40.0 and overridden is False


def test_a_stop_with_no_geocode_is_untouched():
    stop = {"name": "Somewhere", "detour_distance_miles": 99.0, "detour_time_minutes": 120}
    miles, minutes, overridden = _resolve(stop, ORIGIN, DEST)
    assert (miles, minutes, overridden) == (99.0, 120, False)


def test_the_floor_still_applies():
    """Capping must not have removed the under-statement correction."""
    understated = {
        "name": "Far Off Thing",
        "geocode_lat": 39.5, "geocode_lng": -110.5,
        "detour_distance_miles": 0.5, "detour_time_minutes": 1,
    }
    miles, _, overridden = _resolve(understated, ORIGIN, DEST)
    assert overridden is True and miles > 0.5


def test_both_bounds_are_round_trip():
    """The label says round trip because the maths is round trip."""
    floor_miles, _ = URLDiscoverer._en_route_stop_geometry_grounded_detour_floor(10.0)
    assert floor_miles == 20.0
