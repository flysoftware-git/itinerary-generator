# PR-011: Capitol Reef Cafe links to area-reference search instead of subject-specific destination

Labels: `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-discovery`, `area:restaurant-linking`, `area:content-linking`, `area:html-output`

**Fixed in:** v1.4.1 — `google_maps_search` class blocked in enforce mode; the specific area-reference query pattern (`maps/search/restaurants+near+...`) is now rejected by `_retain_discovered_url` instead of being published.

Manifest: `C:/Dev/Sandbox/sw_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

`Capitol Reef Cafe` is rendered as a named restaurant, but its link resolves to a generic area-query page (`restaurants near Capitol Reef National Park`) rather than subject-specific information for Capitol Reef Cafe.

## Expected Behavior

- A named restaurant should link to a subject-specific destination for that restaurant.
- Area-reference query links (for example `restaurants near ...`) should be rejected for named entities.
- If no subject-specific destination is available, fail closed instead of publishing an area query.

## Actual Behavior

- `Capitol Reef Cafe` currently links to:
  - `https://www.google.com/maps/search/restaurants+near+Capitol+Reef+National+Park`
- This is a broad area query, not a deterministic destination for the named subject.

## Evidence

- In [output/index.html](output/index.html), Capitol Reef section shows:
  - visible link text: `Capitol Reef Cafe`
  - rendered URL: `https://www.google.com/maps/search/restaurants+near+Capitol+Reef+National+Park`
- This matches the same area-reference pattern tracked broadly in [docs/reports/v1.4-output-review/PR-010-subject-links-resolve-to-area-reference-queries.md](docs/reports/v1.4-output-review/PR-010-subject-links-resolve-to-area-reference-queries.md).

## Reproduction Context

- Manifest used: `C:/Dev/Sandbox/sw_manifest.yaml`
- Destination/section: `Capitol Reef National Park` → `Dinner Recommendations`
- Visible link text: `Capitol Reef Cafe`
- Rendered URL: `https://www.google.com/maps/search/restaurants+near+Capitol+Reef+National+Park`

## Suspected Area

- Primary component: restaurant subject-entity precision in URL discovery fallback
- Possible files:
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Current fallback policy allows generic area queries to pass as final restaurant links when a specific entity target is missing.

## Scope of Likely Fix

- Reject generic area-query URLs for named restaurant entities.
- Require entity-specific validation for final publish.
- Fall back to no link (or explicit unavailable) when no valid subject-specific target is found.

## Non-Breaking Validation Plan

- Unit tests:
  - reject `restaurants+near+<destination>` for named restaurants.
  - preserve valid subject-specific restaurant links.
- Integration checks:
  - regenerate Capitol Reef output and verify Capitol Reef Cafe no longer links to area-query search.

## Notes

- This is an instance-focused report under the broader pattern tracked in PR-010.
- No implementation performed in this report; this is investigation and scoping only.
