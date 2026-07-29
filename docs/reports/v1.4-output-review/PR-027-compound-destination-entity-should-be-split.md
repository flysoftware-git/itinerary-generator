# PR-027: Compound destination label combines multiple entities into one link target (Santa Fe Plaza & Palace of the Governors)

Labels: `review:v1.4-output`, `type:bug`, `status:open`, `severity:high`, `area:entity-classification`, `area:url-discovery`, `area:content-linking`, `area:html-output`

Manifest: `trip_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

A compound attraction label combines two distinct entities into one card and one link target:

- `Santa Fe Plaza & Palace of the Governors`

Compound entities should not be published as a single destination because they conflate distinct places and weaken link precision.

## Expected Behavior

- Compound destination names should be split into distinct entities before URL discovery.
- Each entity should receive its own validated target link, or one should be selected as canonical with clear naming.
- One broad municipal homepage should not stand in for multiple named POIs.

## Actual Behavior

- A single attraction card is rendered with link text:
  - `Santa Fe Plaza & Palace of the Governors`
- Link target is:
  - `https://www.santafenm.gov`
- This represents a broad city domain and does not provide explicit one-to-one mapping for both named entities.

## Evidence

- In [output/index.html](output/index.html), line 1622 contains:
  - visible link text: `Santa Fe Plaza & Palace of the Governors`
  - URL: `https://www.santafenm.gov`

## Reproduction Context

- Manifest used: `trip_manifest.yaml`
- Destination context: `Santa Fe` attractions
- Subject: `Santa Fe Plaza & Palace of the Governors`

## Suspected Area

- Primary components: entity extraction, compound-name splitting, and link resolution
- Possible files:
  - [generator/ai_content.py](generator/ai_content.py)
  - [generator/url_discovery.py](generator/url_discovery.py)
  - [generator/url_validator.py](generator/url_validator.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Upstream content generation allows conjunction-based compound POI names (`&`, `and`) to persist as single attraction labels.
- URL discovery then resolves one generic target for the compound string instead of two entity-specific targets.

## Scope of Likely Fix

- Add compound-entity detection (`&`, `and`, `/`) for attraction names.
- Split into discrete POIs before discovery and validation.
- Enforce one-card-one-entity in final output.

## Non-Breaking Validation Plan

- Unit tests:
  - compound attraction labels are split into separate entities.
  - each split entity resolves to its own validated link or is dropped if invalid.
- Integration checks:
  - regenerate Santa Fe output and verify no compound attraction cards are emitted.

## Notes

- This is intake-only; no implementation change is included.
