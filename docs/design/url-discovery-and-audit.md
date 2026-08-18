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
