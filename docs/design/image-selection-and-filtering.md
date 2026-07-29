# Image Selection and Filtering

## Purpose
Image selection provides destination visuals that are relevant, safe to render,
and portable in generated output. The pipeline balances source diversity,
destination relevance, and strict mismatch filtering.

## Entry Point
`ImageFetcher.fetch_all(trip)` in `generator/image_fetcher.py`.

Per destination behavior:
- Fetch candidates from multiple sources.
- Rank/filter by destination relevance.
- Download locally and attach `local_path`.
- Enforce minimum image count.

## Provider Strategy
Order of candidate collection:
1. NPS API (when `nps_park_code` is present)
2. Unsplash search (preferred general source)
3. Wikimedia search fallback

If still below minimum image count:
- Run fallback Wikimedia queries (landscape/mountains/aerial/scenic variants)
- Retry up to configured fallback-attempt cap

## Verification and Materialization
Candidates are not considered final until downloadable.

`_verify_and_materialize`:
- Re-ranks candidates
- Deduplicates by URL
- Downloads image bytes to output directory
- Normalizes metadata fields (`title`, `credit`, `license`, `source`)
- Stops at `max_per_destination`

Final HTML references local files via portable relative paths.

## Ranking Model
`_rank_images_for_destination` computes destination-aware relevance:
- Base score from destination token overlap in title/credit/url
- Positive-context boosts
- Negative-context penalties

Additional gates:
- Global blacklist filter
- Hard marine mismatch reject for inland/desert contexts
- Optional `required_any` term gate for high-ambiguity destinations
- Prefer non-negative candidates when available
- Prefer strictly positive score candidates when available

## Destination Disambiguation
`_destination_image_profile` provides dynamic positive/negative token sets.

Special handling includes:
- Inland/desert contexts penalize marine/ocean terms.
- Capitol Reef receives extra anti-marine disambiguation and required cues
	to avoid coral-reef confusion.

`_location_tokens` also removes ambiguous `reef` token for Capitol Reef cases.

## Content Safety and Blacklists
Two levels of hard rejection exist:
1. Global blacklist terms (configurable): `images.never_content_terms`
2. Built-in marine mismatch terms for inland/desert destinations

If all candidates are filtered at a hard-reject stage:
- Function can return empty set, causing fallback attempts or eventual hard fail.

## Caching Behavior
Cache location:
- `.cache/images/cache_index.json`

Cache key shape:
- `v2::{normalized_destination_name}::{nps_code_or_none}`

Cache semantics:
- TTL controlled by `images.cache_ttl_hours`
- `force_refresh` bypasses cache reuse
- Cache stores slim metadata records; verification/download still re-checks viability

## Configuration Controls
From `config.yaml`:
- `images.min_per_destination`
- `images.max_per_destination`
- `images.cache_ttl_hours`
- `images.never_content_terms`

## Known Failure Modes and Mitigations
Issue: Inland destination gets marine imagery.
- Mitigation: hard marine mismatch filters + profile negatives + required cues.

Issue: Metadata noise pollutes captions.
- Mitigation: `_sanitize_metadata_text` removes markup, URLs, and noisy markers.

Issue: Good candidates exist but download fails repeatedly.
- Mitigation: verification requires successful download; failures trigger fallback queries.

Issue: Cached records stale or irrelevant.
- Mitigation: TTL expiration and `force_refresh` option.

## Troubleshooting Checklist
Symptom: Too few images for a destination.
- Check provider API availability and keys (`NPS_API_KEY`, `UNSPLASH_ACCESS_KEY`).
- Check blacklist aggressiveness in config.
- Check cache reuse logs and try refresh.

Symptom: Captions are empty/noisy.
- Inspect metadata sanitizer markers and source metadata quality.

Symptom: Repeated marine mismatch for desert parks.
- Verify destination name tokens and profile cues include expected location terms.

## Key Files
- `generator/image_fetcher.py`
- `config.yaml`
