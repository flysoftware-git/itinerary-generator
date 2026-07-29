# PR-001: What to Know duplicates generic guidance across destinations

Labels: `review:v1.4-output`, `type:bug`, `status:open`, `severity:medium`, `area:html-output`, `area:content-linking`

Manifest: `C:/Dev/Sandbox/sw_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual link-by-link review

## Summary

The `What to Know About ...` card frequently repeats broad, non-destination-specific guidance across parks and towns. Common safety and logistics advice such as hydration, weather awareness, parking pressure, and quiet-hours etiquette appears repeatedly at destination level even when it would be better expressed once in shared travel guidance. This reduces distinctiveness, increases visual noise, and makes truly destination-specific advice less visible.

## Expected Behavior

- Route-level or broadly shared travel advice should be centralized under a shared section such as route overview or common travel guidance.
- Destination-level `What to Know` cards should emphasize distinctive, place-specific information.
- Generic fallback language should not dominate multiple park cards with only minor wording changes.

## Actual Behavior

- Similar safety guidance appears across multiple destinations with only light wording changes.
- Similar crowd/parking/time-of-day patterns are repeated independently in multiple `What to Know` cards.
- The renderer outputs every populated `what_to_know` field without checking whether the content is generic or redundant with shared trip context.

## Evidence

- [output/index.html](output/index.html) contains `What to Know` cards for all eight destinations in the reviewed `sw_manifest` run.
- Repeated safety examples observed in rendered HTML:
  - St. George: `Stay hydrated...`
  - Zion: `Stay hydrated and carry sufficient water...`
  - Bryce: `Stay hydrated and be aware of changing weather conditions...`
  - Capitol Reef: `Stay hydrated and carry sufficient water...`
  - Moab: `Stay hydrated and protect yourself from sun exposure...`
  - Pagosa Springs: `Stay hydrated and be aware of changing weather conditions...`
- Repeated generic timing/parking/crowd guidance appears in multiple cards using variants of:
  - early morning / late afternoon
  - fewer crowds on weekdays
  - limited parking / arrive early

## Reproduction Context

- Manifest used: `C:/Dev/Sandbox/sw_manifest.yaml`
- Section reviewed: destination `What to Know About ...` cards
- Visible content classes involved:
  - `.intro-note-card`
  - `.intro-note-text`

## Suspected Area

- Primary component: what-to-know generation and rendering
- Possible files:
  - [generator/ai_content.py](generator/ai_content.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- [generator/ai_content.py](generator/ai_content.py) normalizes `what_to_know` into a fixed set of required fields and supplies generic fallback defaults for each field when content is weak or absent.
- [generator/html_assembler.py](generator/html_assembler.py) renders all populated `what_to_know` fields unconditionally, with no pass that suppresses generic or repeated guidance.
- There is no distinction today between:
  - shared route-wide advice
  - destination-specific advice
  - fallback filler text

## Scope of Likely Fix

- Introduce a distinction between common travel guidance and destination-specific guidance.
- Add a redundancy filter or specificity scoring pass before rendering `what_to_know` fields.
- Potentially move highly generic items like hydration, weather-awareness, parking scarcity, and basic trail etiquette into a shared route overview/safety section.
- Preserve distinctive per-destination guidance such as shuttle constraints, altitude effects, sacred-site etiquette, or local cultural specifics.

## Non-Breaking Validation Plan

- Cheapest feasibility run:
  - one destination-local run for a representative park/town pair (for example Zion plus Bryce, or St. George plus Zion) to compare generic versus distinctive guidance behavior.
- Additional validation:
  - targeted tests around `what_to_know` normalization and rendering.
  - snapshot-style HTML assertions that generic fallback text is suppressed or consolidated when repeated.
- Regression guardrail:
  - do not remove destination-critical logistics such as Zion shuttle rules or Bryce altitude warnings.

## Notes

- This is a quality/readability issue, not a broken-link issue.
- Fix risk is moderate because over-aggressive deduplication could remove important destination-specific safety information.
- No implementation performed in this report; this is investigation and scoping only.