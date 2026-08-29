# Changelog

Notable user-facing changes to the itinerary generator.

This file starts at 2.2.0. Earlier releases were tagged only by their bump
commits (`2.1.0` 2026-08-20, `2.0.1` 2026-08-19, `2.0.0` 2026-08-18) and are
recoverable from git history; they are not reconstructed here rather than
risk describing them inaccurately.

Versions follow semver as applied to an application: **minor** for new
capability, new configuration surface, or a change in the shape of the
published artifact; **patch** for fixes that leave behaviour unchanged.
`__template_version__` tracks the frozen HTML template separately and does
not move with this number.

## 2.4.0 — 2026-08-29

25 commits since 2.3.0, from two parallel streams. One began with "the Europe
trip has blue links" and ended by finding that the quality gate had been
overreporting its own removal counts. The other began with refining a single
manifest into a real ten-day December itinerary, and found ten defects by
reading the output it produced.

### Added

- **Removed items are recorded by name** (`destination_status_report.json`,
  `.md`). The gate reported "restaurants removed for no verified URL: 39" and
  no artifact said which 39; the names were on the `_registry_decisions`
  records all along and were counted, then discarded. Each destination's
  `url_discovery` block now carries `removed_no_verified_url`, and the
  markdown summary lists them under a "Removed for No Verified URL" heading.
  This is the change that made the two fixes below findable.
- **Lunch-stop suggestions on long legs.** A driving leg past
  `en_route_stops.lunch_stop_min_drive_minutes` (default three hours) now says
  where to break. It never names a place of its own — it picks from en-route
  stops that already survived detour, proximity and URL verification,
  preferring a populated place over a park or a landform (you cannot eat lunch
  on a plateau), then a seeded stop, then whichever sits closest to halfway.
  With en-route discovery off it stays silent, which is the honest outcome.
- **The departure card carries mileage and duration badges.** `html_assembler`
  could always draw them; nothing ever set `getting_there.distance_miles` or
  `drive_time` for that leg, so the branch was unreachable and the card showed
  a bare label.
- **`scripts/sync_local_manifest.py`** generates a gitignored `.local` manifest
  from its public counterpart plus private substitutions, textually rather
  than through a YAML round trip — PyYAML would delete every comment, and the
  comments are the schema documentation. `--check` reports a stale file
  without writing.
- **Two coverage fixtures** (`manifests/alpine_grouped.yaml`,
  `tuning_surface.yaml`). A schema audit found twelve options no manifest set
  and no committed manifest that was non-US, grouped or a round trip. CI now
  dry-runs every committed manifest, which needs no keys and spends nothing.

### Fixed

- **A retried destination had its removals counted twice.** `url_discovery`
  appends to `_registry_decisions` and never resets, so a selective retry
  stacked a second set of records on the first. Old Hickory recorded 37
  removals for 20 distinct items; the duplicated destinations were exactly
  the retried ones. Every removal warning for a trip with unresolved
  destinations has been overstated.
- **An exhausted search balance reported as "no URL found".** Serper returns
  HTTP 400 for an empty balance — the same status as a malformed query — and
  the client logged the status while discarding the body that said why. Runs
  completed, validation passed, and items that were never searched for were
  dropped as unfindable. The body is now logged, and quota exhaustion is its
  own condition with one error naming the consequence.
- **Restaurant and en-route links rendered in default browser blue.** The CSS
  defined five link classes; the assembler emitted two. `.attraction-link`
  and `.restaurant-link` had never been emitted since the initial rebuild —
  duplicates of `.attr-link`/`.rest-link`, which is why a search for the
  styling always found a rule. Dead pair removed; a test now asserts in both
  directions that no class goes unemitted and none unstyled.
- **The email build used a frozen accent colour.** `email_safe.py` carried its
  own palette copy with terracotta pinned to Europe's rail blue, so every
  trip's email attachment rendered blue while its web page rendered its real
  `theme_color`. The accent is now read from the page being converted.

- **`trip.theme_color` had never been applied.** `html_assembler` substitutes a
  `<!--THEME_COLOR-->` placeholder that the template had stopped carrying, and
  a missing placeholder fails silently — every itinerary ever published
  rendered the same hardcoded terracotta regardless of what its manifest
  asked for. Restored, and extended to the six other places the colour was
  hardcoded a second time: the `theme-color` meta tag, the PWA manifest, the
  route polyline, the marker pins, the Tailwind palette entry, and the favicon
  SVGs, which are `data:` URIs and so take the hex without its `#`.
- **Restaurant name links had no colour rule at all.** `.rest-name` sets one
  but anchors do not inherit it — the UA stylesheet wins. A separate cause
  from the dead-class pair above, and present on every destination.
- **A leg nobody could drive.** Old Hickory → Asheville rendered as 304 mi in
  1 hr 30 min, an implied 203 mph. The correction that existed for this
  (written after a grouped day trip came back at 424 mph) was scoped to
  grouped children only. Ordinary legs are now checked too, but only when the
  stated pair is internally impossible — over a winding route a straight-line
  estimate is not obviously better than a good model answer.
- **A 17-mile airport transfer shipped as 95 mi / 2 hrs 15 min.** Internally
  consistent at 42 mph, and completely wrong: speed alone can never see a bad
  distance. The stated distance is now compared against the geometry. That leg
  was also skipped outright for having no origin, because the first stop
  departs from the trip gateway rather than from another destination.
- **Legs measured and labelled from the wrong town.** With grouped day trips
  between two lodging stops, both the distance correction and the rendered
  label took the previous list entry — so the final leg read "Leiper's Fork →
  Asheville" while its own prose said "Drive from Old Hickory". Both now walk
  back to the previous lodging stop.
- **Cultural events filtered against a single day.** A grouped day trip
  carries one date, and that date was read as the window an event must fall
  in — but the traveler is based nearby all week. Nashville in December had
  three events found and two dropped, rendering "no ticketed events were
  confidently verified" for a city at Christmas. Grouped entries now filter
  against the base's stay. Separately, an event that opened before arrival but
  runs through the stay is no longer dropped for starting early, and one
  starting after departure is no longer kept.
- **The same photograph, twice.** Commons served one image as a `.jpg` and as a
  `.tif` rendered to jpg — different files, different hash directories, no
  shared path component. Dedup by URL cannot see that; images are now deduped
  on the underlying file title.
- **A schedule that repeated itself.** The attraction-focus lookback was two
  periods — under one day — so an attraction named on Day 3 was named again on
  Day 4. Widened, but bounded by the pool size: a window as long as the
  attraction list makes every candidate ineligible and the fallback returns
  the very repeat it was avoiding.

### Changed

- `__template_version__` moves to **2.6**. The frozen template gained the
  restaurant-anchor colour rule and nine restored `THEME_COLOR` placeholders,
  so a page rendered by it is a different artifact from one rendered by 2.5 —
  and that version is stamped into the published page. The template filename
  is unchanged: only one template file has ever existed and it is a fixed
  path, not a version tag.
- The Europe manifest no longer carries attraction seeds. This clears the
  `Unverified` badge and also removes the content floor the seeds provided;
  both effects are real and were separated only after the fact.

## 2.3.0 — 2026-08-28

38 commits since 2.2.0. Driven by the first non-US itinerary — Brussels,
Amsterdam, Berlin, Prague and Frankfurt by rail — which exposed a pipeline
built throughout on the assumption of a US road trip.

### Added

- **Google Places as a restaurant filter** (`places_filter.py`). Decides which
  of our own candidates survive; publishes no Places field. The field mask
  requests `id`, `displayName` and `priceLevel` only — `websiteUri`, `rating`
  and `photos` are not requested at all, which is a stronger guarantee than
  choosing not to render them. Rejects every Michelin entry the prompt could
  not: Ciel Bleu, Rutz, Tim Raue, Comme Chez Soi, La Dégustation.
- **Transit travel-time estimates** (`transit_estimate.py`) via Google Routes,
  for booked rail, ferry and bus legs. Brussels Airport to Brussels reads 29
  min / 10 mi, where the invented driving figure claimed 2 hrs 15 min / 95 mi.
  Labelled as estimates: transit times move with the timetable.
- **`index-email.html`**, a script-free copy written on every run. Gmail
  rejects the normal file as a virus — nothing malicious in it, but three
  remote `<script src>` loads in an HTML attachment is the shape generic
  heuristics score as `Trojan:HTML/Phish`.
- **CLI switches in both directions** for trails, events, en-route stops and
  restaurants. They could previously only be turned off.
- **`manifests/europe_cities.yaml`** and **`manifests/old_hickory.yaml`**, a
  five-city rail itinerary and a single non-park destination, both cheap to run.

### Changed

- **En-route stops are suppressed for non-driving arrivals.** They are a
  road-trip concept: on a booked train there is no roadside to stop at, and 14
  of 41 stops were being removed for lacking a verified URL on an all-rail
  trip.
- **Getting Here follows the booked mode.** The prompt asked for highways and
  parking unconditionally, producing "Take the E19 from Antwerp" for an
  8-mile airport train. Maps links use `travelmode=transit` per leg.
- **Restaurant candidates 8 → 20.** Every gate downstream had grown stricter
  while the ask stayed put; Frankfurt asked for 8, had 7 rejected, published 1.
- **Rating floor is budget-aware** — 4.3 by default, 4.0 on a low-cost brief,
  since 4.3 skews toward destination restaurants.

### Fixed

- **The budget was ignored.** `"low-cost"` matched none of the budget keywords,
  the cap ran before the batch replaced the list, and the fine-dining
  instruction lived in four prompts of which one had been edited. `$$$$`
  entries went from 15 to 0.
- **Markdown reached the page** as `**Flat Tire Diner**` — and reached Places
  queries, where it flipped a two-star restaurant to "affordable".
- **Cuisine badges rendered addresses** (`Pflugstrasse 11`, `Photos & …`).
  Validated as a food style now, rather than blocklisted as a place name.
- **The Full Route Map showed one leg.** Transit mode cannot carry waypoints,
  so the overview link is driving; per-leg links stay transit.
- **Duplicate restaurant URLs** — one page published under two names.
- **An unresolvable departure killed the whole run** with a traceback, for a
  feature that only refines a map.
- **The Route Overview heading** was the only one with inline styles, and so
  the only one a palette change would not reach.

### Known

- Cached batch rows are keyed on the rendered query, but only for restaurants.
  An edit to the attraction prompt is still invisible until the cache clears.
- Dining sections remain thin in cities where the batch returns little that
  survives filtering.

## 2.2.0 — 2026-08-25

59 commits since 2.1.0. The theme is per-run cost: a like-for-like run fell
from **$6.32 to $2.82 (55%)**, with a Core-only run at **$1.18**.

### Added

- **Serper as a search provider** for per-item URL discovery, replacing an
  LLM used as a search engine. New `SERPER_API_KEY`, new `nonbatch_search_provider`
  config. The fallback cost **$1.65 → $0.036** and content quality improved.
  The Grok batches remain — they supply ratings and descriptions Serper does
  not carry, on every row.
- **Independent category switches** for `trails`, `en_route_stops`,
  `cultural_events` and `restaurants`, each with an `enabled` flag. This is
  the basis of the Core/premium split; development runs default the optional
  ones off. Switches are enforced at the discovery chokepoint rather than per
  call site, after four earlier per-site attempts each left a leak.
- **`place_resolver`** module, so secondary map links can name a place
  instead of describing it.
- **Content metrics and per-call-site cost attribution**, so a cost change
  can be checked against what the run actually produced rather than against
  the total alone.
- **Change-outcome tracking** (`docs/reports/change-outcomes.jsonl`), which
  measured that 67% of defects in this work escaped 1400+ passing tests.

### Changed

- **Images are published at their source URLs**, not copied from the local
  build cache. `images/` is a download-avoidance cache and was never the
  delivery mechanism; every build had been shipping its own copy of NPS,
  Wikimedia and Unsplash assets — 37 files, one of them 33 MB. The service
  worker now precaches those URLs individually at install, so installed
  itineraries still work offline.
- `grok_max_concurrent_destinations` **1 → 2**, after a measured 2.7×
  wall-clock improvement at width 3 with no circuit-breaker activity.
  Deliberately 2, not 3: the evidence was three small calls, not real Stage 3
  load.
- Direct-batch item count **20 → 12** (measured −3.6%, not the −20%
  predicted — item count drives output tokens, which are 8.6% of the total).
- Attractions **4 → 5/day**, scenic drives **2 → 1/day**, favouring trails.
- Cultural events default to **off**.
- Product renamed to **itinerary-generator**.

### Fixed

- **Grok pricing was 9× wrong** (`grok-4-fast` listed at $0.20/$0.50 against
  a real $2.00/$6.00), which is why reported spend never matched the bill.
  Unpriced models now warn instead of silently reporting $0.00.
- The cultural-events search model fell through to `XAI_MODEL`/`grok-latest`
  instead of the pinned catalog model (#65/#64).
- Hike names were being sent to the attraction batch, whose prompt excludes
  hikes; they now route to the trail batch.
- Drive time leaned short, and a test was holding the lean in place.
- Grok content-generation read timeout is now sized to the call.
- Reservation ingestion: PDF attachments are read, rasterized PDFs are
  reported rather than returning silently empty, IMAP is addressed by UID
  rather than sequence number, and Gmail app passwords are accepted with the
  spaces Google displays.

### Known

- Google Maps Platform remains unusable for both directions and imagery —
  not on terms, but because it assumes a live authenticated application and
  this product publishes a static artifact. See
  `docs/design/per-item-imagery.md`.
- Images are still fetched per destination, not per item, so several
  attractions in one stop share a photo. Scoped in the same note; free
  sources cover 91% of items.
