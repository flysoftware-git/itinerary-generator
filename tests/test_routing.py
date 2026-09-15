"""Road routing for a leg, and the fallback when there is none.

No network. The transport is replaced with one that returns a recorded reply
shape, or raises, so every branch -- routed, ferry, refused, unparsable, no key,
cached -- is a fact the test controls.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from generator import road_estimate, routing

ISSAQUAH = (47.5301, -122.0326)
PORT_TOWNSEND = (48.1170, -122.7604)
OAK_HARBOR = (48.2932, -122.6432)


def _reply(miles, seconds, waytypes=None):
    """The shape OpenRouteService returned for these legs on 2026-09-14."""
    route = {"summary": {"distance": miles, "duration": seconds}}
    if waytypes is not None:
        route["extras"] = {"waytype": {"summary": waytypes}}
    return {"routes": [route]}


class Transport:
    def __init__(self, answer):
        self.answer = answer
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(json.loads(request.data.decode()))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return io.BytesIO(json.dumps(self.answer).encode())


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "routes.json"


def test_a_ferry_leg_is_routed_with_its_crossing(monkeypatch, cache):
    """Port Townsend to Oak Harbor is the Coupeville ferry: 27% of the route."""
    transport = Transport(_reply(21.246, 5064.5, [
        {"value": 1.0, "distance": 0.006, "amount": 46.22},
        {"value": 9.0, "distance": 0.004, "amount": 27.18},
        {"value": 2.0, "distance": 0.004, "amount": 26.59},
    ]))
    monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
    leg = routing.route_leg(PORT_TOWNSEND, OAK_HARBOR, key="k", cache_path=cache)
    assert leg.miles == 21.2 and round(leg.minutes) == 84
    assert leg.has_ferry and leg.ferry_share == pytest.approx(0.2718)
    assert transport.requests[0]["coordinates"] == [
        [PORT_TOWNSEND[1], PORT_TOWNSEND[0]], [OAK_HARBOR[1], OAK_HARBOR[0]]], (
        "OpenRouteService takes longitude first")
    assert transport.requests[0]["extra_info"] == ["waytype"]


def test_without_a_key_nothing_is_asked(monkeypatch, cache):
    transport = Transport(_reply(1, 1))
    monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
    assert routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="", cache_path=cache) is None
    assert transport.requests == []


@pytest.mark.parametrize("failure", [
    urllib.error.HTTPError("u", 429, "quota", {}, None),
    urllib.error.URLError("down"),
    TimeoutError("slow"),
])
def test_a_refusal_is_a_fallback_and_is_not_remembered(monkeypatch, cache, failure):
    monkeypatch.setattr(routing.urllib.request, "urlopen", Transport(failure))
    assert routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache) is None
    assert not cache.exists() or json.loads(cache.read_text()) == {}


def test_an_unparsable_reply_is_a_fallback(monkeypatch, cache):
    monkeypatch.setattr(routing.urllib.request, "urlopen", Transport({"routes": []}))
    assert routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache) is None


def test_a_routed_leg_is_asked_once(monkeypatch, cache):
    transport = Transport(_reply(75.781, 9069.9))
    monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
    first = routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache)
    second = routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache)
    assert first == second and len(transport.requests) == 1


def test_leg_estimate_prefers_the_route():
    """Issaquah to Port Townsend: 75.8 mi and about 2.5 h routed, where the
    straight-line estimate says about 69 mi and 1.4 h."""
    routed = routing.RoutedLeg(miles=75.8, minutes=151.2, ferry_share=0.0736)
    leg = road_estimate.leg_estimate(ISSAQUAH, PORT_TOWNSEND, router=lambda a, b: routed)
    assert leg.routed and leg.miles == 75.8 and leg.minutes == 151.2 and leg.has_ferry

    estimated = road_estimate.leg_estimate(ISSAQUAH, PORT_TOWNSEND, router=lambda a, b: None)
    assert not estimated.routed and not estimated.has_ferry
    assert estimated.minutes < 100, "the fallback is the old estimate, unchanged"


def test_leg_estimate_with_no_key_is_the_old_estimate():
    """The default router, and `conftest.py` has taken the key away: an install
    without one gets the straight-line estimate and makes no request."""
    leg = road_estimate.leg_estimate(ISSAQUAH, PORT_TOWNSEND)
    assert leg is not None and not leg.routed
    miles = road_estimate.road_distance_miles(
        road_estimate.straight_line_miles(ISSAQUAH, PORT_TOWNSEND))
    assert leg.miles == round(miles, 1)


def test_a_routed_leg_replaces_the_maps_scrape_and_the_estimate(monkeypatch):
    """The engine's leg figure comes through `leg_estimate`. Where it routes,
    the Google Maps page is not fetched and the straight-line estimate is not
    what lands in `getting_here`. Red with the routed branch removed from
    `_update_route_distance_and_time`."""
    from generator import url_discovery
    from generator.url_discovery import URLDiscoverer

    routed = road_estimate.LegEstimate(miles=75.8, minutes=151.2, routed=True, ferry_share=0.07)
    monkeypatch.setattr(url_discovery, "leg_estimate", lambda a, b: routed)
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._route_distance_live_fetch_enabled = True

    def _no_scrape(url):
        raise AssertionError("the Maps page was fetched for a routed leg")

    discoverer._parse_route_info_from_maps_html = _no_scrape
    getting_here = {"travel_time": "", "distance_miles": ""}
    discoverer._update_route_distance_and_time(
        ai={"getting_here": getting_here}, getting_here=getting_here,
        origin_name="Issaquah", dest_name="Port Townsend",
        origin_lat=ISSAQUAH[0], origin_lng=ISSAQUAH[1],
        dest_lat=PORT_TOWNSEND[0], dest_lng=PORT_TOWNSEND[1],
        dest={"name": "Port Townsend"},
    )
    assert getting_here["distance_miles"] == "76"
    assert getting_here["travel_time"] == "2 hr 31 min"


def test_points_under_half_a_mile_apart_have_no_leg():
    assert road_estimate.leg_estimate(ISSAQUAH, (47.5302, -122.0327)) is None


# ── The line the route follows ───────────────────────────────────────────────

#: A real OpenRouteService reply, recorded 2026-09-14 for Port Townsend to Oak
#: Harbor with exactly the request `route_leg` sends: 358 points, the
#: Port Townsend - Coupeville ferry at waytype range [38, 50].
RECORDED = Path(__file__).parent / "fixtures" / "ors_port_townsend_oak_harbor.json"

PORT_TOWNSEND_TERMINAL = (48.11111, -122.75902)
COUPEVILLE_TERMINAL = (48.15913, -122.67271)


def _recorded():
    return json.loads(RECORDED.read_text(encoding="utf-8"))


def test_the_recorded_polyline_decodes_at_precision_five():
    """The decoded line spans the reply's own `bbox` and has one point per
    index the reply's `way_points` and `waytype` ranges address."""
    route = _recorded()["routes"][0]
    points = routing.decode_polyline(route["geometry"])
    assert len(points) == route["way_points"][-1] + 1 == 358
    assert points[0] == (48.11702, -122.76035)
    assert points[-1] == (48.29325, -122.6432)
    lats, lngs = [p[0] for p in points], [p[1] for p in points]
    west, south, east, north = route["bbox"]
    assert (min(lngs), min(lats), max(lngs), max(lats)) == (
        round(west, 5), round(south, 5), round(east, 5), round(north, 5))
    assert points[38] == PORT_TOWNSEND_TERMINAL and points[50] == COUPEVILLE_TERMINAL


def test_the_reference_polyline_decodes():
    """The worked example published with the encoding algorithm."""
    assert routing.decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [
        (38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]


def test_a_truncated_polyline_is_refused():
    with pytest.raises(ValueError):
        routing.decode_polyline("_p~iF~ps|U_")


def test_simplification_is_bounded_and_keeps_both_ends():
    """A 5,000-point zigzag every point of which matters at a metre."""
    points = [(40.0 + i * 0.0001, -105.0 + (0.001 if i % 2 else 0.0)) for i in range(5000)]
    simple, spans = routing.simplify_geometry(points, [(1000, 1200)])
    assert len(simple) == routing.MAX_GEOMETRY_POINTS, (
        "the bound is used, not overshot to a handful of points")
    assert simple[0] == points[0] and simple[-1] == points[-1]
    (start, end), = spans
    assert simple[start] == points[1000] and simple[end] == points[1200]


def test_a_line_already_small_enough_loses_only_what_does_not_bend():
    straight = [(40.0 + i * 0.001, -105.0) for i in range(50)]
    assert routing.simplify_geometry(straight)[0] == (straight[0], straight[-1])
    bends = [(40.0, -105.0), (40.01, -105.0), (40.01, -104.99), (40.02, -104.99)]
    assert routing.simplify_geometry(bends)[0] == tuple(bends)


def test_a_recorded_ferry_leg_keeps_its_shape_and_its_crossing(monkeypatch, cache):
    transport = Transport(_recorded())
    monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
    leg = routing.route_leg(PORT_TOWNSEND, OAK_HARBOR, key="k", cache_path=cache)
    assert leg.miles == 21.2 and leg.ferry_share == pytest.approx(0.2718)
    assert leg.geometry is not None
    assert 2 < len(leg.geometry) <= routing.MAX_GEOMETRY_POINTS
    assert leg.geometry[0] == (48.11702, -122.76035)
    assert leg.geometry[-1] == (48.29325, -122.6432)
    (start, end), = leg.ferry_spans
    assert leg.geometry[start] == PORT_TOWNSEND_TERMINAL
    assert leg.geometry[end] == COUPEVILLE_TERMINAL

    estimate = road_estimate.leg_estimate(PORT_TOWNSEND, OAK_HARBOR, router=lambda a, b: leg)
    assert estimate.routed and estimate.geometry == leg.geometry
    assert estimate.ferry_spans == leg.ferry_spans


def test_geometry_survives_the_cache(monkeypatch, cache):
    transport = Transport(_recorded())
    monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
    first = routing.route_leg(PORT_TOWNSEND, OAK_HARBOR, key="k", cache_path=cache)
    second = routing.route_leg(PORT_TOWNSEND, OAK_HARBOR, key="k", cache_path=cache)
    assert len(transport.requests) == 1
    assert second.geometry is not None and second == first


def test_a_cache_entry_from_before_geometry_still_loads_and_is_not_re_asked(monkeypatch, cache):
    """No re-route storm: an old entry is a routed leg without a shape."""
    cache.write_text(json.dumps({routing._cache_key(PORT_TOWNSEND, OAK_HARBOR): {
        "miles": 21.2, "minutes": 84.4, "ferry_share": 0.2718}}), encoding="utf-8")
    transport = Transport(_recorded())
    monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
    leg = routing.route_leg(PORT_TOWNSEND, OAK_HARBOR, key="k", cache_path=cache)
    assert transport.requests == []
    assert leg == routing.RoutedLeg(21.2, 84.4, 0.2718)
    assert leg.geometry is None and leg.ferry_spans == ()


def test_a_reply_without_geometry_still_routes(monkeypatch, cache):
    monkeypatch.setattr(routing.urllib.request, "urlopen", Transport(_reply(75.781, 9069.9)))
    leg = routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache)
    assert leg.miles == 75.8 and leg.geometry is None and leg.ferry_spans == ()


def test_an_estimated_leg_has_no_geometry():
    leg = road_estimate.leg_estimate(ISSAQUAH, PORT_TOWNSEND, router=lambda a, b: None)
    assert not leg.routed and leg.geometry is None and leg.ferry_spans == ()
