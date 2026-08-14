# Reopened Items Manual Validation Checklist

Purpose: provide a closure-ready checklist for all reopened v1.4 output-review items, split by implementation state.

## How To Use This Checklist

1. Validate all items in Section A using the current 1.4.4 behavior baseline.
2. Validate Section B only after regenerating output with post-reopen code changes included.
3. Keep final validation blocked until every item below is manually confirmed.

## Section A: Previously Incorporated Code + Tests

These were already implemented with automated coverage and are functionally in the 1.4.4 line. You can use the 1.4.4 build/output pass to confirm and potentially re-close.

- [Close] PR-003 / #13: Scenic-drive card teaser differs from popup full description. Issue: https://github.com/flysoftware-git/road-trip-generator/issues/13. Report: [PR-003-scenic-drive-card-duplicates-popup-description.md](PR-003-scenic-drive-card-duplicates-popup-description.md)
- [Close] PR-004 / #14: Scenic-drive links are route-specific; generic place pages rejected. Issue: https://github.com/flysoftware-git/road-trip-generator/issues/14. Report: [PR-004-scenic-drive-link-resolves-to-place-not-drive-info.md](PR-004-scenic-drive-link-resolves-to-place-not-drive-info.md)
- [Close] PR-005 / #16: Daily schedule appears route-aware/time-budgeted in rendered itinerary. Issue: https://github.com/flysoftware-git/road-trip-generator/issues/16. Report: [PR-005-possible-daily-schedule-not-realistic-route-aware-or-time-budgeted.md](PR-005-possible-daily-schedule-not-realistic-route-aware-or-time-budgeted.md). Cross-link: overlaps with PR-029 transfer-leg ownership/placement and reopened multi-day duplication findings.
- [Close] PR-008 / #18: Cross-destination duplicate concepts are not repeated with conflicting targets. Issue: https://github.com/flysoftware-git/road-trip-generator/issues/18. Report: [PR-008-cross-destination-duplicate-concepts-and-mismatched-links.md](PR-008-cross-destination-duplicate-concepts-and-mismatched-links.md)
- [Close] PR-011 / #19: Named restaurant does not link to area-reference maps search query. Issue: https://github.com/flysoftware-git/road-trip-generator/issues/19. Report: [PR-011-capitol-reef-cafe-area-reference-link.md](PR-011-capitol-reef-cafe-area-reference-link.md)
- [Close] PR-017 / #21: Ajax Peak invalid AllTrails slug is not published as a link. Issue: https://github.com/flysoftware-git/road-trip-generator/issues/21. Report: [PR-017-ajax-peak-unavailable-link-should-be-discarded.md](PR-017-ajax-peak-unavailable-link-should-be-discarded.md)
- [Close] PR-018 / #22: Bear Creek Trail does not retain redirect-mismatched entity link. Issue: https://github.com/flysoftware-git/road-trip-generator/issues/22. Report: [PR-018-bear-creek-trail-redirect-entity-mismatch.md](PR-018-bear-creek-trail-redirect-entity-mismatch.md)
- [Close] PR-019 / #23: Jud Wiebe invalid AllTrails slug is not published as a link. Issue: https://github.com/flysoftware-git/road-trip-generator/issues/23. Report: [PR-019-jud-wiebe-trail-unavailable-link-should-be-discarded.md](PR-019-jud-wiebe-trail-unavailable-link-should-be-discarded.md). Cross-link: overlaps with PR-017 invalid-trail suppression class and fail-closed rendering policy.
- [Close] PR-020 / #24: Untrusted Lizard Head Pass domain link is rejected (fail-closed). Issue: https://github.com/flysoftware-git/road-trip-generator/issues/24. Report: [PR-020-lizard-head-pass-hallucinated-link.md](PR-020-lizard-head-pass-hallucinated-link.md)
- [Close] PR-021 / #25: Untrusted listing domain for Center for the Arts is rejected. Issue: https://github.com/flysoftware-git/road-trip-generator/issues/25. Report: [PR-021-pagosa-springs-center-for-the-arts-hallucinated-link.md](PR-021-pagosa-springs-center-for-the-arts-hallucinated-link.md)
- [Close] PR-022 / #26: Category-style activity does not resolve to ambiguous maps search link. Issue: https://github.com/flysoftware-git/road-trip-generator/issues/26. Report: [PR-022-category-vs-location-san-juan-river-fly-fishing.md](PR-022-category-vs-location-san-juan-river-fly-fishing.md). Cross-link: overlaps with PR-007/PR-010 ambiguous or area-reference query-link classes.
- [Close] PR-024 / #27: Oversized San Juan Skyway day trip is filtered out unless feasible. Issue: https://github.com/flysoftware-git/road-trip-generator/issues/27. Report: [PR-024-san-juan-skyway-time-budget-route-fit.md](PR-024-san-juan-skyway-time-budget-route-fit.md)
- [Close] PR-025 / #28: Pagosa Brewing link/entry is removed when ineligible/untrusted. Issue: https://github.com/flysoftware-git/road-trip-generator/issues/28. Report: [PR-025-pagosa-brewing-and-grill-hallucinated-link.md](PR-025-pagosa-brewing-and-grill-hallucinated-link.md)
- [Close] PR-027 / #29: Compound attraction name with '&' is not published as a linked single entity. Issue: https://github.com/flysoftware-git/road-trip-generator/issues/29. Report: [PR-027-compound-destination-entity-should-be-split.md](PR-027-compound-destination-entity-should-be-split.md)
- [Close] PR-028 / #30: Out-of-threshold trail candidates are rejected by filtering policy. Issue: https://github.com/flysoftware-git/road-trip-generator/issues/30. Report: [PR-028-trail-threshold-enforcement-atalaya-mountain-trail.md](PR-028-trail-threshold-enforcement-atalaya-mountain-trail.md)
- [Close] PR-029 / #31: Departure-leg route options are rendered in final-leg Getting There behavior. Issue: https://github.com/flysoftware-git/road-trip-generator/issues/31. Report: [PR-029-missing-departure-getting-here-turquoise-trail-placement.md](PR-029-missing-departure-getting-here-turquoise-trail-placement.md)

## Section B: Requires Post-Reopen Code Revalidation

These changed after reopen and should wait for full manual revalidation on the updated build before any re-close action.

- [Close] PR-001 / #12: What to Know duplicate prose fix updated post-reopen to strip Cultural Events echo from What to Know while preserving Cultural Events content. Issue: https://github.com/flysoftware-git/road-trip-generator/issues/12. Report: [PR-001-what-to-know-duplicates-generic-guidance.md](PR-001-what-to-know-duplicates-generic-guidance.md)
- [Close] PR-006 / #17: Route overview marker UX update (compact marker size + secondary date under location label + stop-index readability). Issue: https://github.com/flysoftware-git/road-trip-generator/issues/17. Report: [PR-006-route-overview-map-markers-missing-stop-number-tags.md](PR-006-route-overview-map-markers-missing-stop-number-tags.md). Cross-link: currently treated as standalone visual/readability workstream.

## Completion Gate

- [ ] All Section A rows manually validated against 1.4.4 behavior and marked ready to re-close.
- [x] Section B validated on the updated post-reopen code build.
- [ ] Only then run the final validation pass and execute closure actions.

## Recommended Open-Item Fix Sequence

Use this order when implementing remaining open/reopened work to reduce duplicate effort:

1. PR-022 (`#26`) category-vs-entity suppression and stoplist hardening.
2. PR-019 (`#23`) invalid-trail rejection with strict fail-closed publication.
3. PR-005 (`#16`) schedule ownership/time-budget rationalization (including transfer-leg interaction).
4. PR-006 (`#17`) route-marker UI readability and stop-number presentation.

Expected outcome: steps 1-3 should collapse multiple reopened duplicate classes before the final UI-only PR-006 pass.
