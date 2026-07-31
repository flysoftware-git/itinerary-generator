# V2 Phase 4, 6, 7 Checklist

Purpose: provide an execution-ready checklist for the remaining v2 milestones after Phase 1 completion.

Current context:
- Baseline lock: `ff71a13`
- Phase 1 registry/reconciliation: completed
- Phase 4 foundation: completed (destination status, selective retry, retry cap, JSON/Markdown/CLI triage)

## Phase 4: Destination Quarantine And Selective Regeneration

Status: in progress

### Completed

- [x] Per-destination status synthesis from registry outcomes.
- [x] Selective retry on flagged destinations only.
- [x] Policy-driven retry triggers:
  - URL acceptance ratio threshold
  - section minimum accepted thresholds
- [x] Per-run retry cap per destination.
- [x] Terminal retry outcomes recorded for each destination.
- [x] Human triage outputs:
  - JSON status report
  - Markdown status summary
  - CLI unresolved destination summary

### Remaining

- [ ] Tune `destination_retry` thresholds from one controlled run.
- [x] Add test coverage for multi-destination mixed outcome scenarios.
- [x] Add test coverage for zero-cap mode (`max_retries_per_destination_per_run: 0`).

### Exit Criteria

- [ ] Retry policy thresholds are calibrated and documented.
- [x] Focused selective-retry tests cover mixed and zero-cap cases.
- [ ] Unresolved destinations are always explicit in status artifacts and CLI output.

## Phase 6: Validation And Observability

Status: not started

### Tasks

- [ ] Run focused non-regression suite mapped to v0.30 invariants.
- [ ] Verify destination status artifacts are stable and readable on real run output.
- [ ] Verify registry debug artifact remains verbose-only.
- [ ] Confirm failure isolation by destination/stage from produced artifacts.

### Exit Criteria

- [ ] Regressions are caught by focused suites before end-to-end run.
- [ ] Operator can identify failing destination(s) and trigger(s) in under 2 minutes from artifacts.

## Phase 7: Version 2 Cutover Gate

Status: not started

### Tasks

- [ ] Execute one controlled end-to-end validation pass on agreed manifest.
- [ ] Review generated itinerary + validation report + destination status artifacts.
- [ ] Confirm non-negotiable invariants remain intact.
- [ ] Prepare requirements/versioning update for v2 declaration.

### Exit Criteria

- [ ] Controlled end-to-end run passes with no blocker regressions.
- [ ] Invariant checklist has no unresolved violations.
- [ ] Team sign-off to promote architecture label to Version 2.

## Suggested Run Order

1. Complete remaining Phase 4 tests and threshold tuning.
2. Execute Phase 6 focused validation/observability checks.
3. Perform Phase 7 controlled end-to-end gate run.
4. If all criteria pass, update requirements/versioning to declare v2.
