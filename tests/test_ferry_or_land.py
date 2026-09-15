"""A leg that crosses water: the ferry route, the land route, and the choice.

No network. OpenRouteService replies recorded 2026-09-14 with exactly the
requests `generator.routing.route_leg` sends are served by a transport that
answers the ordinary request and the ferry-avoiding one separately, and counts
both.

| leg | ferry route | land route | chosen |
|---|---|---|---|
| Portage des Sioux, MO -> Grafton, IL | 8.5 mi, 28.5 min, 1 crossing | 29.9 mi, 47.8 min | land |
| Port Townsend -> Oak Harbor, WA | 21.2 mi, 84.4 min, 1 crossing | 200.4 mi, 266.4 min | ferry |
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from generator import road_estimate, routing
from generator.routing import FerryPolicy, RoutedLeg

FIXTURES = Path(__file__).parent / "fixtures"

PORTAGE_DES_SIOUX = (38.9267, -90.3374)
GRAFTON = (38.9681, -90.4318)
PORT_TOWNSEND = (48.1170, -122.7604)
OAK_HARBOR = (48.2932, -122.6432)
HAVELOCK = (34.8791, -76.9013)
ORIENTAL = (35.0310, -76.6933)

DEFAULTS = FerryPolicy(wait_minutes_per_crossing=45, prefer_land_within_minutes=15)


def _fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


#: The reply OpenRouteService documents for a pair it cannot connect: HTTP 404,
#: error code 2009. Not recorded -- the four live calls went on the legs above.
NO_ROUTE = urllib.error.HTTPError(
    "https://api.openrouteservice.org/v2/directions/driving-car", 404, "Not Found", {},
    io.BytesIO(b'{"error":{"code":2009,"message":"Route could not be found"}}'))


class Transport:
    """Answers the ordinary request with `route` and the ferry-avoiding one
    with `land`; either may be an exception."""

    def __init__(self, route, land=None):
        self.route, self.land = route, land
        self.requests = []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data.decode())
        self.requests.append(body)
        avoiding = (body.get("options") or {}).get("avoid_features") == ["ferries"]
        answer = self.land if avoiding else self.route
        if isinstance(answer, BaseException):
            raise answer
        if answer is None:
            raise AssertionError(f"unexpected {'ferry-avoiding' if avoiding else 'ordinary'} request")
        return io.BytesIO(json.dumps(answer).encode())

    @property
    def avoiding(self):
        return [r for r in self.requests if "options" in r]


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "routes.json"


def _estimate(monkeypatch, cache, transport, origin, dest, **kwargs):
    monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
    kwargs.setdefault("ferry_policy", DEFAULTS)
    return road_estimate.leg_estimate(
        origin, dest,
        router=lambda a, b: routing.route_leg(a, b, key="k", cache_path=cache),
        land_router=lambda a, b: routing.route_leg(a, b, key="k", cache_path=cache,
                                                   avoid_ferries=True),
        **kwargs)


def _grafton(monkeypatch, cache, **kwargs):
    transport = Transport(_fixture("ors_portage_des_sioux_grafton"),
                          _fixture("ors_portage_des_sioux_grafton_avoid_ferries"))
    return _estimate(monkeypatch, cache, transport, PORTAGE_DES_SIOUX, GRAFTON, **kwargs), transport


def _port_townsend(monkeypatch, cache, **kwargs):
    transport = Transport(_fixture("ors_port_townsend_oak_harbor"),
                          _fixture("ors_port_townsend_oak_harbor_avoid_ferries"))
    return _estimate(monkeypatch, cache, transport, PORT_TOWNSEND, OAK_HARBOR, **kwargs), transport


def _same_route(leg, route):
    return (leg.miles, leg.minutes, leg.ferry_share, leg.geometry, leg.ferry_spans) == (
        route.miles, route.minutes, route.ferry_share, route.geometry, route.ferry_spans)


# ── The two recorded legs ────────────────────────────────────────────────────


def test_a_crossing_that_saves_little_goes_by_land(monkeypatch, cache):
    """Grafton ferry: 19 min faster as sailed, 26 min slower once a 45-minute
    wait is allowed for. Red with no allowance, and with strict fastest."""
    leg, transport = _grafton(monkeypatch, cache)
    assert leg.chosen == "land"
    assert (leg.ferry.miles, leg.ferry.minutes, leg.ferry.crossings) == (8.5, 28.5, 1)
    assert (leg.land.miles, leg.land.minutes, leg.land.crossings) == (29.9, 47.8, 0)
    reason = leg.chosen_reason
    assert (reason.rule, reason.crossings, reason.ferry_minutes, reason.ferry_minutes_with_wait,
            reason.land_minutes, reason.difference_minutes) == ("auto", 1, 28.5, 73.5, 47.8, -25.7)
    assert _same_route(leg, leg.land) and leg.routed and not leg.has_ferry
    assert leg.alternative is leg.ferry
    assert len(transport.requests) == 2
    assert "options" not in transport.requests[0]
    assert transport.requests[1]["options"] == {"avoid_features": ["ferries"]}
    assert transport.requests[1]["coordinates"] == transport.requests[0]["coordinates"]
    assert "so land" in reason.summary


def test_a_crossing_that_saves_hours_goes_by_ferry(monkeypatch, cache):
    """Port Townsend to Oak Harbor: the land route is round by Deception Pass."""
    leg, _ = _port_townsend(monkeypatch, cache)
    assert leg.chosen == "ferry"
    reason = leg.chosen_reason
    assert (reason.ferry_minutes, reason.ferry_minutes_with_wait, reason.land_minutes,
            reason.difference_minutes) == (84.4, 129.4, 266.4, 137.0)
    assert (leg.land.miles, leg.land.minutes) == (200.4, 266.4)
    assert _same_route(leg, leg.ferry) and leg.has_ferry and leg.geometry is not None
    assert leg.alternative is leg.land
    assert "so the ferry" in reason.summary


# ── The rule ─────────────────────────────────────────────────────────────────


def _ferry(minutes, crossings=1):
    spans = tuple((i * 10, i * 10 + 5) for i in range(crossings))
    geometry = tuple((40.0 + i * 0.01, -105.0) for i in range(crossings * 10 + 1))
    return RoutedLeg(miles=10.0, minutes=minutes, ferry_share=0.2, geometry=geometry,
                     ferry_spans=spans)


def _land(minutes):
    return RoutedLeg(miles=30.0, minutes=minutes)


def test_the_allowance_is_per_crossing():
    """Two crossings at 10 min sailing: 100 min with two allowances, which is
    only 10 faster than 110 by land. One allowance would say 55, and ferry.
    Red with the allowance applied once."""
    chosen, reason = routing.choose_ferry_or_land(_ferry(10, crossings=2), _land(110), policy=DEFAULTS)
    assert reason.crossings == 2 and reason.ferry_minutes_with_wait == 100.0
    assert (chosen, reason.difference_minutes) == ("land", 10.0)


def test_exactly_fifteen_faster_is_still_land():
    """Red with `>=` for `>`, and with strict fastest."""
    chosen, reason = routing.choose_ferry_or_land(_ferry(30), _land(90), policy=DEFAULTS)
    assert (chosen, reason.difference_minutes) == ("land", 15.0)
    chosen, reason = routing.choose_ferry_or_land(_ferry(30), _land(90.1), policy=DEFAULTS)
    assert (chosen, reason.difference_minutes) == ("ferry", 15.1)


def test_the_policy_numbers_are_the_policy():
    assert routing.choose_ferry_or_land(_ferry(30), _land(90), policy=FerryPolicy(0, 15))[0] == "ferry"
    assert routing.choose_ferry_or_land(_ferry(30), _land(90), policy=FerryPolicy(45, 30))[0] == "land"


def test_avoid_takes_land_and_prefer_takes_the_ferry(monkeypatch, cache):
    """Each override against the leg the rule would decide the other way.
    Red with the override ignored."""
    leg, _ = _port_townsend(monkeypatch, cache, ferry_preference="avoid")
    assert leg.chosen == "land" and leg.chosen_reason.rule == "avoid"
    assert _same_route(leg, leg.land) and leg.miles == 200.4

    leg, _ = _grafton(monkeypatch, cache, ferry_preference="prefer")
    assert leg.chosen == "ferry" and leg.chosen_reason.rule == "prefer"
    assert _same_route(leg, leg.ferry) and leg.minutes == 28.5
    assert (leg.land.minutes, leg.chosen_reason.difference_minutes) == (47.8, -25.7), (
        "both routes are kept whichever is chosen")


def test_an_unknown_preference_is_refused():
    with pytest.raises(ValueError):
        road_estimate.leg_estimate(PORT_TOWNSEND, OAK_HARBOR, router=lambda a, b: None,
                                   ferry_preference="never")


def test_no_land_route_means_the_ferry_and_says_so(monkeypatch, cache):
    """An island with no bridge: 404 on the ferry-avoiding request. Even
    `avoid` gets the ferry, because there is nothing else. The answer is
    remembered, so the next run does not ask again."""
    transport = Transport(_fixture("ors_port_townsend_oak_harbor"), NO_ROUTE)
    for preference in ("auto", "avoid"):
        leg = _estimate(monkeypatch, cache, transport, PORT_TOWNSEND, OAK_HARBOR,
                        ferry_preference=preference)
        assert leg.chosen == "ferry" and leg.land is None and leg.alternative is None
        reason = leg.chosen_reason
        assert (reason.rule, reason.land_minutes, reason.difference_minutes) == (
            "no_land_route", None, None)
        assert reason.ferry_minutes_with_wait == 129.4
        assert "no land route" in reason.summary
        assert _same_route(leg, leg.ferry)
    assert len(transport.requests) == 2, "one ordinary, one avoiding; the second run asked neither"
    key = routing._cache_key(PORT_TOWNSEND, OAK_HARBOR, avoid_ferries=True)
    assert json.loads(cache.read_text())[key] == {"no_route": True}


def test_a_refused_land_request_is_not_remembered(monkeypatch, cache):
    transport = Transport(_fixture("ors_port_townsend_oak_harbor"),
                          urllib.error.HTTPError("u", 429, "quota", {}, None))
    leg = _estimate(monkeypatch, cache, transport, PORT_TOWNSEND, OAK_HARBOR)
    assert leg.chosen == "ferry" and leg.land is None
    key = routing._cache_key(PORT_TOWNSEND, OAK_HARBOR, avoid_ferries=True)
    assert key not in json.loads(cache.read_text())


def test_a_land_reply_that_still_takes_a_ferry_is_not_a_land_route(monkeypatch, cache):
    transport = Transport(_fixture("ors_port_townsend_oak_harbor"),
                          _fixture("ors_port_townsend_oak_harbor"))
    leg = _estimate(monkeypatch, cache, transport, PORT_TOWNSEND, OAK_HARBOR)
    assert leg.land is None and leg.chosen_reason.rule == "no_land_route"


def test_a_leg_without_a_ferry_asks_once_and_chooses_nothing(monkeypatch, cache):
    """Havelock to Oriental, NC, recorded: routed round by New Bern, no ferry.
    Red with the alternative requested for every leg."""
    transport = Transport(_fixture("ors_havelock_oriental"))
    leg = _estimate(monkeypatch, cache, transport, HAVELOCK, ORIENTAL)
    assert (leg.miles, leg.minutes, leg.routed) == (47.0, 67.0, True)
    assert transport.avoiding == [] and len(transport.requests) == 1
    assert (leg.chosen, leg.chosen_reason, leg.ferry, leg.land, leg.alternative) == (
        None, None, None, None, None)


# ── The cache ────────────────────────────────────────────────────────────────


def test_both_routes_survive_the_cache(monkeypatch, cache):
    first, transport = _grafton(monkeypatch, cache)
    second = _estimate(monkeypatch, cache, transport, PORTAGE_DES_SIOUX, GRAFTON,
                       ferry_policy=DEFAULTS)
    assert len(transport.requests) == 2, "the second estimate was served from the cache"
    assert second == first and second.land.geometry is not None
    stored = json.loads(cache.read_text())
    assert set(stored) == {routing._cache_key(PORTAGE_DES_SIOUX, GRAFTON),
                           routing._cache_key(PORTAGE_DES_SIOUX, GRAFTON, avoid_ferries=True)}


def test_old_cache_entries_still_load(monkeypatch, cache):
    """Entries written before alternatives (and before geometry): a leg with no
    ferry is served without a request; a ferry leg counts one crossing and
    asks only for its land route."""
    cache.write_text(json.dumps({
        routing._cache_key(HAVELOCK, ORIENTAL): {"miles": 47.0, "minutes": 67.0, "ferry_share": 0.0},
        routing._cache_key(PORTAGE_DES_SIOUX, GRAFTON): {
            "miles": 8.5, "minutes": 28.5, "ferry_share": 0.0845},
    }), encoding="utf-8")
    transport = Transport(None, _fixture("ors_portage_des_sioux_grafton_avoid_ferries"))

    plain = _estimate(monkeypatch, cache, transport, HAVELOCK, ORIENTAL)
    assert plain.routed and plain.minutes == 67.0 and plain.chosen is None
    assert transport.requests == []

    ferry = _estimate(monkeypatch, cache, transport, PORTAGE_DES_SIOUX, GRAFTON)
    assert len(transport.requests) == 1 and transport.avoiding == transport.requests
    assert ferry.ferry.geometry is None and ferry.chosen_reason.crossings == 1
    assert ferry.chosen == "land" and ferry.minutes == 47.8


# ── The configuration ────────────────────────────────────────────────────────


def test_the_policy_comes_from_config():
    assert routing.ferry_policy_from_config({}) == FerryPolicy(45, 15)
    assert routing.ferry_policy_from_config(
        {"routing": {"ferry": {"wait_minutes_per_crossing": 30,
                               "prefer_land_within_minutes": 0}}}) == FerryPolicy(30, 0)
    assert routing.ferry_policy_from_config(
        {"routing": {"ferry": {"wait_minutes_per_crossing": "soon",
                               "prefer_land_within_minutes": -5}}}) == FerryPolicy(45, 15)
    assert routing.ferry_policy_from_config({"routing": "nonsense"}) == FerryPolicy(45, 15)


def test_the_shipped_config_carries_the_defaults(tmp_path):
    assert routing.configured_ferry_policy() == FerryPolicy(45, 15)
    other = tmp_path / "config.yaml"
    other.write_text("routing:\n  ferry:\n    wait_minutes_per_crossing: 20\n", encoding="utf-8")
    assert routing.configured_ferry_policy(other) == FerryPolicy(20, 15)
    assert routing.configured_ferry_policy(tmp_path / "missing.yaml") == FerryPolicy(45, 15)


def test_the_estimate_reads_the_configured_policy(monkeypatch):
    monkeypatch.setattr(routing, "configured_ferry_policy", lambda: FerryPolicy(0, 15))
    leg = road_estimate.leg_estimate(PORTAGE_DES_SIOUX, GRAFTON,
                                     router=lambda a, b: _ferry(28.5),
                                     land_router=lambda a, b: _land(47.8))
    assert leg.chosen == "ferry" and leg.chosen_reason.wait_minutes_per_crossing == 0
