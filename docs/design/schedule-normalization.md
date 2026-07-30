# Schedule Normalization

## Purpose
Schedule normalization turns variable LLM output into a predictable structure:
`[{day_label, periods:[{period, summary}]}]`, while preserving useful detail and
enforcing minimum UX quality.

## Entry Point
`AIContentGenerator._normalize_schedule(...)` in `generator/ai_content.py`.

Inputs include:
- raw schedule payload from model output
- dinner recommendations (for named-dinner constraints)
- destination dates (for day-count inference)
- route context (`getting_here`, previous destination, next destination)

## Accepted Input Shapes
The normalizer accepts:
- List of day objects with `periods`
- List of freeform strings
- Dict with `morning` / `afternoon` / `evening`

Everything else becomes an empty schedule.

## Text Cleanup Rules
For each summary:
- Strips emoji-leading markers and redundant labels (`Morning:`, etc.).
- Strips leading clock-time prefixes (for example `7:00 AM - ...`).
- Collapses whitespace.
- If summary mentions dinner but no known restaurant is named, rewrites to use
	the first normalized restaurant name.

## Day Count Inference and Expansion
`_infer_day_count(dates)` behavior:
- Supports month/day ranges and ISO date ranges.
- Returns range length bounded to 1..5 days.

`_expand_days` behavior:
- Truncates extra generated days above inferred count.
- Expands missing days up to inferred count by cloning period intent with light
	variation scaffolding.

## Coverage Guarantees
`_ensure_day_period_coverage` enforces per-day period coverage:
- Required periods: Morning, Afternoon, Evening.
- Missing periods are filled from:
	1) same period from earlier generated days,
	2) generic fallback text.
- Evening fallback includes a dinner mention tied to a known restaurant when possible.

## De-duplication and Variation
`_dedupe_schedule_day_content` ensures each day has at least one meaningful
distinction from prior days.

If a day is fully repetitive:
- Appends a period-specific variation suffix to first eligible period
- Keeps the original summary but adds diversity intent

## Travel Realism Injection
`_inject_travel_realism` applies route-aware adjustments:
- For multi-day stops, Day 1 can get arrival-driving context.
- Last day evening gets onward-drive preparation note when a next destination exists.
- Single-day schedules skip this to avoid duplicating route detail already shown
	in the Getting Here card.

## Reserved Travel Windows
The scheduler now reserves specific windows for transportation at trip boundaries.

First destination rule:
- Day 1 Morning is reserved for travel from trip origin.
- Trigger condition: `previous_destination` is empty or `none`.

Final destination rule:
- Last day Afternoon and Evening are reserved for return travel to base.
- Trigger condition: `next_destination` is empty.

Intermediate destinations:
- Keep onward-drive guidance on final evening (existing behavior).

Design intent:
- Prevent unrealistic booking of activities in boundary windows where travel
  logistics dominate.

## Departure-Aligned Scenic Drive Reclassification
`normalize_trip_content` includes a final-leg route pass that inspects scenic
drives on the last destination.

Behavior:
- One-way scenic drives aligned with return-route tokens are moved out of
  `scenic_drives` and into `ai_content.getting_there.route_options`.
- Non-aligned or round-trip scenic drives remain in `scenic_drives`.
- When moved options exist and no return summary is present, a default
  departure-leg summary is injected.

Design intent:
- Preserve useful departure-route suggestions while preventing them from being
  rendered as in-stay activities.

## Interaction With Other Normalizers
Schedule normalization runs after:
- attractions normalization/dedupe
- en-route overlap removal
- restaurant normalization

This allows schedule cleanup to reference final restaurant names and route details.

## Design Tradeoffs
Pros:
- Stable UI schema for renderer.
- Better schedule completeness and reduced repetition.
- Cleaner output from mixed-quality LLM responses.

Cons:
- Expansion can create generic text when source output is sparse.
- Bounding to 5 days limits very long stays by design.

## Troubleshooting Checklist
Symptom: Missing Morning/Afternoon/Evening blocks.
- Check `_ensure_day_period_coverage` path and earlier parsing branch.

Symptom: Dinner references are vague.
- Verify `dinner_recommendations` passed into normalizer and that names exist.

Symptom: Days feel repetitive.
- Inspect `_dedupe_schedule_day_content` suffix insertion and upstream model quality.

Symptom: Arrival/departure context absent.
- Confirm multi-day inference and `previous_destination` / `next_destination` inputs.

## Key Files
- `generator/ai_content.py`
- `prompts/destination_content.txt`
