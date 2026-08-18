# Fallback Curation Contract

## Purpose
Define who owns fallback behavior at each stage of the pipeline so link handling is deterministic and auditable.

This note standardizes four terms:
- harvesting: collect candidate URLs and fallback metadata
- qualification: decide whether a candidate is valid for the requested entity
- curation: apply policy and trust gates before publish
- publication: decide what is actually rendered as clickable output

## Ownership by Stage

### 1) Harvesting (URL discovery)
Owner: `URLDiscoverer` in `generator/url_discovery.py`

Responsibilities:
- collect direct candidates from source mode (`search`, `direct-link-batch`)
- extract additional URLs embedded in row snippet/description text
- preserve explicit fallback metadata fields (`maps_url`) where applicable
- record decision telemetry for accepted/rejected candidates

Harvesting does not grant publishability. It only builds candidate/fallback state.

### 2) Qualification (candidate-level relevance)
Owner: `URLDiscoverer` relevance + retention gates in `generator/url_discovery.py`

Responsibilities:
- evaluate candidate relevance to item and destination context
- apply category constraints (for example, AllTrails allowed only for trail-like attraction contexts)
- apply structural checks (generic landing pages, token mismatch, synthetic maps place patterns)
- apply AllTrails checks (slug match, soft-404 markers, redirect mismatch)

Qualification is where most candidates are eliminated before final curation.

### 3) Curation (audit and policy enforcement)
Owner: `URLDiscoverer.audit_discovered_urls` and `_retain_discovered_url`

Responsibilities:
- re-check retained URLs with final policy gates
- enforce blocked URL classes by mode (`off`, `monitor`, `enforce`)
- enforce domain denylist and entity-integrity rules
- apply section constraints and cleanup (scenic-drive route-intent checks, closure gates, trail-threshold demotion)
- keep or remove canonical URLs, and keep fallback metadata where policy allows

This stage decides publishable URL state. Renderer must not override it with synthesized named-entity links.

### 4) Publication (render-time selection)
Owner: `HTMLAssembler` in `generator/html_assembler.py`

Responsibilities:
- choose preferred external link from curated fields (`url`, `maps_url`) via section rules
- suppress ambiguous map-search and directions endpoints for named-entity primary links
- render plain text when no publishable link is available (section-dependent)

Section behavior:
- attractions: publish canonical URL when valid; otherwise plain text name
- en-route stops: publish curated stop URL when valid; otherwise plain text stop name
- restaurants: if curated canonical URL is unavailable, publish explicit lookup link (`google.com/search?q=`) built from name + destination
- events: use normalized event URL when present; otherwise query-based lookup link

## Direct-Link Batch Authoritative Rules
When direct-link batch is authoritative:
- matched row candidates are harvested first
- non-map snippet/source URLs may be accepted even when URL path tokens are weak, provided row-level entity match and retention gates pass
- map/search candidates remain stricter and are frequently rejected for named-entity publication
- if no acceptable candidate survives, canonical URL remains empty and publication follows section fallback behavior

## Trail Threshold Demotion Contract
For trail-like attractions exceeding configured thresholds:
- canonical trail URL is removed
- item is demoted to non-hike attraction type
- threshold rationale is appended to practical note
- maps fallback context is retained for route utility

This is a curation decision, not a renderer heuristic.

## Non-Goals
- This contract does not define ranking weights.
- This contract does not define prompt-level content generation.
- This contract does not replace section-specific policy notes; it links them.

## Primary References
- `generator/url_discovery.py`
- `generator/html_assembler.py`
- `docs/design/url-discovery-and-audit.md`
- `docs/design/restaurant-discovery-ranking-linkage.md`
- `docs/requirements.md` (Section 5)