"""What routing could not draw reaches the run's artifacts, not just memory.

#167 counts what routing did: legs routed, legs given up on the rate limit,
legs refused on the daily quota. A leg it could not route is drawn as a
straight line between its ends with estimated miles and hours, and the map
cannot say which of its lines those are.

Those counts lived in `routing.stats()` and nothing read it outside the tests.
That is #137's failure exactly -- a value set on a dict, and the written file
never carried it -- caught there only after two published builds.
"""

from __future__ import annotations

import json

from generator import routing
from generator.main import _routing_gate_warnings, _routing_outcome, _run_quality_gate
from generator.report_writer import ReportWriter


def _report(**extra):
    report = {"valid": True, "error_count": 0, "warning_count": 0, "meta": {},
              "llm_usage": {}, "errors": [], "warnings": [], "html_path": "index.html"}
    report.update(extra)
    return report


def test_the_written_report_carries_the_routing_counts(tmp_path) -> None:
    """Read back from the file, not from the dict handed to the writer:
    `ReportWriter` copies named keys only."""
    counts = dict.fromkeys(routing.STAT_NAMES, 0) | {"routed": 7, "rate_limited_gave_up": 3}

    path = ReportWriter(output_dir=tmp_path).write(_report(routing=counts))

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["routing"]["rate_limited_gave_up"] == 3
    assert payload["routing"]["routed"] == 7


def test_a_run_that_never_routed_is_not_a_run_that_routed_nothing(tmp_path) -> None:
    """None and zeros mean different things: no key and no request at all,
    against a run answered entirely from the cache."""
    unmeasured = ReportWriter(output_dir=tmp_path / "none").write(_report())
    assert json.loads(unmeasured.read_text(encoding="utf-8"))["routing"] is None

    cached = ReportWriter(output_dir=tmp_path / "cached").write(
        _report(routing=dict.fromkeys(routing.STAT_NAMES, 0) | {"cache_hits": 4}))
    assert json.loads(cached.read_text(encoding="utf-8"))["routing"]["cache_hits"] == 4


def test_the_outcome_is_none_until_something_is_counted() -> None:
    routing.reset_stats()
    assert _routing_outcome() is None

    routing._count("routed")
    try:
        assert (_routing_outcome() or {})["routed"] == 1
    finally:
        routing.reset_stats()


def test_the_gate_says_which_legs_are_straight_lines(capsys) -> None:
    """A straight line drawn because the allowance ran out is a fact about the
    run. Seen red with the gate's routing lines removed."""
    counts = dict.fromkeys(routing.STAT_NAMES, 0) | {
        "routed": 12, "rate_limited_gave_up": 2, "quota_refused": 5, "failed": 1}

    _run_quality_gate({"destinations": []}, None, None, routing=counts)

    said = capsys.readouterr().out
    assert "5 leg(s) are straight-line estimates" in said, said
    assert "2 leg(s) are straight-line estimates" in said, said
    assert "routing unavailable for 1 leg(s)" in said, said


def test_a_run_whose_legs_were_all_routed_says_nothing_about_routing() -> None:
    """No warning where there is nothing wrong: a gate that always speaks is a
    gate nobody reads."""
    clean = dict.fromkeys(routing.STAT_NAMES, 0) | {"routed": 9, "cache_hits": 3}
    assert _routing_gate_warnings(clean) == []
    assert _routing_gate_warnings(None) == []
