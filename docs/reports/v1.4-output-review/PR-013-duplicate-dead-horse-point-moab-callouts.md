# PR-013: Moab contains duplicate Dead Horse Point State Park callouts, including placeholder-link viewpoint card

Labels: `review:v1.4-output`, `type:bug`, `status:open`, `severity:medium`, `area:ai-content`, `area:html-output`, `area:content-linking`, `area:deduplication`

Manifest: `trip_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

Within the Moab destination content, `Dead Horse Point State Park` appears twice as separate callouts:

- one standard attraction with a valid external link
- one additional viewpoint/scenic card for the same entity rendered as a placeholder link (`href="#"`)

This creates duplicate entity presentation and an invalid secondary callout link.

## Expected Behavior

- A named entity should appear once per destination section unless explicitly disambiguated as a distinct sub-stop.
- Duplicate callouts for the same named entity should be merged or de-duplicated.
- Placeholder links (`href="#"`) should not be emitted for duplicate/generated entity cards.

## Actual Behavior

- Moab includes `Dead Horse Point State Park` as:
  - attraction card linked to `https://www.discovermoab.com/dead-horse-point-state-park/`
  - separate viewpoint/scenic card using placeholder anchor link (`href="#"`)

## Evidence

- In [output/index.html](output/index.html):
  - line 1105: linked attraction card for `Dead Horse Point State Park`
  - line 1112: additional `Dead Horse Point State Park` viewpoint card with `<a href="#" ...>`

## Reproduction Context

- Manifest used: `trip_manifest.yaml`
- Destination: `Moab, UT`
- Entity: `Dead Horse Point State Park`

## Suspected Area

- Primary components: attraction/scenic-drive reconciliation and duplicate suppression
- Possible files:
  - [generator/ai_content.py](generator/ai_content.py)
  - [generator/html_assembler.py](generator/html_assembler.py)
  - [generator/url_discovery.py](generator/url_discovery.py)

## Root Cause Hypothesis

- Scenic/viewpoint generation and attraction lists are merged without entity-level de-duplication, resulting in a second card for an already-listed entity.
- Secondary generated card lacks a validated URL and falls back to placeholder anchor output.

## Scope of Likely Fix

- Add entity-level canonicalization and duplicate detection across attraction and scenic/viewpoint lists per destination.
- Prevent emission of duplicate entity cards when one canonical card already exists.
- Enforce no-placeholder-link policy for generated cards.

## Non-Breaking Validation Plan

- Unit tests:
  - same normalized entity name appearing in attraction + scenic/viewpoint sources yields one rendered callout.
  - duplicate scenic/viewpoint entity cards are suppressed.
  - no `href="#"` for rendered entity cards.
- Integration checks:
  - regenerate output and verify Moab contains one `Dead Horse Point State Park` callout.

## Notes

- This report is intake-only; no implementation change is included here.
