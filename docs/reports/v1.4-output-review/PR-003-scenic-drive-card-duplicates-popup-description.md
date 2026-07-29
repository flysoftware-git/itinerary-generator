# PR-003: Scenic drive card text duplicates popup description

Labels: `review:v1.4-output`, `type:bug`, `status:open`, `severity:medium`, `area:html-output`, `area:content-linking`

Manifest: `C:/Dev/Sandbox/sw_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

Scenic drive entries currently present essentially the same descriptive text in two places: the visible drive card teaser and the drive popup content. The only practical difference is the popup's optional more-info link. This defeats the intended UX, where the card should provide a concise teaser and the popup should contain the fuller operational/detail narrative.

## Expected Behavior

- Scenic drive cards should show a brief teaser or hook.
- The popup should expand into fuller practical and operational detail.
- Example target behavior:
  - card: "Zion Canyon Scenic Drive uses the park shuttle system and is the main access corridor for major canyon stops."
  - popup: shuttle frequency, likely waits, boarding expectations, seasonal/private-vehicle access policy, etc.

## Actual Behavior

- The card description for a scenic drive is effectively the same text as the popup description payload.
- Popup value comes from the same `drive['description']` content already printed into the visible card.

## Evidence

- In [generator/html_assembler.py](generator/html_assembler.py), drive cards render `drive['description']` directly into `.attr-desc`.
- In [generator/html_assembler.py](generator/html_assembler.py), popup payload is built by `_build_drive_descriptions(...)`, which again uses the same cleaned `drive['description']` field.
- Rendered output contains scenic-drive card descriptions while `DRIVE_DESCRIPTIONS` JavaScript payload contains matching long-form text for the same titles.

## Reproduction Context

- Manifest used: `C:/Dev/Sandbox/sw_manifest.yaml`
- Section reviewed: scenic drives and scenic-drive popup payload
- Representative examples observed:
  - `Zion Canyon Scenic Drive`
  - `Kolob Canyons Road`
  - `Capitol Reef Scenic Drive`
  - `Wolf Creek Pass Scenic Drive`

## Suspected Area

- Primary component: scenic drive rendering/data modeling
- Possible files:
  - [generator/html_assembler.py](generator/html_assembler.py)
  - upstream scenic-drive content generation path in [generator/ai_content.py](generator/ai_content.py)

## Root Cause Hypothesis

- Scenic drives currently have a single description field that is reused for both:
  - card teaser rendering
  - popup detail rendering
- There is no dedicated model split such as `teaser` vs `details`, nor a derived condensation step for card text.

## Scope of Likely Fix

- Introduce separate scenic-drive fields for summary/teaser versus expanded detail, or derive teaser text from full description.
- Update HTML assembly so cards consume teaser text while popup uses the fuller detail text.
- Preserve existing validated more-info link behavior.
- Likely requires touching both data generation/normalization and rendering logic.

## Non-Breaking Validation Plan

- Cheapest feasibility run:
  - destination-local run for Zion only, because `Zion Canyon Scenic Drive` provides the clearest expected teaser/detail distinction.
- Additional validation:
  - HTML assertions that card and popup are no longer text-identical.
  - regression check that popup still shows more-info link when a validated drive URL exists.
- Guardrail:
  - do not remove important operational details from the popup when condensing card text.

## Notes

- This is primarily a UX/content-structure issue, not a broken-link issue.
- Risk is moderate because teaser condensation can become too vague if not derived carefully.
- No implementation performed in this report; this is investigation and scoping only.