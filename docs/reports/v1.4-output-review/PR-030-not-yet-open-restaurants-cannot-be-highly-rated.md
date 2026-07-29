# PR-030: Not-yet-open restaurants should be excluded from highly-rated recommendations (La Casa Sena)

Labels: `review:v1.4-output`, `type:bug`, `status:open`, `severity:high`, `area:restaurant-linking`, `area:content-freshness`, `area:rating-policy`, `area:url-validation`, `area:html-output`

Manifest: `trip_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

La Casa Sena appears in dinner recommendations, but user-reported status indicates the venue has not yet opened. A venue that is not yet open cannot have a valid public rating/review history and therefore should not be included in highly rated recommendation cohorts.

## Expected Behavior

- Restaurants not yet open must be excluded from highly rated recommendation logic.
- Recommendation pipeline should enforce operating-status eligibility before rating-based ranking.
- If operating status is pre-opening, suppress from final recommendations or mark as future option outside core dining picks.

## Actual Behavior

- La Casa Sena appears in rendered dinner recommendations with URL:
  - https://www.lacasasena.com
- User-reported condition: venue not yet open, incompatible with highly rated recommendation criteria.

## Evidence

- In [output/index.html](output/index.html), line 1734 contains:
  - visible link text: La Casa Sena
  - rendered URL: https://www.lacasasena.com
- Supporting nearby content line:
  - line 1741 includes recommendation description text for the same venue.

## Reproduction Context

- Manifest used: trip_manifest.yaml
- Destination context: Santa Fe dinner recommendations
- Subject: La Casa Sena

## Suspected Area

- Primary components: restaurant status validation and rating-policy eligibility checks
- Possible files:
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/url_validator.py](generator/url_validator.py)
  - [generator/ai_content.py](generator/ai_content.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Restaurant ranking/selection does not currently gate by opening-status maturity before applying quality/rating heuristics.
- Pipeline can include venues lacking sufficient review provenance due to pre-opening status.

## Scope of Likely Fix

- Add explicit not-yet-open status rejection for rating-qualified recommendations.
- Require minimum review/rating provenance only for currently open venues.
- Provide fallback replacement candidates when status-ineligible venues are removed.

## Non-Breaking Validation Plan

- Unit tests:
  - pre-opening restaurants are excluded from highly rated recommendation lists.
  - open restaurants with valid review provenance remain eligible.
- Integration checks:
  - regenerate output and verify La Casa Sena is removed or reclassified as a non-rated future option.

## Notes

- This is intake-only; no implementation change is included.
