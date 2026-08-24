"""Tests for scripts/provenance_canary.py.

These tests never touch the network. Every provider interaction is a fake
whose only job is to reproduce one specific real-world shape:

  * the 2026-08-14 silent deprecation -- content returns, zero tool calls
  * a healthy run -- content returns, tool calls recorded
  * a provider outage -- exception, which must NOT be reported as a
    provenance failure

The last one is the point of the whole exit-code split, so it is tested
explicitly rather than left to the docstring.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "provenance_canary.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("provenance_canary", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["provenance_canary"] = module
    spec.loader.exec_module(module)
    return module


canary = _load_module()


class _FakeTracker:
    """Stands in for UsageTracker, recording only what the canary reads."""

    def __init__(self, tool_calls_per_call: int = 1) -> None:
        self._tool_calls_per_call = tool_calls_per_call
        self._records: list[dict] = []

    def note_call(self) -> None:
        self._records.append({"tool_calls": self._tool_calls_per_call})

    def summary(self) -> dict:
        return {
            "records": list(self._records),
            "total_calls": len(self._records),
            "total_estimated_cost_usd": 0.0123,
            "total_tool_call_cost_usd": 0.005,
        }


class _FakeClient:
    def __init__(self, tracker: _FakeTracker, content: str, *, raises: Exception | None = None,
                 circuit_open: bool = False) -> None:
        self._tracker = tracker
        self._content = content
        self._raises = raises
        self._circuit_open = circuit_open
        self.calls = 0

    def is_circuit_open(self) -> bool:
        return self._circuit_open

    def chat_completion(self, **kwargs):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        assert kwargs.get("live_search") is True, "canary must probe the live-search path"
        self._tracker.note_call()
        return self._content


def _install(monkeypatch, client: _FakeClient, tracker: _FakeTracker, *, any_url_live: bool = True):
    monkeypatch.setattr(canary, "_new_tracker", lambda: tracker)
    monkeypatch.setattr(canary, "_build_client", lambda provider, config_path, t: client)
    monkeypatch.setattr(
        canary,
        "_verify_any_url_live",
        lambda urls, limit=3: (any_url_live, [{"url": u, "ok": any_url_live, "status": 200} for u in urls[:limit]]),
    )


HEALTHY_CONTENT = '{"url": "https://www.nps.gov/zion/index.htm"}'


def test_healthy_run_passes(monkeypatch):
    tracker = _FakeTracker(tool_calls_per_call=2)
    client = _FakeClient(tracker, HEALTHY_CONTENT)
    _install(monkeypatch, client, tracker)

    report = canary.run_canary(provider="grok", probes=1)

    assert report["result"] == "PASS"
    assert report["assertions"]["A1_TOOL_INVOKED"]["status"] == "PASS"
    assert report["assertions"]["A1_TOOL_INVOKED"]["web_search_calls"] == 2
    assert report["assertions"]["A2_CONTENT"]["status"] == "PASS"
    assert report["assertions"]["A3_URL_LIVE"]["status"] == "PASS"


def test_silent_deprecation_shape_fails_a1(monkeypatch):
    """The exact 2026-08-14 failure: plausible content, no search performed.

    Everything downstream looks fine -- content is returned, a real URL is
    present, and that URL resolves. Only the tool count betrays it. If this
    test ever passes with A1 == PASS, the canary has stopped detecting the
    thing it was built for.
    """
    tracker = _FakeTracker(tool_calls_per_call=0)
    client = _FakeClient(tracker, HEALTHY_CONTENT)
    _install(monkeypatch, client, tracker, any_url_live=True)

    report = canary.run_canary(provider="grok", probes=1)

    assert report["result"] == "FAIL"
    assert report["assertions"]["A1_TOOL_INVOKED"]["status"] == "FAIL"
    assert report["assertions"]["A1_TOOL_INVOKED"]["web_search_calls"] == 0
    # The point of the canary: everything else still looks healthy.
    assert report["assertions"]["A2_CONTENT"]["status"] == "PASS"
    assert report["assertions"]["A3_URL_LIVE"]["status"] == "PASS"


def test_empty_content_fails_a2(monkeypatch):
    tracker = _FakeTracker(tool_calls_per_call=1)
    client = _FakeClient(tracker, "")
    _install(monkeypatch, client, tracker, any_url_live=False)

    report = canary.run_canary(provider="grok", probes=1)

    assert report["result"] == "FAIL"
    assert report["assertions"]["A2_CONTENT"]["status"] == "FAIL"


def test_fabricated_url_fails_a3_while_a1_passes(monkeypatch):
    tracker = _FakeTracker(tool_calls_per_call=1)
    client = _FakeClient(tracker, '{"url": "https://example.invalid/nope"}')
    _install(monkeypatch, client, tracker, any_url_live=False)

    report = canary.run_canary(provider="grok", probes=1)

    assert report["assertions"]["A1_TOOL_INVOKED"]["status"] == "PASS"
    assert report["assertions"]["A3_URL_LIVE"]["status"] == "FAIL"
    assert report["result"] == "FAIL"


def test_provider_exception_is_harness_error_not_failure(monkeypatch):
    """An outage must not be reported as "search is dead"."""
    tracker = _FakeTracker()
    client = _FakeClient(tracker, HEALTHY_CONTENT, raises=RuntimeError("connection reset"))
    _install(monkeypatch, client, tracker)

    with pytest.raises(canary.CanaryHarnessError):
        canary.run_canary(provider="grok", probes=1)


def test_open_circuit_is_harness_error(monkeypatch):
    tracker = _FakeTracker()
    client = _FakeClient(tracker, HEALTHY_CONTENT, circuit_open=True)
    _install(monkeypatch, client, tracker)

    with pytest.raises(canary.CanaryHarnessError):
        canary.run_canary(provider="grok", probes=1)
    assert client.calls == 0, "must not spend money during a known outage"


def test_multiple_probes_use_distinct_queries(monkeypatch):
    tracker = _FakeTracker(tool_calls_per_call=1)
    client = _FakeClient(tracker, HEALTHY_CONTENT)
    _install(monkeypatch, client, tracker)

    report = canary.run_canary(provider="grok", probes=3)

    queries = [r["query"] for r in report["responses"]]
    assert len(queries) == 3
    assert len(set(queries)) == 3, "probes must not all hit one cacheable query"


def test_freshness_probe_passes_when_today_present(monkeypatch):
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tracker = _FakeTracker(tool_calls_per_call=1)
    client = _FakeClient(tracker, '{"url": "https://www.nps.gov/zion/index.htm", "as_of": "%s"}' % today)
    _install(monkeypatch, client, tracker)

    report = canary.run_canary(provider="grok", probes=1, freshness=True)

    assert report["assertions"]["A4_FRESHNESS"]["status"] == "PASS"


def test_freshness_probe_fails_on_stale_answer(monkeypatch):
    tracker = _FakeTracker(tool_calls_per_call=1)
    client = _FakeClient(tracker, '{"url": "https://www.nps.gov/zion/index.htm", "as_of": "2024-01-01"}')
    _install(monkeypatch, client, tracker)

    report = canary.run_canary(provider="grok", probes=1, freshness=True)

    assert report["assertions"]["A4_FRESHNESS"]["status"] == "FAIL"
    assert report["result"] == "FAIL"


def test_dry_run_spends_nothing_and_skips(monkeypatch):
    def _boom(*_a, **_k):  # pragma: no cover - must never be reached
        raise AssertionError("dry run must not construct a client")

    monkeypatch.setattr(canary, "_build_client", _boom)
    monkeypatch.setattr(canary, "_new_tracker", _boom)

    report = canary.run_canary(provider="grok", probes=1, dry_run=True)

    assert report["result"] == "SKIPPED"
    assert all(a["status"] == "SKIPPED" for a in report["assertions"].values())


def test_extract_urls_strips_trailing_punctuation():
    urls = canary._extract_urls('see https://www.nps.gov/zion/index.htm, and https://a.example/b.')
    assert urls == ["https://www.nps.gov/zion/index.htm", "https://a.example/b"]


def test_extract_urls_dedupes():
    urls = canary._extract_urls("https://a.example/x https://a.example/x")
    assert urls == ["https://a.example/x"]


@pytest.mark.parametrize(
    "result,expected_exit",
    [("PASS", 0), ("FAIL", 1)],
)
def test_main_exit_codes(monkeypatch, result, expected_exit):
    monkeypatch.setattr(
        canary,
        "run_canary",
        lambda **_k: {"result": result, "assertions": {}, "usage": {}, "started_at": "x"},
    )
    assert canary.main(["--dry-run"]) == expected_exit


def test_main_returns_2_when_harness_cannot_run(monkeypatch):
    def _raise(**_k):
        raise canary.CanaryHarnessError("no API key")

    monkeypatch.setattr(canary, "run_canary", _raise)
    assert canary.main(["--dry-run"]) == 2
