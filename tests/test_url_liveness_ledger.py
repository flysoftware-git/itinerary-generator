"""Tests for the tri-state link liveness ledger in generator.url_discovery.

The publication gate is fail-closed, but what it records afterwards is a single
bit -- the link shipped. These tests pin the third state: a link the gate never
managed to check must be distinguishable from one it checked and found alive,
because on a bot-blocked domain the two are otherwise identical.
"""
from unittest.mock import MagicMock

from generator.url_discovery import (
    LINK_LIVENESS_DEAD,
    LINK_LIVENESS_LIVE,
    LINK_LIVENESS_UNCHECKED,
    URLDiscoverer,
)


def _discoverer() -> URLDiscoverer:
    return URLDiscoverer.__new__(URLDiscoverer)


def _trip(*urls: str) -> dict:
    return {
        "destinations": [
            {
                "name": "Somewhere",
                "ai_content": {
                    "top_attractions": [
                        {"name": f"Attraction {i}", "url": url}
                        for i, url in enumerate(urls)
                    ]
                },
            }
        ]
    }


# --- classification -------------------------------------------------------


def test_a_successful_fetch_is_live():
    d = _discoverer()
    assert d.classify_link_liveness("https://example.com/a", True, 200) == LINK_LIVENESS_LIVE


def test_a_404_is_dead():
    d = _discoverer()
    assert d.classify_link_liveness("https://example.com/a", False, 404) == LINK_LIVENESS_DEAD


def test_a_bot_block_is_unchecked_not_live():
    """The whole point of the third state.

    A 403 from an aggressive WAF is not evidence the page is fine. Before this
    ledger existed the link shipped and carried the same single bit as a page
    that had answered with a 200.
    """
    d = _discoverer()
    assert d.classify_link_liveness("https://www.tripadvisor.com/x", False, 403) == LINK_LIVENESS_UNCHECKED


def test_a_timeout_is_unchecked_not_dead():
    d = _discoverer()
    state = d.classify_link_liveness("https://example.com/a", False, "read timed out")
    assert state == LINK_LIVENESS_UNCHECKED


def test_the_bot_block_carve_out_withdraws_dead_to_unchecked_not_to_live():
    """A connection refused against a .gov host is the carve-out's own case.

    The gate declines to call it dead. That must not silently promote it to
    live: nothing was learned, and the ledger has to say so. The instrument has
    been caught on both sides of this in practice -- the same links called
    blocked on one day and hand-verified live on another, with identical
    output either way.
    """
    d = _discoverer()
    status = "Failed to establish a new connection: [Errno 111] Connection refused"
    assert d._is_definitively_dead_status(status) is True
    assert d._is_bot_block_false_negative_dead_status("https://www.nps.gov/x", status) is True
    assert d.classify_link_liveness("https://www.nps.gov/x", False, status) == LINK_LIVENESS_UNCHECKED


# --- the ledger -----------------------------------------------------------


def test_an_unobserved_published_link_is_unchecked_with_a_reason():
    d = _discoverer()
    report = d.link_liveness_report(_trip("https://www.opentable.com/r/somewhere"))
    assert report["states"]["https://www.opentable.com/r/somewhere"] == LINK_LIVENESS_UNCHECKED
    assert report["details"]["https://www.opentable.com/r/somewhere"] == "never_fetched"
    assert report["counts"] == {LINK_LIVENESS_LIVE: 0, LINK_LIVENESS_DEAD: 0, LINK_LIVENESS_UNCHECKED: 1}
    assert report["unchecked_share"] == 1.0


def test_dead_is_never_laundered_by_a_later_live_observation():
    """Fail-closed precedence, and order-independence with it."""
    d = _discoverer()
    d._record_fetch_liveness("https://example.com/a", False, 404)
    d._record_fetch_liveness("https://example.com/a", True, 200)
    assert d.link_liveness_state("https://example.com/a") == LINK_LIVENESS_DEAD

    e = _discoverer()
    e._record_fetch_liveness("https://example.com/a", True, 200)
    e._record_fetch_liveness("https://example.com/a", False, 404)
    assert e.link_liveness_state("https://example.com/a") == LINK_LIVENESS_DEAD


def test_live_beats_unchecked_in_either_order():
    d = _discoverer()
    d._record_fetch_liveness("https://example.com/a", False, 403)
    d._record_fetch_liveness("https://example.com/a", True, 200)
    assert d.link_liveness_state("https://example.com/a") == LINK_LIVENESS_LIVE

    e = _discoverer()
    e._record_fetch_liveness("https://example.com/a", True, 200)
    e._record_fetch_liveness("https://example.com/a", False, 403)
    assert e.link_liveness_state("https://example.com/a") == LINK_LIVENESS_LIVE


def test_the_report_counts_the_three_states_separately():
    d = _discoverer()
    d._record_fetch_liveness("https://example.com/live", True, 200)
    d._record_fetch_liveness("https://example.com/dead", False, 410)
    d._record_fetch_liveness("https://www.yelp.com/biz/x", False, 403)
    report = d.link_liveness_report(
        _trip(
            "https://example.com/live",
            "https://example.com/dead",
            "https://www.yelp.com/biz/x",
            "https://www.alltrails.com/trail/never-touched",
        )
    )
    assert report["published_count"] == 4
    assert report["counts"] == {
        LINK_LIVENESS_LIVE: 1,
        LINK_LIVENESS_DEAD: 1,
        LINK_LIVENESS_UNCHECKED: 2,
    }
    assert report["unchecked_share"] == 0.5
    assert report["unchecked_by_domain"] == {
        "www.alltrails.com": 1,
        "www.yelp.com": 1,
    }


def test_unchecked_is_attributed_to_the_domain_that_blocked():
    """The failure is domain-shaped, so the report has to be too -- a bare
    count of 34 says nothing a reader can act on; four host names do."""
    d = _discoverer()
    for url in (
        "https://www.tripadvisor.com/a",
        "https://www.tripadvisor.com/b",
        "https://www.yelp.com/c",
    ):
        d._record_fetch_liveness(url, False, 403)
    report = d.link_liveness_report(
        _trip("https://www.tripadvisor.com/a", "https://www.tripadvisor.com/b", "https://www.yelp.com/c")
    )
    assert list(report["unchecked_by_domain"].items()) == [
        ("www.tripadvisor.com", 2),
        ("www.yelp.com", 1),
    ]


# --- wiring ---------------------------------------------------------------


def test_a_domain_cooldown_records_unchecked_rather_than_its_synthetic_403():
    """The cooldown returns `False, 403, ""` without making a request at all.

    Recording that 403 as an observation would be a lie about work that never
    happened, so the cooldown path records the reason instead.
    """
    d = _discoverer()
    d._url_validator = MagicMock()
    d._page_text_cache = {}
    d._domain_blocked_until_ts = {}
    d._is_alltrails_trail_url = lambda url: False

    import time as _time

    d._domain_blocked_until_ts["www.tripadvisor.com"] = _time.monotonic() + 60.0
    ok, status, text = d._fetch_page_text("https://www.tripadvisor.com/blocked")
    assert (ok, status) == (False, 403)
    assert d.link_liveness_state("https://www.tripadvisor.com/blocked") == LINK_LIVENESS_UNCHECKED
    assert d._link_liveness["https://www.tripadvisor.com/blocked"][1] == "domain_cooldown"
    # And no request was made.
    d._url_validator.get_text.assert_not_called()


def test_a_page_text_cache_hit_still_records():
    """The page-text cache persists across runs. A run that publishes a link
    it only ever saw through the cache must not report it as never checked."""
    d = _discoverer()
    d._url_validator = MagicMock()
    d._domain_blocked_until_ts = {}
    d._is_alltrails_trail_url = lambda url: False
    d._page_text_cache = {"https://example.com/a": (True, 200, "hello")}

    d._fetch_page_text("https://example.com/a")
    assert d.link_liveness_state("https://example.com/a") == LINK_LIVENESS_LIVE
    d._url_validator.get_text.assert_not_called()


def test_a_real_fetch_records_its_verdict():
    d = _discoverer()
    validator = MagicMock()
    validator.get_text.return_value = (False, 404, "")
    d._url_validator = validator
    d._page_text_cache = {}
    d._domain_blocked_until_ts = {}
    d._is_alltrails_trail_url = lambda url: False
    d._mark_persistent_cache_dirty = lambda: None

    d._fetch_page_text("https://example.com/gone")
    assert d.link_liveness_state("https://example.com/gone") == LINK_LIVENESS_DEAD


def test_verify_url_cached_records_its_verdict():
    d = _discoverer()
    validator = MagicMock()
    validator.verify_url.return_value = (True, 200)
    d._url_validator = validator
    d._verify_url_cache = {}
    d._mark_persistent_cache_dirty = lambda: None

    d._verify_url_cached("https://example.com/ok")
    assert d.link_liveness_state("https://example.com/ok") == LINK_LIVENESS_LIVE


def test_the_report_lands_in_the_validation_report():
    """One key, so the artifact -- not just the log -- says how much of itself
    the gate was able to check."""
    from generator.report_writer import ReportWriter
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = ReportWriter(tmp).write(
            {
                "valid": True,
                "link_liveness": {"counts": {"live": 1, "dead": 0, "unchecked": 2}},
            }
        )
        written = json.loads(Path(path).read_text(encoding="utf-8"))
    assert written["link_liveness"]["counts"]["unchecked"] == 2
