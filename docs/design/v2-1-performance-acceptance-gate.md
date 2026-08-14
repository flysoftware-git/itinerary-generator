# V2.1 Performance Acceptance Gate

Purpose: determine whether v2 is complete as a performance architecture shift, or only a structural groundwork release.

Status: Gates A-C completed; Gate D pending.

## Decision Statement

Current evidence indicates v2.0 is architecture groundwork with partial operational benefits, not yet a completed performance/cost shift.

Evidence basis:
- User-observed cost movement: $4.0653 to $3.8300 (delta $0.2353, 5.79%).
- Latest full-run runtime profile remains dominated by stages 4-5:
  - `stage_4_5_parallel`: 29714.926 s
  - `total_pipeline`: 29799.716 s
  - Share: 99.72%
- Selective retry did not engage in that run:
  - `retry_candidate_count`: 0
  - `retried_destination_count`: 0

Reference artifact: `output/dev/run_ledger.jsonl` run `20260731T055856.595756Z`.

## Gate Structure

The gate is sequential. Any failed gate blocks promotion to "full shift achieved".

### Gate A: Measurement Completeness

Status: completed (2026-08-01)

Goal: prove that batching effects can be measured directly, not inferred.

Required instrumentation additions:
1. Per-stage provider call counters:
   - AI generation calls
   - URL discovery search calls
   - URL audit fetch calls
   - image fetch/search calls
2. Per-stage cost attribution:
   - estimated USD by stage
3. Per-stage throughput:
   - entities processed per minute
4. Batch-work ratio metrics:
   - requests avoided vs naive per-entity flow
   - destinations covered per provider request where applicable

Pass criteria:
1. Metrics are written to run ledger for all full runs.
2. Metrics can be compared between baseline and candidate runs without manual parsing.

Implementation notes:
1. New payload location: `runtime_metrics.gate_a` in `output/dev/run_ledger.jsonl`.
2. Includes stage call counters, stage cost attribution, stage throughput, and batch-work ratio metrics.
3. Includes image/provider and URL-validation HTTP counter deltas captured across Stages 4-5.

### Gate B: Baseline Stability

Status: completed (2026-08-01, first-destination controlled baseline)

Goal: establish a reliable reference before optimization claims.

Protocol:
1. Use one fixed manifest and one fixed config profile.
2. Execute 3 full runs under equivalent conditions.
3. Compute median values for cost and duration.

Pass criteria:
1. Coefficient of variation for total cost <= 10%.
2. Coefficient of variation for total runtime <= 15%.
3. No validation blocker regressions in output artifacts.

Run set and result summary:
1. Sample runs (latest 3, fixed manifest/config/env, `--first-destination`):
   - `20260801T200405.724147Z`
   - `20260801T201216.562327Z`
   - `20260801T201909.485273Z`
2. Median metrics:
   - cost (USD): `0.536698`
   - total pipeline seconds: `408.034`
3. Variability:
   - cost CoV: `3.659%` (pass)
   - runtime CoV: `4.887%` (pass)
4. Validation status:
   - no validation blockers observed in these baseline runs.

### Gate C: Performance Improvement Thresholds

Status: completed (2026-08-02 candidate validation)

Goal: verify meaningful improvement, not noise-level movement.

Candidate thresholds (relative to Gate B baseline medians):
1. Total cost reduction >= 20%.
2. Total pipeline runtime reduction >= 25%.
3. Stage 4-5 combined runtime reduction >= 30%.
4. Provider work-unit reduction (calls or equivalent) >= 25% in at least two expensive stages.

Pass criteria:
1. At least 3 of 4 thresholds pass.
2. The cost threshold must pass.

Current candidate evaluation (vs Gate B baseline medians):
1. Baseline medians:
   - cost: `0.536698`
   - total pipeline: `408.034s`
   - stage 4-5: `372.333s`
3. Latest candidate run: `20260802T024729.794862Z`
   - cost: `0.387125` (`27.869%` reduction, pass)
   - total pipeline: `254.322s` (`37.671%` reduction, pass)
   - stage 4-5: `223.208s` (`40.052%` reduction, pass)
   - provider work reduction signals:
     - URL search calls `38` vs baseline median `62` (`38.71%` reduction, pass)
     - AI generation calls `4` vs baseline median `4` (`0%` reduction, still flat)
3. Gate C status:
   - Cost threshold: pass
   - Runtime thresholds: pass
   - Overall: pass, because 3 of 4 thresholds passed and the cost threshold passed.

### Gate D: Quality Guardrail Before Defect Intake Expansion

Goal: avoid mixing architecture uncertainty with broad regression triage.

Policy:
1. Keep broad link-curation defect logging in hold status until Gates A-C pass.
2. Allow only blocker defects that prevent gate measurement or produce invalid artifacts.

Exit criteria:
1. Gates A-C passed.
2. Defect intake re-opened for non-blocker link-curation regressions.

## Classification Rules

Use this rubric after each gate cycle:
- If Gate A fails: architecture is unmeasurable and still groundwork.
- If Gate A passes but Gate C fails: architecture is implemented but optimization impact is insufficient.
- If Gates A-C pass: full performance shift achieved.

## Immediate Next Steps

1. Continue Gate C optimization with focus on stage 4-5 latency reduction.
2. Reopen broad link-curation defect logging now that Gates A-C have passed.
3. Run at least 2 additional candidate samples to confirm repeatability of the cost and runtime deltas.

## Notes On Current Scope

This gate does not redefine v2.0 behavioral correctness.
It tests whether the performance intent of the rearchitecture has been met to a practical degree.
