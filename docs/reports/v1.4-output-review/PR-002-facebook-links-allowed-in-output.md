# PR-002: Facebook destination links are allowed in final output

Labels: `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-discovery`, `area:html-output`, `area:content-linking`

**Fixed in:** v1.4.1 — `social_media` URL class added to `url_policy_blocked_classes` in `config.yaml`; enforced via `_classify_url_policy_class` + `_retain_discovered_url` in `generator/url_discovery.py`.

Manifest: `C:/Dev/Sandbox/sw_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual link-by-link review

## Summary

The generated output currently allows attraction links that resolve to Facebook pages. This violates the desired policy that no final itinerary link should require or direct users into Facebook content.

## Expected Behavior

- No rendered itinerary links should point to `facebook.com` or other social-media content pages.
- Social links should be rejected during discovery or stripped during the final audit pass.
- If no acceptable destination-specific page exists, the system should fall back to a non-social alternative or no link.

## Actual Behavior

- `Tonaquint Nature Center` renders as a direct Facebook link in the final HTML.

## Evidence

- Rendered output in [output/index.html](output/index.html) contains:
  - `Tonaquint Nature Center` -> `https://www.facebook.com/TonaquintNatureCenter/`
- Example rendered line observed during review:
  - attraction item with `class="attr-link"` pointing to Facebook.

## Reproduction Context

- Manifest used: `C:/Dev/Sandbox/sw_manifest.yaml`
- Destination/section: `St. George, Utah` → attractions
- Visible link text: `Tonaquint Nature Center`
- Rendered URL: `https://www.facebook.com/TonaquintNatureCenter/`

## Suspected Area

- Primary component: URL discovery policy / audit filtering
- Possible files:
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Current URL discovery scoring applies soft domain penalties through `NEGATIVE_DOMAIN_HINTS`, but does not explicitly ban social-media domains.
- [generator/url_discovery.py](generator/url_discovery.py) appears to allow Facebook links through `_is_relevant_result(...)` when the URL verifies and matches item tokens.
- The audit pass also lacks an explicit social-domain rejection rule, so a Facebook URL can survive through to HTML assembly.

## Scope of Likely Fix

- Add explicit social-domain rejection at discovery and/or audit stage.
- Ensure this rule applies consistently across attractions, restaurants, events, scenic drives, and en-route stops where appropriate.
- Preserve non-social fallbacks so usability does not regress when no official site exists.

## Non-Breaking Validation Plan

- Cheapest feasibility run:
  - local destination-only run for `St. George, Utah` using `C:/Dev/Sandbox/sw_manifest.yaml`
- Additional validation:
  - targeted tests for explicit rejection of `facebook.com` URLs
  - regression check that non-social alternatives still survive selection when present
- Guardrail:
  - do not accidentally block legitimate civic/tourism pages just because discovery broadens away from social URLs

## Notes

- This is a policy/compliance issue as well as a user-experience issue.
- Severity is high because it is a clear, explicit requirement: no Facebook content links in final output.
- No implementation performed in this report; this is investigation and scoping only.