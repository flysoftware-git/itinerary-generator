"""A router that did not answer is written down, and the ask is redirected.

WHAT IT COST
------------
2026-10-18. The router host was taking about 27 seconds to answer a driving
call against a `TIMEOUT_S` of 15, so every leg timed out and nine legs made a
~140 second render. Two things made that worse than it had to be, and PR #229
fixed neither -- it said so in its own description:

1. **Nothing was remembered.** The cache held successes and exactly one kind
   of nothing, `{"no_route": true}`, for an island. A timeout, a spent quota, a
   per-minute give-up and a no-route 404 each returned None and wrote nothing,
   so the next run paid the same `TIMEOUT_S` per leg all over again. The
   breaker added in #229 cannot help with that: it lives in one process, and
   the next run is another process. Measured on the live cache that day: 533
   entries, every one a success, not one negative.

2. **The second router could not be reached.** A leg the quota refused was
   asked of Google Routes, which is the one mechanism in this module for *the
   roads have gone off this page*. It sat inside `except
   urllib.error.HTTPError`, gated on a quota outcome. A socket timeout is a
   `URLError` and not an `HTTPError`, so the mechanism built for a page with no
   roads on it could not fire for the outage that took the roads off the page.

And one defect #229 introduced, which the tests of the day could not see
because every one of them was either source inspection or a direct call to
`_breaker_record`: `_count("breaker_open")` with no such name in `STAT_NAMES`.
`_stats` is a plain dict, so the first leg asked with the breaker open raised
KeyError out of `route_leg` into whatever asked for a leg. The breaker turned a
slow render into a crashed one.

WHAT IS PINNED
--------------
That a failure is remembered, that it expires, that an undateable memo is not
trusted, that an island's permanent memo stays permanent -- and that a timeout
now reaches the second router. Plus the cheap guard for the KeyError: every
name `_count` is called with exists.
"""

from __future__ import annotations

import io
import json
import re
import urllib.error

import pytest

from generator import routing

ISSAQUAH = (47.5301, -122.0326)
PORT_TOWNSEND = (48.1170, -122.7604)

GOOGLE_REPLY = {"routes": [{"distanceMeters": 121956, "duration": "9070s",
                            "polyline": {"encodedPolyline": "_p~iF~ps|U_ulLnnqC"}}]}


def _ors_reply(miles, seconds):
    return {"routes": [{"summary": {"distance": miles, "duration": seconds}}]}


def _refusal(status, body=b"", headers=None):
    return urllib.error.HTTPError("u", status, "refused", headers or {}, io.BytesIO(body))


class Sequence:
    """Answers in order; an exception in the list is raised."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(json.loads(request.data.decode()))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "routes.json"


@pytest.fixture
def google(monkeypatch):
    transport = Sequence(GOOGLE_REPLY)
    monkeypatch.setattr(routing, "_google_urlopen", transport)
    return transport


def _entry(cache):
    """The single cache entry, which these tests always arrange to be one."""
    entries = json.loads(cache.read_text())
    assert len(entries) == 1, entries
    return next(iter(entries.values()))


# ── The memo ─────────────────────────────────────────────────────────────────


class TestAFailureIsWrittenDown:
    def test_a_timeout_is_remembered_with_its_reason_and_its_date(
            self, monkeypatch, cache):
        monkeypatch.setattr(routing.urllib.request, "urlopen",
                            Sequence(urllib.error.URLError("timed out")))

        assert routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k",
                                 cache_path=cache) is None

        entry = _entry(cache)
        assert entry["unavailable"] is True
        assert entry["reason"] == "unavailable"
        assert entry["at"] > 0

    @pytest.mark.parametrize("refusal, reason", [
        (_refusal(403, b'{"error": "Quota exceeded"}'), "quota"),
        (_refusal(403, b'{"error": "Access to this API has been disallowed"}'), "http_403"),
        (_refusal(404, b'{"error": {"code": 2009}}'), "http_404"),
    ], ids=["quota", "refused-key", "no-route"])
    def test_a_refusal_says_which_refusal_it_was(self, monkeypatch, cache,
                                                 refusal, reason):
        """The reason is for whoever opens the cache file. A `quota` and a
        `http_403` call for different things from a person -- wait a day, or go
        and look at the key -- and a bare negative tells them neither."""
        monkeypatch.setattr(routing.urllib.request, "urlopen", Sequence(refusal))

        routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache,
                          google_key="")

        assert _entry(cache)["reason"] == reason

    def test_an_unparseable_reply_is_remembered_too(self, monkeypatch, cache):
        monkeypatch.setattr(routing.urllib.request, "urlopen",
                            Sequence({"routes": []}))

        routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache)

        assert _entry(cache)["reason"] == "unparseable"

    def test_the_memo_is_not_asked_again_while_it_stands(self, monkeypatch, cache):
        """The whole point. Red before this change: the second call asked the
        dead router again, and waited `TIMEOUT_S` to be told the same thing."""
        transport = Sequence(urllib.error.URLError("timed out"),
                             _ors_reply(75.781, 9069.9))
        monkeypatch.setattr(routing.urllib.request, "urlopen", transport)

        assert routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k",
                                 cache_path=cache) is None
        assert routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k",
                                 cache_path=cache) is None

        assert len(transport.requests) == 1, "the remembered failure was asked again"
        assert routing.stats()["unavailable_cached"] == 1

    def test_a_remembered_failure_is_not_counted_as_a_cache_hit(
            self, monkeypatch, cache):
        """`cache_hits` are answers. A leg nobody could answer is a different
        thing to see in a report, and reading one as the other would make an
        outage look like a well-warmed cache."""
        monkeypatch.setattr(routing.urllib.request, "urlopen",
                            Sequence(urllib.error.URLError("timed out")))
        routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache)
        before = routing.stats()["cache_hits"]

        routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache)

        assert routing.stats()["cache_hits"] == before


class TestItStopsStanding:
    def test_an_expired_memo_sends_the_leg_back_to_the_router(
            self, monkeypatch, cache):
        """A refusal is a fact about the router's afternoon, not about the
        world. Red with the memo written without an expiry, which would shut a
        recovered router out of a leg for good."""
        transport = Sequence(urllib.error.URLError("timed out"),
                             _ors_reply(75.781, 9069.9))
        monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
        routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache)
        monkeypatch.setattr(routing, "ROUTING_UNAVAILABLE_TTL_S", 0.0)

        leg = routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache)

        assert leg is not None and leg.miles == 75.8
        assert len(transport.requests) == 2

    def test_a_recovered_router_overwrites_the_memo(self, monkeypatch, cache):
        """The negative must not survive beside the answer: a half-updated
        entry read by `_leg_from_cache` is the one way this could poison a leg
        permanently."""
        transport = Sequence(urllib.error.URLError("timed out"),
                             _ors_reply(75.781, 9069.9))
        monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
        routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache)
        monkeypatch.setattr(routing, "ROUTING_UNAVAILABLE_TTL_S", 0.0)
        routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache)

        entry = _entry(cache)

        assert "unavailable" not in entry and entry["miles"] == 75.8

    @pytest.mark.parametrize("entry", [
        {"unavailable": True, "reason": "timeout"},
        {"unavailable": True, "reason": "timeout", "at": "yesterday"},
        {"unavailable": True, "reason": "timeout", "at": None},
    ], ids=["no-date", "unreadable-date", "null-date"])
    def test_a_memo_nobody_can_date_is_not_trusted(self, monkeypatch, cache, entry):
        """It reads as expired, not as fresh. A negative nobody can date should
        send the caller back to the router rather than silence that leg
        forever -- including one written by a version of this that did not date
        them."""
        key = routing._cache_key(ISSAQUAH, PORT_TOWNSEND, avoid_ferries=False,
                                 profile=routing.DEFAULT_PROFILE)
        cache.write_text(json.dumps({key: entry}))
        transport = Sequence(_ors_reply(75.781, 9069.9))
        monkeypatch.setattr(routing.urllib.request, "urlopen", transport)

        leg = routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache)

        assert leg is not None and len(transport.requests) == 1

    def test_the_ttl_is_longer_than_a_render_and_shorter_than_an_hour(self):
        """Both ends matter. Shorter than a render and a single build asks the
        dead router twice; longer than a person's patience and a page they
        rebuild after fixing the router still comes out with straight lines."""
        assert 600 <= routing.ROUTING_UNAVAILABLE_TTL_S <= 3600


class TestAnIslandIsStillPermanent:
    def test_no_route_is_written_without_a_date(self, monkeypatch, cache):
        """The two kinds of nothing must not collapse into one. An island does
        not grow a bridge between runs, so that memo has no expiry -- and if it
        picked one up, every build would re-ask it and spend quota being told
        about the same island."""
        monkeypatch.setattr(routing.urllib.request, "urlopen",
                            Sequence(_refusal(404, b'{"error": {"code": 2009}}')))

        assert routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache,
                                 avoid_ferries=True) is None

        assert _entry(cache) == {"no_route": True}

    def test_a_permanent_memo_is_honoured_however_old_it_is(self, monkeypatch, cache):
        key = routing._cache_key(ISSAQUAH, PORT_TOWNSEND, avoid_ferries=True,
                                 profile=routing.DEFAULT_PROFILE)
        cache.write_text(json.dumps({key: {"no_route": True}}))
        monkeypatch.setattr(routing, "ROUTING_UNAVAILABLE_TTL_S", 0.0)
        transport = Sequence(_ors_reply(1.0, 1.0))
        monkeypatch.setattr(routing.urllib.request, "urlopen", transport)

        assert routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache,
                                 avoid_ferries=True) is None
        assert transport.requests == []


# ── The second router, reachable at last ─────────────────────────────────────


class TestTheFallbackCanReachTheOutage:
    def test_a_timeout_is_routed_by_google(self, monkeypatch, cache, google):
        """The case the fallback existed for and could never serve: a socket
        timeout is a `URLError`, and the call sat inside `except HTTPError`."""
        monkeypatch.setattr(routing.urllib.request, "urlopen",
                            Sequence(urllib.error.URLError("timed out")))

        leg = routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache,
                                google_key="g")

        assert leg is not None and leg.router == routing.ROUTER_GOOGLE
        assert len(google.requests) == 1
        assert routing.stats()["failed"] == 0, (
            "a leg drawn as a road was counted as a straight line")

    @pytest.mark.parametrize("failure", [
        urllib.error.URLError("timed out"),
        _refusal(403, b'{"error": "Access to this API has been disallowed"}'),
        {"routes": []},
    ], ids=["timeout", "refused-key", "unparseable"])
    def test_every_way_of_not_answering_reaches_it(self, monkeypatch, cache,
                                                   google, failure):
        monkeypatch.setattr(routing.urllib.request, "urlopen", Sequence(failure))

        leg = routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache,
                                google_key="g")

        assert leg is not None and len(google.requests) == 1

    def test_a_remembered_failure_still_goes_to_the_second_router(
            self, monkeypatch, cache, google):
        """Otherwise the memo would be a regression for the case it is best at:
        a spent quota, where Google used to rescue every leg of every run and
        would now rescue none of them for twenty minutes."""
        monkeypatch.setattr(routing.urllib.request, "urlopen",
                            Sequence(urllib.error.URLError("timed out")))
        routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache,
                          google_key="")
        google.answers = [GOOGLE_REPLY]

        leg = routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache,
                                google_key="g")

        assert leg is not None and leg.router == routing.ROUTER_GOOGLE

    def test_the_failure_is_recorded_against_the_breaker_even_when_rescued(
            self, monkeypatch, cache):
        """Red before this change: the rescue returned before
        `_breaker_record`, so a dead primary was asked again -- at `TIMEOUT_S`
        each -- for every remaining leg of a trip Google was carrying.

        Shown with the quota refusal, because that is the one failure the old
        code did route to Google, and so the only one where it could skip the
        record.
        """
        monkeypatch.setattr(routing, "_google_urlopen",
                            Sequence(*[GOOGLE_REPLY] * 4))
        monkeypatch.setattr(
            routing.urllib.request, "urlopen",
            Sequence(*[_refusal(403, b'{"error": "Quota exceeded"}')
                       for _ in range(routing.ROUTING_BREAKER_THRESHOLD)]))

        for i in range(routing.ROUTING_BREAKER_THRESHOLD):
            routing.route_leg((47.0 + i, -122.0), PORT_TOWNSEND, key="k",
                              cache_path=cache, google_key="g")

        assert routing.circuit_open() is True


class TestAnOpenBreakerRedirectsTheAsk:
    def test_an_open_breaker_does_not_raise(self, monkeypatch, cache):
        """The defect #229 shipped. `_count("breaker_open")` with no such name
        in `STAT_NAMES` raised KeyError out of `route_leg` into whatever asked
        for a leg, so the breaker turned a slow render into a crashed one.

        Every breaker test of that day was source inspection or a direct call
        to `_breaker_record`; not one drove `route_leg` with the breaker open,
        which is why nothing saw it.
        """
        transport = Sequence()
        monkeypatch.setattr(routing.urllib.request, "urlopen", transport)
        for _ in range(routing.ROUTING_BREAKER_THRESHOLD):
            routing._breaker_record(failed=True)

        assert routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k",
                                 cache_path=cache, google_key="") is None

        assert transport.requests == []
        assert routing.stats()["breaker_open"] == 1

    def test_an_open_breaker_still_asks_the_second_router(self, monkeypatch,
                                                         cache, google):
        """The breaker is about the router that stopped answering, not about
        the leg. A second router is untouched by the first one's outage, and a
        road beats a straight line."""
        monkeypatch.setattr(routing.urllib.request, "urlopen", Sequence())
        for _ in range(routing.ROUTING_BREAKER_THRESHOLD):
            routing._breaker_record(failed=True)

        leg = routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache,
                                google_key="g")

        assert leg is not None and leg.router == routing.ROUTER_GOOGLE

    def test_an_open_breaker_serves_a_cached_answer_without_asking_anyone(
            self, monkeypatch, cache, google):
        """Pinned here as well as in the breaker's own file, because the second
        router gives a new way to get this wrong: a cached leg must not be
        paid for twice."""
        monkeypatch.setattr(routing.urllib.request,
                            "urlopen", Sequence(_ors_reply(75.781, 9069.9)))
        first = routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache)
        for _ in range(routing.ROUTING_BREAKER_THRESHOLD):
            routing._breaker_record(failed=True)

        again = routing.route_leg(ISSAQUAH, PORT_TOWNSEND, key="k", cache_path=cache,
                                  google_key="g")

        assert again == first and google.requests == []


# ── The guard for the KeyError, rather than one more fix for it ──────────────


def test_every_counted_name_exists():
    """A `_count` call with a name not in `STAT_NAMES` raises KeyError out of
    `route_leg`. That is how the breaker's own counter shipped broken in #229,
    and it will be how the next one does unless something reads the calls.

    `_stats` is left a plain dict deliberately: a defaultdict would make a
    typo'd name silently uncounted, which is a quiet wrong report instead of a
    loud wrong crash. This test is the third option.
    """
    import inspect

    source = inspect.getsource(routing)
    counted = set(re.findall(r'_count\(\s*"([a-z_]+)"', source))

    assert counted, "no literal _count calls found; the pattern needs updating"
    assert counted <= set(routing.STAT_NAMES), (
        f"counted but not declared: {sorted(counted - set(routing.STAT_NAMES))}")


def test_the_counters_a_run_reports_are_all_declared():
    """The same guard from the other end: `stats()` must answer for every name,
    so a report cannot ask for one that is missing."""
    assert set(routing.stats()) == set(routing.STAT_NAMES)
