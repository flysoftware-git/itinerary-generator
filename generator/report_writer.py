"""
report_writer.py — Write JSON validation report to output directory.
"""
from __future__ import annotations
import datetime, json
from pathlib import Path
from typing import Any

from generator import __version__, __template_version__


class ReportWriter:
    def __init__(self, output_dir: str | Path = "output") -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, report: dict[str, Any]) -> Path:
        meta = report.get("meta", {})
        llm_usage = report.get("llm_usage", {})
        out = {
            "generator_version": meta.get("generator_version", __version__),
            "template_version": meta.get("template_version", __template_version__),
            "generated_at_utc": meta.get("generated_at_utc", ""),
            "development_build": meta.get("development_build", {}),
            "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
            "summary": {
                "valid": report.get("valid", False),
                "error_count": report.get("error_count", 0),
                "warning_count": report.get("warning_count", 0),
            },
            "llm": {
                "provider": meta.get("llm", {}).get("provider", ""),
                "model": meta.get("llm", {}).get("model", ""),
                "models": llm_usage.get("models", []),
                "total_calls": llm_usage.get("total_calls", 0),
                "total_estimated_cost_usd": llm_usage.get("total_estimated_cost_usd", 0.0),
                # Per-call records are the only thing carrying per-operation
                # attribution (provider/model/operation/tokens/cost per call).
                # Without them, per-stage token/cost spend is unmeasurable from
                # a completed run -- only the aggregate total survives.
                "records": llm_usage.get("records", []),
            },
            "errors": report.get("errors", []),
            "warnings": report.get("warnings", []),
            # Tri-state liveness for every published link (see
            # url_discovery.LINK_LIVENESS_*). Carried here rather than left in
            # the log because "how much of this guide did the gate actually
            # check?" is a property of the artifact, and a log line is gone by
            # the time anyone asks.
            "link_liveness": report.get("link_liveness", {}),
            # Which search clients ran out of credits (#137). Copied as-is,
            # not defaulted: None means URL discovery never ran, [] means it
            # ran and nothing was exhausted, and a default of [] would turn
            # "not measured" into "measured, clean". The first version of
            # #137 set this on the report dict and never listed it here, so
            # the file never carried it -- this writer copies named keys only.
            "search_quota_exhausted": report.get("search_quota_exhausted"),
            # Routing's own counts (routing.STAT_NAMES), for the same reason as
            # the line above: the straight lines on the map are explained here
            # or nowhere. None when routing never asked for a leg.
            "routing": report.get("routing"),
            "html_path": report.get("html_path", ""),
        }
        report_path = self._output_dir / "validation_report.json"
        report_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        return report_path
