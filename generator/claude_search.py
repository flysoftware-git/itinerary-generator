"""
claude_search.py — Anthropic Claude-based web search provider.

Second working search/harvest provider alongside grok_search.py's GrokSearch,
matching its chat_completion()/search()/is_circuit_open() surface so
url_discovery.py and cultural_events.py can select it via config
(url_discovery.search_provider / cultural_events.search_provider) without any
call-site changes -- a drop-in swap, not a parallel code path.

Uses Claude's web_search_20260318 tool (GA since April 2026) on the Messages
API. Probe evidence (2026-08-14, docs/design/search-provider-capability-probe.md
§3.3): 14/15 citation-matched URLs (93%) across 4 real multi-destination test
cases -- close behind Grok's 100% and well ahead of OpenAI (0%) and Gemini
(19%) on the same task.

Docs:     https://docs.anthropic.com/
Endpoint: https://api.anthropic.com/v1/messages
Env vars: ANTHROPIC_API_KEY, ANTHROPIC_MODEL, ANTHROPIC_API_VERSION,
          ANTHROPIC_API_BASE (all shared with llm_client.py's content-
          generation Anthropic path -- same account, same key).
"""
from __future__ import annotations
import json, logging, os, threading
import time
from typing import Any
import requests

logger = logging.getLogger(__name__)
CLAUDE_ENDPOINT_DEFAULT = "https://api.anthropic.com/v1/messages"
# max_uses caps how many searches Claude may issue per call -- without a cap
# a single harvest prompt could fan out into many searches, ballooning both
# latency and cost for no benefit to a batch-of-N-items harvest prompt.
# Lowered from 5 to 1 (2026-08-15) after reconstructing real spend from the
# Anthropic console -- 9.15M input tokens / 361 web searches in one day
# (~25K tokens/search) is consistent with agentic multi-round search
# compounding context on every internal step, not a single query, and web
# search itself is billed separately ($10/1000 searches, platform.claude.com
# pricing docs) on top of token costs. 1 keeps each call to a single search
# round while this is characterized further; raise deliberately, not by
# drifting back up, once there's real evidence 1 round is insufficient
# coverage rather than just cheaper.
CLAUDE_SEARCH_TOOL = {"type": "web_search_20260318", "name": "web_search", "max_uses": 1}
# Originally 150s: the 90s probe timeout was too short for CLAUDE_SEARCH_TOOL's
# old max_uses=5 (up to 5 agentic search rounds compounding per call), so this
# was raised to 150s to cover that worst case (search-provider-capability-
# probe.md §3.3). Lowered to 60s (2026-08-15) now that max_uses=1 caps every
# call to a single search round -- a call that's still stuck past 60s under
# the new, much narrower call shape is a stronger signal of genuine trouble
# than "needed more of its compounding budget," and the old 150s ceiling was
# actively hurting failure detection: under _CLAUDE_SEMAPHORE's 4-slot
# concurrency cap, a stuck first wave of calls could burn the full 150s
# before the circuit breaker even accumulated enough failures to trip. Not
# yet backed by real single-round timing data at the new call shape --
# revisit with real numbers once a few runs have gone through it.
_DEFAULT_TIMEOUT_SECONDS = 60
# Cut from 2 (2026-08-15, alongside the matching GrokSearch fix): with
# _CLAUDE_SEMAPHORE capping concurrency at 4, 2 retries meant a single
# genuinely-slow-but-working call could occupy a slot for up to 3x the
# per-attempt timeout (180s) before giving up -- and if Anthropic bills a
# completed agentic search turn regardless of whether the client stayed
# connected to receive it, each abandoned retry is a second (or third) real
# charge for the same logical query, not free insurance. 1 retry keeps
# resilience for a single transient blip without that multiplier.
_DEFAULT_NETWORK_RETRIES = 1
# Same derivation as GrokSearch's XAI_CIRCUIT_BREAKER_* defaults (see
# grok_search.py) -- sized to a burst of concurrent-slot timeouts, not
# individual failures. Kept in its own ANTHROPIC_SEARCH_* namespace, separate
# from llm_client.py's generic LLM_CIRCUIT_BREAKER_* (content generation) and
# grok_search.py's XAI_CIRCUIT_BREAKER_* -- these are three independently
# tripped breakers for three different call paths.
#
# window widened 30s->70s (2026-08-15): this was already undersized relative
# to the 60s per-attempt timeout above (set earlier the same day, 150s->60s,
# without revisiting window) -- a burst of concurrent-slot timeouts lands
# roughly every ~60-62s (attempt timeout + backoff), which a 30s window
# could miss entirely, silently disabling the breaker exactly like the
# already-fixed Grok case. 70s leaves margin above the ~62s expected round.
_DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 4
_DEFAULT_CIRCUIT_BREAKER_WINDOW_SECONDS = 70.0
_DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS = 15.0
_DEFAULT_MAX_TOKENS = 4096

# Global semaphore: cap concurrent connections to the Anthropic API, same
# rationale as GrokSearch's _GROK_SEMAPHORE.
_CLAUDE_SEMAPHORE = threading.Semaphore(4)


class ClaudeCircuitOpenError(requests.RequestException):
    """Raised when the circuit breaker short-circuits a call after a recent burst
    of transient Claude errors, instead of letting the caller wait out a full
    timeout+retry cycle against an already-struggling endpoint."""


def _extract_json_object(text: str) -> dict[str, Any]:
    """Duplicated from grok_search.py rather than imported -- llm_client.py
    already independently duplicates the same helper (provider-agnostic JSON
    extraction from LLM text), so this follows established precedent rather
    than introducing a new cross-module private coupling."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("LLM returned empty response")

    if raw.startswith("```"):
        lines = [line for line in raw.splitlines() if not line.strip().startswith("```")]
        raw = "\n".join(lines).strip()

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("LLM response was not a JSON object")
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
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                parsed = json.loads(raw[start:idx + 1])
                if isinstance(parsed, dict):
                    return parsed
                raise ValueError("LLM response was not a JSON object")

    raise ValueError("Unable to extract a complete JSON object from LLM response")


class ClaudeSearch:
    """
    Thread-safe Anthropic Claude-based search provider.

    Uses Claude's Messages API with the web_search_20260318 tool to perform
    real web search. Matches GrokSearch's public surface
    (chat_completion/search/is_circuit_open) exactly so callers can swap
    providers via config with no other code changes.

    Each calling thread gets its own ``requests.Session`` via
    ``threading.local()``, so the instance is safe to share across a
    ``ThreadPoolExecutor``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        network_retries: int = _DEFAULT_NETWORK_RETRIES,
        usage_tracker: Any | None = None,
        usage_operation_prefix: str = "claude_search",
    ) -> None:
        self._api_key = api_key or os.environ["ANTHROPIC_API_KEY"]
        self._model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
        self._api_version = os.environ.get("ANTHROPIC_API_VERSION", "2023-06-01")
        self._endpoint = os.environ.get("ANTHROPIC_API_BASE", CLAUDE_ENDPOINT_DEFAULT)
        self._timeout = int(os.environ.get("ANTHROPIC_SEARCH_TIMEOUT_SECONDS", str(timeout_seconds)))
        self._network_retries = max(
            0, int(os.environ.get("ANTHROPIC_SEARCH_NETWORK_RETRIES", str(network_retries)))
        )
        self._session_local = threading.local()
        self._usage_tracker = usage_tracker
        self._usage_operation_prefix = usage_operation_prefix
        self._warning_state_lock = threading.Lock()
        self._warning_state: dict[str, dict[str, float | int]] = {}
        self._warning_cooldown_seconds = int(os.environ.get("ANTHROPIC_SEARCH_WARNING_COOLDOWN_SECONDS", "30"))
        self._circuit_breaker_threshold = max(
            1,
            int(
                os.environ.get(
                    "ANTHROPIC_SEARCH_CIRCUIT_BREAKER_THRESHOLD", str(_DEFAULT_CIRCUIT_BREAKER_THRESHOLD)
                )
            ),
        )
        self._circuit_breaker_window_seconds = float(
            os.environ.get(
                "ANTHROPIC_SEARCH_CIRCUIT_BREAKER_WINDOW_SECONDS", str(_DEFAULT_CIRCUIT_BREAKER_WINDOW_SECONDS)
            )
        )
        self._circuit_breaker_cooldown_seconds = float(
            os.environ.get(
                "ANTHROPIC_SEARCH_CIRCUIT_BREAKER_COOLDOWN_SECONDS", str(_DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS)
            )
        )
        self._circuit_breaker_lock = threading.Lock()
        self._circuit_breaker_failure_times: list[float] = []
        self._circuit_breaker_open_until: float = 0.0
        # Half-open recovery state -- see GrokSearch._circuit_breaker_check
        # for the full rationale (2026-08-15): exactly one caller becomes
        # the recovery probe when cooldown elapses, instead of the full
        # concurrent backlog rushing back in at once. Lease-bounded rather
        # than a plain flag so a probe that exits through an unanticipated
        # path can't wedge the breaker in "probe permanently in flight."
        self._circuit_breaker_probe_claimed_until: float = 0.0
        self._circuit_breaker_probe_lease_seconds = (
            self._timeout * (self._network_retries + 1) + 5.0
        )
        self._circuit_breaker_trip_count: int = 0
        self._circuit_breaker_total_open_seconds: float = 0.0
        self._circuit_breaker_trip_opened_at: float = 0.0
        self._max_tokens = int(os.environ.get("ANTHROPIC_SEARCH_MAX_TOKENS", str(_DEFAULT_MAX_TOKENS)))

    # ── Thread-local session ─────────────────────────────────────────────────

    def _get_session(self) -> requests.Session:
        if not hasattr(self._session_local, "session"):
            s = requests.Session()
            s.headers.update({
                "x-api-key": self._api_key,
                "anthropic-version": self._api_version,
                "content-type": "application/json",
            })
            self._session_local.session = s
        return self._session_local.session

    # ── Core search ──────────────────────────────────────────────────────────

    def search(self, query: str, count: int | None = None) -> list[dict[str, Any]]:
        """
        Execute a real web search via Claude.

        Returns a list of ``{name, snippet, url}`` dicts (up to ``count``
        results). Returns ``[]`` on any HTTP, parse, or API error so callers
        never need to guard against exceptions.
        """
        try:
            results = self._claude_search(query, attempt=1)
            return results[:count] if count else results
        except Exception as exc:
            logger.warning("Claude search error for %r: %s", query[:60], exc)
            return []

    @staticmethod
    def _extract_message_text(data: dict[str, Any]) -> str:
        """Pull the assistant's text out of a Messages API response.

        Content interleaves text blocks and web_search_tool_result blocks;
        only "text" blocks carry the model's actual answer -- the tool-result
        blocks are the raw search hits, not part of the answer text."""
        parts: list[str] = []
        for block in data.get("content", []) or []:
            if block.get("type") == "text":
                parts.append(str(block.get("text", "") or ""))
        return "\n".join(parts)

    def _record_usage(self, data: dict[str, Any], *, operation_suffix: str) -> None:
        usage = data.get("usage", {})
        if not (self._usage_tracker and isinstance(usage, dict)):
            return
        # Cached-prefix tokens are reported in separate fields, not folded
        # into input_tokens -- omitting them would make usage tracking blind
        # to most of the real input cost once caching kicks in (same
        # reasoning as llm_client.py's _call_anthropic).
        prompt_tokens = (
            int(usage.get("input_tokens", 0) or 0)
            + int(usage.get("cache_creation_input_tokens", 0) or 0)
            + int(usage.get("cache_read_input_tokens", 0) or 0)
        )
        completion_tokens = int(usage.get("output_tokens", 0) or 0)
        if prompt_tokens or completion_tokens:
            self._usage_tracker.add(
                provider="anthropic",
                model=self._model,
                operation=f"{self._usage_operation_prefix}:{operation_suffix}",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

    def chat_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        response_format: dict[str, Any] | None = None,
        live_search: bool = False,
        max_tokens: int | None = None,
        allowed_domains: list[str] | None = None,  # accepted for interface parity; see GrokSearch
    ) -> str:
        """Execute a single Claude completion and return message text.

        Mirrors GrokSearch.chat_completion's surface -- live_search=True
        grants the real web_search tool (web_search_20260318); live_search=
        False is a plain completion with no search tool at all.

        response_format is accepted for interface parity with GrokSearch but
        has no effect: Claude's Messages API has no native JSON-mode
        response_format, and every current caller (url_discovery.py's
        direct-batch HTML harvest) already passes response_format=None,
        relying on the system prompt's own format instructions instead.

        temperature is likewise accepted for interface parity but never
        sent: confirmed live (2026-08-14) that at least one current Claude
        model rejects it outright ("`temperature` is deprecated for this
        model", 400). The probe script this class's payload shape was
        modeled on (scripts/probe_multi_provider_search_2026.py's
        call_claude) never sent temperature either -- this brings the
        production class back in line with the shape that was actually
        validated, rather than a parameter added by analogy to GrokSearch
        that was never itself tested.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": int(max_tokens) if max_tokens is not None else self._max_tokens,
            "system": str(system_prompt or ""),
            "messages": [{"role": "user", "content": str(user_prompt or "")}],
        }
        if live_search:
            payload["tools"] = [CLAUDE_SEARCH_TOOL]

        try:
            resp = self._post_with_retries(payload, user_prompt)
            resp.raise_for_status()
            data = resp.json()
            content = self._extract_message_text(data)
            self._record_usage(
                data, operation_suffix="chat_completion_search" if live_search else "chat_completion"
            )
            return content
        except requests.RequestException as exc:
            self._log_request_exception(user_prompt, exc)
            return ""
        except Exception as exc:
            logger.warning("Claude chat completion error for %r: %s", user_prompt[:60], exc)
            return ""

    def _claude_search(self, query: str, attempt: int = 1) -> list[dict[str, Any]]:
        """Execute a real Claude web search with optional retry on malformed JSON.

        Mirrors GrokSearch._grok_search's contract exactly: same
        system-prompt JSON instruction, same {name, snippet, url}
        normalization, same malformed-JSON stricter-retry-on-attempt-2
        behavior (only json.JSONDecodeError triggers the retry -- a bare
        ValueError from _extract_json_object, e.g. no '{' found at all,
        propagates up to search()'s catch-all and returns [] without a
        retry, matching GrokSearch's behavior)."""
        system_prompt = (
            "You are a web search engine. Perform a web search for the user query and return results "
            "strictly in this JSON format:\n"
            '{"results": [{"title": "...", "url": "...", "snippet": "..."}]}\n'
            "Return only valid JSON. No commentary, no prose, no markdown. JSON only."
            if attempt == 1
            else
            "Return valid JSON only. No commentary. Format: "
            '{"results": [{"title": "...", "url": "...", "snippet": "..."}]}'
        )

        try:
            payload = {
                "model": self._model,
                "max_tokens": self._max_tokens,
                # No temperature -- see chat_completion's docstring: at
                # least one current Claude model rejects it outright.
                "system": system_prompt,
                "messages": [{"role": "user", "content": query}],
                "tools": [CLAUDE_SEARCH_TOOL],
            }
            logger.debug(f"[Claude-Attempt{attempt}] Posting to {self._endpoint} with model={self._model}")
            logger.debug(f"[Claude-Attempt{attempt}] Query: {query[:100]}")

            resp = self._post_with_retries(payload, query)
            logger.debug(f"[Claude-Attempt{attempt}] Response Status: {resp.status_code}")

            resp.raise_for_status()
            data = resp.json()
            content = self._extract_message_text(data)
            self._record_usage(data, operation_suffix="search")

            parsed = _extract_json_object(content)
            results = parsed.get("results", [])

            normalized = []
            for item in results:
                normalized.append({
                    "name": item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                    "url": item.get("url", ""),
                })
            return normalized

        except json.JSONDecodeError as exc:
            if attempt < 2:
                logger.warning("Claude returned malformed JSON on attempt 1, retrying with stricter prompt")
                return self._claude_search(query, attempt=2)
            logger.warning("Claude returned malformed JSON after retry for %r: %s", query[:60], exc)
            return []
        except requests.RequestException as exc:
            self._log_request_exception(query, exc)
            return []

    @staticmethod
    def _classify_request_exception(exc: requests.RequestException) -> str:
        if isinstance(exc, ClaudeCircuitOpenError):
            return "circuit_open"
        text = str(exc).lower()
        if isinstance(exc, requests.Timeout) or "timed out" in text:
            return "timeout"
        if (
            "nameresolutionerror" in text
            or "failed to resolve" in text
            or "getaddrinfo failed" in text
            or "temporary failure in name resolution" in text
        ):
            return "dns"
        if isinstance(exc, requests.ConnectionError):
            return "connection"
        if getattr(exc, "response", None) is not None:
            return "http"
        return "request"

    def _log_request_exception(self, query: str, exc: requests.RequestException) -> None:
        category = self._classify_request_exception(exc)
        now = time.monotonic()
        should_log = False
        suppressed_count = 0

        with self._warning_state_lock:
            state = self._warning_state.get(category, {"last_log": 0.0, "suppressed": 0})
            last_log = float(state.get("last_log", 0.0) or 0.0)
            if not last_log or (now - last_log) >= self._warning_cooldown_seconds:
                should_log = True
                suppressed_count = int(state.get("suppressed", 0) or 0)
                state["last_log"] = now
                state["suppressed"] = 0
            else:
                state["suppressed"] = int(state.get("suppressed", 0) or 0) + 1
            self._warning_state[category] = state

        if not should_log:
            return

        if suppressed_count > 0:
            logger.warning(
                "Claude %s errors continuing; suppressed %s similar warnings in the last %ss. latest query=%r error=%s",
                category,
                suppressed_count,
                self._warning_cooldown_seconds,
                query[:60],
                exc,
            )
            return

        if getattr(exc, "response", None) is not None:
            try:
                resp_body = exc.response.text[:500]
                logger.warning(
                    "Claude %s error for %r (status=%s): %s | Response: %s",
                    category,
                    query[:60],
                    exc.response.status_code,
                    exc,
                    resp_body,
                )
                return
            except Exception:
                pass
        logger.warning("Claude %s error for %r: %s", category, query[:60], exc)

    @classmethod
    def _is_transient_request_error(cls, exc: requests.RequestException) -> bool:
        category = cls._classify_request_exception(exc)
        return category in {"timeout", "dns", "connection"}

    def is_circuit_open(self) -> bool:
        """Non-raising peek at circuit-breaker state, for callers that want to
        skip optional/supplementary work (like a harvest retry-prompt) during
        a known-bad period rather than compounding it -- as opposed to
        _circuit_breaker_check, which is the fail-fast gate on the actual
        network call."""
        with self._circuit_breaker_lock:
            open_until = self._circuit_breaker_open_until
        return open_until > time.monotonic()

    def _circuit_breaker_check(self) -> bool:
        """Fail fast if a recent burst of transient errors tripped the breaker.

        Under a provider-side outage, every concurrent caller would otherwise
        independently retry and wait out its own full timeout budget, piling
        more load onto an already-struggling endpoint and stalling the whole
        run. Once enough transient failures land in a short window, later
        callers short-circuit immediately instead of queuing up behind them.

        Returns True if this call is the half-open recovery probe -- see
        GrokSearch._circuit_breaker_check for the full rationale
        (2026-08-15): exactly one caller is let through once cooldown
        elapses, instead of the full concurrent backlog rushing back in at
        once and risking an near-instant re-trip off a noisy multi-caller
        signal. Non-probe callers keep failing fast until the probe
        resolves or its lease expires.
        """
        now = time.monotonic()
        with self._circuit_breaker_lock:
            open_until = self._circuit_breaker_open_until
            if open_until > now:
                remaining = open_until - now
                raise ClaudeCircuitOpenError(
                    f"Claude circuit breaker open ({remaining:.1f}s remaining) after repeated transient errors"
                )
            if open_until <= 0.0:
                return False
            probe_claimed_until = self._circuit_breaker_probe_claimed_until
            if probe_claimed_until > now:
                raise ClaudeCircuitOpenError(
                    f"Claude circuit breaker recovering (probe in flight, "
                    f"{probe_claimed_until - now:.1f}s until next probe window)"
                )
            self._circuit_breaker_probe_claimed_until = now + self._circuit_breaker_probe_lease_seconds
            return True

    def _record_circuit_breaker_outcome(self, *, transient_failure: bool, is_probe: bool = False) -> None:
        now = time.monotonic()
        with self._circuit_breaker_lock:
            if not transient_failure:
                was_open = self._circuit_breaker_open_until > 0.0
                self._circuit_breaker_failure_times.clear()
                self._circuit_breaker_open_until = 0.0
                if was_open:
                    self._note_recovery_locked(now)
                return
            if is_probe:
                self._circuit_breaker_open_until = now + self._circuit_breaker_cooldown_seconds
                self._note_trip_locked(now)
                return
            cutoff = now - self._circuit_breaker_window_seconds
            self._circuit_breaker_failure_times = [
                t for t in self._circuit_breaker_failure_times if t >= cutoff
            ]
            self._circuit_breaker_failure_times.append(now)
            if len(self._circuit_breaker_failure_times) >= self._circuit_breaker_threshold:
                self._circuit_breaker_open_until = now + self._circuit_breaker_cooldown_seconds
                self._note_trip_locked(now)

    def _note_trip_locked(self, now: float) -> None:
        """Must be called with _circuit_breaker_lock held."""
        if self._circuit_breaker_trip_opened_at > 0.0:
            return
        self._circuit_breaker_trip_opened_at = now
        self._circuit_breaker_trip_count += 1
        logger.warning(
            "Claude circuit breaker OPENED (trip #%d this instance) -- cooling down %.1fs",
            self._circuit_breaker_trip_count,
            self._circuit_breaker_cooldown_seconds,
        )

    def _note_recovery_locked(self, now: float) -> None:
        """Must be called with _circuit_breaker_lock held."""
        if self._circuit_breaker_trip_opened_at > 0.0:
            open_duration = now - self._circuit_breaker_trip_opened_at
            self._circuit_breaker_total_open_seconds += open_duration
            self._circuit_breaker_trip_opened_at = 0.0
            logger.info(
                "Claude circuit breaker RECOVERED after %.1fs open (trip #%d; %.1fs total open this instance)",
                open_duration,
                self._circuit_breaker_trip_count,
                self._circuit_breaker_total_open_seconds,
            )

    def get_circuit_breaker_stats(self) -> dict[str, Any]:
        """Snapshot for run-summary reporting."""
        with self._circuit_breaker_lock:
            total_open = self._circuit_breaker_total_open_seconds
            if self._circuit_breaker_trip_opened_at > 0.0:
                total_open += time.monotonic() - self._circuit_breaker_trip_opened_at
            return {
                "trip_count": self._circuit_breaker_trip_count,
                "total_open_seconds": round(total_open, 1),
                "currently_open": self._circuit_breaker_open_until > time.monotonic(),
            }

    def _post_with_retries(self, payload: dict[str, Any], query: str) -> requests.Response:
        """POST to Claude with lightweight retry/backoff for transient read timeouts."""
        is_probe = self._circuit_breaker_check()
        max_attempts = self._network_retries + 1
        last_exc: requests.RequestException | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                with _CLAUDE_SEMAPHORE:
                    resp = self._get_session().post(
                        self._endpoint,
                        json=payload,
                        timeout=self._timeout,
                    )
                # raise_for_status() here (not left to the caller) is what
                # lets the circuit breaker actually see HTTP-level failures
                # -- see GrokSearch._post_with_retries for the full
                # rationale (2026-08-15): a non-2xx response used to be
                # recorded as a success (no requests-level exception), so a
                # persistent account-level failure like "credit balance too
                # low" made this breaker look perfectly healthy while every
                # call was actually being rejected.
                resp.raise_for_status()
                self._record_circuit_breaker_outcome(transient_failure=False, is_probe=is_probe)
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                is_transient = self._is_transient_request_error(exc)
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code is not None and self._is_retryable_http_status(status_code):
                    is_transient = True
                counts_toward_breaker = is_transient or status_code is not None
                if counts_toward_breaker:
                    self._record_circuit_breaker_outcome(transient_failure=True, is_probe=is_probe)
                if attempt >= max_attempts or not is_transient:
                    raise
                backoff = min(2.0, 0.4 * attempt)
                logger.info(
                    "Claude transient %s for %r (attempt %s/%s), retrying in %.1fs",
                    self._classify_request_exception(exc),
                    query[:60],
                    attempt,
                    max_attempts,
                    backoff,
                )
                time.sleep(backoff)
        if last_exc:
            raise last_exc
        raise requests.RequestException("Unknown Claude POST failure")

    @staticmethod
    def _is_retryable_http_status(status_code: int) -> bool:
        return status_code in (429, 500, 502, 503, 504)
