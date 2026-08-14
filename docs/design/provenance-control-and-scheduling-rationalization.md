# Provenance Control and Schedule Rationalization

Purpose: capture lessons from reopened quality regressions and define the controlling architecture constraints that must hold before additional optimization work.

## Why This Note Exists

Recent triage showed repeated rediscovery of previously fixed link-quality and schedule-quality defects. The recurring pattern was not one missing rule, but weak enforcement of source provenance and quality state across stages.

## Primary Learning

A link must not be treated as publishable simply because a later stage can construct a plausible query from a name. The controlling mechanism for publication must be provenance + validation status, not name recoverability.

## Provenance as the Controlling Mechanism

### 1. Provenance Envelope Is Mandatory

Every named entity candidate must carry a provenance envelope:
- source_stage (ai_candidate, search_candidate, manual_allowlist, etc.)
- source_domain
- source_query_variant
- candidate_rank and score
- validation checks run
- decision state and reason code

### 2. URL Publish States

Use explicit states and transitions:
- unresolved
- candidate_observed
- verified_canonical
- verified_contextual
- rejected_ambiguous
- rejected_invalid
- rejected_policy

Allowed publish state for named entities:
- verified_canonical

Optional publish state for category-level context cards only:
- verified_contextual

v2.1 renderer fallback mode:
- When canonical state is unresolved/rejected, renderer may use explicit
	`maps_url` fallback metadata as secondary navigation context.
- This fallback path does not upgrade canonical provenance state.

Never publish named-entity links from:
- unresolved
- candidate_observed
- rejected_* states

Clarification:
- Canonical named-entity publication is still restricted to `verified_canonical`.
- Fallback rendering is an explicit secondary UX path, not canonical acceptance.

### 3. Renderer Contract

Renderers must consume decisioned URL state from registry/reconciled output only.
Renderers must not synthesize new named-entity links from names when URL is empty.

### 4. Fallback Semantics

Search/query fallbacks are not equivalent to entity-specific targets and must not
be treated as canonical evidence.

In v2.1 they may be published as explicit secondary fallback links when no
canonical URL is available and the card/section fallback policy allows it.

## Multi-Day Schedule Rationalization

### 0. Reconciliation Timing (Implemented)

Schedule text must be reconciled against the *final* entity registry state,
not an earlier, partial snapshot. Previously,
`URLDiscoverer._reconcile_schedule_after_entity_filter` ran during the URL
audit pass -- before the main registry reconciliation
(`_reconcile_trip_via_registry`) -- and only ever scrubbed `top_attractions`
mentions. Two consequences:
- Rejections from `dinner_recommendations`, `scenic_drives`,
  `getting_here.en_route_stops`, `getting_there.route_options`, and
  `cultural_events` were never reflected in schedule text at all.
- `_deduplicate_within_destination` (which runs *after* the old
  reconciliation pass) could silently remove an attraction or scenic drive
  with no registry trace, leaving the schedule referencing something that
  had vanished with no mechanism to ever catch it.

Fixed: `generator/entity_registry.py:reconcile_schedule_from_registry` now
runs inside `_reconcile_trip_via_registry` (`generator/main.py`), after
`reconcile_trip_from_registry` has produced the final entity state, and
spans every section via `registry["entities"]` rather than a
narrower top_attractions-only decision list. `_deduplicate_within_destination`
now calls `_record_registry_entity_removal` for both removal paths (scenic
drive duplicating an attraction; attraction duplicating an en-route stop) so
those removals are visible to the registry.

This is deterministic reconciliation (text substitution against final
entity state), not LLM regeneration -- consistent with the rest of the
pipeline's cost profile. An entity that's still technically "accepted" but
carries a soft-demotion reason code (currently
`threshold_demoted_to_attraction`) is also scrubbed from schedule mentions,
since the entity survives in a repurposed form but the original claim the
mention made (e.g. "go hike this trail") is no longer accurate.

### 1. Source-of-Truth Hierarchy

Order of authority for schedule content:
1. normalized structured schedule payload
2. deterministic route-boundary adjustments
3. bounded filler text only when required for missing periods

Renderer must preserve normalized schedule content and avoid re-synthesizing complete day plans when structured content exists.

### 2. Day Differentiation Contract

For multi-day destinations, each day must have at least one substantive differentiator from prior days:
- distinct attraction/area focus, or
- distinct route/transfer duty, or
- distinct operational window constraint

Minor wording changes alone do not satisfy differentiation.

### 3. Boundary Window Ownership

Route-boundary windows are owned by transfer logic:
- first destination arrival window
- final destination departure window

Transfer-owned content must not be rendered as ordinary in-stay activity content.

### 4. Rationalization Checks

Normalization should enforce:
- period coverage (Morning/Afternoon/Evening)
- duplication detection across days
- route realism at trip boundaries
- dinner anchoring only to surviving, eligible recommendations

## Validation Strategy (Credibility-First)

Do not rely on lengthy full runs for each fix. Use short gates:
1. targeted unit/contract tests for changed behavior
2. small golden-manifest assertions for reopened classes
3. one end-to-end smoke run only after 1 and 2 are clean

## Mapping to Reopened Defect Classes

This model directly addresses recurring classes:
- ambiguous maps/search publication for named entities
- category-vs-entity mismatches (for example guide lists)
- closed/hallucinated restaurant carry-through
- route-transfer content displacement
- multi-day schedule duplication with weak variation

## Implementation Priority

1. Enforce publish-state consumption in renderer
2. Enforce provenance/decision state in discovery + audit
3. Add compact regression matrix for reopened classes
4. Continue performance work only behind these quality gates
