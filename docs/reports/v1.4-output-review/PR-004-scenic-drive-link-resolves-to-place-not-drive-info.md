# PR-004: Scenic drive more-info links can resolve to place pages instead of route-specific drive info

Labels: `review:v1.4-output`, `type:bug`, `status:open`, `severity:medium`, `area:url-discovery`, `area:content-linking`, `area:html-output`

Manifest: `C:/Dev/Sandbox/sw_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

Some scenic-drive more-info links appear to resolve to a general place page that is already represented elsewhere in the itinerary rather than to route- or drive-specific information. The reported example is `Snow Canyon Scenic Drive`, whose linked context points back to `Snow Canyon State Park`, which is already covered as a top attraction, instead of answering drive-specific questions such as where the drive starts, what the route is, and what operational details apply.

## Expected Behavior

- Scenic-drive more-info links should prefer route-specific information.
- Drive links should answer drive questions first, for example:
  - where the drive starts
  - route/segment overview
  - shuttle or access rules
  - timing, wait, closures, or vehicle constraints
- Place pages already used as attraction links should not be reused as the drive-info destination unless they genuinely contain drive-specific operational guidance.

## Actual Behavior

- `Snow Canyon State Park` already appears as a top attraction in the St. George section.
- `Snow Canyon Scenic Drive` is separately rendered as a drive/day-trip item.
- The reported more-info linkage for the drive is effectively anchored to the same place concept instead of dedicated route guidance.

## Evidence

- [output/index.html](output/index.html) contains a top attraction link for `Snow Canyon State Park`.
- [output/index.html](output/index.html) contains a separate scenic-drive item for `Snow Canyon Scenic Drive`.
- Review context indicates the popup/more-info path is using the park page rather than route-specific drive information.

## Reproduction Context

- Manifest used: `C:/Dev/Sandbox/sw_manifest.yaml`
- Destination/section: `St. George, Utah`
- Related attraction: `Snow Canyon State Park`
- Related drive: `Snow Canyon Scenic Drive`

## Suspected Area

- Primary component: scenic-drive URL discovery relevance and link assignment
- Possible files:
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Scenic-drive discovery currently optimizes for any relevant page tied to the named destination/landmark, not necessarily a route-specific drive explainer.
- This can allow place-level pages to satisfy drive discovery if they loosely match the drive title or surrounding destination context.
- The selection logic likely lacks a stronger distinction between:
  - route/drive information pages
  - general attraction/place pages

## Scope of Likely Fix

- Tighten scenic-drive relevance rules so drive links prefer route-specific content over parent attraction/place pages.
- Potentially add route-intent heuristics for drive discovery, for example terms like:
  - scenic drive
  - route
  - road
  - byway
  - shuttle
  - access
  - viewpoint stops
- Preserve valid place-page links when they are the only practical source and genuinely contain drive instructions, but do not treat them as the default success case.

## Non-Breaking Validation Plan

- Cheapest feasibility run:
  - destination-local run for `St. George, Utah`
- Additional validation:
  - regression checks for scenic-drive links in Zion, Bryce, Capitol Reef, and Pagosa Springs
  - confirm attraction links remain intact while drive links become more route-specific
- Guardrail:
  - avoid over-tightening relevance such that legitimate drive links are dropped to none

## Notes

- This is related to scenic-drive modeling but is distinct from PR-003.
- PR-003 concerns duplicated teaser/popup text.
- This report concerns semantic quality of the drive's more-info destination.
- No implementation performed in this report; this is investigation and scoping only.