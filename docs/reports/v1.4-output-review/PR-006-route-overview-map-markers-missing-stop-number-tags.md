# PR-006: Route overview map markers do not show stop numbers matching destination menu

Labels: `review:v1.4-output`, `type:bug`, `status:open`, `severity:medium`, `area:html-output`, `area:map-ui`, `area:usability`

Manifest: `C:/Dev/Sandbox/sw_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

The route overview map marker tags are hard to scan when comparing map pins with destination tabs. The destination tab row uses indexed labels (for example `1 · Zion`, `2 · Bryce`), but map markers do not include matching stop numbers. This makes cross-referencing pins to the tab sequence slower than needed.

## Expected Behavior

- Each destination map marker should include the stop number matching the destination menu order.
- Marker icon should be visually compact to reduce overlap and clutter.
- Marker tag should preserve the current readable structure:
  - date context above
  - location name below in smaller font
  - dark/black background label for contrast
- Number styling should be distinct enough that users can quickly correlate map stops with the numbered tabs.

## Actual Behavior

- Overview map markers currently render month/day and location name, but no tab-index/stop number.
- Destination tabs are numbered independently, which creates a visual mapping gap between the tab list and map pins.
- Dense routes can be harder to interpret at a glance because markers are not index-anchored.

## Evidence

- Marker rendering in [templates/v2.5_template.html](templates/v2.5_template.html) creates custom `L.divIcon` HTML using `s.mo`, `s.dy`, and `s.name` only.
- Marker popup in [templates/v2.5_template.html](templates/v2.5_template.html) also displays only name and date, with no stop number.
- Marker JSON builder in [generator/html_assembler.py](generator/html_assembler.py) currently emits entries with `c`, `mo`, `dy`, and `name`; there is no `stop_index` field.
- Destination tab labels in [generator/html_assembler.py](generator/html_assembler.py) include `i + 1` numbering (`"{i + 1} · ..."`).

## Reproduction Context

- Manifest used: `C:/Dev/Sandbox/sw_manifest.yaml`
- Section reviewed: route overview map and destination tab row
- User-requested readability goal: map pins should align immediately with numbered destination menu.

## Suspected Area

- Primary component: map marker payload + Leaflet marker HTML
- Possible files:
  - [generator/html_assembler.py](generator/html_assembler.py)
  - [templates/v2.5_template.html](templates/v2.5_template.html)

## Root Cause Hypothesis

- Marker payload and marker template were designed around date/name tags only.
- Tab numbering is generated in a separate rendering path and is not propagated into marker metadata.
- Without shared index data, map and tabs cannot present a consistent number-based navigation cue.

## Scope of Likely Fix

- Add a marker index field (for example `idx`) in `_build_map_markers` aligned to destination order.
- Update Leaflet marker icon HTML/CSS to render a smaller, clearer marker with:
  - visible stop number
  - date text above
  - destination label below in smaller font on dark background
- Keep existing popup behavior, adding stop number context where useful.
- Maintain mobile readability and avoid regressions in fitBounds/popup anchoring.

## Non-Breaking Validation Plan

- Static validation:
  - inspect generated `MAP_MARKERS_JSON` for index fields matching tab order.
- UI verification:
  - regenerate itinerary and confirm each marker number corresponds to tab number.
  - verify readability on desktop and mobile widths.
  - verify labels remain legible and do not excessively overlap for dense clusters.
- Guardrails:
  - do not break map initialization, pin placement, or route polyline rendering.

## Notes

- This report captures a usability/readability defect in map navigation affordance.
- No implementation performed in this report; this is investigation and scoping only.
