#!/usr/bin/env python
"""Ask the Google Routes API whether it knows anything about a manifest's legs.

Why this is committed
---------------------
`docs/design.md` §4.5 item 14 records that `scripts/probe_multi_provider_search_2026.py`
was cited as re-runnable by two design notes and is not in the repository.
`docs/design/multimodal-routing.md` §2.2 asks for this probe and says, in as
many words, to commit it. So: committed.

It exists because the most instructive episode in this project's history is
Grok's search having been silently dead for months while code was built on
top of it. A transit corridor with no coverage does not error -- it returns
an empty `routes` list, which reads exactly like a slow day.

What it found on 2026-09-02
---------------------------
Every leg of `Japan_manifest.yaml` answered EMPTY, with and without a pinned
departure time, while the control corridor (Brussels -> Amsterdam) answered
in 220 minutes. Japanese rail is not in the feed set this API routes on. That
matters twice over:

  * `multimodal-routing.md` §8 open question 8 picked Japan as the acceptance
    corridor *because* its four legs are rail-served. True of the world, not
    of this API -- Japan proves the Phase 1 AI path and cannot ever prove the
    API-backed one.
  * A declared-transit leg on an uncovered corridor spends one call per run to
    learn nothing. Run this before assuming a manifest will benefit.

Usage
-----
    python scripts/probe_transit_coverage.py <manifest.yaml> [--departure 2026-10-14T09:00:00Z]

Needs GOOGLE_MAPS_PLATFORM_KEY. Costs one Routes call per leg plus one for the
control. Prints a per-leg table and exits non-zero if nothing answered.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generator.transit_estimate import TransitEstimator  # noqa: E402

#: A corridor known to answer, so "everything empty" can be told apart from
#: "the key is wrong". Europe is where this module's figures came from.
CONTROL_LEG = ("Brussels", "Amsterdam")


def _load_env(env_file: str) -> None:
    path = Path(env_file)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--departure", default="", help="ISO departure time to pin")
    parser.add_argument("--env-file", default="", help="Optional .env to load first")
    args = parser.parse_args()

    if args.env_file:
        _load_env(args.env_file)

    import yaml

    data = yaml.safe_load(Path(args.manifest).read_text(encoding="utf-8")) or {}
    names = [
        str(d.get("name", "") or "").strip()
        for d in (data.get("destinations") or [])
        if isinstance(d, dict) and not str(d.get("group_with", "") or "").strip()
    ]
    legs = [(a, b) for a, b in zip(names, names[1:]) if a and b]
    if not legs:
        print("No adjacent legs in that manifest.")
        return 2

    estimator = TransitEstimator()
    if not estimator.available:
        print("GOOGLE_MAPS_PLATFORM_KEY is not set — nothing to probe.")
        return 2

    print(f"{len(legs)} leg(s), departure={args.departure or 'unpinned'}\n")
    answered = 0
    for origin, destination in legs:
        result = estimator.estimate(origin, destination, departure_iso=args.departure)
        if result:
            answered += 1
            print(f"  ANSWERED  {origin} -> {destination}: "
                  f"{result['minutes']} min, {result.get('miles')} mi")
        else:
            print(f"  EMPTY     {origin} -> {destination}")

    control = estimator.estimate(*CONTROL_LEG, departure_iso=args.departure)
    print(f"\n  CONTROL   {CONTROL_LEG[0]} -> {CONTROL_LEG[1]}: "
          + (f"{control['minutes']} min" if control else "EMPTY — suspect the key, not the coverage"))

    print(f"\n{answered}/{len(legs)} answered, {estimator.call_count} call(s) made.")
    if answered == 0:
        print("No coverage here. A declared-transit leg on this manifest will fall back to "
              "the Phase 1 duration band, and each run spends a call to rediscover that.")
    return 0 if answered else 1


if __name__ == "__main__":
    raise SystemExit(main())
