# HTML Assembly Pipeline

## Purpose
HTML assembly converts enriched trip data into a single portable itinerary page,
then validates structural integrity and writes related PWA assets.

## Stage Context
In `generator/main.py`, assembly occurs after:
- AI content generation
- cultural events discovery
- image fetching
- URL discovery + audit

Sequence:
1. Build `_meta` (versions, timestamps, provider/model usage)
2. `HTMLAssembler.assemble(trip)`
3. Write `index.html`
4. Write PWA assets (`manifest.webmanifest`, `sw.js`)
5. Validate output with `HTMLValidator`
6. Write validation report

## Template Integrity and Placeholder Model
`HTMLAssembler` uses a frozen template (`templates/v2.5_template.html`) and
hard-fails if SHA-256 checksum mismatches the recorded value in
`templates/checksums.txt`.

Assembly uses direct string replacement over named placeholders such as:
- trip title/theme
- nav tabs
- destination sections
- map markers JSON
- route link
- footer attribution

## High-Level Assembly Flow
`assemble(trip)` performs:
1. Load and checksum-verify template
2. Inject generator metadata comment stamp
3. Replace trip-level placeholders
4. Build route-map link and marker payload
5. Build tab bar
6. Build one destination section per destination
7. Inject `DRIVE_DESCRIPTIONS` JSON object
8. Inject generator footer

## Destination Section Composition
Each destination section is assembled in fixed order:
1. Header (hero image + planning links)
2. Intro/what-to-know block
3. Image gallery
4. Expected environment card
5. Getting Here card (with en-route stops)
6. Top Attractions card (plus scenic drives)
7. Possible Daily Schedule card
8. Cultural Events card
9. Dinner Recommendations card
10. Optional debug block (config controlled)

## Link Normalization and Fallbacks
All external links are normalized by `_normalize_external_url`:
- Rejects unsafe protocols (`javascript:`, `data:`)
- Accepts `http`, `https`, `mailto`
- Converts protocol-relative URLs to `https`
- Adds `https://` to plain host/path values

Fallback behavior:
- Missing attraction/restaurant/en-route URLs fall back to maps-search links.
- Fallback query composition avoids contradictory destination suffixes using
	location qualification checks.

## Map and Route Artifacts
Assembly generates:
- Full-route Google Maps URL for nav button
- Leaflet marker payload with departure/return support
- Per-leg Getting Here route URL with waypoint stops

## Scenic Drive Modal Payload
`_build_drive_descriptions` builds `var DRIVE_DESCRIPTIONS = {...}` keyed by raw
drive title. Entries include:
- title/category/distance/best_time/description/vehicle_requirement
- optional discovered URL when valid

Descriptions are sanitized to remove metadata/attribution leakage and template
artifacts before embedding.

## Image Handling in HTML
Images are referenced as portable relative paths (`./images/<file>`), not absolute
`file://` paths, so outputs remain movable.

Caption generation prioritizes:
1. cleaned credit
2. source + title
3. source
4. title

## Validation Touchpoints
`HTMLValidator.validate` checks:
- `var DRIVE_DESCRIPTIONS` exists (not `const`)
- modal trigger keys match `DRIVE_DESCRIPTIONS` keys
- div balance per destination section
- no orphan script tags inside destination sections
- image count meets minimum per destination

Validation output is written via `ReportWriter` and controls non-zero exit on errors.

## Output Artifacts
Primary:
- `index.html`

PWA:
- `manifest.webmanifest`
- `sw.js`

Report:
- validation report JSON in output directory

## Design Tradeoffs
Pros:
- Deterministic output shape and rendering order.
- Strong template integrity guarantees.
- Portable output assets.

Cons:
- String assembly is less resilient than DOM-aware transformation.
- Placeholder mismatches can fail late if template changes unexpectedly.

## Troubleshooting Checklist
Symptom: Missing section content.
- Verify placeholder replacement and section builder returned non-empty strings.

Symptom: Broken modal behavior.
- Validate drive-title keys against `DRIVE_DESCRIPTIONS` payload.

Symptom: Unsafe/broken links.
- Inspect normalization and fallback query composition paths.

Symptom: Validation failures on div balance or scripts.
- Inspect section HTML composition order and newly added markup blocks.

## Key Files
- `generator/html_assembler.py`
- `templates/v2.5_template.html`
- `generator/html_validator.py`
- `generator/main.py`
