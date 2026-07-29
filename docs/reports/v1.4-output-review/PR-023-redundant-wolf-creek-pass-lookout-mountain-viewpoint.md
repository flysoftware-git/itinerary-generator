# PR-023: Redundant viewpoint activity callouts: Wolf Creek Pass and Lookout Mountain Viewpoint

Labels: `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:medium`, `area:ai-content`, `area:content-linking`, `area:deduplication`, `area:html-output`

**Fixed in:** v1.4.3 — `_deduplicate_within_destination` removes `Wolf Creek Pass Scenic Drive` from scenic_drives because all tokens of the `Wolf Creek Pass` top-attraction name appear in the drive title (80%+ overlap threshold). Note: empty scenic-drive popup behavior (Lookout Mountain Viewpoint) is an assembler/UI concern tracked under Epic 6.

Manifest: `trip_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

In Pagosa Springs content, viewpoint-style activities are redundantly represented:

- `Wolf Creek Pass` appears as a standalone attraction
- `Wolf Creek Pass Scenic Drive` appears as a scenic-drive entry
- `Lookout Mountain Viewpoint` appears as an additional viewpoint card with placeholder link behavior (`href="#"`)

This creates overlapping/duplicative activity coverage in the same section and weakens information quality.

## Expected Behavior

- Closely overlapping viewpoint activities should be reconciled into distinct, non-redundant callouts.
- If two entries describe effectively the same activity class and user value, merge or prioritize one canonical entry.
- Placeholder-link scenic/viewpoint cards should not be emitted without a validated link policy.

## Actual Behavior

- `Wolf Creek Pass` is emitted as an attraction linked to:
  - `https://en.wikipedia.org/wiki/Wolf_Creek_Pass`
- `Lookout Mountain Viewpoint` is emitted as a separate scenic/viewpoint card with:
  - `<a href="#" class="attr-link drive-link" data-drive-title="Lookout Mountain Viewpoint">`
- Both cards describe generic panoramic overlook activity with similar user intent.

## Evidence

- In [output/index.html](output/index.html):
  - line 1459: `Wolf Creek Pass` attraction card with external link
  - line 1460: `Wolf Creek Pass Scenic Drive` scenic card
  - line 1461: `Lookout Mountain Viewpoint` scenic/viewpoint card using placeholder link (`href="#"`)

## Reproduction Context

- Manifest used: `trip_manifest.yaml`
- Destination context: `Pagosa Springs`
- Entities involved: `Wolf Creek Pass`, `Wolf Creek Pass Scenic Drive`, `Lookout Mountain Viewpoint`

## Suspected Area

- Primary components: cross-list overlap reconciliation and placeholder-link suppression
- Possible files:
  - [generator/ai_content.py](generator/ai_content.py)
  - [generator/html_assembler.py](generator/html_assembler.py)
  - [generator/url_discovery.py](generator/url_discovery.py)

## Root Cause Hypothesis

- Attraction and scenic/viewpoint pipelines are merged without sufficient semantic overlap detection for near-substitute activities.
- Scenic/viewpoint items lacking validated URLs still render as placeholder-link cards.

## Scope of Likely Fix

- Add overlap scoring between attraction and scenic/viewpoint items within the same destination.
- Keep one canonical card when entries are near-duplicates in intent/value.
- Enforce no-placeholder-link policy for unresolved scenic/viewpoint links.

## Non-Breaking Validation Plan

- Unit tests:
  - overlap candidates (same corridor/pass/viewpoint intent) are de-duplicated.
  - placeholder-link scenic/viewpoint cards are suppressed or rendered without hyperlinking.
- Integration checks:
  - regenerate Pagosa Springs output and confirm redundant viewpoint activity cards are reduced.

## Notes

- This is intake-only; no implementation change is included.
