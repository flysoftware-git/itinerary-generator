# URL Quality PR Backlog (v1.4 Planning)

This note tracks the next batch of URL quality work after v1.3 behavior hardening.
Scope is analysis/backlog only: no runtime behavior changes are applied by this note.

## Context
- Current pipeline can emit technically usable fallback links that are weak quality evidence.
- Logging sometimes conflates "resolved" with "fallback assigned" semantics.
- Full-run costs have increased enough that quality instrumentation must be low-overhead and actionable.

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
1. PR-1 (state model)
2. PR-3 (audit reason codes)
3. PR-2 (restaurant confidence tiers)
4. PR-4 (trace artifact)
5. PR-5 (cost-quality accounting)

## Non-Goals (This Cycle)
- No changes to destination content generation prompts.
- No changes to HTML visual design.
- No broad domain allow/deny-list expansion outside URL state instrumentation.