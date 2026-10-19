"""Routing stops asking a router that has stopped answering.

WHAT IT COST
------------
2026-10-18: a trip page answered Cloudflare 524 twice, at 125.2s and 125.1s.
Every stack sample sat in `_send` -> `urlopen`. The router host was taking about
27 seconds to answer a driving call against a `TIMEOUT_S` of 15, so every leg
timed out, and nine legs made a ~140 second render.

The timeout was working. What was missing is the thing every other outbound
dependency in this package already had: `llm_client`, `grok_search`,
`claude_search` and `openai_search` all carry a circuit breaker and `routing`
carried none -- measured as 0 mentions of circuit/breaker against 49, 86, 76 and
68. So after the first two timeouts the remaining legs were known hopeless and
were asked anyway at full price each.

WHAT IS PINNED
--------------
That repeated failures stop the asking, that a success clears it, and -- the
part that is easy to get wrong -- that the two kinds of "no route" are ANSWERS
rather than failures. A router replying "there is no land route here" is working
perfectly, and counting that against the breaker would trip it on an island.
"""

from __future__ import annotations

import urllib.error

import pytest

from generator import routing


@pytest.fixture(autouse=True)
def _clean_breaker():
    """Breaker state is module-level, so it leaks between tests without this."""
    routing.reset_breaker()
    yield
    routing.reset_breaker()


class TestItOpensOnRepeatedFailure:
    def test_it_starts_closed(self):
        assert routing.circuit_open() is False

    def test_one_failure_is_not_enough(self):
        routing._breaker_record(failed=True)

        assert routing.circuit_open() is False

    def test_the_threshold_opens_it(self):
        for _ in range(routing.ROUTING_BREAKER_THRESHOLD):
            routing._breaker_record(failed=True)

        assert routing.circuit_open() is True

    def test_the_threshold_is_lower_than_the_llm_breakers(self):
        """A build routes many legs in a burst, so being wrong costs one extra
        estimated leg while being slow is multiplied by every leg left."""
        from generator.llm_client import _DEFAULT_LLM_CIRCUIT_BREAKER_THRESHOLD

        assert routing.ROUTING_BREAKER_THRESHOLD < _DEFAULT_LLM_CIRCUIT_BREAKER_THRESHOLD


class TestASuccessClosesIt:
    def test_a_success_clears_the_count(self):
        routing._breaker_record(failed=True)
        routing._breaker_record(failed=False)
        routing._breaker_record(failed=True)

        assert routing.circuit_open() is False, "the count should have restarted"

    def test_a_success_closes_an_open_breaker(self):
        for _ in range(routing.ROUTING_BREAKER_THRESHOLD):
            routing._breaker_record(failed=True)
        assert routing.circuit_open() is True

        routing._breaker_record(failed=False)

        assert routing.circuit_open() is False


class TestNoRouteIsAnAnswerNotAFailure:
    """The case that would make this worse than nothing.

    A router that replies "no land route" is working. If that counted as a
    failure, a trip with two islands would trip the breaker and every remaining
    leg of a perfectly healthy router would be estimated.
    """

    def test_the_source_records_a_success_for_a_no_land_route(self):
        import inspect

        src = inspect.getsource(routing.route_leg)
        after_404 = src.split("the ferry is the only way", 1)[1][:400]

        assert "_breaker_record(failed=False)" in after_404

    def test_the_source_records_a_success_for_a_ferry_crossing_reply(self):
        import inspect

        src = inspect.getsource(routing.route_leg)
        after = src.split("still takes a ferry", 1)[1][:400]

        assert "_breaker_record(failed=False)" in after


class TestTheTimeoutPathCountsAgainstIt:
    """The branch the outage actually took. A socket timeout is a URLError, not
    an HTTPError -- which is also why the Google fallback could never rescue
    it."""

    def test_a_url_error_is_a_failure(self):
        import inspect

        src = inspect.getsource(routing.route_leg)
        handler = src.split("except (urllib.error.URLError", 1)[1][:900]

        assert "_breaker_record(failed=True)" in handler

    def test_a_timeout_is_not_an_http_error(self):
        """Stated as a test because it is the reason the existing fallback,
        gated inside `except urllib.error.HTTPError`, never fired for this."""
        assert not issubclass(urllib.error.URLError, urllib.error.HTTPError)
        assert issubclass(urllib.error.HTTPError, urllib.error.URLError)


class TestAnOpenBreakerStillServesWhatIsKnown:
    def test_the_check_sits_after_the_cache(self):
        """A cached answer is free, and withholding it would make an open
        breaker worse than the outage it prevents. The breaker is about not
        ASKING, not about forgetting."""
        import inspect

        src = inspect.getsource(routing.route_leg)

        assert src.index("_load_cache(path).get(cache_key)") < src.index("circuit_open()")
        assert src.index("circuit_open()") < src.index("request_body")


class TestItReportsItself:
    def test_the_state_is_readable(self):
        state = routing.breaker_state()

        assert set(state) == {"open", "seconds_remaining",
                              "consecutive_failures", "trips"}

    def test_a_trip_is_counted_once_not_per_leg(self):
        for _ in range(routing.ROUTING_BREAKER_THRESHOLD * 4):
            routing._breaker_record(failed=True)

        assert routing.breaker_state()["trips"] == 1

    def test_it_can_say_it_never_tripped(self):
        """A run where routing was skipped looks, in the output, exactly like a
        run where every leg happened to be straight -- so the report has to be
        able to state the zero."""
        state = routing.breaker_state()

        assert state["trips"] == 0 and state["open"] is False
