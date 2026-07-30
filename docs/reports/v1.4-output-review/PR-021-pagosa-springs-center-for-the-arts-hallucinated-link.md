# PR-021: Pagosa Springs Center for the Arts uses hallucinated/untrusted listing link and should be rejected

Labels: `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-validation`, `area:url-discovery`, `area:hallucination-risk`, `area:content-linking`, `area:html-output`

Manifest: `trip_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

`Pagosa Springs Center for the Arts` is published with a user-reported hallucinated/untrusted target URL:

- `https://www.visitpagosasprings.com/listing/pagosa-springs-center-for-the-arts/204/`

If a source cannot be verified as trustworthy and subject-correct, it should be rejected from final output.

## Expected Behavior

- Named attraction links must resolve to verified, trustworthy, subject-specific sources.
- If a link is suspected hallucinated/fabricated/unreliable, reject it.
- Fail closed by rendering plain text rather than publishing uncertain links.

## Actual Behavior

- `Pagosa Springs Center for the Arts` is emitted as a linked attraction with:
  - `https://www.visitpagosasprings.com/listing/pagosa-springs-center-for-the-arts/204/`
- User-reported outcome: hallucination/untrusted target that should be rejected.

## Evidence

- In [output/index.html](output/index.html), line 1457 renders:
  - visible link text: `Pagosa Springs Center for the Arts`
  - URL: `https://www.visitpagosasprings.com/listing/pagosa-springs-center-for-the-arts/204/`
- Related duplicate claim context in local tips:
  - line 1412 includes a local tip about the same venue with a prose-derived Google search `More info` link
  - line 1498 repeats that same local tip and `More info` link

## Reproduction Context

- Manifest used: `trip_manifest.yaml`
- Destination context: `Pagosa Springs`
- Subject: `Pagosa Springs Center for the Arts`

## Suspected Area

- Primary components: source trust gating and entity-link validation
- Possible files:
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/url_validator.py](generator/url_validator.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Link selection permits weakly validated listing targets from broad discovery without strong trust-scoring and entity confirmation.
- When no canonical high-confidence source is validated, fallback still publishes uncertain links.

## Scope of Likely Fix

- Apply trust-tier gating for attraction links.
- Require subject-entity and source-quality verification before publish.
- Reject uncertain/hallucination-flagged targets and fail closed to plain text.

## Non-Breaking Validation Plan

- Unit tests:
  - hallucination-flagged/untrusted listing URLs are rejected.
  - verified canonical links for the same attraction continue to render.
- Integration checks:
  - regenerate output and verify `Pagosa Springs Center for the Arts` no longer publishes untrusted listing links.

## Notes

- This is intake-only; no implementation change is included.
