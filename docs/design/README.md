# Design Notes

This directory captures behavior-oriented design notes for major pipeline components.

## Notes
- `building-attractions.md`: how attractions are generated, normalized, ordered, and rendered.
- `url-discovery-and-audit.md`: how URLs are discovered, scored, and filtered.
- `fallback-curation-contract.md`: ownership contract for fallback harvesting, qualification, curation, and render-time publication.
- `schedule-normalization.md`: how daily schedules are normalized and quality-guarded.
- `image-selection-and-filtering.md`: how destination imagery is discovered, ranked, and filtered.
- `html-assembly-pipeline.md`: how structured trip data is assembled into final HTML.
- `restaurant-discovery-ranking-linkage.md`: how restaurant URLs are discovered, ranked, and selected for final links.
- `url-quality-pr-backlog.md`: staged PR plan for URL-state semantics, fallback confidence, audit reason codes, and cost-quality reporting.
- `provenance-control-and-scheduling-rationalization.md`: post-triage architecture note defining provenance as the controlling publish mechanism and formalizing multi-day schedule rationalization.
- `instrumentation-curation-and-provenance.md`: measurable signals and reporting contract for transform reliability, curation decisions, cache-aware experimentation, and provenance auditability.
- `v2-issue-6-kickoff-checklist.md`: branch/setup checklist for starting Issue #6 on v2 while preserving v1.4 baseline behavior.
- `v2-issue-6-execution-plan.md`: phased local execution plan for Issue #6, anchored to baseline commit `ff71a13` and current v0.30 behavior.
- `v2-issue-6-invariants.md`: Phase 0 non-regression contract capturing the behaviors v2 must preserve.
- `v2-issue-6-registry-schema.md`: Phase 1 registry/reconciliation schema draft for v2 orchestration.
- `reservation-email-ingestion.md`: how forwarded confirmation emails become manifest data — matching, the three outcomes, mailbox handling, and the security posture.
- `multimodal-routing.md`: GH #2 design for transit-aware legs; phased AI-only then Google Directions, and why Phase 1 must not emit clock times.
- `per-day-item-caps.md`: how per-day item targets bound attraction, restaurant, en-route and scenic-drive counts.
- `cost-accounting-and-reduction.md`: how run spend is measured against the provider's own bill, what the 2026-08-21 reconciliation overturned, and a repeatable benchmarking procedure.
- `per-item-imagery.md`: per-item images from free sources (Wikimedia 89%, NPS 32%), why Google Places Photos cannot be used, and two defects the probe exposed.
- `destination-type-coverage.md`: quality thresholds calibrated on a single fixture; a thinly-indexed town loses 77% of its dining. Tests whether indexing density, not park status, is the real differentiator. Also records markdown emphasis leaking into published names.
- `european-content-sources.md`: why Rick Steves is the wrong source for the dining gap, and what Wikivoyage offers instead (25 named Brussels eateries with explicit budget tiers, CC BY-SA).
- `places-for-restaurants.md`: Places Text Search closes every restaurant defect at the source (20 candidates vs 1, authoritative prices, official sites); the obstacle is the caching terms, not cost.
- `live-fetch-and-execution-time-reduction.md`: risk-tiered assessment of reducing live HTTP fetching during URL discovery/audit, plus broader architecture levers (AI-generation concurrency, per-domain block-cooldown, retry gating) to cut manifest execution time.
- `banned-marketing-language-enforcement.md`: deterministic code-level enforcement of the system prompt's banned-cliché list, closing the gap where that instruction alone was routinely violated with zero downstream checking.
- `search-provider-capability-probe.md`: root cause and fix for Grok search never actually being invoked, the cross-provider (Grok/Claude/OpenAI/Gemini) citation-fidelity probe that followed, Claude's addition as a second working search/harvest provider, and the resulting Grok-batch/Claude-non-batch production split.
- `provider-model-matrix.md`: canonical provider × role (content-gen/batch-search/non-batch-search) matrix — current model ids, which roles each provider is actually approved for, and the evidence behind each assignment.
- `multi-site-destination-grouping.md`: spec for GH #68 (multi-site destinations, e.g. Moab as a base for Arches + Canyonlands) — manifest `group_with` field, lodging dedup, nav clustering, and route/distance handling for grouped day-trip hops.

## Conventions
- Focus on runtime behavior, not just intent.
- Document ordering, filtering, and fallback rules explicitly.
- Include key file locations for quick code navigation.
- Call out policy assumptions and known tradeoffs.
