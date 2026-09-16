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


# ── A place that is not on a road, and a leg that is not driven ──────────────

SIEBERT_CREEK = (48.0733, -123.2035)
HOLLYWOOD_BEACH = (48.1204, -123.4290)


def _refusal(code, message="", status=404):
    """An OpenRouteService refusal, body and all.

    The body is where the router says *which* refusal this is; a 404 with no
    code is *these two points do not connect*, and a 404 with 2010 is *I found
    no road near one of them*.
    """
    body = json.dumps({"error": {"code": code, "message": message}}).encode() if code else b""
    return urllib.error.HTTPError("u", status, "refused", {}, io.BytesIO(body))


class Sequence:
    """A transport answering each call from a list, in order."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []
        self.urls = []

    def __call__(self, request, timeout=None):
        self.requests.append(json.loads(request.data.decode()))
        self.urls.append(request.full_url)
        answer = self.answers.pop(0) if self.answers else self.requests and None
        if isinstance(answer, BaseException):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())


def test_every_request_allows_the_router_to_snap_to_a_road(monkeypatch, cache):
    """A geocoded creek is a stream, not a road. Both coordinates carry an
    allowance, in metres, in the order they were given."""
    transport = Sequence(_reply(57.0, 4284.0))
    monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
    routing.route_leg(ISSAQUAH, SIEBERT_CREEK, key="k", cache_path=cache)
    assert transport.requests[0]["radiuses"] == [routing.SNAP_RADIUS_M,
                                                 routing.SNAP_RADIUS_M]


def test_a_point_with_no_road_near_it_is_asked_again_further_out(monkeypatch, cache):
    """OpenRouteService's 2010: asked again once, wider, and that answer is the
    leg. Without it the drive to a creek is a straight line."""
    transport = Sequence(
        _refusal(routing.NO_ROUTABLE_POINT,
                 "Could not find routable point within a radius of 350.0 meters "
                 "of specified coordinate 1: 48.073300 -123.203500."),
        _reply(57.0, 4284.0))
    monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
    leg = routing.route_leg(ISSAQUAH, SIEBERT_CREEK, key="k", cache_path=cache)
    assert leg is not None and leg.miles == 57.0, "the wider ask was not made or not used"
    assert [r["radiuses"] for r in transport.requests] == [
        [routing.SNAP_RADIUS_M] * 2, [routing.WIDE_SNAP_RADIUS_M] * 2]


def test_the_wider_ask_is_made_once_and_then_cached(monkeypatch, cache):
    transport = Sequence(_refusal(routing.NO_ROUTABLE_POINT), _reply(57.0, 4284.0))
    monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
    first = routing.route_leg(ISSAQUAH, SIEBERT_CREEK, key="k", cache_path=cache)
    second = routing.route_leg(ISSAQUAH, SIEBERT_CREEK, key="k", cache_path=cache)
    assert first == second and len(transport.requests) == 2, (
        "the snapped answer was not cached under the ordinary key")


def test_a_point_no_radius_reaches_is_still_a_fallback(monkeypatch, cache):
    """Refused twice is refused: the straight line is then the honest answer,
    and nothing is remembered so tomorrow's run asks again."""
    transport = Sequence(_refusal(routing.NO_ROUTABLE_POINT),
                         _refusal(routing.NO_ROUTABLE_POINT))
    monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
    assert routing.route_leg(ISSAQUAH, SIEBERT_CREEK, key="k", cache_path=cache) is None
    assert len(transport.requests) == 2
    assert not cache.exists() or json.loads(cache.read_text()) == {}


def test_a_404_that_is_not_about_a_radius_is_not_asked_again(monkeypatch, cache):
    """Two points that do not connect are two points that do not connect: only
    2010 means *look further out*."""
    transport = Sequence(_refusal(2009, "Route could not be found"), _reply(1.0, 1.0))
    monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
    assert routing.route_leg(ISSAQUAH, SIEBERT_CREEK, key="k", cache_path=cache) is None
    assert len(transport.requests) == 1, "a refusal that was not about a radius was retried"


def test_a_leg_can_be_routed_as_a_ride(monkeypatch, cache):
    """A bike leg asks the cycling profile, and gets the cycling endpoint."""
    transport = Sequence(_reply(11.4, 3600.0))
    monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
    leg = routing.route_leg(SIEBERT_CREEK, HOLLYWOOD_BEACH, key="k", cache_path=cache,
                            profile="cycling-regular")
    assert leg.miles == 11.4
    assert transport.urls == [f"{routing.ENDPOINT_BASE}/cycling-regular"]


def test_the_driving_answer_and_the_cycling_answer_are_different_entries(monkeypatch, cache):
    """One pair, two questions. Sharing a key would serve a car's road as the
    ride, which is the number the traveller would plan their day on."""
    transport = Sequence(_reply(18.0, 1500.0), _reply(11.4, 3600.0))
    monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
    drive = routing.route_leg(SIEBERT_CREEK, HOLLYWOOD_BEACH, key="k", cache_path=cache)
    ride = routing.route_leg(SIEBERT_CREEK, HOLLYWOOD_BEACH, key="k", cache_path=cache,
                             profile="cycling-regular")
    assert (drive.miles, ride.miles) == (18.0, 11.4)
    assert len(transport.requests) == 2
    keys = set(json.loads(cache.read_text()))
    assert len(keys) == 2 and any("cycling-regular" in k for k in keys)


def test_a_driving_leg_keeps_the_cache_key_it_always_had(monkeypatch, cache):
    """An existing cache still hits: profiles did not move the driving entry."""
    assert routing._cache_key(ISSAQUAH, PORT_TOWNSEND) == (
        f"{ISSAQUAH[0]:.4f},{ISSAQUAH[1]:.4f}>{PORT_TOWNSEND[0]:.4f},{PORT_TOWNSEND[1]:.4f}")


@pytest.mark.parametrize("bad", ["bicycle", "cycling", "", "driving-car "])
def test_a_profile_nobody_offers_is_refused(bad, cache):
    with pytest.raises(ValueError):
        routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache, profile=bad)
    with pytest.raises(ValueError):
        road_estimate.leg_estimate(ISSAQUAH, PORT_TOWNSEND, profile=bad)


def test_leg_estimate_carries_the_profile_to_the_router(monkeypatch, cache):
    transport = Sequence(_reply(11.4, 3600.0))
    monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
    monkeypatch.setattr(routing, "DEFAULT_CACHE_PATH", str(cache))
    monkeypatch.setenv(routing.API_KEY_ENV, "k")
    leg = road_estimate.leg_estimate(SIEBERT_CREEK, HOLLYWOOD_BEACH,
                                     profile="cycling-regular")
    assert leg.routed and leg.miles == 11.4
    assert transport.urls == [f"{routing.ENDPOINT_BASE}/cycling-regular"]
