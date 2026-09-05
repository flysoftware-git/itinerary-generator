# Source-Type Coverage Matrix

This document records the current regression coverage for the main URL-source permutations used by the discovery pipeline in [tests/test_url_discovery.py](tests/test_url_discovery.py). It is intended to answer whether the relevant source-type combinations are adequately exercised, without claiming full input-space completeness.

## Scope

The discovery code path in [generator/url_discovery.py](generator/url_discovery.py) depends on several source modes and validation branches. The main permutations are:

1. AI candidate URL only
2. Search fallback
3. Direct-link batch (authoritative)
4. Direct-link batch (non-authoritative)
5. HTML direct-batch payload before search
6. Google Maps fallback / maps-derived candidate
7. Existing URL preservation without rematch
8. Generic landing page / bad URL rejection
9. Homepage-specific URL acceptance

## Coverage verdict

Adequate for the major source-type permutations used by the pipeline, with representative regression coverage for each class. Not exhaustive over the entire input space.

## Source approval matrix

The current policy is explicit: when a destination is configured to use direct-link batch sources, those direct-batch rows are treated as highest priority and override generic search results. Generic sources remain available only in non-direct-batch modes, where they are evaluated normally.

| Source class | Restaurants | Attractions | Notes |
| --- | --- | --- | --- |
| direct_link_batch | Highest priority | Highest priority | Wins over generic results when present and item-specific |
| search / AI candidate / generic fallback | Allowed only in search mode | Allowed only in search mode | Must not override a valid direct-batch candidate |
| maps search / maps area fallback | Secondary fallback | Secondary fallback | Useful only after direct-batch and ordinary web search paths are exhausted |
| existing preserved row URL | Preserved when direct-batch and item-specific | Preserved when direct-batch and item-specific | Guarded by item validation |

## Direct-batch policy note

The authoritative direct-batch policy is intentionally narrow: a curated batch row remains the minimum accepted selection when it is item-matching and not explicitly dead. The hard reject case is limited to clear dead links (HTTP 404/410 or equivalent terminal failures), not generic region pages, city landing pages, or broad Google Maps search links. Generic Google Maps or generic search URLs remain blocked in normal enforce-mode auditing because they are ambiguous multi-result spillover rather than canonical item pages; the exception is the same harvest-time allowance used to keep a direct-batch row viable while it is still being resolved to a better canonical source.

This preserves the fail-closed rule for authoritative no-match: if a direct-batch source is configured as authoritative and no usable row survives, the pipeline should omit the link rather than silently accept a generic fallback. It does not, however, broaden the rejection rule to discard valid curated direct-batch rows merely because they are broader or less specific than a canonical page.

## Matrix

| Source type / permutation | Status | Evidence in tests |
| --- | --- | --- |
| AI candidate URL used before search | Covered | `test_restaurant_discovery_uses_ai_url_candidates_before_search_passes`, `test_discover_restaurants_direct_batch_takes_precedence_over_ai_candidate_url`, `test_discover_attractions_direct_batch_takes_precedence_over_ai_candidate_for_non_trail`, `test_discover_attractions_direct_batch_authoritative_recovers_seed_from_ai_candidate` |
| Search fallback path | Covered | `test_restaurant_discovery_two_pass`, `test_discover_restaurants_can_use_direct_batch_source`, `test_discover_attractions_uses_google_maps_place_for_non_trail_when_web_search_misses` |
| Direct-batch authoritative success | Covered | `test_search_restaurant_direct_batch_authoritative_prefers_non_maps_when_multiple_rows_match`, `test_search_en_route_direct_batch_authoritative_prefers_maps_when_multiple_rows_match`, `test_search_attraction_direct_batch_authoritative_uses_maps_link_from_snippet_text` |
| Direct-batch authoritative rejection / no match | Covered | `test_search_restaurant_direct_batch_authoritative_rejects_tripadvisor_area_listing`, `test_search_restaurant_direct_batch_authoritative_rejects_near_destination_maps_query`, `test_search_restaurant_direct_batch_authoritative_rejects_maps_q_for_other_venue`, `test_search_attraction_direct_batch_authoritative_omits_link_when_batch_has_no_match`, `test_discover_attractions_direct_batch_authoritative_no_match_does_not_assign_maps_fallback` |
| Direct-batch authoritative mismatch with item name | Covered | `test_search_restaurant_direct_batch_authoritative_prefers_item_matching_url`, `test_search_attraction_direct_batch_authoritative_rejects_snippet_maps_link_for_other_item` |
| Direct-batch authoritative keeps valid generic landing page when item-matching | Covered | `test_search_attraction_direct_batch_authoritative_keeps_item_matching_generic_landing_page` |
| Direct-batch non-authoritative path | Covered | `test_search_restaurant_non_authoritative_rejects_tripadvisor_area_listing`, `test_search_restaurant_non_authoritative_prefers_maps_place_over_generic_non_maps` |
| HTML direct-batch payload preferred over search rows | Covered | `test_get_restaurant_direct_batch_rows_prefers_html_payload_before_search_rows`, `test_get_en_route_direct_batch_rows_falls_back_to_search_when_html_empty`, `test_get_restaurant_direct_batch_rows_retries_html_prompt_when_rows_below_minimum` |
| Destination attraction payload captured when current items are all trails | Covered | `test_zion_attraction_direct_batch_html_integration_round_trip`, `test_zion_all_trail_items_still_capture_attraction_direct_batch_payload` |
| Google Maps fallback / maps-derived candidate | Covered | `test_search_restaurant_from_direct_batch_falls_back_to_source_when_maps_missing`, `test_search_restaurant_direct_batch_authoritative_skips_invalid_maps_and_uses_other`, `test_search_en_route_direct_batch_authoritative_prefers_maps_place_over_source_url`, `test_search_attraction_from_maps_area_pool_selects_item_specific_maps_candidate` |
| Existing URL preserved without rematch | Covered | `test_discover_restaurants_direct_batch_preserves_existing_url_without_rematch`, `test_discover_restaurants_direct_batch_preserves_existing_maps_url_without_rematch`, `test_discover_attractions_direct_batch_preserves_existing_url_without_rematch`, `test_discover_en_route_stops_direct_batch_preserves_existing_url_without_rematch` |
| Generic landing page rejection | Covered | `test_audit_emits_audit_rejection_event_for_restaurant_generic_url`, `test_retain_url_rejects_generic_restaurant_landing_page_for_named_entity`, `test_retain_url_rejects_google_maps_search_for_named_restaurant_in_enforce_mode` |
| Homepage-specific acceptance | Covered | `test_audit_preserves_restaurant_homepage_url_for_specific_site`, `test_audit_preserves_en_route_homepage_url_for_specific_site`, `test_retain_url_accepts_item_specific_restaurant_homepage_when_content_matches` |
| Route-specific scenic-drive validation | Covered | `test_audit_rejects_scenic_drive_place_page_url_without_route_intent`, `test_audit_keeps_direct_batch_authoritative_restaurant_even_if_generic_landing_url`, `test_discover_scenic_drives_uses_nps_deterministic_url_for_nps_park` |
| Restaurant metadata backfill and validation | Covered | `test_enrich_restaurant_metadata_from_url_populates_missing_fields`, `test_backfill_restaurant_metadata_from_available_text_inferrs_cuisine_and_price`, `test_audit_validates_authoritative_restaurant_maps_place_url` |

## Important caveat

This matrix demonstrates that the core source-type permutations have been tested. It does not prove complete coverage of the entire input space across all destination names, URL variants, punctuation patterns, or domain-specific edge cases.

A more precise statement is:

- Strong coverage for the primary source-type branches used by URL discovery.
- Representative regression coverage for the failures already encountered.
- Not an exhaustive proof of all possible input combinations.

## Representative high-value gaps to monitor

The remaining risk areas are the dimensions that vary within each permutation, especially:

- destination formatting variants: "St George", "St. George", "Saint George"
- venue name variations: "&", "and", hyphenation, apostrophes, abbreviations
- URL taxonomy: official homepage, maps search, maps place, TripAdvisor listing, redirect chains
- slow-fail cases: 403 pages, sparse page text, redirect mismatch, generic listing pages
- date-specific logic where the same venue behaves differently by season or closure state

## Evidence snapshot

The coverage is concentrated in the following test sections of [tests/test_url_discovery.py](tests/test_url_discovery.py):

- restaurant discovery: lines roughly 89-196 and 629-954
- direct-batch restaurant matching: 1418-2281
- direct-batch HTML and metadata: 2323-2535
- route and scenic-drive validation: 2865-3250
- retention and URL relevance: 5278-6057
- audit and preserved-homepage cases: 7089-7156

This set covers the principal source-type permutations used by the actual application logic while keeping the suite focused on real breakpoints rather than broad combinatorial noise.
