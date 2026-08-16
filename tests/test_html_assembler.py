import pytest

from generator.html_assembler import CHECKSUM_PATH, TEMPLATE_PATH, HTMLAssembler, _verify_checksum


def test_template_checksum_matches_stored_hash() -> None:
    """Guard against the exact mistake made twice in this session: editing
    templates/v2.5_template.html without regenerating templates/checksums.txt.
    _verify_checksum hard-fails every real run on mismatch (by design, to
    stop a tampered/stale template from silently rendering) -- this test
    catches it at commit time instead of at generation time in production."""
    template_text = TEMPLATE_PATH.read_text(encoding="utf-8")

    _verify_checksum(template_text)  # raises RuntimeError on mismatch


def test_template_checksum_file_is_well_formed() -> None:
    """The checksum file's first whitespace-separated token must be a bare
    64-char lowercase hex sha256 digest -- _verify_checksum does
    .split()[0] with no validation, so a malformed file (wrong length,
    uppercase, stray characters) would silently compare unequal to a
    correctly-computed hash and hard-fail every run with a confusing
    "mismatch" error instead of a clear "checksum file is malformed" one."""
    stored = CHECKSUM_PATH.read_text(encoding="utf-8").strip().split()[0]

    assert len(stored) == 64
    assert stored == stored.lower()
    assert all(ch in "0123456789abcdef" for ch in stored)


def test_drive_descriptions_include_popup_url_when_available() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = [
        {
            "scenic_drives": [
                {
                    "title": "Zion Canyon Scenic Drive",
                    "category": "scenic_drive",
                    "distance_or_duration": "54 mi",
                    "best_time": "Morning",
                    "description": "Classic canyon drive.",
                    "vehicle_requirement": "any",
                    "url": "https://example.com/should-not-render",
                }
            ]
        }
    ]

    payload = assembler._build_drive_descriptions(destinations)

    assert payload["Zion Canyon Scenic Drive"].get("url") == "https://example.com/should-not-render"
    assert payload["Zion Canyon Scenic Drive"].get("route_map_url", "").startswith("https://www.google.com/maps/dir/?api=1&destination=")


def test_drive_descriptions_omit_popup_url_when_unsafe() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = [
        {
            "scenic_drives": [
                {
                    "title": "Unsafe Drive",
                    "category": "scenic_drive",
                    "distance_or_duration": "18 mi",
                    "best_time": "Morning",
                    "description": "Unsafe link test.",
                    "vehicle_requirement": "any",
                    "url": "javascript:alert(1)",
                }
            ]
        }
    ]

    payload = assembler._build_drive_descriptions(destinations)

    assert "url" not in payload["Unsafe Drive"]
    assert payload["Unsafe Drive"].get("route_map_url", "").startswith("https://www.google.com/maps/dir/?api=1&destination=")


def test_drive_descriptions_use_explicit_maps_directions_route_when_available() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = [
        {
            "name": "Bryce Canyon National Park",
            "scenic_drives": [
                {
                    "title": "Bryce Canyon Scenic Drive",
                    "category": "scenic_drive",
                    "distance_or_duration": "18 mi",
                    "best_time": "Sunrise",
                    "description": "Rim viewpoints and amphitheater overlooks.",
                    "vehicle_requirement": "any",
                    "route_map_url": "https://www.google.com/maps/dir/?api=1&origin=Bryce+Canyon+Visitor+Center&destination=Rainbow+Point",
                }
            ],
        }
    ]

    payload = assembler._build_drive_descriptions(destinations)

    assert payload["Bryce Canyon Scenic Drive"]["route_map_url"].startswith("https://www.google.com/maps/dir/?api=1&origin=")


def test_scenic_drive_card_uses_teaser_while_popup_keeps_full_description() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    drives = [
        {
            "title": "Zion Canyon Scenic Drive",
            "category": "drive",
            "distance_or_duration": "54 mi",
            "description": "First sentence teaser. Second sentence detailed popup guidance.",
        }
    ]

    html = assembler._build_attractions({"top_attractions": []}, drives=drives, dest_name="Zion National Park")
    payload = assembler._build_drive_descriptions([{"scenic_drives": drives}])

    assert "First sentence teaser." in html
    assert "Second sentence detailed popup guidance." not in html
    assert payload["Zion Canyon Scenic Drive"]["description"] == (
        "First sentence teaser. Second sentence detailed popup guidance."
    )


def test_scenic_drive_card_ignores_st_abbreviation_in_first_sentence() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    drives = [
        {
            "title": "Gondola at the St. George Temple",
            "category": "drive",
            "distance_or_duration": "10 min",
            "description": "The St. George Temple gondola offers a quick scenic loop and easy access to downtown. Plan around crowds in the afternoon.",
        }
    ]

    html = assembler._build_attractions({"top_attractions": []}, drives=drives, dest_name="St. George, Utah")

    assert "The St. George Temple gondola offers a quick scenic loop and easy access to downtown." in html
    assert "The St." not in html.split("The St.", 1)[1][:20]


def test_assembled_html_omits_attribution_footer(tmp_path) -> None:
    assembler = HTMLAssembler(config_path="config.yaml")
    trip = {
        "trip": {
            "title": "Test Trip",
            "theme_color": "#C0623E",
        },
        "_meta": {
            "generator_version": "test",
            "template_version": "test",
            "generated_at_utc": "2026-07-24T00:00:00+00:00",
            "llm": {"provider": "openai", "model": "test", "usage": {"models": [], "total_estimated_cost_usd": 0.0}},
        },
        "destinations": [],
    }

    html = assembler.assemble(trip)

    assert "id=\"attribution-block\"" not in html
    assert "Attribution &amp; Version Information" not in html


def test_assembled_html_includes_generator_footer_signature() -> None:
    assembler = HTMLAssembler(config_path="config.yaml")
    trip = {
        "trip": {
            "title": "Test Trip",
            "theme_color": "#C0623E",
        },
        "_meta": {
            "generator_version": "9.9.9",
            "template_version": "2.5",
            "generated_at_utc": "2026-07-26T17:41:23+00:00",
            "llm": {"provider": "openai", "model": "test", "usage": {"models": [], "total_estimated_cost_usd": 0.0}},
        },
        "destinations": [],
    }

    html = assembler.assemble(trip)

    assert "Generated by" in html
    assert "Road Trip Itinerary Generator" in html
    assert "v9.9.9" in html
    assert "Itinerary output: 2026-07-26 17:41 UTC" in html


def test_footer_issue_guidance_is_split_and_template_specific() -> None:
    assembler = HTMLAssembler(config_path="config.yaml")
    trip = {
        "trip": {
            "title": "Test Trip",
            "theme_color": "#C0623E",
        },
        "_meta": {
            "generator_version": "9.9.9",
            "template_version": "2.5",
            "generated_at_utc": "2026-07-26T17:41:23+00:00",
            "llm": {"provider": "openai", "model": "test", "usage": {"models": [], "total_estimated_cost_usd": 0.0}},
        },
        "destinations": [],
    }

    html = assembler.assemble(trip)

    assert "Issue reporting:" in html
    assert "?template=broken-link-report.yml&labels=bug" in html
    assert "?template=itinerary-feedback.yml" in html


def test_image_gallery_uses_unified_tile_structure() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    images = [
        {"local_path": "output/images/hero.jpg", "credit": "Hero"},
        {"local_path": "output/images/one.jpg", "credit": "Photo One"},
    ]

    html = assembler._build_image_gallery(images, "Zion National Park")

    assert '<div class="image-tile photo-item">' in html
    assert '<div class="caption photo-caption">Photo One</div>' in html
    assert 'onerror="this.style.display=\'none\';"' in html
    assert "<p class=\"photo-caption\">" not in html


def test_weather_url_uses_weather_gov_for_us_coordinates() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    url = assembler._build_weather_url({"lat": 37.2982, "lng": -113.0263})
    assert "forecast.weather.gov" in url


def test_weather_url_uses_global_fallback_for_non_us_coordinates() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    url = assembler._build_weather_url({"lat": 45.8150, "lng": 15.9819})
    assert "weather.com/weather/today/l/" in url


def test_build_map_markers_includes_sequential_stop_indices() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = [
        {"name": "Zion National Park", "dates": "October 7-9, 2026", "lat": 37.3, "lng": -113.0},
        {"name": "Bryce Canyon National Park", "dates": "October 10-12, 2026", "lat": 37.6, "lng": -112.2},
    ]
    trip_meta = {
        "departure": "Las Vegas",
        "departure_lat": 36.17,
        "departure_lng": -115.14,
        "departure_datetime": "2026-10-07 08:30",
        "return": "Salt Lake City",
        "return_lat": 40.76,
        "return_lng": -111.89,
        "return_datetime": "2026-10-18 17:15",
    }

    markers = assembler._build_map_markers(destinations, trip_meta)
    dest_markers = [m for m in markers if "idx" in m]

    assert [m["idx"] for m in dest_markers] == [1, 2]
    assert [m["stop_index"] for m in dest_markers] == [1, 2]
    assert markers[0]["mo"] == "DEP"
    assert markers[-1]["mo"] == "RET"
    assert markers[0]["date_label"] == "Oct 7"
    assert markers[0]["time_label"] == "8:30 AM"
    assert markers[-1]["date_label"] == "Oct 18"
    assert markers[-1]["time_label"] == "5:15 PM"


def test_build_map_markers_falls_back_for_return_when_return_coords_missing() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = [
        {"name": "Stop 1", "dates": "October 7-8, 2026", "lat": 36.1, "lng": -107.2},
        {"name": "Stop 2", "dates": "October 9-10, 2026", "lat": 37.2, "lng": -108.3},
    ]
    trip_meta = {
        "departure": "Home",
        "departure_lat": 35.0,
        "departure_lng": -106.0,
        "departure_datetime": "2026-10-07 09:00",
        "return": "Airport",
        "return_datetime": "2026-10-10 17:15",
    }

    markers = assembler._build_map_markers(destinations, trip_meta)
    ret_marker = [m for m in markers if m.get("mo") == "RET"][0]

    assert ret_marker["c"] == [37.2, -108.3]
    assert ret_marker["date_label"] == "Oct 10"
    assert ret_marker["time_label"] == "5:15 PM"


def test_build_google_maps_url_prefers_lodging_locations_for_route_endpoints() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = [
        {
            "name": "Zion National Park",
            "lodging": {"location": "Zion Lodge, Springdale, UT"},
        },
        {
            "name": "Bryce Canyon National Park",
            "lodging": {"location": "Bryce Canyon Lodge, Bryce, UT"},
        },
    ]

    url = assembler._build_google_maps_url(destinations, trip_meta={})

    assert "origin=Zion%20Lodge%2C%20Springdale%2C%20UT" in url
    assert "destination=Bryce%20Canyon%20Lodge%2C%20Bryce%2C%20UT" in url


def test_destination_route_target_falls_back_to_destination_name_without_lodging() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)

    route_target = assembler._destination_route_target({"name": "Zion National Park"})

    assert route_target == "Zion National Park"


def test_build_getting_here_uses_destination_name_not_lodging_for_route_target() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Arrive via scenic corridor.",
            "en_route_stops": [{"name": "Scenic Overlook"}],
        }
    }
    dest = {
        "name": "Riverbend Retreat",
        "lodging": {"location": "123 Main Street, Exampleville, TX"},
    }

    html = assembler._build_getting_here(
        ai,
        dest,
        previous_name="Las Vegas",
        previous_route_target="Harry Reid International Airport, Las Vegas, NV",
        current_route_target="Riverbend Retreat",
    )

    assert "origin=Harry%20Reid%20International%20Airport%2C%20Las%20Vegas%2C%20NV" in html
    assert "destination=Riverbend%20Retreat" in html
    assert "waypoints=Scenic%20Overlook" in html
    assert 'class="gmaps-link"' in html
    assert 'target="_blank"' in html


def test_build_getting_here_route_waypoints_are_destination_scoped_for_ambiguous_names() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Arrive via scenic corridor.",
            "en_route_stops": [
                {"name": "Red Cliffs Desert Reserve"},
                {"name": "Leeds Historic District"},
            ],
        }
    }
    dest = {"name": "St. George, Utah"}

    html = assembler._build_getting_here(
        ai,
        dest,
        previous_name="Las Vegas International Airport",
        previous_route_target="Las Vegas International Airport",
        current_route_target="St. George, Utah",
    )

    assert "waypoints=Red%20Cliffs%20Desert%20Reserve%20St.%20George%2C%20Utah|Leeds%20Historic%20District%20St.%20George%2C%20Utah" in html


def test_build_getting_here_route_skips_ineligible_waypoints() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive north.",
            "en_route_stops": [
                {"name": "Mesquite", "route_waypoint_eligible": True},
                {"name": "Cedar City", "route_waypoint_eligible": False},
            ],
        }
    }
    dest = {"name": "St. George, Utah"}

    html = assembler._build_getting_here(
        ai,
        dest,
        previous_name="Las Vegas",
        previous_route_target="Las Vegas",
        current_route_target="St. George, Utah",
    )

    assert "waypoints=Mesquite%20St.%20George%2C%20Utah" in html
    assert "Cedar City" not in html
    assert "Cedar%20City" not in html


def test_build_getting_here_orders_waypoints_by_route_progress() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive north.",
            "en_route_stops": [
                {"name": "Last Stop", "route_waypoint_eligible": True, "route_progress_ratio": 0.9},
                {"name": "First Stop", "route_waypoint_eligible": True, "route_progress_ratio": 0.2},
                {"name": "Mid Stop", "route_waypoint_eligible": True, "route_progress_ratio": 0.5},
            ],
        }
    }
    dest = {"name": "St. George, Utah"}

    html = assembler._build_getting_here(
        ai,
        dest,
        previous_name="Las Vegas",
        previous_route_target="Las Vegas",
        current_route_target="St. George, Utah",
    )

    assert "waypoints=First%20Stop%20St.%20George%2C%20Utah|Mid%20Stop%20St.%20George%2C%20Utah|Last%20Stop%20St.%20George%2C%20Utah" in html
    assert html.index("First Stop") < html.index("Mid Stop") < html.index("Last Stop")


def test_build_getting_here_uses_lodging_endpoint_but_destination_scoped_waypoints() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Arrive via I-15.",
            "en_route_stops": [
                {"name": "Red Cliffs Desert Reserve", "route_waypoint_eligible": True},
            ],
        }
    }
    dest = {
        "name": "St. George, Utah",
        "lodging": {"location": "123 Main Street, St. George, Utah"},
    }

    html = assembler._build_getting_here(
        ai,
        dest,
        previous_name="Las Vegas",
        previous_route_target="Las Vegas, Nevada",
        current_route_target="123 Main Street, St. George, Utah",
    )

    assert "destination=123%20Main%20Street%2C%20St.%20George%2C%20Utah" in html
    assert "waypoints=Red%20Cliffs%20Desert%20Reserve%20St.%20George%2C%20Utah" in html


def test_build_attractions_links_open_in_new_tab_and_keep_destination_scope() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Zion Canyon",
                "url": "https://www.google.com/maps/dir/?api=1&destination=Zion+National+Park",
                "description": "Iconic canyon views.",
            }
        ]
    }

    html = assembler._build_attractions(ai, drives=[], dest_name="Zion National Park")

    assert 'target="_blank"' in html
    assert "https://www.google.com/maps/search/?api=1&amp;query=Zion%20Canyon" in html
    assert "https://www.google.com/maps/dir/?api=1&destination=Zion+National+Park" not in html


def test_select_preferred_external_link_preserves_maps_search_url_for_attractions() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    item = {
        "name": "Cedar Breaks Viewpoint",
        "url": "https://www.google.com/maps/search/?api=1&query=Cedar+Breaks+Viewpoint",
    }

    url, is_map_fallback = assembler._select_preferred_external_link(item, section="attraction")

    assert url == "https://www.google.com/maps/search/?api=1&query=Cedar+Breaks+Viewpoint"
    assert is_map_fallback is False


def test_select_preferred_external_link_restaurant_keeps_selected_maps_search_url() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    item = {
        "name": "Fallback Grill",
        "url": "https://www.google.com/maps/search/?api=1&query=Fallback+Grill+St+George",
    }

    url, is_map_fallback = assembler._select_preferred_external_link(item, section="restaurant")

    assert url == "https://www.google.com/maps/search/?api=1&query=Fallback+Grill+St+George"
    assert is_map_fallback is False


@pytest.mark.parametrize(
    "item",
    [
        {"name": "Fallback Grill", "maps_url": "https://www.google.com/maps/search/?api=1&query=Fallback+Grill+St+George"},
    ],
)
def test_select_preferred_external_link_restaurant_rejects_maps_search_fallback_only(item: dict[str, str]) -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)

    url, is_map_fallback = assembler._select_preferred_external_link(item, section="restaurant")

    assert url == ""
    assert is_map_fallback is False


@pytest.mark.parametrize(
    "item",
    [
        {"name": "Wilson Arch", "url": "https://www.google.com/maps/search/?api=1&query=Wilson+Arch+Moab+UT"},
        {"name": "Wilson Arch", "maps_url": "https://www.google.com/maps/search/?api=1&query=Wilson+Arch+Moab+UT"},
    ],
)
def test_select_preferred_external_link_en_route_accepts_maps_search_variants(item: dict[str, str]) -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)

    url, is_map_fallback = assembler._select_preferred_external_link(item, section="en_route_stop")

    assert url == "https://www.google.com/maps/search/?api=1&query=Wilson+Arch+Moab+UT"
    assert is_map_fallback is True


@pytest.mark.parametrize("section", ["restaurant", "en_route_stop"])
def test_select_preferred_external_link_prefers_non_maps_canonical_over_maps_fallback(section: str) -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    item = {
        "name": "Painted Pony",
        "url": "https://paintedponyrestaurant.com/",
        "maps_url": "https://www.google.com/maps/search/?api=1&query=Painted+Pony+St+George",
    }

    url, is_map_fallback = assembler._select_preferred_external_link(item, section=section)

    assert url == "https://paintedponyrestaurant.com/"
    assert is_map_fallback is False


def test_build_getting_there_includes_return_anchor_time() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_there": {
            "route_summary": "Head to Albuquerque for departure.",
            "route_options": [],
        }
    }
    dest = {
        "name": "Santa Fe",
    }
    trip_meta = {
        "return": "Albuquerque, NM airport",
        "return_datetime": "2026-10-29 14:30",
    }

    html = assembler._build_getting_there(ai, dest, trip_meta)

    assert "Return anchor:" in html
    assert "Oct 29 2:30 PM" in html


def test_build_restaurants_omits_items_without_a_usable_url() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_restaurants(
        {
            "dinner_recommendations": [
                {"name": "No Link Diner", "description": "Missing links."},
            ]
        },
        "St. George",
    )

    assert "No Link Diner" not in html
    assert "Dinner Recommendations" in html
    assert "google.com/search?q=No%20Link%20Diner" not in html


def test_build_restaurants_omits_items_when_only_maps_search_fallback_exists() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_restaurants(
        {
            "dinner_recommendations": [
                {
                    "name": "Fallback Grill",
                    "maps_url": "https://www.google.com/maps/search/?api=1&query=Fallback+Grill+St+George",
                    "description": "Fallback map link only.",
                },
            ]
        },
        "St. George",
    )

    assert "Fallback Grill" not in html
    assert "Dinner Recommendations" in html
    assert "google.com/maps/search" not in html
    assert "google.com/search?q=Fallback%20Grill" not in html


def test_build_restaurants_renders_caution_badge_when_promoted_without_url() -> None:
    """A restaurant with no direct URL and no maps fallback can still be
    promoted (_should_render_without_url) on description strength alone, but
    must be visually flagged as unverified rather than shown like a
    source-linked entry."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_restaurants(
        {
            "dinner_recommendations": [
                {
                    "name": "Cozy Corner Cafe",
                    "description": "A cozy trailside cafe known for its green chile stew and fresh sopapillas.",
                    "cuisine": "Southwestern",
                },
            ]
        },
        "St. George",
    )

    assert "Cozy Corner Cafe" in html
    assert '<span class="badge badge-caution" title="No verified source link found for this recommendation">⚠ Unverified</span>' in html


def test_assembled_html_preserves_marker_date_context_alongside_stop_indices() -> None:
    assembler = HTMLAssembler(config_path="config.yaml")
    trip = {
        "trip": {
            "title": "Test Trip",
            "theme_color": "#C0623E",
            "departure": "Las Vegas",
            "departure_lat": 36.17,
            "departure_lng": -115.14,
            "departure_datetime": "2026-10-07 08:30",
            "return": "Salt Lake City",
            "return_lat": 40.76,
            "return_lng": -111.89,
            "return_datetime": "2026-10-18 17:15",
        },
        "_meta": {
            "generator_version": "9.9.9",
            "template_version": "2.5",
            "generated_at_utc": "2026-07-26T17:41:23+00:00",
            "llm": {"provider": "openai", "model": "test", "usage": {"models": [], "total_estimated_cost_usd": 0.0}},
        },
        "destinations": [
            {
                "id": "zion",
                "name": "Zion National Park",
                "dates": "October 7-9, 2026",
                "lat": 37.3,
                "lng": -113.0,
                "images": [],
                "planning_links": [],
                "ai_content": {"top_attractions": [], "getting_here": {}, "possible_daily_schedule": [], "dinner_recommendations": []},
                "what_to_know": {
                    "summary": "Summary.",
                    "local_customs": "Customs.",
                    "best_times_of_day": "Morning.",
                    "transportation_quirks": "Transit.",
                    "safety_considerations": "Safety.",
                    "crowd_patterns": "Crowds.",
                    "local_etiquette": "Etiquette.",
                },
                "cultural_events": {"has_events": False, "events": []},
                "scenic_drives": [],
            }
        ],
    }

    html = assembler.assemble(trip)

    assert '"stop_index": 1' in html
    assert '"mo": "Oct"' in html
    assert '"dy": "7"' in html
    assert '"date_label": "Oct 7"' in html
    assert '"time_label": "8:30 AM"' in html
    assert "var dateLabel = ((s.mo || '') + (s.dy ? (' ' + s.dy) : '')).trim();" in html
    assert "if (s.date_label) { dateLabel = s.date_label; }" in html
    assert "var timeLabel = (s.time_label || '').trim();" in html
    assert "var markerDateSecondaryText = dateLabel;" in html
    assert ".route-marker-nameplate" in html
    assert ".route-marker-date" in html
    assert "font-variant-numeric: tabular-nums;" in html
    assert "iconSize:[26,50],iconAnchor:[13,13],popupAnchor:[0,-20]" in html


def test_assembled_html_supports_destination_hash_deep_links() -> None:
    assembler = HTMLAssembler(config_path="config.yaml")
    trip = {
        "trip": {
            "title": "Hash Test Trip",
            "theme_color": "#C0623E",
            "departure": "Las Vegas",
            "departure_lat": 36.17,
            "departure_lng": -115.14,
            "return": "Salt Lake City",
            "return_lat": 40.76,
            "return_lng": -111.89,
        },
        "_meta": {
            "generator_version": "9.9.9",
            "template_version": "2.5",
            "generated_at_utc": "2026-07-26T17:41:23+00:00",
            "llm": {"provider": "openai", "model": "test", "usage": {"models": [], "total_estimated_cost_usd": 0.0}},
        },
        "destinations": [
            {
                "id": "zion",
                "name": "Zion National Park",
                "dates": "October 7-9, 2026",
                "lat": 37.3,
                "lng": -113.0,
                "images": [],
                "planning_links": [],
                "ai_content": {"top_attractions": [], "getting_here": {}, "possible_daily_schedule": [], "dinner_recommendations": []},
                "what_to_know": {
                    "summary": "Summary.",
                    "local_customs": "Customs.",
                    "best_times_of_day": "Morning.",
                    "transportation_quirks": "Transit.",
                    "safety_considerations": "Safety.",
                    "crowd_patterns": "Crowds.",
                    "local_etiquette": "Etiquette.",
                },
                "cultural_events": {"has_events": False, "events": []},
                "scenic_drives": [],
            }
        ],
    }

    html = assembler.assemble(trip)

    assert "function normalizeHashTarget(rawHash)" in html
    assert "window.history.replaceState(null, '', '#' + sectionId.replace(/^section-/, ''));" in html
    assert "if (window.location.hash) {" in html
    assert "window.addEventListener('hashchange'" in html


def test_build_getting_there_renders_departure_route_options() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_there": {
            "route_summary": "Departure leg toward Albuquerque, NM.",
            "route_options": [
                {
                    "title": "Turquoise Trail Scenic Byway",
                    "distance_or_duration": "50 miles one-way",
                    "description": "Historic route via Madrid.",
                    "url": "https://example.com/turquoise-trail-byway",
                }
            ],
        }
    }
    dest = {"name": "Santa Fe"}
    trip_meta = {"return": "Albuquerque, NM"}

    html = assembler._build_getting_there(ai, dest, trip_meta)

    assert "Departure Route Options" in html
    assert "Turquoise Trail Scenic Byway" in html
    assert "Departure leg toward Albuquerque, NM." in html
    assert "origin=" not in html


def test_build_getting_there_hides_empty_departure_options_subsection() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_there": {
            "route_summary": "Departure leg toward Albuquerque, NM.",
            "route_options": [
                {
                    "title": "Unnamed route option",
                    "description": "No canonical URL available.",
                }
            ],
        }
    }
    dest = {"name": "Santa Fe"}
    trip_meta = {"return": "Albuquerque, NM"}

    html = assembler._build_getting_there(ai, dest, trip_meta)

    assert "Departure Route Options" in html
    assert "DEPARTURE ROUTE OPTIONS" not in html
    assert "stop-card" not in html


def test_last_destination_departure_route_card_renders_after_restaurants() -> None:
    assembler = HTMLAssembler(config_path="config.yaml")

    dest = {
        "id": "santafe",
        "name": "Santa Fe",
        "dates": "October 20-22, 2026",
        "lat": 35.6870,
        "lng": -105.9378,
        "images": [],
        "planning_links": [],
        "nps_park_code": None,
        "what_to_know": {"summary": "Summary."},
        "cultural_events": {"has_events": False, "events": []},
        "scenic_drives": [],
        "ai_content": {
            "getting_here": {
                "route_summary": "Arrive from Pagosa Springs.",
                "en_route_stops": [],
            },
            "getting_there": {
                "route_summary": "Departure leg toward Albuquerque, NM.",
                "route_options": [
                    {
                        "title": "Sandia Crest Scenic Byway",
                        "distance_or_duration": "16 miles one-way",
                        "description": "Scenic mountain route.",
                    }
                ],
            },
            "top_attractions": [],
            "possible_daily_schedule": [],
            "dinner_recommendations": [
                {
                    "name": "Dinner Spot",
                    "description": "Evening meal.",
                    "url": "https://example.com/dinner-spot",
                }
            ],
        },
    }
    trip_meta = {"return": "Albuquerque, NM"}

    html = assembler._build_single_section(dest, trip_meta, previous_name="Pagosa Springs", is_last=True)

    restaurants_idx = html.find("Dinner Recommendations")
    departure_idx = html.find("Departure Route Options")
    assert restaurants_idx != -1
    assert departure_idx != -1
    assert departure_idx > restaurants_idx


def test_build_schedule_preserves_structured_one_day_schedule() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "possible_daily_schedule": [
            {
                "day_label": "Day 1",
                "periods": [
                    {"period": "Morning", "summary": "Travel from Las Vegas."},
                    {
                        "period": "Afternoon",
                        "summary": "Arrival check-in, meal break, and a short orientation stop near lodging; keep activity light after travel.",
                    },
                    {"period": "Evening", "summary": "Dinner in town and an easy sunset stop."},
                ],
            }
        ],
        "top_attractions": [
            {"name": "Snow Canyon State Park"},
            {"name": "Pioneer Park"},
        ],
        "dinner_recommendations": [{"name": "Cliffside Restaurant"}],
    }

    html = assembler._build_schedule(ai, drives=[], dest_name="St. George, Utah")

    assert "Travel from Las Vegas." in html
    assert "keep activity light after travel." in html
    assert "Start with Snow Canyon State Park; plan for 2–3 hours." not in html


def test_build_restaurants_renders_rating_and_uses_available_description() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_restaurants(
        {
            "dinner_recommendations": [
                {
                    "name": "Thai Basil",
                    "url": "https://example.com/thai-basil",
                    "description": "Cozy Thai spot with curries and noodles.",
                    "cuisine": "Thai",
                    "price_range": "$$",
                    "rating": 4.8,
                    "raw_rating": "4.8/5",
                },
            ]
        },
        "St. George",
    )

    assert "★ 4.8/5" in html
    assert "Cozy Thai spot with curries and noodles." in html


def test_build_restaurants_sanitizes_title_and_uses_tickler_when_description_is_metadata_only() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_restaurants(
        {
            "dinner_recommendations": [
                {
                    "name": "Wild Rabbit Cafe 4.7/5 $",
                    "url": "https://example.com/wild-rabbit-cafe",
                    "description": "4.7/5, $, Cafe",
                    "cuisine": "Cafe",
                    "price_range": "$",
                    "rating": 4.7,
                    "raw_rating": "4.7/5",
                },
            ]
        },
        "St. George",
    )

    assert "Wild Rabbit Cafe" in html
    assert "Wild Rabbit Cafe 4.7/5 $" not in html
    assert "★ 4.7/5" in html
    assert "verify current hours before you go" not in html
    assert "Cafe-style dinner spot" not in html
    assert "4.7/5, $, Cafe" not in html


def test_build_restaurants_strips_rating_price_and_multiword_cuisine_from_title() -> None:
    """Regression for dipstick55 Theme D: the real captured St. George
    restaurant batch harvested titles with rating/price/cuisine glued on
    *after* the real name rather than the simpler "Name - rating $" shape the
    original sanitizer assumed (e.g. "Cliffside Restaurant 4.4/5 $$$
    American", "Painted Pony Restaurant 4.5/5 $$$ Contemporary American",
    "Wood Ash Rye 4.5/5 $$$ New American" -- note the cuisine phrase itself
    can be multiple words that don't match the parsed `cuisine` badge value
    verbatim). All three duplicated their rating/price/cuisine as both title
    text and separate badges."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_restaurants(
        {
            "dinner_recommendations": [
                {
                    "name": "Cliffside Restaurant 4.4/5 $$$ American",
                    "url": "https://www.cliffsiderestaurant.com/",
                    "raw_rating": "4.4/5",
                    "cuisine": "American",
                    "price_range": "$$$",
                },
                {
                    "name": "Painted Pony Restaurant 4.5/5 $$$ Contemporary American",
                    "url": "https://painted-pony.com/",
                    "raw_rating": "4.5/5",
                    "cuisine": "American",
                    "price_range": "$$$",
                },
                {
                    "name": "Wood Ash Rye 4.5/5 $$$ New American",
                    "url": "https://woodashrye.com/",
                    "raw_rating": "4.5/5",
                    "cuisine": "American",
                    "price_range": "$$$",
                },
            ]
        },
        "St. George",
    )

    assert "Cliffside Restaurant 4.4/5" not in html
    assert ">Cliffside Restaurant<" in html
    assert "Painted Pony Restaurant 4.5/5" not in html
    assert "Contemporary American</a>" not in html
    assert ">Painted Pony Restaurant<" in html
    assert "Wood Ash Rye 4.5/5" not in html
    assert "New American</a>" not in html
    assert ">Wood Ash Rye<" in html
    # The rating/price/cuisine still show up exactly once each, as badges.
    assert html.count("★ 4.4/5") == 1
    assert html.count("★ 4.5/5") == 2


def test_build_restaurants_omits_synthetic_default_description_when_description_is_missing() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_restaurants(
        {
            "dinner_recommendations": [
                {
                    "name": "Painted Pony",
                    "url": "https://paintedponyrestaurant.com/",
                    "raw_rating": "4.7/5",
                    "cuisine": "American",
                    "price_range": "$$",
                },
            ]
        },
        "St. George",
    )

    assert "★ 4.7/5" in html
    assert "Painted Pony" in html
    assert "Local dinner spot at Painted Pony in St. George" not in html
    assert "verify current hours before you go" not in html
    assert "rest-desc" not in html


def test_build_restaurants_sanitizes_rating_suffixes_from_names() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_restaurants(
        {
            "dinner_recommendations": [
                {
                    "name": "Cafe Soleil 4.7/5 $$",
                    "url": "https://example.com/cafe-soleil",
                    "description": "Fresh market-driven breakfast and brunch in downtown Springdale.",
                    "cuisine": "Cafe",
                    "price_range": "$$",
                    "rating": 4.7,
                    "raw_rating": "4.7/5",
                },
                {
                    "name": "Whiptail Grill 4.2 stars",
                    "url": "https://example.com/whiptail-grill",
                    "description": "Local comfort food and creative plates.",
                    "cuisine": "American",
                    "price_range": "$$",
                    "rating": 4.2,
                    "raw_rating": "4.2 stars",
                },
            ]
        },
        "Zion National Park",
    )

    assert "Cafe Soleil" in html
    assert "Whiptail Grill" in html
    assert "Cafe Soleil 4.7/5 $$" not in html
    assert "Whiptail Grill 4.2 stars" not in html
    assert "★ 4.7/5" in html
    assert "★ 4.2" in html


def test_build_restaurants_keeps_rating_in_header_and_strips_it_from_teaser() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_restaurants(
        {
            "dinner_recommendations": [
                {
                    "name": "Allred's Restaurant",
                    "url": "https://allredsrestaurant.com/",
                    "description": "4.7/5 $$$$ American",
                    "raw_rating": "4.7/5",
                    "price_range": "$$$",
                    "cuisine": "American",
                },
            ]
        },
        "St. George",
    )

    assert "Allred" in html
    assert "★ 4.7/5" in html
    assert "4.7/5 $$$$" not in html
    assert "4.7/5 $$$$ American" not in html
    assert "American-style dinner spot" not in html
    assert "verify current hours before you go" not in html


def test_build_restaurants_omits_maps_only_restaurant_when_no_direct_link_exists() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_restaurants(
        {
            "dinner_recommendations": [
                {
                    "name": "Fallback Grill",
                    "maps_url": "https://www.google.com/maps/search/?api=1&query=Fallback+Grill+St+George",
                    "description": "Links: https://www.google.com/maps/search/?api=1&query=Fallback+Grill+St+George",
                },
            ]
        },
        "St. George",
    )

    assert "Fallback Grill" not in html
    assert "Dinner Recommendations" in html


def test_build_restaurants_uses_metadata_summary_when_description_is_synthetic() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_restaurants(
        {
            "dinner_recommendations": [
                {
                    "name": "Painted Pony",
                    "url": "https://paintedponyrestaurant.com/",
                    "description": "Source Maps",
                    "cuisine": "American",
                    "price_range": "$$",
                },
            ]
        },
        "St. George",
    )

    assert "Locally surfaced dinner option in St. George." not in html
    assert "American" in html
    assert "$$" in html


def test_build_restaurants_uses_name_tickler_for_wood_fired_pizza() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_restaurants(
        {
            "dinner_recommendations": [
                {
                    "name": "Riggatti's Wood Fired Pizza",
                    "description": "Source",
                },
            ]
        },
        "St. George",
    )

    assert "Wood-fired pizza spot" not in html
    assert "verify current hours before you go" not in html


def test_build_restaurants_uses_cuisine_tickler_for_american_and_cafe() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_restaurants(
        {
            "dinner_recommendations": [
                {"name": "Allred's Restaurant", "description": "Source", "cuisine": "American", "price_range": "$$"},
                {"name": "Wild Rabbit Cafe", "description": "Source", "cuisine": "Cafe", "price_range": "$"},
            ]
        },
        "Telluride",
    )

    assert "American-style dinner spot" not in html
    assert "Cafe-style dinner spot" not in html
    assert "verify current hours before you go" not in html


def test_build_restaurants_rewrites_generic_locally_surfaced_description() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_restaurants(
        {
            "dinner_recommendations": [
                {
                    "name": "Cliffside Restaurant",
                    "description": "Locally surfaced dinner option.",
                },
            ]
        },
        "St. George",
    )

    assert "Locally surfaced dinner option." not in html
    assert "Local dinner spot" not in html
    assert "verify current hours before you go" not in html


def test_build_attractions_renders_distance_and_elevation_badges() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_attractions(
        {
            "top_attractions": [
                {
                    "name": "Emerald Pools Trail",
                    "type": "hike",
                    "difficulty": "Moderate",
                    "distance_miles": 1.5,
                    "elevation_gain_feet": 450,
                    "duration": "1–2 hrs round-trip",
                    "description": "A scenic family-friendly trail with a waterfall and pools.",
                }
            ]
        },
        drives=[],
        dest_name="Zion National Park",
    )

    assert "badge-distance" in html
    assert "1.5 mi" in html
    assert "450 ft" in html
    assert "badge-elevation" in html


def test_build_attractions_ignores_llm_must_see_flag_without_rating_data() -> None:
    """The LLM's must_see flag is unverified opinion and must never drive the
    badge on its own -- without corroborating rating/vote data (attached during
    URL discovery), the badge must not render even if the model said true."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_attractions(
        {
            "top_attractions": [
                {
                    "name": "Angels Landing",
                    "must_see": True,
                    "description": "A strenuous chain-assisted climb to a narrow summit ridge.",
                }
            ]
        },
        drives=[],
        dest_name="Zion National Park",
    )

    assert "badge-mustsee" not in html


def test_build_attractions_awards_must_see_badge_from_verified_rating() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_attractions(
        {
            "top_attractions": [
                {
                    "name": "Angels Landing",
                    "must_see": False,
                    "rating": 4.8,
                    "votes": 4200,
                    "description": "A strenuous chain-assisted climb to a narrow summit ridge.",
                }
            ]
        },
        drives=[],
        dest_name="Zion National Park",
    )

    assert '<span class="badge badge-mustsee">Must-See</span>' in html
    assert '<span class="badge badge-rating">★ 4.8</span>' in html


def test_build_attractions_caps_must_see_badges_at_two_per_destination() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    attractions = [
        {
            "name": "Angels Landing",
            "rating": 4.9,
            "votes": 5000,
            "url": "https://www.nps.gov/zion/angels-landing.htm",
            "description": "A strenuous chain-assisted climb to a narrow summit ridge.",
        },
        {
            "name": "The Narrows",
            "rating": 4.8,
            "votes": 4500,
            "url": "https://www.nps.gov/zion/the-narrows.htm",
            "description": "A slot-canyon river hike through the Virgin River gorge.",
        },
        {
            "name": "Emerald Pools",
            "rating": 4.6,
            "votes": 3000,
            "url": "https://www.nps.gov/zion/emerald-pools.htm",
            "description": "A family-friendly trail to a series of waterfall-fed pools.",
        },
    ]
    html = assembler._build_attractions(
        {"top_attractions": attractions}, drives=[], dest_name="Zion National Park"
    )

    assert html.count("badge-mustsee") == 2
    assert "Angels Landing" in html and "The Narrows" in html
    # Lowest-rated of the three qualifying items is bumped by the top-2 cap.
    rows = html.split('<div class="attr-item">')
    emerald_row = next(row for row in rows if "Emerald Pools" in row)
    assert "badge-mustsee" not in emerald_row


def test_build_attractions_renders_seed_badge_for_user_requested_anchor() -> None:
    """Seed attractions (docs/requirements.md §3.4, user-requested anchors from
    trip_manifest.yaml's seeds: list) get a distinct "Your Pick" badge, separate
    from and independent of the verified-quality Must-See badge."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_attractions(
        {
            "top_attractions": [
                {
                    "name": "Angels Landing",
                    "is_seed": True,
                    "description": "A strenuous chain-assisted climb to a narrow summit ridge.",
                },
                {
                    "name": "Kolob Canyons Viewpoint",
                    "description": "A pull-off overlook of the red-rock Kolob Canyons formations.",
                },
            ]
        },
        drives=[],
        dest_name="Zion National Park",
    )

    rows = html.split('<div class="attr-item">')
    angels_row = next(row for row in rows if "Angels Landing" in row)
    kolob_row = next(row for row in rows if "Kolob Canyons" in row)
    assert '<span class="badge badge-seed">Your Pick</span>' in angels_row
    assert "badge-seed" not in kolob_row


def test_build_attractions_renders_caution_badge_when_promoted_without_url() -> None:
    """An attraction with no URL can still be promoted (_should_render_without_url)
    when it carries enough metadata/description to be useful, but must be
    visually flagged as unverified rather than rendered indistinguishably from
    a source-linked entry."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_attractions(
        {
            "top_attractions": [
                {
                    "name": "Quiet Overlook",
                    "difficulty": "Easy",
                    "duration": "30 min",
                    "description": "A short pull-off with sweeping canyon views.",
                }
            ]
        },
        drives=[],
        dest_name="Zion National Park",
    )

    assert '<span class="badge badge-caution" title="No verified source link found for this recommendation">⚠ Unverified</span>' in html


def test_build_attractions_omits_caution_badge_when_url_present() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_attractions(
        {
            "top_attractions": [
                {
                    "name": "Angels Landing",
                    "url": "https://www.nps.gov/zion/angels-landing.htm",
                    "description": "A strenuous chain-assisted climb to a narrow summit ridge.",
                }
            ]
        },
        drives=[],
        dest_name="Zion National Park",
    )

    assert "badge-caution" not in html


def test_build_attractions_seed_and_must_see_badges_are_independent() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_attractions(
        {
            "top_attractions": [
                {
                    "name": "Angels Landing",
                    "is_seed": True,
                    "rating": 4.9,
                    "votes": 5000,
                    "description": "A strenuous chain-assisted climb to a narrow summit ridge.",
                }
            ]
        },
        drives=[],
        dest_name="Zion National Park",
    )

    assert '<span class="badge badge-seed">Your Pick</span>' in html
    assert '<span class="badge badge-mustsee">Must-See</span>' in html
    # Seed badge renders before Must-See in the badge row.
    assert html.index("badge-seed") < html.index("badge-mustsee")


def test_build_attractions_omits_na_duration_badge() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_attractions(
        {
            "top_attractions": [
                {
                    "name": "Snow Canyon State Park",
                    "duration": "N/A",
                    "description": "Desert canyon views.",
                }
            ]
        },
        drives=[],
        dest_name="St. George",
    )

    assert "badge-duration" not in html


def test_build_schedule_does_not_synthesize_when_schedule_missing() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "possible_daily_schedule": [],
        "top_attractions": [{"name": "Snow Canyon State Park"}],
        "dinner_recommendations": [{"name": "Cliffside Restaurant"}],
    }

    html = assembler._build_schedule(ai, drives=[], dest_name="St. George, Utah")

    assert html == ""


def test_build_schedule_does_not_rewrite_legacy_string_list() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "possible_daily_schedule": [
            "7:00 AM - Travel from Las Vegas.",
            "2:00 PM - Check in and light orientation stop.",
        ],
        "top_attractions": [{"name": "Snow Canyon State Park"}],
        "dinner_recommendations": [{"name": "Cliffside Restaurant"}],
    }

    html = assembler._build_schedule(ai, drives=[], dest_name="St. George, Utah")

    assert html == ""


def test_intro_note_omits_weather_and_photography_rows() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    dest = {
        "name": "Zion National Park",
        "what_to_know": {
            "summary": "A summary.",
            "local_customs": "Customs.",
            "best_times_of_day": "Morning.",
            "typical_weather_patterns": "Cool nights.",
            "transportation_quirks": "Shuttle required.",
            "safety_considerations": "Carry water.",
            "photography_tips": "Bring a polarizer.",
            "crowd_patterns": "Busy weekends.",
            "local_etiquette": "Stay on trail.",
        },
    }
    html = assembler._build_intro_note(dest, events={})

    assert "Local customs:" in html
    assert "Best times of day:" in html
    assert "Transportation quirks:" in html
    assert "Safety:" in html
    assert "Crowd patterns:" in html
    assert "Local etiquette:" in html
    assert "Typical weather:" not in html
    assert "Photography:" not in html


def test_intro_note_does_not_repeat_fallback_cultural_events_copy() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    dest = {
        "name": "St. George",
        "what_to_know": {
            "summary": "Desert gateway with easy access to red-rock parks.",
            "local_customs": "Expect a relaxed pace in town.",
            "best_times_of_day": "Morning and dusk.",
            "transportation_quirks": "Parking is easiest outside midday.",
            "safety_considerations": "Carry water.",
            "crowd_patterns": "Busy on weekends.",
            "local_etiquette": "Respect trail signage.",
        },
    }
    events = {
        "has_events": False,
        "honest_assessment": "St. George has a lively cultural scene in October, characterized by community gatherings and outdoor activities.",
        "local_tip": "Check the local farmers market on Saturday mornings.",
    }

    html = assembler._build_intro_note(dest, events)

    assert "Desert gateway with easy access to red-rock parks." in html
    assert "St. George has a lively cultural scene in October" not in html
    assert "Local tip:" not in html


def test_image_caption_drops_wikimedia_template_boilerplate() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    image = {
        "credit": "<table><tr><td>When reusing, please credit me as ... external text href='https://commons.wikimedia.org/wiki/File:foo'</td></tr></table>",
        "source": "wikimedia",
        "title": "Bryce Canyon in Winter",
    }

    caption = assembler._build_image_caption(image)

    assert "Wikimedia" in caption
    assert "Bryce Canyon in Winter" in caption
    assert "<table" not in caption
    assert "external text" not in caption.lower()


def test_header_links_omit_invalid_urls() -> None:
    assembler = HTMLAssembler(config_path="config.yaml")
    dest = {"lat": 37.2982, "lng": -113.0263}
    links = [
        {"label": "Valid Plan", "url": "example.com/plan"},
        {"label": "Bad Link", "url": "javascript:alert(1)"},
        {"label": "Bad Link 2", "url": "data:text/html;base64,AAAA"},
    ]

    html = assembler._build_header_links(links, nps_code=None, dest=dest, attractions=[])

    assert "Valid Plan" in html
    assert "Bad Link" not in html
    assert "Bad Link 2" not in html
    assert 'target="_blank" rel="noopener"' not in html


def test_build_attractions_drops_scenic_drive_duplicating_a_rendered_attraction() -> None:
    """Full-pipeline regression for a real reported bug: 'Inspiration Point'
    rendered twice for the same destination -- once as a top_attraction (with
    real content) and once as a redundant scenic-drive card. The drive card
    must be dropped once an attraction with the same name already rendered."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Inspiration Point",
                "type": "attraction",
                "description": "Panoramic views of the amphitheater.",
                "url": "https://www.nps.gov/brca/planyourvisit/inspiration-point.htm",
            }
        ]
    }
    drives = [
        {
            "title": "Inspiration Point",
            "category": "viewpoint",
            "description": "A short drive to a panoramic overlook.",
            "distance_or_duration": "10-min drive from park entrance",
        }
    ]

    html = assembler._build_attractions(ai, drives=drives, dest_name="Bryce Canyon National Park")

    assert html.count("Inspiration Point") == 1
    assert "attr-drive-item" not in html
    assert "nps.gov/brca/planyourvisit/inspiration-point.htm" in html


def test_drive_descriptions_omit_drive_duplicating_a_rendered_attraction() -> None:
    """Regression for a real validation failure (dipstick54, 2026-08-15):
    'Potash Road' existed as both a top_attraction and a scenic_drive for
    Moab. _build_attractions correctly drops the redundant drive card (see
    test_build_attractions_drops_scenic_drive_duplicating_a_rendered_attraction),
    but _build_drive_descriptions built the JS DRIVE_DESCRIPTIONS object
    independently and still emitted an entry for it -- an orphan key with no
    modal-trigger button to open it, caught by HTMLValidator's
    'DRIVE_DESCRIPTIONS keys with no modal button' check."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = [
        {
            "name": "Moab",
            "ai_content": {
                "top_attractions": [
                    {
                        "name": "Potash Road",
                        "type": "scenic",
                        "description": "A dirt road along the Colorado River.",
                        "url": "https://www.moab-utah.com/potash-road.html",
                    }
                ]
            },
            "scenic_drives": [
                {
                    "title": "Potash Road",
                    "category": "drive",
                    "description": "A scenic dirt road with river views and petroglyphs.",
                    "distance_or_duration": "1-2 hrs",
                }
            ],
        }
    ]

    payload = assembler._build_drive_descriptions(destinations)

    assert "Potash Road" not in payload


def test_build_attractions_keeps_distinctly_named_scenic_drive() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Inspiration Point",
                "type": "attraction",
                "description": "Panoramic views of the amphitheater.",
                "url": "https://www.nps.gov/brca/planyourvisit/inspiration-point.htm",
            }
        ]
    }
    drives = [
        {
            "title": "Bryce Canyon Scenic Drive",
            "category": "drive",
            "description": "The 18-mile scenic drive.",
            "distance_or_duration": "2-3 hrs",
        }
    ]

    html = assembler._build_attractions(ai, drives=drives, dest_name="Bryce Canyon National Park")

    assert "Inspiration Point" in html
    assert "Bryce Canyon Scenic Drive" in html
    assert "attr-drive-item" in html


def test_build_attractions_omits_items_without_a_usable_url() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Queens Garden Trail",
                "type": "hike",
                "description": "Popular canyon trail.",
                "url": "",
            }
        ]
    }

    html = assembler._build_attractions(ai, drives=[], dest_name="Bryce Canyon National Park")

    assert html == ""
    assert "Queens Garden Trail" not in html
    assert "attr-link" not in html


def test_build_attractions_omits_items_when_only_maps_search_fallback_exists() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Weeping Rock",
                "type": "hike",
                "description": "Short trail to a shaded alcove.",
                "url": "",
                "maps_url": "https://www.google.com/maps/search/?api=1&query=Weeping+Rock+Zion+National+Park",
            }
        ]
    }

    html = assembler._build_attractions(ai, drives=[], dest_name="Zion National Park")

    assert html == ""
    assert "Weeping Rock" not in html
    assert "google.com/maps/search" not in html
    assert "<a href=" not in html


def test_build_attractions_omits_items_without_a_url_even_when_the_name_is_valid() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Historic Downtown St. George",
                "type": "attraction",
                "description": "Historic district walk.",
                "url": "",
            }
        ]
    }

    html = assembler._build_attractions(ai, drives=[], dest_name="Zion National Park")

    assert "Historic Downtown St. George" not in html
    assert "Top Attractions" not in html
    assert "<a href=" not in html


def test_build_getting_here_omits_route_stop_when_no_usable_url_exists() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive to Santa Fe.",
            "distance_miles": "60",
            "drive_time": "1h 15m",
            "en_route_stops": [
                {
                    "name": "El Rito",
                    "description": "Historic stop option.",
                    "url": "",
                }
            ],
        }
    }
    dest = {"name": "Santa Fe"}

    html = assembler._build_getting_here(ai, dest, previous_name="Albuquerque")

    assert "El Rito" not in html
    assert ">El Rito</a>" not in html
    assert "Open in Google Maps" in html
    assert "maps/dir/?" in html
    assert "destination=Santa%20Fe" in html
    assert "api=1" in html
    assert "origin=Albuquerque" in html


def test_build_getting_here_renders_en_route_stop_maps_search_fallback_link() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive to Santa Fe.",
            "distance_miles": "60",
            "drive_time": "1h 15m",
            "en_route_stops": [
                {
                    "name": "Snow Canyon",
                    "description": "Worth a stop near St. George.",
                    "url": "",
                    "maps_url": "https://www.google.com/maps/search/?api=1&query=Snow+Canyon+St+George",
                }
            ],
        }
    }
    dest = {"name": "St. George"}

    html = assembler._build_getting_here(ai, dest, previous_name="Las Vegas")

    assert "google.com/maps/search/?api=1&amp;query=Snow+Canyon+St+George" in html
    assert ">Snow Canyon</a>" in html
    assert "Snow Canyon" in html


def test_build_getting_here_renders_caution_badge_for_en_route_stop_promoted_without_any_url() -> None:
    """An en-route stop with no direct URL and no maps fallback can still be
    promoted on description strength alone (_should_render_without_url), but
    must carry the same unverified-caution flag as attractions/restaurants."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive to Santa Fe.",
            "distance_miles": "60",
            "drive_time": "1h 15m",
            "en_route_stops": [
                {
                    "name": "Adobe Plaza",
                    "description": "A quiet detour through an old adobe plaza with local artisan shops.",
                }
            ],
        }
    }
    dest = {"name": "Santa Fe"}

    html = assembler._build_getting_here(ai, dest, previous_name="Albuquerque")

    assert "Adobe Plaza" in html
    assert '<span class="badge badge-caution" title="No verified source link found for this recommendation">⚠ Unverified</span>' in html


def test_build_getting_here_falls_back_to_maps_url_when_canonical_missing() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive to Santa Fe.",
            "distance_miles": "60",
            "drive_time": "1h 15m",
            "en_route_stops": [
                {
                    "name": "El Rito",
                    "description": "Historic stop option.",
                    "url": "",
                    "maps_url": "https://www.google.com/maps/search/?api=1&query=El+Rito+near+Santa+Fe+route+from+Albuquerque",
                }
            ],
        }
    }
    dest = {"name": "Santa Fe"}

    html = assembler._build_getting_here(ai, dest, previous_name="Albuquerque")

    assert "google.com/maps/search/?api=1&amp;query=El%20Rito" in html
    assert "route+from+Albuquerque" not in html
    assert ">El Rito</a>" in html
    assert "El Rito" in html


def test_build_getting_here_maps_fallback_does_not_append_map_suffix() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive to Santa Fe.",
            "distance_miles": "60",
            "drive_time": "1h 15m",
            "en_route_stops": [
                {
                    "name": "El Rito",
                    "description": "Historic stop option.",
                    "url": "",
                    "maps_url": "https://www.google.com/maps/search/?api=1&query=El+Rito+near+Santa+Fe+route+from+Albuquerque",
                }
            ],
        }
    }
    dest = {"name": "Santa Fe"}

    html = assembler._build_getting_here(ai, dest, previous_name="Albuquerque")

    assert "El Rito (map)" not in html
    assert ">El Rito</a>" in html


def test_build_getting_here_renders_detour_and_practical_note() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive with one worthwhile stop.",
            "distance_miles": "120",
            "drive_time": "2h 30m",
            "en_route_stops": [
                {
                    "name": "Wilson Arch",
                    "description": "Short roadside stop.",
                    "practical_note": "Pullout is on the right when driving north.",
                    "detour_distance_miles": 3,
                    "detour_time_minutes": 8,
                    "maps_url": "https://www.google.com/maps/search/?api=1&query=Wilson+Arch+Moab+UT",
                }
            ],
        }
    }
    dest = {"name": "Moab"}

    html = assembler._build_getting_here(ai, dest, previous_name="Capitol Reef National Park")

    assert "3 mi detour" in html
    assert "8 min" in html
    assert "Pullout is on the right when driving north." in html


def test_build_getting_here_falls_back_to_maps_url_when_canonical_missing() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive to Santa Fe.",
            "distance_miles": "60",
            "drive_time": "1h 15m",
            "en_route_stops": [
                {
                    "name": "El Rito",
                    "description": "Historic stop option.",
                    "url": "",
                    "maps_url": "https://www.google.com/maps/search/?api=1&query=El+Rito+near+Santa+Fe+route+from+Albuquerque",
                }
            ],
        }
    }
    dest = {"name": "Santa Fe"}

    html = assembler._build_getting_here(ai, dest, previous_name="Albuquerque")

    assert "google.com/maps/search/?api=1&amp;query=El%20Rito" in html
    assert "route+from+Albuquerque" not in html
    assert ">El Rito</a>" in html
    assert "El Rito" in html


def test_build_getting_here_renders_detour_and_practical_note() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive with one worthwhile stop.",
            "distance_miles": "120",
            "drive_time": "2h 30m",
            "en_route_stops": [
                {
                    "name": "Wilson Arch",
                    "description": "Short roadside stop.",
                    "practical_note": "Pullout is on the right when driving north.",
                    "detour_distance_miles": 3,
                    "detour_time_minutes": 8,
                    "maps_url": "https://www.google.com/maps/search/?api=1&query=Wilson+Arch+Moab+UT",
                }
            ],
        }
    }
    dest = {"name": "Moab"}

    html = assembler._build_getting_here(ai, dest, previous_name="Capitol Reef National Park")

    assert "3 mi detour" in html
    assert "8 min" in html
    assert "Pullout is on the right when driving north." in html


def test_build_getting_here_extracts_rating_from_stop_description_into_badge() -> None:
    """En-route stops must get the same rating->badge treatment attractions and
    restaurants get instead of leaving "4.5 stars (230 reviews)" baked into the
    rendered description prose (Dipstick48 finding)."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive with one worthwhile stop.",
            "distance_miles": "120",
            "drive_time": "2h 30m",
            "en_route_stops": [
                {
                    "name": "Wilson Arch",
                    "description": "Rated 4.5 stars (230 reviews), a short roadside stop with great views.",
                    "maps_url": "https://www.google.com/maps/search/?api=1&query=Wilson+Arch+Moab+UT",
                }
            ],
        }
    }
    dest = {"name": "Moab"}

    html = assembler._build_getting_here(ai, dest, previous_name="Capitol Reef National Park")

    assert '<span class="badge badge-rating">★ 4.5 (230 reviews)</span>' in html
    assert "stars" not in html.lower()
    assert "230 reviews)," not in html
    assert "short roadside stop with great views" in html


def test_build_getting_here_prefers_structured_rating_field_over_text_extraction() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive with one worthwhile stop.",
            "distance_miles": "120",
            "drive_time": "2h 30m",
            "en_route_stops": [
                {
                    "name": "Wilson Arch",
                    "description": "A short roadside stop with great views.",
                    "rating": 4.6,
                    "raw_rating": "4.6/5",
                    "maps_url": "https://www.google.com/maps/search/?api=1&query=Wilson+Arch+Moab+UT",
                }
            ],
        }
    }
    dest = {"name": "Moab"}

    html = assembler._build_getting_here(ai, dest, previous_name="Capitol Reef National Park")

    assert '<span class="badge badge-rating">★ 4.6/5</span>' in html


def test_destination_attractions_map_url_uses_local_search_not_route() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    url = assembler._build_destination_attractions_map_url(
        "St. George, Utah",
        [
            {"name": "Pioneer Park"},
            {"name": "St. George Dinosaur Discovery Site"},
            {"name": "Snow Canyon State Park"},
        ],
    )

    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "St.%20George%2C%20Utah" in url
    assert "maps/dir/" not in url
    assert "waypoints=" not in url


def test_destination_attractions_map_url_single_item_remains_focused_search() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    url = assembler._build_destination_attractions_map_url(
        "St. George, Utah",
        [{"name": "Pioneer Park"}],
    )

    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "Pioneer%20Park%20St.%20George%2C%20Utah" in url


def test_build_restaurants_prefers_discovered_url_over_maps_query() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "dinner_recommendations": [
            {
                "name": "Tandoor Indian Cuisine",
                "url": "https://www.tripadvisor.com/Restaurant_Review-g57119-d1234567-Reviews-Tandoor_Indian_Cuisine-St_George_Utah.html",
                "maps_url": "https://www.google.com/maps/search/?api=1&query=Tandoor%20Indian%20Cuisine%20St.%20George%2C%20Utah",
                "cuisine": "Indian",
                "description": "Popular local Indian spot.",
            }
        ]
    }

    html = assembler._build_restaurants(ai, dest_name="St. George, Utah")

    assert "tripadvisor.com" in html
    assert "google.com/maps/search/?api=1&amp;query=Tandoor" not in html


def test_build_restaurants_omits_items_with_no_usable_url_even_when_maps_fallback_exists() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "dinner_recommendations": [
            {
                "name": "Tandoor Indian Cuisine",
                "url": "",
                "maps_url": "https://www.google.com/maps/search/?api=1&query=Tandoor%20Indian%20Cuisine%20St.%20George%2C%20Utah",
                "cuisine": "Indian",
                "description": "Popular local Indian spot.",
            }
        ]
    }

    html = assembler._build_restaurants(ai, dest_name="St. George, Utah")

    assert "Tandoor Indian Cuisine" not in html
    assert "Dinner Recommendations" in html
    assert "google.com/maps/search/?api=1" not in html
    assert "google.com/search?q=Tandoor%20Indian%20Cuisine" not in html


def test_build_restaurants_avoids_rendering_maps_directions_url() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "dinner_recommendations": [
            {
                "name": "Bit & Spur Restaurant & Saloon",
                "url": "https://www.google.com/maps/dir//Bit+%26+Spur+Restaurant+%26+Saloon,+1212+Zion+Park+Blvd,+Springdale,+UT+84767",
                "maps_url": "https://www.google.com/maps/search/?api=1&query=Bit%20%26%20Spur%20Restaurant%20%26%20Saloon%20Springdale",
                "cuisine": "Southwestern",
                "description": "Popular Springdale dinner spot.",
            }
        ]
    }

    html = assembler._build_restaurants(ai, dest_name="Zion National Park")

    assert "Bit &amp; Spur Restaurant &amp; Saloon" in html
    assert "google.com/maps/dir/" not in html


def test_build_restaurants_keeps_selected_maps_search_url() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "dinner_recommendations": [
            {
                "name": "Painted Pony",
                "url": "https://www.google.com/maps/search/?api=1&query=Painted+Pony+St+George+UT",
                "cuisine": "American",
                "description": "Downtown restaurant.",
            }
        ]
    }

    html = assembler._build_restaurants(ai, dest_name="St. George, Utah")

    assert "Painted Pony" in html
    assert "google.com/maps/search" in html
    assert "google.com/search?q=Painted%20Pony" not in html


def test_select_preferred_external_link_rewrites_route_context_maps_search_for_en_route_stop() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)

    url, is_map = assembler._select_preferred_external_link(
        {
            "name": "Canyon Overlook Trailhead",
            "maps_url": "https://www.google.com/maps/search/?api=1&query=Canyon%20Overlook%20Trailhead%20near%20Bryce%20Canyon%20National%20Park%20route%20from%20Zion%20National%20Park",
        },
        section="en_route_stop",
    )

    assert is_map is True
    assert "route%20from" not in url.lower()
    assert url.startswith("https://www.google.com/maps/search/?api=1&query=Canyon%20Overlook%20Trailhead")


def test_build_attractions_preserves_maps_search_url_as_link() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Snow Canyon State Park",
                "type": "attraction",
                "description": "Red rock canyon and overlooks.",
                "url": "https://www.google.com/maps/search/?api=1&query=Snow+Canyon+State+Park+Utah",
            }
        ]
    }

    html = assembler._build_attractions(ai, drives=[], dest_name="St. George, Utah")

    assert "Snow Canyon State Park" in html
    assert "google.com/maps/search/?api=1&amp;query=Snow+Canyon+State+Park+Utah" in html
    assert 'target="_blank"' in html


def test_build_getting_here_renders_discovered_maps_search_url_as_primary() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive with one worthwhile stop.",
            "distance_miles": "120",
            "drive_time": "2h 30m",
            "en_route_stops": [
                {
                    "name": "Wilson Arch",
                    "description": "Short roadside stop.",
                    "url": "https://www.google.com/maps/search/?api=1&query=Wilson+Arch+Moab+UT",
                }
            ],
        }
    }
    dest = {"name": "Moab"}

    html = assembler._build_getting_here(ai, dest, previous_name="Capitol Reef National Park")

    assert "Wilson Arch" in html
    assert "google.com/maps/search/?api=1&amp;query=Wilson+Arch+Moab+UT" in html
    assert ">Wilson Arch</a>" in html


def test_build_getting_here_renders_en_route_stop_maps_fallback_link() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive with one questionable stop.",
            "distance_miles": "120",
            "drive_time": "2h 30m",
            "en_route_stops": [
                {
                    "name": "Harrisburg Homestead Ruins",
                    "description": "Historic ruins near the highway.",
                    "maps_url": "https://www.google.com/maps/search/?api=1&query=Harrisburg+Homestead+Ruins+St.+George%2C+Utah",
                }
            ],
        }
    }
    dest = {"name": "St. George"}

    html = assembler._build_getting_here(ai, dest, previous_name="Zion National Park")

    assert "Harrisburg Homestead Ruins" in html
    assert "google.com/maps/search/?api=1&amp;query=Harrisburg+Homestead+Ruins+St.+George%2C+Utah" in html
    assert ">Harrisburg Homestead Ruins</a>" in html


def test_build_getting_here_rewrites_en_route_directions_link_to_stop_location() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive with one route-linked stop.",
            "distance_miles": "120",
            "drive_time": "2h 30m",
            "en_route_stops": [
                {
                    "name": "Red Cliffs Desert Reserve",
                    "maps_url": "https://www.google.com/maps/dir/?api=1&origin=Las+Vegas&destination=Red+Cliffs+Desert+Reserve+St+George",
                }
            ],
        }
    }
    dest = {"name": "St. George, Utah"}

    html = assembler._build_getting_here(ai, dest, previous_name="Las Vegas")

    assert "Red Cliffs Desert Reserve" in html
    assert "https://www.google.com/maps/dir/?api=1&amp;origin=Las+Vegas" not in html
    assert "https://www.google.com/maps/search/?api=1&amp;query=Red%20Cliffs%20Desert%20Reserve" in html


def test_build_getting_here_does_not_duplicate_stop_note_when_same_as_description() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive with one worthwhile stop.",
            "distance_miles": "120",
            "drive_time": "2h 30m",
            "en_route_stops": [
                {
                    "name": "Silver Reef Museum",
                    "description": "Preserved 1870s silver mining town with original buildings and exhibits",
                    "practical_note": "Preserved 1870s silver mining town with original buildings and exhibits",
                }
            ],
        }
    }
    dest = {"name": "St. George"}

    html = assembler._build_getting_here(ai, dest, previous_name="Zion National Park")

    assert html.count("Preserved 1870s silver mining town with original buildings and exhibits") == 1