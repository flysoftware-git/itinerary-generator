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
