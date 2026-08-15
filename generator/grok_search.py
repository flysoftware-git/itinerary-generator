"""
grok_search.py — xAI Grok-based web search provider.

Uses Grok's chat completions API to perform semantic web search and extract
structured results. Grok is instructed to return valid JSON only.

Replaces: Google Programmable Search Engine (v1.4)
Docs:     https://docs.x.ai/
Endpoint: https://api.x.ai/v1/chat/completions
Env var:  XAI_API_KEY

Search API history:
  v1.0: Bing Search API v7 (retired August 11, 2025)
  v1.1: Google Custom Search (deprecated full-web search, unusable)
  v1.2: Brave Search API (retired in favour of Azure AI Services)
  v1.3: Bing Web Search API (deprecated, limited availability)
  v1.4: Google Programmable Search Engine (rate-limited, prohibitive costs)
  v1.5: xAI Grok semantic search via chat-completions + live_search
        (superseded 2026-08-14: live_search tool deprecated, returns 410)
  v1.6: xAI Grok via /v1/responses + web_search tool, SSE streaming
        (current default; see GROK_RESPONSES_ENDPOINT below). Claude and
        OpenAI are also available as alternate search providers -- see
        generator/search_provider.py, generator/claude_search.py,
        generator/openai_search.py.
"""
from __future__ import annotations
import json, logging, os, threading
import time
from typing import Any
import requests

logger = logging.getLogger(__name__)
GROK_ENDPOINT = "https://api.x.ai/v1/chat/completions"
# xAI's chat-completions "live_search" tool is deprecated (confirmed 2026-08-14:
# returns 410 Gone, "Live search is deprecated. Please switch to the Agent
# Tools API"). Real search now requires this separate endpoint with a
# differently-shaped request (top-level "input" string, not "messages") and
# response ("output": [...] items, not "choices", consumed via SSE streaming
# -- see _post_responses_streaming_with_retries). See the call sites in
# chat_completion(live_search=True) / _grok_search.
GROK_RESPONSES_ENDPOINT = "https://api.x.ai/v1/responses"
_DEFAULT_DELAY = 0.05
# Raised from 25s and retries cut from 2->1 (2026-08-15): the 2026-08-14
# /v1/responses migration replaced fast training-data-only "search" with real
# agentic web search, which is inherently slower -- the old 25s/2-retries
# tuning was inherited unchanged from the fast fake-search era. A live probe
# after this change still timed out at 120s with 0 retries, confirming the
# provider is currently in a genuine slow/degraded state (not just under-
# timed) -- 2 retries on top of that was silently tripling both wall-clock
# hang time and, if the provider bills completed-but-abandoned generations,
# real cost. 1 retry keeps a little resilience for a single transient blip
# without compounding a hang into 3x spend.
_DEFAULT_TIMEOUT_SECONDS = 45
_DEFAULT_NETWORK_RETRIES = 1
# Streaming timeouts for /v1/responses + web_search (2026-08-15): a raw SSE
# probe of this exact endpoint found the connection alive and progressing
# the whole time -- real gaps up to ~29s with zero bytes on the wire between
# events (reasoning -> search call -> second search round -> output text),
# total completion in ~68s for a real restaurant direct-batch harvest
# prompt. A single blocking, non-streaming read simply cannot survive that
# gap profile within any timeout tight enough to still catch a genuinely
# dead connection, which is what was misdiagnosed as a provider outage
# before this was found.
# _STREAM_READ_TIMEOUT_SECONDS bounds each individual chunk read (catches a
# truly dead connection); _STREAM_TOTAL_TIMEOUT_SECONDS is a wall-clock
# safety ceiling in case the stream stays alive but never sends
# response.completed. Raised 150s->250s the same day after a real attraction
# harvest call (same endpoint, same code path) needed more than 150s on its
# first attempt -- the too-tight ceiling killed a call that was still
# legitimately working, forcing a full wasted retry (total 306.7s across 2
# attempts for work a single more-patient attempt would have finished
# sooner and cheaper). Per-call latency varies a lot by kind/complexity
# (68s-150s+ observed); 250s leaves real margin above the worst case seen
# so far rather than repeating the same "too tight" mistake at a new number.
_DEFAULT_STREAM_READ_TIMEOUT_SECONDS = 60
_DEFAULT_STREAM_TOTAL_TIMEOUT_SECONDS = 250

# threshold=4/window=70s (widened again 2026-08-15 once the live-search path
# moved to streaming, above): almost all real traffic through this class is
# live_search=True (every direct-batch harvest call and every .search()
# call), whose dominant failure-detection latency is now
# _STREAM_READ_TIMEOUT_SECONDS (60s), not the plain chat-completions
# _timeout (45s) -- a truly dead streaming connection is caught by the
# per-chunk read timeout well before the 150s total-timeout safety ceiling
# in the common case. With _GROK_SEMAPHORE capping concurrency at 4, the
# largest failure burst physically possible is one failure per occupied slot
# roughly every ~60-61s (chunk read timeout + backoff) -- a full round of
# all 4 concurrent slots timing out. A window shorter than that round's own
# duration would silently reintroduce the exact bug this sizing was built to
# fix (a burst that can't land inside its own window, so the breaker never
# trips -- the original 5-within-20s combination's failure mode, observed
# over a 3.5-hour outage in Dipstick48). window=70s leaves margin above the
# ~61s expected round.
_DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 4
_DEFAULT_CIRCUIT_BREAKER_WINDOW_SECONDS = 70.0
_DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS = 15.0
_DEFAULT_CHAT_MAX_TOKENS = 6000

# Global semaphore: cap concurrent connections to xAI API to avoid rate limiting.
# 16 parallel threads -> xAI rate-limits -> all time out (2026-07-20 finding).
# Raised 4->8 (2026-08-15) now that discover_all's per-destination category
# parallelism (4 categories) plus per-run destination parallelism (up to 3
# destinations) was queuing entirely behind this single 4-slot cap, serializing
# work that was already logically concurrent. Kept well below the 16 that
# broke things rather than assuming the old finding no longer applies --
# that measurement predates the /v1/responses + streaming migration, so
# xAI's actual rate limit for this heavier endpoint under load is still
# unverified, not confirmed safe at a higher number.
_GROK_SEMAPHORE = threading.Semaphore(8)


class GrokCircuitOpenError(requests.RequestException):
    """Raised when the circuit breaker short-circuits a call after a recent burst
    of transient Grok errors, instead of letting the caller wait out a full
    timeout+retry cycle against an already-struggling endpoint."""


def _extract_json_object(text: str) -> dict[str, Any]:
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


class GrokSearch:
    """
    Thread-safe xAI Grok-based search provider.

    Uses Grok's chat completions API to perform semantic web search.
    Grok is instructed to return structured JSON results only.

    Each calling thread gets its own ``requests.Session`` via
    ``threading.local()``, so the instance is safe to share across a
    ``ThreadPoolExecutor``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        request_delay_seconds: float = _DEFAULT_DELAY,
        network_retries: int = _DEFAULT_NETWORK_RETRIES,
        usage_tracker: Any | None = None,
        usage_operation_prefix: str = "grok_search",
    ) -> None:
        self._api_key = api_key or os.environ["XAI_API_KEY"]
        self._model = model or os.environ.get("XAI_MODEL", "grok-latest")
        self._timeout = int(os.environ.get("XAI_TIMEOUT_SECONDS", str(timeout_seconds)))
        self._delay = request_delay_seconds
        self._network_retries = max(0, int(os.environ.get("XAI_NETWORK_RETRIES", str(network_retries))))
        self._stream_read_timeout = int(
            os.environ.get("XAI_STREAM_READ_TIMEOUT_SECONDS", str(_DEFAULT_STREAM_READ_TIMEOUT_SECONDS))
        )
        self._stream_total_timeout = int(
            os.environ.get("XAI_STREAM_TOTAL_TIMEOUT_SECONDS", str(_DEFAULT_STREAM_TOTAL_TIMEOUT_SECONDS))
        )
        self._session_local = threading.local()
        self._usage_tracker = usage_tracker
        self._usage_operation_prefix = usage_operation_prefix
        self._warning_state_lock = threading.Lock()
        self._warning_state: dict[str, dict[str, float | int]] = {}
        self._warning_cooldown_seconds = int(os.environ.get("XAI_WARNING_COOLDOWN_SECONDS", "30"))
        self._circuit_breaker_threshold = max(
            1, int(os.environ.get("XAI_CIRCUIT_BREAKER_THRESHOLD", str(_DEFAULT_CIRCUIT_BREAKER_THRESHOLD)))
        )
        self._circuit_breaker_window_seconds = float(
            os.environ.get("XAI_CIRCUIT_BREAKER_WINDOW_SECONDS", str(_DEFAULT_CIRCUIT_BREAKER_WINDOW_SECONDS))
        )
        self._circuit_breaker_cooldown_seconds = float(
            os.environ.get("XAI_CIRCUIT_BREAKER_COOLDOWN_SECONDS", str(_DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS))
        )
        self._circuit_breaker_lock = threading.Lock()
        self._circuit_breaker_failure_times: list[float] = []
        self._circuit_breaker_open_until: float = 0.0
        # Half-open recovery state: when cooldown elapses, exactly one caller
        # becomes the recovery probe instead of the full concurrent backlog
        # rushing back in at once (see _circuit_breaker_check). The lease is
        # timeout-bounded rather than a plain boolean flag so a probe that
        # exits through an unanticipated path (e.g. a non-transient error,
        # which never calls _record_circuit_breaker_outcome) can't wedge the
        # breaker in "probe permanently in flight" -- it just expires and the
        # next caller gets a fresh chance.
        self._circuit_breaker_probe_claimed_until: float = 0.0
        # max(...) with _stream_total_timeout: the live-search path (used for
        # every direct-batch harvest and .search() call) now streams with a
        # 150s wall-clock safety ceiling, longer than the plain
        # chat-completions timeout -- the probe lease must cover whichever
        # call type is actually in flight, not just the shorter one.
        self._circuit_breaker_probe_lease_seconds = (
            max(self._timeout, self._stream_total_timeout) * (self._network_retries + 1) + 5.0
        )
        # Trip/duration instrumentation (2026-08-15): lets a run's summary
        # answer "how much of this run's wall-clock time was actually spent
        # with the breaker open" instead of inferring it from scattered log
        # lines after the fact.
        self._circuit_breaker_trip_count: int = 0
        self._circuit_breaker_total_open_seconds: float = 0.0
        self._circuit_breaker_trip_opened_at: float = 0.0
        self._chat_max_tokens = int(os.environ.get("XAI_CHAT_MAX_TOKENS", str(_DEFAULT_CHAT_MAX_TOKENS)))

    # ── Thread-local session ─────────────────────────────────────────────────

    def _get_session(self) -> requests.Session:
        if not hasattr(self._session_local, "session"):
            s = requests.Session()
            s.headers.update({
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            })
            self._session_local.session = s
        return self._session_local.session

    # ── Core search ──────────────────────────────────────────────────────────

    def search(self, query: str, count: int | None = None) -> list[dict[str, Any]]:
        """
        Execute a semantic web search via Grok.

        Returns a list of ``{name, snippet, url}`` dicts (up to ``count``
        results).  Returns ``[]`` on any HTTP, parse, or API error so callers
        never need to guard against exceptions.
        """
        try:
            results = self._grok_search(query, attempt=1)
            return results[:count] if count else results
        except Exception as exc:
            logger.warning("Grok search error for %r: %s", query[:60], exc)
            return []

    def _record_responses_usage(self, usage: dict[str, Any], *, operation_suffix: str) -> None:
        if not (self._usage_tracker and isinstance(usage, dict)):
            return
        prompt_tokens = int(usage.get("input_tokens", 0) or 0)
        completion_tokens = int(usage.get("output_tokens", 0) or 0)
        if prompt_tokens or completion_tokens:
            self._usage_tracker.add(
                provider="grok",
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
    ) -> str:
        """Execute a single Grok completion and return message content.

        This is used by URL discovery HTML-mode direct-batch prompts where the
        caller needs raw model text rather than normalized search rows.

        live_search=True routes to the /v1/responses endpoint with the real
        web_search tool (see GROK_RESPONSES_ENDPOINT) -- the old
        tools:[{"type":"live_search"}] mechanism on chat-completions is
        deprecated. Probe evidence (2026-08-14): with search genuinely
        enabled, 21/21 embedded URLs matched Grok's own search citations
        across 4 real test cases (582 total citations) -- vs zero citations
        and no way to verify provenance at all with live_search=False.
        """
        if live_search:
            payload: dict[str, Any] = {
                "model": self._model,
                "input": f"{str(system_prompt or '')}\n\n{str(user_prompt or '')}",
                "tools": [{"type": "web_search"}],
            }
            if response_format is not None:
                # /v1/responses uses "text.format", not chat-completions'
                # "response_format" -- {"type": "json_object"} is the shape
                # both share for this one format kind, so pass it through.
                payload["text"] = {"format": response_format}
            try:
                content, usage = self._post_responses_streaming_with_retries(
                    payload, user_prompt, endpoint=GROK_RESPONSES_ENDPOINT
                )
                self._record_responses_usage(usage, operation_suffix="chat_completion_search")
                return content
            except requests.RequestException as exc:
                self._log_request_exception(user_prompt, exc)
                return ""
            except Exception as exc:
                logger.warning("Grok search completion error for %r: %s", user_prompt[:60], exc)
                return ""

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": str(system_prompt or "")},
                {"role": "user", "content": str(user_prompt or "")},
            ],
            "temperature": float(temperature),
            # Uncapped completions let a stuck/rambling model run to whatever the
            # provider's own ceiling is, inflating latency and cost with no
            # legitimate direct-batch harvest needing that much output.
            "max_tokens": int(max_tokens) if max_tokens is not None else self._chat_max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        try:
            resp = self._post_with_retries(payload, user_prompt)
            resp.raise_for_status()
            response_json = resp.json()
            content = str(response_json.get("choices", [{}])[0].get("message", {}).get("content", "") or "")

            usage = response_json.get("usage", {})
            if self._usage_tracker and isinstance(usage, dict):
                prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
                completion_tokens = int(usage.get("completion_tokens", 0) or 0)
                if prompt_tokens or completion_tokens:
                    self._usage_tracker.add(
                        provider="grok",
                        model=self._model,
                        operation=f"{self._usage_operation_prefix}:chat_completion",
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                    )
            return content
        except requests.RequestException as exc:
            self._log_request_exception(user_prompt, exc)
            return ""
        except Exception as exc:
            logger.warning("Grok chat completion error for %r: %s", user_prompt[:60], exc)
            return ""

    def _grok_search(self, query: str, attempt: int = 1) -> list[dict[str, Any]]:
        """Execute a real Grok web search (via /v1/responses + the web_search
        tool) with optional retry on malformed JSON.

        Previously this just told the model "You are a web search engine" via
        the system prompt on the plain chat-completions endpoint, with no
        actual search tool granted -- results could be entirely reproduced
        from training-data memory, not live search. Fixed 2026-08-14
        alongside chat_completion's live_search=True path; see
        GROK_RESPONSES_ENDPOINT."""
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
                "input": f"{system_prompt}\n\n{query}",
                "tools": [{"type": "web_search"}],
                "text": {"format": {"type": "json_object"}},
            }
            logger.debug(f"[Grok-Attempt{attempt}] Posting to {GROK_RESPONSES_ENDPOINT} with model={self._model}")
            logger.debug(f"[Grok-Attempt{attempt}] Query: {query[:100]}")

            content, usage = self._post_responses_streaming_with_retries(
                payload, query, endpoint=GROK_RESPONSES_ENDPOINT
            )
            self._record_responses_usage(usage, operation_suffix="search")

            # Parse JSON from Grok response
            parsed = _extract_json_object(content)
            results = parsed.get("results", [])

            # Normalize to {name, snippet, url} format
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
                logger.warning("Grok returned malformed JSON on attempt 1, retrying with stricter prompt")
                return self._grok_search(query, attempt=2)
            logger.warning("Grok returned malformed JSON after retry for %r: %s", query[:60], exc)
            return []
        except requests.RequestException as exc:
            self._log_request_exception(query, exc)
            return []

    @staticmethod
    def _classify_request_exception(exc: requests.RequestException) -> str:
        if isinstance(exc, GrokCircuitOpenError):
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
                "Grok %s errors continuing; suppressed %s similar warnings in the last %ss. latest query=%r error=%s",
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
                    "Grok %s error for %r (status=%s): %s | Response: %s",
                    category,
                    query[:60],
                    exc.response.status_code,
                    exc,
                    resp_body,
                )
                return
            except Exception:
                pass
        logger.warning("Grok %s error for %r: %s", category, query[:60], exc)

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

        Returns True if this call is the half-open recovery probe: once
        cooldown elapses, exactly one caller is let through to test whether
        the provider has actually recovered, instead of the full concurrent
        backlog (up to _GROK_SEMAPHORE's cap, times however many logical
        callers are queued behind it) rushing back in at once. That flood
        was a real self-inflicted-flapping risk (2026-08-15 finding): with a
        30s failure-detection window and only a 15s cooldown, some pre-trip
        failures are still "in window" the instant cooldown ends, so it can
        take as few as 1-2 fresh failures from a multi-caller burst to
        re-trip -- even when the provider had recovered enough to serve a
        single gentle request cleanly. A lone probe either fully resets
        breaker state on success or reopens immediately on failure, giving a
        clean signal either way. Non-probe callers keep failing fast (same
        as during open) until the probe resolves or its lease expires.
        """
        now = time.monotonic()
        with self._circuit_breaker_lock:
            open_until = self._circuit_breaker_open_until
            if open_until > now:
                remaining = open_until - now
                raise GrokCircuitOpenError(
                    f"Grok circuit breaker open ({remaining:.1f}s remaining) after repeated transient errors"
                )
            if open_until <= 0.0:
                return False
            probe_claimed_until = self._circuit_breaker_probe_claimed_until
            if probe_claimed_until > now:
                raise GrokCircuitOpenError(
                    f"Grok circuit breaker recovering (probe in flight, "
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
                # A single failed recovery probe is itself sufficient
                # evidence the provider hasn't actually recovered yet --
                # reopen immediately rather than waiting for `threshold`
                # fresh failures to reaccumulate.
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
        """Must be called with _circuit_breaker_lock held. Records a fresh
        trip (only -- re-affirming an already-open breaker, e.g. a probe's
        own internal retry attempts each failing, doesn't double-count)."""
        if self._circuit_breaker_trip_opened_at > 0.0:
            return
        self._circuit_breaker_trip_opened_at = now
        self._circuit_breaker_trip_count += 1
        logger.warning(
            "Grok circuit breaker OPENED (trip #%d this instance) -- cooling down %.1fs",
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
                "Grok circuit breaker RECOVERED after %.1fs open (trip #%d; %.1fs total open this instance)",
                open_duration,
                self._circuit_breaker_trip_count,
                self._circuit_breaker_total_open_seconds,
            )

    def get_circuit_breaker_stats(self) -> dict[str, Any]:
        """Snapshot for run-summary reporting: how much of this run's
        wall-clock time was actually spent with the breaker open, not just
        inferred after the fact from scattered log lines."""
        with self._circuit_breaker_lock:
            total_open = self._circuit_breaker_total_open_seconds
            if self._circuit_breaker_trip_opened_at > 0.0:
                total_open += time.monotonic() - self._circuit_breaker_trip_opened_at
            return {
                "trip_count": self._circuit_breaker_trip_count,
                "total_open_seconds": round(total_open, 1),
                "currently_open": self._circuit_breaker_open_until > time.monotonic(),
            }

    def _post_with_retries(
        self, payload: dict[str, Any], query: str, *, endpoint: str = GROK_ENDPOINT
    ) -> requests.Response:
        """POST to Grok with lightweight retry/backoff for transient read timeouts.

        endpoint defaults to the chat-completions endpoint; callers using the
        /v1/responses endpoint (real search -- see GROK_RESPONSES_ENDPOINT)
        pass it explicitly, reusing the same circuit-breaker/retry protection
        rather than duplicating it."""
        is_probe = self._circuit_breaker_check()
        max_attempts = self._network_retries + 1
        last_exc: requests.RequestException | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                with _GROK_SEMAPHORE:
                    resp = self._get_session().post(
                        endpoint,
                        json=payload,
                        timeout=self._timeout,
                    )
                # raise_for_status() here (not left to the caller) is what
                # lets the circuit breaker actually see HTTP-level failures.
                # Before this fix, a non-2xx response (e.g. Anthropic's own
                # 400 "credit balance is too low") got a response object
                # back with no requests-level exception, so this method
                # recorded it as a *success* -- the breaker showed 0 trips
                # while every single call was silently failing (2026-08-15
                # finding: a live run's fallback client looked perfectly
                # healthy in circuit_breaker_stats while 100% of its calls
                # were actually being rejected).
                resp.raise_for_status()
                self._record_circuit_breaker_outcome(transient_failure=False, is_probe=is_probe)
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                is_transient = self._is_transient_request_error(exc)
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code is not None and self._is_retryable_http_status(status_code):
                    # 429/5xx: worth retrying within this same call, same as
                    # a network-level timeout.
                    is_transient = True
                # Any HTTP error status counts toward the breaker even when
                # it isn't worth retrying immediately (e.g. 400/401/403 --
                # retrying the identical payload against a broken account or
                # key won't fix it), so repeated failures of this kind
                # across the run still trip the breaker and protect later
                # calls instead of each one independently rediscovering the
                # same dead end.
                counts_toward_breaker = is_transient or status_code is not None
                if counts_toward_breaker:
                    self._record_circuit_breaker_outcome(transient_failure=True, is_probe=is_probe)
                if attempt >= max_attempts or not is_transient:
                    raise
                backoff = min(2.0, 0.4 * attempt)
                logger.info(
                    "Grok transient %s for %r (attempt %s/%s), retrying in %.1fs",
                    self._classify_request_exception(exc),
                    query[:60],
                    attempt,
                    max_attempts,
                    backoff,
                )
                time.sleep(backoff)
        if last_exc:
            raise last_exc
        raise requests.RequestException("Unknown Grok POST failure")

    @staticmethod
    def _is_retryable_http_status(status_code: int) -> bool:
        return status_code in (429, 500, 502, 503, 504)

    def _post_responses_streaming_with_retries(
        self, payload: dict[str, Any], query: str, *, endpoint: str
    ) -> tuple[str, dict[str, Any]]:
        """Stream a /v1/responses call and accumulate the final output text
        + usage from its SSE events, with the same circuit-breaker/retry
        protection as _post_with_retries.

        A plain (non-streaming) request to this endpoint blocks until the
        entire multi-round agentic search+synthesis loop finishes
        server-side. Real probing (2026-08-15) found that loop alive and
        progressing the whole time -- gaps up to ~29s with zero bytes on the
        wire between events, ~68s total for a real direct-batch harvest
        prompt -- which is exactly the failure mode that had been
        misdiagnosed as a provider outage: any single blocking-read timeout
        tight enough to still be useful cuts off a call that was actually
        going to succeed. Streaming surfaces the same content incrementally
        instead, so a per-chunk read timeout only needs to survive a gap
        between events, not the whole call.
        """
        payload = dict(payload)
        payload["stream"] = True
        is_probe = self._circuit_breaker_check()
        max_attempts = self._network_retries + 1
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            text_parts: list[str] = []
            usage: dict[str, Any] = {}
            deadline = time.monotonic() + self._stream_total_timeout
            try:
                with _GROK_SEMAPHORE:
                    resp = self._get_session().post(
                        endpoint,
                        json=payload,
                        timeout=self._stream_read_timeout,
                        stream=True,
                    )
                resp.raise_for_status()
                completed = False
                for line in resp.iter_lines(decode_unicode=True):
                    if time.monotonic() > deadline:
                        raise requests.exceptions.ReadTimeout(
                            f"Grok streaming response exceeded {self._stream_total_timeout}s total"
                        )
                    if not line or not line.startswith("data: "):
                        continue
                    try:
                        evt = json.loads(line[6:])
                    except (ValueError, json.JSONDecodeError):
                        continue
                    etype = evt.get("type", "")
                    if etype == "response.output_text.delta":
                        text_parts.append(str(evt.get("delta", "") or ""))
                    elif etype == "response.completed":
                        usage = (evt.get("response", {}) or {}).get("usage", {}) or {}
                        completed = True
                        break
                    elif etype in ("response.failed", "response.incomplete"):
                        err = (evt.get("response", {}) or {}).get("error") or etype
                        raise RuntimeError(f"Grok streaming response ended with {etype}: {err}")
                self._record_circuit_breaker_outcome(transient_failure=False, is_probe=is_probe)
                return "".join(text_parts), usage
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
                    "Grok transient %s for %r (attempt %s/%s), retrying in %.1fs",
                    self._classify_request_exception(exc),
                    query[:60],
                    attempt,
                    max_attempts,
                    backoff,
                )
                time.sleep(backoff)
        if last_exc:
            raise last_exc
        raise requests.RequestException("Unknown Grok streaming POST failure")

    # ── URL-resolution helper ────────────────────────────────────────────────

    def search_first_url(
        self,
        query_variants: list[str],
        site_filter: str | None = None,
        site_hint: str | None = None,
        max_attempts: int = 4,
    ) -> str | None:
        """
        Try each query variant in order and return the first live URL found.

        *site_filter*  — only accept URLs whose string contains this value
                         (e.g. ``"alltrails.com"``).
        *site_hint*    — prepend a ``site:`` operator string verbatim
                         (e.g. ``"site:nps.gov/zion"``).
        *max_attempts* — cap on how many variants are tried.

        URL liveness is verified via :class:`~generator.url_validator.URLValidator`.
        Returns ``None`` when no valid URL is found across all variants.
        """
        from generator.url_validator import URLValidator
        uv = URLValidator()

        for query in query_variants[:max_attempts]:
            if site_hint:
                full_query = f"{site_hint} {query}"
            elif site_filter:
                full_query = f"site:{site_filter} {query}"
            else:
                full_query = query

            for item in self.search(full_query, count=10):
                url = item.get("url", "")
                if not url:
                    continue
                if site_filter and site_filter not in url:
                    continue
                ok, _ = uv.verify_url(url)
                if ok:
                    logger.debug("  URL: %s → %s", full_query[:60], url[:80])
                    return url

        return None
