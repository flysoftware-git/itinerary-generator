# Schedule Normalization

## Purpose
Schedule normalization turns variable LLM output into a predictable structure:
`[{day_label, periods:[{period, summary}]}]`, while preserving useful detail and
enforcing minimum UX quality.

## Physical Reality Model: Destination-Day Use Cases (GH #16)

**Status: design update, 2026-08-16 — not yet implemented.** This section
responds directly to the project owner's review of real output against this
document:

> "I think the thing missing from Schedule normalization discussions is the
> underlying physical reality operating behind the scenes. There are checkin
> and checkout times per destination. There needs to be a common assumption
> about how single day and multi day trips transpire. The first destination
> always involves incoming, and nothing might be possible if you don't
> arrive until evening. Similarly, moving from the last destination to
> departure always involves driving time plus safety buffer (90 minutes
> before flight, for example). On other days, you will have arrived the
> night before, need to check out by the defined time, and can't check in
> until the allowed time. In between, there are time blocks, but there is
> also transportation lags, so the second day of a 3 day visit has lots of
> time available, but the first and third days do not. These simple
> assumptions have to be incorporated into the planning model... I have
> reviewed the Schedule Normalization design note, and results have
> definitely improved, but it doesn't seem to recognize the different use
> cases involved."

This corresponds to GH #16 ("PR-005: Possible Daily Schedule is not
realistic, route-aware, or time-budgeted", priority:P1, area:scheduling).
The rest of this document describes a system that reasons in terms of
*periods* (Morning/Afternoon/Evening) and *budgets* (hours per day). It does
not yet reason in terms of the six physically distinct day-types below, and
the sections after this one describe mechanisms that partially,
inconsistently, or not at all encode those distinctions. This section is the
enumeration the owner asked for; it is a design/gap analysis for a future
implementation pass, not a description of new code.

### Status legend
- **(a) Implemented** — matches this use case's physical constraint today.
- **(b) Partial** — a mechanism exists and produces plausible-looking text,
  but it is not actually deriving the constraint from real inputs (time
  math, verified drive duration, checkin/checkout), or it silently
  misapplies logic meant for a different case.
- **(c) Schema/data gap** — no input exists to model this at all. Needs a
  product decision (what field, what shape, whose responsibility to supply
  it) before any code changes.
- **(d) Open question** — the *behavior*, not just the data, needs the
  owner's decision. Do not silently pick a default.

### Case 1 — First destination, Day 1 (arrival day, no prior lodging)

**Inputs available today:**
- `trip.departure_datetime` — when the traveler leaves the origin
  (`manifest_parser.py:45-48`). Used in `ai_content.py:1743-1744` to extract
  an hour and bucket a travel period.
- `trip.departure` origin location, geocoded to `departure_lat`/`departure_lng`
  (`main.py:1848-1851`) — but only consumed for the route-overview map
  (`html_assembler.py:231-237`), never for a drive/flight-time estimate into
  destination 1.
- Destination `lodging.checkin_time` (schema: `manifest_parser.py:140`;
  populated in the sample manifest for every destination, e.g. `"4:00 PM"`).

**Inputs NOT available:**
- No arrival time/datetime at destination 1. `departure_datetime` is when
  the traveler *leaves home*, not when they land or reach the destination —
  there is no `arrival_datetime` (or duration) to convert one into the
  other.
- No computed drive/flight duration for the origin leg. The live-route
  estimator that computes every other leg's `drive_time`
  (`url_discovery.py:10415` `_update_route_distance_and_time`) is fed
  `origin_name`/`origin_lat`/`origin_lng` from `destinations[idx - 1]`
  (`url_discovery.py:1947-1953`); when `idx == 0` these are left empty, so
  the mechanism structurally never runs for the first leg — it isn't a bug
  in that estimator, the first leg is simply out of its scope by
  construction, even though the origin's lat/lng *are* geocoded elsewhere.
- Separately, a haversine driving estimate is the wrong model for this leg
  anyway in the common case where destination 1 is reached by air, not by
  car — what's actually needed is the traveler's own knowledge of when
  their flight/transport arrives, which is manifest input, not something to
  derive from geocoding.

**Current behavior:** `_inject_travel_realism` (`ai_content.py:1741-1783`)
buckets a travel period (Morning/Afternoon/Evening) purely from the *hour
the traveler leaves origin*, reserves that bucket, and — only if the
period immediately after is heuristically "heavy" — softens it to a generic
"meal break and short orientation stop" that mentions `checkin_time` as a
prose aside, not a bound on anything.

**Status: (b) partial.** Departure-hour bucketing is a reasonable proxy but
conflates "when I left" with "when I can start doing things." A flight
leaving at 8 AM lands mid-afternoon; the current heuristic would bucket that
as "Morning is the only reserved slot, Afternoon/Evening are free," which is
exactly the "nothing might be possible if you don't arrive until evening"
failure mode the owner described. `checkin_time` is present in the prompt
context and in the deterministic arrival phrase, but never used as a hard
constraint on the activity budget.

**Gap: (c).** No manifest field carries an expected arrival time (or a
transit-duration + mode) for the leg into destination 1.

**Open question (d):** should this be one new field (e.g.
`destinations[0].expected_arrival_time`, mirroring `checkin_time`'s shape),
or a more general `arrival_mode` (flight/drive) + duration pair that could
also serve intermediate destinations reached by non-driving legs? Flag for
the owner — do not invent a shape silently.

### Case 2 — Last destination, final day (departure to trip end)

**Inputs available today:**
- `trip.return_datetime` (`manifest_parser.py:53-56`) — surfaced verbatim as
  a label: `"Reserved for return travel to {return_label} around
  {return_time_label}"` (`ai_content.py:1846-1852`). No math is performed
  against it.
- `trip.return` (return location name/label only).

**Inputs NOT available:**
- No safety-buffer concept exists anywhere in the codebase. (Grepped for
  `buffer` — the only hits are prose phrases like "transfer/parking
  buffers" baked into generated text, never a numeric, configurable value.)
- No computed drive time for the *departure leg itself* (last destination →
  trip return point / airport). The live-route estimator computes each
  destination's *inbound* `drive_time`; there is no equivalent outbound
  computation for the trip's final leg.
- **`trip.return_datetime`'s semantics are themselves undefined.** The
  schema description ("Optional return date/time anchor... used for route
  overview labels and schedule feasibility guidance") doesn't say whether
  it means "flight departs at this time" (requiring buffer subtraction to
  get a real must-leave-by time) or "must already be back home by this
  time" (which would need drive time *and* buffer subtracted) or something
  the traveler already buffer-adjusted themselves. This ambiguity needs
  resolving before "safety buffer" can be implemented correctly, independent
  of adding the buffer concept itself.

**Current behavior:** `is_last_destination` (`next_destination` empty,
`ai_content.py:1481`) unconditionally overwrites the last day's Afternoon
*and* Evening with static "reserved for return travel" text
(`ai_content.py:1849-1858`), regardless of how much actual drive time the
return leg requires. A destination 20 minutes from the airport and one 5
hours away get identical treatment.

**Status: (b) partial.** The reservation exists and prevents the original
bug pattern (activities booked into a window travel logistics dominate),
but it's a blunt, non-computed block, not a "must leave by X" derived from
real distance + buffer the way the arrival-day discount (Reserved Travel
Windows / v2.1 budget section below) at least attempts for inbound legs.

**Gap: (c).** No safety-buffer field, no outbound-drive-time computation for
the terminal leg.

**Open question (d):** the owner's own example (90 minutes before a flight)
should **not** be silently hardcoded as a universal default — flag as a new
tunable, following this project's existing precedent
(`trip.default_daily_activity_hours`, `trip.default_day_start_time`): e.g.
`trip.default_departure_safety_buffer_minutes` with an optional
per-destination override, defaulting to *something* only after the owner
confirms a number and confirms it should be a flat default rather than
varying by transportation mode (a flight needs meaningfully more buffer than
walking to a car).

### Case 3 — Multi-day destination, arrival day

**Inputs available today:** same as Case 1's `checkin_time`, plus — because
this is *not* the trip's first destination — a genuinely computed inbound
`drive_time` for the leg from the previous destination (subject to the
pipeline-ordering caveat below).

**Current behavior:** `ai_content.py:1788-1815`. When `drive_minutes > 0`,
Morning is rewritten with a real depart/arrival time computed as
`effective_start_minutes + drive_minutes`, and the Afternoon activity budget
is explicitly discounted by `drive_minutes` before packing
(`effective_activity_budget_minutes - drive_minutes`). This is the most
physically-grounded mechanism in the whole file.

**Status: (a) implemented, for the "drive eats the budget" concept** — with
one real caveat and one decorative gap:
- **Caveat — pipeline ordering.** `getting_here.drive_time` at the point
  `_normalize_schedule` reads it is whatever the LLM itself put in its
  `getting_here` payload during content generation (Stage 3,
  `main.py:1929-1930`, `ai_gen.generate_all(trip)`), *not* the
  live-route-verified value. The verified value is computed later, in Stage
  5b (`url_discoverer.discover_all(trip)`, `main.py:2001`), which calls
  `_update_route_distance_and_time` (`url_discovery.py:10415`) — after
  schedule normalization has already run and already consumed
  `drive_time`. So despite the doc language above ("derived from the real
  route"), the number actually driving this discount today is the LLM's own
  guess, not a verified one. This is a genuine sequencing gap worth fixing
  independent of anything else in this section — either normalize schedule
  after route verification, or re-run the discount pass once verified
  `drive_time` lands.
- **checkin_time is still decorative here too** — the discount is purely
  duration-based (subtract drive minutes from a budget), never compared
  against an actual checkin clock time to check whether the traveler could
  even get into the room before doing something. In practice this rarely
  matters (arrival activities happen before checkin anyway, e.g. lunch, a
  short walk), but nothing enforces it.

### Case 4 — Multi-day destination, departure day (not the trip's last day)

**Inputs available today:** `next_destination` name only
(`ai_content.py:1859`, `1864`) — a string, not a duration. The onward drive
duration for this leg *does* eventually get computed (it's the *next*
destination's inbound `getting_here.drive_time`), but it is never threaded
back into the current destination's schedule normalization call — and per
the Case 3 caveat, wouldn't be verified yet even if it were, since it
belongs to a not-yet-processed destination.

**No `checkout_time` field exists anywhere** — confirmed by grep across
`generator/`, `manifest_parser.py`'s schema, and `trip_manifest.yaml`: zero
matches for `checkout_time`. Only `checkin_time` exists.

**Current behavior:** This is the case most likely to surprise the owner.
The "Day 2+ gets the full activity budget" loop
(`ai_content.py:1832-1842`, `for day_index, day in enumerate(days[1:],
start=2)`) iterates over **every** day after Day 1 uniformly — it does not
distinguish "this is a true middle day" from "this is this destination's
own last day, and the traveler needs to check out and drive onward." A
3-day intermediate stop's Day 3 gets the identical undiscounted
`effective_activity_budget_minutes` packing that Day 2 gets. Only
*afterward*, and only if `next_destination` is set, does a separate pass
(`ai_content.py:1859-1866`) overwrite the *last period* (Evening) of the
last day with generic "wrap key stops early... keep departure buffers"
text — Afternoon, already packed with a full-budget multi-activity plan by
the earlier loop, is untouched.

**Status: (b) partial, and this is the closest thing to a real bug in
scope for this section.** The middle-day mechanism (Case 5, which the owner
correctly identifies as the best-handled case) is unconditionally reused
for the departure day too, with no checkout-time bound and no onward-drive
discount mirroring what arrival days already get. The asymmetry — arrival
day is discounted by inbound drive time, departure day is not discounted by
outbound drive time despite the exact same physical logic applying — is a
concrete, fixable inconsistency once the two data gaps above (checkout
time, threaded onward-drive-duration) are resolved.

**Gap: (c).** `checkout_time` field; a way to pass the onward-leg drive
duration back to the *current* destination's normalization call (a plumbing
gap, not just a missing field — see interaction note re: pipeline ordering
above).

### Case 5 — Multi-day destination, middle day(s)

**Current behavior:** Same loop as Case 4
(`ai_content.py:1832-1842`) — full, undiscounted
`effective_activity_budget_minutes`, day-index-rotated attraction
selection so consecutive days don't greedily pick the same set.

**Status: (a) implemented, and correctly matches this use case** — this is
exactly the case the owner says "has lots of time available." No transit
friction applies to a true middle day, so the undiscounted full-budget
packing this loop performs is the *right* behavior here. The gap isn't in
this case's own logic; it's that the same loop is applied to Case 4 without
distinguishing the two (see above) — Case 5 itself needs no changes, only a
guard so its logic stops silently absorbing Case 4's days.

### Case 6 — Single-day-only destination

**Current behavior:** When a destination is both the trip's first and its
last (a one-stop trip, or more commonly a single-day-only destination that
happens to also be first or last in a multi-stop trip — see next
paragraph), both `is_first_destination` and `is_last_destination` blocks run
against the same single day. The first-destination block
(`ai_content.py:1741-1783`) sets a travel period and possibly the period
after it; the last-destination block (`ai_content.py:1844-1858`) then runs
**unconditionally** and overwrites that same day's Afternoon *and* Evening
with static "reserved for return travel" text — clobbering whatever the
arrival logic had just written, with no check for whether the two blocks
are describing the same physical day.

More generally: even when a single-day destination is a *middle* stop
(neither first nor last in the trip), there is no dedicated "single day"
code path at all today — `len(days) == 1` is handled
(`ai_content.py:1868-1888`) but only for a generic "not first destination,
no explicit checkin" fallback that adds one Afternoon "check in and settle
logistics" line. It doesn't reason about *both* an inbound leg and an
onward leg competing for the same day's hours simultaneously, which is
precisely the "tightest case" the owner flagged.

**Status: (b) partial for the middle-of-trip single-day case; the
first-and-last-simultaneously combination is a real defect** (not merely
"unmodeled" — the two blocks actively overwrite each other's output on the
same day with no collision guard). Worth a fix independent of the rest of
this section: at minimum, `is_last_destination` handling needs a guard for
`is_first_destination and is_last_destination and len(days) == 1` so it
doesn't unconditionally clobber arrival-day content.

**Gap: (c).** Same `checkin_time`/`checkout_time`/safety-buffer gaps as
Cases 1-4 apply here simultaneously, compounding rather than being new.

### Cross-cutting findings

- **Pipeline ordering (Cases 1, 3, 4).** Schedule normalization (Stage 3)
  runs before live route-distance verification (Stage 5b). Any use case
  that wants a *verified* drive/arrival time, not the LLM's self-reported
  guess, needs either a reordering or a second pass. This affects the
  credibility of the existing "arrival-day budget discount" claim in the
  v2.1 section below, not just the new cases in this section.
- **`checkin_time` is schema-real but behavior-decorative.** It exists in
  the manifest schema, is populated in the real sample manifest for every
  destination, and is threaded into both the LLM prompt context
  (`ai_content.py:802-818`) and the deterministic arrival phrase
  (`ai_content.py:1776-1777`) — but nowhere is it compared against a clock
  time to actually gate what's schedulable. It reads as implemented; it
  functions as a caption.
- **`checkout_time` does not exist at all.** Not a partial implementation —
  a genuine schema gap, needed for Cases 4 and 6.
- **No safety-buffer concept exists anywhere**, numeric or otherwise.
  Needed for Case 2 and (arguably) the departure side of Case 4 and 6.
- **`return_datetime`'s meaning is undefined** (flight time? must-be-home
  time? already buffer-adjusted?) — needs resolving before a buffer
  computation can be layered on top of it, independent of adding the
  buffer field itself.
- **Single-day collision bug** (Case 6, first-and-last) is the one item
  above that looks like a straightforward correctness fix rather than a
  design gap — flagging it here since it surfaced during this review, not
  because this task was scoped to fix it.

### Interaction with GH #68 (Multi-Site Destination Grouping)

`docs/design/multi-site-destination-grouping.md` describes grouped
destinations (`group_with`) sharing one physical lodging base across
several destination entries (e.g. Moab base, with Arches/Canyonlands as
day-trip entries). That design explicitly states day-count/schedule-budget
inference needs no change (§6: "Each grouped entry already declares its own
`dates` sub-range... the multi-site case falls out of the existing
single-destination logic for free"). The use-case model in this section
complicates that claim in one specific way, worth flagging rather than
resolving here:

- A grouped entry (e.g. Arches, `group_with: moab`) has **no lodging
  block of its own** unless explicitly overridden (§1 of that doc: "if
  omitted, the entry inherits the group base's lodging block wholesale").
  That means a grouped entry's own `checkin_time`/`checkout_time` (once the
  latter exists) are meaningless for *that entry* — the traveler doesn't
  check in or out of Arches, they check in/out of Moab once, on the base
  entry's dates. If Cases 1/3/4/6 above start gating scheduling on
  checkin/checkout, that gating needs to resolve to the **group base's**
  lodging times for a grouped entry, not the grouped entry's own (usually
  absent) lodging block — otherwise a grouped entry would either skip the
  gate entirely (silently wrong — treats every day as an unconstrained
  middle day, Case 5, even on the group's actual arrival/departure day) or
  error looking for a field that was never meant to be there.
- Conversely, a grouped entry's own "day" is a day-trip from an
  already-checked-in base, which is closer to Case 5 (middle-day, full
  budget) than to Cases 1/3/4/6 *regardless* of where it falls in the
  group's date range — the "arrival day is constrained" logic doesn't
  really apply to a day-trip that starts and ends at a base the traveler
  is already settled into, except for the ordinary out-and-back drive time
  to/from the park itself (which is a smaller, symmetric version of the
  Case 3/4 discount, not a checkin/checkout constraint).
- Not resolving here: which entry (base vs. child) owns the arrival/departure
  reservation windows once this model is implemented, and whether that
  needs a new signal (e.g. "this entry is grouped, treat as day-trip") fed
  into `_normalize_schedule`. Flagging as an interaction point for whoever
  picks up implementation on either issue, since GH #68 isn't merged yet
  and this section's model doesn't exist in code yet either — sequencing
  which lands first affects how much rework the second one needs.

### Schema gaps requiring a product decision (summary)

| Gap | Exists today? | Needed for | Notes |
|---|---|---|---|
| `lodging.checkin_time` | Yes (schema + populated in sample manifest) | Cases 1, 3, 6 | Present but only used as prose today, not a hard constraint |
| `lodging.checkout_time` | **No** | Cases 4, 6 | Zero references anywhere in the codebase |
| Destination-1 arrival time/duration | **No** | Case 1 | No field, and the live-route estimator structurally excludes the first leg |
| Terminal-leg (return) drive duration | **No** (only a static label from `trip.return_datetime`) | Case 2 | No computed value, unlike every other leg |
| Safety buffer (numeric, configurable) | **No** | Case 2 (and arguably 4, 6) | Owner's own example value (90 min) explicitly not to be hardcoded without confirmation |
| `trip.return_datetime` semantics | Ambiguous | Case 2 | Flight time vs. must-be-home time vs. already-buffered — undefined today |
| Onward-drive duration threaded to current destination | **No** (data exists one destination later, not passed back) | Case 4 | Plumbing gap, not a schema gap |
| Grouped-entry lodging resolution (base vs. child) | N/A (GH #68 not merged) | Cases 1/3/4/6 × GH #68 | See interaction section above |

### Open questions for the owner

1. Shape of a destination-1 arrival signal: a single `expected_arrival_time`
   field, or a `arrival_mode` (flight/drive) + duration pair? (Case 1)
2. Confirm (or replace) 90 minutes as the default departure safety buffer,
   and confirm whether it should be a flat trip-wide default
   (`trip.default_departure_safety_buffer_minutes`, mirroring
   `default_daily_activity_hours`) or vary by transportation mode. (Case 2)
3. What does `trip.return_datetime` mean today, precisely — is it a
   deadline to subtract buffer from, or already a "leave the last
   destination by" time? (Case 2)
4. Should `checkout_time` be required whenever `checkin_time` is present
   (matched pair), or independently optional? (Case 4, 6)
5. For GH #68 grouped entries, should the group **base** entry alone own
   arrival/departure reservation windows, with every grouped child always
   treated as a Case-5-style middle day regardless of its position in the
   group's date range? (Interaction section above)
6. Should the Case 6 single-day-first-and-last collision (blocks
   overwriting each other) be fixed as a standalone bug ahead of the rest
   of this section's implementation, given it's a straightforward guard
   rather than a design decision?

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

See "Physical Reality Model: Destination-Day Use Cases (GH #16)" above for
the authoritative breakdown of what this section's mechanisms do and don't
cover, per use case, with an (a)/(b)/(c)/(d) status per case. The summary
below undersells the current implementation slightly: the first-destination
rule does not always reserve Morning specifically — it dynamically picks
Morning/Afternoon/Evening based on the hour extracted from
`trip.departure_datetime` (Case 1 above) — but it also does not know an
actual arrival time, and the final-destination rule is a static two-period
block with no drive-time or safety-buffer computation behind it (Case 2
above).

First destination rule:
- Day 1's travel period (Morning by default, or Afternoon/Evening if
  `trip.departure_datetime`'s hour is late) is reserved for travel from
  trip origin.
- Trigger condition: `previous_destination` is empty or `none`.

Final destination rule:
- Last day Afternoon and Evening are reserved for return travel to base.
- Trigger condition: `next_destination` is empty.
- Unconditional and duration-blind: applies the same two-period reservation
  regardless of actual drive distance to the return point (Case 2, GH #16).

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
- `generator/manifest_parser.py` (destination `lodging.checkin_time` schema; no `checkout_time` field exists yet — see GH #16 use-case section above)
- `generator/url_discovery.py` (`_update_route_distance_and_time`, live drive-time verification — runs after schedule normalization, see pipeline-ordering note above)
- `prompts/destination_content.txt`
- `docs/design/multi-site-destination-grouping.md` (GH #68 — see interaction notes above)
