# PR-014: Red Cliffs Lodge Restaurant uses ambiguous Google Maps search link that may not resolve to the intended entity

Labels: `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-discovery`, `area:restaurant-linking`, `area:google-maps`, `area:content-linking`, `area:html-output`

**Fixed in:** v1.4.1 — `google_maps_search` blocked in enforce mode; the ambiguous Maps search URL for Red Cliffs Lodge Restaurant is now dropped by policy gate, resulting in no link rather than a misleading search page.

Manifest: `trip_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

`Red Cliffs Lodge Restaurant` is rendered with a Google Maps search query URL instead of a validated subject-specific destination. Search-result URLs can resolve to multiple or unintended places and are not guaranteed to match the exact intended entity ("not the same").

## Expected Behavior

- A named restaurant should link to a deterministic, subject-specific destination.
- Ambiguous Google Maps search-result links should not be used as final canonical links for named entities.
- If no valid entity-specific URL is available, fail closed (no link) rather than publish an ambiguous search URL.

## Actual Behavior

- `Red Cliffs Lodge Restaurant` currently links to:
  - `https://www.google.com/maps/search/?api=1&query=Red%20Cliffs%20Lodge%20Restaurant%20Moab`
- This is a query/search URL, not a validated single-entity destination.

## Evidence

- In [output/index.html](output/index.html), line 1211 contains:
  - visible link text: `Red Cliffs Lodge Restaurant`
  - rendered URL: `https://www.google.com/maps/search/?api=1&query=Red%20Cliffs%20Lodge%20Restaurant%20Moab`

## Reproduction Context

- Manifest used: `trip_manifest.yaml`
- Destination context: `Moab, UT` dining recommendation
- Subject: `Red Cliffs Lodge Restaurant`

## Suspected Area

- Primary component: restaurant link validation and final-link acceptance policy
- Possible files:
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Fallback behavior accepts Google Maps query URLs for named restaurants without strict entity-level validation against the intended subject.

## Scope of Likely Fix

- Disallow ambiguous Google Maps search URLs as final links for named restaurant entities.
- Require entity-specific validation before publish.
- If no validated entity target is found, render without hyperlink.

## Non-Breaking Validation Plan

- Unit tests:
  - reject search-result/query links for named restaurant entities.
  - preserve valid deterministic links when available.
- Integration checks:
  - regenerate output and verify `Red Cliffs Lodge Restaurant` no longer points to ambiguous search URL.

## Notes

- This is an instance-specific intake report aligned with the broader search-link ambiguity class already tracked in previous reports.
- No implementation change is included in this report.
