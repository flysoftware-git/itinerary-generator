"""
llm_client.py — Multi-provider LLM routing with usage and cost tracking.

Supported providers:
  - openai
  - anthropic
  - deepseek (OpenAI-compatible)
  - gemini
  - grok (OpenAI-compatible)
  - azure_openai (legacy compatibility)
"""
from __future__ import annotations

import json
import os
import copy
import threading
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import openai
from openai import AzureOpenAI, OpenAI
from generator.llm.router import LLMRouter

logger = logging.getLogger(__name__)

# threshold=3/window=180s is sized differently from grok_search.py's search-
# harvest breaker (4/30s) because content-generation calls have a longer
# per-call timeout (60s vs 25s) and, critically, concurrency here depends on
# ai.provider config: when the content-generation provider is itself "grok"
# (this project's default), destinations are generated strictly sequentially
# (grok_max_concurrent_destinations defaults to 1) -- so there is no
# concurrent burst to detect. In that mode a single destination's own tenacity
# retry cycle (ai_content.py's 3 attempts, each up to 60s, separated by 2-8s
# backoff) spans up to ~186s end to end; window=180s reliably captures all 3
# of that one destination's own failures so the breaker trips by the time it
# gives up, protecting every remaining queued destination from repeating the
# same ~186s wasted cycle. For other providers (default concurrency 4,
# matching _GROK_SEMAPHORE), several concurrent first-attempt timeouts land
# within seconds of each other around the ~60s mark, so threshold=3 trips
# well before any of them reach a second attempt.
_DEFAULT_LLM_CIRCUIT_BREAKER_THRESHOLD = 3
_DEFAULT_LLM_CIRCUIT_BREAKER_WINDOW_SECONDS = 180.0
_DEFAULT_LLM_CIRCUIT_BREAKER_COOLDOWN_SECONDS = 45.0
_TRANSIENT_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}


class LLMCircuitOpenError(RuntimeError):
    """Raised when the LLM content-generation circuit breaker short-circuits a
    call after a recent burst of transient provider errors, instead of
    spending a full timeout+retry cycle against an already-struggling
    endpoint. Mirrors GrokCircuitOpenError in grok_search.py."""


DEFAULT_PRICING_USD_PER_1M: dict[str, dict[str, float]] = {
    "openai:gpt-4o": {"input": 5.00, "output": 15.00},
    "openai:gpt-4o-mini": {"input": 0.15, "output": 0.60},
    # Confirmed 2026-08-15 against developers.openai.com/api/docs/pricing
    # (Standard tier). Added after openai_search.py's real bandwidth test
    # showed $0.00 cost for 6 real completed calls -- the exact same class
    # of silent-cost bug found for claude-sonnet-5 and grok-4-fast earlier
    # this session, now a third time from the identical root cause (a new
    # model used before its pricing entry existed). Web search's separate
    # $10-per-1000-calls fee is NOT part of this per-token table (it's a
    # platform-level fee, not a model-level rate) -- it's now tracked
    # separately via DEFAULT_TOOL_CALL_PRICING_USD_PER_1000 below and folded
    # into UsageTracker.add()'s total, fixed 2026-08-17.
    "openai:gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "deepseek:deepseek-chat": {"input": 0.27, "output": 1.10},
    "deepseek:deepseek-reasoner": {"input": 0.55, "output": 2.19},
    # grok-latest's $2/$10 rate is confirmed against real production billing
    # (2026-08-15: a real run's token counts reproduced its exact invoiced
    # cost to the penny using this rate) -- but that only proves internal
    # consistency of our own formula, not that $2/$10 matches xAI's current
    # published rate for whatever model "latest" resolves to today. Treat as
    # empirically-grounded but not re-verified against xAI's docs each time
    # "latest" silently repoints to a new underlying model.
    "grok:grok-latest": {"input": 2.00, "output": 10.00},
    # Catalog rates, read from docs.x.ai/docs/models on 2026-08-21 (the
    # <200k-prompt tier; both models double above 200k, which our calls do
    # not reach -- the largest observed was ~24K).
    "grok:grok-4.5": {"input": 2.00, "output": 6.00},
    "grok:grok-4.6": {"input": 2.00, "output": 6.00},
    "grok:grok-4.3": {"input": 1.25, "output": 2.50},
    "grok:grok-4.20-0309-reasoning": {"input": 1.25, "output": 2.50},
    "grok:grok-4.20-0309-non-reasoning": {"input": 1.25, "output": 2.50},
    "grok:grok-4.20-multi-agent-0309": {"input": 1.25, "output": 2.50},
    "grok:grok-build-0.1": {"input": 1.00, "output": 2.00},
    # CORRECTED 2026-08-21. This entry was $0.20/$0.50 and it was wrong by
    # ~9x, which is the single largest cost-reporting error this project has
    # had. Its own comment said the figure came from a third-party
    # aggregator and was "provisional until checked against real billing" --
    # then it was trusted for six days without that check.
    #
    # The check, finally done: xAI's hourly usage export for the 2026-08-20
    # 19:00 hour isolates one run exactly (console 1,760,982 tokens against
    # our ledger's 1,760,607 -- 0.02% apart). That hour billed $3.75296,
    # i.e. $2.131/M blended, against the $0.239/M this entry implied.
    #
    # $2.00/$6.00 is the grok-4.5/4.6 catalog rate and reproduces the
    # observed bill to within 18% ($4.45 computed vs $3.75 billed). The gap
    # is consistent with xAI's cached-input discount, which this table
    # cannot model; the residual therefore OVERSTATES cost slightly, which
    # is the safe direction. "grok-4-fast" is not in xAI's model catalog at
    # all -- it is what the API reports back for an aliased request, so
    # pricing it at the served tier is the honest choice.
    "grok:grok-4-fast": {"input": 2.00, "output": 6.00},
    "anthropic:claude-3-5-sonnet-latest": {"input": 3.00, "output": 15.00},
    "anthropic:claude-3-7-sonnet-latest": {"input": 3.00, "output": 15.00},
    # Confirmed 2026-08-15 against platform.claude.com/docs/en/about-claude/pricing
    # (the introductory $2/$10 rate is now the permanent standard price -- the
    # previously scheduled Sept 1 2026 increase to $3/$15 was cancelled). Added
    # after discovering this entry's absence meant every claude-sonnet-5 call
    # this session had been silently costed at $0.00 (_estimate_cost's
    # no-match fallback), which is why real spend (verified against the
    # Anthropic console: ~9.15M input / ~327K output tokens in one day) looked
    # like "pennies" internally right up until the account ran out of credit.
    # Not yet included here: web search's separate $10-per-1000-searches
    # charge (on top of token costs) -- claude_search.py's ClaudeSearch
    # class doesn't pass a tool-call count into UsageTracker.add() yet
    # (unlike grok_search.py/openai_search.py, fixed 2026-08-17; see
    # DEFAULT_TOOL_CALL_PRICING_USD_PER_1000 above), a real but smaller
    # (~14% of reconstructed spend) remaining gap. Live verification of
    # Anthropic's usage.server_tool_use.web_search_requests field name was
    # blocked by the same exhausted-account-credit issue documented in
    # docs/design/search-provider-capability-probe.md §4.1/§9 -- confirm
    # against a real response before wiring this one up.
    "anthropic:claude-sonnet-5": {"input": 2.00, "output": 10.00},
    "gemini:gemini-1.5-pro": {"input": 3.50, "output": 10.50},
    "gemini:gemini-1.5-flash": {"input": 0.35, "output": 1.05},
}

# Both xAI and OpenAI bill their server-side web_search tool SEPARATELY from
# token usage, per actual invocation -- not per logical /v1/responses call. A
# single call that runs an agentic search loop can fire web_search multiple
# times internally (confirmed live 2026-08-17: a single Grok /v1/responses
# call returned usage.server_side_tool_usage_details.web_search_calls == 2 for
# one query that needed two search rounds), each billed separately. This is
# keyed by provider, not by model, because the fee is a platform-level tool
# charge, not a per-model token rate -- confirmed against both providers'
# pricing docs, which price web_search identically across every model on that
# platform.
#   xAI:    $5.00 per 1,000 web_search calls (docs.x.ai/developers/pricing).
#   OpenAI: $10.00 per 1,000 web_search calls (community/docs-confirmed). The
#           retrieved search content is ALSO billed as extra input tokens on
#           the next turn for multi-round agentic loops -- that part is
#           already captured by the ordinary input_tokens count this tracker
#           records, so it is not double-counted here.
# Before this table existed, UsageTracker had no field for tool-invocation
# counts at all -- every dipstick run's own [LLM-COST] summary silently
# omitted this real cost component, which is why real xAI billing (~$5/day)
# ran well above what this app's own estimator reported (~$0.40/run).
DEFAULT_TOOL_CALL_PRICING_USD_PER_1000: dict[str, float] = {
    "grok": 5.00,
    "openai": 10.00,
    # Serper, confirmed against its published prepaid tiers 2026-08-23:
    # $50/50k credits = $1.00 per 1,000, falling to $0.30 per 1,000 at the
    # largest pack. $1.00 is the entry-tier rate and therefore the
    # conservative one -- it can only over-state, which is the safe
    # direction (see grok-4-fast above for what under-stating costs).
    #
    # One credit covers a search returning up to 10 results; 11-100 costs
    # two. serper_search.py caps `num` at 10 so this rate stays correct --
    # if that cap is ever raised, this entry is wrong by 2x.
    "serper": 1.00,
}


@dataclass
class UsageRecord:
    provider: str
    model: str
    operation: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    tool_calls: int = 0
    tool_call_cost_usd: float = 0.0


class RunCostCeilingExceeded(RuntimeError):
    """A run tried to spend past its configured ceiling and was stopped.

    Deliberately not a transient error: retrying, failing over to another
    provider, or waiting changes nothing, and each of those would spend more
    money answering a signal that says stop spending money.
    """

    def __init__(self, spent_usd: float, ceiling_usd: float) -> None:
        self.spent_usd = float(spent_usd)
        self.ceiling_usd = float(ceiling_usd)
        super().__init__(
            f"Run cost ceiling reached: ${self.spent_usd:.4f} spent against a "
            f"${self.ceiling_usd:.2f} ceiling (ai.run_cost_ceiling_usd). "
            "No further model calls will be made."
        )


class UsageTracker:
    def __init__(
        self,
        pricing_map: dict[str, dict[str, float]] | None = None,
        tool_call_pricing_map: dict[str, float] | None = None,
        ceiling_usd: float | None = None,
    ) -> None:
        self._pricing = pricing_map or DEFAULT_PRICING_USD_PER_1M
        self._tool_call_pricing = tool_call_pricing_map or DEFAULT_TOOL_CALL_PRICING_USD_PER_1000
        self._records: list[UsageRecord] = []
        # None or <= 0 means no ceiling, which stays the default: a guard that
        # stops a build is only wanted by someone who asked for it.
        self._ceiling_usd = float(ceiling_usd) if ceiling_usd and float(ceiling_usd) > 0 else None
        # Models seen with no pricing entry. Their calls are costed at $0.00,
        # so a run containing one has an understated total -- and a ceiling
        # cannot guard spend it prices at nothing. Reported in summary().
        self._unpriced_models: set[str] = set()
        # Sticky, so the reason a run stopped survives the exception being
        # raised and re-raised through a pipeline that does not catch it.
        self._ceiling_hit = False
        self._lock = threading.Lock()
        # Keys already warned about, so a blind spot is reported once per run
        # rather than once per call. Guarded by _lock -- _warn_once is reached
        # from the parallel stages.
        self._warned: set[str] = set()

    def _warn_once(self, key: str, message: str, *args: Any) -> None:
        with self._lock:
            if key in self._warned:
                return
            self._warned.add(key)
        logger.warning(message, *args)

    def add(
        self,
        provider: str,
        model: str,
        operation: str,
        prompt_tokens: int,
        completion_tokens: int,
        tool_calls: int = 0,
    ) -> None:
        total_tokens = int(prompt_tokens) + int(completion_tokens)
        token_cost = self._estimate_cost(provider, model, int(prompt_tokens), int(completion_tokens))
        tool_call_cost = self._estimate_tool_call_cost(provider, int(tool_calls))
        estimated = round(token_cost + tool_call_cost, 6)
        record = UsageRecord(
            provider=provider,
            model=model,
            operation=operation,
            prompt_tokens=int(prompt_tokens),
            completion_tokens=int(completion_tokens),
            total_tokens=total_tokens,
            estimated_cost_usd=estimated,
            tool_calls=int(tool_calls),
            tool_call_cost_usd=tool_call_cost,
        )
        with self._lock:
            self._records.append(record)

    def total_cost_usd(self) -> float:
        """Estimated spend so far. Cheap enough to call before every request."""
        with self._lock:
            return round(sum(r.estimated_cost_usd for r in self._records), 6)

    @property
    def ceiling_usd(self) -> float | None:
        return self._ceiling_usd

    @property
    def ceiling_hit(self) -> bool:
        """Whether this run was ever refused a call for cost.

        Read at finalize time: nothing in the pipeline catches the exception,
        so without this the ledger would record only that the process exited,
        which is the one explanation that is never useful.
        """
        return self._ceiling_hit

    def check_ceiling(self) -> None:
        """Refuse to authorize another call once the ceiling is reached.

        Checked *before* the request rather than after it, which is the whole
        point: a guard that notices afterwards has already paid. It means the
        ceiling is crossed at most once, by the call that reaches it.
        """
        if self._ceiling_usd is None:
            return
        spent = self.total_cost_usd()
        if spent >= self._ceiling_usd:
            self._ceiling_hit = True
            raise RunCostCeilingExceeded(spent, self._ceiling_usd)

    def _estimate_cost(self, provider: str, model: str, in_tokens: int, out_tokens: int) -> float:
        key = f"{provider}:{model}"
        prices = self._pricing.get(key)
        if not prices:
            # Try prefix matching for versioned model names e.g. "gpt-4o-mini-2024-07-18" → "gpt-4o-mini".
            # Must pick the LONGEST matching prefix, not the first in dict-insertion
            # order -- "gpt-4o-mini-2024-07-18" starts with both "gpt-4o" and
            # "gpt-4o-mini", and matching "gpt-4o" first (as plain iteration would,
            # since it's listed before "gpt-4o-mini") silently applies the full
            # gpt-4o price ($5/$15) instead of the mini price ($0.15/$0.60) -- a
            # ~29x cost overestimate with no error or warning.
            best_match_len = -1
            for pricing_key, pricing_val in self._pricing.items():
                p, m = pricing_key.split(":", 1)
                if p == provider and model.startswith(m) and len(m) > best_match_len:
                    prices = pricing_val
                    best_match_len = len(m)
        if not prices:
            # Silently returning 0.0 here is how token cost reported $0.00/day
            # against $24/day of real xAI billing on 2026-08-16 and 08-17: the
            # configured model had no pricing entry and nothing said so. The
            # ledger looked healthy because search fees still totalled up.
            with self._lock:
                self._unpriced_models.add(key)
            self._warn_once(
                f"unpriced-model:{key}",
                "Cost reporting blind spot: no pricing entry for %r (and no prefix match). "
                "Token cost is being recorded as $0.00 for every call to this model. Add it "
                "to DEFAULT_PRICING_USD_PER_1M or the config pricing map.",
                key,
            )
            return 0.0
        in_cost = (in_tokens / 1_000_000) * prices.get("input", 0.0)
        out_cost = (out_tokens / 1_000_000) * prices.get("output", 0.0)
        return round(in_cost + out_cost, 6)

    def _estimate_tool_call_cost(self, provider: str, tool_calls: int) -> float:
        if tool_calls <= 0:
            return 0.0
        price_per_1000 = self._tool_call_pricing.get(provider)
        if not price_per_1000:
            # Only reachable once the provider HAS reported tool calls, so this
            # is always a real unbilled cost, never a no-op path.
            self._warn_once(
                f"unpriced-tool-calls:{provider}",
                "Cost reporting blind spot: %d tool call(s) recorded for provider %r, which "
                "has no entry in the tool-call pricing table. Search fees are being recorded "
                "as $0.00. Add it to DEFAULT_TOOL_CALL_PRICING_USD_PER_1000.",
                tool_calls, provider,
            )
            return 0.0
        return round((tool_calls / 1000) * price_per_1000, 6)

    def _warn_on_missing_tool_counts(self) -> None:
        """Flag a provider whose search calls never reported a tool-invocation count.

        Checked once per run rather than per call, deliberately. A single
        search call legitimately reports zero tool invocations -- the model
        can decide the answer needs no search -- so a per-call warning would
        fire constantly on grok, which reports the count correctly. Across a
        whole run, though, a search-performing provider that reports zero
        every single time is not being frugal: its usage field is not being
        read at all, and its per-invocation search fees (the largest single
        line on the real bill) are silently absent from the ledger.

        This is not hypothetical: claude_search.py records token usage but
        never reads Anthropic's tool-count field, so it would report $0.00 of
        search fees for an entire run. It is latent only because the current
        config routes search to grok.
        """
        searched: dict[str, int] = {}
        for rec in self._records:
            if "search" not in rec.operation:
                continue
            searched[rec.provider] = searched.get(rec.provider, 0) + rec.tool_calls
        for provider, tool_calls in searched.items():
            if tool_calls > 0:
                continue
            self._warn_once(
                f"no-tool-count:{provider}",
                "Cost reporting blind spot: provider %r ran search operations this run but "
                "reported ZERO tool invocations across all of them. Per-invocation search "
                "fees are absent from this run's cost estimate. The provider's usage block "
                "almost certainly has a tool-count field that is not being read.",
                provider,
            )

    def summary(self) -> dict[str, Any]:
        by_model: dict[str, dict[str, Any]] = {}
        for rec in self._records:
            key = f"{rec.provider}:{rec.model}"
            bucket = by_model.setdefault(
                key,
                {
                    "provider": rec.provider,
                    "model": rec.model,
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "tool_calls": 0,
                    "tool_call_cost_usd": 0.0,
                    "estimated_cost_usd": 0.0,
                },
            )
            bucket["calls"] += 1
            bucket["prompt_tokens"] += rec.prompt_tokens
            bucket["completion_tokens"] += rec.completion_tokens
            bucket["total_tokens"] += rec.total_tokens
            bucket["tool_calls"] += rec.tool_calls
            bucket["tool_call_cost_usd"] = round(bucket["tool_call_cost_usd"] + rec.tool_call_cost_usd, 6)
            bucket["estimated_cost_usd"] = round(bucket["estimated_cost_usd"] + rec.estimated_cost_usd, 6)

        self._warn_on_missing_tool_counts()

        rows = sorted(by_model.values(), key=lambda x: x["estimated_cost_usd"], reverse=True)
        total = round(sum(x["estimated_cost_usd"] for x in rows), 6)
        total_tool_call_cost = round(sum(x["tool_call_cost_usd"] for x in rows), 6)
        return {
            "models": rows,
            "total_calls": len(self._records),
            "total_estimated_cost_usd": total,
            # Non-empty means the total above is a floor rather than an
            # estimate: every call to these models was costed at $0.00. On
            # 2026-08-16 that gap read as $0.00/day against real billing.
            "unpriced_models": sorted(self._unpriced_models),
            "total_tool_call_cost_usd": total_tool_call_cost,
            "records": [
                {
                    "provider": r.provider,
                    "model": r.model,
                    "operation": r.operation,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "total_tokens": r.total_tokens,
                    "tool_calls": r.tool_calls,
                    "tool_call_cost_usd": r.tool_call_cost_usd,
                    "estimated_cost_usd": r.estimated_cost_usd,
                }
                for r in self._records
            ],
        }


class MultiLLMClient:
    def __init__(
        self,
        config_path: str | Path = "config.yaml",
        llm_overrides: dict[str, Any] | None = None,
        usage_tracker: "UsageTracker | None" = None,
    ) -> None:
        import yaml

        with Path(config_path).open(encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        ai_cfg = cfg.get("ai", {})
        legacy_cfg = cfg.get("azure_openai", {})
        llm_cfg = llm_overrides or {}

        self.provider = (llm_cfg.get("provider") or ai_cfg.get("provider") or "azure_openai").lower()
        self.model = (
            llm_cfg.get("model")
            or ai_cfg.get("model")
            or os.environ.get("OPENAI_MODEL")
            or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
            or "gpt-4o"
        )
        self.model = self._normalize_model_for_provider(self.provider, str(self.model or "").strip())
        self.temperature = float(llm_cfg.get("temperature", ai_cfg.get("temperature", legacy_cfg.get("temperature", 0.7))))
        self.max_tokens = int(llm_cfg.get("max_tokens", ai_cfg.get("max_tokens", legacy_cfg.get("max_tokens", 4096))))

        # Shared across a primary instance and its fallback (if any) so cost
        # accounting stays centralized regardless of which provider actually
        # served a given call -- a fresh per-instance tracker would silently
        # lose track of spend incurred during failover.
        self.usage_tracker = usage_tracker or UsageTracker(
            ceiling_usd=llm_cfg.get("run_cost_ceiling_usd", ai_cfg.get("run_cost_ceiling_usd")),
        )
        self._fallback_client: "MultiLLMClient | None" = None
        fallback_provider = str(
            llm_cfg.get("fallback_provider") or ai_cfg.get("fallback_provider") or ""
        ).strip().lower()
        if fallback_provider and fallback_provider != self.provider:
            fallback_model = llm_cfg.get("fallback_model") or ai_cfg.get("fallback_model")
            # Constructed eagerly (not on first failover) so a misconfigured
            # or missing fallback API key fails loudly at startup, matching
            # the eager-key-check philosophy already used for anthropic/
            # gemini above -- not silently discovered deep into a run at the
            # exact moment the primary is already struggling.
            self._fallback_client = MultiLLMClient(
                config_path,
                llm_overrides={"provider": fallback_provider, "model": fallback_model},
                usage_tracker=self.usage_tracker,
            )
        elif fallback_provider:
            logger.warning(
                "ai.fallback_provider ('%s') is the same as ai.provider; ignoring (no fallback configured).",
                fallback_provider,
            )

        self._json_cache: dict[tuple[str, str, str, str, float, int], dict[str, Any]] = {}
        self._json_cache_lock = threading.Lock()
        self._circuit_breaker_threshold = max(
            1, int(os.environ.get("LLM_CIRCUIT_BREAKER_THRESHOLD", str(_DEFAULT_LLM_CIRCUIT_BREAKER_THRESHOLD)))
        )
        self._circuit_breaker_window_seconds = float(
            os.environ.get("LLM_CIRCUIT_BREAKER_WINDOW_SECONDS", str(_DEFAULT_LLM_CIRCUIT_BREAKER_WINDOW_SECONDS))
        )
        self._circuit_breaker_cooldown_seconds = float(
            os.environ.get("LLM_CIRCUIT_BREAKER_COOLDOWN_SECONDS", str(_DEFAULT_LLM_CIRCUIT_BREAKER_COOLDOWN_SECONDS))
        )
        self._circuit_breaker_lock = threading.Lock()
        self._circuit_breaker_failure_times: list[float] = []
        self._circuit_breaker_open_until: float = 0.0
        self._router = LLMRouter()
        self._custom_provider = self._router.get_provider(self.provider, model=self.model)

        if self._custom_provider is not None:
            self.model = getattr(self._custom_provider, "model", self.model)
            return

        if self.provider == "openai":
            self._openai_client = OpenAI(
                api_key=os.environ["OPENAI_API_KEY"],
                base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            )
        elif self.provider == "deepseek":
            self._openai_client = OpenAI(
                api_key=os.environ["DEEPSEEK_API_KEY"],
                base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            )
        elif self.provider == "azure_openai":
            self._azure_client = AzureOpenAI(
                azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                api_key=os.environ["AZURE_OPENAI_API_KEY"],
                api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
            )
            self.model = os.environ.get("AZURE_OPENAI_DEPLOYMENT", self.model)
        elif self.provider == "anthropic":
            # Accessed eagerly (not just inside _call_anthropic) so a missing
            # key fails at construct time -- matching openai/azure_openai
            # above -- instead of after Stage 1/2 already ran and the first
            # generate_json call is made deep into the run.
            _ = os.environ["ANTHROPIC_API_KEY"]
        elif self.provider == "gemini":
            _ = os.environ["GEMINI_API_KEY"]
        elif self.provider != "grok":
            raise ValueError(f"Unsupported LLM provider: {self.provider}")
        # provider == "grok" is always intercepted by the _custom_provider
        # early-return above (LLMRouter always resolves "grok"), so no grok
        # branch is needed here.

    @staticmethod
    def _provider_default_model(provider: str) -> str:
        normalized = str(provider or "").strip().lower()
        if normalized == "openai":
            return "gpt-4o-mini"
        if normalized == "deepseek":
            return "deepseek-chat"
        if normalized == "grok":
            return "grok-latest"
        if normalized == "gemini":
            return "gemini-1.5-flash"
        if normalized == "anthropic":
            return "claude-3-5-sonnet-latest"
        if normalized == "azure_openai":
            return "gpt-4o-mini"
        return "gpt-4o-mini"

    # Model-name prefixes that identify which provider a model string actually
    # belongs to. Used symmetrically for every provider so a leftover model
    # name from switching providers (e.g. provider: anthropic with a stale
    # model: gpt-4o-mini) is caught here instead of failing deep in the first
    # API call.
    _PROVIDER_MODEL_PREFIXES: dict[str, tuple[str, ...]] = {
        "openai": ("gpt-", "o1-", "o3-", "o4-"),
        "deepseek": ("deepseek-",),
        "azure_openai": ("gpt-", "o1-", "o3-", "o4-"),
        "grok": ("grok-",),
        "gemini": ("gemini-",),
        "anthropic": ("claude-",),
    }
    _PROVIDER_MODEL_ENV_VAR: dict[str, str] = {
        "openai": "OPENAI_MODEL",
        "deepseek": "DEEPSEEK_MODEL",
        "azure_openai": "AZURE_OPENAI_DEPLOYMENT",
        "grok": "XAI_MODEL",
        "gemini": "GEMINI_MODEL",
        "anthropic": "ANTHROPIC_MODEL",
    }

    @classmethod
    def _normalize_model_for_provider(cls, provider: str, model: str) -> str:
        normalized_provider = str(provider or "").strip().lower()
        requested_model = str(model or "").strip()
        if not requested_model:
            return cls._provider_default_model(normalized_provider)
        model_lower = requested_model.lower()

        expected_prefixes = cls._PROVIDER_MODEL_PREFIXES.get(normalized_provider)
        if expected_prefixes and model_lower.startswith(expected_prefixes):
            return requested_model

        # Only override when the model is recognizably from a *different*
        # provider's family -- an unrecognized/custom model name for this
        # provider (e.g. a fine-tune or new model not yet in the table) must
        # pass through untouched rather than being silently overridden.
        belongs_to_other_family = any(
            other_provider != normalized_provider and model_lower.startswith(prefixes)
            for other_provider, prefixes in cls._PROVIDER_MODEL_PREFIXES.items()
        )
        if not belongs_to_other_family:
            return requested_model

        env_var = cls._PROVIDER_MODEL_ENV_VAR.get(normalized_provider, "")
        fallback = (
            (str(os.environ.get(env_var, "") or "").strip() if env_var else "")
            or cls._provider_default_model(normalized_provider)
        )
        logger.warning(
            "Model '%s' is incompatible with provider '%s'; using '%s' instead.",
            requested_model,
            normalized_provider,
            fallback,
        )
        return fallback

    def is_circuit_open(self) -> bool:
        """Non-raising peek at circuit-breaker state, for callers that want to
        skip optional/supplementary work during a known-bad period rather than
        compounding it. Mirrors GrokSearch.is_circuit_open."""
        with self._circuit_breaker_lock:
            open_until = self._circuit_breaker_open_until
        return open_until > time.monotonic()

    def _circuit_breaker_check(self) -> None:
        """Fail fast if a recent burst of transient provider errors tripped
        the breaker, instead of spending a full timeout+retry cycle against an
        already-struggling endpoint. See the module-level comment on
        _DEFAULT_LLM_CIRCUIT_BREAKER_THRESHOLD for the sizing rationale."""
        with self._circuit_breaker_lock:
            open_until = self._circuit_breaker_open_until
        remaining = open_until - time.monotonic()
        if remaining > 0:
            raise LLMCircuitOpenError(
                f"LLM circuit breaker open ({remaining:.1f}s remaining) after repeated transient "
                f"'{self.provider}' errors"
            )

    def _record_circuit_breaker_outcome(self, *, transient_failure: bool) -> None:
        now = time.monotonic()
        with self._circuit_breaker_lock:
            if not transient_failure:
                self._circuit_breaker_failure_times.clear()
                self._circuit_breaker_open_until = 0.0
                return
            cutoff = now - self._circuit_breaker_window_seconds
            self._circuit_breaker_failure_times = [
                t for t in self._circuit_breaker_failure_times if t >= cutoff
            ]
            self._circuit_breaker_failure_times.append(now)
            if len(self._circuit_breaker_failure_times) >= self._circuit_breaker_threshold:
                self._circuit_breaker_open_until = now + self._circuit_breaker_cooldown_seconds

    @staticmethod
    def _is_transient_llm_error(exc: BaseException) -> bool:
        """True for network/availability failures worth counting toward the
        circuit breaker (timeouts, connection failures, 429/5xx) -- false for
        auth errors, bad requests, or anything else retrying won't fix."""
        if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
            return True
        if isinstance(exc, requests.exceptions.HTTPError):
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            return status_code in _TRANSIENT_HTTP_STATUS_CODES
        if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
            return True
        if isinstance(exc, openai.APIStatusError):
            return getattr(exc, "status_code", None) in _TRANSIENT_HTTP_STATUS_CODES
        return False

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        operation: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        temp = self.temperature if temperature is None else temperature
        tok = self.max_tokens if max_tokens is None else max_tokens
        cache_key = (self.provider, self.model, system_prompt, user_prompt, temp, tok)

        with self._json_cache_lock:
            cached = self._json_cache.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)

        # After the cache and before any spend, including before failover: a
        # cached answer costs nothing and should still be served once the
        # ceiling is reached, and failing over to a second provider is still
        # spending money.
        self.usage_tracker.check_ceiling()

        if self._fallback_client is not None and self.is_circuit_open():
            logger.warning(
                "LLM circuit breaker open for '%s/%s'; failing over to '%s/%s' for this call.",
                self.provider,
                self.model,
                self._fallback_client.provider,
                self._fallback_client.model,
            )
            return self._fallback_client.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                operation=operation,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        self._circuit_breaker_check()
        try:
            if self._custom_provider is not None:
                text, usage = self._call_custom_provider(system_prompt, user_prompt, temp, tok)
            elif self.provider in {"openai", "deepseek"}:
                text, usage = self._call_openai(system_prompt, user_prompt, temp, tok)
            elif self.provider == "azure_openai":
                text, usage = self._call_azure(system_prompt, user_prompt, temp, tok)
            elif self.provider == "anthropic":
                text, usage = self._call_anthropic(system_prompt, user_prompt, temp, tok)
            else:
                text, usage = self._call_gemini(system_prompt, user_prompt, temp, tok)
        except Exception as exc:
            if self._is_transient_llm_error(exc):
                self._record_circuit_breaker_outcome(transient_failure=True)
            raise
        self._record_circuit_breaker_outcome(transient_failure=False)
        used_model = usage.get("model", self.model)

        self.usage_tracker.add(
            provider=self.provider,
            model=used_model,
            operation=operation,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )
        parsed = _extract_json_object(text)
        with self._json_cache_lock:
            self._json_cache[cache_key] = copy.deepcopy(parsed)
        return parsed

    def usage_summary(self) -> dict[str, Any]:
        return self.usage_tracker.summary()

    def _call_custom_provider(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int) -> tuple[str, dict[str, Any]]:
        return self._custom_provider.create_json_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _call_openai(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int) -> tuple[str, dict[str, Any]]:
        resp = self._openai_client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        usage = resp.usage
        return (
            resp.choices[0].message.content,
            {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "model": getattr(resp, "model", self.model),
            },
        )

    def _call_azure(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int) -> tuple[str, dict[str, Any]]:
        resp = self._azure_client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        usage = resp.usage
        return (
            resp.choices[0].message.content,
            {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "model": getattr(resp, "model", self.model),
            },
        )

    def _call_anthropic(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int) -> tuple[str, dict[str, Any]]:
        headers = {
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": os.environ.get("ANTHROPIC_API_VERSION", "2023-06-01"),
            "content-type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            # system_prompt is the same static template file read once at
            # startup and reused verbatim for every call in the run -- marking
            # it as an ephemeral cache breakpoint lets Anthropic serve repeat
            # calls from cache instead of re-billing the full prefix each time.
            "system": [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": user_prompt}],
        }
        resp = requests.post(
            os.environ.get("ANTHROPIC_API_BASE", "https://api.anthropic.com/v1/messages"),
            headers=headers,
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("stop_reason") == "max_tokens":
            logger.warning(
                "Anthropic response for model '%s' was truncated (stop_reason=max_tokens, "
                "max_tokens=%s) -- the JSON is likely incomplete and will fail to parse. "
                "Raise max_tokens for this operation.",
                self.model,
                max_tokens,
            )
        text_chunks = [c.get("text", "") for c in data.get("content", []) if c.get("type") == "text"]
        usage = data.get("usage", {})
        # Cached-prefix tokens are reported in separate fields, not folded
        # into input_tokens -- omitting them would make usage tracking blind
        # to most of the real input cost once caching kicks in.
        prompt_tokens = (
            int(usage.get("input_tokens", 0) or 0)
            + int(usage.get("cache_creation_input_tokens", 0) or 0)
            + int(usage.get("cache_read_input_tokens", 0) or 0)
        )
        return (
            "\n".join(text_chunks),
            {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": int(usage.get("output_tokens", 0) or 0),
                "model": data.get("model", self.model),
            },
        )

    def _call_gemini(self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int) -> tuple[str, dict[str, Any]]:
        api_key = os.environ["GEMINI_API_KEY"]
        base = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com")
        url = f"{base}/v1beta/models/{self.model}:generateContent?key={api_key}"
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        }
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        if any(cand.get("finishReason") == "MAX_TOKENS" for cand in data.get("candidates", [])):
            logger.warning(
                "Gemini response for model '%s' was truncated (finishReason=MAX_TOKENS, "
                "maxOutputTokens=%s) -- the JSON is likely incomplete and will fail to parse. "
                "Raise max_tokens for this operation.",
                self.model,
                max_tokens,
            )

        text = ""
        for cand in data.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                if "text" in part:
                    text += part["text"]

        usage = data.get("usageMetadata", {})
        return (
            text,
            {
                "prompt_tokens": int(usage.get("promptTokenCount", 0) or 0),
                "completion_tokens": int(usage.get("candidatesTokenCount", 0) or 0),
                "model": self.model,
            },
        )


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("LLM returned empty response")

    if raw.startswith("```"):
        lines = [ln for ln in raw.splitlines() if not ln.strip().startswith("```")]
        raw = "\n".join(lines).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    if start == -1:
        raise ValueError("LLM response does not contain a JSON object")

    depth = 0
    in_str = False
    esc = False
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(raw[start:idx + 1])

    raise ValueError("Unable to extract a complete JSON object from LLM response")
