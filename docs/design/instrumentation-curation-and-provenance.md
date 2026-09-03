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

### Counts are not evidence; names are (2026-08-29)

The quality gate reported "restaurants removed for no verified URL: 39" and no
artifact said *which* 39. The names were on the `_registry_decisions` records
the whole time -- counted, then discarded -- and neither
`destination_status_report.json` nor `run_ledger.jsonl` kept them.

That gap was expensive. A high removal rate coincided with an exhausted Serper
balance, so the balance looked like the cause; confirming otherwise took topping
up the account and a full paid rerun, which returned an identical 39. A list of
names would have shown at a glance that Chez Leon and Atomium are not search
failures. Three further explanations were offered and discarded the same way
before the audit was built.

Now recorded per destination in `removed_no_verified_url`, with each item's
`candidate_trail`: the URLs it was offered and the check that refused each.
`candidates_considered: 0` distinguishes "nothing was ever found" from "a link
was found and rejected" -- different problems needing opposite fixes. This is
what identified the Maps-text-query and trail-gate defects within one run each.

Three cautions learned building it, each of which produced a confident-looking
but wrong picture:

- **Retried destinations double-count.** `_registry_decisions` and the
	disposition threads both accumulate across passes and neither resets. The
	first fix cleared one and not the other, so the counts became right while
	`candidates_considered` stayed inflated. Old Hickory recorded 37 removals
	for 20 distinct items; Brussels carried 65 duplicated events of 131. In both
	cases the duplicated destinations were exactly the retried ones.
- **The trail read the wrong keys.** Events store `reason`/`source`/`url`; the
	extraction read the `_log_decision` *parameter* names. Every removed item in
	a full run reported "0 candidates considered", which reads as a finding
	rather than a broken read. The tell was 238 captured events against 33 items
	all reporting zero.
- **The trail stopped at the stage boundary.** `_trace_id` keys on
	`kind|destination|item`, so `search` and `audit` events for the same item
	live in separate threads. Reading only the removal's own kind hid the step
	that actually discarded the URL.

The pattern in all three: a measurement narrower than the question it was built
to answer, producing an answer that looked complete. Prefer asserting on
compiled/observed behaviour over on the shape a value is expected to have.

## Reading the output is not opening it (2026-09-03)

Every defect in the 2.7.0 range was invisible to inspection of the generated
HTML and obvious the moment a link was opened in a browser:

- 83 Maps links returned "an API is required". The HTML was well-formed and
	the URLs looked plausible; only the scheme was wrong (`api=1` absent).
- Route panels labelled stops by reverse geocoding — "Millcreek 2nd post
	market" for "Red Cliffs National Conservation Area Overlook". The URL was
	correct; Google's rendering of it was not what the itinerary said.
- "The Hermitage" and "Andrew Jackson's Hermitage" were the same place, and
	"The Hermitage Hotel" a different one. All three links were correct. Only
	seeing the resolved names side by side showed the ambiguity.

The pattern: a URL can be syntactically valid, semantically correct, and still
render as something the reader cannot reconcile with the page. Structural
checks — does the anchor exist, does the href parse, does the class match —
cannot see any of that.

Where this matters, verify by rendering. `mcp__Claude_Browser__navigate` plus
`get_page_text` is enough to confirm what a link actually resolves to, and
`javascript_tool` reading the directions panel's input values is enough to see
whether a route's waypoints are the stops the card named.

### The recurring shape: a check narrower than its question

Six times in this work a test or probe passed while the thing it existed to
check was broken:

| check | what it asserted | what it missed |
|---|---|---|
| link classes | the 7 names already changed | anchors selected by descent |
| place URL builder | the exact string the code returned | that the string was a valid scheme |
| retention exits | one exit id | 29 others, then stale instance state |
| removal trail | one event `kind` | `search` and `audit` stages |
| cache save guard | the loop that crashed | six sibling loops |
| restaurant cap | the function in isolation | that reconciliation undid it |

Each was written after the fix, against the shape of the fix. The ones that
held asserted a *property* instead — no bare `return ""`, every anchor rule on
the token, `api=1` present, the cap runs after reconciliation — and several of
those caught a later mistake within the hour.

## Integration Points in Current Code

Current instrumentation signals already exist in URL discovery logging and stats aggregation, including reason-code counting. Extend that surface to include transform-chain accounting and cache-hit accounting for discovery experiments.

Related design docs:
- provenance-control-and-scheduling-rationalization.md
- url-discovery-and-audit.md