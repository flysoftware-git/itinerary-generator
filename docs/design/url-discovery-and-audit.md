# URL Discovery and Audit

## Purpose
URL discovery is a post-AI stage that attaches links to already-generated items
(attractions, restaurants, en-route stops, scenic drives, events). AI does not
generate URLs directly.

## Pipeline Overview
1. `URLDiscoverer.discover_all` runs per destination in parallel.
2. Each destination runs four independent discovery branches in parallel:
	 attractions, restaurants, en-route stops, scenic drives.
3. `URLDiscoverer.audit_discovered_urls` does a final quality pass and strips
	 low-confidence links before HTML assembly.

## Query Variant Strategy
`_build_query_variants(name, destination, category)` builds four increasingly
broad variants.

Behavior:
- Category terms are compacted to first two tokens (avoids over-constrained queries).
- Variants combine quoted and unquoted forms of name + destination.

AllTrails-specific behavior for trail-like attractions:
- `_search_alltrails_for_trail` runs an expanded variant sweep (including explicit
	`alltrails` hints) before falling back to maps.
- A publish-confidence gate evaluates candidate certainty (`high|medium|low`).
  Links below configured threshold are rejected and fallback maps links are used.
- Optional filtered selection mode (`enable_filtered_alltrails_selection`) applies
	snippet-based hard constraints before candidate acceptance:
	- max distance miles
	- max elevation gain feet
	- allowed difficulty values
	- minimum review count
	Candidates passing these filters are ranked by rating/review volume.

## Category Policies
Attraction:
- Trail-like attractions prefer AllTrails first.
- Non-trail-like attractions prefer NPS-scoped results when an NPS code exists.
- On canonical miss, named attractions fail closed at canonical layer and may
	carry `maps_url` fallback metadata for renderer-level fallback behavior.

Restaurant:
- Two-pass strategy:
	1) `site:google.com/maps`
	2) `site:tripadvisor.com`
- Stores `maps_url` fallback metadata; renderer may publish it as secondary
	fallback only when canonical URL is unavailable.

En-route stop:
- AllTrails explicitly disallowed at discovery time (`allow_alltrails=False`).
- Keeps explicit `maps_url` fallback metadata so renderer can avoid dead,
	unlinked stop rows when canonical URL is unavailable.

Scenic drive/day-trip:
- AllTrails explicitly disallowed at discovery time (`allow_alltrails=False`).
- URL is optional; stored as empty if no verified match.
- Scenic-drive URLs must indicate route intent (for example byway/route/drive/road
  markers) and are rejected when they look like generic place pages.

Event:
- Cleaned in audit; AllTrails disallowed.

## Candidate Selection and Scoring
`_search_first_strict` performs two passes for each query variant:
- Pass 1: specific-page candidates only.
- Pass 2: live fallback candidates.

For each candidate:
- Filter by requested domain (if provided).
- Reject obvious generic/non-specific URLs.
- Check relevance (`_is_relevant_result`).
- Score candidate (`_score_candidate_result`) and keep best.

Scoring signals:
- Item/destination token overlap in URL path and result text.
- Positive/negative domain hints.
- Positive path hints.
- Destination country TLD hints.
- Small bonus for specific-pass matches.
- Vote-gated rating boosts for AllTrails and restaurant candidates.

Rating-priority policy:
- High ratings are only prioritized when accompanied by sufficient vote count.
- Applies to AllTrails trail candidates and restaurant candidates.
- Missing rating metadata never hard-rejects a candidate; it simply gets no
	rating-based boost.

## Relevance Gates
Generic URL rejection:
- Blocks known bad URL markers and generic landing/search pages.

AllTrails relevance:
- Requires `/trail/` URL shape.
- Requires slug-token overlap with item tokens.
- Checks page text for soft-404 markers.
- Supports metadata fallback during search candidate evaluation.
- During audit (no candidate metadata), retains strong slug matches when fetch is
	sparse/fails unless explicit not-found status is observed.
- Applies canonical preference for trail slugs by upgrading broad `-via-` URLs to
	matching canonical trail pages when verified.
- Applies configurable publish-confidence threshold via
	`url_discovery.alltrails_min_confidence_for_publish`.
	: `high` requires strong page-text confirmation.
	: `medium` permits strict canonical slug matches when fetches are bot-blocked.
	: `low` is most permissive and keeps legacy blocked-fetch tolerance.

Non-AllTrails relevance:
- On fetch failure, distinguishes blocked/transient (403/401/timeout/5xx/SSL)
  from definitively dead (404/410/DNS failure via `_is_definitively_dead_status`)
  -- mirrors the AllTrails branch's already-established handling. A blocked
  fetch falls back to a secondary liveness probe, then to candidate-metadata
  token matching, rather than being treated as proof of a dead link.
  (Fixed: the generic branch previously rejected on *any* fetch failure,
  including a 403 from a bot-blocking site like TripAdvisor, wrongly
  rejecting live pages.)
- On a successful fetch: requires item-token match in content, and requires
  some destination-token presence in content.

Weakly-named item gate (description-overlap requirement): real bug from a
published eval run -- Bryce Canyon's "Scenic Drive Overlooks" attraction
(description: "18-mile auto tour with multiple pullouts for hoodoo viewing")
linked to `nps.gov/brca/learn/nature/hoodoos.htm`, a page about hoodoo
*geology/formation*, not the scenic drive or its overlooks. Root cause: once
`_significant_tokens` strips "scenic"/"drive" as generic route descriptors
(same reasoning as `"scenic"`/`"byway"` already documented above), the only
token left in "Scenic Drive Overlooks" is `overlook` (`overlooks` after
canonicalization) -- itself a member of `GENERIC_VIEWPOINT_SUFFIX_TOKENS`
(`{"overlook", "view", "viewpoint", "vista"}`, already used elsewhere in this
file to strip non-distinguishing suffix words from harvest-row matching), not
a real identifying word. A single remaining token drops the required overlap
to `_required_general_token_matches(1) == 1`, trivially satisfied by any
same-park page that happens to mention "overlook" once plus the destination
name -- content topic never enters into it.

Fix: `_is_relevant_result` (and `_retain_discovered_url`, which calls it) now
accepts an optional `item_description` parameter. When the item's own
significant-token set is empty or entirely contained in
`GENERIC_VIEWPOINT_SUFFIX_TOKENS` (`name_tokens_are_weak`), the item's
AI-written description -- the only remaining source of real specificity --
must also have token overlap with the fetched page text (same
`_text_matches_item_tokens` helper, same overlap-count threshold). A
same-park page about a genuinely different topic can no longer pass on
destination-name-plus-one-generic-word alone. Threaded through for
attractions and scenic drives in `audit_discovered_urls` (`item_description=
attr.get("description", "")` / `drive.get("description", "")`); other call
sites that don't pass `item_description` (default `""`) are unaffected --
`desc_tokens` is then empty and the new check is skipped entirely, preserving
today's exact behavior for every existing caller.

Tests:
`test_relevant_result_rejects_wrong_topic_page_for_generically_named_attraction`,
`test_relevant_result_accepts_generically_named_attraction_with_matching_description`,
`test_relevant_result_weak_name_gate_is_noop_without_description`, and
`test_audit_discovered_urls_rejects_wrong_topic_link_for_scenic_drive_overlooks`
(`tests/test_url_discovery.py`).

Under-construction / placeholder-content detection: real bug from a
published eval run -- "Bryce Canyon Visitor Center" linked to
`https://www.nps.gov/brca/planyourvisit/visitorcenters.htm`. Live-fetched
during investigation, that URL returns HTTP 200 on the correct `nps.gov`
domain and mentions the destination, so it clears every existing
liveness/genericness/relevance check -- but its entire visible content is
"Page In-Progress -- This page is currently being worked on. Please check
back later." NPS restructures its site often enough that a stub page like
this can sit at an otherwise perfectly plausible URL indefinitely. This is a
distinct failure class from everything else in this file: it isn't a dead
link (`_is_definitively_dead_status`), a closed venue
(`_has_attraction_closure_marker`), or a generic listing/search page
(`_is_generic_section_landing_page`, `_is_obviously_generic_url`) -- it's a
live, on-topic, technically-correct URL with no actual content behind it,
which none of those checks reason about (they all classify the URL shape or
the entity's status, never "does this page contain anything real").

`_is_relevant_result`'s general (non-AllTrails) deep-check branch now calls
`_is_under_construction_page(text)` immediately after a successful fetch,
before the token-overlap checks -- mirroring `_has_attraction_closure_marker`
and `_has_alltrails_closure_marker`'s existing marker-list-plus-HTML-noise-
stripping pattern (`UNDER_CONSTRUCTION_PAGE_MARKERS`, deliberately narrow and
specific: "page in-progress", "this page is currently being worked on",
"this page is under construction", "site under construction", "we are
currently updating this page", "this content is currently unavailable" --
excludes generic phrases like bare "coming soon", which could plausibly
appear in passing on an otherwise substantive page). `_is_generic_section_
landing_page` was not extended for this: it's a pure URL-shape check with no
page fetch, so it structurally cannot see page content at all -- only the
deep-check fetch path in `_is_relevant_result` can catch this class of bug.

This is a general detector, not a one-off special case for this single URL:
any item across any category that routes through the general relevance gate
(attractions, scenic drives, restaurants, en-route stops, route options,
events) now gets the same protection. No static URL substitution was made
for the Bryce Canyon Visitor Center specifically -- there is no manifest/
config entry hardcoding that URL anywhere in this codebase (it's fully
discovered at runtime), so the correct fix is this detector catching it on
the next discovery/audit run, letting the existing search-and-fallback
machinery find a real page (or fail closed to a maps-search link, which is
strictly more useful than a placeholder) rather than hand-patching one URL.

Tests: `test_is_under_construction_page_detects_real_nps_placeholder_text`,
`test_is_under_construction_page_ignores_html_comment_noise`,
`test_is_under_construction_page_false_on_substantive_content`, and
`test_relevant_result_rejects_under_construction_placeholder_page`
(`tests/test_url_discovery.py`).

## Audit Behavior
`audit_discovered_urls` re-validates all discovered links and may remove them.

Retention path:
- Keep safe fallback URL prefixes.
- Remove obvious generic pages.
- Enforce category policy (`allow_alltrails=False` where required).
- Re-check relevance with destination/item context.
- Apply URL class blocklist (see below).
- Apply domain hard-rejection (`url_domain_denylist`) before relevance scoring.
- Reconcile cross-destination scenic-drive duplication against attraction
	ownership in other destinations.

Prewarm scoping (`_prewarm_url_validation_cache`): the audit pass proactively
bulk-fetches discovered URLs before per-item checks run, so those checks hit
a warm cache instead of fetching live. This prewarm now skips URLs whose
provenance already establishes high confidence -- `.gov` domains and harvest
rows already marked direct-batch-authoritative during discovery
(`_is_high_confidence_provenance_url`). Skipping the prewarm does not skip
verification entirely: if a later per-item check still needs that URL's
content, it fetches on demand at that point. This only avoids paying for a
bulk fetch that's rarely actually needed downstream for a source this
trustworthy.

Per-domain block cooldown: a generic equivalent of the AllTrails-specific
fetch cooldown (below) now applies to *any* domain via `_fetch_page_text`'s
shared entry point. When a domain returns 401/403, further fetches to any
other URL on that same domain short-circuit with a synthetic blocked result
for `url_discovery.domain_block_cooldown_seconds` instead of each paying a
full network timeout for a call very unlikely to succeed.

Logging:
- Policy-driven AllTrails rejections for scenic/en-route/restaurant/event are info-level.
- Higher-risk attraction rejections remain warning-level.
- URL policy class hits are info-level in monitor mode; rejections in enforce mode are info-level.

## Rejected URL Classes (Structural Prohibition)
The following URL structural patterns are categorically banned from final output
regardless of discovery method, relevance score, HTTP liveness, or fallback path:

| Class | Pattern | Example |
|---|---|---|
| `google_maps_search` | `google.com/maps/search/` | `/maps/search/restaurants+near+...` |
| `google_maps_dir` | `google.com/maps/dir/` | `/maps/dir/Moab/Dead+Horse+Point` |
| `google_search` | `google.com/search` | `/search?q=Capitol+Reef+Cafe` |
| `social_media` | `facebook.com`, `instagram.com`, `tiktok.com`, `x.com`, `twitter.com` | Any social profile or post |

Implementation:
- `_classify_url_policy_class(url)` returns the class string.
- `_retain_discovered_url` checks the class against `_url_policy_blocked_classes`.
- Mode is config-driven: `off | monitor | enforce` via `url_discovery.url_policy_mode`.
- The blocklist is config-driven via `url_discovery.url_policy_blocked_classes` (list of class strings).

## Entity-Path Integrity for Encyclopedic URLs
For URLs where the subject entity is structurally encoded in the URL path by convention,
item name tokens must appear in that path before any page fetch is attempted.

Currently applied to:
- Wikipedia URLs (`wikipedia.org/wiki/<Slug>`): the wiki-page slug is extracted and checked
  against the item's significant tokens. If no token matches, the URL is rejected immediately.

This catches cross-entity contamination such as "Mammoth Cave" linked to a
`wikipedia.org/wiki/Bryce_Canyon_National_Park` page.

## AllTrails Redirect Entity-Match
After a successful AllTrails page fetch, the final URL (after any HTTP redirect)
is compared against the originally requested URL. If the final URL slug no longer
matches the item name tokens, the link is rejected.

Implementation:
- `_fetch_page_text_uncached` records `response.url` (final URL) into `_fetch_final_url_cache[original_url]`.
- `_is_relevant_result` reads the cache after a successful fetch and calls
  `_alltrails_slug_matches_item(final_url, item_name)`; mismatch → False.
- Limitation: when AllTrails blocks the fetch (HTTP 403), no redirect is followed and
  this check does not apply. The slug denylist (below) covers known cases.

## AllTrails Slug Denylist
A configurable frozenset of known-invalid AllTrails URL slugs, populated from
`url_discovery.alltrails_slug_denylist` in `config.yaml`.

- Applied in `_is_relevant_result` (discovery) and `_retain_discovered_url` (audit),
  both as fast-reject before any network fetch.
- Intended for slugs that return 404/redirect-to-different-entity in browser but
  return 403 to bots, making automated detection impossible.

## AllTrails Trailhead Geo Maps Link
Project owner ask: "a map link for each AllTrails trail that will take you to
the trail" — AllTrails' own "Get Directions" button is client-JS-driven with no
static `href` a fetch-based pipeline can follow, so the trail page's own
structured data is used instead.

Verified live (2026-08-18, via a real browser session against
`https://www.alltrails.com/trail/us/utah/hickman-bridge-trail`): AllTrails
embeds a `<script type="application/ld+json">` block shaped like
`{"@type": "LocalBusiness", "geo": {"@type": "GeoCoordinates", "latitude":
"38.28876", "longitude": "-111.22765"}, ...}` alongside two unrelated ld+json
blocks (`WebPage`, `BreadcrumbList`) on the same page. AllTrails serializes
`latitude`/`longitude` as JSON *strings*, not numbers. This coordinate is the
trailhead itself (AllTrails' own listed location for the trail), which is more
precise than a name-based geocode and does not depend on the page's
JS-rendered "Get Directions" control.

Implementation:
- `_extract_alltrails_geo_from_html(html)` (`generator/url_discovery.py`)
  scans every `ld+json` block on a fetched AllTrails page for one with a
  `geo` dict, `float()`-casts `latitude`/`longitude`, and range/null-island
  sanity-checks the result. Mirrors `_extract_restaurant_meta_from_html`'s
  scan-every-block pattern for restaurant JSON-LD.
- `_alltrails_geo_maps_url(url)` fetches the page via `_fetch_page_text`
  (which dispatches AllTrails URLs to `_fetch_alltrails_text`) and builds
  `https://www.google.com/maps/search/?api=1&query=<lat>,<lng>` — the same
  coordinate-query URL convention already used for en-route stops'
  `geocode_lat`/`geocode_lng`-based `maps_url` (see `_discover_en_route_stops`).
- Wired into `audit_discovered_urls`'s `top_attractions` loop at the point
  where an AllTrails trail URL clears `_retain_discovered_url` **unchanged**
  (`cleaned == url`) — i.e. only after the URL has already passed every
  existing acceptance gate (slug match, relevance, closure-marker check,
  miles threshold, etc.), never as part of deciding whether to accept it.
- Fails closed on any extraction failure (blocked fetch, missing/malformed
  JSON-LD, out-of-range coordinate): `maps_url` is left exactly as whatever
  the pre-existing fallback logic already produced (often absent for a trail
  item, since the "no discovered URL" `maps_url = url` fallback only fires
  when there is no accepted URL at all). Never a fabricated or generic
  search-query link dressed up as a coordinate link, per this module's
  no-invented-data rule.
- Caching: both `_fetch_page_text`/`_fetch_alltrails_text` already cache
  per-URL in memory (`_alltrails_fetch_cache`) and persist successful
  fetches to the on-disk cache (`_load_persistent_caches`'
  `alltrails_fetch_results` section), so this geo lookup piggybacks on
  whatever fetch already happened earlier for the same URL in the same
  audit pass (e.g. the trail-miles threshold check) or in an earlier run —
  it does not add a second live network request in the common case.
- Rendering requires no changes: `html_assembler.py`'s existing
  `_maps_corner_link_html` already renders a map-pin badge whenever an item
  carries a `maps_url` distinct from its primary `url` — previously an
  AllTrails trail item typically had no `maps_url` at all (or one equal to
  its own page URL), so the badge never appeared for a trail card.

### Wayback Machine fallback (the fetch actually has to succeed for this to fire)

The above shipped and passed every unit test, but a real production run
(dipstick71, 19 real AllTrails attractions) showed it fire **zero** times.
Root cause: this module's own pre-existing comments and log output already
establish that AllTrails' DataDome bot-detection blocks this app's own
direct fetches of trail pages essentially universally in production (see
`_fetch_alltrails_text`'s comment) — every other AllTrails-dependent feature
in this file already routes around that via independent search-engine
corroboration instead of successfully reading the page, but geo extraction
had no such fallback, so it silently did nothing.

Fix: `_alltrails_geo_maps_url` now falls back to
`_fetch_wayback_alltrails_text` whenever the direct fetch fails. That method
looks up the closest archived snapshot of the trail URL via the Wayback
Machine's free, no-auth CDX availability API
(`https://archive.org/wayback/available?url=<trail-url>`) and fetches that
snapshot's HTML — archive.org's own crawler isn't the traffic DataDome is
blocking (different domain, different requester, different reputation), and
it stores the *original* page HTML, JSON-LD included, at crawl time. The
archived HTML is run through the existing `_extract_alltrails_geo_from_html`
unchanged (no separate parser) — a trailhead coordinate doesn't go stale the
way ratings/hours/closures do, so even a years-old snapshot is still a
correct answer for this specific field.

Verified live (2026-08-18):
- The availability API's real response shape: `{"archived_snapshots":
  {"closest": {"available": true, "status": "200", "url":
  "http://web.archive.org/web/<timestamp>/<original-url>"}}}` when a
  snapshot exists, or `"archived_snapshots": {}` (no `closest` key) when it
  doesn't.
- A **recent** (2024+) archived snapshot carries the same `<script
  type="application/ld+json">` `geo` block as a live page — confirmed
  against a real 2026-01-08 snapshot (american-samoa/tutuila/
  lower-sauma-ridge-trail) and several 2025/2026 snapshots below.
- An **older** archived snapshot (2023 and earlier, before AllTrails' own
  switch to JSON-LD) uses schema.org *microdata* instead — `<div
  itemprop="geo" itemtype="http://schema.org/GeoCoordinates">` with
  `<meta itemprop="latitude"|"longitude">` children, no `ld+json` block at
  all. `_extract_alltrails_geo_from_html` does not parse this (deliberately
  reused as-is per this module's no-invented-data rule, rather than adding a
  second parser) — a trail stuck on a pre-2024 snapshot fails closed
  (`maps_url` left untouched) rather than fabricating a coordinate.

Real coverage check against all 19 AllTrails trail URLs from dipstick71's
actual output (`C:\Temp\RoadTripRuns\SW2026-dipstick71\dev\index.html`):
- **17/19 (89%)** have *some* archived snapshot available.
- Of those 17, spot-checking extraction end-to-end: trails with a 2025/2026
  snapshot (e.g. Delicate Arch, Double Arch, Emerald Pools, Mesa Arch,
  Windows Loop, Grand Wash, Tsankawi Village, Corona & Bowtie) reliably
  yield a real coordinate; trails whose only snapshot predates the JSON-LD
  rollout (e.g. **Hickman Bridge Trail** itself — its only archived snapshot
  is from 2023-07-10 — plus Bridal Veil Falls, Piedra Falls, Cassidy Arch,
  Chuckwalla, Jenny's Canyon) fetch successfully but correctly extract no
  coordinate. Net effect: a meaningful majority of real trails on a typical
  itinerary now get a real trailhead `maps_url` where today's shipped
  behavior gets zero, but this is not a 100% fix — a trail archived only
  before AllTrails' JSON-LD switch stays fail-closed, same as an
  unarchived trail.
- 2/19 (Treasure Falls, Pueblo Loop) have no archived snapshot at all —
  fails closed exactly like a blocked direct fetch with no fallback
  available, `maps_url` untouched.

Implementation notes:
- `_fetch_wayback_alltrails_text` deliberately does **not** route through
  `_fetch_page_text`/`_fetch_alltrails_text`: both archive.org URLs it uses
  (the availability-API call and the snapshot URL itself) contain the
  literal substrings `"alltrails.com"` and `"/trail/"` (the original
  AllTrails URL is embedded in each), and `_is_alltrails_trail_url` is a
  plain substring check — routing through it would misapply AllTrails' own
  request-pacing/block-cooldown state to archive.org calls, exactly when
  that cooldown is most likely active (this fallback runs right after a
  direct AllTrails fetch just failed). It calls
  `_fetch_page_text_uncached` directly instead, with its own independent
  cache (`_wayback_fetch_cache`, keyed by the *original* AllTrails URL),
  pacing (`_wayback_request_delay_seconds`, default 1.0s — config.yaml
  `url_discovery.wayback_request_delay_seconds`), and persistence
  (`_load_persistent_caches`'/`_save_persistent_caches`'
  `wayback_geo_fetch_results` section, only successful fetches persisted,
  same pattern as `alltrails_fetch_results`). Persistent TTL defaults to 30
  days (`DEFAULT_PERSISTENT_WAYBACK_CACHE_TTL_HOURS`, config key
  `persistent_wayback_cache_ttl_hours`) since an archived snapshot's HTML
  never changes once crawled — same rationale as the Nominatim geocode
  cache's long TTL.
- Direct fetch is still tried first and stays the primary path — free when
  it works (a very new page not yet archived, or a lucky non-blocked
  window) — Wayback is purely an on-failure fallback, verified to add no
  extra call when the direct fetch already succeeds.

### dipstick72: the Wayback fallback itself still fired zero times in production (real root cause + fix)

The Wayback fallback above shipped, passed every unit test, AND was
live-verified (17/19 real trails had a working snapshot). It was merged.
The very next fresh production run (dipstick72, 20 real AllTrails
attractions) still showed the feature fire **zero** times — same signature
as dipstick71 before the fix (no `alltrails_geo_maps_url_attached` log
line anywhere, no exception, `run-console.log` just stops after the
attraction's URL is accepted). Re-reading the calling code
(`audit_discovered_urls`'s `top_attractions` loop) found nothing wrong: the
`elif cleaned and self._is_alltrails_trail_url(cleaned): geo_maps_url =
self._alltrails_geo_maps_url(cleaned)` wiring was — and still is —
correct. The bug was not in this module's own logic at all.

Root cause, found only by live reproduction (a standalone script calling
`_alltrails_geo_maps_url` with a real `URLValidator` and real network
access against dipstick72's actual trail URLs — see
`_fetch_wayback_alltrails_text`'s docstring for the full account): the
Wayback fallback's lookup step called `https://archive.org/wayback/
available?url=<trail-url>` — a **separate, low-quota archive.org host**
from the CDX search / snapshot-playback host (`web.archive.org`) used
everywhere else in this fallback. Under real, moderate production request
volume that endpoint returns HTTP 429 for extended stretches with no
code-side retry: a clean, correctly-paced (1 req/sec, single process) run
against all 20 real dipstick72 trail URLs got **429 on 20/20**, and
isolated single lookups kept 429ing for 60+ seconds afterwards. Because
`_fetch_wayback_alltrails_text` fails closed *silently* by design (matching
`_alltrails_geo_maps_url`'s own "never fabricate a link" contract), a 429
that outlasts a destination's ~20-second audit pass zeroes out every trail
in it with **zero trace in the logs** — exactly the dipstick72 signature.
The feature's own unit tests never caught this because all of them mock
`_fetch_page_text`/`_fetch_wayback_alltrails_text` directly and never make
a real request to archive.org.

Fix: `_fetch_wayback_alltrails_text` now looks up snapshots via the Wayback
Machine's **CDX Server API** (`https://web.archive.org/cdx/search/cdx?url=
<trail-url>&output=json&filter=statuscode:200&limit=-5`) instead of the
`archive.org/wayback/available` helper. Live-reproduced in the same window
as the 429s above: while the availability helper was sustained-429ing, the
CDX endpoint kept answering with real 200s and correct data for the exact
same trail URLs — confirming it is a genuinely separate host/quota, not
just a lucky retry. CDX also lets the query filter to `statuscode:200`
directly (the old availability API's "closest" snapshot could hand back an
archived DataDome block page — reproduced live: a `web.archive.org/web/
2024/<trail-url>` redirect landed on a 2025-08-11 snapshot that was itself
a captured 403 block page, not real content) and take the most recent
matching row (`limit=-N` returns oldest-first, so `rows[-1]` is newest).

Two smaller, evidence-backed resilience additions from the same live
testing session:
- **One retry on a transient failure** (429, 5xx, or a read timeout) after
  a short pause, for both the CDX call and the snapshot fetch. Observed
  live: a request that 429s or times out often succeeds on a plain retry
  moments later — archive.org's flakiness here comes in short (seconds-
  long) bursts, not sustained outages, so a single retry meaningfully
  raises the real-world success rate. A non-transient failure (no
  snapshot, invalid URL) is not retried.
- **Longer timeout for the snapshot fetch specifically** (20s, up from the
  8s used for the direct AllTrails fetch): the archived HTML served through
  `web.archive.org`'s playback proxy is a large page (~1.1-1.2MB) and was
  live-observed to occasionally need more than 8s to arrive even when the
  fetch was going to succeed. An 8s timeout was silently turning a slow
  success into a failure indistinguishable from "no snapshot."
- **Failure-path logging**: the `audit_discovered_urls` call site now logs
  a `alltrails_geo_maps_url_unavailable` decision (informational only, does
  not touch `url`/`maps_url`) whenever `_alltrails_geo_maps_url` returns
  `None` for an accepted trail. Previously this path logged nothing at all
  on failure, which is exactly why dipstick72's regression needed a live
  reproduction script instead of a `run-console.log` grep to diagnose.

Live end-to-end re-verification (2026-08-18, real network, real trail
URLs, no mocks) against the same URLs dipstick72 actually failed on:
Double Arch Trail → `(38.68828, -109.53838)`; Mesa Arch → `(38.38909,
-109.86796)` — the identical coordinate the original pre-merge live
verification found; Windows Loop and Turret Arch Trail → `(38.68716,
-109.53672)`; Corona and Bowtie Arch Trail → `(38.57446, -109.63238)`.
Hickman Bridge Trail still correctly fails closed (its newest archived
snapshot, re-checked live via CDX, is still 2023-07-10 — before AllTrails'
JSON-LD rollout, matching the pre-existing documented limitation above,
not a regression).

Residual risk: this remains a dependency on a third-party archive
service's real-time availability. The CDX host/retry/longer-timeout
changes measurably reduce — they do not eliminate — the chance of a
transient archive.org hiccup zeroing out a run's Wayback fallback; the
fail-closed, unfabricated-link guarantee is unaffected either way.

## Secondary Maps Link for Attractions and Restaurants
Project owner ask: "add Google Maps links to attraction cards and restaurant
cards that are available but are not used because of source links used
instead. Add the other link behind a small map icon placed with other badges
when the link is available." `html_assembler.py`'s `_maps_corner_link_html`
already implements the render side of exactly this — a small `🗺️` badge that
surfaces `item["maps_url"]` alongside a card's primary link — and is wired
into route options, en-route stops, attractions, and restaurants alike. But a
real validation run (dipstick72, `C:\Temp\RoadTripRuns\SW2026-dipstick72\
dev\index.html`) showed **0 of 50** real attraction cards and **0 of 61**
real restaurant cards ever rendered it, despite 49/50 attractions and 61/61
restaurants carrying a real, distinct primary source URL (nps.gov pages,
official restaurant sites, TripAdvisor, etc.). En-route stops and route
options rendered the badge correctly on the same page (39 total `badge-map`
occurrences, all attributable to those two sections) — this was a gap
specific to attractions/restaurants, not a rendering bug.

Root cause, traced through `generator/url_discovery.py`: unlike en-route
stops — which always get an unconditional `maps_url` assigned from
route-waypoint geocoding, or a query-text fallback when ungeocoded, *before*
their `url` field is even decided (see the `has_precise_geocode` block in the
en-route-stop resolution loop, `_discover_en_route_stops`) — attractions and
restaurants had no equivalent step. Every pre-existing `attr["maps_url"] =
...` / `rest["maps_url"] = ...` assignment in `_discover_attractions`,
`_discover_restaurants`, and `audit_discovered_urls`'s own per-item loops
only fires in the "no real source URL was found at all, a maps-search URL
became the PRIMARY url itself" paths (`attr["maps_url"] = attr["url"]`),
which `_maps_corner_link_html` correctly treats as redundant and suppresses
(no separate badge needed when the primary link IS already the maps link).
`_discover_restaurants` goes further and actively does
`rest.pop("maps_url", None)` in every branch that finds a real, distinct URL
(Google Maps place, TripAdvisor, official site, direct-batch row match).
Net effect: whenever a genuinely useful distinct primary link was found for
an attraction or restaurant, no code path ever attached a separate
`maps_url` alongside it, so the badge had nothing to show — a real, common
case, not an edge case (the AllTrails geo-maps-link feature documented above
covers only the narrower AllTrails-trail sub-case, and was never intended to
cover plain attractions/restaurants).

Fix: `_attach_secondary_maps_link(item, item_name, dest_name, kind)`
(`generator/url_discovery.py`, defined just above `audit_discovered_urls`),
called once per item from two new post-loop passes at the end of
`audit_discovered_urls`'s `top_attractions` and `dinner_recommendations`
handling — i.e. only after that pass has already settled each item's final
`url` for this run, the same reason the AllTrails geo hook is inserted at a
"URL already final" point rather than during initial discovery. It attaches
`item["maps_url"] = f"https://www.google.com/maps/search/?api=1&query=
{quote(_maps_fallback_query_text(item_name, dest_name))}"` — reusing the
same name+destination Google-Maps-search-query convention already used
throughout this file (`_maps_fallback_query_text`, also the basis for
`_en_route_maps_fallback_query_text`) — when, and only when:
- `item["url"]` is non-empty (an item with no verified source at all keeps
  whatever its own fail-closed logic upstream already decided — e.g.
  category-style-activity, ambiguous-geography, or policy-enforce
  omissions — untouched; those are deliberate "no map either" decisions,
  not part of this gap);
- the url is not an AllTrails trail URL (out of scope here — AllTrails
  trails get their own coordinate-based `_alltrails_geo_maps_url` hook with
  its own fail-closed/logging contract documented above; duplicating a
  text-query fallback on top of or instead of that would blur which
  mechanism is responsible for a trail card's map link);
- the url's own policy class (`_classify_url_policy_class`) is not itself
  `google_maps_search`/`google_maps_dir` — that IS the "no real source,
  maps became primary" case this fix must not touch;
- `item["maps_url"]` is not already non-empty — so an item that picked up a
  maps_url from any other mechanism (existing direct-batch row data, the
  AllTrails geo hook, a pre-existing fallback) is never double-processed or
  overwritten.

Coordinate-based links were considered (mirroring `_alltrails_geo_maps_url`'s
pattern) but ruled out for the general case: attractions and restaurants
carry no geocode data anywhere in this pipeline (`geocode_lat`/`geocode_lng`
is set only for en-route stops, via route-waypoint geocoding) — a
name+destination search-query link is the only honest option available at
this point, consistent with `_maps_corner_link_html`'s own docstring, which
already treats a search-query `maps_url` as a useful secondary "locate on a
map" convenience even though `_select_preferred_external_link` keeps that
same URL class out of the *primary* link slot.

Purely additive: the already-accepted primary `url` field is never read
except to gate on, and is never modified, replaced, or downgraded by this
method for any item.

Verified against real dipstick72 production data (offline replay of
`audit_discovered_urls` using the real item names and real primary URLs
extracted from that run's `index.html`, e.g. "Zion Canyon Visitor Center" →
`https://www.nps.gov/places/zion-canyon-visitor-center.htm` and "Rib & Chop
House" → `https://ribandchophouse.com/st-george-utah/`): every such item now
gets a distinct `maps_url` attached while its original primary `url` is left
byte-for-byte unchanged, and `_build_attractions`/`_build_restaurants` now
render `class="badge badge-map"` for them where they previously rendered
nothing. Tests: `test_audit_attaches_secondary_maps_link_for_attraction_
with_distinct_primary_url`, `test_audit_attaches_secondary_maps_link_for_
restaurant_with_distinct_primary_url`, `test_audit_does_not_attach_
secondary_maps_link_when_primary_url_is_maps_fallback`, and
`test_attach_secondary_maps_link_skips_alltrails_url_directly`
(`tests/test_url_discovery.py`).

## Cultural Event Maps-Fallback Survival Through the Audit Pass
Real bug, confirmed from a real published eval run
(`C:\Users\bryan\Documents\Github\PWAapps\Travel-apps\sw\eval\index.html`): St.
George's two cultural events -- "I-15 Country Rock Music Festival" and
"Odyssey Dance Theatre's Thriller 2026" -- both had real, structured data
(dates, venue, admission) but rendered as plain `<strong>` text with **no**
`<a href>` at all, unlike every other content type on the page, which always
carries at least a Google-Maps-search fallback link when no real source URL
survives.

`generator/cultural_events.py`'s `_verify_event_urls` already does exactly
what its docstring says: strip a dead or generic event URL, then assign
`event["url"]` a Google-Maps-search fallback (`_event_maps_fallback_url`) for
any event still left without one. That part was working correctly -- the URL
genuinely was set at that point. The bug is downstream, in
`audit_discovered_urls`'s per-destination events loop
(`generator/url_discovery.py`): it re-validates every event's `url` through
`_retain_discovered_url(..., kind="event")`, the same strict retention gate
every other category goes through. `config.yaml` sets `url_policy_mode:
"enforce"` and `google_maps_search`/`google_maps_dir` are both in
`url_policy_blocked_classes` -- so a Google-Maps-search fallback URL is
rejected by the policy-class gate unless the caller passes
`allow_google_maps_search=True`. The events call site never did, so the
fallback `_verify_event_urls` had just attached was silently stripped
(`event.pop("url", None)`) with nothing put back in its place.

This is exactly the same trap the restaurants/attractions/en-route-stops
loops in this same function already avoid: each of them extracts a
pre-existing `google_maps_search`/`google_maps_dir`-classified `url` into a
separate `maps_url` field *before* calling `_retain_discovered_url`, so that
even when the retention gate rejects the primary `url`, the fallback survives
as `maps_url` (see "Secondary Maps Link for Attractions and Restaurants"
above for the render-side half of this same pattern). The events loop had no
equivalent extraction step, so it had no way to reach the fallback link once
it was rejected by policy.

Fix, two parts:
- `audit_discovered_urls`'s events loop now extracts `event["maps_url"]` from
  the pre-existing `url` before the retain call, mirroring the
  restaurant/attraction/en-route-stop pattern exactly, and re-attaches it
  whenever the retain call strips the primary `url`.
- `html_assembler.py`'s `_build_events` (Format-A event rendering, the
  `event-link`/`events-subcard` markup) now falls back to `ev.get("maps_url")`
  when `ev.get("url")` is empty, so the preserved fallback actually gets
  rendered as the event name's link instead of sitting unused in the data.
  Previously this method's own comment ("Omit fallback link when no
  canonical event URL is available; generic search queries fail strict
  single-result validation") reflected a design intent that had drifted out
  of sync with `_verify_event_urls`'s explicit contract of always attaching
  *some* link.

A genuinely bad/hallucinated event URL (e.g. an unrelated `example.com` page
that fails the relevance gate for reasons other than the policy-class check)
is unaffected by this fix and is still stripped with no `maps_url` fallback,
since the extraction only triggers for URLs already classified as
`google_maps_search`/`google_maps_dir`.

Tests: `test_audit_discovered_urls_preserves_event_maps_fallback_as_maps_url`
(`tests/test_url_discovery.py`),
`test_build_events_format_a_falls_back_to_maps_url_when_url_missing` and
`test_build_events_format_a_no_link_when_neither_url_nor_maps_url`
(`tests/test_html_assembler.py`).

## Fail-Closed Policy for Named Entities
A link is only publishable for a named entity if it is a **deterministic, entity-specific
target** — one that refers to that single entity and not a list, search query, or area
reference.

Consequences:
- Canonical publication for named entities remains fail-closed and requires a
	deterministic entity-specific URL.
- `google.com/maps/search/<name>+near+<destination>` remains non-canonical and
	never qualifies as canonical entity evidence.
- Rendering middle-ground in v2.1:
	- canonical URL is used when available,
	- explicit `maps_url` fallback may be rendered as secondary fallback,
	- items with neither canonical nor fallback link are hidden.

## Provenance-Controlled Publication

Discovery and audit must produce decisioned outcomes that control final publication.
Renderer behavior must follow those outcomes rather than synthesizing links from names.

Operational rules:
- Candidate existence is not equivalent to publishability.
- Only validated, decisioned URLs are publishable for named entities.
- Query/recovery fallbacks remain diagnostics metadata, not primary link targets.

## URL Policy Rollout Mechanism
- New installs default to `monitor` mode; `enforce` is the production target.
- Baseline grandfathering is optional: `_load_url_policy_allowlist` can read prior
  `output/index.html` (configurable path) and extract absolute `href` URLs into an
  in-memory allowlist when auto-seeding is enabled.
- Manual allowlist entries (one URL per line, `#` comments) are merged with the
  auto-seeded baseline; manual entries are optional when auto-seeding is enabled.
- Current repository policy sets auto-seeding off by default to avoid preserving
  stale links that would otherwise be rejected by current trust gates.
- A `url_diff_report.json` and `url_diff_report.md` are written each run summarizing
  kept, added, and removed links relative to the prior output baseline.

## Domain Denylist
Config key: `url_discovery.url_domain_denylist`

Behavior:
- Hostnames in this list are rejected in `_retain_discovered_url` before any
  relevance scoring.
- Matching is normalized and supports exact host and subdomain suffix match.

Use case:
- Fast fail for known-untrusted or hallucination-prone domains.

## Cross-Destination Scenic-Drive Dedup
Audit pass includes `_deduplicate_cross_destination_drives`.

Behavior:
- Build significant-token sets for `top_attractions` per destination.
- Remove scenic drives whose title token set substantially overlaps an attraction
  token set in a different destination.

Design intent:
- Prevent duplicate concept ownership conflicts across adjacent stops.

## Maps Fallback Query Composition
Fallback maps queries avoid contradictory location suffixes.

`_maps_fallback_query_text` rules:
- If item already mentions destination tokens, use item only.
- If item appears location-qualified (city/state/park cues), use item only.
- Otherwise append destination (`"{item} {destination}"`).

This prevents malformed fallbacks such as local districts being suffixed with
unrelated park names.

## Configuration Controls
From `config.yaml`:
- `url_discovery.uninterested_attraction_keywords`
- `url_discovery.seasonal_uninterested.ski.keywords`
- `url_discovery.seasonal_uninterested.ski.in_season_months`
- `url_discovery.enable_filtered_alltrails_selection`
- `url_discovery.alltrails_filter_max_miles`
- `url_discovery.alltrails_filter_max_gain_feet`
- `url_discovery.alltrails_filter_min_reviews`
- `url_discovery.alltrails_filter_allowed_difficulties`
- `url_discovery.alltrails_min_confidence_for_publish`
- `url_discovery.alltrails_request_delay_seconds`
- `url_discovery.alltrails_block_cooldown_seconds`
- `url_discovery.domain_block_cooldown_seconds` (generic per-domain equivalent
  of the AllTrails cooldown above, applied via `_fetch_page_text`)
- `url_discovery.direct_batch_html_failure_cooldown_seconds` (in-memory
  negative-result cooldown for a failed direct-batch harvest call, plus
  in-flight coalescing so concurrent callers for the same
  destination/kind/dates share one failure instead of each re-triggering
  the network call)
- `url_discovery.persistent_harvest_cache_ttl_hours` (on-disk cache TTL for
  successful direct-batch harvest rows, so a same-day repeat run of an
  unchanged manifest skips re-harvesting entirely)
- `url_discovery.route_distance_live_fetch_enabled` (default `true`; set
  `false` to always use the Haversine distance/time estimate instead of
  live-scraping Google Maps directions HTML -- the scrape is a pure accuracy
  enhancement on top of an estimate that's already always available)

These gates run before URL discovery for attractions.

## Search-Result Cache Audit
A same-night cost investigation (see `docs/design/per-day-item-caps.md` for
the companion item-count side of the same investigation) found real xAI
billing running well above this app's own cost estimator. Once the
estimator was fixed to include Grok's per-call web_search tool fee, a real
run priced out around **$2.46**, of which **~83% (~$2.05) was the tool fee
itself** (`C:\Temp\RoadTripRuns\SW2026-dipstick73\run-console.log`:
`grok/grok-4-fast: calls=174 ... est=$2.4366 web_search_calls=411
tool_fee=$2.0550`). The AllTrails confidence-corroboration searches
(`alltrails_confidence_corroborated_by_broad_search` /
`_denied_no_corroboration`, see "AllTrails Confidence Tiers" logic around
`_promote_alltrails_confidence`) alone accounted for ~51 log entries in one
real run -- roughly 30% of all Grok calls that run.

### Do the corroboration searches route through the cache?
Yes, already. Traced the call chain from where those two reasons get logged
back to their origin:
- The narrow, metadata-filtered corroboration path
  (`_get_filtered_alltrails_selection` -> `_search_alltrails_for_trail_filtered`)
  and the broad fallback path both ultimately call `_search_first`.
- `_search_first` checks its own in-memory, per-process `_url_cache`
  (item+dest+site-filter keyed; NOT persisted across separate CLI runs) and
  otherwise calls `_search_first_strict`, which calls `self._search_cached(full_query, ...)`
  for every query variant it tries.
- `_search_cached` (~line 11265) is the full persistent, disk-backed,
  TTL'd cache described above (`DEFAULT_PERSISTENT_SEARCH_CACHE_TTL_HOURS`).

So there is no bypass at any point in this path -- every corroboration
search a real run performs is already cache-eligible. No call-site rewiring
was needed or made.

### Real evidence: intra-run hits confirmed, cross-run hits undermined by a save bug
Grepping `C:\Temp\RoadTripRuns\SW2026-dipstick68-console.log` for repeated
corroboration queries within a single run shows the cache working exactly
as designed intra-run -- e.g. "Navajo Loop Trail" resolved fresh once, then
a second lookup for the same query later in the same run logged
`search_cache_hit` instead of another network call.

Cross-run, the picture was worse than expected: consecutive same-manifest
runs tonight (`SW2026-dipstick69` at 00:29, `SW2026-dipstick70` at 08:57,
~8.5 hours apart, well inside even the old 72h TTL) showed only a modest
call-count drop (179 -> 170 grok calls), nowhere near what a warm
cross-run cache should produce. The cause turned out to be a real bug, not
a tracing gap: 3 of those 5 consecutive runs (`dipstick69`, `70`, `72`)
logged `Persistent cache save skipped due to write error` (WinError 32 /
WinError 2 / Errno 13, all on the shared `.cache/url_discovery/persistent_cache.tmp`
path) -- meaning those runs' newly-discovered results never reached disk
for the next run to reuse. Root cause and fix are in `_save_persistent_caches`
(`generator/url_discovery.py`): destinations are processed concurrently via
a `ThreadPoolExecutor`, and the periodic mid-run checkpoint save
(`_mark_persistent_cache_dirty`'s `write_every` threshold) could fire from
any worker thread, so two threads could race to write/rename the same fixed
tmp path at once. Fixed with a dedicated lock (not the existing
`_request_cache_lock`, which at least one call site already holds while
triggering this save -- see the code comment and the
`test_save_persistent_caches_does_not_deadlock_when_dirty_mark_holds_request_cache_lock`
regression test in `tests/test_url_discovery.py`) plus a per-attempt-unique
tmp filename and a short bounded retry.

### TTL: 72h raised to 168h (7 days)
`DEFAULT_PERSISTENT_SEARCH_CACHE_TTL_HOURS` was 72h, matching
`DEFAULT_PERSISTENT_PAGE_TEXT_CACHE_TTL_HOURS` (24h) and
`DEFAULT_PERSISTENT_VERIFY_CACHE_TTL_HOURS` (12h) in spirit -- all three
were fairly short. But a search result answers a different, more static
question than those two: "what URL does this query currently resolve to",
not "is that page still live." Liveness is already independently
re-verified on its own, much shorter TTLs (verify=12h, page-text=24h,
AllTrails-fetch=12h) regardless of how long the search-result mapping
itself is cached, so raising the search TTL cannot let a closure or dead
link go undetected any longer than those already allow -- it only avoids
re-asking the same "which URL is this" question within the same week. This
mirrors the reasoning already used for the geocode (720h) and Wayback
(720h) caches just below in this file, which are long-TTL for the same
reason: querying a genuinely static fact more than once a week is pure
waste, not freshness. Raised to 168h so a work-week gap between iteration
sessions doesn't force a full re-search of everything.

One caveat surfaced during this audit: `_save_persistent_caches` re-stamps
*every* currently-loaded search-result entry with the current save time on
every save (not just entries freshly fetched that run), because
`_load_persistent_caches` doesn't carry the original on-disk timestamp
forward in memory. In practice this means the TTL rarely bites during
active same-week iteration (any dirty save refreshes the whole cache's
clock), and only really matters across a gap longer than the TTL with no
runs in between -- which is exactly the scenario the 168h extension is
aimed at protecting. This is a pre-existing characteristic of the caching
mechanism, not something this pass changed or was asked to change.

### What was intentionally left alone
Per explicit scope for this audit: no new caching infrastructure was built
(the persistent cache described above already existed and already covers
the corroboration path), and the intentionally-uncached liveness/freshness
paths (`_verify_url_cached`, page-text/AllTrails fetch, all with their own
short, independent TTLs) were left untouched -- those exist specifically to
catch a page going stale/closed, which is not this audit's concern.

## Predictive No-Verified-URL Skip Investigation (evaluated, not built)
A second same-night cost-reduction question (see
`docs/design/per-day-item-caps.md` for the companion scenic-drive-cap side
of the same investigation round): the "verified-link-or-seed policy"
already removes a non-seed attraction/restaurant/en-route stop from the
final output when no verified URL survives discovery
(`no_verified_url_removed`, logged with `message="non-seed item removed:
no real verified source URL survived discovery/audit"`). That removal
happens *after* a search call was already spent trying to find it, so it
improves output quality but saves no cost. The question was whether a
candidate item's name could be checked *before* ever searching, to predict
it will end up removed anyway and skip the search call entirely -- reusing
`_is_category_style_activity`/`_is_ambiguous_geographic_feature_name`
(existing detectors built for a different, lower-stakes purpose: deciding
whether a *found-but-generic* URL should be replaced with a maps-search
fallback), not new heuristic logic.

### Real evidence: 177 unique real failing names across 10 runs
Every `no_verified_url_removed` line across all ten real console logs with
this reason code (`C:\Temp\RoadTripRuns\SW2026-dipstick64` through
`SW2026-dipstick73`) was collected and deduplicated by item name: 308 raw
occurrences, 177 unique names. Reading the actual names shows most are
**not** vague/generic -- they're perfectly specific-sounding real places
that simply didn't corroborate a live, verified URL in that run:
- Small local restaurants with ordinary specific names: "Whiptail Grill",
  "MeMe's Cafe", "Bear Paw Cafe", "Rustler's Restaurant", "Ebenezer's Barn
  and Grill", "Zion Pizza & Noodle Co.", "Sakura". Nothing about reading
  these names in isolation predicts search failure -- they read exactly
  like the restaurants that *do* resolve successfully in the same runs.
- Minor roadside pullouts/overlooks: "Coral Pink Sand Dunes overlook
  pullout", "Checkerboard Mesa pullout", "Hatch Scenic Pullout", "Boulder
  Mountain Summit Pullout", "Chama River Gorge Turnout" -- plausibly
  genuinely obscure, but named with the same "Overlook"/"Pullout" pattern
  as thousands of real, well-indexed NPS viewpoints.
- AI-invented composite/combo names stringing two real trail segments
  together with a connector word: "Navajo Loop and Queens Garden Trail",
  "Wall Street and Queens Garden Loop Trail", "Sunset Point to Sunrise
  Point via Rim Trail", "Queen Victoria via Queen's Garden Trail", "Red
  Canyon Overlook via Canyon Rim Trail" -- the literal combined string
  rarely matches any single real page's title, even though each named
  component (Navajo Loop, Queens Garden Trail, Sunset Point, Rim Trail)
  is independently real and well-documented. This is a genuinely
  learnable-looking pattern (connector words "and"/"via"/"to ... Loop"),
  but building a detector for it would be new heuristic logic from
  scratch -- out of scope per this investigation's own ground rule -- and
  is not obviously safe either: "Navajo Loop and Queens Garden Trail" is
  itself a real, commonly-hiked combined route at Bryce Canyon with real
  dedicated write-ups, so a naive connector-word skip would likely
  mis-fire on exactly the kind of composite name that *does* resolve.

### Existing detectors cover ~8% of real failures, and even that 8% includes false positives
Running the actual 169 (name-normalized) unique failing names through both
existing detectors:
- `_is_category_style_activity` (checks for "fishing", "stargazing",
  "kayaking", etc.) flagged exactly **1** name: "Stargazing Programs".
- `_is_ambiguous_geographic_feature_name` (short name + a bare geographic
  marker word, with "trail"/"road"/"overlook"/"viewpoint"/"museum"/etc.
  explicitly *excluded* as "specific enough") flagged **13** names.

Combined, only **14 of 169 (8.3%)** of real failures would have been
caught -- nowhere near "a meaningful share." Worse, several of those 14
are false positives that would have wrongly skipped a real, findable
place: `_is_ambiguous_geographic_feature_name` flagged **"Cliff Palace
(Mesa Verde)"** (a UNESCO World Heritage cliff-dwelling site inside Mesa
Verde National Park, with an unambiguous `nps.gov` page -- flagged purely
because "mesa" is a bare geographic-marker token and the name is short),
and **"Telluride Mountain Village"** / **"Telluride Mountain Village
Gondola"** / **"Telluride Mountain Resort"** (all real, well-documented
places). This is the concrete version of the risk the project owner asked
to be evaluated honestly: `_is_ambiguous_geographic_feature_name` was
tuned for a lower-stakes decision (swap a *found* generic URL for a maps
fallback) where a false positive just means a slightly worse link; reused
as a pre-search skip gate, the same false positive means a real,
verifiable place never gets searched for at all.

The bulk of real failures (restaurants, minor pullouts, AI-invented
composite trail names) have no name-level signal either existing detector
was built to catch, and inventing new detection for them would be new
heuristic scoring logic from scratch -- explicitly out of scope for this
pass.

### Cost math confirms the juice isn't worth the squeeze
Even setting the false-positive risk aside: each `no_verified_url_removed`
item costs at most one already-cached-after-first-attempt search (the
persistent 7-day search-result cache, see above, means a repeat run
against the same manifest doesn't re-pay for the same failed query). A
predictive skip saves a small, already-partially-amortized cost while
risking a real quality regression in the direction this codebase's stated
design principle (fail-open, prefer real content over pure cost
optimization) explicitly favors avoiding.

### Conclusion: not built
No code change was made for this investigation. `_is_category_style_activity`
and `_is_ambiguous_geographic_feature_name` are unchanged, still used only
for their original maps-fallback-assignment purpose.

## Known Failure Modes and Mitigations
Issue: Valid AllTrails links removed in audit because fetch text is sparse.
- Mitigation: slug-based retention with soft-404 checks.

Issue: Place-level attractions (for example state parks or desert reserves)
incorrectly forced into AllTrails because descriptions mention trails or AI sets
`type=hike`.
- Mitigation: place-level name guard in trail-like classifier overrides type-only
	trail signals unless the attraction name itself contains explicit trail cues.
- Note: this guard includes plain `park` entities, not just `state park` or
	`national park` forms.

Issue: Scenic/en-route links resolving to AllTrails then being stripped in audit.
- Mitigation: AllTrails blocked upstream for those categories.

Issue: Apostrophe/plural token mismatch (`Angel's` vs `Angels`, `Queens` vs `queen-s`).
- Mitigation: canonical token normalization in matching logic.

Issue: `-via-` route variants are selected over canonical trail pages.
- Mitigation: canonical post-resolution refinement prefers verified non-`-via-`
	slugs that closely match the requested trail name.

Issue: a live, correct link on a bot-blocking site (TripAdvisor, etc.) gets
wrongly rejected as dead because the generic (non-AllTrails) relevance branch
treated any fetch failure as proof of death.
- Mitigation: blocked-vs-dead distinction (see "Non-AllTrails relevance"
	above), matching the AllTrails branch's already-correct handling.

Issue: under a sustained provider-side Grok outage, every item at a
destination needing the same direct-batch harvest key independently
re-triggered a full multi-attempt timeout cycle for a call that had just
failed seconds earlier, turning one slow endpoint into a pile-up.
- Mitigation: per-key negative-result cooldown + in-flight coalescing in
	`_get_direct_batch_html_rows_for_destination`
	(`direct_batch_html_failure_cooldown_seconds`).
- Related mitigation: the harvest's "insufficient rows" retry-prompt is now
	skipped while `GrokSearch`'s circuit breaker is open, since firing a
	second expensive call during a known-bad period would compound it rather
	than help.

Issue: repeated distinct URLs on the same bot-blocking domain (e.g. multiple
TripAdvisor restaurant pages) each independently paid a full fetch timeout
even after the domain had already 401/403'd once.
- Mitigation: generic per-domain block cooldown in `_fetch_page_text`
	(`domain_block_cooldown_seconds`), generalizing the AllTrails-specific
	mechanism to any domain.

Issue (Dipstick48): the direct-batch-authoritative AllTrails resolution
branch never invoked the publish-confidence gate at all -- only
`_passes_alltrails_post_search_filters`, which is a no-op for AllTrails URLs
whenever `enable_filtered_alltrails_selection` is off (the default), since
AllTrails's 403 bot-blocking makes its one remaining dead-status check
untriggerable in practice.
- Mitigation: the authoritative branch now also requires
	`_meets_alltrails_publish_confidence` to pass, unless the URL was already
	remembered as direct-batch authoritative from this run.

Issue (Dipstick48): `_prefer_canonical_alltrails_url` promoted a purely
templated slug guess (`{name-tokens}-trail`) as canonical whenever the fetch
was blocked (403) but not provably dead -- since AllTrails almost never
returns a "provably dead" status to a blocked fetch, this made the guess
promote in effect unconditionally. This fabricated
`alltrails.com/trail/us/utah/the-narrows-trail` for "The Narrows" in the
observed run.
- Mitigation: a candidate slug is now only promoted when it was positively
	verified (page fetched successfully and content matches the item). A
	blocked/inconclusive fetch keeps the original URL instead of guessing.

Issue (Dipstick48): `_build_primary_items_from_direct_batch` synthesized
brand-new attraction/restaurant items directly from harvested rows using
`row["title"]` as the item name, with none of the generic-URL/listing-page
filters used elsewhere in this module applied. When Grok's harvest surfaced a
TripAdvisor/Yelp listing page (e.g. "THE 10 BEST Restaurants in St. George -
Tripadvisor") as a row, the rendered card's *name* -- not just its link --
became the listicle headline.
- Mitigation: rows whose title matches a listing-page title pattern (`N
	BEST ...`, `Best Restaurants in/near ...`, `Things to Do in ...`, `Top N
	...`, or a `- Tripadvisor`/`- Yelp` suffix) or whose URL is otherwise
	obviously generic are now skipped entirely in this builder
	(`_is_generic_listing_title`).
- Related mitigation: `_is_generic_restaurant_landing_url` also missed
	TripAdvisor's `RestaurantsNear-g...` listing-page URL shape (no hyphen
	before "Near", unlike the `Restaurants-g...-near` pattern it already
	caught); both shapes are now covered.

Issue (Dipstick48): en-route stops never got the rating->badge extraction
attractions/restaurants receive, so a rating baked into AI-generated prose
(e.g. "Rated 4.5 stars (230 reviews)") stayed as raw text in the stop
description instead of becoming a ★ badge.
- Mitigation: `_build_getting_here` now extracts a rating from the stop's
	structured `rating`/`raw_rating` fields first, falling back to a text-based
	extraction from `description`/`practical_note`, and renders it as the same
	`badge-rating` badge attractions/restaurants use.

Issue (Dipstick48): per-item single-URL resolution (the non-batch-padding
paths for attractions, trail-like AllTrails, and restaurants) discarded
rating/vote data that was already present on the matched harvested row --
only `_build_primary_items_from_direct_batch` (the batch-shortfall padding
path) carried it over. Most items go through the per-item path, so most
attractions/restaurants never got rating data attached at all.
- Mitigation: `_direct_batch_row_quality_metadata_for_url` looks the accepted
	url back up against the already-fetched rows (zero extra network cost) and
	carries `rating`/`raw_rating`/`votes`/`source_type` onto the item. Wired
	into all three per-item acceptance sites. Attractions also gained a
	`badge-rating` badge in the renderer (previously restaurant/en-route-stop
	only).

Issue (Dipstick69): `_geocode_en_route_stop_for_route` took Nominatim's first
result unconditionally, sanity-checked only by distance from the route
midpoint. On the St. George -> Zion leg, the en-route stop "Rockville
Historic District" (a real designation with no distinctly-tagged OSM entry)
free-text-matched, inside the route viewbox, onto a completely different,
well-tagged entry named "Grafton Historic DIstrict" -- the real-world
location of "Grafton Ghost Town", a separate en-route stop on the same leg
~3 road miles away. Because both names share the generic words
"historic"/"district", a naive "shares no token" check would not have caught
it either. The distance check didn't catch it because Grafton sits well
inside the route viewbox. Consequence: the rendered Google Maps directions
URL listed "Grafton Ghost Town" as a waypoint twice (once via its own
name-string fallback, once via Rockville's mis-geocoded coordinates, which
Google's UI reverse-geocodes back to Grafton).
- Mitigation: `_geocode_result_name_plausible` compares the query's
	significant tokens against the result's own `name`/`display_name` after
	excluding a small set of generic place-designation words ("historic",
	"district", "downtown", "village", "town", "area", "neighbo(u)rhood") from
	both sides, requiring the remaining identifying ("anchor") tokens to
	overlap. A result that fails is skipped (`continue`), not treated as a
	final failure -- the function keeps trying its other query/viewbox
	attempts, same as the existing out-of-region rejection.


Issue (Dipstick69): `_alltrails_slug_matches_item` (used by every AllTrails
acceptance path, including `_search_alltrails_for_seed_relaxed`) does pure
token-overlap matching, so a slug with extra trail-name content beyond the
item's own tokens still passes as long as the item's tokens are a subset.
Bryce Canyon's "Navajo Loop Trail" (~1.3mi loop) matched
`navajo-loop-trail-to-peekaboo-loop` -- a real but different, ~5.3mi combined
route joining two trails -- because "navajo"/"loop"/"trail" are all present
in the slug. The rendered card's own "1.3 mile loop" description ended up
hyperlinked to a page describing a 5.3-mile route.
- Mitigation: a slug containing AllTrails' "-to-" combined-route naming
	convention (`trail-a-to-trail-b`) is now rejected unless the item's own
	name also contains the word "to" (i.e. the trip owner's own seed
	legitimately describes a combined route). Fixed centrally in
	`_alltrails_slug_matches_item` rather than only in the seed-relaxed path,
	since every one of its ~10 call sites shares the same false-positive risk.

Issue (Dipstick69/70): the direct-batch row-matched leniency paths (both the
restaurant-specific "item-matched authoritative direct-batch URL" block and
the shared attraction/en-route-stop/restaurant "row-matched" block in
`_retain_discovered_url`) accept a candidate URL once its search-result row
matches the item, without ever checking where the URL actually resolves. Real
example: the en-route stop "Poshuouinge Pueblo Ruins" (Santa Fe leg, via
Pagosa Springs) was linked to
`fs.usda.gov/recarea/carson/recarea/?recid=44248` -- a URL whose
distinguishing `?recid=` query param made it look item-specific, so it
cleared `_is_generic_section_landing_page`'s pure URL-string check. Live
verification (2026-08-18) confirmed this exact URL 301-redirects to
`fs.usda.gov/r03/carson/recreation`, a generic Carson National Forest
recreation hub page with zero mentions of "Poshuouinge" anywhere in its body.
Notably, that final path's own last segment ("recreation") isn't in
`_is_generic_section_landing_page`'s `generic_sections` set either, so simply
re-running that existing URL-string heuristic against the final URL would not
have caught this specific case -- redirect targets need a content-relevance
check, not just a second URL-shape check.
- Mitigation: `_redirect_target_lacks_item_relevance` looks up the URL in
	`_fetch_final_url_cache` (already populated by `_fetch_page_text` whenever a
	fetch follows a redirect -- see `_fetch_page_text_uncached`) after the row-
	matched leniency's own fetch has run. If the final URL differs from the
	original, it is checked two ways: (1) the same URL-string heuristic used
	elsewhere (`_is_generic_section_landing_page` for
	attraction/en-route-stop/generic kinds, `_is_generic_restaurant_landing_url`
	for restaurants), and (2) whenever fetch text is available, whether the
	item's own significant tokens actually appear in it
	(`_text_matches_item_tokens`) -- this second check is what catches the
	Poshuouinge case, since the URL-shape heuristic alone does not. Either
	signal being generic/absent rejects the candidate. A redirect by itself is
	not disqualifying -- only a redirect landing somewhere that neither looks
	nor reads as item-specific is. Wired into both leniency call sites in
	`_retain_discovered_url` (search `_redirect_target_lacks_item_relevance`).

## Must-See Badge Policy
The "Must-See" badge is a deterministic, render-time decision -- not the
LLM's opinion. The model still emits a `must_see` boolean per attraction
(`prompts/destination_content.txt`, capped at 2 per destination in
`_normalize_attractions`), but that field is now used only as an inclusion
priority signal for attraction-list pruning
(`_prune_attractions_to_target`'s sort key); it does not by itself earn the
badge.

Rationale: `must_see` is unverified LLM judgment, and "must-see" is
simultaneously one of the exact phrases `prompts/system_prompt.txt`'s banned
marketing-cliche list forbids in prose. Trusting the model's own flag to
print that literal phrase as a UI label was inconsistent with that policy.

Badge eligibility (`HTMLAssembler._build_attractions`):
- Requires verified `rating >= 4.5` AND `votes >= 20` on the item (populated
	during URL discovery -- see above).
- Capped at the top 2 qualifying items per destination, ranked by rating then
	votes, matching the original "if everything is must-see, nothing is"
	intent.
- An item with the LLM's `must_see: true` but no corroborating rating data
	does not get the badge. An item the model never flagged, but which clears
	the threshold, does.

Known consequence: attractions resolved via a discovery path with no
harvested-row data behind it (pure AI-candidate/generic-search resolution,
no direct-batch row match) carry no rating and are therefore never eligible
for the badge, regardless of actual quality. This is intentional -- the
policy is to never fabricate the signal -- but means badge coverage tracks
data availability, not just destination quality.

## Key Files
- `generator/url_discovery.py`
- `generator/url_validator.py`
- `generator/grok_search.py`
