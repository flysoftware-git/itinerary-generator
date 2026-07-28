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

## Conventions
- Focus on runtime behavior, not just intent.
- Document ordering, filtering, and fallback rules explicitly.
- Include key file locations for quick code navigation.
- Call out policy assumptions and known tradeoffs.
