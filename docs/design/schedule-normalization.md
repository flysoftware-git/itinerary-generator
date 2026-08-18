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
with static "reserved for return travel" text (`ai_content.py:1849-1858`),
regardless of how much actual drive time the return leg requires. A
destination 20 minutes from the airport and one 5 hours away get identical
treatment.

**Status: (b) partial.** The reservation exists and prevents the original
bug pattern (activities booked into a window travel logistics dominate),
but it's a blunt, non-computed block, not a "must leave by X" derived from
real distance + buffer the way the arrival-day discount (Reserved Travel
Windows / v2.1 budget section below) at least attempts for inbound legs.

**Update, 2026-08-17 — Evening duplication fixed, blunt-block gap above
still open.** The owner's review also caught a second symptom of the
unconditional-block behavior: "Last day still repeats afternoon and
evening, once headed to airport in the afternoon, there doesn't need to be
an evening." The code used to set near-identical "reserved for return
travel" text on *both* Afternoon and Evening — nonsensical once the
traveler has actually left for the airport that afternoon, since there's no
one at the destination left to have an evening. Fixed: Evening is now
cleared (`_set_period_summary(last, "Evening", "")`) instead of getting a
second copy of the return-travel note; `html_assembler.py`'s
`_build_schedule` already skips periods with an empty summary, so the slot
is simply omitted from the rendered card. This is a narrow language/
rendering fix, not a resolution of the gap above — the block is still
"Afternoon is universally reserved" rather than derived from the traveler's
actual return time, so a destination where departure genuinely happens in
the *evening* (not the afternoon) is not yet distinguished; that requires
the same return-time-bucketing the owner's Case 1 (arrival) example already
gets, applied symmetrically here, which remains gated on the open questions
above (safety-buffer field, return_datetime semantics).

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

**Update, 2026-08-18 — implausible "morning" activity claim on arrival day
fixed, real SW2026-dipstick69 regression.** `morning_already_arrival_aware`
(the guard that decides whether to overwrite the AI's own Morning text with
the deterministic "Travel from X...arrival around Y" phrase) only checked
for the *presence* of arrival language (arrive/drive/route/etc.) in the
AI's text, then trusted that text completely once found — it never checked
whether the same sentence also claims a specific, time-sensitive activity
the actual computed arrival time contradicts. Real observed text, Bryce
Canyon National Park Day 1 (arrival day from Zion, 135 min / 2 hr 15 min
drive): "Arrive at Bryce Canyon National Park and check in to your
lodging. After settling in, head to Sunrise Point for morning views of the
canyon." Computed arrival (10:00 AM default day start + 135 min drive) is
12:15 PM — already past noon, so "morning views" immediately after
arriving and settling in is not physically possible; project owner's
review flagged exactly this class of error (GH #16, physical-reality
scheduling model). Fixed: when `morning_already_arrival_aware` is true
*and* the text also names "morning" combined with an activity verb (head
to/visit/watch/enjoy/hike/tour/see/views, etc.) *and* the computed arrival
time falls past a late-morning cutoff (11:00 AM), only the false
time-of-day phrase is stripped (e.g. "morning views" → "views") and an
honest arrival-time note is appended — the real attraction mention and the
AI's own arrival/check-in narration are left intact, mirroring the
existing `_is_heavy_activity_block` correction pattern a few lines above
rather than reverting to generic filler. Verified by
`tests/test_ai_content_normalization.py::test_inject_travel_realism_corrects_implausible_morning_activity_claim_on_arrival_day`.
This is a narrow language-correctness fix for one specific phrasing
pattern, not a resolution of the broader Case 1/3 gaps above (pipeline
ordering, `checkin_time` still decorative) — those remain open.

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

**Update, 2026-08-17 — framing bug fixed, budget/checkout gap above still
open.** The project owner reviewed a real run and found two symptoms of the
asymmetry described above:

1. The deterministic last-evening override text itself ("Wrap key stops
   early and prepare for onward drive to {next}; skip new sunset
   commitments and keep departure buffers.") read as a multi-hour
   after-dinner drive, contradicting the no-multi-hour-drives-after-dinner
   framing already used elsewhere in this function. Fixed: the override now
   reads "Enjoy a relaxed, local evening; the drive to {next} happens the
   next morning, not tonight." — reusing the existing relaxed-evening voice
   (see the closes-early-venue fallback a few paragraphs below) rather than
   inventing new phrasing, and stating explicitly that the drive is a
   *tomorrow-morning* event (it's next_destination's own Day 1 arrival leg,
   not something that happens tonight).
2. More severe: since the LLM's own schedule text for Morning/Afternoon/
   every-day-but-the-last-Evening is untouched by normalization, a 3-day
   intermediate stop could have Day 1 *and* Day 2 (not just the actual
   departure day) mention departing for `next_destination` — the owner's
   exact report was "the scheduler is also suggesting departing Capitol
   Reef each of the 3 days for Moab." Fixed with a defensive scrub pass in
   `_inject_travel_realism` (`ai_content.py`, after the last-evening
   override): every period except the one intentionally carrying the
   departure note is checked for onward-drive phrasing or a literal mention
   of `next_destination`'s name, and offending sentences are stripped
   (falling back to a local, non-generic-filler sentence per period type if
   nothing else survives). The destination-content prompt
   (`prompts/destination_content.txt`) was also fixed: it had a malformed,
   unscoped fragment duplicating the old "onward drive" instruction outside
   any period's JSON field (a likely contributor to the model applying it
   inconsistently), and now explicitly instructs the model never to
   reference departure/onward travel in `possible_daily_schedule` on any
   day, since the app owns that framing deterministically.

Neither fix touches the underlying data gaps above (`checkout_time`, a
threaded onward-drive duration, or discounting the departure day's activity
budget the way arrival days already are) — those remain open per the
"Gap: (c)" note. This update is scoped to *language correctness*: what the
schedule text says about departure, not *when* departure realistically
allows activities to end.

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

**Grouped-child "What to Know" boilerplate (2026-08-18 fix, adjacent to
schedule generation but not the schedule itself).** Not the
`possible_daily_schedule` mechanism this document otherwise covers, but
investigated and fixed in the same pass since it's the same GH #68
grouped-destination content-quality concern: a grouped child's `what_to_know`
card (`_normalize_what_to_know`, `generator/ai_content.py`) was
independently AI-generated per destination exactly like a base entry's,
with no deferral at all. Real published-run evidence: Moab, Arches
National Park, and Canyonlands National Park each independently produced
the same six categories (local customs / best times of day /
transportation quirks / safety / crowd patterns / local etiquette) with
substantively identical (not verbatim-identical, so the pre-existing
exact-string `_deduplicate_cross_destination_what_to_know` never caught
it) generic seasonal-desert-park advice — e.g. Arches and Canyonlands
both independently said "Early morning and late afternoon provide the
best light/lighting for photography." Project owner: "The 'what to know'
about Day Trips does not need the repetitive [generic boilerplate]...
just offer unique comments to that locality."

Unlike restaurants/cultural_events (fully deferred to the group base via
`multi_site_grouping.category_deferred_to_base` — an entire category
skipped at generation time), full deferral isn't right for `what_to_know`:
a day trip can have genuinely distinct practical notes (permits, road
conditions, facilities) the base's own card wouldn't cover. Fixed instead
at the field level: for a grouped child (`is_grouped(dest)`),
`local_customs`/`best_times_of_day`/`safety_considerations`/
`crowd_patterns`/`local_etiquette` are left empty rather than filled with
yet another boilerplate fallback sentence when the AI's own text is
empty or generic; `summary` and `transportation_quirks` are kept, since
real data showed those are the two categories most likely to carry
genuinely site-specific content. `html_assembler.py`'s `_build_intro_note`
already skips empty fields when rendering the card, so no renderer change
was needed. Verified by
`tests/test_ai_content_normalization.py::test_normalize_what_to_know_suppresses_generic_boilerplate_for_grouped_day_trip_child`
and
`::test_normalize_what_to_know_keeps_full_boilerplate_for_ungrouped_base_destination`.

### Relationship to Side Trips (GH #3)

Added 2026-08-16, following a design discussion with the project owner
that also produced `docs/design/side-trip-exploration.md` and §8 of
`docs/design/multi-site-destination-grouping.md`. Full discussion lives
in that new doc; the piece specific to scheduling is captured here.

GH #3's side trips are explicitly **not** scheduled — no `dates`, no day
assignment, rendered as a static suggestion card. That means none of the
Case 1-6 machinery above applies to them directly; they never enter
`_normalize_schedule` at all under the current spec. But the owner
identified a real gap this section doesn't cover for either feature: for
a long stay (7+ days at one base), the exact day a side-trip option gets
visited mostly doesn't matter, but which options get **combined into one
outing** matters a lot — two options that are each ~45 minutes out but in
the same direction from base should be recommended together, not as two
separate one-hour-each-way trips on different days. Neither this design
nor GH #3's original spec has a mechanism for that today (see
`side-trip-exploration.md` §3 for the full gap analysis and three
candidate approaches — not resolved here).

This reinforces the hub-and-spoke framing above: a `group_with` sibling
is dated but order-flexible relative to other siblings; a side trip is
undated and order-irrelevant, but *pairing*-relevant once (if) clustering
is implemented. Three distinct scheduling looseness levels, not two.

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
| Side-trip option-to-option geographic relationship | **No** (GH #3 not implemented; spec only carries base→option distance) | Clustering (GH #3 §3) | See "Relationship to Side Trips" above and `side-trip-exploration.md` §3 |

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
7. Which side-trip clustering approach (see `side-trip-exploration.md`
   §3 — LLM-suggested groups, lightweight geocode-and-compute, or leave
   pairing to the traveler) matches the intended product experience, and
   should it block GH #3's initial implementation or land as a follow-up
   once the basic suggestion card ships?

## Entry Point
`AIContentGenerator._normalize_schedule(...)` in `generator/ai_content.py`.

**A second, separate normalization pass exists outside this file:**
`generator/entity_registry.py:reconcile_schedule_from_registry`, called from
`main.py:_reconcile_trip_via_registry` *after* `_normalize_schedule` has
already run and after the entity registry's final accept/reject state is
known (URL validation, dedup, cross-destination reassignment, threshold
demotion, etc. — none of which is knowable at `_normalize_schedule` time,
since it runs per-destination before those pipeline stages). If a schedule
period names an attraction/restaurant/stop that gets rejected later, this
pass rewrites that period's text. Until 2026-08-17 it always replaced the
*entire* period with a fully generic sentence (e.g. "Focus on currently
eligible nearby highlights and realistic transition time between stops.")
regardless of whether another real, still-accepted attraction for that same
destination was available to name instead — this was the actual source of
the project owner's "generic filler instead of concrete attraction
allocation" complaint (not an LLM prompt-echo; the phrase is a Python
literal in `_SCHEDULE_FALLBACK_BY_PERIOD`, never present in any
`ai_content.py` prompt template). Fixed: the pass now looks for an
unblocked attraction from the destination's own (already-reconciled)
`top_attractions` and re-anchors the period's text to name it concretely;
the fully generic fallback is now reserved for the case where truly nothing
real is left to substitute (verified by
`tests/test_entity_registry.py::test_reconcile_schedule_from_registry_afternoon_names_a_real_substitute_not_generic_filler`
and the adjacent threshold-demoted-mention test).

**Small-attraction-pool follow-up (2026-08-17):** the initial version above
preferred a candidate "not yet mentioned elsewhere" in the schedule, and
once a candidate was mentioned anywhere it was permanently excluded from
substitution. Real SW2026-dipstick67 output for Bryce Canyon National Park
showed this was too strict: a 3-day stay with only three real accepted
attractions had each of them legitimately named once in an untouched
period, which exhausted the candidate pool before the first blocked period
was even reached — every one of the three blocked periods fell through to
the fully generic filler despite three real attractions existing to name.
Fixed: candidate selection is now round-robin by least-used count rather
than a one-shot "used/unused" flag — a candidate can be reused across
different days once every candidate has had a turn, matching how the rest
of the schedule already tolerates a highlight (e.g. a sunset viewpoint)
being named on more than one day of a stay. The only hard exclusion left is
within the same day, so one day's Morning and Afternoon are never
substituted with the same attraction. Verified by
`tests/test_entity_registry.py::test_reconcile_schedule_from_registry_reuses_real_attractions_when_pool_is_small`.

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
`_dedupe_schedule_day_content` flags periods whose summary exactly
duplicates an earlier day's same-content summary. Runs before
`_inject_travel_realism`'s attraction-name rotation, so it's the earliest
point that can detect duplication in generic fallback text (e.g.
`_ensure_day_period_coverage`'s filler string) that carries no canonical
attraction/restaurant name for that later rotation pass to grab onto.

Detection is per-period against the full running history of every prior
period's summary (fixed -- previously only triggered when *every* period in
a day was already a duplicate, so a day with 2 of 3 periods repeated, but
one genuinely new, triggered nothing at all).

**Leaked-instruction fix (2026-08-18, real SW2026-dipstick68 regression):**
this function used to "fix" a detected duplicate itself, by appending a
literal internal instruction sentence directly onto the rendered summary --
e.g. `period_variation_suffix["Evening"]` = "Choose a different sunset zone
or dining pocket than earlier nights." The docstring described this as a
stopgap: only meant to flag the period for `_inject_travel_realism`'s later
rotation pass to replace with real, varied content, never meant to survive
as visible prose. In practice nothing downstream ever consumed that flag --
`_inject_travel_realism`'s rotation passes run unconditionally on every
period regardless of whether dedup flagged it -- so whenever rotation
didn't happen to change that specific period (e.g. only one real attraction
existed to rotate the pre-dinner clause to), the literal instruction
sentence reached final rendered output verbatim. Real observed text, Bryce
Canyon National Park Day 2 Evening: "Visit Bryce Point for sunset views,
then enjoy dinner at Bryce Canyon Pines Restaurant. Choose a different
sunset zone or dining pocket than earlier nights." -- project owner: "the
first sentence duplicates the prior evening, the second is a silly thing to
tell users."

Fixed unconditionally: `_dedupe_schedule_day_content` never mutates
`summary` at all now. A detected duplicate only sets a private,
non-rendered `period["_dedupe_needs_variation"] = True` marker.
`_inject_travel_realism` strips this marker from every period right before
returning (belt-and-suspenders -- the marker isn't actually consumed by any
rotation logic, since rotation already runs unconditionally regardless of
it; the strip exists purely so a literal internal marker can never leak
into rendered/serialized output even if some future caller forwards the raw
period dict instead of just `period`/`summary`). If a period is still an
exact duplicate after rotation has had its chance -- genuinely only one
real attraction and one real restaurant exist for that destination, so
nothing is left to vary -- the duplicate is left as plain, undecorated
text: an honest limitation rather than a forced, fragile rewrite. Verified
by
`tests/test_ai_content_normalization.py::test_normalize_schedule_dipstick68_leaked_instruction_never_reaches_rendered_evening_text`
(true dead end: 1 attraction, 1 restaurant -- leaked text absent, duplicate
honestly left in place) and
`::test_normalize_schedule_dipstick68_evening_duplicate_resolves_via_restaurant_rotation_when_possible`
(a second real restaurant exists -- the pre-existing `_rotate_restaurant_summary`
rotation, described below, already resolves the duplicate on its own) and
`tests/test_schedule_policy_matrix.py::test_dedupe_schedule_day_content_flags_partial_day_duplication_without_mutating_text`.

Rationalization requirement:
- For multi-day destinations, each day must contain at least one substantive
	differentiator from prior days (distinct area, activity focus, or transfer duty),
	*when real distinguishing data (a second attraction, a second restaurant, etc.)
	exists for that destination to provide one* -- otherwise the current
	normalization pipeline has nothing left to vary and honestly leaves the
	duplicate rather than injecting cosmetic non-content.
- Cosmetic wording changes alone do not satisfy differentiation quality.

**Attraction-focus rotation (day-level allocation pass, in
`_inject_travel_realism`):** separately from the exact-text dedup above,
each day's Morning and Afternoon period gets its named attraction rotated
via `_pick_non_repeating_focus`/`_replace_first_attraction_mention` so a
multi-day stay doesn't name the same highlight in the same period slot two
days running -- this is a text substitution (swap which known attraction
name appears in the sentence), not a rewrite of the surrounding prose.

Evening was excluded from this rotation until 2026-08-17: only the dinner
restaurant half of the Evening sentence rotated
(`_rotate_restaurant_summary`), while the pre-dinner activity clause (e.g.
"Enjoy a sunset from X") stayed fixed to whatever attraction the source
schedule happened to name. Real SW2026-dipstick67 output showed this as
near-duplicate (not exact-duplicate) Evening text across a multi-day Bryce
Canyon stay: "Enjoy a sunset from Sunrise Point. Afterward, have dinner at
Bryce Canyon Lodge Restaurant." on Day 1 vs "...dinner at The Pizza Place."
on Day 2 -- different restaurant, same attraction, so the exact-match dedup
above never caught it. Fixed: Evening now gets the same
`_pick_non_repeating_focus` rotation as Morning/Afternoon, offset so all
three periods of a day prefer distinct attractions. Verified by
`tests/test_ai_content_normalization.py::test_inject_travel_realism_rotates_evening_focus_across_a_multi_day_stay`.

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
- Last day evening at a transfer destination (a next destination exists) gets
	a relaxed, local framing that explicitly states the onward drive happens
	the *next morning* — never phrasing that reads as a same-night drive
	(2026-08-17 fix; see Case 4 in the Physical Reality Model section above).
- Earlier days of that same multi-day transfer destination are scrubbed of
	any premature onward-drive/next-destination mentions (2026-08-17 fix,
	same section).
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
- **Cross-day dedup guard** (fixed -- real dipstick62 Moab output showed the
	rotation above wasn't sufficient on its own: "Moab Giants Dinosaur Park"
	still got packed into more than one day's block, since start-offset
	rotation only changes which attraction is considered *first*, not
	whether an already-used one is excluded outright). A single
	`used_multi_activity_names` set is now threaded through every packing
	call for one destination's `_inject_travel_realism` invocation (the
	arrival-day call and every Day 2+ call): once an attraction is packed
	into any day's block, it's removed from consideration for every later
	day of that same multi-day stay. If too few not-yet-used attractions
	remain to build a real block (fewer than two), that day's Afternoon is
	left as-is rather than forcing a repeat.
- **Cross-day dedup guard, whole-schedule follow-up (fixed 2026-08-18,
	real SW2026-dipstick69 regression).** The guard above only closed the
	gap for names the packer picked *itself* -- `used_multi_activity_names`
	only ever gained an entry via the `used_multi_activity_names.update(...)`
	call inside `_build_multi_activity_afternoon_summary`. A name mentioned
	by raw AI-authored prose for a period the packer never touches (most
	commonly Evening -- Morning and Evening are deliberately not packed, see
	below) was invisible to it. Real observed text, Bryce Canyon National
	Park: Day 1 Evening read "Watch the sunset from Natural Bridge,
	experiencing the changing colors of the canyon...", then Day 2 Afternoon
	read "Consider one or more of the following, within about 1h 30m:
	Natural Bridge (30m), Inspiration Point (30m), Bryce Point (30m)..." --
	the same attraction named twice on different days of the same stay.
	Fixed with `_register_attraction_mentions`, called for Day 1 before the
	Day 2+ packing loop starts and again for each day right after it is
	packed: it scans every period's summary text (not just the ones the
	packer itself set) for any known attraction name and adds matches into
	the same shared `used_multi_activity_names` set, so a name spoken for
	anywhere on an earlier day -- packed or raw prose -- is excluded from
	every later day's pack. Verified by
	`tests/test_ai_content_normalization.py::test_inject_travel_realism_dipstick69_evening_attraction_not_repeated_in_later_day_afternoon_pack`.
- **Cross-day dedup guard, arrival-clone Morning scrub follow-up (fixed
	2026-08-18, real Moab regression).** The two fixes above closed the gap
	for the Afternoon packer's own picks and for raw AI-authored prose in
	ANY period -- but a THIRD, independent code path still bypassed
	`used_multi_activity_names` entirely: the "Day 2+ Morning was cloned
	from Day 1 arrival/check-in text" scrub (`_inject_travel_realism`, the
	block starting "Expanded multi-day schedules can accidentally clone Day
	1 arrival/check-in text into later mornings") -- the source of the
	literal `"Start with {name}, then pivot to a different nearby area
	before midday crowds."` template. Real published-run Moab output: Day 1
	Afternoon named "Moab Giants Dinosaur Park" and Day 3 Morning
	independently read "Start with Moab Giants Dinosaur Park, then pivot to
	a different nearby area before midday crowds." -- the same attraction,
	because this scrub picked its focus via bare day-index rotation
	(`_day_focus_name`) with zero awareness of `used_multi_activity_names`.
	Fixed: this scrub (and its Afternoon/other-period siblings in the same
	block) now calls `_day_focus_name_excluding_used`, which skips any name
	already in `used_multi_activity_names` and registers whatever it picks
	back into that same set -- the actual selection logic is a new, directly
	unit-testable static method, `AIContentGenerator._pick_unused_focus_name`,
	promoted out of the closure specifically so it can be verified without
	depending on the rest of `_inject_travel_realism`. Verified by
	`tests/test_ai_content_normalization.py::test_pick_unused_focus_name_skips_names_already_registered_as_used`
	and
	`::test_pick_unused_focus_name_falls_back_to_repeat_when_pool_exhausted`.

	**Known residual gap, investigated but not closed here.** A separate,
	later pass in the same function -- the day-level Morning/Afternoon/
	Evening rotation (`_pick_non_repeating_focus`, driven by its own short
	`recent_focuses` lookback, not `used_multi_activity_names`) -- runs
	unconditionally over every period afterward and can still independently
	re-derive a Morning period's attraction name, overriding what the scrub
	above just set. Verified empirically (not just by inspection): sweeping
	~120 synthetic multi-day/multi-attraction configurations through both
	the pre-fix and post-fix code found ZERO cases where the scrub fix
	alone changed the final rendered Morning text -- the later rotation
	pass's own pick wins every time a period's text names a known
	attraction, which the scrub always leaves it doing. Two attempts to
	also make that later pass respect `used_multi_activity_names` were
	tried and reverted: an outright second disqualifier (on top of
	`recent_focuses`) over-constrained the small-attraction-pool case and
	broke
	`test_inject_travel_realism_rotates_focus_to_reduce_adjacent_duplicates`;
	a softer "prefer an unused candidate among those already eligible"
	tie-break avoided that specific regression (and did produce real,
	verified end-to-end improvements in several swept configurations) but
	introduced a DIFFERENT regression in two other synthetic small-pool,
	many-days configurations. Given the demonstrated regression risk to
	real, existing, intentional round-robin/reuse test coverage, the
	narrower fix (the scrub's own pick) was kept rather than the broader,
	imperfect one. This is a genuine architectural overlap between multiple
	independent focus-rotation mechanisms in `_inject_travel_realism`, not
	something to silently claim as fully solved -- flagging for whoever
	picks this up next, alongside the "Known gap" note below about
	cross-destination schedule-text dedup.
- **One major destination per block guard** (fixed -- same dipstick62 report:
	"Moab Giants Dinosaur Park (1h 30m), Canyonlands National Park (1h 30m),
	Arches National Park (1h 30m)" packed into a single time block, ignoring
	that Canyonlands and Arches are each a separate multi-mile drive from
	town and from each other). No real inter-attraction distance matrix
	exists in this codebase (see below), so this is a name-pattern heuristic,
	not a distance check: an attraction name matching `National Park`,
	`National Monument`, `National Recreation Area`, `National Forest`, or
	`State Park` is treated as a distinct, genuinely off-site destination.
	At most one such name is ever packed into the same block, regardless of
	whether the raw time budget would technically fit more than one --
	cheap to check, and directionally correct for the common "in-town spot
	plus two separate parks" shape this bug report described. It does not
	distinguish, e.g., two attractions inside the *same* park (that's still
	budget-only, correctly so) or catch a non-"National/State ___"-named
	destination that's actually just as far away.
- Deliberately not extended: Morning and Evening periods (Morning is usually
	already spoken for by transit/logistics text; Evening is anchored to a
	single dinner slot via a separate rotation mechanism, not a multi-activity
	block), and the trip's first destination's arrival day (no comparable
	drive-duration signal exists for travel from trip origin). True
	inter-activity travel-time modeling (distance *between* attractions, not
	just total daily budget, and not just the name-pattern proxy above) would
	need geocoded attraction positions and a real distance matrix between
	them -- a separate, larger effort, not covered here. The "one major
	destination per block" guard above is a deliberately narrow, cheap
	partial mitigation, not a substitute for that.

## Evening Duration Cross-Check

Before this fix, nothing in schedule generation compared a candidate
attraction's own stated `duration` against the time-of-day period it was
being slotted into. The Afternoon multi-activity packer
(`_build_multi_activity_afternoon_summary`) implicitly filters by
duration via its own budget math (`if duration_minutes > remaining:
continue`), but Evening picks -- via the day-level focus rotation
(`_pick_non_repeating_focus`) or untouched raw AI prose -- had no such
check at all, and Morning/Afternoon/Evening's raw AI-authored text was
never cross-checked against duration either. Project owner: "Are these
estimates being really factored in?" Real motivating pattern (this
specific string may not reproduce identically run to run, but the
underlying gap is real): a previous run's Evening period suggested "The
Narrows" -- a real Zion hike whose own duration badge elsewhere on the
same real published page reads "4-8 hrs round-trip" -- physically not
something to start after dinner.

**Fixed (2026-08-18)** by extending the pre-existing closes-early-venue
mechanism (`_is_evening_unsuitable_venue` -- strips the specific sentence
naming a museum/discovery-site/visitor-center from an Evening summary and
falls back to a relaxed-evening sentence if nothing else survives) with a
parallel duration check, `_is_evening_unsuitable_duration`, rather than
building a second, separate Evening-scrubbing pass:
- `_parse_duration_minutes` (promoted to a static method so it's directly
  testable and shared, not duplicated) parses an attraction's `duration`
  field. Grounded against real `badge-duration` strings observed in
  production output rather than one assumed format: `"1-2 hours"`,
  `"4-8 hrs round-trip"`, `"1.5-2 hrs round-trip"`, `"30 min"`, `"1 hr"` --
  both hyphen and en-dash range separators, both `hr(s)`/`hour(s)` and
  `m`/`min(s)`/`minute(s)` unit spellings, a bare single value, and
  trailing free text after the unit are all handled. A range is averaged
  to its midpoint (e.g. `"4-8 hrs"` -> 360 minutes).
- `_EVENING_MAX_ACTIVITY_MINUTES = 180` (3 hours) is the cutoff for "still
  fine to start in the evening" (a sunset viewpoint, a short walk,
  dinner-adjacent stroll) versus a genuine multi-hour undertaking. Missing
  or unparseable duration data (`_parse_duration_minutes` returns `0`) is
  never treated as "too long" -- absence of data must never gate content.
- An attraction failing either the venue check OR the duration check is
  added to the same `unsuitable_evening_names` list the existing
  strip-the-sentence/fall-back-to-relaxed-evening mechanism already
  consumes, so a long-duration item is excluded from Evening candidacy
  specifically while remaining fully eligible for Morning/Afternoon,
  where a multi-hour commitment is realistic (Morning is the "Deliberately
  not extended" period noted above for the *packer*, but is untouched by
  this Evening-only check).

Verified by
`tests/test_ai_content_normalization.py::test_parse_duration_minutes_handles_real_badge_formats`,
`::test_is_evening_unsuitable_duration_flags_multi_hour_hikes_only`, and
`::test_inject_travel_realism_strips_multi_hour_hike_mention_from_evening_schedule`.

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
- Last day Afternoon is reserved for return travel to base; Evening is
  cleared (not rendered) rather than repeating a near-duplicate note, since
  the traveler has already left by the time Evening would occur (2026-08-17
  fix — see Case 2 above).
- Trigger condition: `next_destination` is empty.
- Unconditional and duration-blind: applies the same reservation regardless
  of actual drive distance to the return point (Case 2, GH #16).

Intermediate destinations:
- Final evening at a transfer destination stays local/relaxed and explicitly
  states the onward drive happens the *next* morning, not that night — never
  reworded as an onward-drive-prep block that could be read as a same-night
  drive (2026-08-17 fix — see Case 4 above).
- Earlier days of a multi-day transfer destination (not the actual departure
  day) are scrubbed of any premature onward-drive/next-destination mentions
  the LLM's own text may have introduced (2026-08-17 fix — see Case 4
  above).

**GH #68 grouping-aware `previous_destination`/`next_destination` (2026-08-18
fix):** `previous_destination`/`next_destination` feed both of the rules
above, and until this fix were resolved purely from adjacent-entry list
position (`prev_names = ["none"] + [d["name"] for d in destinations[:-1]]`,
`next_names = [d["name"] for d in destinations[1:]] + [""]`) with zero
awareness of GH #68 `group_with` grouped children (day trips from a shared
physical base, not real relocation stops). Real regression:
`Sandbox/sw_manifest.yaml` lists Moab, then Arches National Park
(`group_with: moab`), then Canyonlands National Park (`group_with: moab`),
then Telluride. Moab's own last evening got `next_destination="Arches
National Park"` (the literal next list entry), producing "Enjoy a relaxed,
local evening; the drive to Arches National Park happens the next morning,
not tonight." — wrong on two counts: Arches was already visited as a day
trip that stay, and the real next-morning drive is to Telluride. Project
owner: "The algorithm for the schedule does not understand the notion of
day trips... The scheduler hasn't incorporated the idea of a day trip into
its scheduling." Canyonlands's own `previous_destination` had the mirror
problem: resolved to "Arches National Park" (the literal prior list entry),
implying a direct Arches-to-Canyonlands drive that never happens -- both
are day trips FROM the same Moab lodging.

Fixed in `generate_destination_content` via a new
`_resolve_grouping_aware_prev_next_names` helper (`generator/ai_content.py`),
using `generator/multi_site_grouping.py`'s `group_base_id` (the same helper
`url_discovery.py`/`manifest_parser.py`/`html_assembler.py` already share
for GH #68 resolution, rather than a fifth independent implementation):
- Previous-side resolution mirrors `url_discovery.py`'s established
  `last_physical_base` pattern (used there for per-destination "getting
  here" origin/distance resolution): a grouped entry's previous destination
  is always its group base; an ungrouped entry's previous destination is
  the most recent *other* ungrouped destination, never a grouped sibling of
  the immediately preceding cluster.
- Next-side resolution is the forward analog (no prior precedent existed
  for this direction): for each destination, scan forward past every
  subsequent entry that shares its "cluster" (its own `group_with` base id
  if grouped, otherwise its own id) and return the first entry in a
  different cluster. Moab, Arches, and Canyonlands all correctly resolve to
  the same real next destination (Telluride) regardless of how many
  day-trip siblings sit between them in the flat list.

This only changes *which* name reaches `_inject_travel_realism` as
`next_destination`/`previous_destination` -- the existing day-level
scrub-vs-last-day machinery documented above (earlier days scrubbed of
onward-drive mentions, only `days[-1]` gets the "next morning" framing)
needed no changes: once `next_destination` is the real relocation target,
a day-trip day (chronologically before the group's actual departure, e.g.
Moab's Arches day) is already correctly left local-only by the existing
scrub pass (it's never `days[-1]`), while only Moab's genuine last evening
(the day after the last day-trip child, immediately before the real drive
to Telluride) gets the onward-travel note, correctly naming Telluride.
Verified by
`tests/test_ai_content_normalization.py::test_resolve_grouping_aware_prev_next_names_skips_day_trip_children`,
`::test_generate_destination_content_moab_gets_telluride_not_arches_as_next_destination`,
and
`::test_normalize_schedule_moab_day_trip_days_stay_local_only_last_evening_mentions_real_next_destination`.

**GH #68 grouped day trips never got a real chance to be named in the
base's own schedule (2026-08-18 fix, real Moab regression).** The
`previous_destination`/`next_destination` fix above only changes
*framing* language (does the schedule correctly say "the drive happens
tomorrow" and name the right relocation target) -- it does nothing about
whether the base destination's own schedule *content* ever names its
day-trip children at all. Investigated before fixing, per the project
owner's report ("Moab's schedule never mentions Arches, only
Canyonlands"): traced how Moab's own schedule-generation candidate pool
(`top_attractions`, threaded into `_inject_travel_realism` as
`attraction_names`) gets built, and confirmed it is Moab's own
AI-generated `top_attractions` ONLY -- nothing anywhere merges in a
grouped child's own attraction list, or even its name. Real evidence
this is a genuine gap, not a false alarm: Moab's real rendered
`top_attractions` (Moab Giants Dinosaur Park, Corona and Bowtie Arch via
Corona Arch Trail, Windows Loop and Turret Arch Trail) contains neither
"Canyonlands National Park" nor "Arches National Park" as an entry, yet
the real published schedule named "Canyonlands National Park Island in
the Sky" once (Day 2 Morning) -- meaning that one real mention was pure
AI-generation luck from the LLM's own free-text schedule authoring, not
the product of any deterministic mechanism. Arches had exactly the same
(zero) structural chance of being named and simply didn't get the same
luck.

Fixed with a new `_resolve_group_day_trip_names` helper
(`generator/ai_content.py`, alongside `_resolve_grouping_aware_prev_next_names`),
manifest-only by necessity: `generate_destination_content` runs every
destination's LLM call in parallel with no cross-destination ordering,
so a grouped child's own AI-generated attractions don't exist yet at the
point the base's own generation call needs this (same constraint that
keeps `_resolve_grouping_aware_prev_next_names` manifest-only). For each
group base entry, it resolves the plain NAMES of its `group_with`
children from the manifest; threaded through
`_generate_destination_bundle` -> `_normalize_destination_content` ->
`_normalize_schedule` -> `_inject_travel_realism` as
`group_day_trip_names`, where those names are merged into the same
`attraction_names` candidate pool the existing focus-rotation/scrub
mechanisms already draw from (Morning/Afternoon/Evening rotation, the
Afternoon multi-activity packer, the arrival-clone scrub above) -- so
every real day trip now has a genuine, deliberate chance to be named,
not just whichever one the model happened to already know about.
Verified by
`tests/test_ai_content_normalization.py::test_resolve_group_day_trip_names_gives_base_both_children_only`
and
`::test_inject_travel_realism_moab_schedule_can_name_arches_not_just_canyonlands`.

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

### Distance-label reframing at reclassification time
Real bug from a published eval run: the "Turquoise Trail National Scenic
Byway" departure route option rendered with the label `(50 miles one-way)`.
`scenic_drives.txt`'s prompt asks the AI for `distance_or_duration` in the
form `"e.g. '17 miles one-way'"`, meaning "this drive is 50 miles
point-to-point, not a round trip" -- a description of the scenic drive's own
length. `_filter_departure_aligned_drives` moves the drive dict into
`getting_there.route_options` with a plain shallow copy (`option =
dict(option)`), carrying that field over completely unchanged. But
`html_assembler.py` renders route options through the same `stop-detour`
markup en-route stops use for genuine detour framing (`"X mi detour off the
main route"`), so on the page the label reads as if it's describing extra
detour distance for a one-way leg. A departure route option is a full
alternate PATH for the whole leg, not a side detour off it -- reusing
detour-style "one-way" phrasing for it misrepresents what kind of choice it
is.

Fix: `_filter_departure_aligned_drives` now calls
`AIContentGenerator._reframe_route_option_distance_label(dist_text)` on each
drive's `distance_or_duration` at the exact point it's repurposed into a
route option, rewriting `"50 miles one-way"` → `"~50 mi total route"`. No
comparison figure against a "direct route" distance (e.g. `"~50 mi vs ~60 mi
via the direct interstate"`) is fabricated: `getting_there.distance_miles`/
`drive_time` are declared in `destination_content.txt`'s schema but no code
path anywhere in this codebase ever populates them for the departure leg
(confirmed by grep -- `getting_here.distance_miles` is populated via
Haversine estimation for the *arrival* leg, but no equivalent exists for
`getting_there`), so there is no real number available to compare against.
The reframed label is honestly vaguer rather than precisely wrong. If a real
comparison distance becomes available upstream in the future,
`_reframe_route_option_distance_label` is the place to use it. When the text
doesn't cleanly parse as `"<number> mi(les) ... one-way"`, the fallback path
only strips the misleading "one-way" qualifier itself rather than
fabricating a rewritten label from unrecognized phrasing.

Tests: `test_filter_departure_aligned_drives_moves_matching_one_way_drive_to_
getting_there` (extended), `test_reframe_route_option_distance_label_
rewrites_one_way_miles`, `test_reframe_route_option_distance_label_handles_
whole_number_with_decimal`, `test_reframe_route_option_distance_label_falls_
back_to_stripping_qualifier`, `test_reframe_route_option_distance_label_also_
normalizes_non_one_way_mileage`, `test_reframe_route_option_distance_label_
empty_input` (`tests/test_ai_content_normalization.py`).

### Rendering and map-link parity with en-route stops
Same real bug run surfaced two further gaps specific to the route-option
render path in `html_assembler.py`'s `_build_getting_there` (a separate code
path from `_build_getting_here`'s en-route-stop cards, not shared logic):

- The route option's `<a>` tag was missing `target="_blank" rel="noopener"`
  -- every other external link on the page carries both. Fixed by adding
  them to the anchor built in `_build_getting_there`'s
  `renderable_route_options` loop.
- No route option ever rendered a `badge-map` icon, even when it had a real,
  distinct primary source URL. `_maps_corner_link_html` (the shared badge
  renderer, already wired into this same loop via `maps_corner_html =
  self._maps_corner_link_html(opt, url)`) was never the problem -- it
  correctly renders the badge whenever `item["maps_url"]` is set. Root cause
  was upstream: unlike en-route stops (route-waypoint geocoding), attractions,
  and restaurants (see "Secondary Maps Link for Attractions and Restaurants"
  in `url-discovery-and-audit.md`), nothing in `generator/url_discovery.py`
  ever attached a `maps_url` to a `getting_there.route_options` entry.
  `audit_discovered_urls`'s route-options loop now calls the same
  `_attach_secondary_maps_link(route_opt, opt_name, dest_name,
  kind="route_option")` helper used for attractions/restaurants, in a
  post-loop pass once each option's primary `url` is settled for the run.

Tests: `test_build_getting_there_route_option_link_has_target_and_rel`,
`test_build_getting_there_route_option_renders_map_badge_when_maps_url_
present` (`tests/test_html_assembler.py`),
`test_audit_attaches_secondary_maps_link_for_departure_route_option`
(`tests/test_url_discovery.py`).

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
- Inspect `_dedupe_schedule_day_content`'s `_dedupe_needs_variation` flagging,
	whether `_inject_travel_realism`'s rotation passes had a second real
	attraction/restaurant to rotate to, and upstream model quality.

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
