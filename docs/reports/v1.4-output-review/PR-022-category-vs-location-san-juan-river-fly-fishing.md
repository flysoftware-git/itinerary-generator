# PR-022: Discovery treats category activity as place entity for San Juan River Fly Fishing, yielding ambiguous map-search link

Labels: `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-discovery`, `area:entity-classification`, `area:content-linking`, `area:google-maps`, `area:html-output`

**Fixed in:** v1.4.1 — ambiguous map-search link produced for category activity classified as `google_maps_search` and now blocked in enforce mode; category activities without a validated single-entity URL fall back to no link.

Manifest: `trip_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

`San Juan River Fly Fishing` is a category/activity concept, not a single deterministic location entity. The current discovery path links it to a broad Google Maps search URL, which does not resolve to one promised destination.

## Expected Behavior

- Discovery should classify items as `category/activity` vs `single location/entity` before URL selection.
- Category activities should not be forced into single-place location links.
- For category items, either:
  - use curated category resources (e.g., official guides/permit pages), or
  - render without a link if no validated category target exists.
- Ambiguous Google Maps search URLs should be rejected as final links for category concepts.

## Actual Behavior

- `San Juan River Fly Fishing` is rendered with:
  - `https://www.google.com/maps/search/?api=1&query=San%20Juan%20River%20Fly%20Fishing%20Pagosa%20Springs`
- This is a search-result query page, not a deterministic single-location destination.

## Evidence

- In [output/index.html](output/index.html), line 1458 contains:
  - visible link text: `San Juan River Fly Fishing`
  - rendered URL: `https://www.google.com/maps/search/?api=1&query=San%20Juan%20River%20Fly%20Fishing%20Pagosa%20Springs`

## Reproduction Context

- Manifest used: `trip_manifest.yaml`
- Destination context: `Pagosa Springs` attractions
- Subject: `San Juan River Fly Fishing`

## Suspected Area

- Primary components: entity-type classification and URL finalization policy
- Possible files:
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/url_validator.py](generator/url_validator.py)
  - [generator/ai_content.py](generator/ai_content.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Discovery treats activity categories as location entities and applies place-link fallback logic.
- When no single entity is found, maps-search URLs are accepted instead of enforcing category-aware handling.

## Scope of Likely Fix

- Add pre-link classification for `entity` vs `category/activity`.
- Route category items through category-specific link policy.
- Reject maps-search query URLs for category concepts unless explicitly allowed by policy.
- Fail closed (no link) when no validated category resource exists.

## Non-Breaking Validation Plan

- Unit tests:
  - category activity labels (e.g., fly fishing, stargazing, wine tasting) are not forced to single-place links.
  - maps-search URLs for category activities are rejected unless policy permits.
  - single-entity attractions continue to receive deterministic links.
- Integration checks:
  - regenerate output and verify `San Juan River Fly Fishing` is either linked to a validated category resource or rendered without hyperlink.

## Notes

- This is intake-only; no implementation change is included.
