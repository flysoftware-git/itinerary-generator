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
    "deepseek:deepseek-chat": {"input": 0.27, "output": 1.10},
    "deepseek:deepseek-reasoner": {"input": 0.55, "output": 2.19},
    "grok:grok-latest": {"input": 2.00, "output": 10.00},
    "grok:grok-4.5": {"input": 3.00, "output": 15.00},
    "anthropic:claude-3-5-sonnet-latest": {"input": 3.00, "output": 15.00},
    "anthropic:claude-3-7-sonnet-latest": {"input": 3.00, "output": 15.00},
    "gemini:gemini-1.5-pro": {"input": 3.50, "output": 10.50},
    "gemini:gemini-1.5-flash": {"input": 0.35, "output": 1.05},
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


class UsageTracker:
    def __init__(self, pricing_map: dict[str, dict[str, float]] | None = None) -> None:
        self._pricing = pricing_map or DEFAULT_PRICING_USD_PER_1M
        self._records: list[UsageRecord] = []
        self._lock = threading.Lock()

    def add(
        self,
        provider: str,
        model: str,
        operation: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        total_tokens = int(prompt_tokens) + int(completion_tokens)
        estimated = self._estimate_cost(provider, model, int(prompt_tokens), int(completion_tokens))
        record = UsageRecord(
            provider=provider,
            model=model,
            operation=operation,
            prompt_tokens=int(prompt_tokens),
            completion_tokens=int(completion_tokens),
            total_tokens=total_tokens,
            estimated_cost_usd=estimated,
        )
        with self._lock:
            self._records.append(record)

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
            return 0.0
        in_cost = (in_tokens / 1_000_000) * prices.get("input", 0.0)
        out_cost = (out_tokens / 1_000_000) * prices.get("output", 0.0)
        return round(in_cost + out_cost, 6)

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
                    "estimated_cost_usd": 0.0,
                },
            )
            bucket["calls"] += 1
            bucket["prompt_tokens"] += rec.prompt_tokens
            bucket["completion_tokens"] += rec.completion_tokens
            bucket["total_tokens"] += rec.total_tokens
            bucket["estimated_cost_usd"] = round(bucket["estimated_cost_usd"] + rec.estimated_cost_usd, 6)

        rows = sorted(by_model.values(), key=lambda x: x["estimated_cost_usd"], reverse=True)
        total = round(sum(x["estimated_cost_usd"] for x in rows), 6)
        return {
            "models": rows,
            "total_calls": len(self._records),
            "total_estimated_cost_usd": total,
            "records": [
                {
                    "provider": r.provider,
                    "model": r.model,
                    "operation": r.operation,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "total_tokens": r.total_tokens,
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
        self.usage_tracker = usage_tracker or UsageTracker()
        self._config_path = config_path
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
