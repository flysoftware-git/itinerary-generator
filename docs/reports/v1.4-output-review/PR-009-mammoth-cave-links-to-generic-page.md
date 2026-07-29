# PR-009: Mammoth Cave links to generic Bryce Canyon page instead of entity-specific information

Labels: `review:v1.4-output`, `type:bug`, `status:open`, `severity:high`, `area:url-discovery`, `area:content-linking`, `area:html-output`

Manifest: `C:/Dev/Sandbox/sw_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

The itinerary renders `Mammoth Cave` as a named en-route stop, but the linked URL points to the generic `Bryce Canyon National Park` Wikipedia page rather than Mammoth Cave-specific information. This creates a misleading association and should be rejected when entity-specific coverage cannot be validated.

## Expected Behavior

- A named stop like `Mammoth Cave` should link to entity-specific information about that stop.
- Generic destination landing pages should not be accepted as final links for specific entities.
- If no specific information can be validated, the link should be rejected (fail-closed) rather than mapped to a broad generic page.

## Actual Behavior

- `Mammoth Cave` appears as a specific stop name in the Bryce route context.
- The linked URL is `https://en.wikipedia.org/wiki/Bryce_Canyon_National_Park`, which is a generic destination page and not Mammoth Cave-specific.

## Evidence

- In [output/index.html](output/index.html), the `Getting Here` route includes waypoint text containing `Mammoth Cave`.
- In [output/index.html](output/index.html), the en-route stop named `Mammoth Cave` is linked to `https://en.wikipedia.org/wiki/Bryce_Canyon_National_Park`.
- URL discovery uses specific-result filtering and fallback selection in [generator/url_discovery.py](generator/url_discovery.py), but this case shows a specific-entity mismatch still passing through.

## Reproduction Context

- Manifest used: `C:/Dev/Sandbox/sw_manifest.yaml`
- Destination/section: `Bryce Canyon National Park` → `Getting Here` / en-route stops
- Visible link text: `Mammoth Cave`
- Rendered URL: `https://en.wikipedia.org/wiki/Bryce_Canyon_National_Park`

## Suspected Area

- Primary component: entity specificity checks in URL discovery for named stops
- Possible files:
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Current relevance checks can accept destination-level generic pages when no precise stop-level page is found.
- For named en-route entities, there is no strict fail-closed rule requiring lexical/entity match confidence between displayed name and target page.

## Scope of Likely Fix

- Enforce stricter entity-level validation for named en-route stops:
  - reject generic destination landing pages for specific stop names.
  - require stronger token/entity match for final URL acceptance.
- If no specific page is found, suppress link for the stop (or mark as unavailable) instead of linking to a generic page.
- Add explicit rejection reason logging for generic-page mismatch.

## Non-Breaking Validation Plan

- Unit tests:
  - reject generic destination pages when stop name is specific and non-matching.
  - preserve acceptance for exact or high-confidence entity matches.
- Integration checks:
  - regenerate Bryce section and verify `Mammoth Cave` no longer links to generic Bryce page.
  - confirm unaffected stop links still resolve correctly.
- Guardrails:
  - avoid over-rejection of legitimate pages with alternate but equivalent naming.

## Notes

- This issue is aligned with the broader entity precision concerns in PR-008 but is tracked separately due to a concrete, reproducible mislink.
- No implementation performed in this report; this is investigation and scoping only.
