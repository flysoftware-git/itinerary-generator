# PR-029: Departure leg lacks dedicated Getting Here section, causing Turquoise Trail to be misplaced as in-stay activity

Labels: `review:v1.4-output`, `type:bug`, `status:open`, `severity:high`, `area:html-output`, `area:route-planning`, `area:scheduling`, `area:scenic-drives`

Manifest: `trip_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

`Turquoise Trail Scenic Byway` (Santa Fe → Albuquerque corridor) appears as a standard in-destination scenic card under Santa Fe rather than being explicitly modeled as part of the departure/return leg. The output currently lacks a dedicated `Getting There`-style section for the final departure leg to Albuquerque.

## Expected Behavior

- Transfer-corridor drives (such as Turquoise Trail to Albuquerque) should be attached to the departure leg when they align with onward routing.
- The itinerary should include explicit departure-leg logistics (analogous to `Getting Here`) for the final outbound segment.
- Final-day scenic suggestions should be route-aware and represented as departure-route options, not generic in-stay attractions.

## Actual Behavior

- `Turquoise Trail Scenic Byway` is rendered as a scenic card in Santa Fe activities:
  - `50 miles one-way`
  - placeholder link style (`href="#"` via drive-link)
- Return-to-Albuquerque appears only in schedule summary text, without a dedicated departure-route block.

## Evidence

- In [output/index.html](output/index.html):
  - line 1628: `Turquoise Trail Scenic Byway` scenic card in attractions
  - line 1666: schedule text references return travel to Albuquerque
  - line 1669: schedule text references return travel buffer
  - line 353: full route map already includes destination `Albuquerque, NM`
  - line 1798: map markers include `RET` marker for Albuquerque

## Reproduction Context

- Manifest used: `trip_manifest.yaml`
- Final destination context: `Santa Fe`
- Departure/return target: `Albuquerque, NM`

## Suspected Area

- Primary components: final-leg itinerary modeling and scenic-drive placement rules
- Possible files:
  - [generator/ai_content.py](generator/ai_content.py)
  - [generator/html_assembler.py](generator/html_assembler.py)
  - [generator/url_discovery.py](generator/url_discovery.py)

## Root Cause Hypothesis

- Data model supports per-destination `Getting Here` from previous stop but lacks symmetric structure for `Getting There`/departure leg from the last stop.
- Scenic-drive generation is destination-scoped only, so transfer-aligned drives are rendered as local activity cards instead of departure-leg options.

## Scope of Likely Fix

- Add explicit departure-leg section (e.g., `Getting There`) for final destination.
- Route transfer-aligned scenic drives into departure section when they overlap with return path.
- Keep destination activities and transfer logistics as distinct planning blocks.

## Non-Breaking Validation Plan

- Unit tests:
  - final destination emits departure-leg logistics when return location exists.
  - transfer-aligned scenic drives are tagged and placed in departure section.
- Integration checks:
  - regenerate output and verify Turquoise Trail appears as departure-route option to Albuquerque, not only as generic in-stay scenic card.

## Notes

- This is intake-only; no implementation change is included.
