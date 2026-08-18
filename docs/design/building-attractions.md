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
- Include all seed attractions
- Add enough items to reach 6-8 total
- Set `must_see=true` for no more than two items

These are prompt-level targets, not a strict hard cap at runtime.

## Runtime Normalization Rules
Normalization in `AIContentGenerator` enforces or adjusts list quality:

- Attraction type is normalized to lowercase.
- `must_see` is capped to a budget of two items.
- Similar attractions are merged by canonical-name similarity.
- Missing user seeds are injected into attractions when the model omits them.
- En-route stops are removed from the attractions list to avoid duplication.
- Seed attractions are protected from en-route overlap removal so user-requested anchors remain.

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

## Suggested Follow-On Design Notes
To keep architecture discoverable for new contributors, add sibling notes under `docs/design` for:
- `url-discovery-and-audit.md`
- `schedule-normalization.md`
- `image-selection-and-filtering.md`
- `html-assembly-pipeline.md`
