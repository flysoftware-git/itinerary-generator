"""Cost summary output helpers."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


def print_cost_summary(
    *,
    model: str,
    manifest_path: str,
    estimated_usd: float,
    environment: str = "dev",
) -> None:
    """Print formatted LLM cost summary.

    ``estimated_usd`` is computed post-run from real recorded token counts times
    a static price table -- it is not a provider-reported bill, so it is labeled
    as an estimate rather than as a distinct "actual" figure.
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    manifest_name = Path(manifest_path).name
    print(f"[LLM-COST] {ts} | {model} | {manifest_name}")
    print(f"  Estimated USD : ${estimated_usd:.4f} (from recorded token usage, list pricing)")
    print(f"  Environment   : {environment}")


def summarize_from_usage(usage: dict[str, Any]) -> float:
    """Return the estimated cost computed from recorded token usage.

    Debugs if usage is empty.
    """
    import logging
    logger = logging.getLogger(__name__)
    if not usage:
        logger.warning("summarize_from_usage: usage dict is empty")
    estimated = float(usage.get("total_estimated_cost_usd", 0.0) or 0.0)
    logger.info(f"Cost summary: estimated=${estimated:.4f} (from {len(usage)} keys)")
    return estimated
