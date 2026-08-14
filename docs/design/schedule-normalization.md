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
- normalized attractions (for duration-aware packing)
- schedule anchors (`trip.default_day_start_time`, destination `schedule_start_time`)
- activity-time budgets (`trip.default_daily_activity_hours`, destination `daily_activity_hours`)

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
distinction from prior days. Runs before `_inject_travel_realism`'s
attraction-name rotation, so it's the only defense against duplication in
generic fallback text (e.g. `_ensure_day_period_coverage`'s filler string)
that carries no canonical attraction/restaurant name for that later pass to
rotate in.

Detection is per-period against the full running history of every prior
period's summary (fixed -- previously only triggered when *every* period in
a day was already a duplicate, so a day with 2 of 3 periods repeated, but
one genuinely new, triggered nothing at all):
- Appends a period-specific variation suffix to whichever specific period(s)
	are duplicates -- not just the first eligible period in the day
	regardless of which period actually repeated.
- Keeps the original summary but adds diversity intent.

Rationalization requirement:
- For multi-day destinations, each day must contain at least one substantive
	differentiator from prior days (distinct area, activity focus, or transfer duty).
- Cosmetic wording changes alone do not satisfy differentiation quality.

Known gap (not addressed here): no cross-destination schedule-text dedup
exists. Cross-destination dedup exists for scenic drives, what_to_know, and
attraction/en-route overlap, but `possible_daily_schedule` text itself is
untouched across destinations -- lower priority than the within-destination
fix since schedule prose is destination-specific and less likely to
literally duplicate verbatim across different destinations the way a
generic fallback string does within one.

## Travel Realism Injection
`_inject_travel_realism` applies route-aware adjustments:
- For multi-day stops, Day 1 can get arrival-driving context.
- Last day evening gets onward-drive preparation note when a next destination exists.
- Single-day schedules skip this to avoid duplicating route detail already shown
	in the Getting Here card.

v2.1 time-anchor behavior:
- Effective day start is resolved by precedence:
	1) destination `schedule_start_time`
	2) trip `default_day_start_time`
	3) fallback `10:00 AM`
- For non-first destinations, when inbound `drive_time` is present, Morning is
	allocated to transit with computed depart/arrival labels.

v2.1 activity-budget behavior:
- Effective per-day activity budget is resolved by precedence:
	1) destination `daily_activity_hours`
	2) trip `default_daily_activity_hours`
	3) fallback `5` hours
- After Morning transit is allocated, Afternoon can be rewritten to a
	multi-activity plan only when estimated durations fit inside the budget.
- Current packing is intentionally bounded (up to three activities) and keeps
	transfer/parking buffer language in the generated summary.
- The arrival-day budget is discounted by the recorded drive duration before
	packing: the activity budget represents willingness/time for a normal full
	day, and a drive eats directly into that allotment rather than being free
	time on top of it (fixed -- previously the full undiscounted budget was
	used even when most of the day was already consumed by travel).
- Packing now also extends to Day 2+ of a multi-day stay (previously only
	Day 1's Afternoon of a non-first destination, arriving via a recorded
	drive, ever got capacity-aware packing -- every other day/period kept
	generic AI text or the older same-name-swap rotation). Day index rotates
	which attractions are considered first so consecutive days don't
	greedily pick the identical set.
- Deliberately not extended: Morning and Evening periods (Morning is usually
	already spoken for by transit/logistics text; Evening is anchored to a
	single dinner slot via a separate rotation mechanism, not a multi-activity
	block), and the trip's first destination's arrival day (no comparable
	drive-duration signal exists for travel from trip origin). True
	inter-activity travel-time modeling (distance *between* attractions, not
	just total daily budget) would need geocoded attraction positions --
	a separate, larger effort, not covered here.

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

Renderer precedence requirement:
- When normalized structured schedule content exists, rendering must preserve it.
- Renderer synthesis is only allowed for missing schedule content, not as override.

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

Symptom: Afternoon looks overloaded after transit.
- Verify `default_daily_activity_hours` / `daily_activity_hours` values.
- Check attraction durations; missing durations fall back to default estimates.

## Key Files
- `generator/ai_content.py`
- `prompts/destination_content.txt`
