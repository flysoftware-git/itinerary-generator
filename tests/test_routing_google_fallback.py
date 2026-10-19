"""A leg OpenRouteService refuses for its quota, and the second router.

When a free key's daily allowance is spent, every further leg in the run is
refused for the quota and used to become a straight line -- a page with a cold
cache came out with no roads on it. Such a leg is asked of Google Routes when
config.yaml allows it.

This file covers the quota -- the case the fallback was built for -- and the two
cases that still do not reach it. Every *other* way of not answering (a
timeout, a refused key, a per-minute give-up, an unparseable reply, an open
breaker, a remembered failure) reaches it as of 2026-10-18 and is covered in
test_a_router_that_refused_is_remembered.py, with the outage that made the case.

No network. OpenRouteService requests go through `urllib.request.urlopen` and
Google's through `routing._google_urlopen`, and each test replaces the one it
means to serve; `tests/conftest.py` makes the Google one refuse loudly for
every other test.
"""

from __future__ import annotations

import io
import json
import logging
import urllib.error

import pytest

from generator import maps_platform, road_estimate, routing

ISSAQUAH = (47.5301, -122.0326)
PORT_TOWNSEND = (48.1170, -122.7604)

#: Google's own example polyline: (38.5, -120.2), (40.7, -120.95),
#: (43.252, -126.453).
REFERENCE_POLYLINE = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"

#: A Compute Routes reply under `GOOGLE_ROUTES_FIELD_MASK`: 75.78 miles,
#: 151.2 minutes.
GOOGLE_REPLY = {"routes": [{"distanceMeters": 121956, "duration": "9070s",
                            "polyline": {"encodedPolyline": REFERENCE_POLYLINE}}]}


def _ors_reply(miles, seconds):
    return {"routes": [{"summary": {"distance": miles, "duration": seconds}}]}


def _refusal(status, body=b"", headers=None):
    return urllib.error.HTTPError("u", status, "refused", headers or {}, io.BytesIO(body))


def _quota_403():
    """What OpenRouteService answers once the key's daily quota is spent."""
    return _refusal(403, b'{"error": "Quota exceeded"}')


def _quota_429():
    """The same wall reported as a 429 whose reset is hours away."""
    return _refusal(429, headers={"Retry-After": "7200"})


class Sequence:
    """Answers in order; an exception in the list is raised. Records each
    request's JSON body and headers."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []
        self.headers = []

    def __call__(self, request, timeout=None):
        self.requests.append(json.loads(request.data.decode()))
        self.headers.append({k.lower(): v for k, v in request.header_items()})
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "routes.json"


@pytest.fixture
def google(monkeypatch):
    """Google's transport, answering with one recorded reply."""
    transport = Sequence(GOOGLE_REPLY)
    monkeypatch.setattr(routing, "_google_urlopen", transport)
    return transport


@pytest.mark.parametrize("refusal", [_quota_403, _quota_429], ids=["403-quota", "429-daily"])
def test_a_quota_refusal_is_routed_by_google_and_says_so(monkeypatch, cache, google, refusal):
    """Red with the fallback call removed from route_leg's quota branch: the
    leg came back None, a straight line."""
    monkeypatch.setattr(routing, "_wall_clock", lambda: 1_700_000_000.0)
    monkeypatch.setattr(routing.urllib.request, "urlopen", Sequence(refusal()))

    leg = routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache,
                            google_key="g")

    assert leg is not None, "a quota refusal became a straight line"
    assert leg.router == routing.ROUTER_GOOGLE == "google_routes"
    assert (leg.miles, leg.minutes) == (75.8, 151.2)
    assert leg.geometry == ((38.5, -120.2), (40.7, -120.95), (43.252, -126.453))
    assert leg.ferry_share == 0.0 and leg.ferry_spans == ()

    sent, headers = google.requests[0], google.headers[0]
    assert sent["origin"]["location"]["latLng"] == {"latitude": ISSAQUAH[0],
                                                    "longitude": ISSAQUAH[1]}
    assert sent["destination"]["location"]["latLng"] == {"latitude": PORT_TOWNSEND[0],
                                                         "longitude": PORT_TOWNSEND[1]}
    assert sent["travelMode"] == "DRIVE"
    assert "routingPreference" not in sent, "a traffic-aware ask bills at a dearer SKU"
    assert headers["x-goog-fieldmask"] == (
        "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline")
    assert headers["x-goog-api-key"] == "g"

    counts = routing.stats()
    assert counts["google_requests"] == 1 and counts["google_routes"] == 1
    assert counts["quota_refused"] == 0, "a leg drawn as a road was counted as a straight line"


def test_a_google_leg_is_not_written_to_the_route_cache(monkeypatch, cache, google):
    """Google's terms grant no storage for distance or duration. Red with the
    Google leg written to `cache_path` beside the OpenRouteService ones."""
    monkeypatch.setattr(routing.urllib.request, "urlopen", Sequence(_quota_403()))
    routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache, google_key="g")

    # The file is no longer empty -- OpenRouteService's refusal is remembered
    # there, dated -- so what is pinned is that nothing Google said is in it.
    entries = json.loads(cache.read_text()) if cache.exists() else {}
    for key, entry in entries.items():
        assert entry.get("unavailable") is True, f"{key} holds an answer: {entry}"
        assert not {"miles", "minutes", "geometry"} & set(entry), (
            f"{key} holds what Google said")


def test_a_google_leg_is_held_for_the_run_and_drawn_from_memory(monkeypatch, cache, google):
    """Asked again in the same run, neither router is asked again, and the map's
    cache-only read draws the road rather than a straight line."""
    ors = Sequence(_quota_403())
    monkeypatch.setattr(routing.urllib.request, "urlopen", ors)
    first = routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache,
                              google_key="g")
    again = routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache,
                              google_key="g")
    assert again == first and len(ors.requests) == 1 and len(google.requests) == 1
    assert routing.cached_leg(ISSAQUAH, PORT_TOWNSEND, cache_path=cache) == first


def test_a_new_run_forgets_what_google_answered(monkeypatch, cache):
    """Held for the run that asked, not for the life of the process. Red with
    `forget_google_routes` a no-op: the second run was served from memory."""
    google = Sequence(GOOGLE_REPLY, GOOGLE_REPLY)
    monkeypatch.setattr(routing, "_google_urlopen", google)
    monkeypatch.setattr(routing.urllib.request, "urlopen",
                        Sequence(_quota_403(), _quota_403()))
    routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache, google_key="g")
    routing.forget_google_routes()
    assert routing.cached_leg(ISSAQUAH, PORT_TOWNSEND, cache_path=cache) is None
    routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache, google_key="g")
    assert len(google.requests) == 2


def test_the_leg_estimate_says_which_router_answered(monkeypatch, cache, google):
    monkeypatch.setattr(routing, "DEFAULT_CACHE_PATH", str(cache))
    monkeypatch.setattr(routing.urllib.request, "urlopen", Sequence(_quota_403()))
    monkeypatch.setattr(routing, "api_key", lambda: "k")
    monkeypatch.setattr(routing, "google_routes_key", lambda config_path=None: "g")
    leg = road_estimate.leg_estimate(ISSAQUAH, PORT_TOWNSEND)
    assert leg.routed and leg.router == "google_routes" and leg.miles == 75.8


def test_an_openrouteservice_leg_says_so_too(monkeypatch, cache):
    monkeypatch.setattr(routing.urllib.request, "urlopen",
                        Sequence(_ors_reply(75.781, 9069.9)))
    leg = routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache, google_key="g")
    assert leg.router == "openrouteservice"
    assert routing.stats()["google_requests"] == 0


# ── Off means exactly what it meant before ────────────────────────────────────


def _config(tmp_path, *, gate, switch):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"maps_platform:\n  enabled: {str(gate).lower()}\n"
        f"routing:\n  google_routes_fallback:\n    enabled: {str(switch).lower()}\n",
        encoding="utf-8")
    return path


@pytest.mark.parametrize("gate, switch, key", [
    (True, False, "funded-key"),
    (False, True, "funded-key"),
    (True, True, ""),
], ids=["switch-off", "maps-platform-gate-off", "no-key"])
def test_with_the_fallback_off_a_quota_refusal_is_a_straight_line(
        monkeypatch, tmp_path, cache, gate, switch, key):
    """Red with `google_routes_key` reading only the Maps Platform gate
    (switch-off) and with it reading the environment directly
    (maps-platform-gate-off): Google was asked."""
    monkeypatch.setattr(routing, "GOOGLE_ROUTES_CONFIG_PATH",
                        str(_config(tmp_path, gate=gate, switch=switch)))
    for var in maps_platform.API_KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    if key:
        monkeypatch.setenv(maps_platform.API_KEY_ENV_VARS[0], key)
    google = Sequence(GOOGLE_REPLY)
    monkeypatch.setattr(routing, "_google_urlopen", google)
    monkeypatch.setattr(routing.urllib.request, "urlopen", Sequence(_quota_403()))

    assert routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache) is None
    assert google.requests == [], "Google was asked with the fallback off"
    counts = routing.stats()
    assert counts["quota_refused"] == 1 and counts["google_requests"] == 0


def test_both_switches_and_a_key_turn_it_on(monkeypatch, tmp_path, cache):
    """The control for the test above: the same file with both switches on
    does reach Google, so the off cases are off for the reason they name."""
    monkeypatch.setattr(routing, "GOOGLE_ROUTES_CONFIG_PATH",
                        str(_config(tmp_path, gate=True, switch=True)))
    monkeypatch.setenv(maps_platform.API_KEY_ENV_VARS[0], "funded-key")
    google = Sequence(GOOGLE_REPLY)
    monkeypatch.setattr(routing, "_google_urlopen", google)
    monkeypatch.setattr(routing.urllib.request, "urlopen", Sequence(_quota_403()))
    leg = routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache)
    assert leg is not None and leg.router == "google_routes"
    assert google.headers[0]["x-goog-api-key"] == "funded-key"


def test_the_shipped_config_leaves_the_fallback_off(monkeypatch):
    """A key in the environment and the repository's own config.yaml: off.
    Red with `routing.google_routes_fallback.enabled` set true."""
    monkeypatch.setenv(maps_platform.API_KEY_ENV_VARS[0], "funded-key")
    assert routing.google_routes_enabled(routing.DEFAULT_CONFIG_PATH) is False
    assert routing.google_routes_key(routing.DEFAULT_CONFIG_PATH) == ""


def test_an_explicitly_empty_google_key_is_off(monkeypatch, cache):
    google = Sequence(GOOGLE_REPLY)
    monkeypatch.setattr(routing, "_google_urlopen", google)
    monkeypatch.setattr(routing.urllib.request, "urlopen", Sequence(_quota_403()))
    assert routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache,
                             google_key="") is None
    assert google.requests == []


# ── What still does not reach the second router ──────────────────────────────


@pytest.mark.parametrize("refusals, avoid_ferries", [
    ([_refusal(404, b'{"error": {"code": 2009, "message": "Route could not be found"}}')], False),
    ([_refusal(404, b'{"error": {"code": 2009}}')], True),
    ([_quota_403()], True),
], ids=["no-route-404", "no-land-route", "ferry-avoiding-ask-on-quota"])
def test_an_answer_and_the_ferry_ask_never_reach_google(
        monkeypatch, cache, refusals, avoid_ferries):
    """The two cases that are not *the router did not answer*.

    A 404 is the router answering: it looked, and there is no route. That is a
    fact about the map, so paying a metered router to be told it again buys
    nothing. And the ferry-avoiding ask cannot be put to Google at all -- it is
    not asked to avoid ferries and the field mask would not say whether it did
    -- so an answer from it would not be the land route that was wanted.

    Red with the gate widened to every failure without these two exceptions.
    A refused key, a per-minute give-up and a timeout DO reach Google now; see
    test_a_router_that_refused_is_remembered.py.
    """
    google = Sequence(GOOGLE_REPLY)
    monkeypatch.setattr(routing, "_google_urlopen", google)
    monkeypatch.setattr(routing.urllib.request, "urlopen", Sequence(*refusals))
    assert routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache,
                             avoid_ferries=avoid_ferries, google_key="g") is None
    assert google.requests == [], "a refusal that is not the quota was sent to Google"


@pytest.mark.parametrize("answer", [
    {},
    {"routes": []},
    _refusal(403, b'{"error": {"status": "PERMISSION_DENIED"}}'),
    urllib.error.URLError("down"),
], ids=["empty-reply", "no-routes", "google-refused", "google-down"])
def test_when_google_cannot_answer_the_leg_is_estimated_as_before(monkeypatch, cache, answer):
    monkeypatch.setattr(routing, "_google_urlopen", Sequence(answer))
    monkeypatch.setattr(routing.urllib.request, "urlopen", Sequence(_quota_403()))
    assert routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache,
                             google_key="g") is None
    counts = routing.stats()
    assert counts["quota_refused"] == 1 and counts["google_requests"] == 1
    assert counts["google_routes"] == 0


@pytest.mark.parametrize("profile, mode", [
    ("driving-car", "DRIVE"), ("driving-hgv", "DRIVE"),
    ("cycling-regular", "BICYCLE"), ("cycling-mountain", "BICYCLE"),
    ("foot-walking", "WALK"), ("foot-hiking", "WALK"),
])
def test_the_leg_is_asked_in_the_mode_it_is_taken(monkeypatch, cache, google, profile, mode):
    monkeypatch.setattr(routing.urllib.request, "urlopen", Sequence(_quota_403()))
    routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache,
                      profile=profile, google_key="g")
    assert google.requests[0]["travelMode"] == mode


def test_every_profile_has_a_google_mode():
    assert set(routing.GOOGLE_TRAVEL_MODES) == set(routing.PROFILES)


# ── The quota refusal is recognised, and the log says so ─────────────────────


def test_the_quota_body_is_recognised_as_quota():
    assert routing._is_quota_message(_quota_403()) is True
    assert routing._is_quota_message(
        _refusal(403, b'{"error": "Access to this API has been disallowed"}')) is False


def test_a_quota_refusal_is_logged_as_the_quota(monkeypatch, cache, caplog):
    """The log read `Routing refused <leg> (HTTP 403)` for a spent quota and for
    a refused key alike, so a reader could not tell the wall from a fault. Red
    against that line."""
    monkeypatch.setattr(routing.urllib.request, "urlopen", Sequence(_quota_403()))
    with caplog.at_level(logging.INFO, logger="generator.routing"):
        assert routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache,
                                 google_key="") is None
    said = [r.getMessage() for r in caplog.records]
    assert any("quota" in line.lower() and "HTTP 403" in line for line in said), said
    assert not any(line.startswith("Routing refused") for line in said), said
    assert routing.stats()["quota_refused"] == 1
