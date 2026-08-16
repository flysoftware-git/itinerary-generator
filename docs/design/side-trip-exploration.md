# Side Trip Exploration (GH #3)

Status: **spec only, not implemented.** No `side_trip_discovery.py` module,
no `side_trip_enabled` manifest field, no rendering, exist in the codebase
today. This document restates the original issue spec and adds design
context worked out in discussion with the project owner on 2026-08-16,
specifically the relationship to GH #68 (Multi-Site Destination Grouping)
and a scheduling gap the original spec doesn't cover.

## 1. Original spec (GH #3)

A destination-level opt-in for AI-suggested, non-committed nearby options:

```yaml
destinations:
  - id: nashville-outskirts
    name: "Franklin, TN"
    dates: "August 1-7, 2026"
    side_trip_enabled: auto   # true | false | auto
```

- `side_trip_enabled` resolution (`manifest_parser.py`, new
  `resolved_side_trip_enabled` field): explicit `true`/`false` always wins;
  `auto`/missing defaults to `false` for 1-2 day stays, `true` for 4+ days,
  `false` for exactly 3 days (conservative).
- New module `generator/side_trip_discovery.py`: given a destination's
  lat/lng, name, and stay length, identify nearby towns/districts/
  attractions within ~1 hour each way. **AI-only inference — no external
  APIs**, unlike every other discovery subsystem in this codebase
  (`url_discovery.py`'s attraction/trail/restaurant paths all do real,
  verified web search). Output:
  ```json
  {
    "base": "Nashville outskirts",
    "radius_minutes": 60,
    "options": [
      {"name": "...", "type": "attraction|town|district", "distance_minutes": 30, "notes": "..."}
    ],
    "fallback": "If options are sparse, remain at the base location."
  }
  ```
- `ai_content.py`: after base content generation, if
  `resolved_side_trip_enabled`, call `side_trip_discovery.generate_side_trips`
  and attach under `ai_content["side_trips"]`.
- `html_assembler.py`: new card, "Optional Side Trips (Within ~1 Hour)",
  rendered on the *existing* destination's page (not a new page/nav-tab) —
  flat list of name/type/distance/notes, plus the fallback paragraph if
  options are sparse.
- `html_validator.py`: non-blocking warning if
  `resolved_side_trip_enabled` is true and `ai_content["side_trips"]`
  exists but no `.side-trip-card` renders.
- Pipeline placement: a new stage after routing, before image generation;
  skipped entirely when `resolved_side_trip_enabled` is false.

Deliberately lightweight by design: no NPS resolution, no verified
attraction/trail/restaurant links, no schedule slot, no nav tab. It's a
suggestion card, not a destination.

## 2. Relationship to Multi-Site Destinations (GH #68)

Both features describe "a place near enough to a home base that the
traveler explores it and returns" — the same physical shape Moab/Arches/
Canyonlands has. It would be a mistake to treat them as unrelated just
because one predates the other in this codebase's history.

**Distance does not distinguish them.** This issue's own spec says "within
a one hour day trip (each way)." The GH #68 design doc's worked example
puts Arches and Canyonlands at "~30-40 min from the same base." Same
range. Geography is not the dividing line.

**The actual test is commitment status:**

| | Side trip (GH #3) | Multi-site destination (GH #68) |
|---|---|---|
| Traveler has decided to go | No — advisory | Yes — planned |
| Content fidelity | AI-only inference, unverified | Real geocoding, NPS resolution, verified attraction/trail links |
| Schedule slot | None — no `dates`, no day assignment | Own `dates` sub-range, own day(s) in the itinerary |
| Rendering | Single card on the base destination's existing page | Own page/nav-tab, visually clustered under the base |
| Ordering | Fully unordered — a browsable list | Dated, but *mutually* reorderable among siblings (hub-and-spoke, not a chain — see schedule-normalization.md's "Interaction with GH #68" section) |

A side trip is not a smaller multi-site destination and a multi-site
destination is not a bigger side trip — the same location (a neighboring
town outside Franklin, say) could legitimately be modeled either way
depending on whether the traveler has committed to it yet. That means
these are two points on one planning-maturity spectrum, not two disjoint
features:

```
  side-trip suggestion  --[traveler commits]-->  group_with entry
  (this doc, GH #3)                              (GH #68, real content, real date)
```

**Design implication**: don't build `side_trip_discovery.py`'s output in
a way that would make graduation expensive later. Concretely, an
`options[]` entry's `name` should be exact enough to become a `dates`-
bearing `group_with` child destination's `name` verbatim if the traveler
promotes it — this doc doesn't need to build that promotion workflow now
(explicitly out of scope), but the two schemas shouldn't need a rewrite
to connect if it's built later.

**UI implication**: keep the visual languages distinct on purpose. A
side-trip card should read as "browse, maybe" — no nav tab, no date, no
implied commitment. A grouped destination should read as "planned" —
its own tab, its own dated section, full content. Blending them (e.g.
giving a side-trip option its own mini-tab) would misrepresent unverified
AI suggestions as committed, verified plans.

## 3. Schedule clustering of remote options (gap, not yet covered by either design)

Owner's framing (2026-08-16), verbatim intent preserved: for a longer stay
(the example given: 7 days outside Nashville), the exact day a side-trip
option gets visited mostly doesn't matter ("if an attraction is visited on
the 3rd or 5th day may not matter") — but the *pairing* of options into
outings matters a lot: "you don't want to make multiple long-distance
drives when one would have done it." If two suggested options are both
~45 minutes out but in roughly the same direction from base, the right
recommendation is one combined outing, not two separate one-hour-each-way
trips on different days.

**Neither GH #3 nor this session's schedule-normalization work covers
this today.** Specifically:

- GH #3's `side_trip_discovery.py` output schema only carries
  `distance_minutes` — a base→option distance. It carries no
  option-to-option relationship (direction, shared area, or relative
  distance), so there's no data available to detect "these two are near
  each other" even if something wanted to.
- GH #3's spec never integrates `ai_content["side_trips"]` into the
  schedule at all — it's a static card, full stop. There is currently no
  concept anywhere in this codebase of a "recommended outing" that
  bundles multiple side-trip options into one day, let alone one that's
  clustering-aware.
- `schedule-normalization.md`'s new physical-reality model (Cases 1-6)
  governs how *destination* days are budgeted; it has no concept of
  optional, unscheduled side-trip content at all, since side trips
  (as spec'd) never get a `dates` entry or a day assignment to normalize
  in the first place.

**Tension worth flagging rather than resolving here**: real clustering
quality needs real geographic relationships between options (actual
distances/bearings between candidate towns, not just each one's distance
from base) — but GH #3 is deliberately AI-only, no external APIs, to keep
it cheap. An LLM can plausibly *guess* that two towns are "in the same
direction," but that's a materially weaker guarantee than the verified
geocoding the rest of this codebase relies on elsewhere (and this
session found real, costly bugs from trusting unverified LLM-only
geographic claims — see the Theme A wrong-geography en-route-stop
root-cause fixed earlier in `dipstick55_bug_triage.md`). Options for a
future implementation pass, not decided here:

1. Keep it AI-only, but explicitly ask the model to *group* options into
   suggested combined outings in its own output (a `clusters: [[...]]`
   field alongside `options[]`), accepting the same unverified-geography
   risk as the rest of GH #3 already accepts.
2. Geocode just the option *names* (a single lightweight coordinates
   lookup per suggested option, reusing `geocoder.py`) without going as
   far as full GH #68-style content verification, then compute real
   inter-option distances server-side for genuinely reliable clustering.
   Still much cheaper than promoting every option to a full destination.
3. Leave clustering entirely to the traveler's own judgment — render
   options with their individual base-distance and direction (e.g. a
   compass bearing or "north of base" label) and let a human do the
   pairing, rather than the system recommending combined outings at all.

## 4. Open questions for the owner

1. Should `side_trip_discovery.py`'s AI-only design (§1) be reconsidered
   given the clustering tension in §3, or is unverified-geography risk
   acceptable here specifically because nothing in this feature is a
   committed plan (unlike Theme A's real destinations)?
2. Is a "promote this side trip to a real destination" workflow (§2)
   worth scoping as a near-term follow-up, or should it stay a documented-
   but-unbuilt intention indefinitely?
3. Which clustering option in §3 (LLM-suggested groups, lightweight
   geocode-and-compute, or leave to the traveler) matches the intended
   product experience?

## Key files (once implemented)

- `generator/side_trip_discovery.py` (new, not yet created)
- `generator/manifest_parser.py` (`resolved_side_trip_enabled`)
- `generator/ai_content.py` (`ai_content["side_trips"]` attachment)
- `generator/html_assembler.py` (side-trip card rendering)
- `generator/html_validator.py` (non-blocking presence check)
- `docs/design/multi-site-destination-grouping.md` (GH #68, related — see §2 above)
- `docs/design/schedule-normalization.md` (GH #16, related — see §3 above)
