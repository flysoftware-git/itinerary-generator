# URL Quality PR Backlog (v1.4 Planning)

This note tracks URL quality hardening after repeated reopened regressions.
Scope is planning and sequencing for quality-first implementation.

## Context
- Reopened defects show repeated publication of ambiguous/fallback links for named entities.
- Logging and artifacts do not yet make provenance/decision state the controlling mechanism.
- Long end-to-end reruns are expensive and weak at isolating root causes; short-gate validation is required.

## PR-0: Provenance-Controlled Publication Contract
- Goal: make provenance and decision state the only authority for final link publication.
- Required behavior:
  - renderers consume decisioned URL state only
  - renderers do not synthesize named-entity links from names
  - fallback/query URLs remain diagnostics metadata unless explicitly category-contextual
- Primary files:
  - `generator/url_discovery.py`
  - `generator/entity_registry.py`
  - `generator/html_assembler.py`
- Acceptance criteria:
  - Named entities with unresolved/ambiguous state render as plain text.
  - Published links are explainable via provenance state + reason code.

## PR-1: Explicit URL State Model
- Goal: separate semantic states from transport success.
- States:
  - `resolved_exact`
  - `resolved_fallback_query`
  - `unresolved`
  - `rejected`
- Primary files:
  - `generator/url_discovery.py`
  - `generator/html_assembler.py`
- Acceptance criteria:
  - Logs never label fallback query URLs as exact resolutions.
  - Final structured data carries `url_state` and `url_reason` fields for attractions/restaurants/stops.

## PR-2: Restaurant Fallback Confidence Tiering
- Goal: keep Google Maps fallback for UX while preventing it from being counted as equivalent to a verified venue URL.
- Confidence tiers:
  - `high`: official site or high-signal canonical profile
  - `medium`: stable map identity page
  - `low`: free-form maps/search query fallback
- Primary files:
  - `generator/url_discovery.py`
  - `generator/report_writer.py`
- Acceptance criteria:
  - Cost/quality reports distinguish low-confidence fallback URLs from high-confidence resolved links.

## PR-3: Audit Pass Reason Codes
- Goal: make `audit_discovered_urls` decisions machine-auditable.
- Behavior:
  - return decision tuples or objects: keep/drop + reason code
  - persist reason code in run artifacts
- Primary files:
  - `generator/url_discovery.py`
  - `generator/report_writer.py`
- Acceptance criteria:
  - Every dropped URL has a reason code.
  - Every retained fallback URL has an explicit policy reason code.

## PR-4: Per-Item URL Trace Artifact
- Goal: make debugging deterministic without reading large console logs.
- Output:
  - one JSON artifact per run with item-level candidate history
  - includes candidate URL, score, checks passed/failed, final state
- Primary files:
  - `generator/url_discovery.py`
  - `generator/main.py`
- Acceptance criteria:
  - Trace file is generated for full runs.
  - Trace file can explain any final URL in `output/index.html`.

## PR-5: Quality Accounting for Cost Optimization
- Goal: make cost work measurable without regressing quality.
- Metrics:
  - `% resolved_exact`
  - `% resolved_fallback_query`
  - `% unresolved`
  - `% rejected`
  - category-level breakdowns (attractions/restaurants/stops)
- Primary files:
  - `generator/costs.py`
  - `generator/report_writer.py`
  - `docs/requirements.md`
- Acceptance criteria:
  - Run summary includes quality-state distribution next to model costs.
  - Regressions trigger visible warnings in output report.

## Rollout Order
1. PR-0 (provenance-controlled publication)
2. PR-1 (state model)
3. PR-3 (audit reason codes)
4. PR-4 (trace artifact)
5. PR-2 (restaurant confidence tiers)
6. PR-5 (cost-quality accounting)

## Validation Strategy

Use short, credibility-first gates for each PR:
1. targeted unit/contract tests for changed logic
2. compact golden-manifest assertions for reopened defect classes
3. one end-to-end smoke run only after gates 1 and 2 pass

## Non-Goals (This Cycle)
- No changes to destination content generation prompts.
- No changes to HTML visual design.
- No broad domain allow/deny-list expansion outside URL state instrumentation.