# V2 Registry And Reconciliation Schema (Issue #6)

Purpose: define the Phase 1 intermediate data model for Version 2 without changing the existing assembler contract.

Baseline assumptions:
- Current behavioral contract remains [../requirements.md](../requirements.md) version 0.30.
- Current rollback-safe baseline is `ff71a13`.
- [../../generator/html_assembler.py](../../generator/html_assembler.py) remains a consumer of destination-shaped trip data.

## Design Constraint

The registry is an internal orchestration structure.
It is not the public rendering contract and should not be fed directly into the HTML assembler.

Pipeline intent:

1. Build global registry entries during batch acquisition.
2. Validate and reconcile entries using ownership and policy rules.
3. Materialize the existing destination-shaped trip structure.
4. Render with the existing assembler contract.

## Proposed Entity Schema

Each registry record should represent one candidate renderable concept.

```json
{
  "entity_id": "string",
  "destination_id": "string",
  "entity_class": "attraction|trail|scenic_drive|restaurant|event|en_route_stop|route_option|what_to_know_fact",
  "ownership_type": "destination|transfer_leg|shared_allowed",
  "source_stage": "manifest|ai_batch|url_batch|image_batch|normalization|reconciliation",
  "display_name": "string",
  "normalized_name": "string",
  "description": "string",
  "raw_payload": {},
  "confidence": "low|medium|high",
  "validation_status": "pending|accepted|rejected|needs_retry|quarantined",
  "rejection_reasons": ["string"],
  "rendered_url": "string",
  "candidate_urls": ["string"],
  "section_target": "top_attractions|scenic_drives|getting_here.en_route_stops|getting_there.route_options|dinner_recommendations|cultural_events",
  "ordering_hint": 0,
  "shared_group_id": "string|null",
  "metadata": {}
}
```

## Required Fields

### `entity_id`
- Stable per-run identifier.
- Should not depend on rendered URL.
- Recommended composition:
  - `destination_id`
  - `entity_class`
  - canonicalized `normalized_name`

### `destination_id`
- Required for every entry, even when ownership is `transfer_leg`.
- For transfer-leg items, this is the owning rendered destination section.

### `entity_class`
- Drives validation and reconciliation policy.
- Must distinguish `trail` from general `attraction` and `route_option` from generic `scenic_drive`.

### `ownership_type`
- `destination`: owned by one destination’s ordinary section content.
- `transfer_leg`: owned by a route leg and rendered in transfer/departure context.
- `shared_allowed`: cross-destination duplication is intentionally permitted.

### `source_stage`
- Tracks where the current entity record was created or last materially changed.
- Important for quarantine, retry, and observability.

### `validation_status`
- `pending`: collected but not yet validated.
- `accepted`: eligible for reconciliation into rendered output.
- `rejected`: must not render.
- `needs_retry`: failed but suitable for selective regeneration.
- `quarantined`: isolated pending destination-level recovery.

### `rendered_url`
- Final post-policy URL only.
- Empty string is valid and expected for fail-closed items.

### `section_target`
- Explicitly identifies the destination-shaped output slot.
- Prevents ambiguous placement during reconciliation.

## Supporting Structures

### Destination Registry View

Derived index for fast reconciliation:

```json
{
  "destination_id": {
    "top_attractions": ["entity_id"],
    "scenic_drives": ["entity_id"],
    "getting_here.en_route_stops": ["entity_id"],
    "getting_there.route_options": ["entity_id"],
    "dinner_recommendations": ["entity_id"],
    "cultural_events": ["entity_id"]
  }
}
```

### Reconciliation Report

Recommended side structure for verbose/debug mode:

```json
{
  "destination_id": "string",
  "accepted": ["entity_id"],
  "rejected": [{"entity_id": "string", "reasons": ["string"]}],
  "reassigned": [{"entity_id": "string", "from": "string", "to": "string"}],
  "quarantined": ["entity_id"]
}
```

## Reconciliation Rules

### Ownership Before Deduplication
- Resolve `ownership_type` before cross-destination deduplication.
- Example: a one-way departure scenic drive aligned with the return route should be reclassified into `getting_there.route_options` rather than treated as conflicting destination activity.

### Entity-Class Policy Before URL Assignment
- Determine whether an item is a named entity, category activity, trail, or route option before deciding whether an empty URL is required.
- This preserves current fail-closed semantics for named entities.

### Rejected Entries May Still Render As Plain Text
- Rejection of `rendered_url` does not imply removal of the content entity itself.
- Reconciliation must distinguish:
  - reject URL only
  - reject render placement
  - reject entity entirely

### Destination Shape Is The Output Contract
- After reconciliation, destination data should still look like the current trip structure used by the renderer and validator.
- This reduces cutover risk and keeps testing localized.

## Phase 1 Exit Criteria

- Registry schema is documented and implemented behind feature-gated or isolated code paths.
- Reconciliation can materialize the current destination-shaped structure without renderer changes.
- Focused non-regression tests continue to pass.

## Open Questions

- Whether `what_to_know` facts should live in the registry or remain destination-local until later phases.
- Whether scenic-drive uniqueness should be enforced by `entity_id`, normalized title, or ownership group.
- Whether restaurant quota retry should operate on entities only or regenerate the full destination restaurant slice.