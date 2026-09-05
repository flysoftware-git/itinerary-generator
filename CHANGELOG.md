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

## 3.0.0 — 2026-09-05

19 commits since 2.7.0, plus the merge that brings `v2` in. `__template_version__`
2.5.5 -> 2.5.7. First release on the `v3` line.

**Major, and the reason is the mainline rather than a broken API.** Nothing here
forces an existing manifest to change: every new key is optional and a manifest
that sets none behaves exactly as it did, which is asserted rather than assumed.
The number moves because the published artifact and the manifest surface both
grew a dimension they did not have — an itinerary no longer assumes the traveler
is driving — and because that is the change worth naming when someone asks what
`v3` is.

### Added

- **Multimodal legs (GH #2).** `transport_mode` says how the traveler covers the
  ground between two stops: `auto | transit | mixed | bike | hike`, set trip-wide
  or per destination, with a `legs:` list as an alternative spelling for authors
  who think in journeys rather than in stops. Omitted, everything behaves as
  before.

  Every way of getting it wrong raises rather than falling back to a drive. An
  unknown destination id, a leg that is not adjacent, the same leg twice, or a
  `legs:` entry disagreeing with a destination's own `transport_mode` — each is a
  build failure naming the offending leg. A silently ignored leg ships a traveler
  an itinerary telling them to drive a leg they have no car for, and a build
  failure costs thirty seconds.

- **Phase 1 transit options**, AI-generated and limited by construction to what a
  model can be right about: a corridor description, a duration band, a transfer
  count, a search phrase. Clock times and booking links are stripped in code
  rather than discouraged in the prompt. "No scheduled service connects these
  stops" is a first-class answer, not a degraded one — on remote corridors it is
  usually the true one.

- **`bike` and `hike`, which are not transit.** Nobody operates them, so no
  options are generated and no provider is called. They change the leg's
  duration, its map link (`bicycling`/`walking`, keeping their waypoints where
  transit drops them), the card's heading, and what the model is told to
  describe. En-route stops stay on: a cyclist stops more often than a driver, not
  less.

- **Real durations from Google Routes**, in the travel mode the leg actually is.
  On a self-powered leg spanning days the API's continuous-travel figure is
  divided by `default_daily_activity_hours` and reads "about 6 days" rather than
  "45 hrs 55 min" — true, unusable, and precise enough to look plannable.

- **`trail_name`, `trail_section` and `trail_url`** link a walked or ridden leg to
  its own page in a trail catalogue. The URL may be authored: `design.md` §1.4
  bars the *model* from producing one, not the human, and a catalogue's own
  titles and slugs can disagree badly enough that no search phrase resolves them.

- **`scripts/probe_transit_coverage.py`**, committed rather than described. It
  asks whether the Routes API knows anything about a manifest's corridors before
  anything is built on the assumption that it does.

### Fixed

- **`getting_here.drive_time` is now `travel_time`.** Not a badge: the schedule
  normalizer derives arrival clock time and the whole arrival-day activity budget
  from it, so a car estimate left there produced a page showing a 3h15 bus in one
  card and scheduling a 2h drive in the next. Renamed on unchanged all-car
  behaviour, ahead of the routing work, with one permanent tolerance at the model
  boundary. No manifest migrates — the field was never a manifest input.

- **Road geometry no longer writes to a leg that is not a road leg.** Three paths
  did: the model was never told the leg was transit, the implausible-leg
  corrector recomputed it at 60 mph, and stage 5b overwrote it from a scraped
  driving duration. All three now key on the resolved mode, with a backstop that
  leaves a leg blank rather than guessing.

- **The overview map shipped watermarked.** CARTO's keyless raster endpoint now
  stamps "API KEY REQUIRED" across every tile; reverted to OpenStreetMap. Labels
  therefore render in the local language again — unsolved without a keyed
  provider, and recorded in the template so it is not retried blind.

### Known limitations

- Distances on `bike`/`hike` legs are road distances. Google routed 123 miles
  where the trail covers 82.
- `multimodal-routing.md` §9's multi-day-leg scheduling is open. A thru-hike
  manifest is made entirely of such legs, so its day schedules describe the stops
  rather than the walks between them.
- Phase 2 (`transit_routing.provider: google_directions`) raises rather than
  falling back. §6.4's Maps Platform terms question is unresolved, and the probe
  found Routes has no transit coverage on the acceptance corridor anyway.

## 2.7.0 — 2026-09-03

11 commits since 2.6.0, almost all found by opening the output rather than
reading it. `__template_version__` 2.5.3 -> 2.5.5.

### Fixed

- **Google answered "an API is required" on 83 links.** Place links were built
  as `/maps/place/?q=place_id:<id>`, which omits `api=1` and is not part of
  the keyless Maps URLs scheme, so Google routed them to a legacy handler. The
  53 links on the same page carrying `api=1` worked, which is why it looked
  like a map fault rather than a URL fault. Now
  `/maps/search/?api=1&query=<text>&query_place_id=<id>`. The builder's own
  docstring had claimed it used the documented scheme, and its tests asserted
  the string the code produced — agreement, not verification.
- **Route panels named a post market where the card named an overlook.** A
  coordinate waypoint resolves to the right point but Google labels it by
  reverse geocoding, so "Red Cliffs National Conservation Area Overlook"
  appeared as "Millcreek 2nd post market, 5FGM+75". En-route stops now carry a
  `place_id` and legs pass `waypoint_place_ids`, so the panel reads the stop's
  real name. All-or-nothing per leg: one unresolved stop returns that leg to
  coordinates rather than misaligning labels against points.
- **Stops resolved against the wrong place.** The Places query was qualified
  with the leg's *arrival* destination — "Cumberland Plateau Asheville, North
  Carolina" for a plateau in Tennessee, 250 miles away. It found nothing, and
  took its whole leg back to coordinates. Others on that leg were wrong too
  and survived only because their names are distinctive. A geocoded stop is
  now asked for by bare name with its own coordinate as a location bias.
- **One place under two names.** "The Hermitage" and "Andrew Jackson's
  Hermitage" shared a `place_id` while the same itinerary listed "The
  Hermitage Hotel", a different place. Items sharing a `place_id` are the same
  place and now read the same, most specific name winning. Found by opening
  both links in a browser; the URLs were correct and the names were not.
- **Blue, green and brown links on one card.** Three colour sources were in
  play: the theme accent, a fixed canyon brown, and a hardcoded `#c0714a`.
  Every in-content link now resolves through one `--link` token. The first
  attempt fixed only the seven `*-link` classes and its test asserted over
  that same list, so anchors selected by descent stayed brown and the test
  passed anyway.
- **A production run died saving its own cache.** `_save_persistent_caches`
  iterated dicts that discovery threads were still writing to. The publish
  guard held — nothing shipped — but a paid run was lost.
- **A concert is not a place.** An event without a verified URL fell back to a
  Maps search for the show's *title*; it now maps the venue, or links nothing.
- **Transit estimates were re-bought on every retry pass.**

### Changed

- Detour figures are labelled "round trip", which is what the geometry has
  always computed, and are now bounded above as well as below.

## 2.6.0 — 2026-09-02

31 commits since 2.5.0. Link quality and per-day limits, plus transit-leg
work. `__template_version__` moved 2.5 -> 2.5.3 across this range; it had
been frozen at 2.5 since the initial rebuild while the template changed
repeatedly, which is what the guard below now prevents.

### Fixed

- **The per-day restaurant target did not govern the published list.** It was
  applied to the AI-generated list, but with `restaurant_source:
  direct_link_batch` the published list is the batch's — a flat
  `restaurant_direct_batch_item_count` of 20 per destination regardless of
  stay length. Invisible while verified-link-or-seed was discarding 60–77% of
  candidates, since the survivors landed near the target by accident. A
  two-night stop at Capitol Reef published 19 dinner recommendations; it now
  publishes 8. Selection keeps range — best of each cuisine, then each price
  level, then best-rated — rather than eight variations on one bistro.
- **Maps links that did not say where on earth they meant.** A bare
  `?query=Rico Historic District` and the same bare text as a route waypoint
  put a San Juan Island pin in Washington State on a Colorado leg. Text
  queries are now destination-qualified where the value is final, and an
  unqualifiable waypoint is omitted from the route line rather than guessed
  at. The codebase had documented this failure twice before (Snoqualmie; a
  Washington-state clinic producing a 2,196-mile route) without taking that
  third option.
- **On-route stops claiming large detours.** The geometry correction only ever
  raised a figure, never questioned an inflated one, so a stop on I-70 kept
  whatever the model wrote. Now bounded in both directions. The figure is also
  labelled "round trip", which is what the maths has always computed.
- **A designator that looked like a control.** The link-source icon is a
  `<span>` and carried a hover background; the real map link inherited a
  smaller font than it. They rendered the same glyph when the source was
  itself a Maps URL.
- **Two en-route stops could publish the same link.** Five Asheville-leg stops
  shared a city homepage — a waterfall and a national park among them. Each
  assignment path validated its own choice and none consulted what the others
  had claimed; restaurants had had this guard all along.
- **The local tip and the lunch stop were invisible.** Tip links carried no
  class, and with Tailwind Preflight and no base anchor rule an unclassed
  anchor inherits its parent's colour — invisible rather than blue. The lunch
  stop was a sentence in the schedule with no marking on the card it belonged
  to; it now has a badge and a link that searches for food rather than for the
  town.
- **Transit legs scheduling themselves as drives** — a declared train, a
  mistyped leg, and a field named `drive_time` that scheduled trains.
- **An attraction resolved by a Maps text query was resolved into deletion**,
  which also suppressed the search that would have found its real page.
- **AllTrails-first applied to anything the description made trail-like**,
  stripping correct nps.gov pages from a rock formation and a crater. It now
  follows the item's own title.

### Added

- **Removal provenance.** Removed items are recorded by name with the URLs
  they were offered and the check that refused each; every rejection exit in
  `_retain_discovered_url` is labelled from its own guarding condition; and
  each en-route URL records which of seven code paths assigned it. Three
  defects this release were found by these and not by reading.
- **A template-version guard.** `templates/template_versions.json` ties a
  checksum to a declared version, so changing the template without moving
  `__template_version__` fails the suite. It caught its first edit within the
  hour.

### Changed

- Real trip manifests no longer live in the generator repo, which also closes
  a Sandbox/repo drift that had to be maintained by hand.
- The Travel-apps gallery describes trips rather than the build system, and no
  longer surfaces template version, validation status or warning counts.

## 2.5.0 — 2026-08-29

6 commits since 2.4.0, same day. Three reports that looked unrelated —
seed badges on a trip with no seeds, a pushed site that looked unpushed,
verified fixes that seemed not to land — turned out to be one bug in the
service worker.

### Added

- **Priced categories can be answered per trip.** `_resolve_category` now
  consults the manifest between the CLI flag and `config.yaml`: the
  run-specific answer wins, the trip's own answer is sticky across runs, and
  config remains the default. A manifest may carry `categories: {trails: true}`
  at top level or under `trip:`, with either a bool or an `enabled:` subkey.
  Enabling trails globally would have bought them for Europe, whose manifest
  asks for no hikes.
- **Removed items record the URLs they were offered** and which check refused
  each one. `candidates_considered: 0` distinguishes "nothing was ever found"
  from "a link was found and rejected" — different problems needing opposite
  fixes. This is what identified the Prague Castle and Balanced Rock defect
  described below.

### Fixed

- **Every republish left browsers on the previous build.** The service
  worker's cache name was the literal `roadtrip-shell-v2`. Its activate
  handler purges caches whose key is not current, so a key that never changed
  meant the old shell was never purged. True since the first PWA commit. Now
  keyed on the run id, falling back to a content hash of `index.html`.
- **A test held that bug in place.** `test_cache_name_was_bumped` asserted the
  literal constant while its docstring described the intent. Bumping the value
  by hand once satisfied it permanently. It now asserts that the key moves
  with the build.
- **The candidate trail read the wrong keys** and reported "0 candidates
  considered" for every removed item in a full run — which reads as a finding
  rather than a broken read. The tests added with the feature built the trail
  in its output shape and never exercised the extraction.

### Changed

- Trail discovery is on for the Southwest trip, via its manifest rather than
  the global switch. Measured cost was ~15% more per run, not the doubling the
  `config.yaml` comment implies.

### Known

- An attraction whose only candidate is a Google Maps **text query** is marked
  resolved, which suppresses the paid per-item search that would have found its
  real page, and the query then fails `_item_has_verified_url` by design — so
  the item is deleted as unfindable. Prague Castle, Balanced Rock and several
  named trails are lost this way while their official pages rank second in a
  plain search. Unfixed: correcting it increases paid fallback calls, which is
  an owner cost decision.

## 2.4.0 — 2026-08-29

24 commits since 2.3.0. A session that began with "the Europe trip has blue
links" and ended by finding that the quality gate had been overreporting its
own removal counts.

### Added

- **Removed items are recorded by name** (`destination_status_report.json`,
  `.md`). The gate reported "restaurants removed for no verified URL: 39" and
  no artifact said which 39; the names were on the `_registry_decisions`
  records all along and were counted, then discarded. Each destination's
  `url_discovery` block now carries `removed_no_verified_url`, and the
  markdown summary lists them under a "Removed for No Verified URL" heading.
  This is the change that made the two fixes below findable.

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

### Changed

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
