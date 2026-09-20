# Building Attractions Design Note

## Purpose
This note explains how the program builds the attractions list for each destination, how ordering is determined, and where URL ranking fits into the flow.

## Scope
This document covers:
- Generation and normalization of `top_attractions`
- De-duplication and removal of overlap with en-route stops
- Final display ordering in HTML output
- URL discovery behavior as a separate concern

This document does not cover:
- Scenic-drive popup assembly internals
- Restaurant selection details
- Cultural events selection details

## End-to-End Flow
1. Destination content generation asks the model for attraction candidates.
2. The model output is normalized for structure and policy constraints.
3. Attractions are de-duplicated and en-route overlap is removed.
4. URL discovery resolves/filters links for each attraction.
5. HTML assembly renders attractions in the normalized order.

## Input Contract From Prompt
The destination content prompt requests:
- Include all seed attractions, **described to the same standard as the rest**.
  A seed returned as a bare name is worse than one not listed at all: the
  traveller already knows they asked for it, so a card with only the name on it
  tells them nothing they did not write themselves.
- Add enough items to reach 6-8 total
- Set `must_see=true` for no more than two items

These are prompt-level targets, not a strict hard cap at runtime.

## Runtime Normalization Rules
Normalization in `AIContentGenerator` enforces or adjusts list quality:

- Attraction type is normalized to lowercase.
- `must_see` is capped to a budget of two items.
- Similar attractions are merged by canonical-name similarity.
- Missing user seeds are injected into attractions when the model omits them.
  What that injection writes is read on the trip, so it says why the place is
  on the itinerary and what to check before going, and nothing about this
  pipeline (`SEED_FALLBACK_DESCRIPTION`). It is a fallback for the run where
  generation was skipped or came back short, not the normal way a seed is
  described -- the prompt above is.
- En-route stops are removed from the attractions list to avoid duplication.
- Seed attractions are protected from en-route overlap removal so user-requested anchors remain.

## What A Seed Is Exempt From, And What It Is Not
A seed carries one exemption and it is narrow. Under the verified-link-or-seed
policy (`_keep_item_if_verified_or_seed`, owner decision 2026-08-17) a non-seed
attraction left with no verified link is removed from the itinerary; a seed
stays and renders with the `Unverified` badge, because an unverifiable seed may
be an obscure-but-real place rather than a pipeline failure, and silently
dropping a traveller's own request is the worse outcome.

That exemption is about **removal**, not about **effort**. A seed is searched,
audited and linked exactly like any other attraction, and three consequences
follow:

- The interest filter (`_is_uninterested_attraction`, keywords such as
  `bike trail` and out-of-season `ski`) does **not** apply to a seed, in either
  `_discover_attractions` or `audit_discovered_urls`. Naming a place in the
  manifest is the strongest statement of interest there is and it outranks a
  keyword guess about the category. Skipping a seed there used to be invisible:
  the removal exemption kept the card whatever happened, so the only symptom
  was a card with no link.
- A seed that is searched and still finds nothing is the honest case, and the
  card says so in as many words (`html_assembler`, beside the `Unverified`
  badge) rather than implying a later stage will fill the gap in.
- Everything else -- relevance gates, closure detection, the trail-miles
  threshold -- still applies, with the existing seed-specific relaxations
  (`seed_threshold_override`, `_search_alltrails_for_seed_relaxed`).

## Ordering Rules (What Determines Placement)
Attraction placement is deterministic after normalization and is not based on web page ranking.

Final sort key order is:
1. `must_see` first
2. Difficulty rank: `Strenuous`, `Moderate`, `Easy`, `N/A`, then unknown
3. Attraction name alphabetically

Result: list position is a product-content ordering decision, not a search-engine rank decision.

## URL Ranking vs Attraction Ordering
URL discovery has its own ranking/scoring logic to choose the best link for each attraction. That scoring influences the URL attached to an item, but not the item's place in the attractions list.

In other words:
- Attraction order: content normalization sort
- URL choice: URL discovery scoring and relevance gates

## Quantity Guidance: "How many is enough?"
Expected quantity is 6-8 attractions per destination from prompt guidance.

Important nuance:
- The code does not enforce a post-normalization *minimum* -- de-duplication
  and en-route pruning can still reduce final count below the prompt target.
- The code does enforce a post-normalization *maximum*: `_apply_manifest_attraction_target`
  caps the final list at `attractions_per_day * day_count` (default 4/day,
  configurable via the manifest `attractions_per_day` field), applied after
  de-duplication/en-route pruning and before URL discovery ever runs, since
  URL discovery only searches whatever this list hands it. Manifest-seeded
  attractions are always preserved regardless of the cap. See
  `docs/design/per-day-item-caps.md` for the full design (this cap now
  applies uniformly to restaurants and en-route stops too, not just
  attractions).

## Key Implementation Locations
- Prompt contract for attraction quantity and must-see constraints:
  - `prompts/destination_content.txt`
- Attraction normalization, de-duplication, and ordering:
  - `generator/ai_content.py`
- En-route overlap removal from attractions:
  - `generator/ai_content.py`
- Attraction URL discovery and relevance/ranking:
  - `generator/url_discovery.py`
- Final attractions rendering order:
  - `generator/html_assembler.py`

## Operational Implications
- If list quality looks wrong, inspect normalization and de-duplication first.
- If links look wrong but order looks right, inspect URL discovery/audit logic.
- If final count is too low, check for aggressive de-duplication or en-route overlap removals.
- If a requested seed attraction is missing, inspect seed canonicalization and the seed-injection path before URL discovery.
- If a seed renders with no link and nothing to say, look for
  `interest_filter_seed_override` in the decision log: it means the filter
  matched the seed and discovery ran anyway. Its absence beside a keyword match
  is the old behaviour, where the search never ran.
- If a non-trail feature loses its official page, check `trail_like`. It is
  inferred from the *description*, so "accessible via a short walk" is enough to
  classify a rock formation or a crater as a trail. The audit's AllTrails-only
  gate then strips any non-AllTrails URL it carries. Since 2026-08-29 the gate
  defers to the item's **title**: AllTrails when the name claims a trail,
  otherwise the official/NPS page (`_title_claims_a_trail`). See
  `url-discovery-and-audit.md`, Known Failure Modes.
- This whole class of failure is dormant while `trails.enabled` is false, which
  it was between the 2026-08-22 cost reduction and 2026-08-29. Re-enabling
  trails re-enables it; `sw` attraction removals rose 7 -> 17 in the next run.

## Suggested Follow-On Design Notes
To keep architecture discoverable for new contributors, add sibling notes under `docs/design` for:
- `url-discovery-and-audit.md`
- `schedule-normalization.md`
- `image-selection-and-filtering.md`
- `html-assembly-pipeline.md`
