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
- On miss, fallback maps URL is generated.

Restaurant:
- Two-pass strategy:
	1) `site:google.com/maps`
	2) `site:tripadvisor.com`
- Also stores `maps_url` fallback for rendering.

En-route stop:
- AllTrails explicitly disallowed at discovery time (`allow_alltrails=False`).
- Missing URLs fall back to maps query.

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
- Requires successful page text fetch.
- Requires item-token match in content.
- Requires some destination-token presence in content.

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

## Fail-Closed Policy for Named Entities
A link is only publishable for a named entity if it is a **deterministic, entity-specific
target** — one that refers to that single entity and not a list, search query, or area
reference.

Consequences:
- `google.com/maps/search/<name>+near+<destination>` is an area-reference query; it must
  not be published as the link for a named restaurant, attraction, or stop.
- When no entity-specific URL survives audit, the item is rendered **without a link**.
  This is the correct fail-closed behavior, not a degraded fallback.
- The synthesized `maps_url` search query is only acceptable as a last-resort context link
  for category-level items where no single entity is implied (for example a destination
  overview card), never for individually named subjects.

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

## Key Files
- `generator/url_discovery.py`
- `generator/url_validator.py`
- `generator/grok_search.py`
