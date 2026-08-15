"""Cheap, multi-destination probe: does each provider's search/grounding
capability produce real, live-verifiable URLs when given the *same* real
production HTML-mode direct-batch prompt -- and does explicitly enabling
Grok's live_search tool (never done in production -- confirmed via code
read, every call site passes live_search=False) change anything?

Background (2026-08-14 investigation): xAI's own docs confirm real-time
search is opt-in per request; without the live_search tool, Grok answers
from its training-data knowledge cutoff, not live search. Neither the
JSON-mode nor HTML-mode harvest path in this codebase has ever set
live_search=True. This probe tests that gap directly, alongside whether
OpenAI/Claude/Gemini's native search tools can produce comparable results
to what this app currently trusts from Grok.

Design: the *same* real, current `_direct_batch_html_prompt` (imported
directly from generator.url_discovery, not duplicated -- the previous
experiment script's local copy had drifted significantly stale) is sent to
every provider/mode configuration below, parsed with the same real
`_direct_batch_rows_from_html`. This directly tests the "a simple universal
HTML-list prompt is the lowest common denominator across providers"
hypothesis. Every resulting candidate URL is then live-verified (HTTP HEAD)
-- not just scored on whether it looks lexically plausible, since a
plausible-looking URL can still be hallucinated (demonstrated separately:
raw/ungrounded Gemini output followed the exact real nps.gov URL
convention while never having actually searched).

Kept deliberately cheap: 2 destinations (chosen for known-problematic
history: Zion/"The Narrows" AllTrails fabrication, St. George restaurant
address-resolution per real GH #67 log evidence), 2 kinds each, 7
provider/mode configs -- 28 calls total, each a single completion, not a
full pipeline run.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generator.url_discovery import URLDiscoverer  # noqa: E402

XAI_ENDPOINT = "https://api.x.ai/v1/chat/completions"
OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
GEMINI_BASE = "https://generativelanguage.googleapis.com"

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
    (alive, status_description). This is the empirical test the old
    experiment script never did -- it only scored lexical plausibility."""
    headers = {"User-Agent": "Mozilla/5.0 (probe; road-trip-generator research)"}
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True, headers=headers)
        if resp.status_code in (405, 403):
            resp = requests.get(url, timeout=timeout, headers=headers, stream=True)
            resp.close()
        return (200 <= resp.status_code < 400, str(resp.status_code))
    except requests.RequestException as exc:
        return (False, type(exc).__name__)


# ── Provider call functions ─────────────────────────────────────────────────

XAI_RESPONSES_ENDPOINT = "https://api.x.ai/v1/responses"


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


def main() -> None:
    load_dotenv("C:/Dev/Sandbox/.env")
    load_dotenv()

    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    configs: list[tuple[str, Any]] = [
        ("grok_no_search (current production)", lambda s, u: call_grok(s, u, live_search=False, model="grok-4.5", timeout=60)),
        ("grok_live_search (never used in production)", lambda s, u: call_grok(s, u, live_search=True, model="grok-4.5", timeout=90)),
        ("openai_search (gpt-5-search-api)", lambda s, u: call_openai(s, u, model="gpt-5-search-api", json_mode=False, timeout=90)),
        ("openai_plain_html (gpt-4o-mini, no search)", lambda s, u: call_openai(s, u, model="gpt-4o-mini", json_mode=False, timeout=60)),
        ("openai_plain_json (gpt-4o-mini, no search)", lambda s, u: call_openai(s, u, model="gpt-4o-mini", json_mode=True, timeout=60)),
        ("claude_web_search (claude-opus-5)", lambda s, u: call_claude(s, u, model="claude-opus-5", use_search=True, timeout=90)),
        ("gemini_google_search (gemini-flash-latest)", lambda s, u: call_gemini(s, u, model="gemini-flash-latest", use_search=True, timeout=90)),
    ]

    run_stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = REPO_ROOT / "output" / "dev" / "multi_provider_search_probe" / run_stamp
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {"run_stamp_utc": run_stamp, "cases": []}

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
                slug = re.sub(r"[^a-z0-9]+", "-", f"{dest_name}-{kind}-{label}".lower()).strip("-")
                (raw_dir / f"{slug}.txt").write_text(text, encoding="utf-8")
                print(
                    f"rows={analysis['row_count']} alive={analysis['rows_verified_alive']} "
                    f"dead={analysis['rows_verified_dead']} citations={analysis['citation_count']}"
                )
            except Exception as exc:  # noqa: BLE001 -- probe must survive any single provider failing
                case_result["providers"][label] = {"error": f"{type(exc).__name__}: {exc}"}
                print(f"FAILED: {type(exc).__name__}: {exc}")

        report["cases"].append(case_result)

    (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {run_dir / 'report.json'}")
    print(f"Wrote raw responses under {raw_dir}")


if __name__ == "__main__":
    main()
