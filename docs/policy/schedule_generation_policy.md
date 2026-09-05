# Schedule Generation Policy (Lifecycle + Constraint Model)

## Purpose

Define deterministic policy for schedule text so behavior is stable across:

- arrival day,
- single-day stop,
- multi-day stay days,
- departure day.

This policy is intended to be validated with unit tests that run independently of manifest parsing and full itinerary generation.

## Inputs (Authoritative)

The scheduler should treat the following as input contract:

- `possible_daily_schedule` from AI (draft only; not authoritative).
- `top_attractions` after normalization and de-duplication.
- `getting_here` transfer metadata (`travel_time`, `en_route_stops`).
- destination date span (for day count).
- trip anchors (`departure`, `departure_datetime`, `return`, `return_datetime`).
- lodging anchors (`lodging.location`, `lodging.checkin_time`).
- activity budget controls (`default_daily_activity_hours`, `daily_activity_hours`).

## Day Archetypes

### 1) Arrival Transfer Day

- Exactly one period may mention transfer/logistics from prior origin.
- Check-in language is allowed only on arrival day.
- Heavy activity blocks immediately after transfer should be softened or replaced with light orientation guidance.

### 2) In-Stay Full Day (Day 2+ of multi-day stop)

- Must not mention arrival, check-in, or inbound drive from prior destination.
- Should focus on destination activities and local pacing.
- Must not recycle Day 1 transfer logistics language.

### 3) Departure Day

- For non-final destination in trip: closing guidance may mention onward drive prep.
- For final destination with return anchor: afternoon/evening should be reserved for return travel buffer.

### 4) Single-Day Stop

- If stop is transfer-based (previous destination exists), include one concise arrival/check-in guidance block.
- Do not over-allocate full-day activity density after a long inbound transfer.

## Entity Consistency Rules

Schedule text must remain consistent with published entities.

- If an attraction is filtered/rejected later in pipeline, schedule must not continue naming it.
- En-route stops should only appear in schedule where route context is valid (typically arrival leg, not in-stay days).
- If no named entity survives policy checks for a block, fall back to generic but truthful guidance.

## Constraint Rules

- Respect effective daily activity budget from destination override or global default.
- Respect transfer friction from `travel_time` when choosing same-day activity density.
- Use deterministic fallbacks when AI output is sparse: preserve period coverage (Morning/Afternoon/Evening) without inventing contradictory logistics.

## Normalization Priorities

When rules conflict, apply in this order:

1. Safety and truthfulness (no filtered/nonexistent entity references).
2. Lifecycle correctness (arrival vs in-stay vs departure).
3. Time/effort feasibility (transfer + activity budget).
4. Variety/non-repetition.
5. Style polish.

## Test Matrix Requirements

Minimum independent unit matrix should cover:

- arrival day transfer injection,
- single-day transfer check-in behavior,
- multi-day Day 2+ check-in suppression,
- departure-day return reservation,
- filtered-entity leakage suppression,
- budget-limited afternoon packing.

## Implementation Notes

- Keep AI schedule as draft text, then enforce policy transforms.
- Add a post-filter schedule reconciliation pass against final published entities.
- Prefer deterministic rewrite templates over ad-hoc string replacement.
