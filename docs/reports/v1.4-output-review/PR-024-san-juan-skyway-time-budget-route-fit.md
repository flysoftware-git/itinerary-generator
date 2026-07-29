# PR-024: San Juan Skyway Day Trip likely exceeds day-level time budget unless aligned with inter-destination transfer

Labels: `review:v1.4-output`, `type:bug`, `status:open`, `severity:high`, `area:ai-content`, `area:scheduling`, `area:route-planning`, `area:html-output`

Manifest: `trip_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

`San Juan Skyway Day Trip` is rendered as an in-destination scenic activity with a stated size of a full-day, 236-mile loop. This likely exceeds the practical daily activity time budget unless it is intentionally integrated into transportation between destinations.

## Expected Behavior

- Large loop drives should be included only when they fit the destination day budget.
- If an activity likely exceeds available per-day hours, it should be:
  - omitted,
  - downgraded to optional/alternate, or
  - explicitly tied to transfer-day routing where it replaces other activities.
- Day-trip entries should be route-aware and schedule-aware, not just scenic relevance.

## Actual Behavior

- A card is rendered for:
  - `San Juan Skyway Day Trip`
  - `236-mile loop — allow a full day`
- The item appears alongside normal destination activities without explicit transfer-day fit constraints.

## Evidence

- In [output/index.html](output/index.html):
  - line 1463 renders `San Juan Skyway Day Trip`
  - badge text states: `236-mile loop — allow a full day`
  - card is a scenic-drive entry using placeholder link behavior (`href="#"`)

## Reproduction Context

- Manifest used: `trip_manifest.yaml`
- Destination context: `Pagosa Springs`
- Entity: `San Juan Skyway Day Trip`

## Suspected Area

- Primary components: day-trip candidate scoring and schedule/time-budget gating
- Possible files:
  - [generator/ai_content.py](generator/ai_content.py)
  - [generator/cultural_events.py](generator/cultural_events.py)
  - [generator/html_assembler.py](generator/html_assembler.py)

## Root Cause Hypothesis

- Scenic/day-trip generation emphasizes attractiveness without hard constraints for available daily hours and transfer-day compatibility.
- No explicit gating appears to reject oversized loops when they conflict with day-level itinerary capacity.

## Scope of Likely Fix

- Add budget-aware and route-aware feasibility checks for day-trip drives.
- Require explicit transfer-day compatibility for long loop candidates.
- Suppress or demote candidates exceeding configured daily time limits.

## Non-Breaking Validation Plan

- Unit tests:
  - day-trip drives exceeding configured day budget are rejected or marked optional.
  - long drives are allowed only when modeled as transfer-day-compatible.
- Integration checks:
  - regenerate output and verify oversized loop entries do not appear as default in-destination activities unless route-fit logic supports them.

## Notes

- This is intake-only; no implementation change is included.
