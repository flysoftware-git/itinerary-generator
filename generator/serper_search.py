"""serper_search.py — candidate URL discovery via Serper (Google SERP API).

Mirrors `grok_search.py`'s surface so `search_provider.build_search_client`
can return either interchangeably: same constructor keywords, the same
`search(query, count) -> [{name, snippet, url}]` contract, the same
never-raise behaviour, and `is_circuit_open()` for parity.

WHY THIS EXISTS
---------------
Measured 2026-08-23 against the 55 items run 7's paid LLM fallback actually
handled, taken from that run's own artifacts:

    coverage        Serper 55/55 (100%)   vs   LLM 52/55
    official .gov   28                    vs   2
    official .org   6                     vs   1
    travel content  2                     vs   11
    same domain as the LLM chose          5/55 (9%)

Then every Serper hit was put through `_retain_discovered_url` -- the same
relevance, redirect and promise-to-target gate the pipeline already applies,
unmodified -- and **53 of 55 (96%) passed**. Same items, same validator.

The LLM path was returning magazines and aggregators (lonelyplanet for
Inspiration Point, tripadvisor for Pioneer Park); Serper returns the
item-specific official page (`nps.gov/brca/planyourvisit/inspiration.htm`,
`sgcityutah.gov`). It is the first change in this investigation that improves
content *and* reduces cost -- see docs/design/cost-accounting-and-reduction.md
sections 8.6 and 8.7.

The saving is structural rather than incremental. An agentic LLM search bills
the retrieved page content back as input tokens -- 23,400 of a batch call's
23,700 tokens on a measured run -- so the pipeline was paying ~$2.13/M to read
web pages into a context window and then keep only a URL. Serper returns links
directly: no token cost at all, and $1.00 per 1,000 queries against xAI's
$5.00.

SCOPE
-----
Deliberately wired to the per-item fallback only, via
`url_discovery.nonbatch_search_provider`. The direct batch does a different
job -- it *invents the item list* as well as resolving URLs -- and a SERP API
cannot do the first part. That half is untested and out of scope here.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

SERPER_ENDPOINT = "https://google.serper.dev/search"

# Serper bills 1 credit for a search returning up to 10 results, and 2 credits
# for 11-100. Asking for 11 therefore doubles the price for results the
# fallback never looks at -- it takes the first candidate that survives
# retention. Capped here so the credit boundary cannot be crossed by a caller
# passing a larger `count`.
_MAX_FREE_TIER_RESULTS = 10
_DEFAULT_RESULTS = 5

_DEFAULT_TIMEOUT_SECONDS = 15
_DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 4
_DEFAULT_CIRCUIT_BREAKER_WINDOW_SECONDS = 30.0
_DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS = 30.0


class SerperSearch:
    def __init__(
        self,
        model: str | None = None,
        usage_tracker: Any | None = None,
        usage_operation_prefix: str = "search",
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        # `model` is accepted and ignored: build_search_client passes it
        # uniformly to every provider, and Serper has no model to select.
        # Recorded as the literal "serper" in usage so cost attribution has a
        # stable key (see DEFAULT_TOOL_CALL_PRICING_USD_PER_1000).
        self._model = "serper"
        self._api_key = os.environ.get("SERPER_API_KEY", "")
        self._usage_tracker = usage_tracker
        self._usage_operation_prefix = usage_operation_prefix
        self._timeout = int(os.environ.get("SERPER_TIMEOUT_SECONDS", str(timeout_seconds)))
        self._session = requests.Session()

        self._lock = threading.Lock()
        self._failures: list[float] = []
        self._circuit_open_until: float = 0.0
        self._threshold = max(1, int(os.environ.get(
            "SERPER_CIRCUIT_BREAKER_THRESHOLD", str(_DEFAULT_CIRCUIT_BREAKER_THRESHOLD))))
        self._window = float(os.environ.get(
            "SERPER_CIRCUIT_BREAKER_WINDOW_SECONDS", str(_DEFAULT_CIRCUIT_BREAKER_WINDOW_SECONDS)))
        self._cooldown = float(os.environ.get(
            "SERPER_CIRCUIT_BREAKER_COOLDOWN_SECONDS", str(_DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS)))

        if not self._api_key:
            logger.warning(
                "SERPER_API_KEY is not set; Serper search will return no results. "
                "Set it in the env file, or set url_discovery.nonbatch_search_provider "
                "back to grok."
            )

    # ---- circuit breaker (mirrors GrokSearch) --------------------------------

    def is_circuit_open(self) -> bool:
        with self._lock:
            return time.monotonic() < self._circuit_open_until

    def _record_failure(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._failures = [t for t in self._failures if now - t < self._window]
            self._failures.append(now)
            if len(self._failures) >= self._threshold:
                self._circuit_open_until = now + self._cooldown
                self._failures.clear()
                logger.warning(
                    "Serper circuit breaker opened for %.0fs after %d failures in %.0fs",
                    self._cooldown, self._threshold, self._window,
                )

    # ---- usage ---------------------------------------------------------------

    def _record_usage(self, *, queries: int) -> None:
        """Report query count as tool calls.

        Serper bills per query and consumes no tokens, so the entire cost is
        carried by the tool-call count. Reporting zero here would make a run of
        Serper searches cost $0.00 in the ledger -- the exact blind spot
        UsageTracker's summary-time warning was written for, and the one
        claude_search.py still has.
        """
        if not self._usage_tracker or queries <= 0:
            return
        self._usage_tracker.add(
            provider="serper",
            model=self._model,
            operation=f"{self._usage_operation_prefix}:search",
            prompt_tokens=0,
            completion_tokens=0,
            tool_calls=queries,
        )

    # ---- search --------------------------------------------------------------

    def search(self, query: str, count: int | None = None) -> list[dict[str, Any]]:
        """Return up to `count` ``{name, snippet, url}`` dicts.

        Never raises: returns ``[]`` on any HTTP, parse or auth failure, so
        callers need no guard. Matches GrokSearch.search's contract exactly.
        """
        text = str(query or "").strip()
        if not text or not self._api_key:
            return []
        if self.is_circuit_open():
            logger.info("Serper circuit open; skipping search for %r", text[:60])
            return []

        want = int(count) if count else _DEFAULT_RESULTS
        want = max(1, min(want, _MAX_FREE_TIER_RESULTS))

        try:
            resp = self._session.post(
                SERPER_ENDPOINT,
                headers={"X-API-KEY": self._api_key, "Content-Type": "application/json"},
                json={"q": text, "num": want},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            logger.warning("Serper request failed for %r: %s", text[:60], exc)
            self._record_failure()
            return []

        # Billed on the request, so record usage whatever the body turns out to
        # be. A 4xx that still consumed a credit must not report as free.
        self._record_usage(queries=1)

        if resp.status_code >= 400:
            logger.warning("Serper returned HTTP %s for %r", resp.status_code, text[:60])
            self._record_failure()
            return []

        try:
            payload = resp.json()
        except ValueError as exc:
            logger.warning("Serper returned unparseable JSON for %r: %s", text[:60], exc)
            self._record_failure()
            return []

        rows: list[dict[str, Any]] = []
        for entry in (payload.get("organic") or []):
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("link", "") or "").strip()
            if not url:
                continue
            rows.append({
                "name": str(entry.get("title", "") or "").strip(),
                "snippet": str(entry.get("snippet", "") or "").strip(),
                "url": url,
            })
            if len(rows) >= want:
                break
        return rows

    def chat_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        response_format: dict[str, Any] | None = None,
        live_search: bool = False,
        max_tokens: int | None = None,
        allowed_domains: list[str] | None = None,
    ) -> str:
        """Not supported: Serper is a SERP API, not a model.

        Present so that wiring this as a BATCH provider degrades to an empty
        harvest -- which the caller already treats as "fall back to
        per-destination calls" -- rather than raising AttributeError mid-run.
        The batch job needs a model to invent the item list; see this module's
        docstring for why that half is out of scope.
        """
        logger.warning(
            "SerperSearch.chat_completion called: Serper cannot generate a batch "
            "harvest. Set url_discovery.search_provider to a model provider; "
            "Serper belongs on nonbatch_search_provider."
        )
        return ""
