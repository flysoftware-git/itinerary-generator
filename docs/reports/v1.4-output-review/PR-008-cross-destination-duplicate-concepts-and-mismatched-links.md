# PR-008: Same destination concept appears under multiple stops with conflicting context/link targets

Labels: `review:v1.4-output`, `type:bug`, `status:open`, `severity:medium`, `area:ai-content`, `area:url-discovery`, `area:content-linking`, `area:html-output`

Manifest: `C:/Dev/Sandbox/sw_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

The generated itinerary can surface the same destination concept in multiple destination sections with conflicting role/context, which creates confusion and weakens trust. Reported example: `Kolob Canyons` appears under Zion as a top attraction while `Kolob Canyons Road` appears under St. George as a scenic drive, effectively duplicating the same area under different destination ownership.

A related issue is that some links can be semantically mismatched to the exact shown item (for example, a general page that does not precisely represent the displayed location entity).

## Expected Behavior

- A destination-specific concept should be owned by one primary destination section unless explicitly marked as a day-trip crossover.
- If a concept appears in multiple sections, each occurrence should be intentional, clearly labeled, and non-conflicting.
- Link targets should correspond to the exact displayed entity (not just a nearby or loosely related page).
- Scenic-drive entries should avoid duplicating top-attraction concepts across neighboring stops without explicit route-context framing.

## Actual Behavior

- `Kolob Canyons` appears in Zion top attractions.
- `Kolob Canyons Road` appears in St. George scenic-drive items.
- This causes cross-destination conceptual duplication without explicit disambiguation.
- Link relevance may be inconsistent with the precise item identity in some cases.

## Evidence

- In [output/index.html](output/index.html), St. George section includes a scenic-drive card titled `Kolob Canyons Road`.
- In [output/index.html](output/index.html), Zion section includes top attraction `Kolob Canyons`.
- Scenic drive payload in [output/index.html](output/index.html) (`DRIVE_DESCRIPTIONS`) includes `Kolob Canyons Road` route metadata.
- Current pipeline generates scenic drives per destination independently, with no cross-destination concept dedupe in [generator/ai_content.py](generator/ai_content.py) / [generator/url_discovery.py](generator/url_discovery.py).

## Reproduction Context

- Manifest used: `C:/Dev/Sandbox/sw_manifest.yaml`
- Sections involved:
  - `St. George, Utah` (scenic-drive listing)
  - `Zion National Park` (top attraction listing)
- Reported examples:
  - `Kolob Canyons`
  - `Kolob Canyons Road`

## Suspected Area

- Primary components:
  - destination-scoped AI generation without trip-level concept reconciliation
  - URL assignment that validates generic relevance but not strict entity identity consistency
- Possible files:
  - [generator/ai_content.py](generator/ai_content.py)
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Scenic drives are produced per destination and later rendered without a trip-level canonical concept pass.
- There is no explicit cross-destination dedupe/ownership policy for nearby shared geography.
- Link selection may permit pages that are broadly related but not always precise to the specific labeled entity.

## Scope of Likely Fix

- Introduce trip-level concept reconciliation for attractions/scenic drives:
  - canonicalize names and detect near-duplicate entities across adjacent destinations.
  - enforce ownership policy (primary destination vs explicit day-trip crossover).
- Add explicit crossover labels where duplication is intentional.
- Tighten entity-link matching for ambiguous/nearby concepts so the linked page matches the displayed item identity.

## Non-Breaking Validation Plan

- Unit tests:
  - canonical matching for concept variants (for example `Kolob Canyons` vs `Kolob Canyons Road`).
  - ownership policy behavior for adjacent destinations.
- Integration checks:
  - full manifest generation verifying no unintended cross-destination duplicates for major concept clusters.
  - link-target sanity checks for duplicated/near-duplicated concept names.
- Guardrails:
  - preserve legitimate cross-destination references when intentionally marked as day trips.

## Notes

- This issue is related to but distinct from PR-004.
- PR-004 focuses on scenic-drive link target type quality (place page vs route page).
- PR-008 focuses on cross-destination concept duplication and entity ownership consistency.
- No implementation performed in this report; this is investigation and scoping only.
