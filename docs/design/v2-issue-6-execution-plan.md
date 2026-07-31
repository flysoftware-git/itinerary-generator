# V2 Execution Plan (Issue #6)

Purpose: convert Issue #6 from discussion into a repo-local implementation plan anchored to the stabilized baseline commit `ff71a13`.

Status:
- Active architecture track: Version 2, pending implementation and validation.
- Current behavioral contract: [docs/requirements.md](../requirements.md) version 0.30.
- Rollback-safe baseline: `ff71a13` (`Stabilize review fixes before v2 architecture`).

## Principles

- Batch expensive acquisition stages, not validation semantics.
- Preserve the existing destination-shaped rendering contract.
- Keep fail-closed URL behavior mandatory.
- Prefer destination quarantine and selective regeneration over full reruns.
- Do not designate the work as Version 2 in requirements/versioning until implementation validates against current behavior.

## Non-Negotiable Invariants

- URL policy enforcement remains mandatory.
- Domain denylist and AllTrails slug denylist remain mandatory.
- Redirect entity-match checks remain mandatory.
- Category-vs-entity handling remains mandatory.
- Final-leg route ownership and departure-leg placement remain mandatory.
- Template integrity checks remain mandatory.
- Per-destination minimums and quality thresholds remain mandatory.
- Named entities fail closed to empty URLs or plain text when verification fails.

## Phase 0: Baseline Lock

Goals:
- Freeze the current v0.30 behavior as the non-regression contract for v2.
- Capture the exact specialized rules that must survive the refactor.

Deliverables:
- Invariant checklist covering URL fail-closed policies, ownership rules, and rendering assumptions.
- Explicit non-regression checks mapped to existing focused tests.
- One reference baseline commit: `ff71a13`.

Exit criteria:
- All preserved behaviors are documented well enough to reject accidental simplification during refactor.

## Phase 1: Registry and Reconciliation Layer

Goals:
- Introduce a global entity registry without changing the assembler contract.

Target schema fields:
- `entity_id`
- `destination_id`
- `entity_class`
- `ownership_type`
- `source_stage`
- `confidence`
- `validation_status`
- `rendered_url`

Constraints:
- `HTMLAssembler` continues consuming destination-shaped trip data.
- Registry must reconcile back into the current destination structure before assembly.

Exit criteria:
- Registry/reconciliation layer exists with no behavioral change to output shape.

## Phase 2: Batch AI Generation Behind Existing Output Shape

Goals:
- Add a batched AI path keyed by destination id while preserving the normalized per-destination output contract.

Required validation:
- destination presence/completeness
- per-destination quotas
- duplicate-entity conflicts
- scenic-drive uniqueness or explicit ownership

Recovery strategy:
- Per-destination fallback regeneration when one destination fails schema validation.

Exit criteria:
- Batched AI content can flow through existing normalization and rendering without assembler changes.

## Phase 3: Batch URL Candidate Acquisition With Existing Validation Preserved

Goals:
- Batch semantic candidate acquisition for named entities.
- Preserve the current per-item validation and audit rules after acquisition.

Must preserve:
- redirect entity checks
- slug denylist
- domain denylist
- URL class blocklist
- category-vs-entity handling
- confidence gating
- fail-closed publish behavior

Rules:
- Store empty URLs when validation fails.
- Do not fabricate fallback links for named entities.
- Reconcile using destination-locality and ownership rules before writing URLs back.

Exit criteria:
- Candidate acquisition is batched, while output semantics match current strict URL behavior.

## Phase 4: Destination Quarantine and Selective Regeneration

Goals:
- Let one destination fail or retry without forcing a full rerun.

Retry triggers:
- schema failure
- quota failure
- URL collapse below acceptable thresholds
- image shortfall

Exit criteria:
- One destination can be regenerated independently while others remain stable.

## Phase 5: Image Pipeline Refactor

Goals:
- Move image query planning toward batched orchestration where providers support it.

Must preserve:
- per-destination image minimums
- current relevance filtering
- current gallery and hero rendering contract

Recovery strategy:
- destination-specific retry when image minimums are not met.

Exit criteria:
- Image acquisition cost decreases without weakening image quality guards.

## Phase 6: Validation and Observability

Goals:
- Make batch-stage status inspectable and debuggable without turning normal runs into artifact dumps.

Required outputs:
- per-destination status reporting per stage
- verbose-only registry/reconciliation debug artifacts
- validation coverage for ownership conflicts, transfer-leg placement, template integrity, minimums, and empty-URL fail-closed behavior

Exit criteria:
- Failures can be isolated to one destination or stage quickly.

## Phase 7: Version 2 Cutover

Goals:
- Promote the architecture to Version 2 only after behavior is validated.

Required validation:
- focused regression suites against v0.30 invariants
- one controlled end-to-end validation pass against the agreed manifest

Exit criteria:
- requirements/versioning updated only after v2 behavior is proven.

## Immediate Next Steps

1. Capture Phase 0 invariants in a dedicated non-regression document.
	Status: completed in [v2-issue-6-invariants.md](./v2-issue-6-invariants.md).
2. Draft the registry/reconciliation schema and ownership model before touching AI orchestration.
	Status: completed in [v2-issue-6-registry-schema.md](./v2-issue-6-registry-schema.md).
3. Keep the current branch history anchored to `ff71a13` for rollback and diff clarity.