# dipstick55 content-quality triage

Combined from the user's manual review (`dipstick55_manual_notes.md`) and my own
automated pass. Grouped by root-cause theme, not by who found it, since several
items are almost certainly the same underlying bug surfacing in different places.

Status legend: `[ ]` open, `[~]` in progress, `[x]` fixed+tested, `[!]` needs a
human design decision, not a pure bug fix.

## Theme A — Wrong-geography hallucinations in en-route stops (SEVERE)

- [ ] "Stan's Overlook Trail, Snoqualmie, WA 98065" appears as a Google Maps
  waypoint for the Zion -> Bryce en-route leg. Snoqualmie is in Washington
  state, nowhere near this trip. (user)
- [ ] "Looking Glass Rock, Brevard, NC 28712" appears as an en-route map link
  for Moab -> Telluride. North Carolina, nowhere near this trip. (user)

Both are en-route-stop discovery, both are real places that exist -- just in
the wrong US region entirely. Root cause is very likely a same-name trail
existing in multiple states, and whatever resolves "trail name" -> "specific
place/URL" isn't constraining by geographic proximity to the actual route.
Highest severity: this isn't a missing link, it's an actively wrong,
plausible-looking one a user could drive toward.

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

## Theme C — Orphan cards: link removed, card survives [x]

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

## Theme D — Metadata/teaser inconsistency

- [ ] Restaurant ratings appear in both the card title text AND the rating
  badge -- redundant, only want the badge. (user) -- likely a simple
  template/rendering fix, not a data problem.
- [ ] Restaurant teasers are inconsistently present; several links (e.g.
  `redhillsdesertgarden.com`, `alltrails.com/trail/us/utah/chuckwalla-trail`)
  have no teaser at all. User suspects different sourcing paths produce
  different completeness. (user)
- [ ] NPS.gov pages (`nps.gov/brca/planyourvisit/sunset.htm`,
  `.../naturalbridge.htm`) don't extract metadata/teaser text, unlike other
  sources. User's hypothesis: NPS page structure may not suit whatever
  extraction approach is used; suggests trying NPS-specific lookups before
  the general batch search, or backfilling missing metadata from Google Maps
  data when the primary source comes up empty. (user)

## Theme E — Imprecise geocoding rendered instead of pruned

- [ ] Swasey's Beach doesn't resolve to a single point on Google Maps -- the
  address isn't specific enough for a usable link, but it renders anyway.
  User's recommendation: if an address can't be resolved precisely, prune
  the item entirely rather than ship an unusable link. (user)

## Theme F — Duplicate/overlapping entities

- [ ] "Telluride Mountain Village" and "Telluride Mountain Village Gondola"
  render as two separate cards for what's effectively the same place. (user)
- [ ] Bryce Canyon: "Inspiration Point" renders as its own card, AND "Sunset
  and Inspiration Points via Rim Trail and Bryce Canyon Path", AND "Lower,
  Mid, and Upper Inspiration Points" -- three overlapping entries covering
  the same viewpoint. (mine)

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

- Zero cultural events for at least 2 destinations (Bryce Canyon, Santa Fe) --
  unclear if genuine (no events in date range) or an artifact of tonight's
  cultural_events 3-query-to-1 collapse being too narrow. Needs investigation
  before treating as a bug.
