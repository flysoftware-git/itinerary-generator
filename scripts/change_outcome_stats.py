"""change_outcome_stats.py — first-pass success and detection cost, for iteration planning.

    python scripts/change_outcome_stats.py

Reads docs/reports/change-outcomes.jsonl and reports the rates that actually
govern how many iterations a piece of work takes to converge:

  - first-pass success: how often a change achieved its intent and broke nothing
  - failure modes: failed_to_fix vs injected_defect (independent -- a change can
    hit its goal and still break something else)
  - DETECTION COST: what it took to notice. This is the planning lever. A defect
    caught by a test costs a rerun of the suite; one caught by a paid run costs
    money and wall-clock; one caught by the user costs their attention and trust.

Why detection cost matters more than the raw failure rate: the failure rate sets
how many iterations are needed, but the detection channel sets what each
iteration costs. Halving the failure rate is hard; moving detection from
paid_run to tests is often just a matter of writing the assertion first.

Records with outcome "pending" are excluded from rates and counted separately --
they are changes awaiting a measurement that has not happened yet.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

LEDGER = Path(__file__).resolve().parent.parent / "docs" / "reports" / "change-outcomes.jsonl"


def load(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        if "_schema" in entry:  # header line, not a record
            continue
        records.append(entry)
    return records


def main() -> int:
    if not LEDGER.exists():
        print(f"No ledger at {LEDGER}")
        return 1
    records = load(LEDGER)
    settled = [r for r in records if r.get("outcome") != "pending"]
    pending = [r for r in records if r.get("outcome") == "pending"]

    if not settled:
        print("No settled records yet.")
        return 0

    n = len(settled)
    outcomes = collections.Counter(r["outcome"] for r in settled)
    injected = [r for r in settled if r.get("injected_defect")]
    # "Clean first pass" is the honest bar: achieved intent AND broke nothing.
    clean = [r for r in settled if r["outcome"] == "clean" and not r.get("injected_defect")]

    print(f"Behaviour-changing operations (settled): {n}   pending: {len(pending)}")
    print()
    print(f"  clean first pass        {len(clean):3}  {len(clean)/n:6.0%}")
    for name in ("incomplete", "failed_to_fix"):
        c = outcomes.get(name, 0)
        print(f"  {name:23} {c:3}  {c/n:6.0%}")
    print(f"  injected a new defect   {len(injected):3}  {len(injected)/n:6.0%}   (independent of outcome)")
    print()

    print("Detection channel — what it took to notice a problem:")
    problems = [r for r in settled if r["outcome"] != "clean" or r.get("injected_defect")]
    if not problems:
        print("  (none)")
    else:
        by_channel = collections.Counter(r.get("detected_by", "?") for r in problems)
        for channel, count in by_channel.most_common():
            print(f"  {channel:20} {count:3}  {count/len(problems):6.0%}")
        expensive = [r for r in problems if r.get("cost_to_detect") in {"paid_run", "user_time"}]
        print()
        print(f"  caught only by a paid run or the user: {len(expensive)}/{len(problems)}"
              f"  ({len(expensive)/len(problems):.0%} of problems)")

    lat = [r.get("detected_after_ops") for r in problems if isinstance(r.get("detected_after_ops"), int)]
    if lat:
        print(f"  detection latency (operations): min {min(lat)}, max {max(lat)}, mean {sum(lat)/len(lat):.1f}")

    print()
    print("Planning implication:")
    rate = len(clean) / n
    if rate >= 0.999:
        print("  Every settled change landed clean. Budget 1 pass per change.")
    else:
        # Expected passes for a geometric process with per-pass success `rate`.
        print(f"  First-pass success {rate:.0%} -> expect ~{1/rate:.1f} passes per change to converge.")
        print(f"  Budget {1/rate:.1f}x the nominal effort for anything on this codebase's")
        print("  discovery/cost paths, and prefer changes whose failure a TEST can catch.")
    if pending:
        print()
        print("Pending (awaiting measurement):")
        for r in pending:
            print(f"  {r['commit']}  {r['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
