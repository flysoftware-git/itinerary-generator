"""Research experiment (NOT a production change): does asking for the places
LOCALS go find better restaurants than asking for "local restaurants"?

The question. `_restaurant_direct_batch_query` asks for "a list of local
restaurants for <place>", and *local* there is doing almost no work -- it reads
as *in this town*, which every candidate already is. A search phrased that way
returns what the web ranks highest for a town's dining, and what the web ranks
highest is not what a resident would tell you: it skews to chains with strong
SEO, to aggregator pages, and -- measured on a real build -- to businesses that
are not restaurants at all. A caterer in Oak Harbor, WA shipped in a guide as
somewhere to have dinner.

*Favourite of locals* is a different question, and the guess worth testing is
that it is a better one. It may also be worse: it is the kind of phrasing that
invites listicles ("10 spots only locals know"), which are aggregator pages, and
those are exactly what the link policy refuses.

WHAT THIS MEASURES, per stop, per phrasing. The four the owner named:

  chain_rate                how many returned names are chains. A chain is the
                            thing the prompt already excludes and still gets.
  verified_link_survival    how many returned links survive the REAL acceptance
                            path -- policy class, specificity, and a live HTTP
                            check -- because a better-sounding list of dead or
                            policy-blocked links is not better.
  caterer_rate              how many are businesses with nowhere to walk in and
                            eat, by the same check production uses.
  overlap                   how much the two lists agree. A phrasing that
                            returns the same restaurants in a different order
                            has changed nothing, whatever its other numbers say,
                            and this is the metric that says so.

WHAT IT DOES NOT DO. It does not change any query, any prompt, or anything under
generator/. It imports the real query builder and the real acceptance helpers
rather than restating them -- the pattern scripts/probe_multi_provider_search_2026.py
and scripts/experiment_link_recall_strategies.py already establish, and for the
same reason: a phrasing that "wins" against a hand-rolled copy of the rules has
won nothing.

COST. Two search calls per stop, six stops: twelve calls. Search fees dominate a
real run, so the stop list is short, fixed, and chosen rather than sampled --
one of them is Oak Harbor, which is where the caterer shipped.

Usage:
  python scripts/experiment_locals_favourite_phrasing.py
  python scripts/experiment_locals_favourite_phrasing.py --stop "Oak Harbor, Washington"
  python scripts/experiment_locals_favourite_phrasing.py --dry-run   # prints the
      two queries per stop and exits, spending nothing

Writes docs/reports/locals_favourite_phrasing_results.json and .md, per this
repo's docs/reports/ convention for experiment write-ups.

NOT YET RUN. Written 2026-09-22 in an environment with no search credentials and
no outbound network, so the numbers do not exist yet and no query has been
changed on the strength of them. `--dry-run` works anywhere and is what was used
to check the two queries are what this docstring says they are.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generator.url_discovery import URLDiscoverer  # noqa: E402

REPORT_JSON = REPO_ROOT / "docs" / "reports" / "locals_favourite_phrasing_results.json"
REPORT_MD = REPO_ROOT / "docs" / "reports" / "locals_favourite_phrasing_results.md"

#: Six stops, chosen rather than sampled, because twelve search calls is the
#: budget and a random six would tell us about randomness.
#:
#: Oak Harbor is here because the defect is: a caterer shipped in its guide as a
#: dinner recommendation. The rest span the axis the phrasing is supposed to
#: move along -- a small town where a resident's answer and the web's answer are
#: most likely to differ, up to a city where the web has plenty to say and the
#: risk is listicles rather than chains.
STOPS: tuple[str, ...] = (
    "Oak Harbor, Washington",       # the measured defect
    "Port Townsend, Washington",    # small town, strong independent scene
    "Moab, Utah",                   # tourist town: where chains rank hardest
    "Marquette, Michigan",          # small city, little national SEO
    "Santa Fe, New Mexico",         # city with a real food press
    "Munising, Michigan",           # very small; the thin-data case
)

#: The production phrasing, and the one being tested against it. Only the FIRST
#: sentence differs -- everything after it (price clause, the $/cuisine
#: contract, the ratings and links instructions) is the real builder's, taken
#: verbatim, so the comparison isolates the wording and nothing else.
BASELINE_OPENER = "Generate a list of local restaurants for "
LOCALS_FAVOURITE_OPENER = (
    "Generate a list of the restaurants that are favourites of the people who live in "
)

#: Chain names, for the metric. Deliberately a short explicit list rather than a
#: cleverness: this counts a known failure mode, and a heuristic that guessed
#: would make the number unreadable. Extend it when a run shows a chain it
#: missed -- and say in the report that it was extended, because a metric whose
#: denominator moved between runs is not a comparison.
CHAINS: tuple[str, ...] = (
    "applebee", "arby", "burger king", "chili's", "chipotle", "cracker barrel",
    "denny", "dairy queen", "domino", "dunkin", "five guys", "hardee",
    "ihop", "jack in the box", "jimmy john", "kfc", "little caesar",
    "mcdonald", "olive garden", "outback", "panda express", "panera",
    "papa john", "pizza hut", "popeyes", "red lobster", "red robin",
    "starbucks", "subway", "taco bell", "texas roadhouse", "tgi friday",
    "wendy", "buffalo wild wings", "cheesecake factory", "carl's jr",
)

POLICY_BLOCKED_CLASSES = {
    "google_maps_search", "google_maps_dir", "google_search", "social_media",
}


def _discoverer() -> URLDiscoverer:
    """The real thing, unconfigured -- the bare-`__new__` pattern the sibling
    experiment scripts use. Only the pure helpers are called."""
    return URLDiscoverer.__new__(URLDiscoverer)


def queries_for(stop: str, dates: str = "") -> dict[str, str]:
    """The two queries, both built by the REAL builder.

    The variant is the production query with its opening clause replaced, so
    every other instruction -- and every later change to any of them -- is
    shared by both arms automatically. Restating the query here would make this
    an experiment about a copy of the prompt.
    """
    baseline = _discoverer()._restaurant_direct_batch_query(stop, dates)
    if not baseline.startswith(BASELINE_OPENER):
        raise SystemExit(
            "The production restaurant query no longer opens with "
            f"{BASELINE_OPENER!r}. This script replaces that clause and must "
            "not guess at a new one -- read _restaurant_direct_batch_query and "
            "update the opener here deliberately, or the two arms stop being "
            "the same question asked two ways."
        )
    variant = LOCALS_FAVOURITE_OPENER + baseline[len(BASELINE_OPENER):]
    return {"baseline": baseline, "locals_favourite": variant}


def _is_chain(name: str) -> bool:
    lowered = " ".join(str(name or "").lower().split())
    return any(chain in lowered for chain in CHAINS)


def _is_caterer(row: dict[str, Any]) -> bool:
    """By the same check production uses, where this checkout has it.

    Falls back to False rather than to a local copy: a metric computed by a
    rule production does not apply is a metric about this script.
    """
    says = getattr(URLDiscoverer, "_serves_no_walk_in_diner", None)
    names = getattr(URLDiscoverer, "_name_says_it_is_a_caterer", None)
    if says is None or names is None:
        return False
    text = " ".join(str(row.get(k, "") or "") for k in ("title", "snippet", "description"))
    return bool(says(text)) or bool(names(row.get("title") or row.get("name") or ""))


def _key(row: dict[str, Any]) -> str:
    """What makes two rows the same restaurant, for the overlap figure."""
    name = " ".join(str(row.get("title") or row.get("name") or "").lower().split())
    return re.sub(r"^(the|le|la|el)\s+", "", name).strip(" .,'\"-")


def _link_survives(discoverer: URLDiscoverer, url: str, name: str, stop: str) -> bool:
    """The real acceptance path, then a live check. Both, in that order.

    A URL that the policy refuses never reaches a reader however alive it is,
    and a URL that passes the policy and 404s reaches them as a dead link. The
    metric is about what a reader would actually get.
    """
    url = str(url or "").strip()
    if not url:
        return False
    try:
        if discoverer._classify_url_policy_class(url) in POLICY_BLOCKED_CLASSES:
            return False
        if not discoverer._is_specific_result_url(url):
            return False
    except Exception:
        return False
    import requests

    try:
        answer = requests.head(url, timeout=8, allow_redirects=True)
        if answer.status_code >= 400:
            answer = requests.get(url, timeout=12, allow_redirects=True, stream=True)
        return answer.status_code < 400
    except Exception:
        return False


def _score(rows: list[dict[str, Any]], stop: str) -> dict[str, Any]:
    discoverer = _discoverer()
    total = len(rows)
    if not total:
        return {"returned": 0, "chain_rate": None, "caterer_rate": None,
                "verified_link_survival": None, "names": []}
    chains = [r for r in rows if _is_chain(r.get("title") or r.get("name") or "")]
    caterers = [r for r in rows if _is_caterer(r)]
    survived = [r for r in rows
                if _link_survives(discoverer, r.get("url", ""),
                                  r.get("title") or r.get("name") or "", stop)]
    return {
        "returned": total,
        "chain_rate": round(len(chains) / total, 3),
        "caterer_rate": round(len(caterers) / total, 3),
        "verified_link_survival": round(len(survived) / total, 3),
        "names": [r.get("title") or r.get("name") or "" for r in rows],
        "chains": [r.get("title") or r.get("name") or "" for r in chains],
        "caterers": [r.get("title") or r.get("name") or "" for r in caterers],
    }


def _overlap(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> float | None:
    """Jaccard over the two name sets.

    The metric that can say *this changed nothing*. A phrasing whose other three
    numbers all improve while its overlap is 1.0 has reordered one list, and the
    improvements are noise in how the rows were read.
    """
    left = {_key(r) for r in a if _key(r)}
    right = {_key(r) for r in b if _key(r)}
    if not left and not right:
        return None
    return round(len(left & right) / len(left | right), 3)


def _ask(query: str) -> list[dict[str, Any]]:
    """One search call. Raises SystemExit with the reason when it cannot be made.

    Deliberately the only place in this script that needs a credential or a
    socket, so everything above it can be exercised -- and was -- in an
    environment that has neither.
    """
    from generator.search_provider import build_search_client  # noqa: WPS433

    key_names = ("XAI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SERPER_API_KEY")
    if not any(os.environ.get(name) for name in key_names):
        raise SystemExit(
            "No search credential in the environment (looked for "
            + ", ".join(key_names) + "). This experiment makes real search "
            "calls and there is nothing to report without them."
        )
    client = build_search_client(
        str(REPO_ROOT / "config.yaml"), config_section="url_discovery")
    text = client.chat_completion(query)
    try:
        parsed = json.loads(text) if text.strip().startswith("{") else {}
    except Exception:
        parsed = {}
    rows = parsed.get("results") or parsed.get("restaurants") or []
    return [r for r in rows if isinstance(r, dict)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # Repeatable rather than comma-separated: a stop name HAS a comma in it
    # ("Oak Harbor, Washington"), and splitting on one turned six stops into
    # twelve, half of them the word "Washington".
    parser.add_argument("--stop", action="append", default=[],
                        help="repeatable; default is the six above")
    parser.add_argument("--dates", default="", help="passed to the real query builder")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the two queries per stop and exit, spending nothing")
    args = parser.parse_args()

    stops = [s.strip() for s in args.stop if s.strip()] or list(STOPS)

    if args.dry_run:
        for stop in stops:
            pair = queries_for(stop, args.dates)
            print(f"\n=== {stop}")
            for arm, query in pair.items():
                print(f"  [{arm}] {query}")
        print(f"\n{len(stops)} stops x 2 arms = {len(stops) * 2} search calls when run for real.")
        return 0

    results: dict[str, Any] = {"stops": {}, "arms": ["baseline", "locals_favourite"]}
    for stop in stops:
        pair = queries_for(stop, args.dates)
        rows = {arm: _ask(query) for arm, query in pair.items()}
        results["stops"][stop] = {
            "queries": pair,
            "baseline": _score(rows["baseline"], stop),
            "locals_favourite": _score(rows["locals_favourite"], stop),
            "overlap": _overlap(rows["baseline"], rows["locals_favourite"]),
        }

    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    REPORT_MD.write_text(_as_markdown(results), encoding="utf-8")
    print(f"Wrote {REPORT_JSON} and {REPORT_MD}")
    return 0


def _as_markdown(results: dict[str, Any]) -> str:
    lines = [
        "# Does *favourite of locals* find better restaurants?",
        "",
        "Produced by `scripts/experiment_locals_favourite_phrasing.py`. Nothing in",
        "`generator/` or `prompts/` was changed to produce it.",
        "",
        "| stop | arm | returned | chain | caterer | link survives | overlap |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for stop, row in results.get("stops", {}).items():
        for arm in results.get("arms", []):
            got = row.get(arm) or {}
            lines.append(
                f"| {stop} | {arm} | {got.get('returned')} | {got.get('chain_rate')} | "
                f"{got.get('caterer_rate')} | {got.get('verified_link_survival')} | "
                f"{row.get('overlap') if arm == 'baseline' else ''} |"
            )
    lines += [
        "",
        "**Read the overlap column first.** A phrasing whose other three numbers",
        "improve while its overlap is near 1.0 returned the same restaurants in a",
        "different order, and the improvement is in how the rows were read rather",
        "than in what was found.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
