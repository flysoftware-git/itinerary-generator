# PR-005: Possible Daily Schedule is not realistic, route-aware, or time-budgeted

Labels: `review:v1.4-output`, `type:bug`, `status:open`, `severity:high`, `area:ai-content`, `area:scheduling`, `area:html-output`, `area:manifest-config`

Manifest: `C:/Dev/Sandbox/sw_manifest.yaml`
Output Artifact: `output/index.html`
Baseline Version: `v1.4.0`
Review Source: manual output review

## Summary

Daily schedules across destinations can be implausible and disconnected from real constraints (arrival logistics, route sequencing, activity duration, meal windows, and per-day time budget). This appears as over-packed plans, strenuous long-duration items placed in unrealistic windows, and repeated day plans that do not vary meaningfully by destination context.

## Expected Behavior

- Day 1 for each destination should be treated as arrival-constrained.
- For the first destination in the trip, schedule should reserve Day 1 morning for travel/arrival and orientation only (no major activity block).
- Daily plans should fit a configurable time budget (for example: hours-available-per-day = 5).
- Activity sequencing should be route-aware and include realistic transitions, meal windows, and parking/transport buffers.
- Long-duration activities (for example 4-5 hour strenuous hikes) should not be combined with additional major blocks when that exceeds the day budget.
- Multi-day destinations should avoid near-identical day plans; each day should have distinct, non-redundant intent.

## Actual Behavior

- Schedules can include major activities in periods that should be consumed by travel.
- Long activities can be positioned as if they are one block among many without explicit budget tradeoffs.
- Existing fallback/synthesis can produce generic day templates that are not budget-aware.
- Distinct-day variation is currently enforced by text suffix heuristics, which may still produce functionally repeated plans.

## Evidence

- [generator/ai_content.py](generator/ai_content.py) currently normalizes and post-processes schedule content via `_normalize_schedule`, `_ensure_day_period_coverage`, `_dedupe_schedule_day_content`, and `_inject_travel_realism`.
- [generator/ai_content.py](generator/ai_content.py) `_inject_travel_realism` currently reserves first-day morning as `Travel from ...` for first destination, but does not reserve the full first day from activity scheduling.
- [generator/ai_content.py](generator/ai_content.py) `_dedupe_schedule_day_content` uses suffix-based variation text, which can create textual differences without true route/time-budget differences.
- [generator/html_assembler.py](generator/html_assembler.py) `_build_schedule` synthesizes fallback Day 1 schedule entries from top attractions when schedule content is sparse; these synthesized entries are not time-budget-aware.
- [tests/test_ai_content_normalization.py](tests/test_ai_content_normalization.py) currently validates first-day morning travel injection and synthetic uniqueness behavior, but does not validate per-day hour budget fit or route-time feasibility.

## Reproduction Context

- Manifest used: `C:/Dev/Sandbox/sw_manifest.yaml`
- Destinations explicitly cited by review:
  - `St. George, Utah` (arrival context and onward Zion sequencing)
  - `Zion National Park` (example mismatch: long strenuous activity proposed in unrealistic context)
  - `Bryce Canyon National Park` (reported repeated day recommendations)
- Section reviewed: `Possible Daily Schedule`

## Suspected Area

- Primary component: schedule generation and normalization logic
- Possible files:
  - [generator/ai_content.py](generator/ai_content.py)
  - [generator/html_assembler.py](generator/html_assembler.py)
  - [prompts/destination_content.txt](prompts/destination_content.txt)
  - [config.yaml](config.yaml)
  - [tests/test_ai_content_normalization.py](tests/test_ai_content_normalization.py)

## Root Cause Hypothesis

- The current model prompt requests schedule structure and some travel-aware language, but does not enforce a quantified daily time budget contract.
- Post-generation normalization edits text for consistency and coverage, but does not perform explicit duration accounting or route-time feasibility checks.
- Fallback schedule synthesis in HTML assembly can bypass realism constraints entirely when upstream schedule data is thin.
- No traveler profile or pace configuration currently feeds scheduling constraints.

## Scope of Likely Fix

- Add explicit schedule policy in config/manifest for v2 (for example):
  - `schedule.hours_available_per_day`
  - `schedule.arrival_day_policy` (none/light/full)
  - `schedule.meal_break_minutes`
  - `schedule.transition_buffer_minutes`
  - optional `traveler_profile` (senior, moderate, athletic) with pacing multipliers
- Move schedule feasibility from prompt-only guidance to deterministic post-processing rules.
- Introduce day-level budget accounting:
  - parse attraction durations
  - add transfer/meal buffers
  - cap day blocks to budget
  - defer overflow items to later days
- Replace text-only day dedupe with route-zone/activity-category diversification under the budget constraint.
- Ensure renderer never injects synthetic schedule entries that violate schedule policy.

## Non-Breaking Validation Plan

- Unit tests (new/expanded):
  - first-day no-activity policy behavior
  - strict hours-per-day budget cap
  - long-activity fit checks (single dominant block day)
  - route-transition/meal buffer accounting
  - anti-duplication based on planned items rather than suffix-only text
- Integration tests:
  - destination-local schedule generation for St. George, Zion, and Bryce
  - full-manifest run with schedule-policy assertions in final JSON/HTML representation
- Guardrails:
  - do not remove mandatory seeded attractions from itinerary; allow carryover/reassignment instead
  - preserve current URL/content-linking behavior while changing schedule semantics

## Notes

- This report is intentionally broad because the issue spans policy, generation, normalization, and rendering.
- User requested investigation/reporting first and no implementation until explicitly instructed.
- No implementation performed in this report; this is investigation and scoping only.
