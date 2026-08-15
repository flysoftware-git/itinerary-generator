"""Supplementary re-run: grok_live_search and openai_search were fixed after
the first pass (deprecated endpoint / incompatible parameter respectively);
claude_web_search mostly timed out at 90s. Re-runs just those three configs
across the same 4 test cases with fixes applied and a longer Claude timeout."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from probe_multi_provider_search_2026 import (  # noqa: E402
    TEST_CASES,
    URLDiscoverer,
    _real_prompt,
    analyze_response,
    call_claude,
    call_grok,
    call_openai,
)
from dotenv import load_dotenv  # noqa: E402

load_dotenv("C:/Dev/Sandbox/.env")
load_dotenv()

discoverer = URLDiscoverer.__new__(URLDiscoverer)

configs = [
    ("grok_live_search_v2 (responses API, fixed)", lambda s, u: call_grok(s, u, live_search=True, model="grok-4.5", timeout=90)),
    ("openai_search_v2 (gpt-5-search-api, fixed)", lambda s, u: call_openai(s, u, model="gpt-5-search-api", json_mode=False, timeout=90)),
    ("claude_web_search_v2 (longer timeout)", lambda s, u: call_claude(s, u, model="claude-opus-5", use_search=True, timeout=150)),
]

run_stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
run_dir = REPO_ROOT.parent / "output" / "dev" / "multi_provider_search_probe" / f"{run_stamp}-rerun"
raw_dir = run_dir / "raw"
raw_dir.mkdir(parents=True, exist_ok=True)

report: dict = {"run_stamp_utc": run_stamp, "cases": []}

for dest_name, dates, kind in TEST_CASES:
    system_prompt, user_prompt = _real_prompt(discoverer, kind, dest_name, dates)
    case_result: dict = {"destination": dest_name, "kind": kind, "providers": {}}
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
                f"dead={analysis['rows_verified_dead']} citations={analysis['citation_count']} "
                f"not_in_citations={analysis['html_urls_not_in_citations']}"
            )
        except Exception as exc:  # noqa: BLE001
            case_result["providers"][label] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"FAILED: {type(exc).__name__}: {exc}")
    report["cases"].append(case_result)

(run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(f"\nWrote {run_dir / 'report.json'}")
