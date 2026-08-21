"""Research experiment (NOT a production change): can smarter search/harvest
strategies find real, policy-compliant source URLs for the real dipstick65
attractions/trails that currently ship with NO link at all?

Background: config.yaml sets url_policy_mode: enforce and blocks
google_maps_search / google_maps_dir / google_search / social_media
site-wide (~config.yaml line 300-306) -- deliberately, so every shown link is
a real, specific, verified page rather than a generic maps-search guess. A
real validation run, SW2026-dipstick65
(C:/Temp/RoadTripRuns/SW2026-dipstick65/dev/index.html), still has several
attractions/trails with no link at all because search never found anything
better than a policy-blocked fallback. This script tests whether better
query construction, site-scoping, or an alternative search provider can close
that gap for a fixed list of real target items, BEFORE the project owner
considers relaxing the policy as an alternative.

This script does not modify generator/ or tests/. It reuses generator/
url_discovery.py's real, unmodified query-building and URL-acceptance
helpers (_build_query_variants, URLDiscoverer._classify_url_policy_class,
URLDiscoverer._is_specific_result_url, URLDiscoverer._matches_site_filter)
via a bare `URLDiscoverer.__new__(URLDiscoverer)` instance -- the same
"import the real thing, never duplicate a prompt/heuristic locally" pattern
already established by scripts/probe_multi_provider_search_2026.py -- so a
strategy that "wins" here is judged by the same acceptance rules production
would actually apply, not a hand-rolled reimplementation that could drift.

Strategies tested per item (see STRATEGIES below):
  - baseline_grok       : single query -- the real fallback-search first
                           variant (_build_query_variants()[0], plus the real
                           site:nps.gov/<code> site_hint when the item is
                           inside an NPS unit) -- via Grok's live
                           /v1/responses web_search tool. This is as close as
                           this script gets to reproducing today's actual
                           per-item fallback search call
                           (URLDiscoverer._search_first_strict).
  - site_scoped_grok    : force a domain-appropriate site: filter (nps.gov
                           for in-park items, alltrails.com for trail-like
                           items, a state-parks/BLM/USFS/official-org guess
                           for the rest) via the same Grok endpoint.
  - alt_phrasing_grok   : a single differently-worded, unquoted query
                           ("<name> <destination> official page") via the
                           same Grok endpoint -- isolates whether query
                           *wording* alone (no site-scoping) moves the
                           needle.
  - claude_web_search   : the exact same baseline query, sent through
                           Claude's real web_search tool instead of Grok's.
  - gemini_google_search: the exact same baseline query, via Gemini's
                           google_search grounding tool.
  - openai_search       : the exact same baseline query, via OpenAI's
                           gpt-5-search-api search model.

Every candidate URL returned by every strategy is (a) run through the real,
unmodified _classify_url_policy_class / _is_specific_result_url /
_matches_site_filter checks -- exactly like production would -- and (b)
live-verified with an HTTP HEAD/GET check, exactly like
scripts/probe_multi_provider_search_2026.py already does for its own
candidates.

Usage:
  python scripts/experiment_link_recall_strategies.py
  python scripts/experiment_link_recall_strategies.py --strategies baseline_grok,claude_web_search
  python scripts/experiment_link_recall_strategies.py --items "Sunrise Point,Piedra Falls"

Writes docs/reports/link_recall_strategy_experiment_results.json (full raw
data) and docs/reports/link_recall_strategy_experiment_results.md (human
summary), per this repo's docs/reports/ convention for experiment write-ups.
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

from generator.url_discovery import URLDiscoverer, _build_query_variants  # noqa: E402

XAI_RESPONSES_ENDPOINT = "https://api.x.ai/v1/responses"
ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"
GEMINI_BASE = "https://generativelanguage.googleapis.com"

GROK_MODEL = os.environ.get("XAI_MODEL", "grok-4.5")
CLAUDE_MODEL = os.environ.get("PROBE_CLAUDE_MODEL", "claude-opus-5")
GEMINI_MODEL = os.environ.get("PROBE_GEMINI_MODEL", "gemini-flash-latest")
OPENAI_SEARCH_MODEL = os.environ.get("PROBE_OPENAI_SEARCH_MODEL", "gpt-5-search-api")

POLICY_BLOCKED_CLASSES = {"google_maps_search", "google_maps_dir", "google_search", "social_media"}

# ── Real target list, grounded in the actual SW2026-dipstick65 output ──────
# kind: "attraction" or "trail" -- selects the same category string
# _discover_attractions / the trail fallback branch would pass to
# _build_query_variants (see generator/url_discovery.py ~L2142, ~L3922).
TARGETS: list[dict[str, Any]] = [
    {"name": "Sunrise Point", "destination": "Bryce Canyon National Park", "nps_code": "brca", "kind": "attraction", "site_guess": "nps.gov"},
    {"name": "Inspiration Point", "destination": "Bryce Canyon National Park", "nps_code": "brca", "kind": "attraction", "site_guess": "nps.gov"},
    {"name": "The Waterpocket Fold", "destination": "Capitol Reef National Park", "nps_code": "care", "kind": "attraction", "site_guess": "nps.gov"},
    {"name": "Chimney Rock National Monument", "destination": "Pagosa Springs, Colorado", "nps_code": None, "kind": "attraction", "site_guess": "fs.usda.gov"},
    {"name": "Museum of International Folk Art", "destination": "Santa Fe, New Mexico", "nps_code": None, "kind": "attraction", "site_guess": "internationalfolkart.org"},
    {"name": "Queens Garden Trail", "destination": "Bryce Canyon National Park", "nps_code": "brca", "kind": "trail", "site_guess": "alltrails.com"},
    {"name": "Fins and Things Trail", "destination": "Moab, Utah", "nps_code": None, "kind": "trail", "site_guess": "alltrails.com"},
    {"name": "Park Avenue Trail", "destination": "Arches National Park", "nps_code": "arch", "kind": "trail", "site_guess": "alltrails.com"},
    {"name": "Jud Wiebe Trail", "destination": "Telluride, Colorado", "nps_code": None, "kind": "trail", "site_guess": "alltrails.com"},
    {"name": "Bear Creek Trail", "destination": "Telluride, Colorado", "nps_code": None, "kind": "trail", "site_guess": "alltrails.com"},
    {"name": "Piedra Falls", "destination": "Pagosa Springs, Colorado", "nps_code": None, "kind": "trail", "site_guess": "alltrails.com"},
    {"name": "Tent Rocks Cave Loop", "destination": "Kasha-Katuwe Tent Rocks National Monument, New Mexico", "nps_code": None, "kind": "trail", "site_guess": "blm.gov"},
    {"name": "Lava Tubes via Lava Flow Trail", "destination": "Snow Canyon State Park, Utah", "nps_code": None, "kind": "trail", "site_guess": "alltrails.com"},
]

RESULTS_JSON_SCHEMA = (
    '{"results": [{"title": "...", "url": "...", "snippet": "..."}]}'
)


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = [line for line in raw.splitlines() if not line.strip().startswith("```")]
        raw = "\n".join(lines).strip()
    # Some models wrap JSON in prose despite instructions -- grab the first
    # top-level {...} blob rather than failing outright.
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw = raw[start:end + 1]
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("response was not a JSON object")
    return parsed


def _normalize_results(text: str) -> list[dict[str, str]]:
    try:
        parsed = _extract_json_object(text)
    except Exception:
        return []
    rows = parsed.get("results", [])
    if not isinstance(rows, list):
        return []
    out: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append({
            "title": str(row.get("title", "") or ""),
            "url": str(row.get("url", "") or "").strip(),
            "snippet": str(row.get("snippet", "") or ""),
        })
    return out


# ── Query construction ──────────────────────────────────────────────────────

def _category_for(item: dict[str, Any]) -> str:
    return "trail hike" if item["kind"] == "trail" else "attraction landmark museum viewpoint"


def _nps_site_hint(item: dict[str, Any]) -> str | None:
    code = item.get("nps_code")
    return f"site:nps.gov/{code}" if code else None


def baseline_query(item: dict[str, Any]) -> str:
    """Reproduces the real per-item fallback query: _build_query_variants()[0]
    (the most specific, quoted-name variant), with the real nps.gov site_hint
    prefix when the item is inside an NPS unit -- matching
    URLDiscoverer._search_first_strict's `full_query` construction."""
    variants = _build_query_variants(item["name"], item["destination"], _category_for(item))
    query = variants[0]
    hint = _nps_site_hint(item)
    return f"{hint} {query}" if hint else query


def site_scoped_query(item: dict[str, Any]) -> str:
    site = item["site_guess"]
    return f"site:{site} {item['name']} {item['destination']}"


def alt_phrasing_query(item: dict[str, Any]) -> str:
    return f"{item['name']} {item['destination']} official page"


# ── Provider calls -- each returns (results, citations) ────────────────────

def call_grok(query: str, session: requests.Session, api_key: str, *, timeout: int = 60) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    system_prompt = (
        "You are a web search engine. Perform a web search for the user query and return results "
        f"strictly in this JSON format:\n{RESULTS_JSON_SCHEMA}\n"
        "Return only valid JSON. No commentary, no prose, no markdown. JSON only. "
        "Prefer specific, item-level pages (official park/venue/trail pages) over generic "
        "search-results, maps-search, or social-media pages."
    )
    payload = {
        "model": GROK_MODEL,
        "input": f"{system_prompt}\n\n{query}",
        "tools": [{"type": "web_search"}],
        "text": {"format": {"type": "json_object"}},
    }
    resp = session.post(
        XAI_RESPONSES_ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
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
    results = _normalize_results("\n".join(text_parts))
    if not results and citations:
        results = [{"title": c["title"], "url": c["url"], "snippet": ""} for c in citations]
    return results, citations


def call_claude(query: str, api_key: str, *, timeout: int = 150) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    system_prompt = (
        "Use your web_search tool to find real, live, specific pages for the user's query -- not "
        "generic search-results pages, maps-search pages, or social-media pages. Then return your "
        f"findings strictly as JSON: {RESULTS_JSON_SCHEMA}. No commentary outside the JSON."
    )
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 1536,
        "system": system_prompt,
        "messages": [{"role": "user", "content": query}],
        "tools": [{"type": "web_search_20260318", "name": "web_search", "max_uses": 4}],
    }
    resp = requests.post(
        ANTHROPIC_ENDPOINT,
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
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
        elif block.get("type") == "web_search_tool_result":
            for r in block.get("content", []) or []:
                if isinstance(r, dict) and r.get("type") == "web_search_result":
                    citations.append({"url": r.get("url", ""), "title": r.get("title", "")})
    results = _normalize_results("\n".join(text_parts))
    if not results and citations:
        results = [{"title": c["title"], "url": c["url"], "snippet": ""} for c in citations]
    return results, citations


def call_gemini(query: str, api_key: str, *, timeout: int = 90) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    base = os.environ.get("GEMINI_API_BASE", GEMINI_BASE)
    url = f"{base}/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
    system_prompt = (
        "Use Google Search grounding to find real, live, specific pages for the user's query -- not "
        "generic search-results pages, maps-search pages, or social-media pages. Then return your "
        f"findings strictly as JSON: {RESULTS_JSON_SCHEMA}. No commentary outside the JSON."
    )
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": query}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "text/plain"},
        "tools": [{"google_search": {}}],
    }
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
    results = _normalize_results("\n".join(text_parts))
    if not results and citations:
        results = [{"title": c["title"], "url": c["url"], "snippet": ""} for c in citations]
    return results, citations


def call_openai(query: str, api_key: str, *, timeout: int = 90) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    system_prompt = (
        "Search the web for real, live, specific pages for the user's query -- not generic "
        "search-results pages, maps-search pages, or social-media pages. Then return your findings "
        f"strictly as JSON: {RESULTS_JSON_SCHEMA}. No commentary outside the JSON."
    )
    payload: dict[str, Any] = {
        "model": OPENAI_SEARCH_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ],
    }
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
    annotations = message.get("annotations", []) or []
    citations = [
        {"url": a.get("url_citation", {}).get("url", ""), "title": a.get("url_citation", {}).get("title", "")}
        for a in annotations
        if a.get("type") == "url_citation"
    ]
    results = _normalize_results(text)
    if not results and citations:
        results = [{"title": c["title"], "url": c["url"], "snippet": ""} for c in citations]
    return results, citations


# ── Acceptance / verification -- reuses the real, unmodified production
#    helpers so a "win" here is judged by the same rules production applies.

def evaluate_candidate(discoverer: URLDiscoverer, url: str, item_name: str, dest_name: str) -> dict[str, Any]:
    policy_class = URLDiscoverer._classify_url_policy_class(url)
    policy_blocked = policy_class in POLICY_BLOCKED_CLASSES
    try:
        is_specific = discoverer._is_specific_result_url(url, item_name, dest_name)
    except Exception as exc:  # noqa: BLE001 -- helper touched an unset instance attr
        is_specific = None
        policy_blocked = policy_blocked  # keep policy verdict; specificity unknown
        _ = exc
    accepted = bool(is_specific) and not policy_blocked
    return {"policy_class": policy_class, "policy_blocked": policy_blocked, "is_specific": is_specific, "accepted": accepted}


def verify_url(url: str, timeout: int = 8) -> tuple[bool | None, str]:
    headers = {"User-Agent": "Mozilla/5.0 (research probe; itinerary-generator link-recall experiment)"}
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True, headers=headers)
        if resp.status_code in (405, 403):
            resp = requests.get(url, timeout=timeout, headers=headers, stream=True)
            resp.close()
        return (200 <= resp.status_code < 400, str(resp.status_code))
    except requests.RequestException as exc:
        return (None, type(exc).__name__)


def run_strategy_for_item(
    label: str,
    query_fn: Callable[[dict[str, Any]], str],
    call_fn: Callable[[str], tuple[list[dict[str, str]], list[dict[str, str]]]],
    item: dict[str, Any],
    discoverer: URLDiscoverer,
) -> dict[str, Any]:
    query = query_fn(item)
    entry: dict[str, Any] = {"strategy": label, "item": item["name"], "query": query}
    try:
        results, citations = call_fn(query)
    except Exception as exc:  # noqa: BLE001 -- a single item/strategy must not kill the run
        entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["candidates"] = []
        entry["chosen"] = None
        return entry

    candidates: list[dict[str, Any]] = []
    for row in results[:6]:
        url = row.get("url", "")
        if not url:
            continue
        verdict = evaluate_candidate(discoverer, url, item["name"], item["destination"])
        candidates.append({**row, **verdict})

    entry["citation_count"] = len(citations)
    entry["candidates"] = candidates

    chosen = next((c for c in candidates if c["accepted"]), None)
    if chosen is None and candidates:
        chosen = candidates[0]
        chosen_is_fallback_pick = True
    else:
        chosen_is_fallback_pick = False

    if chosen:
        alive, status = verify_url(chosen["url"])
        entry["chosen"] = {
            "url": chosen["url"],
            "title": chosen.get("title", ""),
            "accepted_by_policy_and_specificity": chosen["accepted"],
            "was_only_a_fallback_pick": chosen_is_fallback_pick,
            "policy_class": chosen["policy_class"],
            "alive": alive,
            "http_status": status,
        }
        time.sleep(0.2)
    else:
        entry["chosen"] = None
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--strategies",
        default="baseline_grok,site_scoped_grok,alt_phrasing_grok,claude_web_search,gemini_google_search,openai_search",
        help="Comma-separated strategy labels to run (default: all).",
    )
    parser.add_argument("--items", default="", help="Comma-separated item names to restrict to (default: all 13 targets).")
    parser.add_argument("--env-file", default="C:/Dev/Sandbox/.env", help="Extra .env file to load before the repo's own .env.")
    args = parser.parse_args()

    load_dotenv(args.env_file)
    load_dotenv()

    xai_key = os.environ.get("XAI_API_KEY", "").strip()
    claude_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    session = requests.Session()

    strategy_registry: dict[str, tuple[Callable[[dict[str, Any]], str], Callable[[str], tuple[list[dict[str, str]], list[dict[str, str]]]], bool]] = {
        "baseline_grok": (baseline_query, lambda q: call_grok(q, session, xai_key), bool(xai_key)),
        "site_scoped_grok": (site_scoped_query, lambda q: call_grok(q, session, xai_key), bool(xai_key)),
        "alt_phrasing_grok": (alt_phrasing_query, lambda q: call_grok(q, session, xai_key), bool(xai_key)),
        "claude_web_search": (baseline_query, lambda q: call_claude(q, claude_key), bool(claude_key)),
        "gemini_google_search": (baseline_query, lambda q: call_gemini(q, gemini_key), bool(gemini_key)),
        "openai_search": (baseline_query, lambda q: call_openai(q, openai_key), bool(openai_key)),
    }

    requested = [s.strip() for s in args.strategies.split(",") if s.strip()]
    unknown = [s for s in requested if s not in strategy_registry]
    if unknown:
        raise SystemExit(f"Unknown strategies: {unknown}. Valid: {sorted(strategy_registry)}")

    item_filter = {s.strip() for s in args.items.split(",") if s.strip()}
    targets = [t for t in TARGETS if not item_filter or t["name"] in item_filter]
    if not targets:
        raise SystemExit("No targets match --items filter.")

    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    report: dict[str, Any] = {"run_stamp_utc": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), "targets": [t["name"] for t in targets], "strategies": {}}

    for label in requested:
        query_fn, call_fn, has_key = strategy_registry[label]
        if not has_key:
            print(f"[skip] {label}: API key not set")
            continue
        print(f"\n=== Strategy: {label} ===")
        entries: list[dict[str, Any]] = []
        for item in targets:
            print(f"  [{item['name']}] ...", end=" ", flush=True)
            entry = run_strategy_for_item(label, query_fn, call_fn, item, discoverer)
            entries.append(entry)
            chosen = entry.get("chosen")
            if entry.get("error"):
                print(f"ERROR: {entry['error']}")
            elif chosen:
                print(
                    f"{'HIT' if chosen['accepted_by_policy_and_specificity'] and chosen['alive'] else 'weak/no-hit'} "
                    f"url={chosen['url']!r} class={chosen['policy_class']} alive={chosen['alive']}"
                )
            else:
                print("no candidates returned")
        report["strategies"][label] = entries

    # ── Summarize hit rate per strategy ─────────────────────────────────────
    summary: dict[str, Any] = {}
    for label, entries in report["strategies"].items():
        hits = []
        for e in entries:
            chosen = e.get("chosen")
            if chosen and chosen["accepted_by_policy_and_specificity"] and chosen["alive"] is True:
                hits.append(e["item"])
        summary[label] = {"hit_count": len(hits), "total": len(entries), "hit_items": hits}
    report["summary"] = summary

    out_dir = REPO_ROOT / "docs" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "link_recall_strategy_experiment_results.json"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append("# Link Recall Strategy Experiment")
    lines.append("")
    lines.append(f"Run: {report['run_stamp_utc']}")
    lines.append("")
    lines.append("## Hit-rate summary")
    lines.append("")
    lines.append("| Strategy | Hits | Total | Items |")
    lines.append("|---|---|---|---|")
    for label, s in summary.items():
        lines.append(f"| {label} | {s['hit_count']} | {s['total']} | {', '.join(s['hit_items']) or '-'} |")
    lines.append("")
    lines.append("## Per-item detail")
    lines.append("")
    for label, entries in report["strategies"].items():
        lines.append(f"### {label}")
        for e in entries:
            chosen = e.get("chosen")
            if e.get("error"):
                lines.append(f"- **{e['item']}**: ERROR {e['error']} (query: `{e['query']}`)")
            elif chosen:
                verdict = "HIT" if (chosen["accepted_by_policy_and_specificity"] and chosen["alive"] is True) else "no-hit"
                lines.append(
                    f"- **{e['item']}** [{verdict}]: `{chosen['url']}` "
                    f"(class={chosen['policy_class']}, alive={chosen['alive']}, "
                    f"accepted={chosen['accepted_by_policy_and_specificity']}, query=`{e['query']}`)"
                )
            else:
                lines.append(f"- **{e['item']}**: no candidates (query: `{e['query']}`)")
        lines.append("")

    out_md = out_dir / "link_recall_strategy_experiment_results.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"\nWrote {out_json}")
    print(f"Wrote {out_md}")
    print("\n=== Hit-rate summary ===")
    for label, s in summary.items():
        print(f"  {label}: {s['hit_count']}/{s['total']}")


if __name__ == "__main__":
    main()
