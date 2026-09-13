import json

from generator.report_writer import ReportWriter


def test_write_persists_llm_usage_records(tmp_path) -> None:
    """Regression: per-call usage records (provider/model/operation/tokens/cost)
    are the only thing carrying per-operation attribution. Without them,
    per-stage token/cost spend is unmeasurable from a completed run -- only
    the aggregate total survives."""
    writer = ReportWriter(output_dir=tmp_path)
    report = {
        "valid": True,
        "error_count": 0,
        "warning_count": 0,
        "meta": {
            "generator_version": "1.4.4",
            "template_version": "2.5",
            "generated_at_utc": "2026-08-14T00:00:00Z",
            "llm": {"provider": "grok", "model": "grok-latest"},
        },
        "llm_usage": {
            "models": ["grok:grok-latest"],
            "total_calls": 2,
            "total_estimated_cost_usd": 0.5,
            "records": [
                {
                    "provider": "grok",
                    "model": "grok-latest",
                    "operation": "destination_bundle:zion",
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "estimated_cost_usd": 0.3,
                },
                {
                    "provider": "grok",
                    "model": "grok-latest",
                    "operation": "url_discovery:chat_completion",
                    "prompt_tokens": 80,
                    "completion_tokens": 40,
                    "estimated_cost_usd": 0.2,
                },
            ],
        },
        "errors": [],
        "warnings": [],
        "html_path": "output/index.html",
    }

    path = writer.write(report)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["llm"]["records"] == report["llm_usage"]["records"]
    assert len(payload["llm"]["records"]) == 2


def _minimal_report(**extra):
    report = {"valid": True, "error_count": 0, "warning_count": 0, "meta": {}, "llm_usage": {},
              "errors": [], "warnings": [], "html_path": "index.html"}
    report.update(extra)
    return report


def test_the_written_report_says_when_search_credits_ran_out(tmp_path) -> None:
    """#137 set report["search_quota_exhausted"] and the file never had it.

    ReportWriter copies named keys only, so a key set on the dict and not
    listed here is silently dropped -- and the tests that shipped with #137
    read the dict, not the file. Measured on two real 3.2.1 builds: the run
    ledger carried [] and validation_report.json had no such key.
    """
    writer = ReportWriter(output_dir=tmp_path)
    path = writer.write(_minimal_report(search_quota_exhausted=["url_discovery_fallback"]))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["search_quota_exhausted"] == ["url_discovery_fallback"]


def test_measured_clean_and_not_measured_stay_distinct_in_the_file(tmp_path) -> None:
    clean = ReportWriter(output_dir=tmp_path / "clean").write(_minimal_report(search_quota_exhausted=[]))
    unmeasured = ReportWriter(output_dir=tmp_path / "none").write(_minimal_report())
    assert json.loads(clean.read_text(encoding="utf-8"))["search_quota_exhausted"] == []
    assert json.loads(unmeasured.read_text(encoding="utf-8"))["search_quota_exhausted"] is None
