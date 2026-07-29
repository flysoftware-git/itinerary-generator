# PR-010: Subject links can resolve to area-reference queries instead of entity-specific targets

Labels: `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-discovery`, `area:restaurant-linking`, `area:content-linking`, `area:html-output`

**Fixed in:** v1.4.1 — area-reference query patterns (`google.com/maps/search/`) classified as `google_maps_search` and blocked in enforce mode via `url_policy_blocked_classes` in `config.yaml`.

Manifest: `C:/Dev/Sandbox/sw_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

Some selected links target area-reference query pages rather than the named subject itself. Reported example: `Chuckleberry's` links to `https://www.google.com/maps/search/Chuckleberry%27s+near+Capitol+Reef+National+Park`, which is a location-area query and not a deterministic subject-level destination.

## Expected Behavior

- Link targets should refer to the named subject entity (for example the specific restaurant), not an area-level “near <destination>” query.
- Query/list pages that represent geographic proximity rather than the exact subject should be rejected.
- If specific subject information is unavailable, fail closed (no misleading link).

## Actual Behavior

- The output includes area-reference map queries such as:
  - `Chuckleberry's` → `.../maps/search/Chuckleberry%27s+near+Capitol+Reef+National+Park`
  - `Capitol Reef Cafe` → `.../maps/search/restaurants+near+Capitol+Reef+National+Park`
  - `Broken Spur Steakhouse` → `.../maps/search/Broken+Spur+Steakhouse+near+Capitol+Reef+National+Park`

## Evidence

- In [output/index.html](output/index.html), the `Dinner Recommendations` section for Capitol Reef includes links with `near+Capitol+Reef+National+Park` search patterns.
- In [generator/url_discovery.py](generator/url_discovery.py), restaurant fallback construction and normalization still allow query-based maps/search outcomes to publish as final links.
- In [generator/html_assembler.py](generator/html_assembler.py), rendered restaurant links can use `rest.url` / `rest.maps_url`, propagating area-query links to final output.

## Reproduction Context

- Manifest used: `C:/Dev/Sandbox/sw_manifest.yaml`
- Destination/section: `Capitol Reef National Park` → `Dinner Recommendations`
- Visible link text example: `Chuckleberry's`
- Rendered URL: `https://www.google.com/maps/search/Chuckleberry%27s+near+Capitol+Reef+National+Park`

## Suspected Area

- Primary component: subject-entity precision in restaurant URL discovery and fallback policy
- Possible files:
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Fallback logic currently tolerates proximity-based query URLs when precise subject pages are unavailable.
- Specificity checks are not strict enough to reject “near <area>” query patterns for entity-labeled links.

## Scope of Likely Fix

- Add hard rejection for area-reference query patterns in final restaurant link publishing, such as:
  - `near+<destination>`
  - `restaurants+near+<destination>`
- Require stronger entity match between displayed subject name and linked destination identity.
- If no specific link is found, suppress link or show unavailable state rather than publishing area-query URLs.

## Non-Breaking Validation Plan

- Unit tests:
  - reject links containing area-proximity query patterns when item is a named subject.
  - preserve valid subject-specific links.
- Integration checks:
  - regenerate Capitol Reef section and verify named restaurants do not link to area-reference queries.
  - ensure link coverage remains acceptable where precise sources exist.
- Guardrails:
  - avoid false rejection for legitimate canonical pages that include unavoidable location qualifiers but still represent one entity.

## Notes

- Related to PR-007 but distinct in scope.
- PR-007 addresses non-single-location maps-search links generally.
- PR-010 focuses on semantic mismatch where link selection references area proximity instead of the named subject.
- No implementation performed in this report; this is investigation and scoping only.
