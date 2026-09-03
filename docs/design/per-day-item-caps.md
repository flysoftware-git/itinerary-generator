# Per-Day Item Caps (Attractions, Restaurants, En-Route Stops, Scenic Drives)

## Purpose
This note documents the uniform "N items per day" ceiling applied to the
lists `ai_content.py` normalizes per destination -- `top_attractions`,
`dinner_recommendations`, `getting_here.en_route_stops`, and (added in a
later pass, see "Scenic-Drive Cap" below) `scenic_drives` -- and why it
exists.

## Why: cost, not just content quality
Real xAI billing for this app runs well above the app's own token-based cost
estimator, because the estimator (before a same-night instrumentation fix)
didn't account for Grok's per-call web_search tool fee. Once that fee was
added to the estimate, a real multi-destination run priced out around
**$2.46**, of which **~83% (~$2.05) was the web_search tool fee itself**, not
token cost (see `C:\Temp\RoadTripRuns\SW2026-dipstick73\run-console.log`:
`grok/grok-4-fast: calls=174 ... est=$2.4366 web_search_calls=411
tool_fee=$2.0550`).

`url_discovery.py` never decides on its own how many attractions/restaurants/
en-route stops to go search for -- it only ever searches whatever
`ai_content.py`'s normalization step hands it in `top_attractions`,
`dinner_recommendations`, and `getting_here.en_route_stops`. So the single
highest-leverage lever for cutting the number of paid `web_search` calls is
capping those lists at generation/normalization time, before URL discovery
ever runs. That's what this mechanism does.

## The uniform rule
As of this pass, all three item types share the same default: **4 items per
day**, where "day" is the arriving destination's inferred day count
(`_infer_day_count`, parsed from the manifest `dates` string, clamped to
1-5 days). A destination's effective cap is `items_per_day * day_count`.

| Item type | Method pair | Manifest field (trip/destination level) | Default |
|---|---|---|---|
| Attractions | `_resolve_attraction_target` / `_apply_manifest_attraction_target` | `attractions_per_day` | 5/day |
| Restaurants | `_resolve_restaurant_target` / `_apply_manifest_restaurant_target` | `restaurants_per_day` | 4/day |
| En-route stops | `_resolve_enroute_target` / `_apply_manifest_enroute_target` | `en_route_stops_per_day` | **4 flat, not scaled** |
| Scenic drives | `_resolve_scenic_drive_target` / `_apply_manifest_scenic_drive_target` | `scenic_drives_per_day` | **1/day** |

Scenic drives are the exception to the shared default -- see
"Scenic-Drive Cap" below for why they are capped hardest, and where the cap
is actually applied
(not in the same `_normalize_destination_content` pipeline as the other
three).

All three live in `generator/ai_content.py`. The manifest fields mirror
`attractions_per_day`'s existing pattern exactly: optional, numeric, settable
at `trip:` level (default for the whole itinerary) or overridden per
`destinations[].` entry; an explicit destination-level value always wins over
the trip-level default.

### Concrete example (real `sw_manifest.yaml`, `C:\Dev\Sandbox\sw_manifest.yaml`)
- **St. George, Utah** -- 1-day stopover (`dates: "October 17, 2026"`) ->
  `day_count=1` -> cap = `4 * 1 = 4` per list.
- **Bryce Canyon National Park** -- 3-day stay (`dates: "October 19-21,
  2026"`) -> `day_count=3` -> cap = `4 * 3 = 12` per list.

Before this pass, attractions used a default of 2/day (6 for Bryce, 2 for
St. George); restaurants and en-route stops had no per-day cap at all --
whatever the AI generated (typically 4-6 restaurants, 2-4 en-route stops
per the prompt's own quantity guidance in `prompts/destination_content.txt`)
went straight to URL discovery regardless of trip length.

## Seed/protected-item preservation
All three caps guarantee a manifest-seeded item is never evicted, matching
the pre-existing attraction behavior:
- Attractions: `dest.seeds` (existing).
- En-route stops: `dest.en_route_seeds` (existing manifest field, now wired
  into the cap for the first time).
- Restaurants: the cap function accepts a `protected_names` parameter for
  structural symmetry with the other two, but **no manifest field seeds
  restaurants today** -- there is no `dinner_seeds`/restaurant-seed concept
  anywhere in `manifest_parser.py`'s schema, unlike `seeds`/`en_route_seeds`.
  Callers currently pass no protected names for restaurants; the parameter
  exists so a future manifest field could plug in without a signature change.

## Update: en-route stops moved to a flat cap (not day-scaled)
The section below documents the *original* rationale for keeping en-route
stops on the shared day-count basis despite the conceptual mismatch already
flagged at the time. That decision was reversed in a later same-night pass.

Real trigger: a 3-day destination's arrival leg could carry up to `4 * 3 =
12` en-route-stop candidates under the day-count formula -- comfortably
exceeding Google's own 8-waypoint cap on the public
`maps/dir/?api=1&waypoints=...` URL scheme (`_build_route_gmaps_url`,
`generator/html_assembler.py`). Stops beyond the 8th rendered as cards in
the itinerary with no corresponding pin on the overview/route map at all --
a real card/map desync the project owner caught from a live screenshot.
Project owner: "Can we prioritize the enroutes to keep it to the top 4 or
less? Could also save calls."

`_resolve_enroute_target`/`_apply_manifest_enroute_target` now use a FLAT
target of 4 (still overridable via the same `en_route_stops_per_day`
manifest field, kept for call-site continuity even though the name no
longer implies a per-day multiplier) -- `target = max(1,
int(en_route_stops_per_day))`, no `* day_count`. This keeps every surviving
en-route-stop card comfortably under the map's own 8-waypoint limit, and
since this runs before `url_discovery.py` ever searches for a link per
candidate, fewer surviving candidates also means fewer paid `web_search`
calls -- unlike a hypothetical post-search prioritization pass, which would
only change what renders, not what got searched.

### Original rationale (superseded by the above, kept for history)
En-route stops conceptually belong to the single drive **into** a
destination, which happens once regardless of how many days the traveler
then stays -- so "days at the destination" is an imperfect scaling proxy
here, unlike attractions/restaurants which are genuinely consumed
day-by-day throughout the stay. A distance- or drive-time-scaled cap would
be conceptually cleaner, but `getting_here.distance_miles`/`travel_time` are
themselves AI-guessed at this point in the pipeline and already documented
elsewhere in `ai_content.py` (see `_override_grouped_child_distance_from_geocode`)
as sometimes wildly wrong (a real dipstick68 case rendered an 424-mph-implied
guess) -- building a new cap on top of a value already known to be
unreliable would trade one weak proxy for a shakier one. Day-count was kept
as the uniform basis per the explicit ask for one consistent "N/day" rule
across all three types; a longer multi-destination leg is also plausibly
correlated with a longer stay at the destination it delivers you to, so it
is not a pure mismatch either.

## Ranking basis differs per type (and why)
- **Attractions**: `must_see` first, then `rating`/`votes` (from
  `top_attractions`, which the prompt schema populates with a `must_see`
  boolean, though not always a rating), then a difficulty/name tie-break.
  Unchanged by this pass except the default target (2 -> 4/day).
- **Restaurants**: `dinner_recommendations` has no `must_see` field in the
  prompt schema (`prompts/destination_content.txt`). Ranks by `rating`/
  `votes` when present (gracefully degrading to 0, same as attractions),
  then alphabetically by name for a deterministic trim.
- **En-route stops**: `getting_here.en_route_stops` has no quality signal
  in the schema (no `rating`, no `must_see`), but it does have a real,
  always-available, pre-search quantity: `detour_distance_miles` (the AI's
  own self-reported estimate, requested by `prompts/destination_content.txt`
  for every candidate and defaulted to `0` by `_normalize_getting_here` when
  absent). A shorter detour is objectively more worth keeping for a
  "can't-miss" quick stop -- the same real-world quantity
  `DEFAULT_EN_ROUTE_DETOUR_MAX_MILES` (`generator/url_discovery.py`, a
  same-night fix elsewhere) already uses to reject stops outright once real
  geocoded detour metrics exist; this reuses the same intuition earlier in
  the pipeline, on the AI's own self-reported estimate, to choose among too
  many otherwise-plausible candidates. Non-seeded stops are sorted ascending
  by `detour_distance_miles` before truncation (a stop whose figure doesn't
  parse as a real number sorts last, not first); seeded stops are pulled to
  the front and always survive regardless of detour length.

## Ordering fix bundled into this pass: restaurants must be trimmed before schedule generation
`_normalize_destination_content` (in `generator/ai_content.py`) builds
`possible_daily_schedule` via `_normalize_schedule`, which reads whichever
restaurant list it's handed to fill in an unnamed "dinner" mention in a
schedule period's summary text (see `_normalize_schedule`'s internal
`clean_text` closure: `"dinner at {restaurant_names[0]}"`).

Attractions were already safe: `_apply_manifest_attraction_target` runs
*before* the `_normalize_schedule` call, so the schedule only ever sees the
already-capped `top_attractions` list. Restaurants were not -- their
normalization (`_normalize_restaurants`) previously ran *after*
`_normalize_schedule`, which meant the schedule could reference (via the
`clean_text` filler above) a restaurant that the not-yet-applied cap was
about to drop from the final `dinner_recommendations` output entirely --
an itinerary naming a dinner spot that never appears anywhere else on the
rendered page.

This pass reorders `_normalize_destination_content` so restaurant
normalization *and* the new per-day cap both run before the
`_normalize_schedule` call, exactly like attractions. This intentionally
does **not** rely on `reconcile_schedule_from_registry`
(`generator/entity_registry.py`) -- that mechanism exists to clean up
rejected/dead URLs discovered *after* URL discovery runs, a different
concern from schedule-generation-time list trimming, and running before
`_normalize_schedule` in the first place is strictly simpler than trimming
and then trying to detect/repair a stale reference after the fact.

Restaurant normalization itself (dedup, chain/fast-food filtering, budget-
tier filtering -- see `_normalize_restaurants`) is unchanged; the new cap
is applied as the final step, on top of whatever survives that existing
logic, not instead of it.

## Why `url_discovery.py`'s own items-per-day mechanisms don't fight this cap
`url_discovery.py` has its own, separate "items per day" concept used during
direct-batch harvesting -- but it's a **floor** (pads a sparse AI-generated
list up to a minimum), not a ceiling, and stays below the new 4/day cap in
every case that was traced:

- `_prioritize_direct_batch_attractions` (`DEFAULT_ATTRACTION_DIRECT_BATCH_ITEMS_PER_DAY = 3`):
  `target_total = max(len(existing), items_per_day * day_count)`. Since the
  new cap already allows up to `4 * day_count` attractions, and the floor
  only asks for `3 * day_count`, `len(existing)` already meets or exceeds
  the floor's target whenever the AI generated close to the cap, so
  `additional_slots <= 0` and nothing extra gets added.
- `_prioritize_direct_batch_restaurants` (`DEFAULT_RESTAURANT_DIRECT_BATCH_ITEM_COUNT = 4`)
  and `_prioritize_direct_batch_en_route_stops`
  (`DEFAULT_EN_ROUTE_DIRECT_BATCH_ITEM_COUNT = 4`) both go through
  `_primary_list_target_count`, which is a **flat** floor (not day-scaled):
  `target_count = max(current_count, min(fallback_count, available_count))`.
  A flat floor of 4 is at or below the new cap for a 1-day destination and
  strictly below it for any multi-day destination, so it can only ever
  raise a sparse list up toward the cap, never push it over.

No changes were made to any of these three functions or their constants.

## Scenic-Drive Cap
A later same-night pass added a fourth cap, `scenic_drives`, at the
project owner's explicit request to "cap scenic drives and day trips at
2/day, if it would meaningfully reduce search calls." This section
documents the real measurement behind both the "yes, add it" decision and
the honest caveat about how much it actually saves.

### Measured first: scenic drives ARE a real, structural cost driver
`url_discovery.py`'s `_discover_scenic_drives` has **no direct-batch
harvest fallback at all** -- unlike attractions/restaurants/en-route stops
(each backed by `_get_attraction_direct_batch_rows_for_destination` /
`_get_restaurant_direct_batch_rows_for_destination` /
`_get_en_route_direct_batch_rows_for_destination`, a single shared HTML
harvest fetch per destination that resolves most items with zero
incremental search cost), every scenic drive always falls through to an
individual `_search_first` call. The one deterministic shortcut that
exists (`nps_deterministic_accepted`, an NPS park's own scenic-drive page
matched without a search) fired **zero times** across all ten real runs
inspected (`C:\Temp\RoadTripRuns\SW2026-dipstick64` through
`SW2026-dipstick73`).

Slug-matching each run's `[scenic-drive-...|reason=discovery_completed]`
decision lines against their corresponding `[search-...|reason=...]`
outer search-call lines (same dest+item slug) confirmed **100% of scenic
drives in every run required an individual live search** -- e.g. 21 of 21
in dipstick73, 20 of 20 in dipstick71/72. Those individual scenic-drive
searches alone accounted for roughly **14-19% of all outer `_search_first`
invocations** in a run (e.g. dipstick73: 21 of 110 total outer search
calls), despite scenic drives being a small item category (2-4 per
destination). This confirms scenic drives are structurally more
expensive *per item* to discover than attractions/restaurants -- the
missing direct-batch fallback, not raw item count, is the real driver.

### Measured second: a 2/day cap's actual yield is modest, because AI generation is destination-scoped, not day-scoped
`prompts/scenic_drives.txt` itself instructs "Include 2-4 entries per
destination" -- with no day-count scaling in the prompt at all. Real
output matches: across all 10 destinations x 10 runs (100
destination-instances), the generated count was almost always exactly 2,
occasionally 3-5, and this did not vary by how many days the destination
lasted (a 3-day Bryce Canyon stay generated the same 2-4 drives as a
1-day St. George stopover).

Applying the day-scaled `2 * day_count` cap retroactively against all 10
real runs' actual per-destination counts (day counts from
`C:\Dev\Sandbox\sw_manifest.yaml`: St. George/Zion/Arches/Canyonlands =
1 day each -> cap 2; Capitol Reef/Pagosa Springs = 2 days -> cap 4;
Bryce/Moab/Telluride/Santa Fe = 3 days -> cap 6) found the cap would have
trimmed **20 of 261 total scenic-drive searches (7.7%)**, averaging **~2
searches saved per run**. Every single overage happened at a **1-day**
destination (St. George, Zion, Arches, Canyonlands) where `cap=2` is
tight; not one multi-day destination ever generated enough drives to hit
its own (much looser) day-scaled cap, because the AI's 2-4-per-destination
habit stays flat regardless of stay length. At the run's average
web_search-call multiplier, ~2 saved outer calls corresponds to roughly
**1-2% of a run's total paid web_search-call volume** -- real, but not
close to being a primary lever toward the project owner's <$1 target on
its own.

### Decision: built anyway, with the yield reported honestly
Per the investigation's own framing ("is scenic-drive discovery a
meaningful cost contributor"), the category qualifies -- the missing
direct-batch fallback makes every scenic drive item structurally
expensive, and 14-19% of a run's outer search-call volume is not
negligible. The cap was implemented (mirroring the existing
`_resolve_*_target`/`_apply_manifest_*_target` pattern exactly, at
**2/day, half the other three types' 4/day default** per the project
owner's explicit ask) because it is zero-risk -- it only ever trims a
destination's scenic-drive list down to a data-driven ceiling that real
runs already sit at or under in the overwhelming majority of cases, with
no seed-eviction risk (see below) -- even though its measured yield
(~2 calls/run, ~1-2% of total search-call volume) is modest rather than
transformative. The real lever for scenic-drive cost would be adding a
direct-batch harvest fallback for this category, mirroring attractions/
restaurants/en-route stops -- a materially larger architectural change,
out of scope for this pass.

### No seed concept
Unlike attractions (`dest.seeds`) and en-route stops
(`dest.en_route_seeds`), scenic drives have no manifest-seed field
anywhere in `manifest_parser.py`'s schema. `Sandbox/sw_manifest.yaml`'s
own top-of-file schema notes say so explicitly: "scenic drives: FULLY
AI-DISCOVERED -- not seeded here." `_apply_manifest_scenic_drive_target`
accepts a `protected_names` parameter for structural symmetry with the
other three cap functions, but no caller passes anything into it today.

### Where the cap runs (different from the other three)
Attractions/restaurants/en-route stops are capped inside
`_normalize_destination_content`, which processes `destination_content`
(`dest["ai_content"]`). Scenic drives are generated and attached
separately -- `dest["scenic_drives"] = bundle["scenic_drives"]` inside
`generate_destination_content`'s own per-destination closure, not inside
`_normalize_destination_content` -- so the new cap is applied there
instead, immediately after the existing markdown-name-scrub call and
before `discover_all` ever runs. `normalize_trip_content`'s existing
`_filter_oversized_scenic_drives`/`_filter_departure_aligned_drives`/
`_deduplicate_cross_destination_scenic_drives` were deliberately left
alone as the wrong place for a search-cost-saving cap: they run *after*
`discover_all`, so trimming there only cleans up already-paid-for search
results, exactly the same "too late to save cost" problem the restaurant/
schedule-ordering fix (above) solved for restaurants.

### Ranking basis: stable truncation, order-preserving
`scenic_drives` has no `rating`/`votes`/`must_see` field, like en-route
stops -- but unlike en-route stops, list order is meaningful by the
prompt's own convention: `prompts/scenic_drives.txt` instructs "For
destinations with a well-known named drive ... that drive is always the
first entry." A stable truncation that preserves existing order (rather
than a fabricated ranking heuristic) is therefore not just the safe
default, it directly respects that primacy convention.

## The restaurant cap did not govern the published list (2026-09-02)

`restaurants_per_day * day_count` was applied in `ai_content` to the
**AI-generated** list. With `restaurant_source: direct_link_batch` the
published list is the batch's instead — a flat
`restaurant_direct_batch_item_count` (20) per destination regardless of stay
length — written to `dinner_recommendations` after the cap had already run on
a different list.

It stayed invisible because verified-link-or-seed was discarding 60-77% of
candidates, so the surviving count landed near the target by accident. Once
link discovery improved, a two-night stop at Capitol Reef published 19 dinner
recommendations and Bryce and Santa Fe published exactly 20 — the batch count.
The owner spotted it and correctly guessed the threshold had never been
exercised.

`_enforce_restaurant_per_day_cap` (main.py) now applies it after discovery,
the audit **and registry reconciliation** — the first point where the list is
final whatever produced it. Ordering is the whole fix: the first attempt ran
before reconciliation, which rebuilds `dinner_recommendations` from the
registry, so the trim was silently overwritten by the pre-trim snapshot and
the run reported nothing removed. A test asserts the ordering by source
position.

Selection keeps range rather than the top N by rating, which would answer 18
candidates with eight variations on one expensive bistro: best of each
distinct cuisine, then best of each price level not yet represented, then
best-rated to fill. Blank values do not claim a diversity slot, and the
incoming page order is restored so trimming does not re-sort the section.

Note when measuring this: a rendered section is not a destination. Old Hickory
groups five destinations under one section, so 26 restaurants there is six
destinations each within its own target, not one destination over.

## Key implementation locations
- Caps and ranking: `generator/ai_content.py`
  (`_resolve_attraction_target`/`_apply_manifest_attraction_target`,
  `_resolve_restaurant_target`/`_apply_manifest_restaurant_target`,
  `_resolve_enroute_target`/`_apply_manifest_enroute_target`,
  `_resolve_scenic_drive_target`/`_apply_manifest_scenic_drive_target`).
- Call-site wiring and the restaurant/schedule ordering fix:
  `_normalize_destination_content` in the same file. The scenic-drive cap
  is wired separately, in `generate_destination_content` (see "Where the
  cap runs" above).
- Manifest schema fields: `generator/manifest_parser.py`
  (`attractions_per_day`, `restaurants_per_day`, `en_route_stops_per_day`,
  `scenic_drives_per_day`, all at both `trip:` and per-destination level).
- Tests: `tests/test_ai_content_normalization.py`.

## Related documents
- `docs/design/building-attractions.md` -- attraction normalization/ordering
  detail; its "Quantity Guidance" section previously said no hard cap was
  enforced, which this pass changes.
- `docs/design/schedule-normalization.md` -- schedule generation this cap
  now runs ahead of for both attractions and restaurants.
- `docs/design/url-discovery-and-audit.md` -- see "Search-Result Cache
  Audit" for the companion cost investigation (search-result caching, not
  item counts), and "Predictive No-Verified-URL Skip Investigation" for a
  related same-night investigation (evaluated, not built) into predicting
  and skipping searches likely to fail before spending the call.

## Revision: 2026-08-21 (attractions 4 -> 5, scenic drives 2 -> 1)

Both changed together at the owner's request, to favour trails over scenic
drives. The pairing is deliberate: dropping scenic drives frees more search
budget than raising attractions spends, because the two types have very
different costs per published item.

Measured on the 2026-08-21 cold-start run (`coldstart-cal`, sw_manifest):

| Type | Slots | Published | Search cost |
|---|---|---|---|
| Attractions/trails | 80 (4/day x 20 days) | ~30 | direct batch, ~5 candidates per call |
| Scenic drives | 40 (2/day x 20 days) | 9 | one dedicated live call each |

9 scenic drives consumed ~9 dedicated calls; 10 trail batch calls returned 50
candidate rows. Roughly **5x the search cost per published item**, which is
why this type is capped hardest rather than merely lower.

### Do not expect the attraction raise to add much on its own

The same run shows the attraction ceiling was **not the binding constraint**:
80 slots available, ~30 published. What limits attraction volume today is
candidate generation plus the verified-link-or-seed policy, which removed 23
attractions for lacking a verified URL (of 13 logged rejections, 8 were
nps.gov pages failing the promise-to-target check). The raise to 5/day only
helps destinations that actually reach the ceiling -- short stopovers, where
day_count is 1.

> **See also:** `cost-accounting-and-reduction.md` — the per-day caps are one cost lever among several, and that note carries the measured spend breakdown they were tuned against (URL discovery is 91% of tokens; these caps bound what discovery is asked to find).
