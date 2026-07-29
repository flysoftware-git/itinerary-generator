# PR-015: Telluride repeats the same local tip and More info link in What to Know and Cultural Events

Labels: `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:medium`, `area:ai-content`, `area:content-linking`, `area:html-output`, `area:deduplication`

**Fixed in:** v1.4.3 — `_deduplicate_cross_section_tips` in `normalize_trip_content` detects when `cultural_events.local_tip` text appears verbatim in any `what_to_know` field value for the same destination and removes it from cultural events. Note: the `More info` search-query URL duplicate was already resolved by Epic 1 (v1.4.1 `google_search` class blocked).

Manifest: `trip_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

In Telluride, the exact same local tip text and identical `More info` link appear in both:

- `What to Know About Telluride`
- `Cultural Events`

This duplicates content across adjacent sections and reduces section specificity.

## Expected Behavior

- `What to Know` and `Cultural Events` should provide distinct, non-duplicative content.
- If a local tip is already surfaced in one section, the other section should use different context or omit duplication.
- `More info` links should correspond to section-specific content, not repeated verbatim text blocks.

## Actual Behavior

- The same local tip sentence and the same Google search `More info` URL are rendered in both Telluride sections.

## Evidence

- In [output/index.html](output/index.html):
  - line 1240: local tip under `What to Know About Telluride` with `More info` link
  - line 1332: identical local tip under `Cultural Events` with the same `More info` link
- Shared URL in both places:
  - `https://www.google.com/search?q=Check%20the%20Sheridan%20Opera%20House%20schedule%20for%20any%20live%20music%20events%20during%20your%20stay.%20It%27s%20a%20historic%20venue%20that%20frequently%20features%20local%20and%20touring%20artists.%20Telluride`

## Reproduction Context

- Manifest used: `trip_manifest.yaml`
- Destination: `Telluride`
- Sections involved: `What to Know` and `Cultural Events`

## Suspected Area

- Primary components: section-content generation and cross-section de-duplication
- Possible files:
  - [generator/ai_content.py](generator/ai_content.py)
  - [generator/cultural_events.py](generator/cultural_events.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Section generation pipelines produce similar local tips independently without a destination-level de-duplication or semantic overlap check before render.

## Scope of Likely Fix

- Add cross-section duplicate detection for destination-level text snippets.
- Enforce section differentiation rules so `What to Know` and `Cultural Events` are semantically distinct.
- Optionally suppress one repeated tip when content identity exceeds threshold.

## Non-Breaking Validation Plan

- Unit tests:
  - identical or near-identical local-tip text across `What to Know` and `Cultural Events` is reduced to one occurrence.
  - section-specific replacement content is retained when available.
- Integration checks:
  - regenerate output and confirm Telluride no longer duplicates the Sheridan Opera House local tip across both sections.

## Notes

- This report is intake-only; no implementation change is included here.
