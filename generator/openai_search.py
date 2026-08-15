"""
openai_search.py — OpenAI-based web search provider.

Built 2026-08-15 as a cross-provider fallback for grok_search.py, after a
real same-shape run comparison found Claude (the previous fallback) costing
~4.6x Grok for equivalent output and its account no longer being funded --
see issue #64's engineering-work comment for the full context.

Uses OpenAI's /v1/responses endpoint with the web_search tool -- the same
endpoint shape xAI's own /v1/responses migration was modeled on, confirmed
by direct SSE probing (2026-08-15): identical event types
(response.output_text.delta, response.completed) to grok_search.py's
streaming path. Real evidence before this file was written: gpt-4.1-mini
completed the same production-shaped multi-destination direct-batch prompts
in 6-27s (vs Grok's 68-250s), at ~$0.005/destination/kind (vs Grok's
grok-latest averaging ~$0.17/call), with 100% reliability at group_size=2
and 3 across all three batchable kinds -- including trail, the one kind
where Grok's group_size=3 failed outright.

Deliberately leaner than grok_search.py: only the streaming /v1/responses
path is implemented (no legacy non-streaming or chat-completions fallback)
because every real caller in this codebase only ever requests
live_search=True -- see url_discovery.py and cultural_events.py's exclusive
use of chat_completion(..., live_search=True). Matches GrokSearch's public
surface (chat_completion/search/is_circuit_open/get_circuit_breaker_stats)
so search_provider.py's callers can use either without knowing which
provider was selected.

Docs:     https://developers.openai.com/api/docs/guides/tools-web-search
Endpoint: https://api.openai.com/v1/responses
Env var:  OPENAI_API_KEY (already required elsewhere in this project for
          stage-3 content generation, no new secret needed)
"""
from __future__ import annotations
import json, logging, os, threading
import time
from typing import Any
import requests

logger = logging.getLogger(__name__)
OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
_DEFAULT_MODEL = "gpt-4.1-mini"
_DEFAULT_NETWORK_RETRIES = 1
# Same rationale as grok_search.py's streaming timeouts, tuned to OpenAI's
# own measured latency profile (2026-08-15 real-call evidence: 6-27s for
# every observed call, none exceeding 30s) rather than copying Grok's
# numbers, which were sized for a much slower provider. Kept generous
# relative to observed latency -- if real traffic later shows OpenAI
# occasionally running as long as Grok does, these should be revisited with
# the same "measure, don't guess" discipline, not left as an assumption.
_DEFAULT_STREAM_READ_TIMEOUT_SECONDS = 45
_DEFAULT_STREAM_TOTAL_TIMEOUT_SECONDS = 120

# threshold=4/window=50s: sized the same way as grok_search.py's breaker --
# with _OPENAI_SEMAPHORE capping concurrency at 8, the largest failure burst
# possible is one failure per occupied slot roughly every ~45-46s (read
# timeout + backoff); window must exceed that round's own duration or the
# breaker can silently stop tripping during a real outage (see
# grok_search.py's window-sizing comments for the mechanism this avoids).
# Not yet validated against a real OpenAI outage -- unlike Grok's numbers,
# which were tuned against an actual observed failure cadence.
_DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 4
_DEFAULT_CIRCUIT_BREAKER_WINDOW_SECONDS = 50.0
_DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS = 15.0

# Global semaphore, same rationale as grok_search.py's _GROK_SEMAPHORE.
# Started at 8 (not ramped up from a smaller number first) since OpenAI's
# real per-call latency (6-27s) is far below what made 16 unsafe for Grok's
# old chat-completions endpoint -- but this is an assumption carried over
# from Grok's finding, not independently verified for OpenAI under load.
_OPENAI_SEMAPHORE = threading.Semaphore(8)


class OpenAiCircuitOpenError(requests.RequestException):
    """Raised when the circuit breaker short-circuits a call after a recent
    burst of transient OpenAI errors. Mirrors GrokCircuitOpenError."""


def _extract_json_object(text: str) -> dict[str, Any]:
    """Duplicated from grok_search.py/claude_search.py rather than shared --
    both already independently duplicate this, so this follows established
    precedent (provider-agnostic JSON extraction) rather than introducing a
    new cross-module coupling."""
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


class OpenAiSearch:
    """
    Thread-safe OpenAI-based search provider.

    Uses OpenAI's Responses API with the web_search tool to perform real,
    live web search via streaming SSE. Each calling thread gets its own
    ``requests.Session`` via ``threading.local()``, so the instance is safe
    to share across a ``ThreadPoolExecutor``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        network_retries: int = _DEFAULT_NETWORK_RETRIES,
        usage_tracker: Any | None = None,
        usage_operation_prefix: str = "openai_search",
    ) -> None:
        self._api_key = api_key or os.environ["OPENAI_API_KEY"]
        self._model = model or os.environ.get("OPENAI_MODEL", _DEFAULT_MODEL)
        self._network_retries = max(0, int(os.environ.get("OPENAI_SEARCH_NETWORK_RETRIES", str(network_retries))))
        self._stream_read_timeout = int(
            os.environ.get("OPENAI_SEARCH_STREAM_READ_TIMEOUT_SECONDS", str(_DEFAULT_STREAM_READ_TIMEOUT_SECONDS))
        )
        self._stream_total_timeout = int(
            os.environ.get("OPENAI_SEARCH_STREAM_TOTAL_TIMEOUT_SECONDS", str(_DEFAULT_STREAM_TOTAL_TIMEOUT_SECONDS))
        )
        self._session_local = threading.local()
        self._usage_tracker = usage_tracker
        self._usage_operation_prefix = usage_operation_prefix
        self._warning_state_lock = threading.Lock()
        self._warning_state: dict[str, dict[str, float | int]] = {}
        self._warning_cooldown_seconds = int(os.environ.get("OPENAI_SEARCH_WARNING_COOLDOWN_SECONDS", "30"))
        self._circuit_breaker_threshold = max(
            1, int(os.environ.get("OPENAI_SEARCH_CIRCUIT_BREAKER_THRESHOLD", str(_DEFAULT_CIRCUIT_BREAKER_THRESHOLD)))
        )
        self._circuit_breaker_window_seconds = float(
            os.environ.get("OPENAI_SEARCH_CIRCUIT_BREAKER_WINDOW_SECONDS", str(_DEFAULT_CIRCUIT_BREAKER_WINDOW_SECONDS))
        )
        self._circuit_breaker_cooldown_seconds = float(
            os.environ.get("OPENAI_SEARCH_CIRCUIT_BREAKER_COOLDOWN_SECONDS", str(_DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS))
        )
        self._circuit_breaker_lock = threading.Lock()
        self._circuit_breaker_failure_times: list[float] = []
        self._circuit_breaker_open_until: float = 0.0
        self._circuit_breaker_probe_claimed_until: float = 0.0
        self._circuit_breaker_probe_lease_seconds = (
            self._stream_total_timeout * (self._network_retries + 1) + 5.0
        )
        self._circuit_breaker_trip_count: int = 0
        self._circuit_breaker_total_open_seconds: float = 0.0
        self._circuit_breaker_trip_opened_at: float = 0.0

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
        """Execute a real web search via OpenAI's web_search tool.

        Returns a list of ``{name, snippet, url}`` dicts (up to ``count``
        results). Returns ``[]`` on any HTTP, parse, or API error so callers
        never need to guard against exceptions -- matches GrokSearch.search."""
        try:
            results = self._openai_search(query, attempt=1)
            return results[:count] if count else results
        except Exception as exc:
            logger.warning("OpenAI search error for %r: %s", query[:60], exc)
            return []

    def _record_responses_usage(self, usage: dict[str, Any], *, operation_suffix: str) -> None:
        if not (self._usage_tracker and isinstance(usage, dict)):
            return
        prompt_tokens = int(usage.get("input_tokens", 0) or 0)
        completion_tokens = int(usage.get("output_tokens", 0) or 0)
        if prompt_tokens or completion_tokens:
            self._usage_tracker.add(
                provider="openai",
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
        """Execute a single OpenAI completion with real web search and
        return the output text. Matches GrokSearch.chat_completion's
        signature so both clients are interchangeable to callers.

        live_search=False is intentionally unsupported (raises) -- every
        real call site in this codebase (url_discovery.py, cultural_events.py)
        only ever passes live_search=True for the search client; a plain
        non-search completion path would be dead code carrying its own
        maintenance burden for a case that never happens in production.
        """
        if not live_search:
            raise NotImplementedError(
                "OpenAiSearch.chat_completion only supports live_search=True -- "
                "no caller in this codebase requests the non-search path for a search client."
            )
        payload: dict[str, Any] = {
            "model": self._model,
            "input": f"{str(system_prompt or '')}\n\n{str(user_prompt or '')}",
            "tools": [{"type": "web_search"}],
        }
        if response_format is not None:
            payload["text"] = {"format": response_format}
        try:
            content, usage = self._post_responses_streaming_with_retries(payload, user_prompt)
            self._record_responses_usage(usage, operation_suffix="chat_completion_search")
            return content
        except requests.RequestException as exc:
            self._log_request_exception(user_prompt, exc)
            return ""
        except Exception as exc:
            logger.warning("OpenAI search completion error for %r: %s", user_prompt[:60], exc)
            return ""

    def _openai_search(self, query: str, attempt: int = 1) -> list[dict[str, Any]]:
        """Execute a real OpenAI web search with optional retry on malformed
        JSON. Mirrors GrokSearch._grok_search exactly."""
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
            content, usage = self._post_responses_streaming_with_retries(payload, query)
            self._record_responses_usage(usage, operation_suffix="search")

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
                logger.warning("OpenAI returned malformed JSON on attempt 1, retrying with stricter prompt")
                return self._openai_search(query, attempt=2)
            logger.warning("OpenAI returned malformed JSON after retry for %r: %s", query[:60], exc)
            return []
        except requests.RequestException as exc:
            self._log_request_exception(query, exc)
            return []

    @staticmethod
    def _classify_request_exception(exc: requests.RequestException) -> str:
        if isinstance(exc, OpenAiCircuitOpenError):
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
                "OpenAI %s errors continuing; suppressed %s similar warnings in the last %ss. latest query=%r error=%s",
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
                    "OpenAI %s error for %r (status=%s): %s | Response: %s",
                    category,
                    query[:60],
                    exc.response.status_code,
                    exc,
                    resp_body,
                )
                return
            except Exception:
                pass
        logger.warning("OpenAI %s error for %r: %s", category, query[:60], exc)

    @classmethod
    def _is_transient_request_error(cls, exc: requests.RequestException) -> bool:
        category = cls._classify_request_exception(exc)
        return category in {"timeout", "dns", "connection"}

    def is_circuit_open(self) -> bool:
        """Non-raising peek at circuit-breaker state. Matches
        GrokSearch.is_circuit_open."""
        with self._circuit_breaker_lock:
            open_until = self._circuit_breaker_open_until
        return open_until > time.monotonic()

    def _circuit_breaker_check(self) -> bool:
        """Fail fast if a recent burst of transient errors tripped the
        breaker; returns True if this call is the half-open recovery probe.
        Identical mechanism to GrokSearch._circuit_breaker_check -- see that
        method's docstring for the full rationale."""
        now = time.monotonic()
        with self._circuit_breaker_lock:
            open_until = self._circuit_breaker_open_until
            if open_until > now:
                remaining = open_until - now
                raise OpenAiCircuitOpenError(
                    f"OpenAI circuit breaker open ({remaining:.1f}s remaining) after repeated transient errors"
                )
            if open_until <= 0.0:
                return False
            probe_claimed_until = self._circuit_breaker_probe_claimed_until
            if probe_claimed_until > now:
                raise OpenAiCircuitOpenError(
                    f"OpenAI circuit breaker recovering (probe in flight, "
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
        if self._circuit_breaker_trip_opened_at > 0.0:
            return
        self._circuit_breaker_trip_opened_at = now
        self._circuit_breaker_trip_count += 1
        logger.warning(
            "OpenAI circuit breaker OPENED (trip #%d this instance) -- cooling down %.1fs",
            self._circuit_breaker_trip_count,
            self._circuit_breaker_cooldown_seconds,
        )

    def _note_recovery_locked(self, now: float) -> None:
        if self._circuit_breaker_trip_opened_at > 0.0:
            open_duration = now - self._circuit_breaker_trip_opened_at
            self._circuit_breaker_total_open_seconds += open_duration
            self._circuit_breaker_trip_opened_at = 0.0
            logger.info(
                "OpenAI circuit breaker RECOVERED after %.1fs open (trip #%d; %.1fs total open this instance)",
                open_duration,
                self._circuit_breaker_trip_count,
                self._circuit_breaker_total_open_seconds,
            )

    def get_circuit_breaker_stats(self) -> dict[str, Any]:
        """Matches GrokSearch.get_circuit_breaker_stats."""
        with self._circuit_breaker_lock:
            total_open = self._circuit_breaker_total_open_seconds
            if self._circuit_breaker_trip_opened_at > 0.0:
                total_open += time.monotonic() - self._circuit_breaker_trip_opened_at
            return {
                "trip_count": self._circuit_breaker_trip_count,
                "total_open_seconds": round(total_open, 1),
                "currently_open": self._circuit_breaker_open_until > time.monotonic(),
            }

    @staticmethod
    def _is_retryable_http_status(status_code: int) -> bool:
        return status_code in (429, 500, 502, 503, 504)

    def _post_responses_streaming_with_retries(
        self, payload: dict[str, Any], query: str
    ) -> tuple[str, dict[str, Any]]:
        """Stream a /v1/responses call and accumulate the final output text
        + usage from its SSE events. Identical structure to
        GrokSearch._post_responses_streaming_with_retries -- see that
        method's docstring for why streaming (not a plain blocking POST) is
        required for this endpoint shape."""
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
                with _OPENAI_SEMAPHORE:
                    resp = self._get_session().post(
                        OPENAI_RESPONSES_ENDPOINT,
                        json=payload,
                        timeout=self._stream_read_timeout,
                        stream=True,
                    )
                resp.raise_for_status()
                for line in resp.iter_lines(decode_unicode=True):
                    if time.monotonic() > deadline:
                        raise requests.exceptions.ReadTimeout(
                            f"OpenAI streaming response exceeded {self._stream_total_timeout}s total"
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
                        break
                    elif etype in ("response.failed", "response.incomplete"):
                        err = (evt.get("response", {}) or {}).get("error") or etype
                        raise RuntimeError(f"OpenAI streaming response ended with {etype}: {err}")
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
                    "OpenAI transient %s for %r (attempt %s/%s), retrying in %.1fs",
                    self._classify_request_exception(exc),
                    query[:60],
                    attempt,
                    max_attempts,
                    backoff,
                )
                time.sleep(backoff)
        if last_exc:
            raise last_exc
        raise requests.RequestException("Unknown OpenAI streaming POST failure")
