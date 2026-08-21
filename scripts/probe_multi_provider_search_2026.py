"""Repeatable, schedulable multi-provider search/harvest capability probe.

Cheap, multi-destination check: does each provider's search/grounding
capability produce real, live-verifiable URLs when given the *same* real
production HTML-mode direct-batch prompt? The sharpest signal is citation
fidelity -- whether a provider's embedded <a href> URLs actually match its
own returned search citations, independent of bot-blocking noise that
pollutes simple alive/dead HTTP checks.

Design: the *same* real, current `_direct_batch_html_prompt` (imported
directly from generator.url_discovery, never duplicated locally -- a
previous experiment script's local copy had drifted stale) is sent to every
configured provider, parsed with the same real `_direct_batch_rows_from_html`.
Every resulting candidate URL is live-verified (HTTP HEAD, GET fallback).

History (2026-08-14, docs/design/search-provider-capability-probe.md):
  Initial pass found grok_live_search (410, deprecated live_search tool) and
  openai_search (400, rejects "temperature") both broken, and 3/4 initial
  Claude calls timed out at 90s. All three fixed and re-verified in a
  follow-up rerun (formerly a separate script, scripts/probe_rerun_fixed.py,
  now folded in here as the default config for each provider -- there is no
  more "v1 broken" / "v2 fixed" distinction to track). Results at that
  point: Grok 21/21 (100%) citation-matched, Claude 14/15 (93%), Gemini 4/21
  (19%), OpenAI 0/21 (0%).

Usage:
  python scripts/probe_multi_provider_search_2026.py
  python scripts/probe_multi_provider_search_2026.py --providers openai,gemini
  python scripts/probe_multi_provider_search_2026.py --include-baselines

Each run writes a full report (output/dev/multi_provider_search_probe/<ts>/
report.json + raw/) AND appends one compact, schema-stable summary line to
output/dev/multi_provider_search_probe/history.jsonl -- the history file is
what makes "run this again periodically to track drift" actually usable:
comparing two report.json files by hand doesn't scale, comparing two lines
of the same JSONL does. Scheduling itself (Windows Task Scheduler, cron, or
an agent wakeup) is left to the operator -- this script only needs to be a
clean, idempotent CLI entry point for whichever mechanism is chosen.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generator.url_discovery import URLDiscoverer  # noqa: E402

XAI_ENDPOINT = "https://api.x.ai/v1/chat/completions"
XAI_RESPONSES_ENDPOINT = "https://api.x.ai/v1/responses"
OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
GEMINI_BASE = "https://generativelanguage.googleapis.com"

# Model pins, overridable via env so a future rename/deprecation doesn't
# require editing this file -- just the environment the scheduled run uses.
GROK_MODEL = os.environ.get("PROBE_GROK_MODEL", "grok-4.5")
OPENAI_SEARCH_MODEL = os.environ.get("PROBE_OPENAI_SEARCH_MODEL", "gpt-5-search-api")
OPENAI_PLAIN_MODEL = os.environ.get("PROBE_OPENAI_PLAIN_MODEL", "gpt-4o-mini")
CLAUDE_MODEL = os.environ.get("PROBE_CLAUDE_MODEL", "claude-opus-5")
GEMINI_MODEL = os.environ.get("PROBE_GEMINI_MODEL", "gemini-flash-latest")

# Provider -> the env var that must be set for that provider's configs to run.
# A monitoring run shouldn't hard-crash because one provider's key is absent
# from whatever environment it happens to run in -- skip with a clear reason
# instead.
PROVIDER_API_KEY_ENV = {
    "grok": "XAI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

# (destination, dates, kind) -- kinds chosen for known-problematic history.
TEST_CASES: list[tuple[str, str, str]] = [
    ("Zion National Park", "October 2026", "trail"),       # The Narrows fabrication history
    ("Zion National Park", "October 2026", "attraction"),
    ("St. George, Utah", "October 2026", "restaurant"),     # GH #67 address-specific log evidence
    ("St. George, Utah", "October 2026", "attraction"),
]


def _real_prompt(discoverer: URLDiscoverer, kind: str, dest_name: str, dates: str) -> tuple[str, str]:
    """Pulls the REAL, current production prompt -- never duplicated locally,
    so this probe can never go stale the way the old experiment script did."""
    result = discoverer._direct_batch_html_prompt(kind=kind, dest_name=dest_name, dates=dates)
    if result is None:
        raise RuntimeError(f"No prompt builder for kind={kind!r}")
    return result


def _verify_url(url: str, timeout: int = 6) -> tuple[bool, str]:
    """Cheap live-liveness check: HEAD request, fall back to a short-timeout
    GET if the host rejects HEAD (common for bot-defensive sites). Returns
    (alive, status_description)."""
    headers = {"User-Agent": "Mozilla/5.0 (probe; itinerary-generator research)"}
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True, headers=headers)
        if resp.status_code in (405, 403):
            resp = requests.get(url, timeout=timeout, headers=headers, stream=True)
            resp.close()
        return (200 <= resp.status_code < 400, str(resp.status_code))
    except requests.RequestException as exc:
        return (False, type(exc).__name__)


# ── Provider call functions ─────────────────────────────────────────────────

def call_grok(system_prompt: str, user_prompt: str, *, live_search: bool, model: str, timeout: int) -> tuple[str, dict[str, Any]]:
    api_key = os.environ["XAI_API_KEY"]
    if live_search:
        # The old chat-completions "live_search" tool is deprecated (returns
        # 410, confirmed 2026-08-14) -- xAI moved search to a *different*
        # endpoint, /v1/responses, with a differently-shaped request/response.
        resp = requests.post(
            XAI_RESPONSES_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "input": f"{system_prompt}\n\n{user_prompt}",
                "tools": [{"type": "web_search"}],
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        text_parts: list[str] = []
        citations: list[dict[str, str]] = []
        for item in data.get("output", []) or []:
            if item.get("type") != "message":
                continue
            for block in item.get("content", []) or []:
                if block.get("type") == "output_text":
                    text_parts.append(str(block.get("text", "") or ""))
                    for a in block.get("annotations", []) or []:
                        if a.get("type") == "url_citation":
                            citations.append({"url": a.get("url", ""), "title": a.get("title", "")})
        return "\n".join(text_parts), {"raw_citations": citations}

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
    }
    resp = requests.post(
        XAI_ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    text = str(data.get("choices", [{}])[0].get("message", {}).get("content", "") or "")
    return text, {"raw_citations": []}


def call_openai(system_prompt: str, user_prompt: str, *, model: str, json_mode: bool, timeout: int) -> tuple[str, dict[str, Any]]:
    api_key = os.environ["OPENAI_API_KEY"]
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
        payload["messages"][0]["content"] += (
            " Return a JSON object: {\"html\": \"<the same HTML described above, as a string>\"}."
        )
        payload["temperature"] = 0.1
    elif "search" not in model:
        # Search-preview/search-api models reject temperature entirely
        # (confirmed via a 400 "Model incompatible request argument
        # supplied: temperature" -- they manage sampling internally).
        payload["temperature"] = 0.1
    resp = requests.post(
        OPENAI_ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    message = data.get("choices", [{}])[0].get("message", {})
    text = str(message.get("content", "") or "")
    if json_mode:
        try:
            text = json.loads(text).get("html", text)
        except (json.JSONDecodeError, AttributeError):
            pass
    annotations = message.get("annotations", []) or []
    citations = [
        {"url": a.get("url_citation", {}).get("url", ""), "title": a.get("url_citation", {}).get("title", "")}
        for a in annotations
        if a.get("type") == "url_citation"
    ]
    return text, {"raw_citations": citations}


def call_claude(system_prompt: str, user_prompt: str, *, model: str, use_search: bool, timeout: int) -> tuple[str, dict[str, Any]]:
    api_key = os.environ["ANTHROPIC_API_KEY"]
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 2048,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    if use_search:
        payload["tools"] = [{"type": "web_search_20260318", "name": "web_search", "max_uses": 5}]
    resp = requests.post(
        ANTHROPIC_ENDPOINT,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    text_parts: list[str] = []
    citations: list[dict[str, str]] = []
    for block in data.get("content", []) or []:
        if block.get("type") == "text":
            text_parts.append(str(block.get("text", "") or ""))
            for c in block.get("citations", []) or []:
                if c.get("type") == "web_search_result_location":
                    citations.append({"url": c.get("url", ""), "title": c.get("title", "")})
        elif block.get("type") == "web_search_tool_result":
            for r in block.get("content", []) or []:
                if isinstance(r, dict) and r.get("type") == "web_search_result":
                    citations.append({"url": r.get("url", ""), "title": r.get("title", "")})
    return "\n".join(text_parts), {"raw_citations": citations}


def call_gemini(system_prompt: str, user_prompt: str, *, model: str, use_search: bool, timeout: int) -> tuple[str, dict[str, Any]]:
    api_key = os.environ["GEMINI_API_KEY"]
    base = os.environ.get("GEMINI_API_BASE", GEMINI_BASE)
    url = f"{base}/v1beta/models/{model}:generateContent?key={api_key}"
    payload: dict[str, Any] = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "text/plain"},
    }
    if use_search:
        payload["tools"] = [{"google_search": {}}]
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    text_parts: list[str] = []
    citations: list[dict[str, str]] = []
    for candidate in data.get("candidates", []) or []:
        for part in candidate.get("content", {}).get("parts", []) or []:
            if "text" in part:
                text_parts.append(str(part["text"]))
        gm = candidate.get("groundingMetadata", {}) or {}
        for chunk in gm.get("groundingChunks", []) or []:
            web = chunk.get("web", {}) or {}
            if web.get("uri"):
                citations.append({"url": web.get("uri", ""), "title": web.get("title", "")})
    return "\n".join(text_parts), {"raw_citations": citations}


# ── Config registry ─────────────────────────────────────────────────────────
# Each entry: provider key -> list of (label, is_baseline, call_fn). The
# default probe run (no --include-baselines) only runs is_baseline=False
# configs -- one canonical "real search, current best-known call shape" per
# provider, which is what actually answers "did this provider's search
# capability get better or worse since last time." Baseline/no-search
# configs are kept for comparison but are opt-in, to keep the default
# scheduled run cheap (per the original "cheap probes" framing).
CallFn = Callable[[str, str], tuple[str, dict[str, Any]]]
ProviderConfig = tuple[str, bool, CallFn]

CONFIG_REGISTRY: dict[str, list[ProviderConfig]] = {
    "grok": [
        ("grok_live_search", False, lambda s, u: call_grok(s, u, live_search=True, model=GROK_MODEL, timeout=90)),
        ("grok_no_search", True, lambda s, u: call_grok(s, u, live_search=False, model=GROK_MODEL, timeout=60)),
    ],
    "openai": [
        ("openai_search", False, lambda s, u: call_openai(s, u, model=OPENAI_SEARCH_MODEL, json_mode=False, timeout=90)),
        ("openai_plain_html", True, lambda s, u: call_openai(s, u, model=OPENAI_PLAIN_MODEL, json_mode=False, timeout=60)),
        ("openai_plain_json", True, lambda s, u: call_openai(s, u, model=OPENAI_PLAIN_MODEL, json_mode=True, timeout=60)),
    ],
    "claude": [
        ("claude_web_search", False, lambda s, u: call_claude(s, u, model=CLAUDE_MODEL, use_search=True, timeout=150)),
    ],
    "gemini": [
        ("gemini_google_search", False, lambda s, u: call_gemini(s, u, model=GEMINI_MODEL, use_search=True, timeout=90)),
    ],
}


# ── Analysis ─────────────────────────────────────────────────────────────

def _extract_row_name(row: dict[str, Any]) -> str:
    return str(row.get("name", "") or row.get("title", "") or "").strip()


def analyze_response(text: str, meta: dict[str, Any]) -> dict[str, Any]:
    rows = URLDiscoverer._direct_batch_rows_from_html(text)
    verified: list[dict[str, Any]] = []
    for row in rows:
        name = _extract_row_name(row)
        url = str(row.get("url", "") or "").strip()
        if not url:
            verified.append({"name": name, "url": "", "alive": None, "status": "no_url"})
            continue
        alive, status = _verify_url(url)
        verified.append({"name": name, "url": url, "alive": alive, "status": status})
        time.sleep(0.15)  # be polite to target hosts

    citation_urls = {c.get("url", "") for c in meta.get("raw_citations", []) if c.get("url")}
    html_urls = {v["url"] for v in verified if v["url"]}
    return {
        "row_count": len(rows),
        "rows_with_url": sum(1 for v in verified if v["url"]),
        "rows_verified_alive": sum(1 for v in verified if v["alive"] is True),
        "rows_verified_dead": sum(1 for v in verified if v["alive"] is False),
        "citation_count": len(citation_urls),
        # A URL the model put in its HTML that never actually appeared among
        # its own cited search results -- the sharpest fabrication signal
        # available: the model had real search results in hand and chose to
        # emit something else anyway.
        "html_urls_not_in_citations": len(html_urls - citation_urls) if citation_urls else None,
        "details": verified,
    }


def summarize_provider_runs(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up one config's per-case analyze_response() results into a single
    comparable-across-runs summary. This -- not the raw per-case detail -- is
    what gets written to history.jsonl."""
    ok_entries = [e for e in entries if "error" not in e]
    errors = [e for e in entries if "error" in e]

    rows_with_url = sum(e.get("rows_with_url", 0) for e in ok_entries)
    verified_alive = sum(e.get("rows_verified_alive", 0) for e in ok_entries)
    verified_dead = sum(e.get("rows_verified_dead", 0) for e in ok_entries)

    fidelity_cases = [e for e in ok_entries if e.get("html_urls_not_in_citations") is not None]
    not_in_citations = sum(e.get("html_urls_not_in_citations", 0) for e in fidelity_cases)
    fidelity_denominator = sum(e.get("rows_with_url", 0) for e in fidelity_cases)
    citation_fidelity_pct = (
        round(100.0 * (fidelity_denominator - not_in_citations) / fidelity_denominator, 1)
        if fidelity_denominator
        else None
    )

    return {
        "cases_run": len(entries),
        "cases_errored": len(errors),
        "rows_with_url": rows_with_url,
        "rows_verified_alive": verified_alive,
        "rows_verified_dead": verified_dead,
        "citation_fidelity_pct": citation_fidelity_pct,
        "errors": [e["error"] for e in errors] if errors else [],
    }


def _parse_providers_arg(raw: str) -> list[str]:
    requested = [p.strip().lower() for p in raw.split(",") if p.strip()]
    unknown = [p for p in requested if p not in CONFIG_REGISTRY]
    if unknown:
        raise SystemExit(f"Unknown provider(s): {unknown}. Valid: {sorted(CONFIG_REGISTRY)}")
    return requested


def build_configs(providers: list[str], *, include_baselines: bool) -> list[tuple[str, CallFn]]:
    configs: list[tuple[str, CallFn]] = []
    for provider in providers:
        key_env = PROVIDER_API_KEY_ENV[provider]
        if not os.environ.get(key_env):
            print(f"[skip] {provider}: {key_env} not set")
            continue
        for label, is_baseline, fn in CONFIG_REGISTRY[provider]:
            if is_baseline and not include_baselines:
                continue
            configs.append((label, fn))
    return configs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--providers",
        default="grok,openai,claude,gemini",
        help="Comma-separated providers to probe (grok,openai,claude,gemini). Default: all four.",
    )
    parser.add_argument(
        "--include-baselines",
        action="store_true",
        help="Also run no-search/comparison baseline configs (more calls, more cost).",
    )
    parser.add_argument(
        "--history-file",
        default=None,
        help="Append-only JSONL summary path (default: output/dev/multi_provider_search_probe/history.jsonl)",
    )
    parser.add_argument(
        "--env-file",
        default="C:/Dev/Sandbox/.env",
        help="Extra .env file to load before the repo's own .env (default: C:/Dev/Sandbox/.env)",
    )
    args = parser.parse_args()

    load_dotenv(args.env_file)
    load_dotenv()

    providers = _parse_providers_arg(args.providers)
    configs = build_configs(providers, include_baselines=args.include_baselines)
    if not configs:
        raise SystemExit("No configs to run -- no requested provider has its API key set.")

    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    run_stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = REPO_ROOT / "output" / "dev" / "multi_provider_search_probe" / run_stamp
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {"run_stamp_utc": run_stamp, "cases": []}
    per_provider_entries: dict[str, list[dict[str, Any]]] = {label: [] for label, _ in configs}

    for dest_name, dates, kind in TEST_CASES:
        system_prompt, user_prompt = _real_prompt(discoverer, kind, dest_name, dates)
        case_result: dict[str, Any] = {"destination": dest_name, "kind": kind, "providers": {}}
        print(f"\n=== {dest_name} / {kind} ===")

        for label, fn in configs:
            print(f"  [{label}] ...", end=" ", flush=True)
            try:
                text, meta = fn(system_prompt, user_prompt)
                analysis = analyze_response(text, meta)
                case_result["providers"][label] = analysis
                per_provider_entries[label].append(analysis)
                slug = re.sub(r"[^a-z0-9]+", "-", f"{dest_name}-{kind}-{label}".lower()).strip("-")
                (raw_dir / f"{slug}.txt").write_text(text, encoding="utf-8")
                print(
                    f"rows={analysis['row_count']} alive={analysis['rows_verified_alive']} "
                    f"dead={analysis['rows_verified_dead']} citations={analysis['citation_count']} "
                    f"not_in_citations={analysis['html_urls_not_in_citations']}"
                )
            except Exception as exc:  # noqa: BLE001 -- probe must survive any single provider failing
                err = {"error": f"{type(exc).__name__}: {exc}"}
                case_result["providers"][label] = err
                per_provider_entries[label].append(err)
                print(f"FAILED: {type(exc).__name__}: {exc}")

        report["cases"].append(case_result)

    summary = {label: summarize_provider_runs(entries) for label, entries in per_provider_entries.items()}
    report["summary"] = summary

    (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {run_dir / 'report.json'}")
    print(f"Wrote raw responses under {raw_dir}")

    print("\n=== Summary (citation fidelity %) ===")
    for label, s in summary.items():
        fidelity = f"{s['citation_fidelity_pct']}%" if s["citation_fidelity_pct"] is not None else "n/a"
        print(f"  {label}: {fidelity} (rows_with_url={s['rows_with_url']}, errors={s['cases_errored']})")

    history_path = Path(args.history_file) if args.history_file else (
        REPO_ROOT / "output" / "dev" / "multi_provider_search_probe" / "history.jsonl"
    )
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"run_stamp_utc": run_stamp, "providers": summary}) + "\n")
    print(f"Appended summary to {history_path}")


if __name__ == "__main__":
    main()
