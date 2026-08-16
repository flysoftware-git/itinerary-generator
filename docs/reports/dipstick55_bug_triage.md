# dipstick55 content-quality triage

Combined from the user's manual review (`dipstick55_manual_notes.md`) and my own
automated pass. Grouped by root-cause theme, not by who found it, since several
items are almost certainly the same underlying bug surfacing in different places.

Status legend: `[ ]` open, `[~]` in progress, `[x]` fixed+tested, `[!]` needs a
human design decision, not a pure bug fix.

## Theme A — Wrong-geography hallucinations in en-route stops (SEVERE) [x]

- [x] "Stan's Overlook Trail, Snoqualmie, WA 98065" appears as a Google Maps
  waypoint for the Zion -> Bryce en-route leg. Snoqualmie is in Washington
  state, nowhere near this trip. (user)
- [x] "Looking Glass Rock, Brevard, NC 28712" appears as an en-route map link
  for Moab -> Telluride. North Carolina, nowhere near this trip. (user)

**Root cause found and fixed.** `url_discovery.py` already had a geocoding-
based route-proximity guard (`_geocode_en_route_stop_for_route` /
`_prune_en_route_stops_by_geometry`, added the day before in commit
`b3e4e76`, so it was already active during the dipstick55 run) that queries
Nominatim with a route-biased viewbox and, when that finds nothing, sanity-
checks an unrestricted fallback match against the route's midpoint. The gap:
when the sanity check *did* correctly identify and reject a same-named
place far outside the route (e.g. the real "Looking Glass Rock" landmark in
Transylvania County, NC, ~1442 mi from the Moab-Telluride route midpoint
against a ~156 mi sanity radius -- confirmed live against the real
Nominatim API), that rejection collapsed into the exact same `None` result
as "we have no geocoding data at all" -- and the *latter* case is
intentionally lenient (falls back to a detour-metadata heuristic and keeps
the stop, since Nominatim often just lacks minor POIs and being too
aggressive there would wrongly drop legitimate stops). So a stop with
**positive evidence of being wrong-region** was treated exactly like a
stop with **no evidence either way**, and survived with its own stop
card/link intact. Fixed by recording the sanity-radius rejection distinctly
(`_mark_en_route_stop_geocode_rejected_out_of_region` /
`_en_route_stop_geocode_was_rejected_out_of_region`) so
`_prune_en_route_stops_by_geometry` can tell the two cases apart: "no data"
still falls back to the lenient heuristic as before, but "confirmed
far outside the route" now drops the stop entirely (`en_route_geometry_
filtered_wrong_region`) rather than just marking it ineligible for
waypoint-ordering purposes while still rendering its card and link. Verified
with unit tests reproducing the exact reported case ("Stan's Overlook
Trail" resolving only to Snoqualmie, WA) plus a live (free, no-cost)
Nominatim query confirming the real "Looking Glass Rock, Brevard, NC"
mechanism and sanity-radius math exactly as described above.

## Theme B — Wrong trail/landmark -> AllTrails URL attribution (SEVERE, RECURRING) [x]

- [x] "The Narrows, Emerald Pools trail" links to
  `alltrails.com/trail/us/utah/canyon-overlook-trail` -- a different trail. (user)
- [x] "Delicate Arch" links to `alltrails.com/trail/us/utah/double-arch-trail`
  -- a different arch. (user)
- [x] "Landscape Arch" links to `alltrails.com/trail/us/utah/mesa-arch` -- a
  different arch (a separate, correct "Mesa Arch" card exists elsewhere
  pointing to the same URL, so it's double-assigned). (user)
- [x] "Bridal Veil Falls" links to
  `alltrails.com/trail/us/colorado/cornet-creek-falls-hike` -- a different
  hike. User notes this pattern "persists in multiple places, such as Bear
  Creek and Town Park Loop" -- more instances likely exist beyond the 4 named
  here.

**Root cause confirmed and fixed.** `_retain_discovered_url` had a "remembered
authoritative direct-batch URL" bypass (`url_discovery.py` ~line 2410) that
short-circuited *before* any relevance check ran, if the exact URL string had
ever been validated as correct for *any* item earlier in the run. The
remembered-URL cache (`_direct_batch_authoritative_urls`) was keyed only by
URL, not by (URL, item) -- so once a URL was legitimately validated for one
item (e.g. "Double Arch Trail" -> `double-arch-trail`), any *later* item that
happened to have that same URL show up as a raw row candidate (e.g. via
`_search_alltrails_for_trail_from_direct_batch`'s per-row iteration, which
tries every row's own canonical URL against every item name) got it approved
with zero relevance check. Fixed by re-keying the cache to
`dict[url, set[frozenset(item_significant_tokens)]]`
(`_remember_direct_batch_authoritative_url` / `_is_remembered_direct_batch_authoritative_url`,
now both take an `item_name`) so the bypass only fires when the URL was
specifically validated for the item currently being checked; all 6 call sites
that remember URLs and all 6 that consult the cache were updated to pass
`item_name` through. Verified two ways: (1) new unit tests in
`tests/test_url_discovery.py` covering all 4 reported mismatches directly
against the cache API and end-to-end through `_retain_discovered_url`; (2)
replayed the actual captured AllTrails direct-batch rows from this exact
dipstick55 run (`C:\Temp\RoadTripRuns\SW2026-dipstick55\...\url_discovery_direct_batch_html\`)
through `_search_alltrails_for_trail_from_direct_batch` in the real seq order
recorded in `destination_status_report.json` -- confirmed the unfixed code
reproduces the exact real corruption (Delicate Arch -> double-arch-trail,
Landscape Arch -> mesa-arch, even Double Arch Trail overwriting its own
correct URL with mesa-arch on a second pass) and the fixed code eliminates
all of it while preserving every legitimate match. No live API call was
needed since the real captured batch data already had everything required.
Also confirmed via the same real data that **Jud Wiebe Trail** (Telluride) is
a fourth real instance of this exact bug, reusing Bridal Veil Falls' wrongly-
remembered `cornet-creek-falls-hike` URL.

## Theme C — Orphan cards: link removed, card survives [~]

- [x] "Peek-a-boo Loop" (Bryce Canyon) renders as a card with no link.
  Confirmed independently by both the user and my own automated pass. User
  hypothesizes a threshold/rating-based card-keep decision runs independently
  of the link-removal decision, so a high-rated item's card survives even
  after its link gets pruned. User also flags Jud Wiebe trail as a possible
  second instance -- confirmed as a Theme B instance, see above. (user + mine)
- [x] "Bryce Canyon Scenic Drive" also renders with no link (mine).

**Confirmed as the same root cause as Theme B, via the real captured run
data.** The decision log shows "Peek-a-boo Loop" *did* get a URL accepted
(`sunset-and-inspiration-points-via-rim-trail-and-bryce-canyon-path`) at
authoritative-match time -- but the final rendered `index.html` shows that
exact URL attached to a *different* card, "Queens Garden Trail", while
"Peek-a-boo Loop" rendered with no link at all. This is precisely the
cross-item cache bug: the remembered-URL bypass let the wrong URL through
unconditionally for whichever item hit it first in a later pass (Queens
Garden Trail), while a separate downstream check independently caught and
stripped it for Peek-a-boo Loop, producing one wrong-linked card and one
orphan card from a single mis-cached URL. The Theme B fix (per-item cache
scoping) resolves this at the root: the bypass can no longer approve a URL
for an item it wasn't validated against, so this specific "wrong link here,
orphan card there" split can no longer happen. Whether Peek-a-boo Loop
specifically gets a *real* AllTrails link in any given run still depends on
whether the AI's direct-batch search happens to return a matching row for it
at all (in this captured run, it didn't -- `direct_batch_no_match` -- which
is a data-coverage question, not a matching-logic bug); the fix guarantees
that when a real match exists, it isn't stolen by another item, and when it
doesn't exist, no wrong link gets fabricated either. "Bryce Canyon Scenic
Drive" was checked against this same real run and renders correctly (uses the
scenic-drive modal pattern, `href="#" data-drive-title=...`, with a populated
`DRIVE_DESCRIPTIONS` entry) -- this was likely the general
attraction/scenic-drive-name-collision orphan-description bug already fixed
by commit `3988c8f` (the commit immediately preceding this session), not a
new issue.

**Downgraded to `[~]` after live validation (dipstick56+, 2026-08-15).** The
specific reported mechanism (a link stolen by another item's cache entry) is
genuinely fixed and can no longer produce this exact symptom. But a real,
fresh end-to-end run afterward found the *broader* symptom class -- link-less
attraction cards in general -- got worse, not better: 12 orphan cards in
dipstick56+ vs. ~7-8 in dipstick55, including marquee names (Delicate Arch,
The Narrows, Bryce Amphitheater, Fiery Furnace, Bandelier National Monument).
As predicted in this theme's own analysis above, this is the data-coverage
half of the problem re-surfacing on its own: the AI's direct-batch search
simply not returning a matching row for every named item, independent of the
matching-logic bug. Not a regression from tonight's fixes -- a
pre-existing, unrelated gap that the wrong-link bug was previously masking
by giving *some* of these items a (wrong) link instead of no link. Needs a
product decision (improve harvest recall, vs. treat "no match" as a hard
filter that drops the card instead of rendering it link-less) before this
can go to `[x]`.

## Theme D — Metadata/teaser inconsistency [~]

- [x] Restaurant ratings appear in both the card title text AND the rating
  badge -- redundant, only want the badge. (user) -- likely a simple
  template/rendering fix, not a data problem.
- [~] Restaurant teasers are inconsistently present; several links (e.g.
  `redhillsdesertgarden.com`, `alltrails.com/trail/us/utah/chuckwalla-trail`)
  have no teaser at all. User suspects different sourcing paths produce
  different completeness. (user)
- [x] NPS.gov pages (`nps.gov/brca/planyourvisit/sunset.htm`,
  `.../naturalbridge.htm`) don't extract metadata/teaser text, unlike other
  sources. User's hypothesis: NPS page structure may not suit whatever
  extraction approach is used; suggests trying NPS-specific lookups before
  the general batch search, or backfilling missing metadata from Google Maps
  data when the primary source comes up empty. (user)

**All three root-caused and fixed; the NPS hypothesis turned out to be
wrong in an informative way.**

1. **Title/badge rating duplication.** The real captured St. George
   restaurant batch harvests titles like `"Cliffside Restaurant 4.4/5 $$$
   American"` -- rating, price, and cuisine glued on *after* the real name.
   `HTMLAssembler._sanitize_restaurant_display_name` only stripped decoration
   anchored at the very end of the string, so this shape (decoration in the
   middle, cuisine phrase trailing after) passed through untouched. Fixed by
   truncating at the first occurrence of the row's own known rating value
   (not a generic pattern guess) rather than trying to strip each piece --
   this also correctly handles multi-word cuisine phrases that don't match
   the parsed `cuisine` field verbatim (e.g. "Contemporary American", "New
   American").

2. **Teasers missing was never NPS-specific -- it affected every source
   equally, including non-NPS ones** (`redhillsdesertgarden.com`,
   `chuckwalla-trail`, both non-NPS). Two compounding causes, both fixed:
   - The direct-batch attraction/trail harvest prompts never asked the model
     for a description at all (only name + rating + distance/links) --
     unlike the en-route-stop prompt, which already does and works. Extended
     both prompts (single- and multi-destination variants) to also request a
     short note, matching the en-route pattern.
   - Doing so exposed a real parser bug: for attraction/trail rows the
     `<a>Source</a> <a>Maps</a>` links sit *between* the name and the
     rating/note (unlike en-route/restaurant rows, where links trail at the
     very end), and the row parser's anchor-label cleanup was trailing-only,
     so "Source"/"Maps" leaked into the extracted name once real content
     followed them. Also, live-verified against the real xAI API (grok-4-
     fast) that the model doesn't reliably use a dash before the note (a
     bare space just as often). Fixed the parser to strip anchor-label
     tokens from anywhere in the text and to locate the note boundary via
     name -> rating -> optional distance position tracking rather than
     requiring a literal dash.
   - Separately, and independent of the above: `_discover_attractions` /
     `_discover_restaurants` have a shortcut path
     (`direct_batch_existing_url_preserved`, for items whose url was already
     attached before the per-item loop ran) that skipped the row-metadata
     merge (`_direct_batch_row_quality_metadata_for_url`) the other path
     performs -- confirmed via the real run that this is exactly why "Red
     Hills Desert Garden", "Sunset Point", and "Natural Bridge" rendered with
     *no rating badge at all* despite their harvested rows having one (4.8,
     4.8, 4.7 respectively). Fixed by merging row metadata in that path too,
     and extended the shared merge helper to also carry cuisine/price for
     restaurants.

   Verified with new unit tests covering all of the above, plus two live
   grok-4-fast calls (Bryce Canyon attractions, Moab trails) confirming the
   model follows the new instruction and the updated parser produces clean
   names and real teasers end-to-end against actual API output.

**Downgraded to `[~]` after live validation (dipstick56+, 2026-08-15).**
Item 1 (restaurant title/badge duplication) and the restaurant half of item 2
are confirmed fully fixed in real output: 0/57 restaurant teasers empty, no
rating-in-title duplication found anywhere. But the attraction/trail half of
item 2 (originally reported partly via a trail URL, `chuckwalla-trail`) is
only partially resolved: 14/73 (19%) attraction/trail teasers still render
empty in the fresh run (e.g. Chuckwalla Trail itself). The prompt/parser fix
was real and necessary but evidently not sufficient to close the gap for
every harvested row -- same underlying data-coverage class of problem as
Theme C's residual issue, likely worth investigating together.

## Theme E — Imprecise geocoding rendered instead of pruned [x]

- [x] Swasey's Beach doesn't resolve to a single point on Google Maps -- the
  address isn't specific enough for a usable link, but it renders anyway.
  User's recommendation: if an address can't be resolved precisely, prune
  the item entirely rather than ship an unusable link. (user)

**Root cause found; fixed by upgrading precision instead of pruning.**
Checked live (free, no-cost Nominatim query): "Swasey's Beach" is real and
correctly in-region -- it resolves precisely to Grand County, UT. The actual
problem is that the generator always builds en-route-stop Maps links from a
free-text query (`"Swasey's Beach Campground Green River UT"`), and a
loosely-defined BLM beach/campsite like this often isn't indexed as an exact
match in Google's own POI database, so the free-text search can fail to
resolve to one place even though the location itself is perfectly real and
findable via geocoding. `_prune_en_route_stops_by_geometry` (used for Theme A
above) already runs a real Nominatim geocode for every en-route stop as part
of route-proximity pruning -- but the resulting precise coordinates were
computed and then thrown away, never reused for the stop's actual `url` /
`maps_url`. Fixed by persisting the verified `(lat, lng)` onto the stop and
using a coordinate-based Maps query (`query=<lat>,<lng>`, which always
resolves to exactly one point) as the fallback everywhere a free-text query
was previously used, instead of pruning: pruning would have been the right
call if we truly couldn't confirm a location at all (and that path is
unchanged -- an unconfirmable stop still falls back to the existing
detour-metadata heuristic, or gets dropped outright if Theme A's out-of-
region check fires), but here we already had a free, verified, precise
answer sitting unused. Verified with a unit test reproducing the exact case
(coordinates come from a live Nominatim lookup for "Swasey's Beach, USA")
confirming both `url` and `maps_url` end up coordinate-precise.

## Theme F — Duplicate/overlapping entities [~]

- [x] "Telluride Mountain Village" and "Telluride Mountain Village Gondola"
  render as two separate cards for what's effectively the same place. (user)
- [~] Bryce Canyon: "Inspiration Point" renders as its own card, AND "Sunset
  and Inspiration Points via Rim Trail and Bryce Canyon Path", AND "Lower,
  Mid, and Upper Inspiration Points" -- three overlapping entries covering
  the same viewpoint. (mine)

**Root cause: `_deduplicate_within_destination` only ever deduped
attraction-vs-scenic-drive and attraction-vs-en-route-stop -- it never
deduped `top_attractions` against itself, so two entries pointing at the
exact same URL (both discovered independently, one from the original AI
content pass and one from the direct-batch link harvest) both survived as
separate cards.** Confirmed via the real captured run: "Mountain Village"
and "Telluride Mountain Village Gondola" both resolved to
`telluride.com/discover/the-gondola/`; "Inspiration Point" and "Sunset and
Inspiration Points via Rim Trail and Bryce Canyon Path" both resolved to the
same AllTrails page. Fixed by adding an exact-URL-match merge pass over
`top_attractions` within each destination, keeping whichever entry has
richer metadata (description, then rating, then a shorter/more-canonical
name) -- exact URL identity has no false-positive risk the way a name-
similarity heuristic would, since two attractions that happen to share a
word but point at genuinely different pages (Bryce's third "Lower, Mid, and
Upper Inspiration Points" entry, a different AllTrails URL) are correctly
left alone. This resolves the Telluride pair fully. It resolves 2 of Bryce's
3 overlapping "Inspiration Point" entries (marked `[~]` rather than `[x]`
since the third, distinctly-URLed entry is a genuinely different page
covering overlapping real-world geography, not a same-URL duplicate --
deciding whether/how to also merge *distinct-URL* near-duplicates would need
a fuzzier heuristic with real false-positive risk, which is a product
judgment call rather than a mechanical fix, so it's left as-is rather than
guessed at.

## Theme G — En-route waypoint ordering

- [ ] [!] Google Maps links for en-route stops aren't ordered to avoid
  backtracking along the route (user, with a screenshot reference not
  reproduced here). This is a routing/sequencing question, not a simple data
  bug -- needs a look at how en-route stop order is decided before deciding
  what "fixed" means here.

## Theme H — Scheduling realism [!]

- [ ] [!] Day-transition scheduling assumes checkout/checkin timing that
  doesn't account for real drive time -- e.g. Bryce Canyon's schedule has
  "arrive after your drive from Zion and check in" in the morning, but a
  2-hour drive plus a 10am-default checkout leaves no way to physically
  check in that early. User raises the broader open question: how should a
  multi-day itinerary's last-day-at-A / first-day-at-B overlap be handled at
  all? This needs a product/design decision, not a mechanical fix -- flagging
  for the user rather than having the autonomous loop guess at an answer.

## Not yet triaged from the earlier automated pass

- [x] Zero cultural events for at least 2 destinations (Bryce Canyon, Santa
  Fe) -- unclear if genuine (no events in date range) or an artifact of
  tonight's cultural_events 3-query-to-1 collapse being too narrow. Needs
  investigation before treating as a bug.

  **Investigated live against the real xAI API (grok-4-fast); genuine, not a
  bug.** Ran the actual production single-query search
  (`"{destination} festivals events concerts {month} 2026"`) plus the full
  `_synthesize` LLM step for both destinations with their real trip dates:
  both correctly returned `has_events: false` with a specific, grounded
  `honest_assessment` (Bryce Canyon: remote location, only routine ranger
  programs; Santa Fe: active gallery/performance circuit but nothing festival-
  dated to Oct 27-29 specifically). To directly test whether the 3-query-to-
  1 collapse (commit `e6bd759`) caused this by losing recall, also
  reconstructed and ran the original pre-collapse 3-query search for Santa
  Fe live -- it did surface a couple of extra specific-looking leads (a
  harvest festival page, a "Route 66 Centennial Festival" page) that the
  single collapsed query missed -- but re-ran `_synthesize` against the full
  merged 20-result set from all 3 original queries and got the same
  `has_events: false` conclusion, because neither extra lead actually dated
  to the Oct 27-29 window. So the collapse does cost some raw recall, but it
  did not change the final answer for either reported destination; the
  synthesis step's "never invent events" honesty behaved correctly with
  either query strategy. No code change made.

  One unrelated observation from reading `cultural_events.py` during this
  investigation: `config.yaml`'s `cultural_events.primary_query_template`,
  `secondary_query_template`, `max_results`, `target_events_per_destination`,
  and `verify_event_urls` keys are no longer read anywhere in
  `generator/cultural_events.py` -- dead config left over from before the
  3-query collapse. Harmless (no behavior depends on them) but worth a
  cleanup pass separately.

## New findings from dipstick56+ live validation, not yet triaged

- [ ] **Call volume did not go down after the double-harvest cache-dirty fix.**
  dipstick56+ made *more* real Grok calls than dipstick55 (168 vs 137,
  +23%), not fewer, despite that fix's whole premise being "every
  attraction/restaurant/trail harvest call ran twice." All of the
  $26.94->$0.457 cost improvement is attributable to the `grok-4-fast` model
  swap (confirmed: ~9.8x from per-token price, ~7.7x from fewer
  tokens/call); essentially none of it came from fewer calls. Needs
  investigation into why the fix didn't reduce call count as expected --
  possibly the persistent-cache TTL/write-timing means it still doesn't
  help within a single run the way intended, or dipstick55's inflated call
  count had a different dominant cause than believed.
- [x] **Banned-phrase metric/log inconsistency.** `runtime_metrics.
  banned_phrase_violations` for dipstick56+ reported 17 "stunning," 8
  "iconic," 5 "breathtaking" detected, but the enforcement log line only
  claimed `{charming: 2, iconic: 2}` removed -- and the final rendered
  `index.html` was genuinely clean of all of them either way.

  **Root cause found and fixed.** `main.py` calls
  `ai_gen.normalize_trip_content()` (which runs the banned-phrase scrub)
  twice per run -- once unconditionally after initial generation, again
  after the selective-retry pass if anything was retried. Each call
  *overwrote* `self.last_banned_phrase_violations` instead of accumulating,
  and `runtime_metrics["banned_phrase_violations"]` was captured right
  after the FIRST call -- before the second (retry-triggered) call ever
  ran. So the persisted metric was a stale, incomplete mid-run snapshot,
  and the console's last-printed log line (from the second call) reflected
  only whatever fresh, not-yet-scrubbed content the retry pass introduced
  -- neither number was ever the real total. Fixed by accumulating counts
  across calls (`generator/ai_content.py`) and moving the
  `runtime_metrics` read to after both possible calls
  (`generator/main.py`), so it always reflects the true final total
  regardless of whether retry ran. Verified with a new unit test
  reproducing two sequential calls with overlapping and distinct phrases.

  Separately: I initially also flagged `stage_timings` as an empty `{}` in
  both dipstick55 and dipstick56+'s `runtime_metrics` -- **this was my own
  mistake, not a real gap.** The data was there the whole time, just at
  `run_ledger.jsonl`'s top-level `stage_timings_seconds` key (sibling to
  `runtime_metrics`, not nested inside it). No code change was needed;
  correcting the record here since I'd stated it as a real finding before
  checking carefully.

## Open risk: validator coverage gap for Theme A/B, not yet signed off

Themes A (wrong-geography en-route stops) and B (wrong AllTrails URL
attribution) are fixed at the data-generation layer and covered by real
unit tests against the specific fixes -- but they have **no
validator-level (HTMLValidator) backstop**, unlike Themes C/D/F, which now
have persisted, structural checks in `validation_report.json` (see the
"Content-quality gate" section of `config.yaml` and
`html_validator.py`'s checks 6-8). A regression of either fix would only
be caught if it happens to also break one of the existing unit tests for
that exact code path -- there is currently no independent, integration-
level signal that would catch a *different* code path reintroducing either
bug.

This is a real, currently-unresolved gap between "protected by tests" and
"protected by validation" for two of the most severe bug classes found
this session. A validator-level backstop for these would require live
re-verification (geocoding, live AllTrails fetches) on every validation
run, which is a real cost/latency tradeoff, not a free addition like
checks 6-8 were. **This has not been decided or signed off on -- flagging
it explicitly rather than treating unit-test coverage as sufficient on my
own judgment.**
