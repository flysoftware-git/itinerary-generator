"""Road routing for a leg, and the fallback when there is none.

No network. The transport is replaced with one that returns a recorded reply
shape, or raises, so every branch -- routed, ferry, refused, unparsable, no key,
cached -- is a fact the test controls.
"""

from __future__ import annotations

import io
import json
import urllib.error

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
