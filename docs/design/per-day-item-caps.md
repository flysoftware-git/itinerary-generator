# Per-Day Item Caps (Attractions, Restaurants, En-Route Stops)

## Purpose
This note documents the uniform "N items per day" ceiling applied to the
three lists `ai_content.py` normalizes per destination -- `top_attractions`,
`dinner_recommendations`, and `getting_here.en_route_stops` -- and why it
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
| Attractions | `_resolve_attraction_target` / `_apply_manifest_attraction_target` | `attractions_per_day` | 4/day |
| Restaurants | `_resolve_restaurant_target` / `_apply_manifest_restaurant_target` | `restaurants_per_day` | 4/day |
| En-route stops | `_resolve_enroute_target` / `_apply_manifest_enroute_target` | `en_route_stops_per_day` | 4/day |

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

## Why en-route stops still use a day-count basis
En-route stops conceptually belong to the single drive **into** a
destination, which happens once regardless of how many days the traveler
then stays -- so "days at the destination" is an imperfect scaling proxy
here, unlike attractions/restaurants which are genuinely consumed
day-by-day throughout the stay. A distance- or drive-time-scaled cap would
be conceptually cleaner, but `getting_here.distance_miles`/`drive_time` are
themselves AI-guessed at this point in the pipeline and already documented
elsewhere in `ai_content.py` (see `_override_grouped_child_distance_from_geocode`)
as sometimes wildly wrong (a real dipstick68 case rendered an 424-mph-implied
guess) -- building a new cap on top of a value already known to be
unreliable would trade one weak proxy for a shakier one. Day-count was kept
as the uniform basis per the explicit ask for one consistent "N/day" rule
across all three types; a longer multi-destination leg is also plausibly
correlated with a longer stay at the destination it delivers you to, so it
is not a pure mismatch either. If `getting_here` distance/time data becomes
reliably available before normalization runs (e.g. if the geocode-based
override in `_override_grouped_child_distance_from_geocode` is ever moved
earlier in the pipeline), revisiting this basis would be reasonable.

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
  at all in the schema (no `rating`, no `must_see`). Rather than fabricate a
  ranking heuristic from data that carries no real signal, the cap performs
  a **stable truncation**: non-seeded stops keep their existing relative
  order and are simply cut off at the target count; seeded stops are pulled
  to the front and always survive.

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

## Key implementation locations
- Caps and ranking: `generator/ai_content.py`
  (`_resolve_attraction_target`/`_apply_manifest_attraction_target`,
  `_resolve_restaurant_target`/`_apply_manifest_restaurant_target`,
  `_resolve_enroute_target`/`_apply_manifest_enroute_target`).
- Call-site wiring and the restaurant/schedule ordering fix:
  `_normalize_destination_content` in the same file.
- Manifest schema fields: `generator/manifest_parser.py`
  (`attractions_per_day`, `restaurants_per_day`, `en_route_stops_per_day`,
  both at `trip:` and per-destination level).
- Tests: `tests/test_ai_content_normalization.py`.

## Related documents
- `docs/design/building-attractions.md` -- attraction normalization/ordering
  detail; its "Quantity Guidance" section previously said no hard cap was
  enforced, which this pass changes.
- `docs/design/schedule-normalization.md` -- schedule generation this cap
  now runs ahead of for both attractions and restaurants.
- `docs/design/url-discovery-and-audit.md` -- see "Search-Result Cache
  Audit" for the companion cost investigation (search-result caching, not
  item counts).
