# PR-020: Lizard Head Pass uses untrusted/hallucinated target link and should be rejected if unverified

Labels: `review:v1.4-output`, `type:bug`, `status:open`, `severity:high`, `area:url-validation`, `area:url-discovery`, `area:hallucination-risk`, `area:content-linking`, `area:html-output`

Manifest: `trip_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

`Lizard Head Pass` is published with a target URL reported as hallucinated/untrusted:

- `https://visitpagosasprings.com/lizard-head-pass-area`

If a source target cannot be verified as trustworthy and subject-correct, it should be rejected from final output.

## Expected Behavior

- Named stop links must resolve to verified, trustworthy, subject-specific sources.
- If a link is suspected hallucinated (fabricated, unreliable, or not clearly authoritative), reject it.
- Fail closed by rendering plain text rather than publishing uncertain links.

## Actual Behavior

- `Lizard Head Pass` is currently emitted with:
  - `https://visitpagosasprings.com/lizard-head-pass-area`
- User-reported outcome: hallucination/untrusted target.

## Evidence

- In [output/index.html](output/index.html):
  - line 1446 renders `Lizard Head Pass` with `https://visitpagosasprings.com/lizard-head-pass-area`
- Additional context in same destination:
  - line 1270: `Lizard Head Pass` linked to Wikipedia
  - line 1285: `Lizard Head Pass` also appears as a separate scenic card with placeholder link (`href="#"`)

## Reproduction Context

- Manifest used: `trip_manifest.yaml`
- Destination context: Telluride/Pagosa segment stop cards and attractions
- Subject: `Lizard Head Pass`

## Suspected Area

- Primary components: source trust policy, entity-link reconciliation, and final-link validation
- Possible files:
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/url_validator.py](generator/url_validator.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Link selection permits weakly validated sources from broad discovery without strong trust-scoring and entity confirmation.
- Multiple representations of the same entity across sections are merged without one canonical validated target.

## Scope of Likely Fix

- Apply trust-tier gating for stop/attraction links.
- Require subject-entity and source-quality verification prior to publish.
- Reject uncertain targets and render plain text when verification fails.

## Non-Breaking Validation Plan

- Unit tests:
  - unverified/hallucination-flagged URLs are rejected.
  - verified canonical links for the same entity are retained.
- Integration checks:
  - regenerate output and verify `Lizard Head Pass` no longer publishes untrusted target URLs.

## Notes

- This is intake-only; no implementation change is included.

## Comments

- Link remains invalid