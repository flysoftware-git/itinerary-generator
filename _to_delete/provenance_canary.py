#!/usr/bin/env python3
"""
provenance_canary.py — assert that live web search is actually running.

WHY THIS EXISTS
---------------
On 2026-08-14 it was discovered that xAI had silently deprecated the
`live_search` chat-completions tool (it answers `410 Gone`). For an unknown
period before that, every direct-batch harvest call and every
``GrokSearch.search()`` call in this pipeline was being answered from the
model's *training memory* rather than from the live web -- while the code
read a `live_search` parameter the API had stopped honouring, and while the
product's central claim was that every published link is discovered from a
real search and verified.

Nothing failed. No exception was raised, no circuit breaker tripped, no
validation error fired. Content came back, URLs came back, and a plausible
fraction of them even resolved over HTTP. The degradation was invisible to
every guard this repository had.

That is the class of failure this canary detects: **the provider returns a
normal-looking answer without having searched.**

WHAT IT ASSERTS
---------------
The discriminator is not the content -- content is exactly what looked fine
last time. It is the provider's own report of server-side tool usage, which
this repository already parses for cost accounting
(``GrokSearch._record_responses_usage`` reads
``usage.server_side_tool_usage_details.web_search_calls``, recorded onto
``UsageTracker`` as ``tool_calls``).

  A1  TOOL_INVOKED   -- the provider reports >= 1 billed web_search
                        invocation for a call made with live_search=True.
                        THIS IS THE DEPRECATION DETECTOR. When live_search
                        silently stopped working, this count was zero.
  A2  CONTENT        -- the call returned non-empty content. Separates
                        "searched but said nothing" from "did not search".
  A3  URL_LIVE       -- at least one URL in the response resolves over plain
                        HTTP. Free (no provider spend); catches a provider
                        that reports tool use but returns fabricated links.
  A4  FRESHNESS      -- optional, --freshness. Asks for a fact that cannot
                        predate the model's training cutoff. A second,
                        independent signal that does not rely on the
                        provider's own usage bookkeeping being honest.

A1 is the one that matters. A2-A4 exist so a failure report distinguishes
"the provider is down", "the provider is lying", and "the provider stopped
searching" -- three situations that call for three different responses.

COST
----
One `--probes 1` run is a single /v1/responses call: roughly 1-2 billed
web_search invocations plus a few thousand tokens. At xAI list pricing that
is well under $0.05. A3 costs nothing (plain HTTP). Running this daily is
cheaper than one minute of a real generation run, and it is the only thing
standing between a silent provider change and shipping unverifiable links.

USAGE
-----
    python scripts/provenance_canary.py                    # grok, 1 probe
    python scripts/provenance_canary.py --provider claude
    python scripts/provenance_canary.py --probes 2 --freshness
    python scripts/provenance_canary.py --json out/canary.json
    python scripts/provenance_canary.py --dry-run          # free; no network

EXIT CODES
----------
    0  all assertions passed
    1  an assertion FAILED -- provenance is not established, treat every
       link this pipeline would publish today as unverified
    2  the canary could not run (missing key, unreadable config, provider
       unreachable, circuit already open). NOT a provenance failure -- do
       not read a 2 as "search is broken", read it as "unknown".

The 1/2 split is deliberate. Conflating "we proved search is dead" with "we
could not check" is how the original deprecation stayed invisible: an absent
signal was read as a quiet one.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("provenance_canary")

DEFAULT_CONFIG_PATH = _REPO_ROOT / "config.yaml"

# A deliberately mundane, stable, worldwide-known entity with an unambiguous
# official website. The probe must be something a search engine answers
# trivially, because the canary is testing WHETHER a search happened, not
# whether the model is clever. A hard question would confound the two.
PROBE_SYSTEM_PROMPT = (
    "You are a URL lookup tool. Use web search. Return only a compact JSON "
    'object of the form {"url": "<official website URL>"} and nothing else.'
)
PROBE_QUERIES: tuple[str, ...] = (
    "What is the official National Park Service website URL for Zion National Park?",
    "What is the official National Park Service website URL for Arches National Park?",
    "What is the official website URL for the Golden Gate Bridge Highway and "
    "Transportation District?",
)

# Freshness probe: the answer necessarily postdates any fixed training corpus.
# Deliberately does NOT ask about this project's own domain -- a travel
# question could be answered from a stale memory of a page that rarely
# changes, which is the exact ambiguity this assertion exists to remove.
FRESHNESS_SYSTEM_PROMPT = (
    "You are a current-events lookup tool. Use web search. Return only a "
    'compact JSON object of the form {"answer": "...", "as_of": "YYYY-MM-DD"} '
    "and nothing else."
)
FRESHNESS_QUERY = (
    "Search the web for today's date and one news headline published today. "
    "Report the headline and today's date."
)

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]}\\]+", re.IGNORECASE)


class CanaryHarnessError(RuntimeError):
    """The canary could not run. Distinct from the canary failing."""


def _extract_urls(text: str) -> list[str]:
    seen: list[str] = []
    for match in _URL_RE.findall(str(text or "")):
        url = match.rstrip(".,;")
        if url not in seen:
            seen.append(url)
    return seen


def _verify_any_url_live(urls: list[str], *, limit: int = 3) -> tuple[bool, list[dict[str, Any]]]:
    """Resolve up to `limit` URLs over plain HTTP. Costs nothing.

    Uses the pipeline's own URLValidator so the canary agrees with production
    about what "resolves" means -- including its blocked-is-not-dead handling,
    which is why a 403 from a bot-blocking host is not counted as a failure
    here either.
    """
    try:
        from generator.url_validator import URLValidator
    except Exception as exc:  # pragma: no cover - import guard
        raise CanaryHarnessError(f"cannot import URLValidator: {exc}") from exc

    validator = URLValidator()
    results: list[dict[str, Any]] = []
    any_ok = False
    for url in urls[:limit]:
        try:
            ok, status = validator.verify_url(url)
        except Exception as exc:  # network/SSL/etc -- record, don't crash
            ok, status = False, f"error:{exc.__class__.__name__}"
        results.append({"url": url, "ok": bool(ok), "status": status})
        any_ok = any_ok or bool(ok)
    return any_ok, results


def _build_client(provider: str, config_path: Path, tracker: Any) -> Any:
    try:
        from generator.search_provider import build_search_client
    except Exception as exc:  # pragma: no cover - import guard
        raise CanaryHarnessError(f"cannot import build_search_client: {exc}") from exc
    try:
        return build_search_client(
            str(config_path),
            config_section="url_discovery",
            provider_override=provider,
            usage_tracker=tracker,
            usage_operation_prefix="provenance_canary",
        )
    except Exception as exc:
        raise CanaryHarnessError(f"cannot construct {provider} search client: {exc}") from exc


def _new_tracker() -> Any:
    try:
        from generator.llm_client import UsageTracker
    except Exception as exc:  # pragma: no cover - import guard
        raise CanaryHarnessError(f"cannot import UsageTracker: {exc}") from exc
    return UsageTracker()


def _tool_calls_from(summary: dict[str, Any]) -> int:
    return sum(int(rec.get("tool_calls", 0) or 0) for rec in summary.get("records", []) or [])


def run_canary(
    *,
    provider: str = "grok",
    probes: int = 1,
    freshness: bool = False,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute the canary and return a report dict. Never raises on a FAIL.

    Raises CanaryHarnessError only when the check could not be performed at
    all -- see this module's docstring on the 1-vs-2 exit-code split.
    """
    config_path = config_path or DEFAULT_CONFIG_PATH
    started = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "started_at": started.isoformat(),
        "provider": provider,
        "probes_requested": int(probes),
        "freshness_probe": bool(freshness),
        "dry_run": bool(dry_run),
        "assertions": {},
        "responses": [],
        "usage": {},
    }

    if dry_run:
        # Exercises argument handling, prompt construction and reporting with
        # zero provider spend, so the canary itself can be smoke-tested in CI
        # on every commit while the paid assertion runs on a schedule.
        report["assertions"] = {
            "A1_TOOL_INVOKED": {"status": "SKIPPED", "detail": "dry run"},
            "A2_CONTENT": {"status": "SKIPPED", "detail": "dry run"},
            "A3_URL_LIVE": {"status": "SKIPPED", "detail": "dry run"},
        }
        if freshness:
            report["assertions"]["A4_FRESHNESS"] = {"status": "SKIPPED", "detail": "dry run"}
        report["result"] = "SKIPPED"
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        return report

    tracker = _new_tracker()
    client = _build_client(provider, config_path, tracker)

    if hasattr(client, "is_circuit_open") and client.is_circuit_open():
        raise CanaryHarnessError(
            f"{provider} circuit breaker is already open before the first probe; "
            "the canary cannot establish provenance during a known outage"
        )

    n = max(1, int(probes))
    contents: list[str] = []
    for i in range(n):
        query = PROBE_QUERIES[i % len(PROBE_QUERIES)]
        try:
            content = client.chat_completion(
                system_prompt=PROBE_SYSTEM_PROMPT,
                user_prompt=query,
                live_search=True,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            raise CanaryHarnessError(f"probe {i + 1} raised {exc.__class__.__name__}: {exc}") from exc
        contents.append(str(content or ""))
        report["responses"].append({"query": query, "content": str(content or "")[:2000]})

    if freshness:
        try:
            fresh = client.chat_completion(
                system_prompt=FRESHNESS_SYSTEM_PROMPT,
                user_prompt=FRESHNESS_QUERY,
                live_search=True,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            raise CanaryHarnessError(
                f"freshness probe raised {exc.__class__.__name__}: {exc}"
            ) from exc
        report["responses"].append({"query": FRESHNESS_QUERY, "content": str(fresh or "")[:2000]})
    else:
        fresh = ""

    summary = tracker.summary()
    report["usage"] = {
        "total_calls": summary.get("total_calls", 0),
        "total_estimated_cost_usd": summary.get("total_estimated_cost_usd", 0.0),
        "total_tool_call_cost_usd": summary.get("total_tool_call_cost_usd", 0.0),
    }
    tool_calls = _tool_calls_from(summary)
    report["usage"]["web_search_calls"] = tool_calls

    # --- A1: the deprecation detector -------------------------------------
    a1_pass = tool_calls >= 1
    report["assertions"]["A1_TOOL_INVOKED"] = {
        "status": "PASS" if a1_pass else "FAIL",
        "web_search_calls": tool_calls,
        "detail": (
            f"provider reported {tool_calls} billed web_search invocation(s)"
            if a1_pass
            else (
                "provider reported ZERO web_search invocations for calls made with "
                "live_search=True. Either the search tool is no longer being invoked "
                "(the 2026-08-14 live_search deprecation shape), or the provider "
                "stopped reporting its tool-count field. Both mean this pipeline "
                "cannot presently claim its links come from a live search."
            )
        ),
    }

    # --- A2: content ------------------------------------------------------
    non_empty = [c for c in contents if c.strip()]
    a2_pass = len(non_empty) == len(contents)
    report["assertions"]["A2_CONTENT"] = {
        "status": "PASS" if a2_pass else "FAIL",
        "detail": f"{len(non_empty)}/{len(contents)} probes returned non-empty content",
    }

    # --- A3: at least one returned URL actually resolves ------------------
    urls: list[str] = []
    for c in contents:
        for u in _extract_urls(c):
            if u not in urls:
                urls.append(u)
    if urls:
        any_live, url_results = _verify_any_url_live(urls)
    else:
        any_live, url_results = False, []
    report["assertions"]["A3_URL_LIVE"] = {
        "status": "PASS" if any_live else "FAIL",
        "urls_checked": url_results,
        "detail": (
            "at least one returned URL resolves over HTTP"
            if any_live
            else (
                "no URL in the response resolved. With A1 passing this points at "
                "fabricated links despite a real search; with A1 failing it is the "
                "expected downstream symptom, not an independent finding."
            )
        ),
    }

    # --- A4: freshness ----------------------------------------------------
    if freshness:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        a4_pass = today in str(fresh or "")
        report["assertions"]["A4_FRESHNESS"] = {
            "status": "PASS" if a4_pass else "FAIL",
            "expected_date": today,
            "detail": (
                "response carries today's date, so it cannot have come from a "
                "fixed training corpus"
                if a4_pass
                else (
                    "response did not carry today's date. Weaker than A1 -- a model "
                    "can decline or format unexpectedly -- so treat a lone A4 failure "
                    "as a prompt to investigate, not as proof."
                )
            ),
        }

    statuses = [a["status"] for a in report["assertions"].values()]
    report["result"] = "FAIL" if "FAIL" in statuses else "PASS"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def _render(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"[PROVENANCE-CANARY] {report.get('started_at', '')}")
    lines.append(f"  Provider      : {report.get('provider')}")
    usage = report.get("usage") or {}
    if usage:
        lines.append(
            "  Search calls  : {} billed web_search invocation(s)".format(
                usage.get("web_search_calls", 0)
            )
        )
        lines.append(
            "  Cost          : ${:.4f} (tool fees ${:.4f})".format(
                float(usage.get("total_estimated_cost_usd", 0.0) or 0.0),
                float(usage.get("total_tool_call_cost_usd", 0.0) or 0.0),
            )
        )
    for name, body in (report.get("assertions") or {}).items():
        lines.append(f"  {body.get('status', '?'):<7} {name}")
        detail = str(body.get("detail", "") or "")
        if detail and body.get("status") != "PASS":
            lines.append(f"          {detail}")
    lines.append(f"  RESULT        : {report.get('result')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assert that live web search is actually being invoked.",
    )
    parser.add_argument("--provider", default="grok", choices=["grok", "claude", "openai"])
    parser.add_argument("--probes", type=int, default=1, help="paid search probes (default 1)")
    parser.add_argument(
        "--freshness",
        action="store_true",
        help="add a second probe whose answer cannot predate the training cutoff",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--json", dest="json_path", default=None, help="write the report here")
    parser.add_argument("--dry-run", action="store_true", help="no network, no spend")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if not args.dry_run:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:
            pass  # .env is a convenience; real env vars are the contract

    try:
        report = run_canary(
            provider=args.provider,
            probes=args.probes,
            freshness=args.freshness,
            config_path=Path(args.config),
            dry_run=args.dry_run,
        )
    except CanaryHarnessError as exc:
        print(f"[PROVENANCE-CANARY] COULD NOT RUN: {exc}", file=sys.stderr)
        print(
            "[PROVENANCE-CANARY] exit 2 means UNKNOWN, not OK. Provenance was "
            "not established this run.",
            file=sys.stderr,
        )
        return 2

    print(_render(report))

    if args.json_path:
        out = Path(args.json_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"  Report        : {out}")

    return 1 if report.get("result") == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
