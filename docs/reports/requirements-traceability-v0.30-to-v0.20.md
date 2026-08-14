# Requirements Traceability to Tests (v2.1 to v0.20)

Date: 2026-08-02
Scope: Changelog requirements from v2.1 through v0.20 in docs/requirements.md,
plus post-triage quality-hardening linkage for current provenance/schedule updates.

Legend:
- Tested: direct test evidence exists for the requirement behavior.
- Partial: some evidence exists, but not all acceptance dimensions are explicitly tested.
- Inspect: no strong direct test found; verify by code/design inspection and runtime output review.

## Traceability Matrix

| Req ID | Requirement (short) | Evidence | Status | Recommendation |
|---|---|---|---|---|
| v2.1-1 | Configurable schedule start time (`trip.default_day_start_time`, destination override) | tests/test_content_normalization.py::test_inject_travel_realism_uses_default_day_start_time_for_arrival_leg; tests/test_content_normalization.py::test_inject_travel_realism_honors_destination_start_time_override | Tested | Keep |
| v2.1-2 | Configurable daily activity-hour budget (`trip.default_daily_activity_hours`, destination override) | tests/test_content_normalization.py::test_inject_travel_realism_packs_multiple_afternoon_activities_with_default_budget; tests/test_content_normalization.py::test_inject_travel_realism_respects_destination_activity_hour_override | Tested | Keep |
| v2.1-3 | Afternoon multi-activity packing only when durations fit budget after transit | tests/test_content_normalization.py::test_inject_travel_realism_packs_multiple_afternoon_activities_with_default_budget; tests/test_content_normalization.py::test_inject_travel_realism_respects_destination_activity_hour_override | Tested | Keep |
| v2.1-4 | Named-entity rendering middle-ground (canonical first, explicit map fallback second, hide when neither available) | tests/test_html_assembler.py::test_build_restaurants_hides_items_without_link_or_fallback; tests/test_html_assembler.py::test_build_restaurants_uses_maps_fallback_when_canonical_missing; tests/test_html_assembler.py::test_build_attractions_without_canonical_url_renders_plain_text_no_maps_fallback; tests/test_html_assembler.py::test_build_getting_here_without_stop_url_renders_plain_stop_text | Tested | Keep |
| v2.1-5 | Return leg time anchor shown in departure route card | tests/test_html_assembler.py::test_build_getting_there_includes_return_anchor_time; tests/test_html_assembler.py::test_build_map_markers_includes_sequential_stop_indices | Tested | Keep |
| v0.30-1 | Last destination must render dedicated Getting There block | tests/test_html_assembler.py::test_build_getting_there_renders_departure_route_options | Tested | Keep |
| v0.30-2 | Departure-aligned one-way drives moved to ai_content.getting_there.route_options | tests/test_ai_content_normalization.py::test_filter_departure_aligned_drives_moves_matching_one_way_drive_to_getting_there | Tested | Keep |
| v0.30-3 | Marker payload has stop_index aligned with tabs; marker preserves date context while numbered | tests/test_html_assembler.py::test_build_map_markers_includes_sequential_stop_indices; tests/test_html_assembler.py::test_assembled_html_preserves_marker_date_context_alongside_stop_indices | Tested | Keep |
| v0.30-4 | URL domain denylist hard-rejects known-untrusted domains | tests/test_url_discovery.py::test_retain_url_rejects_domain_in_denylist | Tested | Keep |
| v0.30-5 | Scenic-drive links require route intent (not generic place pages) | tests/test_url_discovery.py::test_audit_rejects_scenic_drive_place_page_url_without_route_intent | Tested | Keep |
| v0.30-6 | Cross-destination scenic-drive dedup against attraction ownership | tests/test_url_discovery.py::test_deduplicate_cross_destination_drives_removes_overlap_with_other_destination_attraction | Tested | Keep |
| v0.30-7 | Footer issue guidance on second line with distinct links | tests/test_html_assembler.py::test_footer_issue_guidance_is_split_and_template_specific | Tested | Keep |
| v0.29-1 | Exclude permanently closed/not-yet-open venues from dinner recommendations | tests/test_url_discovery.py::test_audit_removes_ineligible_restaurant_from_destination | Tested | Keep |
| v0.29-2 | Restaurant name denylist enforced | tests/test_url_discovery.py::test_is_restaurant_ineligible_via_name_denylist | Tested | Keep |
| v0.29-3 | Closure and pre-opening marker detection via fetched page text | tests/test_url_discovery.py::test_is_restaurant_ineligible_via_closure_page_text; tests/test_url_discovery.py::test_is_restaurant_ineligible_via_pre_opening_page_text | Tested | Keep |
| v0.29-4 | AI-side closure signal rejection in _normalize_restaurants | tests/test_ai_content_normalization.py::test_normalize_restaurants_filters_ai_closure_signal | Tested | Keep |
| v0.28-1 | Within-destination attraction/scenic-drive dedup (one-card-one-entity) | tests/test_url_discovery.py::test_deduplicate_within_destination_removes_drive_matching_attraction | Tested | Keep |
| v0.28-2 | Cross-section dedup: local_tip not duplicated from what_to_know | tests/test_content_normalization.py::test_cross_section_dedup_removes_local_tip_present_in_what_to_know | Tested | Keep |
| v0.28-3 | Cross-destination what_to_know dedup to fallback | tests/test_content_normalization.py::test_cross_destination_what_to_know_dedup_resets_repeated_field | Tested | Keep |
| v0.28-4 | Compound entity URL rejection (" & ") | tests/test_url_discovery.py::test_retain_url_rejects_compound_entity_name | Tested | Keep |
| v0.27-1 | Encyclopedic URL entity-path integrity (Wikipedia slug) | tests/test_url_discovery.py::test_retain_url_rejects_wikipedia_wrong_entity | Tested | Keep |
| v0.27-2 | AllTrails redirect entity-match requirement | tests/test_url_discovery.py::test_is_relevant_result_rejects_alltrails_redirect_to_different_entity; tests/test_url_discovery.py::test_is_relevant_result_rejects_alltrails_redirect_mismatch_when_blocked_fetch | Tested | Keep |
| v0.27-3 | Configurable AllTrails slug denylist | tests/test_url_discovery.py::test_retain_url_rejects_alltrails_slug_in_denylist | Tested | Keep |
| v0.26-1 | URL class blocklist (maps search/dir, google search, social media) | tests/test_url_discovery.py::test_retain_url_rejects_google_maps_search_in_enforce_mode; tests/test_url_discovery.py::test_retain_url_rejects_google_maps_dir_in_enforce_mode; tests/test_url_discovery.py::test_retain_url_rejects_google_search_in_enforce_mode; tests/test_url_discovery.py::test_retain_url_rejects_social_media_in_enforce_mode | Tested | Keep |
| v0.26-2 | Fail-closed for named entities when deterministic link unavailable | tests/test_url_discovery.py::test_retain_url_rejects_compound_entity_name; tests/test_url_discovery.py::test_retain_url_rejects_google_maps_search_in_enforce_mode; tests/test_url_discovery.py::test_audit_fail_closed_removes_named_entity_url_when_policy_blocks_only_candidate | Tested | Keep |
| v0.26-3 | URL policy supports monitor-only mode | tests/test_url_discovery.py::test_retain_url_keeps_blocked_class_in_monitor_mode | Tested | Keep |
| v0.26-4 | Maps-search fallback never publishable for named entities | tests/test_url_discovery.py::test_retain_url_rejects_google_maps_search_in_enforce_mode; tests/test_url_discovery.py::test_retain_url_rejects_google_maps_search_for_named_restaurant_in_enforce_mode; tests/test_url_discovery.py::test_retain_url_rejects_google_maps_search_for_named_waypoint_in_enforce_mode | Tested | Keep |
| v0.25-1 | Optional filtered AllTrails mode with hard constraints | tests/test_url_discovery.py::test_filtered_alltrails_strategy_rejects_candidates_outside_constraints | Tested | Keep |
| v0.25-2 | Filtered mode ranks by rating/reviews and allows fewer-than-target | tests/test_url_discovery.py::test_filtered_alltrails_strategy_prefers_highest_rated_candidate_with_constraints; tests/test_url_discovery.py::test_filtered_alltrails_does_not_pad_with_weak_matches_when_only_one_candidate_passes | Tested | Keep |
| v0.24-1 | AllTrails publish-confidence gate and fallback on low confidence | tests/test_url_discovery.py::test_trail_like_attraction_falls_back_when_alltrails_confidence_below_threshold | Tested | Keep |
| v0.24-2 | alltrails_min_confidence_for_publish strictness control | tests/test_url_discovery.py::test_retain_discovered_url_rejects_low_confidence_alltrails_for_trails | Tested | Keep |
| v0.23-1 | Vote-gated rating boosts for AllTrails and restaurant candidates | tests/test_url_discovery.py::test_restaurant_rating_priority_requires_sufficient_votes; tests/test_url_discovery.py::test_alltrails_rating_priority_requires_sufficient_votes | Tested | Keep |
| v0.23-2 | Config controls for rating/vote thresholds and boosts | tests/test_url_discovery.py::test_load_interest_filters_applies_rating_threshold_and_boost_controls | Tested | Keep |
| v0.22-1 | Seed attractions injected/protected at normalization time | tests/test_ai_content_normalization.py::test_ensure_seed_attractions_adds_missing_seed; tests/test_ai_content_normalization.py::test_normalize_destination_content_preserves_seeded_angels_landing_through_enroute_filter | Tested | Keep |
| v0.22-2 | Schedule boundary policy (first morning travel + final return windows) | tests/test_ai_content_normalization.py::test_normalize_schedule_reserves_first_day_morning_for_origin_transport; tests/test_ai_content_normalization.py::test_normalize_schedule_reserves_last_day_afternoon_evening_for_return | Tested | Keep |
| v0.22-3 | Canonical slug preference and place-level anti-false-trail guard | tests/test_url_discovery.py::test_search_strict_prefers_exact_alltrails_slug_over_via_variant; tests/test_url_discovery.py::test_place_level_attraction_not_forced_to_alltrails_even_when_type_is_hike | Tested | Keep |
| v0.22-4 | Restaurant cards prefer discovered URL over query fallback in rendering | tests/test_html_assembler.py::test_build_restaurants_prefers_discovered_url_over_maps_query | Tested | Keep |
| v0.22-5 | CLI --first-destination behavior | tests/test_main_requirements.py::test_filter_destinations_can_limit_to_first_destination; tests/test_main_requirements.py::test_filter_destinations_applies_destination_filter_before_first_destination | Tested | Keep |
| v0.21-1 | Schedule normalization full period coverage + uniqueness + arrival/departure context | tests/test_ai_content_normalization.py::test_normalize_schedule_fills_sparse_multi_day_periods_and_departure_on_last_day; tests/test_ai_content_normalization.py::test_normalize_schedule_ensures_each_day_has_unique_signal | Tested | Keep |
| v0.21-2 | Expanded trail-like detection beyond explicit hike type | tests/test_url_discovery.py::test_trail_like_attraction_prefers_alltrails_even_when_type_is_not_hike; tests/test_url_discovery.py::test_trail_like_attraction_prefers_alltrails_for_riverside_walk_name | Tested | Keep |
| v0.21-3 | Generic landing-page rejection + trusted-host SSL fallback | tests/test_url_discovery.py::test_search_strict_rejects_generic_nps_things2do_page; tests/test_url_discovery.py::test_search_strict_accepts_blm_url_when_ssl_fallback_fetch_succeeds | Tested | Keep |
| v0.21-4 | Capitol Reef marine-image disambiguation hardening | tests/test_image_fetcher.py::test_rank_images_penalizes_marine_mismatch_for_capitol_reef; tests/test_image_fetcher.py::test_rank_images_hard_rejects_marine_only_results_for_inland_dest | Tested | Keep |
| v0.21-5 | Destination-agnostic image blacklist | tests/test_image_fetcher.py::test_rank_images_global_blacklist_rejects_underwater_for_any_destination | Tested | Keep |
| v0.20-1 | Attraction-interest filtering (blacklist + seasonal ski suppression) | tests/test_url_discovery.py::test_discover_attractions_skips_blacklisted_interest_keywords; tests/test_url_discovery.py::test_discover_attractions_skips_ski_out_of_season; tests/test_url_discovery.py::test_discover_attractions_allows_ski_in_season | Tested | Keep |
| v0.20-2 | What-to-Know schema trimmed to rendered fields only | tests/test_html_assembler.py::test_intro_note_omits_weather_and_photography_rows; tests/test_ai_content_normalization.py::test_normalize_what_to_know_does_not_require_or_emit_legacy_weather_photo_fields | Tested | Keep |
| v0.20-3 | Scenic-drive popup optional verified More Info link; no generic fallback required | tests/test_html_assembler.py::test_drive_descriptions_include_popup_url_when_available; tests/test_html_assembler.py::test_drive_descriptions_omit_popup_url_when_unsafe | Tested | Keep |

## Post-Triage Hardening Addendum (v2.0)

This addendum links the four agreed hardening steps to requirements and focused
test gates. It is intentionally short-gate oriented (targeted suites first,
single smoke run last).

| Step | Requirement Linkage | Existing Evidence | Required Additions | Adequacy |
|---|---|---|---|---|
| 1. Renderer consumes fail-closed outcomes with explicit fallback mode | docs/requirements.md §5 fail-closed + fallback rendering policy; provenance-controlled publication requirement | tests/test_html_assembler.py::test_build_restaurants_hides_items_without_link_or_fallback; tests/test_html_assembler.py::test_build_restaurants_uses_maps_fallback_when_canonical_missing; tests/test_html_assembler.py::test_build_attractions_without_canonical_url_renders_plain_text_no_maps_fallback; tests/test_html_assembler.py::test_build_getting_here_without_stop_url_renders_plain_stop_text | None | Adequate |
| 2. Restaurant credibility gate (historical/off-destination/hallucinated targets rejected) | docs/requirements.md §5 named-entity determinism; §4 restaurant freshness and reliability semantics | tests/test_url_discovery.py::test_is_restaurant_ineligible_via_name_denylist; tests/test_url_discovery.py::test_is_restaurant_ineligible_via_closure_page_text; tests/test_url_discovery.py::test_retain_url_rejects_google_maps_search_for_named_restaurant_in_enforce_mode | Add focused tests for: historical-place false positive (Gifford class), off-destination duplicate-name mismatch (Capitol Reef Cafe class), unresolved canonical -> fail-closed publish | Partial (needs targeted additions) |
| 3. Category stoplist and offer-page suppression (guide/listing pages not treated as entity links) | docs/requirements.md §5 category-vs-entity + fail-closed policy | tests/test_url_discovery.py::test_discover_attractions_omits_maps_fallback_for_ambiguous_geographic_feature_name; docs/reports/v1.4-output-review/index.md PR-022 status tracking | Add targeted tests for activity-offer patterns (guide/listing pages) and explicit stoplist behavior for fly-fishing style category entities | Partial (open PR class, requires tests + fix) |
| 4. Compact golden-manifest contract suite for reopened classes | docs/design/provenance-control-and-scheduling-rationalization.md validation strategy; docs/design/v2-issue-6-invariants.md | Existing focused suites provide components but not a single contract matrix pass | Add small contract suite (new test module) asserting no named maps-search primaries, no invalid AllTrails slugs, schedule day differentiation threshold, departure-route placement invariants | Partial (needs dedicated contract suite) |

### Focused Gate Sequence (Before Any Full Run)

1. Gate A (renderer fail-closed behavior)
- `pytest tests/test_html_assembler.py -k "attractions_without_canonical_url or getting_here_without_stop_url or restaurants"`

2. Gate B (URL discovery provenance/credibility gates)
- `pytest tests/test_url_discovery.py -k "restaurant_ineligible or named_restaurant or category or ambiguous_geographic or alltrails"`

3. Gate C (schedule rationalization + placement invariants)
- `pytest tests/test_ai_content_normalization.py -k "unique_signal or reserves_first_day_morning or reserves_last_day_afternoon_evening"`
- `pytest tests/test_html_assembler.py -k "departure_route_options or schedule_preserves_structured_one_day_schedule"`

4. Gate D (compact contract suite)
- `pytest tests/test_main_requirements.py -k "quality_contract|fail_closed|route_ownership|schedule"`

5. Smoke gate (single end-to-end run)
- One controlled `generator.main` run only after Gates A-D pass.

## Priority Gaps (Updated)

1. Restaurant credibility still benefits from more fixtures for historical-place and off-destination false positives.
2. Category stoplist/offer-page suppression remains high-risk and should retain targeted regression pressure.
3. A compact, dedicated quality-contract suite is still recommended before further optimization work.

## Requirements Better Verified by Inspection

1. Visual compactness/overlap behavior of map markers across dense routes and mobile viewports.
2. Footer second-line readability and spacing in real browser rendering (desktop + mobile).
3. Getting There section placement/flow quality relative to schedule and attractions in full generated outputs.
4. Scenic-drive route-intent heuristic precision across diverse destination domains (false positives/negatives).
