# Multimodal Routing

**GH #2 · Design note · Drafted 2026-08-20 against generator `v2.0.1`, branch `v2`.**

Today every leg of every itinerary is a car leg. Not by decision — by accumulation.
Nothing in the pipeline ever asked "how does the traveler get there?", so the answer
"they drive" got baked into a prompt, two distance estimators, a schedule normalizer,
three HTML builders and one URL parameter, each independently and none of them aware of
the others.

This note maps where those assumptions actually live, then proposes a two-phase route to
real multimodal support. The phasing is not a project-management convenience. Phase 1 is
Phase 2's fallback rung: a corridor with no GTFS feed returns nothing from the Directions
API, and something has to render there. Building the honest-negative path first means
Phase 2 has somewhere to land when it fails, which it will, often.

> **Requires:** `requirements.md` §3 Trip Manifest Schema · §4 AI Content Generation ·
> §5 URL Discovery
>
> **Read first:** `design.md` §1.4 (the model never produces a URL) ·
> §2.6 (honest fallback as a product surface) · `schedule-normalization.md`

---

## 1. What exists today

### 1.1 Where "car" is written down

| # | Location | What assumes a car | Load-bearing elsewhere? |
|---|---|---|---|
| 1 | `prompts/destination_content.txt`, the `getting_here` block | Asks the model for `drive_time`, `distance_miles`, and a `route_summary` that must "Name the specific highways in order" | Yes — every field below flows from this shape |
| 2 | `ai_content.AIContentGenerator._normalize_getting_here` | Synthesizes a missing summary as `f"Arrival leg into {dest_name} typically takes about {drive_time}."` | No — cosmetic |
| 3 | `url_discovery.URLDiscoverer._update_route_distance_and_time` | Builds `travelmode=driving` Maps URL and scrapes it; overwrites the model's numbers | **Yes** — see §1.2 |
| 4 | `url_discovery.URLDiscoverer._estimate_route_from_haversine` | `road_factor=1.30`, `avg_speed_mph=60.0` — road-network constants | Yes, as #3's fallback |
| 5 | `ai_content._estimate_haversine_route` (module-level) | Same formula, deliberately duplicated; used by `_override_grouped_child_distance_from_geocode` for `group_with` day trips | Yes, for grouped children |
| 6 | `html_assembler._build_getting_here` | Literal `🚗 Getting Here`; badges `{distance} mi` and `{drive_time}` | Yes — the only inbound-leg surface |
| 7 | `html_assembler._build_route_gmaps_url` | `travelmode=driving` hardcoded; waypoint model is car-specific | **Yes** — §1.3 |
| 8 | `html_assembler._build_getting_there` | `🚗` icon on every departure route-option card; same two badges | Yes, final destination only |
| 9 | `ai_content` schedule normalizer, the `drive_time` block | Arrival clock time and the afternoon activity budget both computed from drive minutes | **Yes, the most** — §1.2 |
| 10 | `url_discovery` en-route geometry: `route_progress_ratio`, `_route_perpendicular_distance_miles`, `detour_distance_miles`/`detour_time_minutes`, and the `MAX_PLAUSIBLE_EN_ROUTE_DETOUR_MPH` ceiling | The entire en-route-stop concept — you cannot take a 12-mile detour off a scheduled bus | Yes — a whole discovery category |
| 11 | `scenic_drives` as a content category, and `trip.has_high_clearance_vehicle` | Presumes the traveler is behind a wheel | Partially — see §4.4 |

### 1.2 The load-bearing one: `getting_here.drive_time` drives the schedule

This is the coupling that makes multimodal routing a real change rather than a rendering
change. In `ai_content`'s schedule normalization:

```python
drive_time = str(getting_here.get("drive_time", "") or "").strip()
drive_minutes = _parse_duration_minutes(drive_time)
```

From that single value the normalizer derives, for every non-first destination:

- **Arrival clock time** — `effective_start_minutes + drive_minutes`, rendered into the
  Day 1 Morning summary as "Travel from X (depart around 10:00 AM); arrival around
  12:15 PM."
- **A correctness guard on the model's own prose** — if the model already narrated
  arrival *and* claimed a morning activity, and computed arrival is past an 11:00 AM
  cutoff, the "morning" qualifier is surgically stripped and a parenthetical realistic
  arrival appended. This exists because of a real regression (dipstick69, Bryce arriving
  from Zion on a 135-minute drive, "head to Sunrise Point for morning views" at a
  computed 12:15 PM arrival).
- **The arrival-day activity budget** — `max(0, effective_activity_budget_minutes -
  drive_minutes)` feeds `_build_multi_activity_afternoon_summary`. Travel time is
  subtracted from the day's allowance, not treated as free time on top of it.

So `drive_time` is not a badge. It is the arrival-day scheduling input. Any transit
design that leaves it holding a car estimate produces a page that shows a 3h15 bus in one
card and schedules a 2h drive in the next — the exact class of internal contradiction the
dipstick69 guard was written to prevent.

### 1.3 `_build_route_gmaps_url` and its scar tissue

Three things matter here.

**It hardcodes the mode.** `params = [f"destination={quote(destination)}",
"travelmode=driving", "api=1"]`. Changing that one string is the smallest possible
Phase 1 win — and also the one most likely to break in a way tests cannot see.

**Its waypoint handling has failed publicly, twice, on speculative changes.** An earlier
fix prepended `optimize:true|` to the waypoint list, borrowing the Directions *API*
convention. The public keyless Maps URL scheme does not support it: Google geocoded the
literal string `optimize:true` to a Washington-state clinic and returned a 33-hour,
2,196-mile route (dipstick68). Separately, `design.md` §4.5 item 16 records an unresolved
en-route waypoint mis-geocode of the same family, deferred by owner call rather than
fixed blind.

**The lesson:** do not add a mode branch inside the existing waypoint logic. Transit legs
have no waypoints anyway (§4.3), so the correct shape is an early return, not a
conditional threaded through the loop.

**Uncertain, flagged:** whether `travelmode=transit` combined with `waypoints=` behaves
sanely in the public URL scheme, is ignored, or produces another `optimize:true`-class
failure is unknown. Given that function's history this must be verified live before
shipping, not reasoned about. It is the highest-value cheap experiment in this plan.

### 1.4 What already exists that is *not* an obstacle

`TRANSPORTATION_ITEM_SCHEMA` already models booked travel legs — `type`
(`plane|train|car|other`), `provider`, `label`, `confirmation_number`, `depart`, `arrive`,
`website` — shared verbatim by `trip.transportation` and `destination.transportation`.
They render as header pills (`_build_transportation_pills`) and route-overview chips
(`_build_trip_transportation`), and are cleared by `main._apply_privacy_redaction`.
`reservation_ingest` fills them from forwarded confirmation emails.

§3.3 reasons about whether routing options should reuse it. Short version: no, but they
should share the render vocabulary.

Also: `getting_here` is only partially registry-managed. `entity_registry`'s
`_SECTION_TARGETS` includes `getting_here.en_route_stops` but nothing else — `drive_time`,
`distance_miles` and `route_summary` are plain trip-dict fields that survive
reconciliation untouched. A `transit_options` block beside them inherits that position:
outside the registry, which is the right default (§7 non-goals).

---

## 2. The phased plan

### 2.1 Phase 1 — AI-only, and what it honestly cannot do

**LANDED 2026-08-30**, as specified below. `generator/transit_routing.py` holds the
normalizer, the leg-mode resolution and the `ai | google_directions` factory;
`prompts/transit_options.txt` asks for Shape B; `config.yaml`'s `transit_routing` block
carries the kill switch and the provider key. Options are generated at the end of stage 3
(`main._apply_transit_routing`) and render in `_build_getting_here` behind an
`⚠ Unverified` badge and the §4.5 disclaimer. Selecting `google_directions` raises rather
than falling back, so nothing can report an AI guess under a name that reads as verified.

Not done, and each needs a live run rather than more code: whether the model's claims are
true, real per-run cost against §6, and whether the transit card and the day schedule agree
on the page. `travelmode=transit` in the public Maps URL scheme was already live-verified
before this work (the waypoint-rejection note in `_build_route_gmaps_url`), which is why
§4.3's early return could be written as a certainty rather than an experiment.


The issue accepts that "this information initially may not be reliable." That is the right
instinct but understates the problem, so state it plainly:

> **A language model cannot know a departure time.** Not unreliably — *at all*. A
> timetable is exactly the class of fact `design.md` §1.4 removed from the model's job for
> URLs: a plausible 09:00 departure is indistinguishable from a real one until someone
> stands at a bus stop.

The issue contains two candidate output shapes and they are not equivalent:

```jsonc
// Shape A, from the issue's opening example
{ "mode": "bus", "provider": "Greyhound", "depart": "2026-10-14T09:00",
  "arrive": "2026-10-14T12:15", "duration": "3h 15m", "transfers": 1,
  "url": "https://greyhound.com/..." }

// Shape B, from the issue's own transit_routing.py contract
{ "label": "Regional bus via [nearest town]", "duration": "3-4 hours", "transfers": 1,
  "notes": "Runs daily in peak season; check local transit site.",
  "booking_hint": "Search 'Moab to Grand Junction bus' for current schedules." }
```

**Phase 1 must emit Shape B and must structurally forbid Shape A.** Shape A carries an ISO
datetime and a URL: the two things this project has decided, twice and with evidence, not
to let a model produce. Shape B carries a corridor description, a duration *range*, and a
search phrase — all of which a model can plausibly get roughly right, and none of which
strand a traveler at a station.

Phase 1's normalizer should **strip, not trust**: any `depart`/`arrive` matching a datetime
pattern is dropped; any `url`/`booking_url` is dropped regardless of content. Enforced in
code, not asked for in the prompt — `design.md` principle 7, with the banned-marketing
episode as precedent (one real run contained 28 violations of a rule the prompt had stated
for months).

**Model it as a discriminated union, following cultural events.** `cultural_events` solved
the identical problem — a model asked "what's on?" will invent a festival rather than
disappoint — with Format A (real dated events) versus Format B (an honest assessment).
Transit should mirror it:

```jsonc
// Format A — the corridor plausibly has scheduled service
{ "has_transit": true,
  "options": [ /* Shape B entries, 1-3 */ ],
  "fallback": "Driving remains the most reliable option on this corridor." }

// Format B — it does not
{ "has_transit": false,
  "honest_assessment": "No scheduled public transit connects Bryce Canyon to Capitol Reef.
     The nearest regional service reaches Panguitch, ~60 miles short, with no onward
     connection. Driving or a private shuttle are the only realistic options.",
  "local_tip": "Several Springdale-based outfitters run point-to-point park shuttles on
     request." }
```

Remote US national-park corridors will land on Format B nearly every time, exactly as they
do for events. That is not a degraded output. It is the correct answer, and far more useful
than an invented Greyhound route.

**What Phase 1 delivers:** mode plausibility, rough duration bands, transfer counts as an
order-of-magnitude signal, a search phrase, and — most valuably — a defensible *negative*.

**What Phase 1 cannot deliver, and must not appear to:** departure times, arrival times,
service frequency, seasonal windows stated as fact, operator names presented as verified,
fares, or booking links. The issue lists "scheduled departures", "service availability",
"seasonal variations" and "ticketing URLs" as goals. Phase 1 addresses none of them
honestly. It addresses *shape*. Say so in the UI (§4.5) rather than hoping the reader
infers it.

**Operator names are the residual risk.** Even Shape B names providers. A model claiming
Greyhound serves a corridor Greyhound abandoned in 2019 is the transit analogue of the
fabricated festival. Two mitigations, in preference order:

1. Prompt for *categories* over brands — "regional bus via Panguitch" beats "Greyhound"
   and is more likely true.
2. Optionally corroborate named operators through the existing per-item search path. This
   costs search calls, which is where this project's money actually goes (§6). Ship it
   config-gated and **off** by default.

### 2.2 Phase 2 — Google Directions in transit mode

Facts that shape the design:

- **Transit mode requires a departure time.** The manifest already carries the anchors to
  compute one: `dates` per destination, `trip.departure_datetime`, and the start-time chain
  (`destination.schedule_start_time` > `trip.default_day_start_time` > `10:00 AM`) that
  schedule normalization already resolves. Reuse that chain rather than inventing a second
  time model — two answers would drift.
- **Coverage is GTFS-feed dependent, and absence is silent-ish.** A corridor with no
  participating agency returns `ZERO_RESULTS`, not an error. That is a *true negative* and
  must route to Phase 1's Format B, not to an empty card. This is the structural reason
  Phase 1 is not throwaway work.
- **Frequency needs sampling.** One call gives one itinerary. "Runs roughly hourly" versus
  "one departure a day" — the difference between a usable leg and a trap — requires 2-3
  calls at spread departure times. Budget for that (§6).

**Module contract.** One new module, `generator/transit_routing.py`, exposing
`generate_transit_options(from_dest, to_dest, trip_meta) -> dict`, behind a provider factory
mirroring `generator/search_provider.py`. A `transit_routing.provider: ai | google_directions`
key in `config.yaml` then makes Phase 2 a config flip rather than a rewrite — and makes an
A/B comparison run possible on one manifest, which is how this project has settled every
provider question so far.

*Naming note:* the issue writes `transit-routing.py`. A hyphenated module cannot be
imported. Use `transit_routing.py`.

**Before writing Phase 2, run a probe.** `design.md` §2.4 records the most instructive
episode in this project's history: Grok's search had been silently dead for months because
nobody measured the dependency before building on it. A ~50-line script hitting Directions
transit for the real manifest's legs, recording answered / `ZERO_RESULTS` / error per leg,
answers the only question that matters — *does this API know anything about these
corridors?* — for a few cents. And unlike its predecessor, **commit it**: `design.md` §4.5
item 14 notes that `scripts/probe_multi_provider_search_2026.py`, cited as re-runnable by
two design notes, is not in the repository.

---

## 3. Manifest schema changes

### 3.1 `transport_mode`

Two additions to `MANIFEST_SCHEMA`, both optional, both defaulting to today's behaviour:

```python
# trip.properties
"transport_mode": {
    "type": "string",
    "enum": ["auto", "transit", "mixed"],
    "description": "Optional trip-wide travel assumption for inter-destination legs. "
                   "'auto' (the default when omitted) is current behaviour, unchanged: "
                   "every leg is a drive. 'transit' asks for scheduled public transport "
                   "instead, including the arrival-day schedule. 'mixed' renders transit "
                   "options alongside the drive rather than in place of it. Overridable "
                   "per destination.",
},

# destinations.items.properties
"transport_mode": {
    "type": "string",
    "enum": ["auto", "transit", "mixed"],
    "description": "Optional override for the leg ARRIVING at this destination -- the "
                   "journey from the previous stop to this one. Attaches to the arriving "
                   "destination for the same reason en_route_seeds and transportation do: "
                   "the inbound leg belongs to the place it delivers you to.",
},
```

`jsonschema` gives enum rejection for free, so a typo fails at Stage 1 with a located
message via `_format_schema_error`, which already names the offending destination by id.

**Naming.** Recommend plain `transport_mode` on the destination, with "inbound leg"
semantics in the description, rather than the issue's `transport_mode_from_previous`. It
matches the existing convention exactly — `en_route_seeds` and `transportation` both attach
to the arriving destination and neither carries a suffix. Resolved 2026-08-21: accepted.

### 3.2 The `legs:` list — accepted, with a validation contract

**Decided 2026-08-21: both.** Per-destination `transport_mode` (§3.1) *and* a `legs:`
list. This section previously recommended against `legs:`; that recommendation is
withdrawn. What survives it is the contract below, because the original objection was
never "`legs:` is a bad idea" — it was "`legs:` as the issue specifies it fails silently."

The issue proposes `from`/`to` as free text matched against `name`. That is the part that
does not survive. `"Zion NP"` against a manifest saying `"Zion National Park"` matches
nothing, the leg quietly stays `auto`, the build succeeds, and the output renders a normal
drive card. The failure is invisible to schema validation, to the runtime, and to the
reader — you would notice only by remembering you wanted a train there.

**A silently-ignored leg is worse than a build failure.** A build failure costs thirty
seconds. A silent fallback ships a traveller an itinerary telling them to drive a leg they
have no car for.

#### The contract

`from`/`to` are destination **`id`s** — pattern-constrained slugs (`^[a-z0-9_]+$`) — never
display names:

```yaml
legs:
  - from: zion
    to: bryce_canyon
    mode: transit
```

Validated in `_validate_legs`, mirroring `_validate_group_with` (which exists precisely
because dangling references are a real, observed authoring failure here). Every one of
these **raises**, naming the offending leg:

| Condition | Why it cannot merely warn |
| --- | --- |
| `from` or `to` is not an existing destination `id` | The typo case. This is the whole reason for the contract |
| `from == to` | Not a leg |
| `from`/`to` are not adjacent in destination order | The issue's own example is adjacency-shaped; a non-adjacent pair has no defined meaning |
| The same leg appears twice | Two answers, no rule for choosing |

#### Collision with `transport_mode` — also an error

Accepting both mechanisms creates a case the single-mechanism design did not have: a
`legs:` entry and the arriving destination's `transport_mode` can name **the same leg**,
since "the leg from `zion` to `bryce_canyon`" and "`bryce_canyon`'s inbound leg" are the
same thing.

Resolution: **agreement is fine, disagreement raises.** Not last-wins, not
most-specific-wins.

This is the one place worth spending a build failure, because it is the drift bug class
this project has already hit four times — one value restated in two places, free to
diverge. Silent precedence would mean an author edits `transport_mode`, sees no change
because a `legs:` entry outranks it, and has no way to discover why. Raising converts a
mystery into a located message.

#### What this costs

A third expression of adjacency, on top of destination order and `group_with`. That cost is
real and is the reason for the original recommendation. The contract above is what makes it
payable: every way the redundancy could diverge is a build failure rather than a silent
wrong answer.

### 3.3 Should routing legs reuse `TRANSPORTATION_ITEM_SCHEMA`?

**Recommendation: keep them separate. Share the render vocabulary, not the schema.**

1. **They differ in epistemic status, and that difference is the whole point of this note.**
   A `TRANSPORTATION_ITEM_SCHEMA` entry is ground truth — a human typed it, or
   `reservation_ingest` extracted it from a confirmation the traveler forwarded — and
   carries a `confirmation_number` to prove it. A routing option is a guess about a service
   the traveler has not bought. Merging them puts "your 09:00 flight, locator XR7Q2M" and
   "there might be a bus around 9" in one list, indistinguishable downstream.
2. **`depart`/`arrive` mean different things.** The existing schema documents them as
   *"display strings, not scheduling inputs. Nothing in the pipeline parses them."* A
   routing option's times must be parseable to feed `travel_time` (§4.1). Overloading one
   field with both contracts guarantees someone eventually parses a booked leg's free text
   and gets a `ValueError` on `"SFO 8:15 AM, October 7"`.
3. **`additionalProperties: False`, shared verbatim by three call sites.** Adding
   `transfers`/`duration`/`confidence` would widen the *booked leg* schema at trip level,
   destination level, and through `reservation_ingest`'s post-merge re-validation — a model
   extracting a hotel email could then legally emit `transfers: 2`.
4. **Routing options do not belong in `MANIFEST_SCHEMA` at all.** They are generated output,
   and the governing shape is that the manifest holds only what the human provides
   (`design.md` §1.1). The right home is a normalizer in `transit_routing.py`.

**What they *should* share:** `html_assembler._TRANSPORT_KINDS`. It covers
`plane|train|car|other`, so a Phase 1 bus option renders as "🧳 Travel". Extend with `bus`,
`shuttle`, `ferry` and both features benefit. Render transit options in the existing
`stop-card` / header-pill vocabulary so booked and suggested legs read as siblings,
distinguished by what they carry: a confirmation number versus `⚠ Unverified`.

### 3.4 The generated shape

Attached at `ai_content["getting_here"]["transit_options"]`:

```jsonc
"transit_options": {
  "has_transit": true,
  "source": "ai",                   // "ai" | "google_directions"
  "confidence": "unverified",       // "unverified" | "api_verified"
  "queried_at_utc": "2026-08-20T...", // Phase 2 only; a timetable answer has a shelf life
  "options": [
    { "mode": "bus", "label": "Regional bus via Panguitch", "duration": "3-4 hours",
      "transfers": 1, "notes": "...", "booking_hint": "Search '...' for current schedules." }
  ],
  "fallback": "Driving remains the most reliable option on this corridor."
}
```

`source` and `confidence` are not decoration — they are how the renderer chooses between a
verified card and an `⚠ Unverified` one without re-deriving the reasoning (`design.md` §2.2,
confidence as a property of the path a fact travelled).

---

## 4. Interaction with existing behaviour

### 4.1 Scheduling: rename to `travel_time` first, then guard the overwrite

**LANDED 2026-08-30.** `getting_here.travel_time` (and `getting_there.travel_time`) is the
canonical field. `_normalize_getting_here` accepts either key from the model and emits the
canonical one; every internal reader now reads `travel_time` only. Full suite green,
unchanged at 1865 tests. The byte-identical-output check in the table below is the one part
not done — it needs a live run.

**Decided 2026-08-21 (open question 4): rename now, as its own change, before the routing
work.** This section previously recommended reusing `drive_time` and renaming later. That
recommendation was explicitly conditional on transit being an occasional variation. The
owner's answer is that transit is where the product is heading, which inverts it.

| Option | Effect | Verdict |
|---|---|---|
| Leave `drive_time` as the car estimate; add `transit_options` beside it | Page shows a 3h15 bus and schedules a 2h drive | Reject — the incoherence dipstick69 was fixed to prevent |
| Populate `drive_time` from the transit duration | Downstream keeps working unchanged; the field name becomes a permanent lie | Reject on the "heading there" forecast — see below |
| **`getting_here.travel_time` as the canonical field** | Honest name before transit code grows around the wrong one | **Chosen** |

**Why the forecast decides it.** "Rename later, separately" has a failure mode: *later*
does not arrive. `drive_time` is read across four modules and the test suite today. If
transit becomes the common case, every new transit code path is also written against a
field named for the mode it is not using, and the rename gets more expensive every week.
Renaming while there are four readers is the cheapest this will ever be.

**It is cheaper than this note first assumed.** `drive_time` does not appear in
`manifest_parser.py` — it is model-generated content in the trip dict, never a manifest
input. **No user-authored manifest breaks, and there is no migration for anyone's data.**
That is the fact that makes doing it now genuinely cheap rather than merely correct.

#### The alias belongs at one boundary only

Not a transitional alias threaded through the codebase. The prompt
(`prompts/destination_content.txt`) will ask for `travel_time`, but a model asked for one
key will sometimes emit the other, and prompts drift (four recorded incidents of exactly
this class). So:

```
model output  --(accepts travel_time OR drive_time)-->  _normalize_getting_here
                                                                |
                                                     emits canonical travel_time
                                                                |
                       every internal reader sees travel_time only
```

The tolerance is **permanent defensive handling of model output**, not a deprecation
window. Internally there is exactly one name from the normalizer onward.

#### Sequencing

The rename lands **first, on today's all-car behaviour**, as a separate change: full suite
green, and generated output byte-identical to the previous build. Only then does routing
work begin, against the correct name. Bundling them would mean that when a schedule comes
out wrong, nothing distinguishes "the rename missed a reader" from "the routing logic is
wrong".

**The hazard this creates, and it is real.** Stage 3 runs before Stage 5b, and
`url_discovery._update_route_distance_and_time` overwrites `getting_here`:

```python
if best_time and (not current_time_raw or not current_miles):
    getting_here["drive_time"] = best_time
```

`best_time` is a scraped Google *driving* duration or a 60 mph Haversine estimate. If a
transit leg set `drive_time = "3 hr 15 min"` and left `distance_miles` empty (as it should
— §4.2), then `current_miles` is falsy, the condition is **true**, and a real transit
duration is silently replaced by a car estimate two stages later.

**A second reader of the same hazard, found 2026-08-30 and fixed.** Guarding
`_update_route_distance_and_time` closes the stage-5b path, but two stage-3 paths write the
same field from road geometry: `_override_grouped_child_distance_from_geocode`'s
implausible-leg correction, and the model itself, which was never told the leg was transit
because `_build_arrival_mode_guidance` keyed on a BOOKED leg. A declared-but-unbooked
transit leg therefore kept a drive estimate and scheduled from it. All three now key on the
resolved mode, and `main._enforce_transit_leg_durations` is the backstop: on a declared
transit leg, `travel_time` is a Routes API figure, else the Phase 1 duration band, else
empty -- never a road-derived one. Clearing costs that day its travel subtraction, which is
the honest trade: an unstated arrival is a gap, an invented one is a wrong answer the reader
cannot see is wrong.

`_update_route_distance_and_time` must **return early** for any leg whose `transport_mode`
resolves to `transit`. Add a regression test running the full Stage 3 → Stage 5b ordering;
a unit test of either function alone will pass while the pipeline is broken.

### 4.2 The badge pair, and a rendering trap

`_build_getting_here` renders the badge row only when both values are present:

```python
if distance and drive_time:
```

For a transit leg, road miles are meaningless. But leaving `distance_miles` empty drops the
*duration* badge too, silently, because of that `and`.

Restructure to render each badge independently, and on a transit leg substitute a
transfer-count badge for distance:

```
Bryce NP -> Capitol Reef NP   [Transit] [3-4 hours] [1 transfer] [⚠ Unverified]
```

Same change benefits `_build_getting_there`, which has the identical structure.

**Escaping.** `design.md` §4.5 item 11 records that `route_summary` is interpolated raw in
`_build_getting_here` while the identical line in `_build_getting_there` *is* escaped. Do
not extend that inconsistency: every transit prose field goes through
`html_escape.escape`, any URL through `_safe_href`. Assert it in tests.

### 4.3 `_build_route_gmaps_url`

Minimal change: a keyword-only `travel_mode: str = "driving"` feeding the existing
`travelmode=` param, plus **an early return past the entire waypoint block when
`travel_mode != "driving"`**.

The waypoint skip is correctness twice over: en-route stops are a car concept, and §1.3's
history says the waypoint path is where this function's failures live. Leave the driving
path byte-identical. **Verify live before shipping.**

### 4.4 En-route stops, scenic drives, grouped day trips

**En-route stops** are not discovered for a `transit` leg — **confirmed 2026-08-21 (open
question 3)**. Structurally meaningless: there is no roadside to stop at on a train. Skipping
one of the four parallel discovery jobs is a genuine cost saving (§6), and it removes a class
of geocode mis-resolution failures for those legs. Under `mixed`, keep them — the drive is
still on the table there, so its stops are still real.

The accepted cost: those destinations lose a content section. That is the right trade because
the section was never applicable, not because it was cheap to drop.

**Scenic drives** should *not* be suppressed by `transport_mode` alone. A traveler who takes
a train to Moab may still rent a jeep there; `has_high_clearance_vehicle` is the existing
orthogonal flag, and scenic drives are destination content rather than leg content. Product
call — open question #7.

**Grouped day trips.** A `group_with` hop is a there-and-back day trip from a shared base,
not an inbound relocation leg, so `transport_mode` on a grouped child is a category error.
Recommend: log a warning and ignore, matching `_warn_if_group_dates_outside_base_range`.

### 4.6 A booked leg outranks a generated one

**Decided 2026-08-21 (open question 2).** Where `reservation_ingest` has attached a booking
for a leg, render the booking and **do not generate options for that leg at all**.

This follows directly from §3.3's epistemic split. A `TRANSPORTATION_ITEM_SCHEMA` entry is
ground truth — the traveller forwarded a confirmation and it carries a
`confirmation_number`. A routing option is a guess about a service nobody has bought.
Offering "there might be a bus around 9" beside "your 09:00 flight, locator XR7Q2M" is not
helpfulness; it is noise attached to a decided question.

**Matching.** The ingestion already attaches each booking to the destination it delivers the
traveller to (or to `TRIP_LEVEL_ID` for `trip.departure`/`trip.return`). That is the same
"inbound leg belongs to the arriving destination" convention `transport_mode` uses in §3.1,
so the two line up without new matching logic: a destination-level transportation item **is**
that destination's inbound leg.

**The booking wins, and does not raise.** This is deliberately unlike §3.2's collision rule,
and the difference matters:

| Collision | Resolution | Why |
| --- | --- | --- |
| `legs:` vs `transport_mode` (§3.2) | **Raise** | Two *authoring* statements. One is a mistake, and the author must say which |
| Booking vs `transport_mode` | **Booking wins, logged** | An authoring statement against an *observed fact*. Not a mistake — plans change |

A traveller who wrote `transport_mode: transit` in March and forwarded a car-rental
confirmation in August has not made an error. Raising there would break a build because an
email arrived, which is absurd. Log at INFO naming both, so the divergence is discoverable
without being fatal.

**`mixed` degenerates for a booked leg.** With a booking present there is nothing to render
alongside — options are suppressed, so `mixed` and `transit` behave identically for that leg.

**Cost.** This is the cheapest branch in the design: a booked leg costs zero routing calls.
On a trip whose flights and rail are already forwarded, most legs never reach the routing
provider at all.

### 4.5 Telling the reader what they are looking at

The card must not read as a timetable. Phase 1 output should carry, in the card itself:

> These options are AI-suggested and unverified. Confirm schedules with the operator before
> relying on them.

Same posture as `⚠ Unverified` on an unlinked attraction. A traveler who arrives to find a
fabricated festival is worse off than one told there is nothing on. A traveler stranded at a
bus stop is worse off still.

---

## 5. Verification strategy

### 5.1 Unit-testable

| File | What to assert |
|---|---|
| `tests/test_manifest_parser.py` | `transport_mode` accepted at both levels for each enum value; unknown value fails naming the destination; omitting it leaves the parsed manifest byte-identical to today's. **`legs:` (confirmed 2026-08-21, §3.2):** each raise case separately — unknown `from`/`to` id, `from == to`, non-adjacent pair, duplicate leg — each asserting the message names the offending leg; a `legs:` entry agreeing with the arriving destination's `transport_mode` parses clean, and one disagreeing raises naming **both** sources; `from`/`to` given as display names rather than ids fails loudly rather than matching nothing |
| **`tests/test_transit_routing.py`** (new) | Normalizer against fixture payloads: missing keys; `options` not a list; **an ISO datetime is stripped**; **a URL is stripped**; `has_transit: false` produces Format B; provider factory selects from config; `ZERO_RESULTS` degrades to Format B rather than an empty card |
| **The rename, tested before any routing work** | `_normalize_getting_here` accepts a model emitting `drive_time` OR `travel_time` and emits canonical `travel_time` either way; every internal reader reads only `travel_time`; an all-car build produces output **byte-identical** to the pre-rename build (§4.1) |
| `tests/test_ai_content_normalization.py` | `travel_time` populated from transit duration; arrival-clock and afternoon-budget derivations transit-consistent; `mixed` retains the car estimate while attaching options |
| `tests/test_url_discovery.py` | `_update_route_distance_and_time` returns early on a transit leg and does **not** overwrite `travel_time` (§4.1). The overwrite hazard survives the rename unchanged -- renaming the field does not disarm it |
| `tests/test_pipeline_integration.py` | The Stage 3 → Stage 5b ordering case, end to end |
| `tests/test_html_assembler.py` | Transit card from Format A; Format B renders the honest assessment; duration badge survives empty `distance_miles`; `travelmode=transit` present and `waypoints=` absent; every prose field escaped; `⚠ Unverified` when `confidence != "api_verified"` |
| `tests/test_main_requirements.py` | Privacy redaction: generated transit options are **not** redacted (no personal data, unlike booked legs). Assert explicitly so a later reader doesn't "fix" the asymmetry |
| `tests/test_costs.py` / ledger | The `transit_routing:` operation prefix is recognised by stage-cost attribution. `design.md` §4.4 records **two** incidents of spend silently excluded because a prefix was unrecognised |

### 5.2 Only verifiable by a live run

- **Whether the model's claims are true.** No offline test catches "Greyhound serves
  Bryce → Capitol Reef" being false.
- **`travelmode=transit` behaviour in the real Maps UI** (§1.3, §4.3).
- **Phase 2 coverage** — which corridors return `ZERO_RESULTS`. That is what the §2.2 probe
  is for.
- **Real per-run cost**, against §6's estimates.
- **Whether the transit card and the day schedule agree on the page.** The failure mode is
  two cards that each look fine alone.

Suggested acceptance case: one manifest where transit genuinely exists (`Japan_manifest.yaml`
exists in the Sandbox directory and is the natural candidate) **and** one leg of the
Southwest manifest, which should land Format B. Both matter; the negative path is the common
one for this project's actual trips.

---

## 6. Cost

**Baseline.** The entry-into-service run: **$2.6549 across 202 calls**, of which **$2.225
(83.8%) was web-search tool fees**, not tokens — 445 `web_search` invocations at $5/1000.
Token cost across 184 Grok calls was ~$0.405, about **$0.0022 per content call**.

*Caveat on provenance:* these figures come from `design.md` §4.7 citing the prod ledger for
`run_id 20260819T011501.442336Z`; they were not independently re-derived here.

### 6.1 Phase 1

One LLM call per **leg** — `N − 1` for `N` destinations, and zero when `transport_mode` is
`auto`, the default.

- 10-destination manifest, fully transit: **≤ 9 calls**, +4.9% on 184 content calls.
- Content calls carry **no** `web_search` fee. Token cost only.
- At ~$0.0022/call: **≈ +$0.02 per run**, under 1% of baseline.

Two favourable second-order effects: transit legs skip en-route discovery (§4.4), a real
consumer of the expensive search calls — a fully-transit trip could plausibly cost *less*
than today. And folding transit into the existing merged bundle call would be cheaper still,
but reject it: coupling transit failure to content failure trades a rounding error for a
real reliability cost.

**Phase 1's cost is noise. Do not let cost drive its design; let honesty (§2.1) drive it.**

### 6.2 Phase 2

1 Directions call per transit leg minimum; realistically **2-3** for frequency (§2.2). So
**9-27 calls** on a 10-destination fully-transit manifest.

At list pricing believed to be ~$5 per 1,000 basic Directions requests, that is
**$0.05-$0.14 per run — roughly 2-5% of current run cost**. Even at a $10-15/1,000 advanced
tier it stays under $0.50.

> **Flagged as unverified.** Current 2026 Maps Platform pricing, the monthly free credit,
> and whether transit is billed basic or advanced were not checked. Order-of-magnitude only
> — confirm before committing. The §2.2 probe should record actual billed cost alongside
> coverage.

**Routing is not this project's cost problem. Search is.** Phase 2 should be gated on ToS
and coverage, not price.

### 6.3 Making the new dependency visible

`llm_client.UsageTracker` prices tokens and `web_search` invocations. A Directions call is
neither, and bending it into the tool-call bucket would misattribute a non-LLM cost to an
LLM provider, corrupting the per-provider comparison the tracker exists for.

Recommend a separate `runtime_metrics` counter — `transit_routing_api_calls` plus estimated
USD — surfaced in the ledger and cost summary from **day one**. The `costs.py` docstring
records why: real xAI billing ran ~$5/day while the estimator reported ~$0.40/run, for as
long as `tool_call_cost_usd` did not exist.

---

### 6.4 Maps Platform terms — researched 2026-08-21, and they are the blocker

Open question 6 asked whether the ToS posture is acceptable for a `prod` build published to
public GitHub Pages. Researched, and **cost is not the deciding factor — licensing is.**

**Pricing, for completeness.** Routes API bills in three tiers, roughly $5 / $10 / $15 per
1,000 requests depending on which fields the request includes. Google's own
usage-and-billing page does not state which tier a `TRANSIT` route falls into, nor whether
asking for transit details (departure/arrival times, line info) promotes the request to a
higher tier, nor any free allowance. **Treat the per-leg cost as unconfirmed.** At ~20
destination-legs per trip it is small either way — this was never going to be the problem.

**The problem is three clauses that interact badly with what this product actually does.**

| Clause | Source | Our situation |
| --- | --- | --- |
| "Directions API results displayed on a map must be shown on a Google Map" | Directions policies | Our output renders a **Leaflet** map (`leaflet@1.9.4`, confirmed in the 2026-08-21 build) |
| Displayed *without* a map: requires a visible Google logo and "Google Maps" attribution, with prescribed styling and placement | Directions policies | Feasible, but constrains the card design |
| Caching/storage is restricted — lat/lng for at most 30 consecutive days, then deletion; other Directions data per the agreement (place IDs exempt) | Maps service terms | **This is the fatal one.** See below |

**Why the caching clause is fatal to the current model.** This product generates a static
HTML page and publishes it. A trip built in August for an October departure sits on GitHub
Pages for months, with any transit times baked into the HTML. That is not caching, it is
indefinite republication of Directions content — and the page is public, so it is
republication to the world. No 30-day window survives that.

**The EEA terms are stricter still**, prohibiting use of Directions summaries, addresses or
steps *with any map* — which a page carrying a Leaflet map plausibly is.

**What this changes.** Phase 2 as drafted — fetch transit times, bake them into a published
static page — does not look compatible with Maps Platform terms at any price. The remaining
shapes:

1. **A different data source.** Direct GTFS feeds, or an aggregator over them, are the
   natural fit: many are open data under licences that permit redistribution. This is now
   the recommended direction and deserves its own research pass.
2. **Client-side fetch at view time, on a Google Map.** Satisfies the terms, but inverts the
   architecture — a key shipped to the browser, a Google map replacing Leaflet, and a page
   that is no longer self-contained. That contradicts this project's single-file output.
3. **Never publish the times.** Show them in `dev`/`eval` only and redact for `prod`, the
   way personal data already is. Cheap, and leaves the published artefact — the actual
   product — without the feature.

**Recommendation: do not buy a Maps Platform key for this.** Research GTFS-based sources
instead, as a separate note. Phase 1's approximate-durations model (open question 5) is
unaffected and remains the right near-term answer.

## 7. Risks and non-goals

### 7.1 Risks

**1 — Fabricated schedules published as fact.** The biggest by a wide margin, and the only
one that can strand a person. All three mitigations required: no clock times ever in Phase 1
(enforced by a stripping normalizer, not a prompt instruction); Format B as an expected
outcome rather than an error; `⚠ Unverified` plus explicit "confirm with the operator" text
on every card.

**2 — Schedule incoherence.** Concentrated in the `drive_time` coupling and the Stage 5b
overwrite (§4.1). Mitigated by the early return and an ordering-aware integration test.

**3 — Fabricated ticketing URLs.** Directly violates `design.md` §1.4 principle 1.
Non-negotiable: Phase 1 emits no URLs and the normalizer strips any returned. Note
`url_policy_blocked_classes` already blocks `google_maps_dir`.

**4 — Scope creep into `url_discovery.py`.** Corroborating operator names through search is
the obvious next step and the expensive one, in dollars and maintenance — that module is
~12.7k lines. Keep it behind a config flag, default off.

**5 — Maps Platform terms for Phase 2.** Attribution, caching limits, any display-on-a-map
requirement, applied to a static itinerary on GitHub Pages. Not researched — open question
#6. Note `_parse_route_info_from_maps_html` already scrapes Maps HTML today, a greyer
position than a licensed key; Phase 2 could plausibly *improve* this posture.

**6 — Template blast radius.** `templates/v2.5_template.html` is checksum-verified and owns
all CSS. A genuinely new card type means a template edit, checksum bump, and a brush with
the newline-normalization gotcha (`design.md` §4.1). **Mitigation: render Phase 1 entirely
inside the existing card/badge vocabulary and touch no template at all.**

**7 — `mixed` semantics undefined.** The issue's logic treats `transit` and `mixed`
identically. If they behave the same, one is dead config. Proposed split in open question #2.

### 7.2 Non-goals

- **No intra-destination transit** — local buses, park shuttles, gondolas. Shuttle
  requirements already surface through `expected_environment` and attraction
  `practical_note`s.
- **No booking, fares, seat availability, or real-time status.** The output is a static file
  with no server; a fare quoted at build time is wrong by the time it is read.
- **No replacement of `scenic_drives` or `has_high_clearance_vehicle`** (§4.4).
- **No change to booked legs** — `TRANSPORTATION_ITEM_SCHEMA`, `reservation_ingest` and the
  transportation pills are untouched (§3.3).
- **No routing for the departure leg** (`getting_there`). Different builder, different data
  shape. Deferred until the arrival side is proven.
- **No entity-registry participation.** Transit options are leg metadata, not renderable
  named entities competing for a section slot.
- **No new runtime dependency in Phase 1.** `transit_routing.py` needs `llm_client` and
  nothing else. Phase 2 needs an HTTP call, which `requests` already covers, plus a key.

---

## 8. Open questions for the project owner

1. ~~**Naming**, and do you want `legs:` at all?~~ **RESOLVED 2026-08-21: `transport_mode`,
   with `legs:`.** Destination-level `transport_mode` (matching the existing
   arriving-destination convention used by `en_route_seeds` and `transportation`), not the
   issue's `transport_mode_from_previous`. `legs:` is accepted alongside it, subject to the
   validation contract in §3.2 — `id` references, adjacency checks, and a raise on any
   disagreement between the two mechanisms about one leg.
2. ~~**What does `mixed` mean?**~~ **RESOLVED 2026-08-21.** As proposed: `transit` =
   transit replaces the car everywhere including the arrival-day schedule; `mixed` =
   transit options render *alongside* the drive, with `travel_time`/`distance_miles`
   untouched. **Plus:** where a reservation has been forwarded and ingested for a leg, use
   the booking and suppress option generation entirely for that leg — see §4.6.
3. ~~**Should a transit leg suppress en-route stop discovery?**~~ **RESOLVED 2026-08-21:
   yes.** Suppressed on `transit`, kept on `mixed`. The lost content section is accepted —
   the section was never applicable to a train leg. See §4.4.
4. ~~**Reuse `drive_time`, or introduce `travel_time`?**~~ **RESOLVED 2026-08-21:
   `travel_time`, renamed first as its own change.** The prior recommendation (reuse now,
   rename later) was conditional on transit being occasional; the owner's forecast is that
   transit is the direction of travel, which inverts it. Confirmed cheap: `drive_time` is
   generated content, absent from `manifest_parser.py`, so no user manifest migrates. See
   §4.1.
5. ~~**Is a Phase 1 with zero clock times acceptable?**~~ **RESOLVED 2026-08-21: yes —
   approximate durations only, with one exception.** Generated legs carry an approximate
   journey time and no clock times. **Ingested reservations are exempt:** a forwarded
   confirmation's `depart`/`arrive` are facts the traveller already holds a ticket for, not
   guesses, so they render as times. See §4.6 and the parsing consequence noted there.
6. **Phase 2 funding and terms.** ~~Not researched~~ — **researched 2026-08-21, and the
   answer is a problem. See §6.4.** Short version: Maps Platform terms appear incompatible
   with this product's publish-a-static-page model, independently of cost. Still open, but
   the question is now "what source instead?" rather than "is the key worth it?".
7. ~~**Scenic drives on a transit trip**~~ **RESOLVED 2026-08-21: keep.** A traveller who
   takes a train to Moab may still rent a jeep there. `has_high_clearance_vehicle` remains
   the orthogonal flag, and scenic drives are destination content, not leg content.
8. ~~**Which corridor is the acceptance case?**~~ **RESOLVED 2026-08-21: `Japan_manifest.yaml`,
   confirmed suitable.** This is acceptance testing — which real corridor proves the feature
   end to end. Bryce → Capitol Reef has no transit at all, so it exercises only the
   honest-negative path (Format B); shipping on that alone would mean never having run the
   success path. Japan's four legs are all rail-served with strong feed coverage:
   Shinagawa→Hakone (Odakyu Romancecar/JR), Hakone→Kyoto (Shinkansen via Odawara),
   Kyoto→Kanazawa (Thunderbird), Kanazawa→Tokyo (Hokuriku Shinkansen). Real, tight schedules
   are what will stress the parsing and scheduling work.
9. ~~**Should Phase 1 corroborate operator names through `url_discovery`?**~~ **RESOLVED
   2026-08-21: not in Phase 1.** Deferred to a future issue rather than built off-by-default,
   so Phase 1 carries no dormant cost lever.
10. **How should a multi-day sea leg be scheduled?** See §9. **Scope resolved 2026-08-21:
    not deferred — cruise manifests already contain these legs (most sail overnight), and
    one has already been ingested. The shape question in §9 remains open; the timing
    question does not.**

---

## 9. Multi-day carried legs (cruises, sleeper trains, ferries)

Added 2026-08-20. **In scope, not deferred — owner call 2026-08-21 (open question 10).**

`ship`, `ferry`, `bus` and `shuttle` are now accepted booked-leg types, so a
customer-arranged cruise renders correctly as a chip. That part was additive and is
done. **The scheduling model is not**, and it is a genuinely different problem from
transit routing.

**Why this cannot wait for a later phase.** Cruise manifests already contain multi-day
sea legs — most sailings run overnight, so the overnight leg is the normal case rather
than an exotic one. This is not speculative: a real cruise confirmation was ingested on
2026-08-21 and produced exactly these legs. The gap is live in data the pipeline is
already receiving, so deferring it means shipping a scheduler that is wrong on every
cruise itinerary it sees.

**These are booked legs, not routing options.** A traveler holds a confirmation
for a cruise; nothing here needs guessing, so §2's Phase 1/Phase 2 machinery does
not apply. They belong in `transportation` and always did.

**But they break two assumptions the schedule rests on.**

*Travel consumes part of one day.* §1.2's chain treats the inbound leg as a
subtraction from the arrival day's activity budget: arrival clock time is
`day_start + drive_minutes`, and the afternoon budget is
`activity_budget − drive_minutes`. A three-day repositioning sailing is not a
`drive_time` in any sense that arithmetic can absorb. Clamping it to a single
day's budget produces a nonsense arrival; leaving it out produces a page where
the traveler teleports between stops.

*Lodging and transport are separate things.* A cruise is **simultaneously
both** — the traveler sleeps aboard. The manifest models them as independent
blocks, so a sailing either duplicates as a lodging entry with no address (and
`lodging.location` is a geocoding anchor, so a blank one degrades routing and
the "restaurants near lodging" search), or it is absent from lodging entirely
and the stay looks unaccounted for.

**Three shapes, none obviously right:**

| Shape | Consequence |
|---|---|
| Days at sea become **destinations** with `lodging` pointing at the ship | Fits the existing model exactly; every downstream feature works. But it invites URL discovery, weather grounding and attraction search for a moving vessel — mostly meaningless, some actively wrong |
| A leg carries an explicit **duration in days**, and the schedule skips those days | Smallest schema change; the days simply do not exist in the itinerary. Loses the ability to say anything about them |
| A distinct **`carried` leg kind** that owns both travel and accommodation for its span | Most honest to what a cruise is; largest change, touching scheduling, lodging and rendering together |

Weather grounding is a further wrinkle under any of them: `expected_environment`
is derived per destination from monthly normals at fixed coordinates, and a ship
does not have fixed coordinates.

**Recommendation: do not guess.** The chip renders today, which covers the
common case of a cruise the traveler simply wants recorded. Deciding the
scheduling shape should wait for a real manifest that contains one — the same
discipline §2.2 applies to Phase 2, where a coverage probe precedes integration
rather than following it.

---

*Companion notes: `schedule-normalization.md` (the `drive_time` consumers) ·
`html-assembly-pipeline.md` (card rendering) · `reservation-email-ingestion.md` (booked
legs) · `search-provider-capability-probe.md` (measure the dependency first).*
