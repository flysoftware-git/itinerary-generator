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
    tool_call_cost_usd: float = 0.0,
) -> None:
    """Print formatted LLM cost summary.

    ``estimated_usd`` is computed post-run from real recorded token counts and
    web_search tool-invocation counts times static price tables -- it is not a
    provider-reported bill, so it is labeled as an estimate rather than as a
    distinct "actual" figure. ``tool_call_cost_usd`` is the portion of
    ``estimated_usd`` billed per web_search invocation (xAI $5/1000, OpenAI
    $10/1000 -- see llm_client.py's DEFAULT_TOOL_CALL_PRICING_USD_PER_1000),
    already included in ``estimated_usd``, broken out separately here because
    it was previously omitted entirely: real xAI billing (~$5/day) ran well
    above what this estimator reported (~$0.40/run) until this field existed.
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    manifest_name = Path(manifest_path).name
    token_cost = estimated_usd - tool_call_cost_usd
    print(f"[LLM-COST] {ts} | {model} | {manifest_name}")
    print(f"  Estimated USD : ${estimated_usd:.4f} (token usage ${token_cost:.4f} + web_search tool fees ${tool_call_cost_usd:.4f}, list pricing)")
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
