# Restaurant Discovery, Ranking, and Linkage

## Purpose
This note explains how restaurant links are discovered, what ranking/relevance logic is used, and how the final clickable URL is chosen in rendered output.

## Pipeline Position
Restaurant link handling spans two stages:
1. URL discovery (`generator/url_discovery.py`)
2. HTML assembly (`generator/html_assembler.py`)

AI content generation produces restaurant names and descriptions only. URL discovery resolves links afterward.

## Discovery Strategy
`URLDiscoverer._discover_restaurants` runs a two-pass search per restaurant:

1. Pass 1: `site:google.com/maps`
2. Pass 2 (fallback): `site:tripadvisor.com`

Stored fields:
- `url`: best discovered verified URL (maps place/listing or tripadvisor)
- `maps_url`: deterministic Google Maps search fallback query

## Candidate Ranking and Relevance
Discovery uses the shared strict selector (`_search_first` / `_search_first_strict`):
- Query variants from `_build_query_variants`
- Specific-page pass, then general-live pass
- Relevance checks (`_is_relevant_result`)
- Candidate scoring (`_score_candidate_result`)

Scoring factors include token overlap, domain/path hints, destination hints, and specificity bias.

Restaurant rating-priority behavior:
- Rating signals from candidate title/snippet are used as a ranking boost only
	when both conditions are true:
	- rating is at or above configured minimum
	- review/vote count is at or above configured minimum
- High ratings with low vote counts do not receive a priority boost.
- If rating metadata is absent, candidate selection continues using non-rating
	relevance and scoring signals.

## Category Policy
For restaurants:
- AllTrails is disallowed in audit (`allow_alltrails=False`)
- Generic or non-relevant URLs are stripped in audit
- `maps_url` remains available as deterministic fallback

## Final Link Selection in HTML
Restaurant links are rendered by `HTMLAssembler._build_restaurants`.

Current selection order:
1. Prefer normalized discovered `url`
2. Else use normalized `maps_url`
3. Else synthesize a maps-search query from name + destination

Why this order:
- Avoid overriding a valid discovered listing (for example TripAdvisor or a stable maps listing) with a potentially brittle generic maps-search query.

**When the maps-search fallback is not acceptable:**
A synthesized `maps/search/` query is never an acceptable published link for a **named restaurant**.
If both `url` and `maps_url` are absent or resolve to a maps-search query, the correct
behavior is to render the restaurant **without a hyperlink**, not to publish an area-query page.
Rationale: a named restaurant card with a search-query link implies a specific target that the
link does not deliver, violating the named-entity fail-closed policy in §5 of the requirements.

## Why Some Maps Search Links Can Be Fragile
A maps search query can still fail to resolve exactly if:
- business naming changed
- punctuation/alias mismatch
- local map indexing changed

Using discovered canonical URLs first reduces this fragility.

## Troubleshooting Checklist
Symptom: Restaurant link opens wrong place or no place.
- Check whether `url` was discovered and retained after audit.
- Check whether renderer fell back to `maps_url`.
- Verify destination suffix in maps fallback query text.

Symptom: Restaurant always maps-search despite good discovered URL.
- Check renderer selection order in `_build_restaurants`.

Symptom: Restaurant URL missing entirely.
- Inspect discovery logs for pass-1/pass-2 misses and audit rejection reasons.

## Key Files
- `generator/url_discovery.py`
- `generator/html_assembler.py`
- `tests/test_html_assembler.py`
