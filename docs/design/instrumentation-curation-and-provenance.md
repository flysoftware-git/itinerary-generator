# Instrumentation for Curation and Provenance

Purpose: define measurable signals for URL/trail curation, make provenance decisions auditable, and separate data-quality failures from policy decisions.

## Why This Matters

Reliable transforms require feedback loops. A transform that appears to work on one run can silently drift if upstream search snippets, site slugs, or anti-bot behavior changes. Instrumentation provides:
- observability (what happened),
- explainability (why a decision was made),
- and control (which decisions are accepted for publication).

## Scope

This note covers instrumentation for:
- discovery candidate collection,
- transform/canonicalization steps,
- curation decisions,
- provenance state transitions,
- and report-level quality summaries.

## Core Measurement Model

Each candidate URL should emit a lifecycle record with these fields:
- trace_id: stable identifier for entity+destination+kind.
- destination_name, entity_name, category.
- source_query: exact query string used.
- source_rank: rank/order from search provider.
- raw_url: unmodified URL from provider.
- transformed_url: URL after transform pipeline.
- transform_chain: ordered list of transform steps applied.
- transform_changed: boolean for raw_url != transformed_url.
- transform_confidence: numeric confidence from transform policy.
- relevance_score: selection/relevance score.
- maps_match_score: optional corroboration score.
- metadata_present: booleans for rating/reviews/difficulty/miles/gain.
- validation_status: HTTP/check result and policy checks.
- disposition: final decision label.
- disposition_reason_codes: normalized reason codes used.

## Metrics to Track

### Candidate and Transform Health
- candidates_observed_total
- candidates_kept_total
- transform_applied_total
- transform_changed_rate
- transform_revert_rate (when transformed URL later fails checks and raw alternative survives)
- unique_slug_collision_rate (multiple raw URLs collapsing to one slug key)

### Quality Gates
- metadata_partial_rate
- maps_corroborated_rate (maps_match_score >= corroboration threshold)
- weak_maps_match_rate (threshold_low <= maps_match_score < threshold_high)
- uncorroborated_rate
- url_policy_rejection_rate by class
- final_publish_rate by category

### Provenance Integrity
- unresolved_to_verified_transition_rate
- candidate_observed_to_rejected_rate by reason_code
- fallback_only_rate (no canonical accepted)
- cross-destination_entity_conflict_count

### Experiment Reliability
- cache_hit_rate_search
- cache_hit_rate_verify
- cache_hit_rate_page_text
- stale_cache_suspect_rate (decision changed after forced fresh run)
- rerun_drift_rate (same inputs, different accepted URLs)

## How Measurements Are Used

### Curation Decisions
- If transform_revert_rate rises, reduce transform aggressiveness and require stronger evidence before path rewriting.
- If weak_maps_match_rate spikes for a destination, downgrade publish confidence or flag manual review.
- If metadata_partial_rate is high, keep links but route them into review-needed buckets for downstream rendering policy.

### Provenance Controls
- Publishable canonical links require deterministic evidence and acceptable disposition classes.
- Fallback links are explicitly tracked as secondary evidence and never promoted to canonical without verification.
- Reason-code aggregates per destination are used as release gates for quality checks.

### Policy Tuning
- Thresholds (maps corroboration, metadata requirements, minimum reviews, difficulty limits) should be tuned from measured precision/recall tradeoffs across destinations.
- Transform rules should be added only when they improve aggregate correctness and reduce rerun drift.

## Operational Guidance for Experiments

Persistent caches can mask regressions or preserve stale provider output. For experiment runs:
- run A/B: cached run versus fresh-cache run,
- compare accepted URLs and disposition distributions,
- compute stale_cache_suspect_rate and rerun_drift_rate.

Recommended protocol:
1. Baseline run with current cache.
2. Fresh-cache run (clear persistent cache file or disable persistent cache temporarily).
3. Diff accepted URLs and reason-code counts.
4. Promote transform/policy changes only if quality improves in both modes.

## Minimal Reporting Contract

Per run, emit:
- destination summary: kept count, rejected count, fallback-only count.
- disposition histogram by destination.
- top reason codes by count.
- transform summary: applied/changed/reverted rates.
- cache summary: hit rates and stale-cache suspects.

This report should be versioned with run metadata (config hash, manifest hash, timestamp) so decisions are reproducible.

## Integration Points in Current Code

Current instrumentation signals already exist in URL discovery logging and stats aggregation, including reason-code counting. Extend that surface to include transform-chain accounting and cache-hit accounting for discovery experiments.

Related design docs:
- provenance-control-and-scheduling-rationalization.md
- url-discovery-and-audit.md