# Design Notes

This directory captures behavior-oriented design notes for major pipeline components.

## Notes
- `building-attractions.md`: how attractions are generated, normalized, ordered, and rendered.
- `url-discovery-and-audit.md`: how URLs are discovered, scored, and filtered.
- `schedule-normalization.md`: how daily schedules are normalized and quality-guarded.
- `image-selection-and-filtering.md`: how destination imagery is discovered, ranked, and filtered.
- `html-assembly-pipeline.md`: how structured trip data is assembled into final HTML.
- `restaurant-discovery-ranking-linkage.md`: how restaurant URLs are discovered, ranked, and selected for final links.
- `url-quality-pr-backlog.md`: staged PR plan for URL-state semantics, fallback confidence, audit reason codes, and cost-quality reporting.
- `v2-issue-6-kickoff-checklist.md`: branch/setup checklist for starting Issue #6 on v2 while preserving v1.4 baseline behavior.
- `v2-issue-6-execution-plan.md`: phased local execution plan for Issue #6, anchored to baseline commit `ff71a13` and current v0.30 behavior.
- `v2-issue-6-invariants.md`: Phase 0 non-regression contract capturing the behaviors v2 must preserve.
- `v2-issue-6-registry-schema.md`: Phase 1 registry/reconciliation schema draft for v2 orchestration.

## Conventions
- Focus on runtime behavior, not just intent.
- Document ordering, filtering, and fallback rules explicitly.
- Include key file locations for quick code navigation.
- Call out policy assumptions and known tradeoffs.
