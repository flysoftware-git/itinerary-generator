"""A run says which retention gate refused links, and how often.

_reject_retention records the exit that fired on the latest call in
_last_retention_rejection, and the next call overwrites it. Nothing added the
refusals up, so the question every gate change needs answered first -- is this
the gate that is firing? -- could only be answered by wrapping _reject_retention
from outside. The audit pass carried the exit as prose inside a discard
message; a discovery-time refusal, which is where nearly all of them happen,
carried no exit at all.

The counts are instrumentation. They must change no decision, so the last test
builds the same guide with the counter live and with it switched off and
requires the HTML to be identical byte for byte.
"""
from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from generator.main import _build_destination_status_report
from generator.url_discovery import _RETENTION_EXIT_LABELS, URLDiscoverer


def _bare() -> URLDiscoverer:
    return URLDiscoverer.__new__(URLDiscoverer)


# ── (a) discovery-time refusals ────────────────────────────────────────────


def test_a_refusal_is_counted_under_its_exit_and_destination():
    d = _bare()
    assert d._retain_discovered_url("", "Delicate Arch", "Arches", allow_alltrails=False) == ""
    assert d._retain_discovered_url("", "Windows", "Arches", allow_alltrails=False) == ""
    assert d._retain_discovered_url("", "Mesa Arch", "Canyonlands", allow_alltrails=False) == ""

    assert d.retention_exit_counts("Arches") == {
        "1": {"label": _RETENTION_EXIT_LABELS[1], "count": 2},
    }
    assert d.retention_exit_counts("Canyonlands") == {
        "1": {"label": _RETENTION_EXIT_LABELS[1], "count": 1},
    }
    assert d.retention_exit_counts() == {
        "1": {"label": _RETENTION_EXIT_LABELS[1], "count": 3},
    }


def test_different_exits_are_counted_apart():
    d = _bare()
    d._is_url_domain_denied = lambda url: "denied.example" in url
    d._retain_discovered_url("", "A", "Zion", allow_alltrails=False)
    d._retain_discovered_url("https://denied.example/a", "B", "Zion", allow_alltrails=False)
    d._retain_discovered_url("https://denied.example/b", "C", "Zion", allow_alltrails=False)

    counts = d.retention_exit_counts("Zion")
    assert {k: v["count"] for k, v in counts.items()} == {"1": 1, "2": 2}
    assert counts["2"]["label"] == _RETENTION_EXIT_LABELS[2]


def test_a_refusal_with_no_destination_counts_toward_the_run_only():
    d = _bare()
    d._retain_discovered_url("", "Somewhere", "", allow_alltrails=False)
    assert d.retention_exit_counts()["1"]["count"] == 1
    assert d.retention_exit_counts("") == {}


def test_no_refusals_reads_as_empty_not_an_error():
    d = _bare()
    assert d.retention_exit_counts() == {}
    assert d.retention_exit_counts("Anywhere") == {}


# ── (c) concurrent callers ─────────────────────────────────────────────────


class _ReadWaitsForASecondReader(dict):
    """A counter whose read waits for a second thread to read too.

    With the lock held, the second caller cannot reach its read: the barrier
    times out, the first update lands, and the second one reads the new value.
    Without it both callers read the same count and one update is lost -- the
    interleaving is forced rather than hoped for.
    """

    def __init__(self) -> None:
        super().__init__()
        self._barrier = threading.Barrier(2, timeout=0.5)

    def get(self, key: Any, default: Any = None) -> Any:
        value = super().get(key, default)
        try:
            self._barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return value


def test_two_threads_refusing_at_once_do_not_lose_a_count():
    d = _bare()
    d._retention_exit_counts_total = _ReadWaitsForASecondReader()
    d._retention_exit_counts_by_destination = {}

    threads = [
        threading.Thread(
            target=d._retain_discovered_url,
            args=("", f"Item {i}", f"Dest {i}"),
            kwargs={"allow_alltrails": False},
        )
        for i in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert d.retention_exit_counts()["1"]["count"] == 2, (
        "two threads refused at once and one count was lost -- the counter "
        "update is not serialised"
    )


def test_each_thread_s_refusal_is_attributed_to_its_own_destination():
    """Two destinations inside the gate at once, on one instance.

    Both calls enter _retain_discovered_url and record their destination
    before either reaches an exit. Destination held on the instance, the later
    writer would take both refusals.
    """
    d = _bare()
    barrier = threading.Barrier(2, timeout=5)

    def _denied_after_both_have_entered(url: str) -> bool:
        barrier.wait()
        return True

    d._is_url_domain_denied = _denied_after_both_have_entered
    threads = [
        threading.Thread(
            target=d._retain_discovered_url,
            args=("https://x.example/", "Item", dest),
            kwargs={"allow_alltrails": False},
        )
        for dest in ("Arches", "Canyonlands")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert d.retention_exit_counts("Arches") == {"2": {"label": _RETENTION_EXIT_LABELS[2], "count": 1}}, (
        "a refusal was attributed to another thread's destination"
    )
    assert d.retention_exit_counts("Canyonlands") == {"2": {"label": _RETENTION_EXIT_LABELS[2], "count": 1}}, (
        "a refusal was attributed to another thread's destination"
    )


# ── (d) the report ─────────────────────────────────────────────────────────


def _report(dest_counts: dict[str, Any], run_counts: dict[str, Any] | None) -> dict[str, Any]:
    return _build_destination_status_report(
        trip={"destinations": [{
            "id": "arches", "name": "Arches",
            "_registry_decisions": [],
            "ai_content": {},
            "_url_discovery": {
                "reason_counts": {"url_rejected": 3},
                "source_counts": {"search": 3},
                "retention_exit_counts": dest_counts,
            },
        }]},
        registry={"entities": [], "reports": [], "destination_view": {}},
        run_id="r", skip_events=True, skip_images=True, skip_url_discovery=False,
        retention_exit_counts=run_counts,
    )


def test_the_report_carries_the_counts_beside_the_reason_codes():
    dest_counts = {"16": {"label": _RETENTION_EXIT_LABELS[16], "count": 5}}
    run_counts = {
        "16": {"label": _RETENTION_EXIT_LABELS[16], "count": 5},
        "29": {"label": _RETENTION_EXIT_LABELS[29], "count": 2},
    }
    report = _report(dest_counts, run_counts)

    url_stage = report["destinations"][0]["stage_status"]["url_discovery"]
    assert url_stage["retention_exit_counts"] == dest_counts
    # Existing fields untouched.
    assert url_stage["reason_counts"] == {"url_rejected": 3}
    assert url_stage["source_counts"] == {"search": 3}
    assert report["summary"]["retention_exit_counts"] == run_counts


def test_a_report_without_counts_says_empty_rather_than_failing():
    report = _report({}, None)
    assert report["destinations"][0]["stage_status"]["url_discovery"]["retention_exit_counts"] == {}
    assert report["summary"]["retention_exit_counts"] == {}


# ── (b) the audit pass, and (e) no decision changes ────────────────────────


@pytest.fixture
def _no_network(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    with patch.object(
        requests.sessions.Session,
        "request",
        side_effect=requests.exceptions.ConnectionError("network disabled in test"),
    ):
        yield


def _pipeline_trip() -> dict[str, Any]:
    from tests.test_pipeline_integration import _minimal_trip

    return _minimal_trip()


def _discoverer(fake_llm: Any) -> URLDiscoverer:
    d = URLDiscoverer("config.yaml", llm_client=fake_llm, output_dir="output")
    d._search = MagicMock()
    d._search.is_circuit_open.return_value = False
    d._search.chat_completion.return_value = ""
    return d


def test_the_audit_pass_refusals_are_counted_and_reach_the_destination(_no_network):
    """The audit re-runs the gate after discovery snapshotted its counts."""
    from tests.test_pipeline_integration import _FakeLLMClient

    trip = _pipeline_trip()
    fake_llm = _FakeLLMClient()
    from generator.ai_content import AIContentGenerator

    AIContentGenerator("config.yaml", llm_client=fake_llm).generate_all(trip)
    d = _discoverer(fake_llm)
    d.discover_all(trip)
    dest = trip["destinations"][0]
    name = dest["name"]
    at_discovery = d.retention_exit_counts(name)
    assert dest["_url_discovery"]["retention_exit_counts"] == at_discovery

    # A link the audit must refuse: unescaped whitespace, exit 6.
    dest["ai_content"]["top_attractions"][0]["url"] = "https://www.nps.gov/zion/a b.htm"
    d.audit_discovered_urls(trip)

    after_audit = d.retention_exit_counts(name)
    before = at_discovery.get("6", {}).get("count", 0)
    assert after_audit.get("6", {}).get("count", 0) == before + 1, (
        f"the audit pass refused a link at exit 6 and it was not counted: {after_audit}"
    )
    assert dest["_url_discovery"]["retention_exit_counts"] == after_audit, (
        "the destination still carries discovery's snapshot, so the audit's "
        "refusals never reach the report"
    )


def _build_guide(count_exits: bool) -> tuple[str, dict[str, Any]]:
    from generator.ai_content import AIContentGenerator
    from generator.entity_registry import (
        build_entity_registry,
        reconcile_schedule_from_registry,
        reconcile_trip_from_registry,
    )
    from generator.html_assembler import HTMLAssembler
    from tests.test_pipeline_integration import _FakeLLMClient

    trip = _pipeline_trip()
    fake_llm = _FakeLLMClient()
    ai_gen = AIContentGenerator("config.yaml", llm_client=fake_llm)
    ai_gen.generate_all(trip)
    d = _discoverer(fake_llm)
    with patch.object(
        URLDiscoverer,
        "_count_retention_exit",
        URLDiscoverer._count_retention_exit if count_exits else (lambda self, exit_id: None),
    ):
        d.discover_all(trip)
        # Refused at the audit as well as at discovery.
        trip["destinations"][0]["ai_content"]["top_attractions"][0]["url"] = "https://www.nps.gov/zion/a b.htm"
        d.audit_discovered_urls(trip)
    counts = d.retention_exit_counts()
    ai_gen.normalize_trip_content(trip)
    registry = build_entity_registry(trip)
    trip = reconcile_trip_from_registry(trip, registry)
    reconcile_schedule_from_registry(trip, registry)
    return HTMLAssembler("config.yaml").assemble(trip), counts


def test_counting_changes_no_decision_the_guide_is_byte_identical(_no_network):
    counted_html, counted = _build_guide(count_exits=True)
    uncounted_html, uncounted = _build_guide(count_exits=False)

    assert counted, "the fixture refused nothing, so it cannot show counting is inert"
    assert uncounted == {}
    assert counted_html.encode("utf-8") == uncounted_html.encode("utf-8"), (
        "the guide differs with retention-exit counting on -- instrumentation changed a decision"
    )
