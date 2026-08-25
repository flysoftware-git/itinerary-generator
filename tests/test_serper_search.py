"""Tests for SerperSearch — the per-item fallback candidate provider.

Chosen on measured evidence (docs/design/cost-accounting-and-reduction.md
sections 8.6/8.7): against the 55 items run 7's paid LLM fallback handled,
Serper returned a result for 55/55 and 53 of those (96%) passed
_retain_discovered_url unmodified, versus the LLM path's 52/55 coverage and
far weaker sources (28 official .gov hits against 2).
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from generator.serper_search import SerperSearch, _MAX_FREE_TIER_RESULTS


def _client(**kw):
    with patch.dict("os.environ", {"SERPER_API_KEY": "test-key"}):
        return SerperSearch(**kw)


def _response(status=200, organic=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"organic": organic if organic is not None else []}
    return r


class TestSearchContract:
    """Must match GrokSearch.search exactly: {name, snippet, url}, never raises."""

    def test_returns_normalised_rows(self):
        c = _client()
        with patch.object(c._session, "post", return_value=_response(organic=[
            {"title": "Inspiration Point", "snippet": "A viewpoint.",
             "link": "https://www.nps.gov/brca/planyourvisit/inspiration.htm"},
        ])):
            rows = c.search("Inspiration Point Bryce Canyon")
        assert rows == [{
            "name": "Inspiration Point",
            "snippet": "A viewpoint.",
            "url": "https://www.nps.gov/brca/planyourvisit/inspiration.htm",
        }]

    def test_entries_without_a_link_are_dropped(self):
        c = _client()
        with patch.object(c._session, "post", return_value=_response(organic=[
            {"title": "No link here"}, {"title": "Good", "link": "https://example.gov/x"},
        ])):
            rows = c.search("something")
        assert [r["url"] for r in rows] == ["https://example.gov/x"]

    @pytest.mark.parametrize("failure", [
        requests.RequestException("boom"),
    ])
    def test_network_failure_returns_empty_not_raise(self, failure):
        c = _client()
        with patch.object(c._session, "post", side_effect=failure):
            assert c.search("anything") == []

    def test_http_error_returns_empty(self):
        c = _client()
        with patch.object(c._session, "post", return_value=_response(status=429)):
            assert c.search("anything") == []

    def test_unparseable_body_returns_empty(self):
        c = _client()
        bad = MagicMock(); bad.status_code = 200; bad.json.side_effect = ValueError("nope")
        with patch.object(c._session, "post", return_value=bad):
            assert c.search("anything") == []

    def test_no_api_key_returns_empty_without_calling(self):
        with patch.dict("os.environ", {}, clear=True):
            c = SerperSearch()
        with patch.object(c._session, "post") as post:
            assert c.search("anything") == []
        post.assert_not_called()


class TestCreditBoundary:
    """<=10 results costs 1 credit; 11-100 costs 2. Crossing it silently
    doubles the price of results the fallback never reads -- it takes the
    first candidate that survives retention."""

    def test_count_is_capped_at_the_one_credit_limit(self):
        c = _client()
        with patch.object(c._session, "post", return_value=_response()) as post:
            c.search("x", count=50)
        assert post.call_args.kwargs["json"]["num"] == _MAX_FREE_TIER_RESULTS

    def test_pricing_entry_matches_the_capped_rate(self):
        """If the cap is ever raised past 10, the $1.00/1000 entry is wrong by 2x."""
        from generator.llm_client import DEFAULT_TOOL_CALL_PRICING_USD_PER_1000
        assert DEFAULT_TOOL_CALL_PRICING_USD_PER_1000["serper"] == 1.00
        assert _MAX_FREE_TIER_RESULTS == 10


class TestUsageAccounting:
    """Serper consumes no tokens, so the ENTIRE cost rides on the tool-call
    count. Reporting zero would make a run of Serper searches cost $0.00 --
    the exact blind spot UsageTracker's summary warning exists for."""

    def test_each_query_is_recorded_as_one_tool_call(self):
        tracker = MagicMock()
        c = _client(usage_tracker=tracker, usage_operation_prefix="url_discovery_fallback")
        with patch.object(c._session, "post", return_value=_response(organic=[
            {"title": "t", "link": "https://example.gov/a"},
        ])):
            c.search("a"); c.search("b")
        assert tracker.add.call_count == 2
        kw = tracker.add.call_args.kwargs
        assert kw["provider"] == "serper"
        assert kw["tool_calls"] == 1
        assert kw["prompt_tokens"] == 0 and kw["completion_tokens"] == 0
        assert kw["operation"] == "url_discovery_fallback:search"

    def test_a_billed_error_response_still_records_usage(self):
        """A 4xx that consumed a credit must not report as free."""
        tracker = MagicMock()
        c = _client(usage_tracker=tracker)
        with patch.object(c._session, "post", return_value=_response(status=400)):
            c.search("a")
        assert tracker.add.call_count == 1

    def test_a_network_failure_records_nothing(self):
        """No request reached Serper, so no credit was consumed."""
        tracker = MagicMock()
        c = _client(usage_tracker=tracker)
        with patch.object(c._session, "post", side_effect=requests.RequestException("x")):
            c.search("a")
        tracker.add.assert_not_called()

    def test_the_cost_guard_prices_serper_rather_than_warning(self):
        """End-to-end against the real UsageTracker: a Serper run must not
        trip the 'no tool-call pricing' blind-spot warning."""
        from generator.llm_client import UsageTracker
        t = UsageTracker()
        t.add(provider="serper", model="serper", operation="url_discovery_fallback:search",
              prompt_tokens=0, completion_tokens=0, tool_calls=88)
        assert t.summary()["total_estimated_cost_usd"] == pytest.approx(0.088)


class TestCircuitBreaker:
    def test_opens_after_repeated_failures_and_then_short_circuits(self):
        c = _client()
        with patch.object(c._session, "post", side_effect=requests.RequestException("x")) as post:
            for _ in range(c._threshold):
                c.search("q")
            assert c.is_circuit_open()
            post.reset_mock()
            assert c.search("q") == []
            post.assert_not_called()


class TestBatchRoleDegradesSafely:
    def test_chat_completion_returns_empty_rather_than_raising(self):
        """Serper cannot invent an item list. Wired as a batch provider it must
        degrade to an empty harvest -- which callers already treat as 'fall
        back to per-destination calls' -- not AttributeError mid-run."""
        c = _client()
        assert c.chat_completion(system_prompt="s", user_prompt="u", live_search=True) == ""


class TestProviderFactory:
    def test_build_search_client_can_return_serper(self):
        from generator.search_provider import build_search_client
        with patch.dict("os.environ", {"SERPER_API_KEY": "k"}):
            client = build_search_client(
                "config.yaml", config_section="url_discovery",
                provider_key="nonbatch_search_provider", provider_override="serper",
                usage_tracker=None, usage_operation_prefix="url_discovery_fallback",
            )
        assert isinstance(client, SerperSearch)
