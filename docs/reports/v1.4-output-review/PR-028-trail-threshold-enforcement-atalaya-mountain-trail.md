# PR-028: Trail candidates exceeding configured distance/elevation/intensity thresholds should be rejected (Atalaya Mountain Trail)

Labels: `review:v1.4-output`, `type:bug`, `status:open`, `severity:high`, `area:url-discovery`, `area:trail-filtering`, `area:policy-enforcement`, `area:hiking`, `area:html-output`

Manifest: `trip_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

`Atalaya Mountain Trail` is included in output despite reported requirement that trails exceeding configured distance, altitude/elevation, or intensity thresholds should be rejected.

## Expected Behavior

- Trail candidates must be validated against configured policy thresholds before publish.
- If any threshold is exceeded (distance, elevation gain, or intensity), reject the trail from final recommendations.
- Prefer compliant alternatives over publishing non-compliant trail entries.

## Actual Behavior

- `Atalaya Mountain Trail` is rendered as an attraction with:
  - `https://www.alltrails.com/trail/us/new-mexico/atalaya-mountain-trail`
- Card text states:
  - `6.6 miles round-trip`
  - `steady elevation gain`
- This indicates possible threshold violation under stricter trail-policy constraints.

## Evidence

- In [output/index.html](output/index.html), line 1623 contains:
  - visible link text: `Atalaya Mountain Trail`
  - rendered URL: `https://www.alltrails.com/trail/us/new-mexico/atalaya-mountain-trail`
  - description excerpt: `The trail is 6.6 miles round-trip with a steady elevation gain.`

## Reproduction Context

- Manifest used: `trip_manifest.yaml`
- Destination context: `Santa Fe` attractions
- Subject: `Atalaya Mountain Trail`

## Suspected Area

- Primary components: trail metadata filtering and threshold policy enforcement
- Possible files:
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/url_validator.py](generator/url_validator.py)
  - [generator/ai_content.py](generator/ai_content.py)

## Root Cause Hypothesis

- Threshold enforcement is incomplete or not consistently applied across all trail sources.
- Distance/elevation/intensity gating may rely on missing metadata or permissive fallback behavior.

## Scope of Likely Fix

- Enforce hard rejection checks for max distance, max elevation gain, and allowed intensity.
- Require required metadata presence for threshold evaluation; fail closed when unavailable.
- Ensure all trail ingestion paths use the same threshold gate before publish.

## Non-Breaking Validation Plan

- Unit tests:
  - trails exceeding any configured threshold are excluded.
  - trails within thresholds remain eligible.
  - missing metadata triggers safe rejection or explicit downgrade policy.
- Integration checks:
  - regenerate output and verify non-compliant trail entries are absent or replaced.

## Notes

- This is intake-only; no implementation change is included.
