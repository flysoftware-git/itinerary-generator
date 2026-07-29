# V2 Kickoff Checklist (Issue #6)

Purpose: start v2 work on a separate branch while preserving v1.4 behavior as a stable baseline.

## 1. Baseline Preservation

- [ ] Confirm current baseline commit hash: `9c9b0da`.
- [ ] Create annotated release tag from baseline:
  - `git tag -a v1.4.0 -m "v1.4.0 baseline before v2/Issue #6" 9c9b0da`
- [ ] Push tag:
  - `git push origin v1.4.0`
- [ ] Verify tag points to expected commit:
  - `git show v1.4.0 --no-patch --oneline`

## 2. V2 Branch Setup

- [ ] Create Issue #6 branch from baseline tag:
  - `git checkout -b issue-6-v2 v1.4.0`
- [ ] Push branch and set upstream:
  - `git push -u origin issue-6-v2`
- [ ] Add branch protection policy (if using hosted repo rules):
  - Require PRs for merge to `main`.
  - Require tests/checks on `issue-6-v2` PRs.

## 3. Environment Tagging Plan (dev/tst/prod)

Current state:
- CLI accepts `dev|test|prod` in [generator/main.py](../../generator/main.py).
- Ledger currently writes under `output/dev/run_ledger.jsonl`.

Decisions to lock before implementation:
- [ ] Canonical test label: choose one of:
  - `test` (keep current CLI choices), or
  - `tst` (add alias and normalize to `test` internally).
- [ ] Output layout policy:
  - default environment subfolders, or
  - opt-in subfolders with a strict flag.
- [ ] Ledger path policy:
  - ledger must be environment-resolved, not hardcoded to `dev`.

Implementation checklist:
- [ ] Update resolved environment normalization in [generator/main.py](../../generator/main.py).
- [ ] Write run ledger to environment-aware path (for example `output/<env>/run_ledger.jsonl`).
- [ ] Ensure ledger entry always stores resolved environment (`dev|test|prod`).
- [ ] Add/adjust tests for environment behavior and ledger location.

## 4. Issue #6 Requirements Intake

Use this section to attach agreed requirements before coding.

- [ ] Problem statement (what changes, what stays the same).
- [ ] In-scope components.
- [ ] Explicit non-goals.
- [ ] Acceptance criteria.
- [ ] Rollout strategy and kill-switch/feature-flag plan.
- [ ] Cost guardrails (target run-cost threshold).
- [ ] Risk controls and rollback plan.

### 4.1 Intake Template (Fill This In)

Copy this block for each requirement group you want to add under Issue #6.

```text
Requirement Group Name:
Owner:
Date:
Source (issue/experiment/doc):

Problem Statement:
-

Current Behavior (v1.4 baseline):
-

Target Behavior (v2):
-

In Scope:
- 

Out of Scope / Non-Goals:
-

Constraints:
- Cost:
- Runtime:
- Data quality:
- Backward compatibility:

Acceptance Criteria:
1.
2.
3.

Test Plan:
- Unit tests:
- Integration tests:
- Golden manifest checks:

Observability:
- Required logs:
- Required metrics:
- Failure signals:

Rollout Plan:
- Feature flag name:
- Default state:
- Environments enabled first:
- Rollback trigger:

Dependencies / Sequencing:
- Blocks:
- Blocked by:

Open Questions:
-
```

### 4.2 Baseline Snapshot (Before Issue #6 Changes)

Fill this once before coding starts so post-change deltas are unambiguous.

- Baseline tag/commit: `v1.4.0` / `9c9b0da`
- Baseline manifests:
  - `C:/Dev/Sandbox/sw_manifest.yaml`
  - `trip_manifest.yaml`
- Baseline cost (full run):
  - Predicted USD:
  - Actual USD:
- Baseline quality summary:
  - resolved_exact:
  - resolved_fallback_query:
  - unresolved:
  - rejected:

## 5. Regression Guardrails (Protect v1.4 Gains)

- [ ] Keep existing URL/search/validation tests green:
  - [tests/test_url_discovery.py](../../tests/test_url_discovery.py)
  - [tests/test_html_validator.py](../../tests/test_html_validator.py)
  - [tests/test_ai_content_normalization.py](../../tests/test_ai_content_normalization.py)
- [ ] Add golden-manifest regression checks with at least:
  - `C:/Dev/Sandbox/sw_manifest.yaml`
  - `trip_manifest.yaml`
- [ ] Track per-run quality counters:
  - `resolved_exact`
  - `resolved_fallback_query`
  - `unresolved`
  - `rejected`
- [ ] Fail PR check when quality regresses beyond agreed thresholds.

## 6. Cost-Control Checkpoints for V2

- [ ] Define target max run-cost for full manifest.
- [ ] Record baseline cost from latest full run for comparison.
- [ ] Add periodic cost checkpoints per milestone.
- [ ] Require cost delta summary in each Issue #6 PR.

## 7. PR/Issue Workflow

- [ ] Open parent tracking issue: "V2 / Issue #6 execution plan".
- [ ] Create child PRs in small slices:
  1. env tagging + ledger path correctness
  2. URL state instrumentation
  3. Issue #6 core implementation
  4. quality/cost reporting enhancements
- [ ] Require each PR to include:
  - behavior summary
  - test evidence
  - cost impact
  - rollback notes

## 8. Ready-to-Start Gate

Only begin core Issue #6 coding after all checks are complete:

- [ ] Baseline tag created and pushed.
- [ ] v2 branch created from tag.
- [ ] environment tagging decisions finalized.
- [ ] requirements and design inputs attached.
- [ ] regression and cost guardrails agreed.