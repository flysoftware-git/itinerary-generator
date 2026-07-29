# PR-012: Fruita Campground waypoint publishes non-validated Google Maps directions link instead of dropping waypoint

Labels: `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-discovery`, `area:route-waypoints`, `area:google-maps`, `area:html-output`

**Fixed in:** v1.4.1 — `google_maps_dir` URL class (matching `/maps/dir/` patterns) blocked in enforce mode via `url_policy_blocked_classes`; waypoint links of this type are now dropped by `_retain_discovered_url`, falling back to no link.

Manifest: `trip_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

`Fruita Campground` appears as a waypoint with a Google Maps directions URL (`/maps/dir/...`) that is not a validated destination page for the subject entity. For waypoint cards, if no valid subject-specific link is available, the waypoint should be rendered without a link (drop waypoint URL).

## Expected Behavior

- Waypoint links must pass destination-level validation for the named subject.
- Route/directions URL patterns (`google.com/maps/dir/...`) should not be published as canonical information links for waypoint entities.
- If no valid link is available for a waypoint, render the waypoint text without hyperlinking (drop waypoint link).

## Actual Behavior

- `Fruita Campground` is rendered as a hyperlink to:
  - `https://www.google.com/maps/dir/Moab,+UT/Fruita+Campground,+Capitol+Reef`
- This is a directions route URL, not a stable subject information destination.

## Evidence

- In [output/index.html](output/index.html), line 1097 contains:
  - visible link text: `Fruita Campground`
  - rendered URL: `https://www.google.com/maps/dir/Moab,+UT/Fruita+Campground,+Capitol+Reef`

## Reproduction Context

- Manifest used: `trip_manifest.yaml`
- Output artifact: `output/index.html`
- Section type: waypoint/stop card
- Subject: `Fruita Campground`

## Suspected Area

- Primary component: waypoint link validation and fallback policy
- Possible files:
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Current fallback accepts Google Maps route/directions links as publishable waypoint links when subject-specific validated URLs are unavailable.

## Scope of Likely Fix

- Disallow `google.com/maps/dir/` for entity info links in waypoint cards.
- Enforce subject-level validation for waypoint links.
- If validation fails, keep waypoint content but drop hyperlink.

## Non-Breaking Validation Plan

- Unit tests:
  - reject `/maps/dir/` links for waypoint entities.
  - preserve valid subject-specific links when available.
- Integration checks:
  - regenerate output and verify `Fruita Campground` renders without link when no valid target exists.

## Notes

- This report is intake-only; no implementation change is included here.
