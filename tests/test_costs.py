"""Tests for generator.costs cost-summary output."""
from __future__ import annotations

from generator.costs import print_cost_summary, summarize_from_usage


def test_summarize_from_usage_returns_single_estimated_value():
    usage = {"total_estimated_cost_usd": 0.4950, "models": []}
    estimated = summarize_from_usage(usage)
    assert estimated == 0.4950


def test_summarize_from_usage_handles_empty_usage():
    assert summarize_from_usage({}) == 0.0


def test_print_cost_summary_labels_estimate_not_actual(capsys):
    print_cost_summary(
        model="grok/grok-4-fast+openai/gpt-4o-mini-2024-07-18",
        manifest_path="sw_manifest.yaml",
        estimated_usd=0.4950,
        environment="dev",
    )
    out = capsys.readouterr().out
    assert "Estimated USD" in out
    assert "Actual USD" not in out
    assert "Predicted USD" not in out
    assert "grok/grok-4-fast+openai/gpt-4o-mini-2024-07-18" in out
