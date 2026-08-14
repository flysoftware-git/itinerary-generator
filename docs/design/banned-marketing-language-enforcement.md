# Banned Marketing-Language Enforcement

## Purpose
`prompts/system_prompt.txt`'s "Avoid without exception" list bans a set of
travel-writing clichés ("stunning", "hidden gem", "iconic", ...) from
generated prose. Before this, that instruction was the *only* mechanism —
enforced nowhere in code. It was routinely violated: a real run
(Dipstick48) had 28 occurrences despite the "without exception" wording,
dominated by "stunning" (20 occurrences alone). This document describes the
deterministic, code-level enforcement added to close that gap.

## Canonical List
`AIContentGenerator.BANNED_MARKETING_PHRASES` in `generator/ai_content.py` is
the single source of truth:

```
hidden gem, off the beaten path, world-class, iconic, stunning,
breathtaking, charming, nestled, boasts, spectacular, majestic
```

`prompts/system_prompt.txt` and `prompts/scenic_drives.txt` both carry a
human-readable copy of this list for the LLM's benefit (a soft signal that
demonstrably reduces but does not eliminate violations); they must be kept
in sync with the code list by hand — there is no build step that generates
the prompt text from the code constant.

**`"must-see"` is a deliberate exception.** It's used as a structured badge
label (`badge-mustsee`, see `url-discovery-and-audit.md`'s "Must-See Badge
Policy") gated on verified rating/vote data, not a subjective prose claim —
banning the word from prose while relying on it as a badge label would be
self-contradictory.

## Enforcement Mechanism
Policy decision: **deterministic removal**, not synonym substitution or LLM
regeneration-retry. Rationale:
- All real observed violations are pre-nominal adjectives ("stunning red
  rock formations", "charming town") that drop cleanly without breaking
  grammar.
- Substitution would require curating and maintaining a synonym map for
  uncertain benefit.
- Regeneration would add a real API call (cost + latency) purely for word
  choice, working against this project's broader runtime/cost-reduction
  effort.

`AIContentGenerator._strip_banned_marketing_language(text, violation_counts=None)`:
1. Removes every banned phrase (case-insensitive, word-boundary matched).
2. Runs a dangling-copula cleanup pass for the common sentence-final
   predicate pattern these clichés often appear in ("is a hidden gem.", "is
   off the beaten path.") — without it, removal alone would leave "is a."
   or "is." Handles `is/are/was/were/remains/becomes (a/an)?` immediately
   before terminal punctuation or end of string.
3. Collapses resulting double-spaces and space-before-punctuation.

Known gap: a mid-sentence verb usage of "boasts" (e.g. "the inn boasts a
patio") would leave a dangling subject with no verb after removal. Not
observed in practice yet (zero occurrences in the evidence run), so not
specially handled — revisit if it shows up in a real run.

## Scope: Which Fields Get Scrubbed
Only an explicit allowlist of prose field names
(`AIContentGenerator._PROSE_FIELD_NAMES`) is ever rewritten:

```
description, practical_note, summary, local_customs, best_times_of_day,
transportation_quirks, route_summary, best_time
```

`_scrub_banned_language_in_place` walks the full destination content
structure (`ai_content`, `what_to_know`, `scenic_drives`) recursively,
descending into every dict/list, but only rewrites string values under an
allowlisted key. This is deliberate and load-bearing: a business genuinely
named "The Charming Cafe" (a real, verifiable name — see the fail-closed
named-entity policy) must never be mangled by a blind text scrub. `name`,
`title`, `url`, `type`, `difficulty`, `cuisine`, `price_range`, and every
other structural/enum/numeric field are traversed into (for nested prose)
but never rewritten themselves.

## Pipeline Placement
Runs as the first step of `AIContentGenerator.normalize_trip_content`,
before cross-section/cross-destination dedup — placed early so that two
near-duplicate descriptions differing only by a banned word are compared
post-scrub, not pre-scrub. This means it also runs *after* content
generation (`generate_all`) completes but *before* HTML assembly, matching
where all other post-generation normalization already happens.

Violation counts are returned from `_enforce_banned_marketing_language`,
stored on `AIContentGenerator.last_banned_phrase_violations`, and copied
into `main.py`'s `runtime_metrics["banned_phrase_violations"]` (captured
after the *first* `normalize_trip_content` call, since the selective-retry
pass's second call doesn't re-run content generation and would otherwise
overwrite the real count with an empty one).

## Key Files
- `generator/ai_content.py` — `BANNED_MARKETING_PHRASES`, `_PROSE_FIELD_NAMES`,
  `_strip_banned_marketing_language`, `_scrub_banned_language_in_place`,
  `_enforce_banned_marketing_language`
- `prompts/system_prompt.txt`, `prompts/scenic_drives.txt` — human-readable
  copies of the list, kept in sync by hand
- `generator/main.py` — `runtime_metrics["banned_phrase_violations"]` wiring
