# PR-025: Pagosa Brewing & Grill uses hallucinated/untrusted restaurant link and should be rejected

Labels: `review:v1.4-output`, `type:bug`, `status:open`, `severity:high`, `area:url-validation`, `area:url-discovery`, `area:hallucination-risk`, `area:restaurant-linking`, `area:html-output`

Manifest: `trip_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

`Pagosa Brewing & Grill` is published with a user-reported hallucinated/untrusted URL:

- `https://www.pagosabrewing.com`

If a restaurant source target is unverified or suspected hallucinated, it should be rejected from final output.

## Expected Behavior

- Restaurant links must resolve to verified, trustworthy, subject-specific targets.
- If a link is suspected hallucinated/fabricated/unreliable, reject it.
- Fail closed by rendering plain text rather than publishing uncertain links.

## Actual Behavior

- `Pagosa Brewing & Grill` is currently emitted as a linked restaurant with:
  - `https://www.pagosabrewing.com`
- User-reported outcome: hallucination/untrusted target that should be rejected.

## Evidence

- In [output/index.html](output/index.html), line 1505 renders:
  - visible link text: `Pagosa Brewing & Grill`
  - URL: `https://www.pagosabrewing.com`

## Reproduction Context

- Manifest used: `trip_manifest.yaml`
- Destination context: `Pagosa Springs` dinner recommendations
- Subject: `Pagosa Brewing & Grill`

## Suspected Area

- Primary components: restaurant source trust gating and final-link validation
- Possible files:
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/url_validator.py](generator/url_validator.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Restaurant URL acceptance permits low-confidence/unverified targets when stronger entity-verified links are unavailable.
- Trust-tier and entity-confirmation checks are insufficiently strict before publish.

## Scope of Likely Fix

- Enforce stricter trust and entity matching for restaurant links.
- Reject hallucination-flagged/untrusted restaurant URLs.
- Fall back to plain text when no validated destination is available.

## Non-Breaking Validation Plan

- Unit tests:
  - hallucination-flagged/untrusted restaurant URLs are rejected.
  - validated restaurant links continue to render.
- Integration checks:
  - regenerate output and verify `Pagosa Brewing & Grill` no longer publishes untrusted links.

## Notes

- This is intake-only; no implementation change is included.
