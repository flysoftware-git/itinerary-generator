# PR-006: Route overview map markers do not show stop numbers matching destination menu

Labels: `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:medium`, `area:html-output`, `area:map-ui`, `area:usability`

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

## Resolution

- Destination markers render a stop index aligned to destination tab order (`idx` and `stop_index`).
- Marker icons were made more compact to reduce overlap and improve scanability.
- Secondary date/time text now appears centered under the destination label in a readable stacked layout.
- Popup labeling preserves stop index and date context.

## Evidence

- Marker JSON includes stop order metadata in [generator/html_assembler.py](generator/html_assembler.py).
- Marker template now uses compact class-based rendering (`route-marker-*`) and reduced icon geometry in [templates/v2.5_template.html](templates/v2.5_template.html).
- Regression assertions updated in [tests/test_html_assembler.py](tests/test_html_assembler.py).
- Smoke output confirms updated marker markup in [output/index.html](output/index.html).

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

## Scope of Applied Fix

- Added/retained marker index metadata in payload (`idx`, `stop_index`) aligned to destination order.
- Updated Leaflet marker icon HTML/CSS to use compact, wrapped labels with centered secondary date context.
- Preserved popup behavior while improving stop-index context and date/time formatting.
- Tuned icon geometry (`iconSize`, `iconAnchor`, `popupAnchor`) for better readability and overlap control.

## Validation

- Unit tests: `tests/test_html_assembler.py` and `tests/test_url_discovery.py` passed (`169 passed`).
- Smoke run: full generator execution completed successfully and produced updated marker markup in `output/index.html`.
- Guardrails: map initialization and route polyline rendering remained intact.

## Notes

- This issue is now closure-ready based on implemented marker UX updates and post-change validation.
