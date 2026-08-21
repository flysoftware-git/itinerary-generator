# V1.4 Output Review Index

## Canonical Tracking

Use the table below as the single source of truth for disposition and notes.

### Disposition Key
- **Accepted** — PR fully resolves the issue and matches manual 1.4 output review.
- **Needs Work** — PR does not fully resolve the issue; additional changes required.
- **Deferred** — Intentionally postponed.

> Tracker note: every row should map to an issue in the external issue tracker. If a matching issue does not yet exist, use `TBD` in the Issue column and create the tracker entry before closing the PR.

| ID | Title | Labels | Status | Issue | Notes |
| --- | --- | --- | --- | --- | --- |
| PR-001 | What to Know duplicates generic guidance across destinations | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:medium`, `area:html-output`, `area:content-linking` | Needs Work | [#12](https://github.com/flysoftware-git/itinerary-generator/issues/12) | Manually revalidated as fixed on latest rerun; What to Know no longer echoes Cultural Events prose. |
| PR-002 | Facebook destination links are allowed in final output | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-discovery`, `area:html-output`, `area:content-linking` | Accepted | [#32](https://github.com/flysoftware-git/itinerary-generator/issues/32) | Fixed (v1.4.1) |
| PR-003 | Scenic drive card text duplicates popup description | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:medium`, `area:html-output`, `area:content-linking` | Accepted | [#13](https://github.com/flysoftware-git/itinerary-generator/issues/13) | Manually validated under 1.4.4 checklist pass; issue closed. |
| PR-004 | Scenic drive more-info links can resolve to place pages instead of route-specific drive info | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:medium`, `area:url-discovery`, `area:content-linking`, `area:html-output` | Accepted | [#14](https://github.com/flysoftware-git/itinerary-generator/issues/14) | Manually validated under 1.4.4 checklist pass; issue closed. |
| PR-005 | Possible Daily Schedule is not realistic, route-aware, or time-budgeted | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:ai-content`, `area:scheduling`, `area:html-output`, `area:manifest-config` | Needs Work | [#16](https://github.com/flysoftware-git/itinerary-generator/issues/16) | Fixed in renderer ownership path: schedule card no longer synthesizes fallback itinerary content when upstream normalized schedule is absent, preventing route-awareness drift. |
| PR-006 | Route overview map markers do not show stop numbers matching destination menu | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:medium`, `area:html-output`, `area:map-ui`, `area:usability` | Needs Work | [#17](https://github.com/flysoftware-git/itinerary-generator/issues/17) | Fixed with compact marker UX: centered stop-index pin, readable secondary date block, wrapped nameplate, and adjusted icon geometry. Verified in generated output and marker tests. |
| PR-007 | Google Maps search-result links that do not resolve to a single location are allowed | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-discovery`, `area:restaurant-linking`, `area:google-maps`, `area:html-output` | Accepted | [#33](https://github.com/flysoftware-git/itinerary-generator/issues/33) | Removed; fixed (v1.4.1) |
| PR-008 | Same destination concept appears under multiple stops with conflicting context/link targets | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:medium`, `area:ai-content`, `area:url-discovery`, `area:content-linking`, `area:html-output` | Needs Work | [#18](https://github.com/flysoftware-git/itinerary-generator/issues/18) | Manually validated under 1.4.4 checklist pass; issue closed. |
| PR-009 | Mammoth Cave links to generic Bryce Canyon page instead of entity-specific information | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-discovery`, `area:content-linking`, `area:html-output` | Accepted | [#34](https://github.com/flysoftware-git/itinerary-generator/issues/34) | Removed; fixed (v1.4.2) |
| PR-010 | Subject links can resolve to area-reference queries instead of entity-specific targets | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-discovery`, `area:restaurant-linking`, `area:content-linking`, `area:html-output` | Accepted | [#35](https://github.com/flysoftware-git/itinerary-generator/issues/35) | Removed; fixed (v1.4.1) |
| PR-011 | Capitol Reef Cafe links to area-reference search instead of subject-specific destination | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-discovery`, `area:restaurant-linking`, `area:content-linking`, `area:html-output` | Needs Work | [#19](https://github.com/flysoftware-git/itinerary-generator/issues/19) | Manually validated under 1.4.4 checklist pass; issue closed. |
| PR-012 | Fruita Campground waypoint publishes non-validated Google Maps directions link instead of dropping waypoint | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-discovery`, `area:route-waypoints`, `area:google-maps`, `area:html-output` | Accepted | [#36](https://github.com/flysoftware-git/itinerary-generator/issues/36) | Fixed (v1.4.1) |
| PR-013 | Moab contains duplicate Dead Horse Point State Park callouts, including placeholder-link viewpoint card | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:medium`, `area:ai-content`, `area:html-output`, `area:content-linking`, `area:deduplication` | Accepted | [#37](https://github.com/flysoftware-git/itinerary-generator/issues/37) | Fixed (v1.4.3) |
| PR-014 | Red Cliffs Lodge Restaurant uses ambiguous Google Maps search link that may not resolve to the intended entity | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-discovery`, `area:restaurant-linking`, `area:google-maps`, `area:content-linking`, `area:html-output` | Accepted | [#38](https://github.com/flysoftware-git/itinerary-generator/issues/38) | Fixed (v1.4.1) |
| PR-015 | Fallback Cultural Events copy repeats in What to Know and Cultural Events across multiple destinations | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:medium`, `area:ai-content`, `area:content-linking`, `area:html-output`, `area:deduplication` | Needs Work | [#20](https://github.com/flysoftware-git/itinerary-generator/issues/20) | Fixed in renderer: fallback `honest_assessment` and `local_tip` no longer echo inside the What to Know card before the dedicated Cultural Events section renders. |
| PR-016 | More info link promises specific live-event verification but resolves to unvalidated claim-text search query | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:ai-content`, `area:content-linking`, `area:url-validation`, `area:hallucination-risk`, `area:html-output` | Accepted | [#39](https://github.com/flysoftware-git/itinerary-generator/issues/39) | Fixed (v1.4.1) |
| PR-017 | Ajax Peak points to unavailable AllTrails URL and should be discarded when target is invalid | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-validation`, `area:url-discovery`, `area:content-linking`, `area:hiking`, `area:html-output` | Needs Work | [#21](https://github.com/flysoftware-git/itinerary-generator/issues/21) | Manually validated under 1.4.4 checklist pass; issue closed. |
| PR-018 | Bear Creek Trail link resolves to different entity (Penrose Trail), violating promise-to-target match | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-validation`, `area:url-discovery`, `area:content-linking`, `area:hiking`, `area:redirect-validation`, `area:html-output` | Needs Work | [#22](https://github.com/flysoftware-git/itinerary-generator/issues/22) | Manually revalidated as fixed on latest rerun; redirect-mismatched Bear Creek target is no longer published. |
| PR-019 | Jud Wiebe Trail points to unavailable AllTrails URL and should be discarded when target is invalid | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-validation`, `area:url-discovery`, `area:content-linking`, `area:hiking`, `area:html-output` | Needs Work | [#23](https://github.com/flysoftware-git/itinerary-generator/issues/23) | Fixed via strict fail-closed trail publication path; invalid AllTrails candidates are no longer replaced by fallback links. Regression coverage added. |
| PR-020 | Lizard Head Pass uses untrusted/hallucinated target link and should be rejected if unverified | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-validation`, `area:url-discovery`, `area:hallucination-risk`, `area:content-linking`, `area:html-output` | Accepted | [#24](https://github.com/flysoftware-git/itinerary-generator/issues/24) | Manually revalidated as fixed on latest rerun; untrusted Lizard Head link is no longer published. |
| PR-021 | Pagosa Springs Center for the Arts uses hallucinated/untrusted listing link and should be rejected | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-validation`, `area:url-discovery`, `area:hallucination-risk`, `area:content-linking`, `area:html-output` | Accepted | [#25](https://github.com/flysoftware-git/itinerary-generator/issues/25) | Manually validated under 1.4.4 checklist pass; issue closed. |
| PR-022 | Discovery treats category activity as place entity for San Juan River Fly Fishing, yielding ambiguous map-search link | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-discovery`, `area:entity-classification`, `area:content-linking`, `area:google-maps`, `area:html-output` | Needs Work | [#26](https://github.com/flysoftware-git/itinerary-generator/issues/26) | Fixed with category-activity fail-closed handling and offer/listing suppression to prevent ambiguous maps-link publication for non-entity activities. |
| PR-023 | Redundant viewpoint activity callouts: Wolf Creek Pass and Lookout Mountain Viewpoint | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:medium`, `area:ai-content`, `area:content-linking`, `area:deduplication`, `area:html-output` | Accepted | [#40](https://github.com/flysoftware-git/itinerary-generator/issues/40) | Fixed (v1.4.3) |
| PR-024 | San Juan Skyway Day Trip likely exceeds day-level time budget unless aligned with inter-destination transfer | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:ai-content`, `area:scheduling`, `area:route-planning`, `area:html-output` | Accepted | [#27](https://github.com/flysoftware-git/itinerary-generator/issues/27) | Manually validated under 1.4.4 checklist pass; issue closed. |
| PR-025 | Pagosa Brewing & Grill uses hallucinated/untrusted restaurant link and should be rejected | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-validation`, `area:url-discovery`, `area:hallucination-risk`, `area:restaurant-linking`, `area:html-output` | Needs Work | [#28](https://github.com/flysoftware-git/itinerary-generator/issues/28) | Manually validated under 1.4.4 checklist pass; issue closed. |
| PR-026 | Permanently closed restaurant surfaced as recommendation (Nello's Bistro) and should be rejected | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-validation`, `area:restaurant-linking`, `area:content-freshness`, `area:google-maps`, `area:html-output` | Accepted | [#41](https://github.com/flysoftware-git/itinerary-generator/issues/41) | Fixed (v1.4.4) |
| PR-027 | Compound destination label combines multiple entities into one link target (Santa Fe Plaza & Palace of the Governors) | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:entity-classification`, `area:url-discovery`, `area:content-linking`, `area:html-output` | Needs Work | [#29](https://github.com/flysoftware-git/itinerary-generator/issues/29) | Manually validated under 1.4.4 checklist pass; issue closed. |
| PR-028 | Trail candidates exceeding configured distance/elevation/intensity thresholds should be rejected (Atalaya Mountain Trail) | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:url-discovery`, `area:trail-filtering`, `area:policy-enforcement`, `area:hiking`, `area:html-output` | Needs Work | [#30](https://github.com/flysoftware-git/itinerary-generator/issues/30) | Manually validated under 1.4.4 checklist pass; issue closed. |
| PR-029 | Departure leg lacks dedicated Getting Here section, causing Turquoise Trail to be misplaced as in-stay activity | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:html-output`, `area:route-planning`, `area:scheduling`, `area:scenic-drives` | Needs Work | [#31](https://github.com/flysoftware-git/itinerary-generator/issues/31) | Manually revalidated as fixed on latest rerun; final-leg Getting There route options now render correctly. |
| PR-030 | Not-yet-open restaurants should be excluded from highly-rated recommendations (La Casa Sena) | `review:v1.4-output`, `type:bug`, `status:fixed`, `severity:high`, `area:restaurant-linking`, `area:content-freshness`, `area:rating-policy`, `area:url-validation`, `area:html-output` | Accepted | [#42](https://github.com/flysoftware-git/itinerary-generator/issues/42) | Fixed (v1.4.4) |

## Open-PR Overlap Map

This map exists to reduce duplicate implementation effort across open and reopened items.

- PR-005 (schedule realism): overlaps with transfer-leg ownership/placement semantics in PR-029 and with reopened multi-day duplication findings (Bryce/Capitol Reef).
- PR-019 (invalid trail handling): overlaps with PR-017 invalid-trail suppression and global fail-closed named-entity policy work.
- PR-022 (category vs entity): overlaps with PR-007/PR-010 ambiguous or area-reference query-link classes and reopened fishing-guide/listing style defects.
- PR-006 (map marker UX): currently treated as largely standalone visual/readability workstream.

## Overlap-Driven Execution Order

Run remediation in this order to maximize closure impact per code change:

1. PR-022 first (category stoplist/entity-classification hardening)
	- Highest reuse against reopened ambiguous/offer/listing-style link defects.
	- Closure leverage: PR-022 directly, plus substantial reopened-link class reduction.
2. PR-019 second (invalid trail suppression + strict fail-closed publication)
	- Consolidates invalid-slug and fallback-relink behavior into one enforcement path.
	- Closure leverage: PR-019 directly and stability reinforcement for PR-017/PR-026 classes.
3. PR-005 third (schedule ownership and transfer-leg rationalization)
	- Applies provenance-first schedule ownership to reduce multi-day duplication and drift.
	- Closure leverage: PR-005 directly and reopened Bryce/Capitol Reef schedule-duplication reports.
4. PR-006 last (marker visual/readability tuning)
	- Isolated UI polish with minimal overlap; run after functional correctness items.

Gate per step:

- After each step: run focused tests for that defect class.
- After steps 1-3: run one targeted manual output spot-check.
- After all four: run one final smoke execution for closure confirmation.

## Corrected Link Registry

Broken or misleading links verified as corrected in accepted PR rows.

| PR | Subject | Broken or Misleading Link | Correction Outcome | Issue |
| --- | --- | --- | --- | --- |
| PR-007 | Hawaiian Barbecue (example pattern) | https://www.google.com/maps/search/Hawaiian+Barbecue+restaurant+St.+George,+Utah | Ambiguous Google Maps search-result links are blocked and dropped from final publish output. | [#33](https://github.com/flysoftware-git/itinerary-generator/issues/33) |
| PR-009 | Mammoth Cave | https://en.wikipedia.org/wiki/Bryce_Canyon_National_Park | Wrong-entity generic destination page is rejected for specific subject links. | [#34](https://github.com/flysoftware-git/itinerary-generator/issues/34) |
| PR-010 | Area-reference query links for named restaurants | https://www.google.com/maps/search/Chuckleberry%27s+near+Capitol+Reef+National+Park | Area-reference query links are blocked and rejected for subject-specific targets. | [#35](https://github.com/flysoftware-git/itinerary-generator/issues/35) |
| PR-012 | Fruita Campground waypoint | https://www.google.com/maps/dir/Moab,+UT/Fruita+Campground,+Capitol+Reef | Directions URLs are blocked for waypoint entity links; waypoint is rendered without misleading link. | [#36](https://github.com/flysoftware-git/itinerary-generator/issues/36) |
| PR-014 | Red Cliffs Lodge Restaurant | https://www.google.com/maps/search/?api=1&query=Red%20Cliffs%20Lodge%20Restaurant%20Moab | Ambiguous Google Maps search URL is dropped (fail-closed) when no deterministic entity target is validated. | [#38](https://github.com/flysoftware-git/itinerary-generator/issues/38) |
| PR-016 | Telluride claim-text More info | https://www.google.com/search?q=Check%20the%20Sheridan%20Opera%20House%20schedule%20for%20any%20live%20music%20events%20during%20your%20stay.%20It%27s%20a%20historic%20venue%20that%20frequently%20features%20local%20and%20touring%20artists.%20Telluride | Prose-derived Google search links are blocked and removed when claim-support validation is not met. | [#39](https://github.com/flysoftware-git/itinerary-generator/issues/39) |




