# PR-007: Google Maps search-result links that do not resolve to a single location are allowed

Labels: `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-discovery`, `area:restaurant-linking`, `area:google-maps`, `area:html-output`

**Fixed in:** v1.4.1 — `google_maps_search` URL class added to `url_policy_blocked_classes`; enforce mode in `config.yaml` causes `_retain_discovered_url` to drop all `/maps/search/` links that are not in the baseline allowlist.

Manifest: `C:/Dev/Sandbox/sw_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

Some restaurant links point to Google Maps search-result pages (query lists) instead of a single, resolved place. These links are ambiguous and can present multiple businesses, reducing trust and usability of the itinerary.

## Expected Behavior

- Restaurant links should resolve to a single canonical destination (for example one place page, one official listing, or one authoritative profile).
- Google Maps search-result URLs (for example `/maps/search/...`) should not be used as final links when they do not resolve to one specific location.
- If no single-location URL can be validated, the renderer should avoid presenting a misleading pseudo-specific link.

## Actual Behavior

- The current pipeline can emit Google Maps search URLs as fallback links for restaurants.
- These are query endpoints and may open multi-result listings rather than a single destination.

## Evidence

- Reported example link (multi-result search pattern):
  - `https://www.google.com/maps/search/Hawaiian+Barbecue+restaurant+St.+George,+Utah`
- In [generator/url_discovery.py](generator/url_discovery.py), `_discover_restaurants` sets a maps-search fallback URL and uses it when no better URL is found.
- In [generator/url_discovery.py](generator/url_discovery.py), `_normalize_restaurant_url` rejects some Maps paths (`/maps/place`, `/maps/@`, directions), but does not reject `/maps/search` for final publish.
- In [generator/html_assembler.py](generator/html_assembler.py), `_build_restaurants` will render `rest['url']` or `rest['maps_url']`, both of which can be maps-search links.

## Reproduction Context

- Manifest used: `C:/Dev/Sandbox/sw_manifest.yaml`
- Section reviewed: `Dinner Recommendations`
- Visible link text example: `Hawaiian Barbecue`
- Rendered URL pattern: `https://www.google.com/maps/search/...`

## Suspected Area

- Primary component: restaurant URL normalization and fallback policy
- Possible files:
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Restaurant fallback policy currently treats query-style Google Maps search links as acceptable publish targets.
- Normalization logic prevents some bad Maps URL shapes but does not enforce single-location resolvability as a hard requirement.

## Scope of Likely Fix

- Tighten restaurant URL acceptance rules:
  - disallow publish-time `/maps/search` URLs as final restaurant links unless they can be deterministically resolved to one place.
- Prefer validated single-location alternatives:
  - official restaurant site
  - authoritative profile/listing with unambiguous venue identity
  - stable place-specific maps URL if reliably resolvable.
- Add explicit rejection reason logging for non-single-location map queries.

## Non-Breaking Validation Plan

- Unit tests:
  - reject `/maps/search` restaurant links as final output when ambiguous.
  - preserve acceptance of valid single-location non-direction links.
- Integration checks:
  - run destination-local generation for St. George.
  - verify restaurant links in HTML are no longer query-list pages.
- Guardrails:
  - do not regress valid restaurant coverage; if strictness removes candidates, ensure fallback behavior remains transparent and non-misleading.

## Notes

- This issue is distinct from PR-002 (Facebook links) and focuses specifically on maps search-result ambiguity.
- No implementation performed in this report; this is investigation and scoping only.
