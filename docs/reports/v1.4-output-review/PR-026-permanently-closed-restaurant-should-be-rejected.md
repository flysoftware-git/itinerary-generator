# PR-026: Permanently closed restaurant surfaced as recommendation (Nello's Bistro) and should be rejected

Labels: `review:v1.4-output`, `type:bug`, `status:open`, `severity:high`, `area:url-validation`, `area:restaurant-linking`, `area:content-freshness`, `area:google-maps`, `area:html-output`

Manifest: `trip_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

`Nello's Bistro` appears in dinner recommendations despite being user-reported as permanently closed. Closed venues should be filtered out before publishing itinerary output.

## Expected Behavior

- Restaurants marked permanently closed must be rejected from final recommendations.
- Discovery should include freshness/status checks (open/closed) before publish.
- If no validated open destination is available, fail closed by removing the link and/or replacing the venue.

## Actual Behavior

- `Nello's Bistro` is rendered with:
  - `https://www.google.com/maps/search/Nello's+Bistro+Pagosa+Springs+restaurant`
- User-reported outcome: venue is permanently closed and should not be recommended.

## Evidence

- In [output/index.html](output/index.html), line 1549 contains:
  - visible link text: `Nello's Bistro`
  - rendered URL: `https://www.google.com/maps/search/Nello's+Bistro+Pagosa+Springs+restaurant`

## Reproduction Context

- Manifest used: `trip_manifest.yaml`
- Destination context: `Pagosa Springs` dinner recommendations
- Subject: `Nello's Bistro`

## Suspected Area

- Primary components: restaurant freshness/status validation and recommendation filtering
- Possible files:
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/url_validator.py](generator/url_validator.py)
  - [generator/ai_content.py](generator/ai_content.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Current restaurant selection and URL acceptance path does not enforce closure-status gating.
- Search/query links can pass without confirming venue operating status.

## Scope of Likely Fix

- Add explicit closed-business rejection checks in restaurant pipeline.
- Prefer deterministic, entity-specific links with status metadata over generic search queries.
- Replace or suppress entries flagged as permanently closed.

## Non-Breaking Validation Plan

- Unit tests:
  - permanently closed restaurants are excluded from final output.
  - open, validated restaurants remain eligible.
- Integration checks:
  - regenerate output and verify `Nello's Bistro` is absent or replaced with an open alternative.

## Notes

- This is intake-only; no implementation change is included.
