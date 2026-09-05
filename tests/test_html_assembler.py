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


def test_scenic_drive_card_excludes_high_clearance_drives_when_manifest_declares_no_vehicle() -> None:
    """End-to-end regression for the manifest-driven vehicle-clearance filter:
    AIContentGenerator._filter_drives_requiring_high_clearance_vehicle runs
    during normalize_trip_content (before html assembly), so a 4WD/high-clearance
    drive dropped there never reaches _build_attractions's rendered HTML, while
    an 'Any vehicle' drive in the same destination survives untouched."""
    from generator.ai_content import AIContentGenerator

    gen = AIContentGenerator.__new__(AIContentGenerator)
    trip = {
        "trip": {"has_high_clearance_vehicle": False},
        "destinations": [
            {
                "name": "Canyonlands National Park",
                "scenic_drives": [
                    {
                        "title": "Paved Overlook Road",
                        "category": "drive",
                        "distance_or_duration": "20 mi",
                        "description": "Easy paved drive to the main overlooks.",
                        "vehicle_requirement": "Any vehicle",
                    },
                    {
                        "title": "White Rim Road",
                        "category": "drive",
                        "distance_or_duration": "100 mi",
                        "description": "Remote backcountry route around the rim.",
                        "vehicle_requirement": "4WD required",
                    },
                ],
            }
        ],
    }

    gen._filter_drives_requiring_high_clearance_vehicle(trip)

    dest = trip["destinations"][0]
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_attractions(
        {"top_attractions": []}, drives=dest["scenic_drives"], dest_name=dest["name"]
    )

    assert "Paved Overlook Road" in html
    assert "White Rim Road" not in html


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
    assert "Itinerary Generator" in html
    assert "v9.9.9" in html
    assert "Itinerary output: 2026-07-26 17:41 UTC" in html


def test_generator_footer_includes_manifest_name_when_present() -> None:
    assembler = HTMLAssembler(config_path="config.yaml")
    trip = {
        "trip": {"title": "Test Trip", "theme_color": "#C0623E"},
        "_meta": {
            "generator_version": "9.9.9",
            "template_version": "2.5",
            "generated_at_utc": "2026-07-26T17:41:23+00:00",
            "manifest_name": "sw_manifest.yaml",
            "llm": {"provider": "openai", "model": "test", "usage": {"models": [], "total_estimated_cost_usd": 0.0}},
        },
        "destinations": [],
    }

    html = assembler.assemble(trip)

    assert "Manifest: sw_manifest.yaml" in html


def test_generator_footer_omits_manifest_segment_when_absent() -> None:
    """Existing (pre-manifest-name) trips/tests must render unchanged --
    no stray 'Manifest:  ·' when the field is missing."""
    assembler = HTMLAssembler(config_path="config.yaml")
    trip = {
        "trip": {"title": "Test Trip", "theme_color": "#C0623E"},
        "_meta": {
            "generator_version": "9.9.9",
            "template_version": "2.5",
            "generated_at_utc": "2026-07-26T17:41:23+00:00",
            "llm": {"provider": "openai", "model": "test", "usage": {"models": [], "total_estimated_cost_usd": 0.0}},
        },
        "destinations": [],
    }

    html = assembler.assemble(trip)

    assert "Manifest:" not in html


def _minimal_trip_with_meta(privacy_redacted: bool | None) -> dict:
    meta = {
        "generator_version": "9.9.9",
        "template_version": "2.5",
        "generated_at_utc": "2026-07-26T17:41:23+00:00",
        "llm": {"provider": "openai", "model": "test", "usage": {"models": [], "total_estimated_cost_usd": 0.0}},
    }
    if privacy_redacted is not None:
        meta["privacy_redacted"] = privacy_redacted
    return {
        "trip": {"title": "Test Trip", "theme_color": "#C0623E"},
        "_meta": meta,
        "destinations": [],
    }


def test_assemble_disables_pwa_install_when_privacy_redacted() -> None:
    """main._resolve_privacy_redaction's outcome (trip["_meta"]["privacy_redacted"])
    must suppress the PWA Install App affordance -- see requirements.md's
    privacy redaction policy. Guards the frozen-template PWA_INSTALL_ENABLED
    toggle (templates/v2.5_template.html) via html_assembler's substitution."""
    assembler = HTMLAssembler(config_path="config.yaml")

    redacted_html = assembler.assemble(_minimal_trip_with_meta(True))
    assert "var PWA_INSTALL_ENABLED = false;" in redacted_html
    assert "var PWA_INSTALL_ENABLED = true;" not in redacted_html

    not_redacted_html = assembler.assemble(_minimal_trip_with_meta(False))
    assert "var PWA_INSTALL_ENABLED = true;" in not_redacted_html

    default_html = assembler.assemble(_minimal_trip_with_meta(None))
    assert "var PWA_INSTALL_ENABLED = true;" in default_html


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
    # A broken image still hides itself, but via one delegated listener rather
    # than an inline onerror attribute per image. Mail scanners flag a dozen
    # `onerror=` attributes beside remote <script src> loads as
    # Trojan:HTML/Phish, which made the itinerary unattachable to an email.
    assert 'onerror=' not in html
    assert 'class="hide-on-error"' in html
    assert "addEventListener('error'" in html
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


def test_build_map_markers_excludes_grouped_day_trip_children() -> None:
    """Project owner: "I don't want the overview map to contain Daytrips,
    as that doesn't render well" -- a grouped day-trip child shares its
    base's own lodging coordinates, so it rendered as a marker sitting on
    top of (or a few pixels from) its base's own marker. Mirrors the same
    exclusion _build_nav_tabs already applies for the nav menu, including
    renumbering the surviving markers sequentially rather than skipping
    the excluded ones' indices."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = [
        {"id": "moab", "name": "Moab", "dates": "August 1-4, 2026", "lat": 38.57, "lng": -109.55},
        {
            "id": "arches",
            "name": "Arches National Park",
            "dates": "August 2, 2026",
            "lat": 38.73,
            "lng": -109.59,
            "group_with": "moab",
        },
        {
            "id": "canyonlands",
            "name": "Canyonlands National Park",
            "dates": "August 3, 2026",
            "lat": 38.46,
            "lng": -109.82,
            "group_with": "moab",
        },
        {"id": "telluride", "name": "Telluride", "dates": "August 5-6, 2026", "lat": 37.94, "lng": -107.81},
    ]

    markers = assembler._build_map_markers(destinations, {})
    dest_markers = [m for m in markers if "idx" in m]

    assert [m["name"] for m in dest_markers] == ["Moab", "Telluride"]
    assert [m["idx"] for m in dest_markers] == [1, 2]
    assert [m["stop_index"] for m in dest_markers] == [1, 2]


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
            # is_seed=True: this stop carries no url and no description, so
            # under the verified-link-or-seed policy (2026-08-17) only a seed
            # renders without a url -- unrelated to what this test actually
            # verifies (waypoint URL construction).
            "en_route_stops": [{"name": "Scenic Overlook", "is_seed": True}],
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
    # "Scenic Overlook" with no geocode is exactly the ambiguous case now left
    # off the route line rather than guessed at -- a bare name of that
    # generality resolves anywhere. This test's subject is the origin and
    # destination asserted above; this records the safer waypoint contract.
    assert "waypoints=Scenic%20Overlook" not in html
    assert 'class="gmaps-link"' in html
    assert 'target="_blank"' in html


def test_build_getting_here_route_waypoints_are_destination_scoped_for_ambiguous_names() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Arrive via scenic corridor.",
            # is_seed=True on both: no url/description on these bare-name
            # fixtures, so seeding keeps them visible under the
            # verified-link-or-seed policy (2026-08-17) for this test's real
            # focus (destination-scoped waypoint qualification).
            "en_route_stops": [
                {"name": "Red Cliffs Desert Reserve", "is_seed": True},
                {"name": "Leeds Historic District", "is_seed": True},
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


def test_build_getting_here_route_waypoint_prefers_geocoded_coordinates() -> None:
    """dipstick60: "Canyon Overlook Trail" (a real Zion-area trail) rendered
    as a fully UNQUALIFIED waypoint on the Zion->Bryce leg, because it
    coincidentally shares the word "canyon" with arrival destination "Bryce
    Canyon National Park" -- the qualification heuristic wrongly concluded
    no qualifier was needed. In Google Maps, the bare name resolved to an
    unrelated "Stan's Overlook Trail, Snoqualmie, WA". A stop with a real
    verified geocode must use coordinates instead, bypassing the
    name-qualification guesswork (and its failure modes) entirely."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive to Bryce.",
            "en_route_stops": [
                {
                    "name": "Canyon Overlook Trail",
                    "geocode_lat": 37.2136,
                    "geocode_lng": -113.0064,
                    # is_seed=True: no url/description on this bare fixture;
                    # seeding keeps it visible under the verified-link-or-seed
                    # policy (2026-08-17) for this test's real focus (geocode
                    # preferred over name qualification).
                    "is_seed": True,
                },
            ],
        }
    }
    dest = {"name": "Bryce Canyon National Park"}

    html = assembler._build_getting_here(
        ai,
        dest,
        previous_name="Zion National Park",
        previous_route_target="Zion National Park",
        current_route_target="Bryce Canyon National Park",
    )

    assert "waypoints=37.2136%2C-113.0064" in html
    assert "Canyon%20Overlook%20Trail" not in html


def test_build_getting_here_route_waypoint_falls_back_to_qualified_name_without_geocode() -> None:
    """Without a real geocode, the existing name-qualification fallback still
    applies (unchanged behavior) -- this is the pre-fix path other tests
    already cover, confirming the geocode-preference addition doesn't break
    the no-geocode case."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Arrive via scenic corridor.",
            # is_seed=True: bare-name fixture, no url/description; seeding
            # keeps it visible under the verified-link-or-seed policy
            # (2026-08-17) so this test can verify the no-geocode
            # name-qualification fallback it's actually about.
            "en_route_stops": [{"name": "Red Cliffs Desert Reserve", "is_seed": True}],
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

    assert "waypoints=Red%20Cliffs%20Desert%20Reserve%20St.%20George%2C%20Utah" in html


def test_build_getting_here_route_skips_ineligible_waypoints() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive north.",
            # is_seed=True on both: bare-name fixtures with no url/
            # description; seeding keeps the eligible one visible so this
            # test can verify ineligible-waypoint skipping, which is what
            # it's actually about.
            "en_route_stops": [
                {"name": "Mesquite", "route_waypoint_eligible": True, "is_seed": True},
                {"name": "Cedar City", "route_waypoint_eligible": False, "is_seed": True},
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
            # is_seed=True throughout: bare-name fixtures with no url/
            # description; seeding keeps them visible under the
            # verified-link-or-seed policy (2026-08-17) so this test can
            # verify route-progress ordering, which is what it's actually
            # about.
            "en_route_stops": [
                {"name": "Last Stop", "route_waypoint_eligible": True, "route_progress_ratio": 0.9, "is_seed": True},
                {"name": "First Stop", "route_waypoint_eligible": True, "route_progress_ratio": 0.2, "is_seed": True},
                {"name": "Mid Stop", "route_waypoint_eligible": True, "route_progress_ratio": 0.5, "is_seed": True},
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


def test_build_getting_here_unresolved_progress_ratio_sorts_after_confirmed_stops() -> None:
    """Regression for dipstick58 Bug 1 (real Bryce Canyon -> Capitol Reef leg).

    Two en-route stops -- "Fremont Petroglyphs" and "Gifford Homestead" --
    are both physically inside Capitol Reef National Park, i.e. at/past the
    destination. url_discovery's Nominatim geocoder failed to resolve either
    name to coordinates, so neither ever got a route_progress_ratio computed
    (unlike the five Highway 12 stops below, which are well-known state
    parks/overlooks that geocoded cleanly to real, verified ratios). Before
    the fix, a missing ratio silently defaulted to 0.0 in the sort key --
    tying with (and via stable sort, landing ahead of) "Kodachrome Basin
    State Park" at ratio 0.05, the genuinely earliest real stop along the
    route. The two unresolved, destination-adjacent stops rendered 1st and
    2nd, backwards from reality. They must now sort after every stop with a
    confirmed ratio instead.
    """
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Take US-89 S, then UT-12 E to UT-24 E.",
            # is_seed=True throughout: bare-name fixtures with no url/
            # description; seeding keeps them visible under the
            # verified-link-or-seed policy (2026-08-17) so this test can
            # verify route-progress-ratio sort ordering, which is what it's
            # actually about.
            "en_route_stops": [
                # Listed first in the AI-generated content, but never geocoded.
                {"name": "Fremont Petroglyphs", "route_waypoint_eligible": True, "is_seed": True},
                {"name": "Gifford Homestead", "route_waypoint_eligible": True, "is_seed": True},
                # Real Highway 12 stops between Bryce and Torrey, in true
                # geographic order, each with a verified route_progress_ratio.
                {"name": "Kodachrome Basin State Park", "route_waypoint_eligible": True, "route_progress_ratio": 0.05, "is_seed": True},
                {"name": "Escalante Petrified Forest State Park", "route_waypoint_eligible": True, "route_progress_ratio": 0.32, "is_seed": True},
                {"name": "Head of the Rocks Overlook", "route_waypoint_eligible": True, "route_progress_ratio": 0.41, "is_seed": True},
                {"name": "Lower Calf Creek Falls", "route_waypoint_eligible": True, "route_progress_ratio": 0.55, "is_seed": True},
                {"name": "Anasazi State Park Museum", "route_waypoint_eligible": True, "route_progress_ratio": 0.58, "is_seed": True},
            ],
        }
    }
    dest = {"name": "Capitol Reef National Park"}

    html = assembler._build_getting_here(
        ai,
        dest,
        previous_name="Bryce Canyon City",
        previous_route_target="Bryce Canyon City",
        current_route_target="Capitol Reef National Park",
    )

    rendered_order = [
        "Kodachrome Basin State Park",
        "Escalante Petrified Forest State Park",
        "Head of the Rocks Overlook",
        "Lower Calf Creek Falls",
        "Anasazi State Park Museum",
        "Fremont Petroglyphs",
        "Gifford Homestead",
    ]
    positions = [html.index(name) for name in rendered_order]
    assert positions == sorted(positions), (
        "Highway 12 stops with a confirmed route_progress_ratio must render "
        "before destination-adjacent stops with no resolved ratio"
    )


def test_build_getting_here_renders_geographic_order_when_all_stops_resolve() -> None:
    """Regression for dipstick59 Bug 1 (real Zion -> Bryce Canyon leg).

    The reported bug: Google's actual driving route for this leg zigzagged
    between two geographic clusters (Cedar City area vs. Kanab area) three
    times -- Parowan Gap Petroglyphs (Cedar City) -> Moqui Cave (Kanab) ->
    Cedar Breaks National Monument (Cedar City) -> Coral Pink Sand Dunes
    (Kanab) -> Willis Creek (near Bryce) -- instead of visiting each cluster
    once. Root cause (see generator/url_discovery.py
    _geocode_en_route_stop_for_route): 3 of these 5 real stop names carry an
    AI-generated descriptive suffix ("... Rim View", "... Boardwalk", "...
    Trailhead") that Nominatim's free-text search can't match, so those 3
    stops got no route_progress_ratio at all and piled up at the end of the
    list in AI-harvest order (reproducing the exact zigzag above) rather
    than in their real geographic position. Once url_discovery's
    progressive-truncation geocoding fallback resolves real coordinates for
    all 5 (verified against live Nominatim during investigation), this
    layer -- which only ever consumes whatever ratio it's given -- must
    render them in true route order.
    """
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Take US-89 N to UT-12 E.",
            # is_seed=True throughout: bare-name fixtures with no url/
            # description; seeding keeps them visible under the
            # verified-link-or-seed policy (2026-08-17) so this test can
            # verify geographic route ordering, which is what it's actually
            # about.
            "en_route_stops": [
                # Original AI-harvest order exactly as it appeared in the
                # dipstick59 output, each carrying its real, Nominatim-verified
                # route_progress_ratio (computed from real coordinates for a
                # Zion Canyon Visitor Center -> Bryce Canyon City leg).
                {"name": "Parowan Gap Petroglyphs", "route_waypoint_eligible": True, "route_progress_ratio": 0.3224, "is_seed": True},
                {"name": "Moqui Cave", "route_waypoint_eligible": True, "route_progress_ratio": 0.3664, "is_seed": True},
                {
                    "name": "Cedar Breaks National Monument Rim View",
                    "route_waypoint_eligible": True,
                    "route_progress_ratio": 0.3328,
                    "is_seed": True,
                },
                {
                    "name": "Coral Pink Sand Dunes State Park Boardwalk",
                    "route_waypoint_eligible": True,
                    "route_progress_ratio": 0.1662,
                    "is_seed": True,
                },
                {
                    "name": "Willis Creek Slot Canyon Trailhead",
                    "route_waypoint_eligible": True,
                    "route_progress_ratio": 0.9959,
                    "is_seed": True,
                },
            ],
        }
    }
    dest = {"name": "Bryce Canyon National Park"}

    html = assembler._build_getting_here(
        ai,
        dest,
        previous_name="Zion National Park",
        previous_route_target="Zion National Park",
        current_route_target="Bryce Canyon National Park",
    )

    rendered_order = [
        "Coral Pink Sand Dunes State Park Boardwalk",
        "Parowan Gap Petroglyphs",
        "Cedar Breaks National Monument Rim View",
        "Moqui Cave",
        "Willis Creek Slot Canyon Trailhead",
    ]
    positions = [html.index(name) for name in rendered_order]
    assert positions == sorted(positions), (
        "All 5 real en-route stops must render in true route-progress order "
        "once each has a resolved ratio, instead of the buggy AI-harvest "
        "order that zigzagged between the Cedar City and Kanab clusters"
    )
    # The buggy AI-harvest order must not survive: Moqui Cave (Kanab)
    # historically rendered immediately after Parowan Gap (Cedar City),
    # backtracking across the whole route.
    assert html.index("Cedar Breaks National Monument Rim View") < html.index("Moqui Cave")


def test_build_getting_here_uses_lodging_endpoint_but_destination_scoped_waypoints() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Arrive via I-15.",
            # is_seed=True: bare-name fixture with no url/description; seeding
            # keeps it visible under the verified-link-or-seed policy
            # (2026-08-17) so this test can verify the lodging-endpoint /
            # destination-scoped-waypoint distinction it's actually about.
            "en_route_stops": [
                {"name": "Red Cliffs Desert Reserve", "route_waypoint_eligible": True, "is_seed": True},
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


def test_build_getting_here_gmaps_waypoints_match_rendered_cards_zion_bryce() -> None:
    """Regression for a real dipstick63 run (Zion -> Bryce Canyon leg).

    The rendered "CAN'T-MISS ENROUTE" cards showed "Moqui Caverns" and "Best
    Friends Animal Sanctuary" (both with real discovered links), but the
    "Open in Google Maps" URL's waypoints were two bare coordinates that
    Google's own UI resolves to unrelated places -- because
    _build_route_gmaps_url used to pick its (up to 8) waypoints from the
    raw, unfiltered en_route_stops list purely by route-progress order, with
    no regard for which stops actually had a usable link and would render as
    a card. Here, 8 route-eligible-but-linkless/nameless candidate pins sort
    ahead (by route_progress_ratio) of the two real, linked stops -- under
    the old behavior they alone would fill the [:8] cap and the two real
    stops would never appear in the Maps URL at all, exactly reproducing
    "zero overlap" between cards and waypoints. The fix must ensure every
    waypoint corresponds to a stop actually rendered as a card.
    """
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    filler_candidates = [
        {
            "name": "",
            "route_waypoint_eligible": True,
            "route_progress_ratio": ratio,
            "geocode_lat": 37.0 + idx * 0.01,
            "geocode_lng": -112.5 - idx * 0.01,
        }
        for idx, ratio in enumerate([0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40])
    ]
    ai = {
        "getting_here": {
            "route_summary": "Take US-89 N to UT-12 E.",
            "en_route_stops": filler_candidates
            + [
                {
                    "name": "Moqui Caverns",
                    "url": "https://example.com/moqui-caverns",
                    "route_waypoint_eligible": True,
                    "route_progress_ratio": 0.85,
                },
                {
                    "name": "Best Friends Animal Sanctuary",
                    "url": "https://example.com/best-friends-animal-sanctuary",
                    "route_waypoint_eligible": True,
                    "route_progress_ratio": 0.95,
                },
            ],
        }
    }
    dest = {"name": "Bryce Canyon National Park"}

    html = assembler._build_getting_here(
        ai,
        dest,
        previous_name="Zion National Park",
        previous_route_target="Zion National Park",
        current_route_target="Bryce Canyon National Park",
    )

    # Both real, linked stops render as cards.
    assert "Moqui Caverns" in html
    assert "Best Friends Animal Sanctuary" in html

    # The Google Maps URL's waypoints section must contain exactly the two
    # rendered card names (qualified with the destination) and none of the
    # nameless filler candidates that never rendered as cards.
    gmaps_start = html.index("https://www.google.com/maps/dir/")
    gmaps_end = html.index('"', gmaps_start)
    gmaps_url = html[gmaps_start:gmaps_end]
    assert "Moqui%20Caverns" in gmaps_url
    assert "Best%20Friends%20Animal%20Sanctuary" in gmaps_url
    # None of the filler candidates' coordinates leaked into the URL.
    for idx in range(8):
        assert f"{37.0 + idx * 0.01}" not in gmaps_url


def test_build_getting_here_gmaps_waypoints_match_rendered_cards_canyonlands_telluride() -> None:
    """Regression for a real dipstick63 run (Canyonlands -> Telluride leg).

    The rendered cards showed 4 named stops (Castle Valley Overlook, Fisher
    Towers, Gateway Colorado Historic Site, Paradox Valley Scenic Pullout),
    but the Maps URL had 8 waypoints -- all bare coordinates or names of
    OTHER en-route candidates that never rendered as cards (no verified
    link) -- with zero overlap with the 4 rendered names. This test uses 4
    real named+linked stops plus more than 8 linkless filler candidates
    sorted ahead of them by route position, matching the real leg's shape
    (a long ~150+ mile drive with many discovered-but-unverified candidate
    pins).
    """
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    filler_candidates = [
        {
            "name": f"Unverified Pin {idx}",
            "description": "x",
            "route_waypoint_eligible": True,
            "route_progress_ratio": ratio,
        }
        for idx, ratio in enumerate([0.05, 0.08, 0.11, 0.14, 0.17, 0.20, 0.23, 0.26, 0.29])
    ]
    real_stops = [
        {
            "name": "Castle Valley Overlook",
            "url": "https://example.com/castle-valley-overlook",
            "route_waypoint_eligible": True,
            "route_progress_ratio": 0.55,
        },
        {
            "name": "Fisher Towers",
            "url": "https://example.com/fisher-towers",
            "route_waypoint_eligible": True,
            "route_progress_ratio": 0.60,
        },
        {
            "name": "Gateway Colorado Historic Site",
            "url": "https://example.com/gateway-colorado-historic-site",
            "route_waypoint_eligible": True,
            "route_progress_ratio": 0.70,
        },
        {
            "name": "Paradox Valley Scenic Pullout",
            "url": "https://example.com/paradox-valley-scenic-pullout",
            "route_waypoint_eligible": True,
            "route_progress_ratio": 0.80,
        },
    ]
    ai = {
        "getting_here": {
            "route_summary": "Take CO-141 through the valley.",
            "en_route_stops": filler_candidates + real_stops,
        }
    }
    dest = {"name": "Telluride, Colorado"}

    html = assembler._build_getting_here(
        ai,
        dest,
        previous_name="Canyonlands National Park",
        previous_route_target="Canyonlands National Park",
        current_route_target="Telluride, Colorado",
    )

    for stop in real_stops:
        assert stop["name"] in html

    gmaps_start = html.index("https://www.google.com/maps/dir/")
    gmaps_end = html.index('"', gmaps_start)
    gmaps_url = html[gmaps_start:gmaps_end]
    for stop in real_stops:
        # Waypoint text is the stop name qualified with the destination
        # (no geocode present here), space-encoded as %20.
        qualified = stop["name"].replace(" ", "%20")
        assert qualified in gmaps_url, f"expected {stop['name']} waypoint in {gmaps_url}"
    for filler in filler_candidates:
        assert filler["name"] not in html
        assert filler["name"].replace(" ", "%20") not in gmaps_url


def test_build_getting_here_gmaps_url_caps_at_eight_visible_cards() -> None:
    """More than 8 real, linked stops render as cards on a long leg (no cap
    on the cards themselves), but _build_route_gmaps_url still caps its
    waypoints at 8 -- now operating on the already-link-filtered visible
    list, so the cap can only ever drop trailing cards, never substitute in
    a stop that isn't rendered. This documents that deliberate, retained
    design decision (Google's own interactive directions UI does not
    reliably support arbitrarily many waypoints) rather than silently
    losing coverage."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    real_stops = [
        {
            "name": f"Real Stop {idx}",
            "url": f"https://example.com/real-stop-{idx}",
            "route_waypoint_eligible": True,
            "route_progress_ratio": ratio,
        }
        for idx, ratio in enumerate([0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95])
    ]
    ai = {
        "getting_here": {
            "route_summary": "Long scenic drive.",
            "en_route_stops": real_stops,
        }
    }
    dest = {"name": "Telluride, Colorado"}

    html = assembler._build_getting_here(
        ai,
        dest,
        previous_name="Canyonlands National Park",
        previous_route_target="Canyonlands National Park",
        current_route_target="Telluride, Colorado",
    )

    # All 10 render as cards -- no cap on the visible list.
    for stop in real_stops:
        assert stop["name"] in html

    gmaps_start = html.index("https://www.google.com/maps/dir/")
    gmaps_end = html.index('"', gmaps_start)
    gmaps_url = html[gmaps_start:gmaps_end]
    waypoint_count = gmaps_url.count("Real%20Stop")
    assert waypoint_count == 8, f"expected the Maps URL capped at 8 waypoints, got {waypoint_count}"
    # The 8 kept are the first 8 by route progress, not an arbitrary subset.
    for stop in real_stops[:8]:
        qualified = stop["name"].replace(" ", "%20")
        assert qualified in gmaps_url
    for stop in real_stops[8:]:
        qualified = stop["name"].replace(" ", "%20")
        assert qualified not in gmaps_url


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


def test_build_getting_there_route_option_link_has_target_and_rel() -> None:
    """Real bug (published eval run): the Turquoise Trail departure route
    option's <a> tag was missing target="_blank" rel="noopener" -- every
    other external link on the page carries both. Confirms the route-option
    render path (a different code path from en-route stops) was fixed to
    match."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_there": {
            "route_summary": "Departure leg toward Albuquerque, NM.",
            "route_options": [
                {
                    "title": "Turquoise Trail National Scenic Byway",
                    "url": "https://nsbfoundation.com/nb/turquoise-trail-national-scenic-byway/",
                    "distance_or_duration": "~50 mi total route",
                    "description": "This scenic byway connects Santa Fe and Albuquerque.",
                }
            ],
        }
    }
    dest = {"name": "Santa Fe"}
    trip_meta = {"return": "Albuquerque, NM"}

    html = assembler._build_getting_there(ai, dest, trip_meta)

    assert (
        '<a href="https://nsbfoundation.com/nb/turquoise-trail-national-scenic-byway/" '
        'target="_blank" rel="noopener">Turquoise Trail National Scenic Byway</a>' in html
    )
    assert "~50 mi total route" in html
    assert "one-way" not in html


def test_build_getting_there_route_option_renders_map_badge_when_maps_url_present() -> None:
    """Confirms the render side already surfaces maps_url as a badge for
    route options once it's present -- the real bug was that no code path
    upstream ever attached one (see url_discovery.py's _attach_secondary_
    maps_link now being called for route options too)."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_there": {
            "route_options": [
                {
                    "title": "Turquoise Trail National Scenic Byway",
                    "url": "https://nsbfoundation.com/nb/turquoise-trail-national-scenic-byway/",
                    "maps_url": "https://www.google.com/maps/search/?api=1&query=Turquoise+Trail+National+Scenic+Byway",
                    "distance_or_duration": "~50 mi total route",
                    "description": "This scenic byway connects Santa Fe and Albuquerque.",
                }
            ],
        }
    }
    dest = {"name": "Santa Fe"}
    trip_meta = {"return": "Albuquerque, NM"}

    html = assembler._build_getting_there(ai, dest, trip_meta)

    assert 'class="badge badge-map"' in html


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


def test_build_restaurants_with_no_url_are_absent_no_seed_exception() -> None:
    """Policy (2026-08-17): restaurants have no seed concept anywhere in this
    codebase (no manifest field, no is_seed tracking), so a restaurant with no
    direct URL and no maps fallback is never promoted -- unlike attractions
    and en-route stops, there is no exception that would keep it visible with
    an "Unverified" caution badge. It must not render at all."""
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

    assert "Cozy Corner Cafe" not in html
    assert "badge-caution" not in html


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
                # A real url on each: restaurants have no seed exception under
                # the verified-link-or-seed policy (2026-08-17), so a
                # no-url restaurant would no longer render at all -- these
                # fixtures need a real link to exercise the cuisine-tickler
                # rendering logic this test is actually about.
                {
                    "name": "Allred's Restaurant",
                    "description": "Source",
                    "cuisine": "American",
                    "price_range": "$$",
                    "url": "https://allredsrestaurant.com/",
                },
                {
                    "name": "Wild Rabbit Cafe",
                    "description": "Source",
                    "cuisine": "Cafe",
                    "price_range": "$",
                    "url": "https://wildrabbitcafe.com/",
                },
            ]
        },
        "Telluride",
    )

    assert "American-style dinner spot" not in html
    assert "Cafe-style dinner spot" not in html
    assert "verify current hours before you go" not in html
    # The names are already clean (no rating/price glued on) -- the cuisine
    # badge matching the name's own trailing word must not cause the
    # sanitizer to chop it off (see the dedicated regression below). Now
    # rendered as a link (real url present), so the name is followed by the
    # anchor close tag rather than the bare-name span close tag.
    assert "Wild Rabbit Cafe</a>" in html


def test_sanitize_restaurant_display_name_leaves_clean_name_alone_when_cuisine_matches_tail() -> None:
    """Dipstick58 bug 2 fallout: fixing the missing "bistro" cuisine keyword
    (see test_direct_batch_rows_from_html_recognizes_bistro_as_cuisine in
    test_url_discovery.py) surfaced a latent bug in the display-name
    sanitizer's cuisine-suffix "last resort" heuristic. That heuristic is
    only supposed to fire when rating/price decoration is glued onto the
    name (the docstring explicitly says "Wild Rabbit Cafe" with
    cuisine="Cafe" must not be touched), but the "not truncated" guard alone
    doesn't actually protect that case: an already-clean name (no rating or
    price at all) also leaves `truncated` False, so the cuisine match at the
    tail was still wrongly stripped -- "Book Club Bistro" with cuisine=
    "Bistro" collapsed to "Book Club". The fix requires independent evidence
    of glued-on decoration (a rating pattern or a price-symbol run
    elsewhere in the name) before trusting a cuisine match at the tail is
    decoration rather than the restaurant's real name."""
    assert HTMLAssembler._sanitize_restaurant_display_name("Wild Rabbit Cafe", cuisine="Cafe") == "Wild Rabbit Cafe"
    assert HTMLAssembler._sanitize_restaurant_display_name("Book Club Bistro", cuisine="Bistro") == "Book Club Bistro"
    # Genuine glued-on decoration (rating present) must still be stripped.
    assert (
        HTMLAssembler._sanitize_restaurant_display_name(
            "Cliffside Restaurant 4.4/5 $$$ American",
            rating_text="4.4/5",
            price="$$$",
            cuisine="American",
        )
        == "Cliffside Restaurant"
    )


def test_build_restaurants_rewrites_generic_locally_surfaced_description() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_restaurants(
        {
            "dinner_recommendations": [
                {
                    "name": "Cliffside Restaurant",
                    "description": "Locally surfaced dinner option.",
                    # A real url: restaurants have no seed exception under the
                    # verified-link-or-seed policy (2026-08-17), so a no-url
                    # restaurant wouldn't render at all -- this fixture needs
                    # a real link to actually exercise the generic-description
                    # rewrite this test is about.
                    "url": "https://cliffsiderestaurant.example.com/",
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
                    # A real url: under the verified-link-or-seed policy
                    # (2026-08-17), a non-seed no-url item no longer renders
                    # at all -- this fixture needs a real link to exercise
                    # the distance/elevation badge rendering this test is
                    # actually about.
                    "url": "https://www.nps.gov/zion/planyourvisit/emerald-pools.htm",
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
                    # A real url: under the verified-link-or-seed policy
                    # (2026-08-17), a non-seed no-url item no longer renders
                    # at all -- this fixture needs a real link to exercise
                    # the must-see/rating badge rendering this test is
                    # actually about.
                    "url": "https://www.nps.gov/zion/angels-landing.htm",
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
                    # A real url: under the verified-link-or-seed policy
                    # (2026-08-17), a non-seed no-url item no longer renders
                    # at all -- this fixture needs a real link so this
                    # non-seed item still renders, to prove it does NOT also
                    # get the seed badge (what this test is actually about).
                    "url": "https://www.nps.gov/zion/planyourvisit/kolobcanyonsroad.htm",
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


def test_build_attractions_non_seed_no_url_attraction_is_absent() -> None:
    """Policy (2026-08-17): a non-seed attraction with no URL used to still be
    promoted (_should_render_without_url) when it carried enough metadata/
    description to be useful, shown with the "Unverified" caution badge.
    Under the verified-link-or-seed policy that promotion path is gone for
    non-seed items -- rich metadata/description no longer earns a card, only
    a real verified url or seed status does. It must not render at all."""
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

    assert "Quiet Overlook" not in html
    assert "badge-caution" not in html


def test_build_attractions_seed_no_url_attraction_renders_caution_badge() -> None:
    """A seed attraction with no URL must still render, with the "Unverified"
    caution badge, per the verified-link-or-seed policy (2026-08-17) --
    unlike the non-seed case in
    test_build_attractions_non_seed_no_url_attraction_is_absent."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_attractions(
        {
            "top_attractions": [
                {
                    "name": "Quiet Overlook",
                    "difficulty": "Easy",
                    "duration": "30 min",
                    "description": "A short pull-off with sweeping canyon views.",
                    "is_seed": True,
                }
            ]
        },
        drives=[],
        dest_name="Zion National Park",
    )

    assert "Quiet Overlook" in html
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


def test_build_events_local_tip_renders_anchor_around_named_place() -> None:
    """Project owner's concrete example: 'Check out Moab Farmers Market' names
    a real place. When cultural_events.py attaches a verified/maps-fallback
    URL plus the specific place name, the rendered Local Tip must wrap that
    name in a real clickable <a href> instead of flat escaped text."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    events = {
        "has_events": False,
        "honest_assessment": "Moab has a reliable weekly market scene.",
        "local_tip": "Check out Moab Farmers Market on Thursday evenings for fresh produce.",
        "local_tip_name": "Moab Farmers Market",
        "local_tip_url": "https://www.google.com/maps/search/?api=1&query=Moab%20Farmers%20Market",
    }

    html = assembler._build_events(events, "Moab")

    assert (
        '<a href="https://www.google.com/maps/search/?api=1&amp;query=Moab%20Farmers%20Market" '
        'class="tip-link" target="_blank" rel="noopener">Moab Farmers Market</a>' in html
    )
    assert "Check out " in html
    assert "on Thursday evenings for fresh produce." in html


def test_build_events_local_tip_appends_link_when_name_not_verbatim_in_text() -> None:
    """If the tip text paraphrases the place name (doesn't contain it
    verbatim), still surface the real URL rather than silently dropping it --
    append a linked mention of the place instead."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    events = {
        "has_events": False,
        "honest_assessment": "Moab has a reliable weekly market scene.",
        "local_tip": "Head downtown Thursday evenings for fresh local produce.",
        "local_tip_name": "Moab Farmers Market",
        "local_tip_url": "https://moabfarmersmarket.org/",
    }

    html = assembler._build_events(events, "Moab")

    # class="tip-link" is load-bearing: the page loads Tailwind, whose Preflight
    # sets a{color:inherit;text-decoration:inherit}, and the template has no base
    # anchor rule -- so an unclassed tip link is invisible as a link.
    assert '<a href="https://moabfarmersmarket.org/" class="tip-link" target="_blank" rel="noopener">Moab Farmers Market</a>' in html
    assert "Head downtown Thursday evenings for fresh local produce." in html


def test_build_events_local_tip_renders_plain_text_without_url() -> None:
    """Today's exact behavior must be preserved when no URL is present:
    plain escaped text, no anchor markup."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    events = {
        "has_events": False,
        "honest_assessment": "Quiet scene with visitor center programs.",
        "local_tip": "Check ranger talks posted at the visitor center desk.",
    }

    html = assembler._build_events(events, "Zion National Park")

    assert (
        '<p class="local-tip"><strong>Local tip:</strong> '
        "Check ranger talks posted at the visitor center desk.</p>" in html
    )
    assert "<a href=" not in html


def test_build_events_format_a_falls_back_to_maps_url_when_url_missing() -> None:
    """Real bug (St. George eval run): "I-15 Country Rock Music Festival" and
    "Odyssey Dance Theatre's Thriller 2026" rendered as plain <strong> text
    with no <a href> at all -- despite cultural_events.py's _verify_event_urls
    deliberately assigning a Google-Maps-search fallback link to every
    Format-A event with no surviving real URL. Root cause: url_discovery.py's
    audit_discovered_urls re-validates every event url through the strict
    retention gate and strips a google_maps_search-class fallback in
    "enforce" policy mode, but (until fixed) never preserved it as a separate
    maps_url the way restaurants/attractions/en-route stops already do --
    so by render time the event had neither url nor maps_url. Once the audit
    pass preserves maps_url, _build_events must actually use it as a
    fallback for the event name link."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    events = {
        "has_events": True,
        "events": [
            {
                "name": "I-15 Country Rock Music Festival",
                "dates_in_range": "October 17, 2026",
                "venue": "Mesquite Regional Sports and Event Complex",
                "admission": "Varies",
                "maps_url": (
                    "https://www.google.com/maps/search/?api=1&query="
                    "I-15+Country+Rock+Music+Festival+Mesquite+Regional+Sports+and+Event+Complex"
                ),
            }
        ],
    }

    html = assembler._build_events(events, "St. George, Utah")

    assert '<a href="https://www.google.com/maps/search/?api=1&amp;query=' in html
    assert 'class="event-link"' in html
    assert '>I-15 Country Rock Music Festival</a>' in html


def test_build_events_format_a_no_link_when_neither_url_nor_maps_url() -> None:
    """When there's truly nothing to link to (no url, no maps_url), the event
    still renders as plain text -- preserves pre-fix behavior for that case."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    events = {
        "has_events": True,
        "events": [
            {
                "name": "Unlinked Event",
                "dates_in_range": "October 17, 2026",
                "venue": "Some Venue",
                "admission": "Free",
            }
        ],
    }

    html = assembler._build_events(events, "St. George, Utah")

    assert "<a href=" not in html
    assert "<strong>Unlinked Event</strong>" in html


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
    assert 'target="_blank" rel="noopener"' in html


def test_header_links_renders_redacted_placeholder_not_a_link() -> None:
    """A privacy-redacted planning_links entry (main._apply_privacy_redaction)
    must render as an explanatory, non-clickable placeholder rather than
    either a real link or silently vanishing -- see requirements.md's
    planning-links redaction policy."""
    assembler = HTMLAssembler(config_path="config.yaml")
    dest = {"lat": 37.2982, "lng": -113.0263}
    links = [{"label": "Trip Plans", "url": "", "redacted": True}]

    html = assembler._build_header_links(links, nps_code=None, dest=dest, attractions=[])

    assert "Trip Plans" in html
    assert ">Trip Plans</span>" in html
    assert ">Trip Plans</a>" not in html
    assert 'title="' in html


def test_header_links_all_four_types_open_in_new_tab() -> None:
    """Every notion-header-btn anchor (Current Weather, Attractions Map,
    NPS, and custom manifest-provided links) must carry
    target="_blank" rel="noopener" so it doesn't navigate away from the
    generated page -- see dipstick63 bug report."""
    assembler = HTMLAssembler(config_path="config.yaml")
    dest = {"name": "St. George", "lat": 37.2982, "lng": -113.0263}
    links = [{"label": "Trip Plan", "url": "https://example.com/plan"}]
    attractions = [
        {"name": "Pioneer Park"},
        {"name": "Snow Canyon State Park"},
    ]

    html = assembler._build_header_links(links, nps_code="zion", dest=dest, attractions=attractions)

    assert html.count('class="notion-header-btn"') == 4
    assert html.count('target="_blank" rel="noopener"') == 4
    assert ">Current Weather<" in html
    assert ">Attractions Map<" in html
    assert ">NPS<" in html
    assert ">Trip Plan<" in html


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
    'DRIVE_DESCRIPTIONS keys with no modal button' check.

    Entries are now derived from the buttons actually rendered
    (self._rendered_drive_titles), so a drive whose card was dropped emits no
    key by construction. An empty set here represents "sections rendered, no
    drive buttons among them"."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._rendered_drive_titles = set()
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


def test_build_attractions_orders_non_hikes_before_hikes_before_scenic_drives() -> None:
    """Owner feedback: attraction cards should render with non-hike items up
    front, hikes/trails in the middle, and scenic drives last -- regardless
    of the order top_attractions arrived in from AI generation/harvest.
    Items within the same bucket must keep their original relative order
    (stable sort), so this deliberately interleaves two hikes and two
    non-hikes rather than grouping them already."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Zion Narrows Hike",
                "type": "hike",
                "description": "A river-wading slot canyon hike.",
                "url": "https://www.nps.gov/zion/narrows.htm",
            },
            {
                "name": "Zion History Museum",
                "type": "attraction",
                "description": "Exhibits on the park's human history.",
                "url": "https://www.nps.gov/zion/museum.htm",
            },
            {
                "name": "Angels Landing Trail",
                "type": "hike",
                "description": "A strenuous chain-assisted climb to a narrow summit ridge.",
                "url": "https://www.nps.gov/zion/angels-landing.htm",
            },
            {
                "name": "Kolob Canyons Viewpoint",
                "type": "viewpoint",
                "description": "A pull-off overlook of the red-rock Kolob Canyons formations.",
                "url": "https://www.nps.gov/zion/kolob.htm",
            },
        ]
    }
    drives = [
        {
            "title": "Zion-Mt Carmel Highway",
            "category": "drive",
            "description": "A scenic switchback highway through slickrock tunnels.",
            "distance_or_duration": "1 hr",
        }
    ]

    html = assembler._build_attractions(ai, drives=drives, dest_name="Zion National Park")

    positions = {
        name: html.index(name)
        for name in (
            "Zion Narrows Hike",
            "Zion History Museum",
            "Angels Landing Trail",
            "Kolob Canyons Viewpoint",
            "Zion-Mt Carmel Highway",
        )
    }

    # Non-hike bucket first, in original relative order.
    assert positions["Zion History Museum"] < positions["Kolob Canyons Viewpoint"]
    # Hike bucket next, in original relative order, after all non-hikes.
    assert positions["Kolob Canyons Viewpoint"] < positions["Zion Narrows Hike"]
    assert positions["Zion Narrows Hike"] < positions["Angels Landing Trail"]
    # Scenic drives last, after every top_attractions entry.
    assert positions["Angels Landing Trail"] < positions["Zion-Mt Carmel Highway"]


def test_build_attractions_orders_hike_difficulty_items_in_hike_bucket_despite_type() -> None:
    """Regression for dipstick60 Bug 2 (real data): type == "hike" alone
    under-detects hikes. Canyonlands' "Mesa Arch" rendered with type
    "viewpoint" and Telluride's "Bridal Veil Falls" rendered with type
    "attraction", yet both carried a hike-difficulty badge (Easy/Moderate)
    and a walking duration -- so both stayed in the non-hike bucket and
    rendered first instead of in the middle with the real hikes.
    `difficulty` is a hike-specific field (url_discovery.py clears it when
    demoting an item away from hike-ness), so a recognized difficulty value
    must also route an item into the hike bucket even when `type` doesn't
    say "hike"."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Mesa Arch",
                "type": "viewpoint",
                "difficulty": "Easy",
                "duration": "30 min",
                "description": "A short trail leads to this arch.",
                "url": "https://www.alltrails.com/trail/us/utah/mesa-arch",
            },
            {
                "name": "Grand View Point",
                "type": "attraction",
                "description": "Sweeping 360-degree vistas.",
                "url": "https://www.nps.gov/cany/grandview.htm",
            },
            {
                "name": "White Rim Overlook Trail",
                "type": "hike",
                "description": "Flat scenic path to a rim overlook.",
                "url": "https://www.alltrails.com/trail/us/utah/white-rim-overlook-trail",
            },
        ]
    }

    html = assembler._build_attractions(ai, drives=[], dest_name="Canyonlands National Park")

    positions = {
        name: html.index(name)
        for name in ("Mesa Arch", "Grand View Point", "White Rim Overlook Trail")
    }

    # The true non-hike (no difficulty, type "attraction") renders first...
    assert positions["Grand View Point"] < positions["Mesa Arch"]
    # ...and the difficulty-bearing "viewpoint" joins the type=="hike" item
    # in the hike bucket, not ahead of every non-hike.
    assert positions["Grand View Point"] < positions["White Rim Overlook Trail"]


def test_build_attractions_drops_scenic_drive_describing_same_place_different_wording() -> None:
    """Regression for dipstick58 Bug 3 (real Telluride data): a top_attraction
    titled "Telluride Mountain Village Gondola" and a scenic_drives item
    titled "Free Gondola to Mountain Village" describe the same real, free
    town<->resort gondola -- just worded differently by two independent AI
    generation passes. The prior exact-normalized-string dedup missed this
    entirely (different wording => different normalized string => no dedup),
    so both cards rendered. Token-overlap matching must catch it."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Telluride Mountain Village Gondola",
                "type": "attraction",
                "description": "Free gondola connecting Telluride and Mountain Village.",
                "url": "https://www.telluride.com/activities/gondola",
                "rating": 4.9,
            }
        ]
    }
    drives = [
        {
            "title": "Free Gondola to Mountain Village",
            "category": "scenic",
            "description": (
                "The gondola ride connects Telluride and Mountain Village, "
                "offering aerial views of the mountains and valleys below."
            ),
            "distance_or_duration": "13 min",
        }
    ]

    html = assembler._build_attractions(ai, drives=drives, dest_name="Telluride")

    assert "Telluride Mountain Village Gondola" in html
    assert "attr-drive-item" not in html
    assert "telluride.com/activities/gondola" in html


def test_build_attractions_dedup_never_discards_a_url_the_attraction_side_already_had() -> None:
    """dipstick60 Bug 1 investigation (real Telluride data): the owner
    reported that after the dipstick58 dedup fix above correctly merged the
    duplicate gondola cards, the surviving card had no link at all. Traced
    against the actual dipstick60 run (destination_status_report.json):
    this run's URL-discovery harvest never resolved a real gondola URL for
    either the top_attractions or scenic_drives entry that run (repeated
    "direct_batch_no_match", one wrong-domain candidate that didn't survive
    to render) -- a harvest-recall variance, not something this rendering
    code did.

    Structurally, `_build_attractions` determines the attraction's own URL
    (via `_select_preferred_external_link`) and renders its row *before*
    the scenic-drives dedup loop runs, so dropping a duplicate drive can
    never retroactively clear a URL the attraction row already resolved.
    This test locks in that ordering: when the attraction side has no URL
    of its own, it still renders (with the "Unverified" caution badge, not
    silently dropped) and the duplicate drive is still suppressed -- even
    when the drive side *does* carry the real URL. The intended behavior is
    "the attraction renders with whatever URL IT already had", not
    "borrow the dropped drive's URL".

    Seeded here (is_seed=True): under the verified-link-or-seed policy
    (2026-08-17), a non-seed attraction with no url would now be removed
    from the itinerary entirely rather than rendered unverified -- this
    real dipstick60 harvest-miss scenario is exactly the kind of case the
    new policy targets for removal. Seeding keeps the item visible so this
    test can still verify the URL-borrowing-ordering guarantee it's really
    about.
    """
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Telluride Mountain Village Gondola",
                "type": "attraction",
                "description": "Free gondola connecting Telluride and Mountain Village.",
                "rating": 4.5,
                "duration": "20-min round-trip",
                "is_seed": True,
                # No url/url_candidates -- mirrors this run's harvest miss.
            }
        ]
    }
    drives = [
        {
            "title": "Free Gondola to Mountain Village",
            "category": "scenic",
            "description": (
                "The gondola ride connects Telluride and Mountain Village, "
                "offering aerial views of the mountains and valleys below."
            ),
            "distance_or_duration": "13 min",
            # Even if a URL had been found for the drive-side duplicate, it
            # must not be borrowed onto the attraction card.
            "url": "https://example.com/should-not-be-borrowed",
        }
    ]

    html = assembler._build_attractions(ai, drives=drives, dest_name="Telluride")

    assert "Telluride Mountain Village Gondola" in html
    assert "attr-drive-item" not in html  # duplicate drive still suppressed
    assert "should-not-be-borrowed" not in html  # no URL-borrowing from the drive
    assert "⚠ Unverified" in html  # renders without a link, flagged, not blank


def test_build_attractions_keeps_distinct_attractions_sharing_directional_qualifier() -> None:
    """Guard against over-matching: two real, genuinely distinct places (e.g.
    Bryce Canyon's actual Sunrise Point and Sunset Point viewpoints) can
    share every word but a directional/temporal qualifier. High word overlap
    alone must not be treated as evidence they're the same place."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Sunrise Point Overlook",
                "type": "viewpoint",
                "description": "A classic first-light view over the amphitheater.",
                "url": "https://www.nps.gov/brca/planyourvisit/sunrise-point.htm",
            }
        ]
    }
    drives = [
        {
            "title": "Sunset Point Overlook",
            "category": "viewpoint",
            "description": "A classic end-of-day view over the amphitheater.",
            "distance_or_duration": "5-min walk",
        }
    ]

    html = assembler._build_attractions(ai, drives=drives, dest_name="Bryce Canyon National Park")

    assert "Sunrise Point Overlook" in html
    assert "Sunset Point Overlook" in html
    assert "attr-drive-item" in html


def test_attraction_names_are_duplicates_matches_and_guards() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)

    assert assembler._attraction_names_are_duplicates(
        "Telluride Mountain Village Gondola", "Free Gondola to Mountain Village"
    )
    assert not assembler._attraction_names_are_duplicates(
        "Sunrise Point Overlook", "Sunset Point Overlook"
    )
    assert not assembler._attraction_names_are_duplicates(
        "Inspiration Point", "Bryce Canyon Scenic Drive"
    )
    assert assembler._attraction_names_are_duplicates("Inspiration Point", "Inspiration Point")


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
            "travel_time": "1h 15m",
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
            "travel_time": "1h 15m",
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


def test_build_getting_here_non_seed_stop_with_no_url_is_absent() -> None:
    """Policy (2026-08-17): a non-seed en-route stop with no direct URL and no
    maps fallback is no longer promoted on description strength alone -- a
    rich description used to be enough to render it with the "Unverified"
    caution badge, but under the verified-link-or-seed policy a maps-search
    fallback or bare-description promotion doesn't count as verified, and
    this stop isn't a seed, so it must not render at all. (In the real
    pipeline this stop would already have been pruned by url_discovery.py's
    audit pass before reaching HTML assembly -- this exercises
    _should_render_without_url's own defense-in-depth behavior directly.)"""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive to Santa Fe.",
            "distance_miles": "60",
            "travel_time": "1h 15m",
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

    assert "Adobe Plaza" not in html
    assert "badge-caution" not in html


def test_build_getting_here_seed_stop_with_no_url_renders_caution_badge() -> None:
    """A seed en-route stop (the traveler's own explicit `en_route_seeds`
    request) with no direct URL and no maps fallback must still render, with
    the "Unverified" caution badge, per the verified-link-or-seed policy
    (2026-08-17) -- unlike the non-seed case in
    test_build_getting_here_non_seed_stop_with_no_url_is_absent."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive to Santa Fe.",
            "distance_miles": "60",
            "travel_time": "1h 15m",
            "en_route_stops": [
                {
                    "name": "Adobe Plaza",
                    "description": "A quiet detour through an old adobe plaza with local artisan shops.",
                    "is_seed": True,
                }
            ],
        }
    }
    dest = {"name": "Santa Fe"}

    html = assembler._build_getting_here(ai, dest, previous_name="Albuquerque")

    assert "Adobe Plaza" in html
    assert '<span class="badge badge-caution" title="No verified source link found for this recommendation">⚠ Unverified</span>' in html


def test_build_getting_here_maps_fallback_does_not_append_map_suffix() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive to Santa Fe.",
            "distance_miles": "60",
            "travel_time": "1h 15m",
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


def test_build_getting_here_falls_back_to_maps_url_when_canonical_missing() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive to Santa Fe.",
            "distance_miles": "60",
            "travel_time": "1h 15m",
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
            "travel_time": "2h 30m",
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

    # "round trip", not "detour": both the geometry floor and the estimate in
    # url_discovery are 2x the perpendicular offset, and the card never said so.
    assert "3 mi round trip" in html
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
            "travel_time": "2h 30m",
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
            "travel_time": "2h 30m",
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


def test_destination_attractions_map_url_uses_multi_waypoint_directions() -> None:
    """Multiple attractions must render as real named map pins via a
    /maps/dir/ URL, not collapse to a generic destination-only search."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    url = assembler._build_destination_attractions_map_url(
        "St. George, Utah",
        [
            {"name": "Pioneer Park"},
            {"name": "St. George Dinosaur Discovery Site"},
            {"name": "Red Cliffs Desert Reserve"},
            {"name": "Snow Canyon State Park"},
        ],
    )

    assert url.startswith("https://www.google.com/maps/dir/?")
    assert "origin=Pioneer%20Park%20St.%20George%2C%20Utah" in url
    assert "destination=Snow%20Canyon%20State%20Park" in url
    assert "waypoints=" in url
    # optimize:true is NOT valid syntax for this public, keyless Maps URL
    # scheme (google.com/maps/dir/?api=1&...) -- live-verified (dipstick68)
    # that Google tries to geocode the literal string as a place name
    # instead, producing a wildly wrong route. Must never reappear here.
    assert "optimize" not in url.lower()
    assert "St.%20George%20Dinosaur%20Discovery%20Site" in url
    assert "Red%20Cliffs%20Desert%20Reserve" in url


def test_build_route_gmaps_url_never_emits_optimize_true() -> None:
    """Real dipstick68 regression: the consumer-facing, keyless
    google.com/maps/dir/?api=1&... URL scheme does not support the
    Directions API's "optimize:true|" waypoint-reorder convention. Adding
    it made Google try to geocode the literal string "optimize:true" as a
    place, matching it to an unrelated business ("Optimize Health," a
    Washington-state clinic) and turning a normal ~10-minute St. George
    route into a 33-hour, 2,196-mile one -- live-verified directly against
    real Google Maps. Must never reappear in a multi-waypoint route URL."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    stops = [
        {
            "name": "Jenny's Canyon Trail",
            "route_waypoint_eligible": True,
            "route_progress_ratio": 0.3,
        },
        {
            "name": "Red Hills Desert Garden",
            "route_waypoint_eligible": True,
            "route_progress_ratio": 0.6,
        },
    ]
    url = assembler._build_route_gmaps_url(
        "St. George Dinosaur Discovery Site at Johnson Farm",
        {"name": "Chuckwalla Trail"},
        stops,
        waypoint_scope_name="St. George, Utah",
    )

    assert "optimize" not in url.lower()
    assert "waypoints=" in url
    assert "Jenny" in url
    assert "Red%20Hills%20Desert%20Garden" in url


def test_destination_attractions_map_url_prefers_geocoded_coordinates() -> None:
    """When an attraction has real geocoded coordinates, use them instead of
    a name-based text query -- mirrors _build_route_gmaps_url's preference,
    since a coordinate resolves to exactly one point."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    url = assembler._build_destination_attractions_map_url(
        "Moab, Utah",
        [
            {"name": "Delicate Arch", "geocode_lat": 38.7436, "geocode_lng": -109.4993},
            {"name": "Dead Horse Point", "geocode_lat": 38.4802, "geocode_lng": -109.7404},
        ],
    )

    assert url.startswith("https://www.google.com/maps/dir/?")
    assert "origin=38.7436%2C-109.4993" in url
    assert "destination=38.4802%2C-109.7404" in url


def test_destination_attractions_map_url_single_item_remains_focused_search() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    url = assembler._build_destination_attractions_map_url(
        "St. George, Utah",
        [{"name": "Pioneer Park"}],
    )

    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "Pioneer%20Park%20St.%20George%2C%20Utah" in url


def test_build_restaurants_prefers_discovered_url_over_maps_query() -> None:
    """The primary attribution link must stay the real discovered URL, not
    the maps-search fallback -- but the fallback is still useful as a
    secondary "locate on a map" badge, so it's expected to appear there
    (see _maps_corner_link_html)."""
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
    assert '<a href="https://www.tripadvisor.com' in html
    assert 'class="badge badge-map"' in html
    assert "google.com/maps/search/?api=1&amp;query=Tandoor" in html


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
            "travel_time": "2h 30m",
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
            "travel_time": "2h 30m",
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
            "travel_time": "2h 30m",
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
            "travel_time": "2h 30m",
            # is_seed=True: no url on this fixture; seeding keeps it visible
            # under the verified-link-or-seed policy (2026-08-17) so this test
            # can verify note/description dedup, which is what it's actually
            # about.
            "en_route_stops": [
                {
                    "name": "Silver Reef Museum",
                    "description": "Preserved 1870s silver mining town with original buildings and exhibits",
                    "practical_note": "Preserved 1870s silver mining town with original buildings and exhibits",
                    "is_seed": True,
                }
            ],
        }
    }
    dest = {"name": "St. George"}

    html = assembler._build_getting_here(ai, dest, previous_name="Zion National Park")

    assert html.count("Preserved 1870s silver mining town with original buildings and exhibits") == 1


# ── GH #68 multi-site grouping ───────────────────────────────────────────────


def _moab_group_destinations() -> list[dict]:
    return [
        {
            "id": "moab",
            "name": "Moab",
            "dates": "August 1-4, 2026",
            "lodging": {"name": "Moab Springs Ranch", "location": "Moab Springs Ranch, Moab, UT", "checkin_time": "4:00 PM"},
        },
        {
            "id": "arches",
            "name": "Arches National Park",
            "dates": "August 2, 2026",
            "group_with": "moab",
        },
        {
            "id": "canyonlands",
            "name": "Canyonlands National Park",
            "dates": "August 3, 2026",
            "group_with": "moab",
        },
    ]


def test_build_nav_tabs_omits_grouped_entries_entirely() -> None:
    """dipstick60 review: a grouped child's content lives nested inside its
    base's own section (_build_group_child_card), so it must not also get
    its own top-level nav-tab entry -- that broke the 1:1 relationship
    between a nav entry and a numbered overview-map marker, and isn't
    needed once the content is reachable by scrolling the base's section."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_nav_tabs(_moab_group_destinations(), {})

    assert 'class="tab-group"' not in html
    assert "tab-btn-grouped" not in html
    assert 'data-tab="section-moab"' in html
    assert 'data-tab="section-arches"' not in html
    assert 'data-tab="section-canyonlands"' not in html


def test_build_nav_tabs_numbers_stay_sequential_after_grouped_entries() -> None:
    """Regression: a real trip (St. George, Zion, Bryce, Capitol Reef, Moab,
    Arches[grouped], Canyonlands[grouped], Telluride, ...) rendered nav-tab
    labels "1, 2, 3, 4, 5, 8, 9, 10" -- jumping straight from 5 to 8 once
    the two grouped day-trip children (original indices 5 and 6) were
    correctly skipped from getting their own tab, but the surviving tabs'
    displayed number was still each destination's original full-list index
    rather than its position among only the rendered tabs. The overview map
    is unaffected by this fix and deliberately keeps numbering every
    physical stop including day trips (see this function's docstring) --
    only the nav menu's own internal numbering needed to be self-consistent."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = _moab_group_destinations() + [
        {"id": "telluride", "name": "Telluride"},
        {"id": "santafe", "name": "Santa Fe"},
    ]
    html = assembler._build_nav_tabs(destinations, {})

    assert "1 · Moab" in html
    assert "2 · Telluride" in html
    assert "3 · Santa Fe" in html
    assert "4 ·" not in html


def test_build_nav_tabs_ungrouped_destinations_render_flat() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = [
        {"id": "zion", "name": "Zion National Park"},
        {"id": "moab", "name": "Moab, Utah"},
    ]
    html = assembler._build_nav_tabs(destinations, {})

    assert 'class="tab-group"' not in html
    assert "tab-btn-grouped" not in html
    assert 'data-tab="section-zion"' in html
    assert 'data-tab="section-moab"' in html


def test_build_group_lodging_pointer_renders_for_grouped_entry_without_own_lodging() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = _moab_group_destinations()
    dest_by_id = {d["id"]: d for d in destinations}
    arches = dest_by_id["arches"]

    html = assembler._build_group_lodging_pointer(arches, dest_by_id)

    assert "Moab Springs Ranch" in html
    assert 'href="#section-moab"' in html


def test_build_group_lodging_pointer_empty_when_entry_overrides_lodging() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = _moab_group_destinations()
    dest_by_id = {d["id"]: d for d in destinations}
    arches = dict(dest_by_id["arches"])
    arches["lodging"] = {"name": "Arches Basecamp", "location": "Arches Basecamp, Moab, UT"}

    assert assembler._build_group_lodging_pointer(arches, dest_by_id) == ""


def test_build_group_lodging_pointer_empty_for_ungrouped_entry() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = _moab_group_destinations()
    dest_by_id = {d["id"]: d for d in destinations}
    moab = dest_by_id["moab"]

    assert assembler._build_group_lodging_pointer(moab, dest_by_id) == ""


def test_build_restaurants_renders_see_base_pointer_when_deferred() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._multi_site_base_owned_categories = frozenset({"restaurant"})
    destinations = _moab_group_destinations()
    dest_by_id = {d["id"]: d for d in destinations}
    arches = dest_by_id["arches"]

    html = assembler._build_restaurants({"dinner_recommendations": []}, "Arches National Park", dest=arches, dest_by_id=dest_by_id)

    assert "Dinner recommendations: see " in html
    # dipstick60 Bug 3: only the destination name itself is the clickable
    # anchor, not the whole "see Moab" phrase.
    assert '<a href="#section-moab">Moab</a>' in html


def test_group_base_pointer_html_styles_and_links_only_the_destination_name() -> None:
    """dipstick60 Bug 3: the owner reported the "see base" pointer (e.g.
    "Dinner recommendations: see Moab") as plain, unstyled/uncentered text.
    _group_base_pointer_html now (a) carries the .group-base-pointer CSS
    class the template styles with padding/centering (see
    templates/v2.5_template.html), and (b) wraps only the destination name
    itself in the <a> -- the icon/label prefix stays plain text -- reusing
    the same #section-<id> hash target the top nav's own tab buttons jump
    to, not a new navigation mechanism."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    dest_by_id = {"moab": {"id": "moab", "name": "Moab"}}
    dest = {"id": "arches", "name": "Arches National Park", "group_with": "moab"}

    html = assembler._group_base_pointer_html(dest, dest_by_id, "Dinner recommendations", icon="\U0001f37d️")

    assert html == (
        '<p class="group-base-pointer">\U0001f37d️ Dinner recommendations: see '
        '<a href="#section-moab">Moab</a></p>\n'
    )


def test_build_restaurants_no_pointer_for_ungrouped_entry_with_no_restaurants() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._multi_site_base_owned_categories = frozenset({"restaurant"})

    html = assembler._build_restaurants({"dinner_recommendations": []}, "Zion National Park", dest={"id": "zion"})

    assert html == ""


def test_build_events_renders_see_base_pointer_when_deferred() -> None:
    """dipstick67 real validation-run finding: Canyonlands (a grouped
    child of Moab) independently rendered its own Cultural Events card
    with a confusing, self-contradictory tip ("Check out the Moab Music
    Festival, which... concludes before your visit") that was actually
    about Moab's own cultural scene, not Canyonlands. cultural_events is
    now a default base_owned_category (see multi_site_grouping.py), so a
    grouped child with empty cultural_events (cultural_events.py's
    discovery skip-gate leaves it {}) renders the same "see base" pointer
    restaurants/scenic-drives already use, instead of silently vanishing
    or (as observed) generating its own unrelated content."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._multi_site_base_owned_categories = frozenset({"restaurant", "cultural_events"})
    destinations = _moab_group_destinations()
    dest_by_id = {d["id"]: d for d in destinations}
    canyonlands = dest_by_id["canyonlands"]

    html = assembler._build_events({}, "Canyonlands National Park", dest=canyonlands, dest_by_id=dest_by_id)

    assert "Cultural events: see " in html
    assert '<a href="#section-moab">Moab</a>' in html
    assert "Moab Music Festival" not in html


def test_build_events_no_pointer_for_ungrouped_entry_with_no_events() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._multi_site_base_owned_categories = frozenset({"restaurant", "cultural_events"})

    html = assembler._build_events({}, "Zion National Park", dest={"id": "zion"})

    assert html == ""


def test_build_group_child_card_renders_cultural_events_pointer_not_own_content() -> None:
    """End-to-end grouped-child-card regression grounded in the real
    dipstick67 example: Canyonlands (grouped under Moab) previously
    independently rendered its own Cultural Events card with a confusing,
    self-contradictory local tip ("Check out the Moab Music Festival,
    which... concludes before your visit") that was actually about Moab's
    own cultural scene. cultural_events.py's discovery skip-gate now
    leaves a deferred grouped child's dest["cultural_events"] empty (the
    Grok search + synthesis call for it is skipped entirely, saving the
    API cost -- see cultural_events.py's discover()), so the realistic
    post-generation state is {} here, same as dinner_recommendations for
    a deferred restaurant. _build_group_child_card must render the "see
    base" pointer for it, not silently omit the section."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._config = {}
    assembler._multi_site_base_owned_categories = frozenset({"restaurant", "cultural_events"})
    destinations = _moab_group_destinations()
    dest_by_id = {d["id"]: d for d in destinations}
    canyonlands = dict(dest_by_id["canyonlands"])
    canyonlands["ai_content"] = {"top_attractions": [], "getting_here": {"en_route_stops": []}}
    canyonlands["cultural_events"] = {}

    html = assembler._build_group_child_card(
        canyonlands, {}, "Arches National Park", "Arches National Park, UT", "Moab", dest_by_id
    )

    assert "Moab Music Festival" not in html
    assert "Cultural events: see " in html
    assert '<a href="#section-moab">Moab</a>' in html


def test_build_single_section_base_still_renders_own_cultural_events() -> None:
    """The group base itself (Moab, no group_with) must keep rendering its
    own real Cultural Events content normally -- deferral only ever
    applies to grouped children, never to the base that owns the
    category (mirrors category_deferred_to_base's is_grouped() guard)."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._multi_site_base_owned_categories = frozenset({"restaurant", "cultural_events"})
    destinations = _moab_group_destinations()
    dest_by_id = {d["id"]: d for d in destinations}
    moab = dest_by_id["moab"]

    events = {
        "has_events": False,
        "honest_assessment": "No ticketed events were confidently verified for these dates.",
        "local_tip": "Check out the Moab Farmers Market on Thursday evenings.",
    }

    html = assembler._build_events(events, "Moab", dest=moab, dest_by_id=dest_by_id)

    assert "Cultural events: see " not in html
    assert "Moab Farmers Market" in html


def test_build_attractions_renders_see_base_pointer_for_deferred_scenic_drives_only() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._multi_site_base_owned_categories = frozenset()
    destinations = _moab_group_destinations()
    dest_by_id = {d["id"]: d for d in destinations}
    arches = dict(dest_by_id["arches"])
    arches["base_owned_categories"] = ["scenic_drive"]

    html = assembler._build_attractions({"top_attractions": []}, [], "Arches National Park", dest=arches, dest_by_id=dest_by_id)

    assert "Scenic drives" in html
    assert "Scenic drives: see " in html
    assert '<a href="#section-moab">Moab</a>' in html


def test_build_attractions_appends_scenic_drive_pointer_alongside_own_attractions() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._multi_site_base_owned_categories = frozenset()
    destinations = _moab_group_destinations()
    dest_by_id = {d["id"]: d for d in destinations}
    arches = dict(dest_by_id["arches"])
    arches["base_owned_categories"] = ["scenic_drive"]
    ai = {
        "top_attractions": [
            {"name": "Delicate Arch", "type": "hike", "description": "Iconic arch hike.", "url": "https://www.nps.gov/arch/delicate"},
        ]
    }

    html = assembler._build_attractions(ai, [], "Arches National Park", dest=arches, dest_by_id=dest_by_id)

    assert "Delicate Arch" in html
    assert "Scenic drives" in html
    assert "Scenic drives: see " in html
    assert '<a href="#section-moab">Moab</a>' in html


def test_build_getting_here_renders_day_trip_badge_for_grouped_entry() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = _moab_group_destinations()
    dest_by_id = {d["id"]: d for d in destinations}
    arches = dest_by_id["arches"]
    ai = {
        "getting_here": {
            "distance_miles": "5",
            "travel_time": "15 min",
            "en_route_stops": [],
        }
    }

    html = assembler._build_getting_here(ai, arches, previous_name="Moab", dest_by_id=dest_by_id)

    assert "Day Trip" in html


def test_build_getting_here_no_day_trip_badge_for_ungrouped_entry() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {"getting_here": {"distance_miles": "120", "travel_time": "2h 30m", "en_route_stops": []}}
    dest = {"id": "moab", "name": "Moab"}

    html = assembler._build_getting_here(ai, dest, previous_name="Zion National Park")

    assert "Day Trip" not in html


def test_build_getting_there_uses_group_base_name_when_final_destination_is_grouped() -> None:
    """GH #68 §4 open question #3: the departure leg after a group must be
    labeled from the shared base (where the traveler actually overnights),
    not from the grouped entry itself, even when it's the trip's last stop."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = _moab_group_destinations()
    dest_by_id = {d["id"]: d for d in destinations}
    canyonlands = dest_by_id["canyonlands"]  # final destination, group_with: moab
    ai = {
        "getting_there": {
            "route_summary": "Head toward Grand Junction for departure.",
            "route_options": [],
        }
    }
    trip_meta = {"return": "Grand Junction, CO airport"}

    html = assembler._build_getting_there(ai, canyonlands, trip_meta, dest_by_id=dest_by_id)

    assert "Moab" in html
    assert "Canyonlands National Park →" not in html


def test_build_getting_there_uses_own_name_when_ungrouped() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    dest = {"id": "santafe", "name": "Santa Fe"}
    ai = {"getting_there": {"route_summary": "Head to Albuquerque.", "route_options": []}}
    trip_meta = {"return": "Albuquerque, NM airport"}

    html = assembler._build_getting_there(ai, dest, trip_meta)

    assert "Santa Fe" in html


def test_build_getting_here_renders_en_route_pointer_when_deferred_and_empty() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._multi_site_base_owned_categories = frozenset()
    destinations = _moab_group_destinations()
    dest_by_id = {d["id"]: d for d in destinations}
    arches = dict(dest_by_id["arches"])
    arches["base_owned_categories"] = ["en_route_stop"]
    ai = {"getting_here": {"distance_miles": "5", "travel_time": "15 min", "en_route_stops": []}}

    html = assembler._build_getting_here(ai, arches, previous_name="Moab", dest_by_id=dest_by_id)

    assert "En-route stops" in html
    assert "En-route stops: see " in html
    assert '<a href="#section-moab">Moab</a>' in html


def test_assemble_full_moab_group_manifest_renders_expected_pointers_and_clustering() -> None:
    assembler = HTMLAssembler(config_path="config.yaml")
    trip = {
        "trip": {"title": "Moab Group Trip", "subtitle": "Test", "theme_color": "#C0623E"},
        "destinations": [
            {
                "id": "moab",
                "name": "Moab",
                "dates": "August 1-4, 2026",
                "lodging": {"name": "Moab Springs Ranch", "location": "Moab Springs Ranch, Moab, UT", "checkin_time": "4:00 PM"},
                "planning_links": [],
                "ai_content": {
                    "top_attractions": [],
                    "dinner_recommendations": [
                        {"name": "Moab Diner", "url": "https://www.moabdiner.example/", "description": "Classic diner."},
                    ],
                    "getting_here": {"en_route_stops": []},
                },
                "scenic_drives": [],
                "images": [],
                "cultural_events": {},
            },
            {
                "id": "arches",
                "name": "Arches National Park",
                "dates": "August 2, 2026",
                "group_with": "moab",
                "planning_links": [],
                "ai_content": {
                    "top_attractions": [
                        {"name": "Delicate Arch", "type": "hike", "description": "Iconic hike.", "url": "https://www.nps.gov/arch/delicate"},
                    ],
                    "dinner_recommendations": [],
                    "getting_here": {"distance_miles": "5", "travel_time": "15 min", "en_route_stops": []},
                },
                "scenic_drives": [],
                "images": [],
                "cultural_events": {},
            },
            {
                "id": "canyonlands",
                "name": "Canyonlands National Park",
                "dates": "August 3, 2026",
                "group_with": "moab",
                "planning_links": [],
                "ai_content": {
                    "top_attractions": [
                        {"name": "Grand View Point", "type": "viewpoint", "description": "Sweeping canyon views.", "url": "https://www.nps.gov/cany/grandview"},
                    ],
                    "dinner_recommendations": [],
                    "getting_here": {"distance_miles": "32", "travel_time": "40 min", "en_route_stops": []},
                },
                "scenic_drives": [],
                "images": [],
                "cultural_events": {},
            },
        ],
    }

    html = assembler.assemble(trip)

    # Grouped children no longer get their own nav-tab entry at all (dipstick60
    # review) -- their content is reachable by scrolling Moab's own section.
    assert 'class="tab-group"' not in html
    assert 'data-tab="section-arches"' not in html
    assert 'data-tab="section-canyonlands"' not in html
    assert 'data-tab="section-moab"' in html
    # Consolidated day-trip banner on both grouped children: centered,
    # linked back to Moab's section (no separate "Based from X (see Y)"
    # pointer line, and no restated lodging text -- the link is the
    # reference that matters).
    assert html.count('Day trip from <a href="#section-moab"') == 2
    assert html.count("Based at") == 0
    assert "class=\"group-lodging-pointer\"" not in html
    # Restaurant deferral pointer on both grouped children, base keeps its own card
    # -- only the destination name itself is the anchor (dipstick60 Bug 3).
    assert html.count("Dinner recommendations: see ") == 2
    assert html.count('<a href="#section-moab">Moab</a>') >= 2
    assert "Moab Diner" in html
    # Each grouped entry keeps its own genuinely distinct attractions
    assert "Delicate Arch" in html
    assert "Grand View Point" in html
    # Day-trip framing for both grouped entries
    assert html.count("Day Trip") == 2

    # GH #68 card-within-card hierarchy: Arches and Canyonlands no longer
    # get their own top-level <section> -- only Moab does. Their content
    # renders as nested .group-child-card blocks inside Moab's section.
    assert html.count('<section id="section-') == 1
    assert 'id="section-moab" class="dest-section"' in html
    assert html.count('id="section-arches" class="group-child-card"') == 1
    assert html.count('id="section-canyonlands" class="group-child-card"') == 1
    moab_start = html.index('id="section-moab"')
    moab_end = html.index("</section>", moab_start)
    arches_pos = html.index('id="section-arches"')
    canyonlands_pos = html.index('id="section-canyonlands"')
    assert moab_start < arches_pos < moab_end
    assert moab_start < canyonlands_pos < moab_end


def test_assemble_grouped_child_getting_here_uses_base_not_preceding_sibling() -> None:
    """GH #68 design doc §4 (Route/distance handling for grouped hops):
    'route/distance calculation for B should compute base->B (not
    previous-in-list->B, which could itself be another grouped sibling)'.

    Real dipstick62 output showed this still broken: Canyonlands (which
    renders after Arches in manifest order, both group_with: moab) had its
    'Getting Here' route computed as Arches -> Canyonlands instead of the
    correct Moab -> Canyonlands, because the previous-in-manifest-list
    destination was used instead of the group's shared lodging base."""
    assembler = HTMLAssembler(config_path="config.yaml")
    trip = {
        "trip": {"title": "Moab Group Trip", "subtitle": "Test", "theme_color": "#C0623E"},
        "destinations": [
            {
                "id": "moab",
                "name": "Moab",
                "dates": "August 1-4, 2026",
                "lodging": {"name": "Moab Springs Ranch", "location": "Moab Springs Ranch, Moab, UT", "checkin_time": "4:00 PM"},
                "planning_links": [],
                "ai_content": {
                    "top_attractions": [],
                    "dinner_recommendations": [],
                    "getting_here": {"en_route_stops": []},
                },
                "scenic_drives": [],
                "images": [],
                "cultural_events": {},
            },
            {
                "id": "arches",
                "name": "Arches National Park",
                "dates": "August 2, 2026",
                "group_with": "moab",
                "planning_links": [],
                "ai_content": {
                    "top_attractions": [
                        {"name": "Delicate Arch", "type": "hike", "description": "Iconic hike.", "url": "https://www.nps.gov/arch/delicate"},
                    ],
                    "dinner_recommendations": [],
                    "getting_here": {"distance_miles": "5", "travel_time": "15 min", "en_route_stops": []},
                },
                "scenic_drives": [],
                "images": [],
                "cultural_events": {},
            },
            {
                "id": "canyonlands",
                "name": "Canyonlands National Park",
                "dates": "August 3, 2026",
                "group_with": "moab",
                "planning_links": [],
                "ai_content": {
                    "top_attractions": [
                        {"name": "Grand View Point", "type": "viewpoint", "description": "Sweeping canyon views.", "url": "https://www.nps.gov/cany/grandview"},
                    ],
                    "dinner_recommendations": [],
                    "getting_here": {"distance_miles": "32", "travel_time": "40 min", "en_route_stops": []},
                },
                "scenic_drives": [],
                "images": [],
                "cultural_events": {},
            },
        ],
    }

    html = assembler.assemble(trip)

    # Canyonlands' route headline must originate from Moab (the shared
    # base), never from Arches (the sibling that happens to render first).
    # _short_place_name abbreviates "National Park" to "NP" in the
    # route-headline label.
    assert "Moab → Canyonlands NP" in html
    assert "Arches NP → Canyonlands NP" not in html

    # Arches, the first-rendered child, is also base->child (unchanged from
    # before, but confirms the fix didn't regress the already-correct case).
    assert "Moab → Arches NP" in html


def test_assemble_moab_group_suppresses_schedule_card_for_grouped_children() -> None:
    """Problem 1 (dipstick59, owner's words: 'I don't think the daily
    schedule makes sense for day trips, as Moab already dictates') -- a
    grouped entry must not render its own 'Possible Daily Schedule' card
    at all, even when its own ai_content has one; only the base's own
    schedule should render."""
    assembler = HTMLAssembler(config_path="config.yaml")
    trip = {
        "trip": {"title": "Moab Group Trip", "subtitle": "Test", "theme_color": "#C0623E"},
        "destinations": [
            {
                "id": "moab",
                "name": "Moab",
                "dates": "August 1-4, 2026",
                "lodging": {"name": "Moab Springs Ranch", "location": "Moab Springs Ranch, Moab, UT", "checkin_time": "4:00 PM"},
                "planning_links": [],
                "ai_content": {
                    "top_attractions": [],
                    "dinner_recommendations": [],
                    "possible_daily_schedule": [
                        {"day_label": "Day 1", "periods": [{"period": "morning", "summary": "Arrive in Moab."}]},
                    ],
                    "getting_here": {"en_route_stops": []},
                },
                "scenic_drives": [],
                "images": [],
                "cultural_events": {},
            },
            {
                "id": "arches",
                "name": "Arches National Park",
                "dates": "August 2, 2026",
                "group_with": "moab",
                "planning_links": [],
                "ai_content": {
                    "top_attractions": [
                        {"name": "Delicate Arch", "type": "hike", "description": "Iconic hike.", "url": "https://www.nps.gov/arch/delicate"},
                    ],
                    "dinner_recommendations": [],
                    "possible_daily_schedule": [
                        {"day_label": "Day 1", "periods": [{"period": "morning", "summary": "Hike to Delicate Arch."}]},
                    ],
                    "getting_here": {"distance_miles": "5", "travel_time": "15 min", "en_route_stops": []},
                },
                "scenic_drives": [],
                "images": [],
                "cultural_events": {},
            },
        ],
    }

    html = assembler.assemble(trip)

    # Exactly one schedule card in the whole document -- Moab's own.
    assert html.count('class="card schedule-card"') == 1
    assert "Arrive in Moab." in html
    # Arches' own schedule content must not render anywhere.
    assert "Hike to Delicate Arch." not in html


def test_assemble_moab_group_dedupes_base_attractions_against_grouped_child() -> None:
    """Problem 2 (dipstick59, owner's words: 'The big thing for the Moab
    situation is to avoid duplication') -- Moab's own AI-generated
    top_attractions must not re-list a landmark a grouped child already
    covers in its own nested card, even when worded identically."""
    assembler = HTMLAssembler(config_path="config.yaml")
    trip = {
        "trip": {"title": "Moab Group Trip", "subtitle": "Test", "theme_color": "#C0623E"},
        "destinations": [
            {
                "id": "moab",
                "name": "Moab",
                "dates": "August 1-4, 2026",
                "lodging": {"name": "Moab Springs Ranch", "location": "Moab Springs Ranch, Moab, UT", "checkin_time": "4:00 PM"},
                "planning_links": [],
                "ai_content": {
                    "top_attractions": [
                        {"name": "Delicate Arch", "type": "hike", "description": "Moab own suggestion duplicate.", "url": "https://x/delicate-moab"},
                        {"name": "Moab Giants Dinosaur Park", "type": "attraction", "description": "Genuinely Moab-only.", "url": "https://x/giants"},
                    ],
                    "dinner_recommendations": [],
                    "getting_here": {"en_route_stops": []},
                },
                "scenic_drives": [],
                "images": [],
                "cultural_events": {},
            },
            {
                "id": "arches",
                "name": "Arches National Park",
                "dates": "August 2, 2026",
                "group_with": "moab",
                "planning_links": [],
                "ai_content": {
                    "top_attractions": [
                        {"name": "Delicate Arch", "type": "hike", "description": "Arches own coverage of the arch.", "url": "https://www.nps.gov/arch/delicate"},
                    ],
                    "dinner_recommendations": [],
                    "getting_here": {"distance_miles": "5", "travel_time": "15 min", "en_route_stops": []},
                },
                "scenic_drives": [],
                "images": [],
                "cultural_events": {},
            },
        ],
    }

    html = assembler.assemble(trip)

    # "Delicate Arch" renders exactly once -- from Arches' own nested card,
    # not duplicated in Moab's own attraction list.
    assert html.count("Delicate Arch") == 1
    assert "Arches own coverage of the arch." in html
    assert "Moab own suggestion duplicate." not in html
    # Moab's genuinely distinct attraction is untouched by the filter.
    assert "Moab Giants Dinosaur Park" in html


def test_assemble_departure_card_renders_on_base_when_last_destination_is_grouped() -> None:
    """When the trip's final destination is itself a grouped (day-trip)
    entry, the trailing 'Departure Route Options' card must render on the
    group base's section (where the traveler actually overnights), not on
    the grouped child's now-nested card -- generalizing the existing
    base-tracking route-label fix (§4 open question #3) to *where the
    card renders*, now that grouped entries no longer have their own
    section to render it in."""
    assembler = HTMLAssembler(config_path="config.yaml")
    trip = {
        "trip": {"title": "Moab Group Trip", "subtitle": "Test", "theme_color": "#C0623E"},
        "destinations": [
            {
                "id": "moab",
                "name": "Moab",
                "dates": "August 1-4, 2026",
                "lodging": {"name": "Moab Springs Ranch", "location": "Moab Springs Ranch, Moab, UT", "checkin_time": "4:00 PM"},
                "planning_links": [],
                "ai_content": {
                    "top_attractions": [],
                    "dinner_recommendations": [],
                    "getting_here": {"en_route_stops": []},
                },
                "scenic_drives": [],
                "images": [],
                "cultural_events": {},
            },
            {
                "id": "canyonlands",
                "name": "Canyonlands National Park",
                "dates": "August 3, 2026",
                "group_with": "moab",
                "planning_links": [],
                "ai_content": {
                    "top_attractions": [],
                    "dinner_recommendations": [],
                    "getting_here": {"distance_miles": "32", "travel_time": "40 min", "en_route_stops": []},
                    "getting_there": {"route_summary": "Head toward Grand Junction for departure.", "route_options": []},
                },
                "scenic_drives": [],
                "images": [],
                "cultural_events": {},
            },
        ],
    }

    html = assembler.assemble(trip)

    assert "Departure Route Options" in html
    departure_pos = html.index("Departure Route Options")
    moab_start = html.index('id="section-moab"')
    moab_end = html.index("</section>", moab_start)
    # The departure card must land inside Moab's own <section>, after the
    # nested Canyonlands child card that Moab's section also contains.
    assert moab_start < departure_pos < moab_end
    canyonlands_pos = html.index('id="section-canyonlands"')
    assert canyonlands_pos < departure_pos


def test_build_group_child_card_omits_schedule_and_renders_nested_div() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._config = {}
    destinations = _moab_group_destinations()
    dest_by_id = {d["id"]: d for d in destinations}
    arches = dict(dest_by_id["arches"])
    arches["ai_content"] = {
        "top_attractions": [{"name": "Delicate Arch", "type": "hike", "description": "Iconic hike.", "url": "https://www.nps.gov/arch/delicate"}],
        "possible_daily_schedule": [{"day_label": "Day 1", "periods": [{"period": "morning", "summary": "Should never render."}]}],
        "getting_here": {"distance_miles": "5", "travel_time": "15 min", "en_route_stops": []},
    }

    html = assembler._build_group_child_card(arches, {}, "Moab", "Moab Springs Ranch, Moab, UT", "Arches National Park", dest_by_id)

    assert html.startswith('<div id="section-arches" class="group-child-card"')
    assert html.rstrip().endswith("</div>")
    assert "<section" not in html
    assert "Should never render." not in html
    assert "schedule-card" not in html
    assert "Delicate Arch" in html
    assert 'Day trip from <a href="#section-moab"' in html
    assert ">Moab</a>" in html
    assert "Based at" not in html
    assert "text-align:center" in html
    assert "class=\"group-lodging-pointer\"" not in html


def test_build_group_child_card_banner_is_centered_and_distinct_from_base() -> None:
    """Project-owner review feedback: the day-trip banner must (a) be
    visually distinct from a regular destination section/attraction card
    (no longer the same #faf7f2 as a plain attraction card, and no longer
    the plain --sandstone banner background either), and (b) be centered,
    not left-aligned."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._config = {}
    destinations = _moab_group_destinations()
    dest_by_id = {d["id"]: d for d in destinations}
    arches = dict(dest_by_id["arches"])
    arches["ai_content"] = {"top_attractions": [], "getting_here": {"en_route_stops": []}}

    html = assembler._build_group_child_card(arches, {}, "Moab", "Moab Springs Ranch, Moab, UT", "Arches National Park", dest_by_id)

    banner_start = html.index('class="group-child-banner"')
    banner_div = html[banner_start : html.index("</div>", banner_start)]
    assert "text-align:center" in banner_div
    assert "var(--sage)" in banner_div
    assert "#faf7f2" not in html  # not the plain attraction-card background
    assert "var(--sandstone)" not in html  # not the old plain banner background either


def test_build_group_child_card_banner_carries_the_base_section_link() -> None:
    """The link back to the base's section must live on the top banner row
    itself (dipstick review: "inject link on top row"), not only in a
    separate lower pointer line."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._config = {}
    destinations = _moab_group_destinations()
    dest_by_id = {d["id"]: d for d in destinations}
    arches = dict(dest_by_id["arches"])
    arches["ai_content"] = {"top_attractions": [], "getting_here": {"en_route_stops": []}}

    html = assembler._build_group_child_card(arches, {}, "Moab", "Moab Springs Ranch, Moab, UT", "Arches National Park", dest_by_id)

    banner_start = html.index('class="group-child-banner"')
    banner_div = html[banner_start : html.index("</div>", banner_start)]
    assert 'href="#section-moab"' in banner_div


def test_build_group_child_card_banner_omits_lodging_when_own_lodging_present() -> None:
    """The banner text is just "Day trip from X" regardless of lodging --
    no lodging name is ever folded in (project-owner review, 2026-08-19).
    Covers the case where the grouped entry overrides lodging itself (own
    `lodging` block present) to confirm that doesn't change the banner."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._config = {}
    destinations = _moab_group_destinations()
    dest_by_id = {d["id"]: d for d in destinations}
    arches = dict(dest_by_id["arches"])
    arches["lodging"] = {"name": "Arches Basecamp", "location": "Arches Basecamp, Moab, UT"}
    arches["ai_content"] = {"top_attractions": [], "getting_here": {"en_route_stops": []}}

    html = assembler._build_group_child_card(arches, {}, "Moab", "Moab Springs Ranch, Moab, UT", "Arches National Park", dest_by_id)

    assert 'Day trip from <a href="#section-moab"' in html
    assert ">Moab</a>" in html
    assert "Based at" not in html
    assert "Arches Basecamp" not in html
    assert "Moab Springs Ranch" not in html


def test_build_group_child_card_no_longer_renders_separate_lodging_pointer() -> None:
    """The old separate group-lodging-pointer paragraph must be gone from
    the grouped-child card entirely -- its distinct info (the lodging
    name) now lives in the consolidated banner instead."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._config = {}
    destinations = _moab_group_destinations()
    dest_by_id = {d["id"]: d for d in destinations}
    arches = dict(dest_by_id["arches"])
    arches["ai_content"] = {"top_attractions": [], "getting_here": {"en_route_stops": []}}

    html = assembler._build_group_child_card(arches, {}, "Moab", "Moab Springs Ranch, Moab, UT", "Arches National Park", dest_by_id)

    assert "class=\"group-lodging-pointer\"" not in html
    assert "(see Moab)" not in html


def test_group_child_covered_names_collects_child_name_attractions_and_drives() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    children = [
        {
            "name": "Arches National Park",
            "ai_content": {"top_attractions": [{"name": "Delicate Arch"}, {"name": "Landscape Arch"}]},
            "scenic_drives": [{"title": "Arches Scenic Drive"}],
        },
    ]

    covered = assembler._group_child_covered_names(children)

    assert "Arches National Park" in covered
    assert "Delicate Arch" in covered
    assert "Landscape Arch" in covered
    assert "Arches Scenic Drive" in covered


def test_dedupe_attractions_against_names_uses_fuzzy_match_and_preserves_order() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    attractions = [
        {"name": "Delicate Arch Trail"},  # fuzzy-duplicates "Delicate Arch"
        {"name": "Moab Giants Dinosaur Park"},
    ]

    kept = assembler._dedupe_attractions_against_names(attractions, ["Delicate Arch"])

    assert kept == [{"name": "Moab Giants Dinosaur Park"}]


def test_dedupe_attractions_against_names_no_covered_names_is_noop() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    attractions = [{"name": "Delicate Arch"}]

    assert assembler._dedupe_attractions_against_names(attractions, []) == attractions

def test_build_packing_summary_consolidates_differently_worded_same_advice() -> None:
    """dipstick58: 'layered clothing', 'layers for fluctuating temperatures',
    'layers for warmth', and 'light jacket' are the same actionable advice
    worded four different ways across independent per-destination AI
    generation -- the exact-string grouping this list previously used never
    caught it, so the packing summary listed four near-identical bullets
    instead of one consolidated one."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = [
        {"name": "Moab", "ai_content": {"expected_environment": {"what_to_pack": ["layered clothing"]}}},
        {"name": "Telluride", "ai_content": {"expected_environment": {"what_to_pack": ["layers for fluctuating temperatures"]}}},
        {"name": "Bryce Canyon National Park", "ai_content": {"expected_environment": {"what_to_pack": ["layers for warmth"]}}},
        {"name": "Santa Fe", "ai_content": {"expected_environment": {"what_to_pack": ["light jacket"]}}},
    ]

    html = assembler._build_packing_summary(destinations)

    assert html.count("<li>") == 1
    assert "Layers / light jacket (temperature swings)" in html
    for name in ("Moab", "Telluride", "Bryce Canyon National Park", "Santa Fe"):
        assert name in html


def test_build_packing_summary_consolidates_camera_dropping_qualifier() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = [
        {"name": "Zion National Park", "ai_content": {"expected_environment": {"what_to_pack": ["camera"]}}},
        {"name": "Telluride", "ai_content": {"expected_environment": {"what_to_pack": ["camera for fall foliage"]}}},
    ]

    html = assembler._build_packing_summary(destinations)

    assert html.count("<li>") == 1
    assert "<strong>Camera</strong>" in html
    assert "Zion National Park" in html and "Telluride" in html


def test_build_packing_summary_consolidates_hiking_footwear_but_keeps_walking_shoes_distinct() -> None:
    """Hiking boots/hiking shoes/sturdy hiking shoes are the same advice and
    should merge. 'Comfortable walking shoes' (a plaza-walking city) is
    genuinely different advice and must NOT merge into the hiking bucket --
    over-merging would misrepresent Santa Fe as needing trail-rated boots."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = [
        {"name": "Bryce Canyon National Park", "ai_content": {"expected_environment": {"what_to_pack": ["hiking boots"]}}},
        {"name": "St. George, Utah", "ai_content": {"expected_environment": {"what_to_pack": ["hiking shoes"]}}},
        {"name": "Zion National Park", "ai_content": {"expected_environment": {"what_to_pack": ["sturdy hiking shoes"]}}},
        {"name": "Santa Fe", "ai_content": {"expected_environment": {"what_to_pack": ["comfortable walking shoes"]}}},
    ]

    html = assembler._build_packing_summary(destinations)

    assert html.count("<li>") == 2
    assert "Hiking boots/shoes" in html
    assert "comfortable walking shoes" in html
    hiking_row = html.split("Hiking boots/shoes")[1].split("</li>")[0]
    assert "Santa Fe" not in hiking_row


def test_build_packing_summary_keeps_waterproof_jacket_distinct_from_generic_layers() -> None:
    """Waterproof jacket signals expected rain -- materially different
    packing reason than generic temperature-swing layers advice, so it must
    not collapse into the same bucket even though both mention 'jacket'."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    destinations = [
        {"name": "Pagosa Springs", "ai_content": {"expected_environment": {"what_to_pack": ["waterproof jacket"]}}},
        {"name": "Santa Fe", "ai_content": {"expected_environment": {"what_to_pack": ["light jacket"]}}},
    ]

    html = assembler._build_packing_summary(destinations)

    assert html.count("<li>") == 2
    assert "waterproof jacket" in html
    assert "Layers / light jacket (temperature swings)" in html


# ── Maps-corner-link icon (project owner ask: when an item has BOTH a real
# primary source URL and a separate maps_url, the maps_url was previously
# discarded entirely -- there was no way to reach it from the card). See
# HTMLAssembler._maps_corner_link_html and the `.map-corner-link` CSS rule
# in templates/v2.5_template.html. ─────────────────────────────────────────


def test_build_attractions_renders_maps_corner_link_when_distinct_from_primary_url() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Zion Narrows",
                "type": "hike",
                "description": "Iconic slot canyon hike.",
                "url": "https://www.nps.gov/zion/planyourvisit/narrows.htm",
                "maps_url": "https://www.google.com/maps/place/The+Narrows/@37.2982,-112.9481,15z",
            }
        ]
    }

    html = assembler._build_attractions(ai, drives=[], dest_name="Zion National Park")

    assert "nps.gov" in html
    assert 'class="badge badge-map"' in html
    assert "maps/place/The+Narrows" in html


def test_build_attractions_non_seed_with_only_maps_fallback_is_absent() -> None:
    """Real example from a validation run (Bryce Canyon): "Sunrise Point" and
    "Inspiration Point" are non-seed viewpoints with only a Google Maps
    search-by-name fallback, never a real verified source URL. Under the
    verified-link-or-seed policy (2026-08-17), a maps-search fallback is
    explicitly not "verified" -- this item is removed from the itinerary
    entirely, not rendered with the caution badge and map icon it used to
    get."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Sunrise Point",
                "type": "attraction",
                "description": "A viewpoint offering sweeping views of the amphitheater and hoodoos below.",
                "maps_url": "https://www.google.com/maps/search/?api=1&query=Sunrise+Point+Bryce+Canyon",
            }
        ]
    }

    html = assembler._build_attractions(ai, drives=[], dest_name="Bryce Canyon National Park")

    assert "Sunrise Point" not in html
    assert "badge-caution" not in html


def test_build_attractions_renders_maps_corner_link_when_no_primary_url() -> None:
    """A seed item with no verified source at all (the 'Unverified' caution
    badge case) is exactly where a map icon is most useful, not least --
    it must not be suppressed just because there's no primary link to
    compare it against. Seeded here (is_seed=True) since, per the
    verified-link-or-seed policy (2026-08-17), a non-seed item in this
    situation is now removed entirely rather than rendered (see
    test_build_attractions_non_seed_with_only_maps_fallback_is_absent)."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Sunrise Point",
                "type": "attraction",
                "description": "A viewpoint offering sweeping views of the amphitheater and hoodoos below.",
                "maps_url": "https://www.google.com/maps/search/?api=1&query=Sunrise+Point+Bryce+Canyon",
                "is_seed": True,
            }
        ]
    }

    html = assembler._build_attractions(ai, drives=[], dest_name="Bryce Canyon National Park")

    assert "badge-caution" in html
    assert 'class="badge badge-map"' in html
    assert "Sunrise+Point" in html


def test_build_attractions_omits_maps_corner_link_when_no_maps_url() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Zion Narrows",
                "type": "hike",
                "description": "Iconic slot canyon hike.",
                "url": "https://www.nps.gov/zion/planyourvisit/narrows.htm",
            }
        ]
    }

    html = assembler._build_attractions(ai, drives=[], dest_name="Zion National Park")

    assert "nps.gov" in html
    assert "badge-map" not in html


def test_build_attractions_omits_maps_corner_link_when_redundant_with_primary_url() -> None:
    """The primary URL already IS the maps_url (or another Maps URL) --
    a second map icon pointing at essentially the same place is redundant."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Same Place",
                "type": "attraction",
                "description": "desc",
                "url": "https://www.google.com/maps/place/Same/@1,2,3z",
                "maps_url": "https://www.google.com/maps/place/Same/@1,2,3z",
            }
        ]
    }

    html = assembler._build_attractions(ai, drives=[], dest_name="Anywhere")

    assert "badge-map" not in html


def test_build_attractions_renders_maps_corner_link_for_search_fallback_maps_url() -> None:
    """A generic text-query google.com/maps/search fallback is ambiguous as a
    *primary* attribution link (see
    test_build_restaurants_prefers_discovered_url_over_maps_query -- the real
    nps.gov page must still win that slot), but it's still shown here as a
    secondary "locate on a map" convenience icon: excluding it entirely made
    this feature almost never fire on real data, since nearly every maps_url
    this pipeline attaches is exactly this kind of search-fallback URL."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "top_attractions": [
            {
                "name": "Weeping Rock",
                "type": "hike",
                "description": "Short trail to a shaded alcove.",
                "url": "https://www.nps.gov/zion/planyourvisit/weeping-rock.htm",
                "maps_url": "https://www.google.com/maps/search/?api=1&query=Weeping+Rock+Zion+National+Park",
            }
        ]
    }

    html = assembler._build_attractions(ai, drives=[], dest_name="Zion National Park")

    assert "nps.gov" in html
    assert '<a href="https://www.nps.gov' in html
    assert 'class="badge badge-map"' in html
    assert "google.com/maps/search" in html


def test_maps_corner_link_html_renders_when_primary_url_is_empty() -> None:
    """Direct unit test on the helper itself: an empty primary_url (the
    'Unverified' caution-badge case -- nothing else clickable on the card)
    must not suppress the map icon. The redundancy checks (identical to
    primary, primary already a maps URL) only make sense when there IS a
    primary link; with none, showing the map icon is the single most
    useful case for this affordance, not a case to exclude."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    item = {"maps_url": "https://www.google.com/maps/search/?api=1&query=Some+Place"}

    html = assembler._maps_corner_link_html(item, "")

    assert 'class="badge badge-map"' in html
    assert "Some+Place" in html


def test_maps_corner_link_html_omits_when_no_maps_url_and_no_primary() -> None:
    """No maps_url at all -- nothing to show, regardless of primary_url."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    item: dict = {}

    assert assembler._maps_corner_link_html(item, "") == ""
    assert assembler._maps_corner_link_html(item, "https://example.com") == ""


def test_build_restaurants_with_only_maps_fallback_no_seed_exception_is_absent() -> None:
    """A restaurant with no verified primary source and only a maps_url
    fallback used to still render with the "Unverified" caution badge and a
    map icon. Under the verified-link-or-seed policy (2026-08-17), restaurants
    have no seed concept anywhere in this codebase, so there is no exception
    that keeps an unverified restaurant visible -- it must be absent
    entirely, map icon included."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "dinner_recommendations": [
            {
                "name": "Painted Pony",
                "maps_url": "https://www.google.com/maps/dir/?api=1&destination=Painted+Pony+St.+George+UT",
                "cuisine": "American",
                "description": "Fine dining.",
            }
        ]
    }

    html = assembler._build_restaurants(ai, dest_name="St. George, Utah")

    assert "Painted Pony" not in html
    assert "badge-caution" not in html
    assert "badge-map" not in html


def test_build_restaurants_renders_maps_corner_link_when_distinct_from_primary_url() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "dinner_recommendations": [
            {
                "name": "Painted Pony",
                "url": "https://paintedponyrestaurant.com/",
                "maps_url": "https://www.google.com/maps/place/Painted+Pony/@37.09,-113.58,15z",
                "cuisine": "American",
                "description": "Fine dining.",
            }
        ]
    }

    html = assembler._build_restaurants(ai, dest_name="St. George, Utah")

    assert "paintedponyrestaurant.com" in html
    assert 'class="badge badge-map"' in html
    assert "maps/place/Painted+Pony" in html


def test_build_restaurants_omits_maps_corner_link_when_no_maps_url() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "dinner_recommendations": [
            {
                "name": "Painted Pony",
                "url": "https://paintedponyrestaurant.com/",
                "cuisine": "American",
                "description": "Fine dining.",
            }
        ]
    }

    html = assembler._build_restaurants(ai, dest_name="St. George, Utah")

    assert "paintedponyrestaurant.com" in html
    assert "badge-map" not in html


def test_build_getting_here_renders_maps_corner_link_when_distinct_from_primary_url() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive to Santa Fe.",
            "distance_miles": "60",
            "travel_time": "1h 15m",
            "en_route_stops": [
                {
                    "name": "El Rito",
                    "description": "Historic stop option.",
                    "url": "https://example.com/el-rito-visitor-info",
                    "maps_url": "https://www.google.com/maps/place/El+Rito/@36.35,-106.19,15z",
                }
            ],
        }
    }
    dest = {"name": "Santa Fe"}

    html = assembler._build_getting_here(ai, dest, previous_name="Albuquerque")

    assert "example.com/el-rito-visitor-info" in html
    assert 'class="badge badge-map"' in html
    assert "maps/place/El+Rito" in html


def test_build_getting_here_omits_maps_corner_link_when_redundant_with_primary_url() -> None:
    """No distinct maps_url here -- the primary link IS the maps fallback
    (_select_preferred_external_link's maps-fallback case), so a second map
    icon would point at the same place. Mirrors
    test_build_getting_here_falls_back_to_maps_url_when_canonical_missing."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {
        "getting_here": {
            "route_summary": "Drive to Santa Fe.",
            "distance_miles": "60",
            "travel_time": "1h 15m",
            "en_route_stops": [
                {
                    "name": "El Rito",
                    "description": "Historic stop option.",
                    "url": "",
                    "maps_url": "https://www.google.com/maps/place/El+Rito/@36.35,-106.19,15z",
                }
            ],
        }
    }
    dest = {"name": "Santa Fe"}

    html = assembler._build_getting_here(ai, dest, previous_name="Albuquerque")

    assert "El Rito" in html
    assert "badge-map" not in html


def test_template_drive_popup_offers_distinct_route_map_icon() -> None:
    """Project owner separately called out 'No maps offered on Scenic Drive
    popups' -- the popup already had a plain 'Route Map' text link
    (desc.route_map_url) but no map-icon convention matching the rest of the
    UI, and no guard against duplicating an identical 'More Info' link. This
    checks both: the 🗺️ icon convention is present, and it's gated on the
    route map genuinely differing from the info link (see openDriveInfo in
    v2.5_template.html)."""
    template_text = TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "desc.route_map_url && desc.route_map_url !== desc.url" in template_text
    assert "🗺️ Route Map" in template_text


def test_lodging_card_renders_confirmation_website_and_checkin() -> None:
    """The card is the traveler-facing home for booking details that have no
    place in a header pill -- notably the confirmation number."""
    assembler = HTMLAssembler(config_path="config.yaml")
    dest = {
        "id": "zion",
        "name": "Zion National Park",
        "lodging": {
            "name": "Zion Lodge",
            "location": "Zion Lodge, Springdale, UT",
            "checkin_time": "4:00 PM",
            "confirmation_number": "ZL-4471902",
            "website": "https://www.zionlodge.com/",
        },
    }

    html = assembler._build_lodging_card(dest)

    assert "<details" in html and 'class="lodging-card"' in html
    assert "Lodging — Zion Lodge" in html
    assert "ZL-4471902" in html
    assert "4:00 PM" in html
    assert 'href="https://www.zionlodge.com/"' in html
    assert 'target="_blank" rel="noopener"' in html
    # Collapsed by default -- booking details are desk-time, not reading-time.
    assert "<details open" not in html


def test_lodging_card_omits_location_even_though_it_survives_redaction() -> None:
    """lodging.location is a geocoding/routing anchor that is deliberately
    never redacted (main._resolve_privacy_redaction). Printing it in the card
    would put a street address on the page in every build, including redacted
    ones, defeating the rest of the lodging redaction."""
    assembler = HTMLAssembler(config_path="config.yaml")
    dest = {
        "id": "zion",
        "lodging": {
            "name": "Zion Lodge",
            "location": "1 Zion Lodge Rd, Springdale, UT",
            "confirmation_number": "ZL-4471902",
        },
    }

    html = assembler._build_lodging_card(dest)

    assert "Springdale" not in html
    assert "Zion Lodge Rd" not in html


def test_lodging_card_absent_entirely_for_redacted_build() -> None:
    """After main._apply_privacy_redaction blanks name/website/confirmation,
    nothing renders -- not even a greyed placeholder. A visible 'Lodging'
    affordance would still announce a booked room at this stop on these
    dates, which is most of what redaction protects."""
    assembler = HTMLAssembler(config_path="config.yaml")
    dest = {
        "id": "zion",
        "lodging": {
            "name": "",
            "website": "",
            "confirmation_number": "",
            # Both survive redaction by design; neither may resurrect the card.
            "location": "Zion Lodge, Springdale, UT",
            "checkin_time": "4:00 PM",
        },
    }

    assert assembler._build_lodging_card(dest) == ""


def test_lodging_card_absent_when_destination_owns_no_lodging() -> None:
    """Grouped day-trip children defer to their base and must not restate its
    lodging -- the banner's link back to the base section is the reference
    that matters (6da6fd9)."""
    assembler = HTMLAssembler(config_path="config.yaml")

    assert assembler._build_lodging_card({"id": "arches", "group_with": "moab"}) == ""


def test_lodging_card_escapes_untrusted_field_text() -> None:
    """Confirmation codes and property names arrive from parsed email in the
    ingestion path, so they are untrusted input, not hand-authored manifest
    text."""
    assembler = HTMLAssembler(config_path="config.yaml")
    dest = {
        "id": "zion",
        "lodging": {
            "name": '<script>alert(1)</script>',
            "confirmation_number": '"><img src=x onerror=alert(1)>',
        },
    }

    html = assembler._build_lodging_card(dest)

    assert "<script>" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html


def test_drive_description_kept_when_matching_attraction_is_dropped_for_no_url() -> None:
    """An attraction that never renders must not suppress a drive's entry.

    Regression: prod run 20260820T050108 failed validation with "Drive modal
    buttons with no DRIVE_DESCRIPTIONS entry: ['Arches National Park Scenic
    Drive']". _build_attractions skips an attraction with no usable URL and so
    never records it in rendered_attraction_names -- meaning it never
    suppresses the drive's button. But _build_drive_descriptions deduped
    against the RAW top_attractions list, so the dropped attraction still
    suppressed the ENTRY, leaving a button that opens an empty modal.
    """
    assembler = HTMLAssembler(config_path="config.yaml")
    destinations = [
        {
            "name": "Moab",
            "ai_content": {
                "top_attractions": [
                    # Same name as the drive, but no URL of any kind -- pruned
                    # by the verified-link-or-seed policy before rendering.
                    {"name": "Arches National Park Scenic Drive", "url": "", "maps_url": ""},
                ]
            },
            "scenic_drives": [
                {
                    "title": "Arches National Park Scenic Drive",
                    "description": "A paved route climbing past Balanced Rock.",
                    "category": "scenic_drive",
                }
            ],
        }
    ]

    assembler._rendered_drive_titles = {"Arches National Park Scenic Drive"}
    dd = assembler._build_drive_descriptions(destinations)

    assert "Arches National Park Scenic Drive" in dd, (
        "a drive that rendered a button must get an entry to fill its modal"
    )


def test_drive_description_still_suppressed_when_attraction_does_render() -> None:
    """The original dedup must survive: a drive sharing its name with an
    attraction that DOES render gets no button, so it must get no entry either
    (the inverse orphan -- an entry with no button -- is also a hard error).

    Expressed against the new contract: _build_attractions rendered no button
    for this drive, so its title never entered _rendered_drive_titles."""
    assembler = HTMLAssembler(config_path="config.yaml")
    assembler._rendered_drive_titles = set()
    destinations = [
        {
            "name": "Moab",
            "ai_content": {
                "top_attractions": [
                    {
                        "name": "Potash Road",
                        "url": "https://www.blm.gov/visit/potash-road",
                    },
                ]
            },
            "scenic_drives": [
                {"title": "Potash Road", "description": "Petroglyphs along the Colorado."},
            ],
        }
    ]

    dd = assembler._build_drive_descriptions(destinations)

    assert "Potash Road" not in dd


def test_transportation_pills_render_by_type_like_the_nps_pill() -> None:
    """Legs render as header chips labelled by TYPE, styled as notion-header-btn
    exactly like the NPS/Weather pills, linking to the carrier."""
    assembler = HTMLAssembler(config_path="config.yaml")
    dest = {
        "id": "vegas",
        "transportation": [
            {"type": "plane", "provider": "United", "label": "UA 1234 SFO→LAS",
             "confirmation_number": "XR7Q2M", "website": "https://www.united.com/"},
            {"type": "train", "provider": "Amtrak", "confirmation_number": "AMT-889",
             "website": "https://www.amtrak.com/"},
            {"type": "car", "provider": "Hertz", "confirmation_number": "H99120",
             "website": "https://www.hertz.com/"},
        ],
    }

    pills = assembler._build_transportation_pills(dest)
    html = "".join(pills)

    assert len(pills) == 3
    assert "Flight" in html and "Train" in html and "Rental Car" in html
    assert html.count('class="notion-header-btn"') == 3
    assert 'href="https://www.hertz.com/"' in html
    assert html.count('target="_blank" rel="noopener"') == 3


def test_transportation_pill_carries_confirmation_in_its_tooltip() -> None:
    """A chip has no room for the booking details, but the confirmation code is
    the part worth keeping, so it rides in the title attribute."""
    assembler = HTMLAssembler(config_path="config.yaml")
    dest = {"id": "v", "transportation": [
        {"type": "plane", "provider": "United", "label": "UA 1234",
         "depart": "SFO 8:15 AM", "confirmation_number": "XR7Q2M",
         "website": "https://www.united.com/"},
    ]}

    html = "".join(assembler._build_transportation_pills(dest))

    assert "Confirmation XR7Q2M" in html
    assert "United" in html and "SFO 8:15 AM" in html


def test_transportation_pill_without_website_is_a_span_not_a_dead_link() -> None:
    """A leg with nowhere to click still renders -- the confirmation code
    matters even without a URL -- but must not be an anchor to nothing."""
    assembler = HTMLAssembler(config_path="config.yaml")
    dest = {"id": "v", "transportation": [
        {"type": "train", "provider": "Amtrak", "confirmation_number": "AMT-889"},
    ]}

    html = "".join(assembler._build_transportation_pills(dest))

    assert "<span" in html and "<a " not in html
    assert "AMT-889" in html


def test_transportation_pills_absent_after_redaction() -> None:
    assembler = HTMLAssembler(config_path="config.yaml")

    assert assembler._build_transportation_pills({"id": "v", "transportation": []}) == []
    assert assembler._build_transportation_pills({"id": "v"}) == []


def test_transportation_pill_unknown_type_falls_back_to_travel() -> None:
    assembler = HTMLAssembler(config_path="config.yaml")
    dest = {"id": "v", "transportation": [{"type": "hovercraft", "confirmation_number": "Q1"}]}

    html = "".join(assembler._build_transportation_pills(dest))

    assert "Travel" in html


def test_transportation_pill_escapes_untrusted_email_text() -> None:
    assembler = HTMLAssembler(config_path="config.yaml")
    dest = {"id": "v", "transportation": [
        {"type": "plane", "provider": '"><script>alert(1)</script>',
         "confirmation_number": "A1", "website": "https://x.example/"},
    ]}

    html = "".join(assembler._build_transportation_pills(dest))

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_transportation_pills_appear_in_the_header_actions_row() -> None:
    """End-to-end: the pill must reach _build_header_links alongside NPS."""
    assembler = HTMLAssembler(config_path="config.yaml")
    dest = {
        "id": "zion", "lat": 37.2982, "lng": -113.0263,
        "transportation": [{"type": "car", "provider": "Hertz",
                            "website": "https://www.hertz.com/"}],
    }

    html = assembler._build_header_links([], nps_code="zion", dest=dest, attractions=[])

    assert "Rental Car" in html
    assert "NPS" in html


def test_drive_entries_match_rendered_buttons_across_destinations() -> None:
    """The invariant the validator enforces, exercised end to end.

    Reproduces the 2026-08-19 prod failure shape: Moab carries the drive as
    "Arches National Park Scenic Drive" while Arches National Park carries the
    same real drive as "Arches Scenic Drive". Because the titles differ, no
    amount of aligning per-destination dedup filters can reconcile the two
    sides -- the entry set must be DERIVED from the buttons.
    """
    import re

    assembler = HTMLAssembler(config_path="config.yaml")
    assembler._rendered_drive_titles = set()

    destinations = [
        {
            "id": "moab", "name": "Moab",
            "ai_content": {"top_attractions": [
                {"name": "Sand Flats Recreation Area", "url": "https://example.invalid/sf"},
            ]},
            "scenic_drives": [
                {"title": "Arches National Park Scenic Drive", "description": "Paved climb."},
            ],
        },
        {
            "id": "arches", "name": "Arches National Park",
            "ai_content": {"top_attractions": [
                # Duplicates the drive below -- its card is dropped, so it must
                # produce no button AND no entry.
                {"name": "Arches Scenic Drive", "url": "https://example.invalid/asd"},
            ]},
            "scenic_drives": [
                {"title": "Arches Scenic Drive", "description": "Same road, other name."},
            ],
        },
    ]

    rendered = ""
    for dest in destinations:
        rendered += assembler._build_attractions(
            dest["ai_content"], dest["scenic_drives"], dest["name"], dest=dest,
        )

    dd = assembler._build_drive_descriptions(destinations)
    buttons = set(re.findall(r'data-drive-title="([^"]+)"', rendered))

    assert buttons == set(dd), (
        f"buttons {sorted(buttons)} != entries {sorted(dd)} -- "
        "an orphan button opens an empty modal; an orphan key is unreachable"
    )
    # The duplicated one produced neither; the distinct one produced both.
    assert "Arches Scenic Drive" not in dd
    assert "Arches National Park Scenic Drive" in dd


def test_trip_transportation_renders_under_the_overview_map() -> None:
    """Trip-wide legs render as chips beneath the route overview map, with
    their own light-background style -- .notion-header-btn is white-on-
    translucent for the dark hero image and would be invisible there."""
    assembler = HTMLAssembler(config_path="config.yaml")
    trip = {"trip": {"transportation": [
        {"type": "plane", "provider": "Example Air", "label": "SEA-LAS",
         "confirmation_number": "AAA111", "website": "https://air.example/"},
        {"type": "car", "provider": "Example Rentals", "confirmation_number": "BBB222"},
    ]}}

    html = assembler._build_trip_transportation(trip)

    assert 'class="trip-transport"' in html
    assert html.count("trip-transport-btn") == 2
    assert "Flight" in html and "Rental Car" in html
    # The record locator is shown, not hidden in a tooltip: this row is where a
    # traveler looks for it before leaving.
    assert "AAA111" in html and "BBB222" in html
    assert 'href="https://air.example/"' in html
    # No website -> a span, never an anchor to nothing.
    assert "<span class=\"trip-transport-btn\"" in html


def test_trip_transportation_absent_when_redacted_or_empty() -> None:
    assembler = HTMLAssembler(config_path="config.yaml")

    assert assembler._build_trip_transportation({"trip": {"transportation": []}}) == ""
    assert assembler._build_trip_transportation({"trip": {}}) == ""
    assert assembler._build_trip_transportation({}) == ""


def test_trip_transportation_escapes_untrusted_email_text() -> None:
    assembler = HTMLAssembler(config_path="config.yaml")
    trip = {"trip": {"transportation": [
        {"type": "plane", "label": "<script>alert(1)</script>", "confirmation_number": "A1"},
    ]}}

    html = assembler._build_trip_transportation(trip)

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_booked_leg_types_cover_carried_travel() -> None:
    """A customer-arranged cruise is a BOOKED leg -- the traveler holds a
    confirmation -- and unrelated to transit routing, which concerns services
    nobody has bought. Before these types it had to be entered as "other" and
    rendered as a generic "Travel" chip, losing the detail that makes it
    legible."""
    assembler = HTMLAssembler(config_path="config.yaml")
    legs = [
        {"type": t, "provider": "Example Line", "confirmation_number": f"C{i}"}
        for i, t in enumerate(("ship", "ferry", "bus", "shuttle"))
    ]

    html = assembler._build_trip_transportation({"trip": {"transportation": legs}})

    assert "Cruise" in html
    assert "Ferry" in html
    assert "Bus" in html
    assert "Shuttle" in html
    # None fell through to the fallback. Checked via the fallback's ICON rather
    # than the word "Travel", which is also the section's own label.
    assert "\U0001f9f3" not in html


def test_unknown_booked_leg_type_still_renders_its_details() -> None:
    """The fallback exists so an unrecognized booking keeps its confirmation
    rather than being dropped."""
    assembler = HTMLAssembler(config_path="config.yaml")

    html = assembler._build_trip_transportation(
        {"trip": {"transportation": [{"type": "hovercraft", "confirmation_number": "Q1"}]}}
    )

    assert "Travel" in html
    assert "Q1" in html


def test_full_route_map_link_is_not_in_the_nav_strip() -> None:
    """The button used to be the last flex item in the nav's overflow-x-auto
    row with margin-left:auto, so with enough stops it straddled that
    container's clip edge -- 67px cut off at 1280px with 8 stops, reachable
    only by horizontal scrolling nobody thinks to try. It now renders beside
    the Route Overview heading, so nothing in the strip can be clipped."""
    assembler = HTMLAssembler(config_path="config.yaml")
    destinations = [
        {"id": "zion", "name": "Zion National Park", "lat": 37.2, "lng": -113.0},
        {"id": "moab", "name": "Moab", "lat": 38.5, "lng": -109.5},
    ]

    tabs = assembler._build_nav_tabs(destinations, {})

    assert "map-tab-btn" not in tabs
    assert tabs.count("tab-btn") == 2


def test_route_map_link_renders_a_real_anchor() -> None:
    assembler = HTMLAssembler(config_path="config.yaml")
    destinations = [
        {"id": "zion", "name": "Zion National Park", "lat": 37.2, "lng": -113.0},
        {"id": "moab", "name": "Moab", "lat": 38.5, "lng": -109.5},
    ]

    link = assembler._build_route_map_link(destinations, {})

    assert 'class="map-tab-btn"' in link
    assert link.startswith("<a href=")
    assert 'target="_blank" rel="noopener"' in link
    assert "Full Route Map" in link


def test_route_map_link_is_empty_when_there_is_no_route() -> None:
    """An anchor to nothing is worse than no button: it looks live and isn't."""
    assembler = HTMLAssembler(config_path="config.yaml")

    assert assembler._build_route_map_link([], {}) == ""


def test_template_has_exactly_one_route_map_link_slot() -> None:
    """The assembler replaces this placeholder unconditionally. Zero slots means
    the button silently disappears from every build; two means one is left as a
    raw HTML comment in the output."""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    assert template.count("<!--ROUTE_MAP_LINK-->") == 1
    assert template.count('class="route-overview-head"') == 1


def test_previous_lodging_stop_skips_day_trips() -> None:
    """Real December itinerary: the final leg was labelled "Leiper's Fork ->
    Asheville" because the day trip was simply the last entry in the list,
    while the prose beneath it correctly read "Drive from Old Hickory". The
    traveler drives on from the base they slept at."""
    from generator.html_assembler import HTMLAssembler

    destinations = [
        {"id": "oldhickory", "name": "Old Hickory, Tennessee"},
        {"id": "nashville", "name": "Nashville, Tennessee", "group_with": "oldhickory"},
        {"id": "leipers_fork", "name": "Leiper's Fork, Tennessee", "group_with": "oldhickory"},
        {"id": "asheville", "name": "Asheville, North Carolina"},
    ]

    previous = HTMLAssembler._previous_lodging_stop(destinations, 3)

    assert previous is not None
    assert previous["name"] == "Old Hickory, Tennessee"


def test_previous_lodging_stop_is_none_for_the_first_stop() -> None:
    """No earlier lodging stop means the leg starts at the departure gateway."""
    from generator.html_assembler import HTMLAssembler

    destinations = [{"id": "oldhickory", "name": "Old Hickory, Tennessee"}]

    assert HTMLAssembler._previous_lodging_stop(destinations, 0) is None


def test_previous_lodging_stop_returns_the_immediate_predecessor_when_ungrouped() -> None:
    from generator.html_assembler import HTMLAssembler

    destinations = [
        {"id": "moab", "name": "Moab, Utah"},
        {"id": "telluride", "name": "Telluride, Colorado"},
    ]

    assert HTMLAssembler._previous_lodging_stop(destinations, 1)["name"] == "Moab, Utah"


# ── GH #2 Phase 1: transit options rendering ──────────────────────────────

def _transit_ai(transit_options, **getting_here):
    gh = {"route_summary": "By rail.", "travel_time": "3 hours"}
    gh.update(getting_here)
    gh["transit_options"] = transit_options
    return {"getting_here": gh}


_FORMAT_A = {
    "has_transit": True,
    "source": "ai",
    "confidence": "unverified",
    "options": [
        {
            "mode": "bus",
            "label": "Regional bus via Panguitch",
            "duration": "3-4 hours",
            "transfers": 1,
            "notes": "Runs daily in peak season.",
            "booking_hint": "Search 'Bryce to Capitol Reef bus' for schedules.",
        }
    ],
    "fallback": "Driving remains the most reliable option on this corridor.",
}

_FORMAT_B = {
    "has_transit": False,
    "honest_assessment": "No scheduled public transit connects Bryce Canyon to Capitol Reef.",
    "local_tip": "Springdale outfitters run point-to-point shuttles on request.",
}


def test_transit_card_renders_format_a_options() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_getting_here(
        _transit_ai(_FORMAT_A), {"name": "Capitol Reef"}, previous_name="Bryce Canyon"
    )
    assert "PUBLIC TRANSPORT OPTIONS" in html
    assert "Regional bus via Panguitch" in html
    assert "3-4 hours" in html
    assert "1 transfer" in html
    assert "Runs daily in peak season." in html
    assert "Driving remains the most reliable option" in html


def test_transit_card_renders_format_b_honest_assessment() -> None:
    """The honest negative is a product surface, not an empty card."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_getting_here(
        _transit_ai(_FORMAT_B), {"name": "Capitol Reef"}, previous_name="Bryce Canyon"
    )
    assert "No scheduled public transit connects" in html
    assert "point-to-point shuttles" in html
    assert "PUBLIC TRANSPORT OPTIONS" not in html


def test_unverified_badge_shown_unless_api_verified() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_getting_here(
        _transit_ai(_FORMAT_A), {"name": "Capitol Reef"}, previous_name="Bryce Canyon"
    )
    assert "Unverified" in html
    assert "AI-suggested and unverified" in html

    verified = dict(_FORMAT_A, confidence="api_verified", source="google_directions")
    html = assembler._build_getting_here(
        _transit_ai(verified), {"name": "Capitol Reef"}, previous_name="Bryce Canyon"
    )
    assert "Unverified" not in html
    assert "AI-suggested and unverified" not in html


def test_duration_badge_survives_an_empty_distance() -> None:
    """The rendering trap in multimodal-routing.md 4.2: the badge row used to
    be gated on distance AND duration, so a transit leg with no road mileage
    silently lost the one figure it does have."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_getting_here(
        _transit_ai(_FORMAT_A, distance_miles=""),
        {"name": "Capitol Reef"},
        previous_name="Bryce Canyon",
    )
    assert "badge-time" in html
    assert "3 hours" in html
    assert "badge-distance" not in html


def test_transfer_badge_substitutes_for_distance_on_a_transit_leg() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    html = assembler._build_getting_here(
        _transit_ai(_FORMAT_A, distance_miles=""),
        {"name": "Capitol Reef"},
        previous_name="Bryce Canyon",
    )
    assert "badge-transfers" in html


def test_distance_badge_still_renders_without_a_duration() -> None:
    """The same gate, the other way round."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {"getting_here": {"distance_miles": "212", "travel_time": ""}}
    html = assembler._build_getting_here(ai, {"name": "Moab"}, previous_name="Bryce Canyon")
    assert "212 mi" in html
    assert "badge-time" not in html


def test_every_transit_prose_field_is_escaped() -> None:
    """design.md 4.5 item 11 records route_summary being interpolated raw one
    function away from an identical escaped line. Do not extend that."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    hostile = "<script>alert('x')</script>"
    payload = {
        "has_transit": True,
        "confidence": "unverified",
        "options": [{
            "mode": "bus",
            "label": hostile,
            "duration": hostile,
            "notes": hostile,
            "booking_hint": hostile,
        }],
        "fallback": hostile,
    }
    html = assembler._build_getting_here(
        _transit_ai(payload), {"name": "Capitol Reef"}, previous_name="Bryce Canyon"
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html

    html_b = assembler._build_getting_here(
        _transit_ai({"has_transit": False, "honest_assessment": hostile, "local_tip": hostile}),
        {"name": "Capitol Reef"},
        previous_name="Bryce Canyon",
    )
    assert "<script>" not in html_b


def test_a_leg_with_no_transit_options_renders_exactly_as_before() -> None:
    """The default path must be untouched: no transit markup at all."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {"getting_here": {"route_summary": "US-89 to UT-12.", "distance_miles": "95",
                           "travel_time": "2 hrs 15 min"}}
    html = assembler._build_getting_here(ai, {"name": "Bryce"}, previous_name="Zion")
    assert "transit-options" not in html
    assert "Unverified" not in html
    assert "95 mi" in html and "2 hrs 15 min" in html


def test_transit_leg_maps_url_is_transit_mode_without_waypoints() -> None:
    """multimodal-routing.md 4.3: an early return past the waypoint block.
    Transit mode rejects waypoints outright -- Google returns 'could not
    calculate transit directions' and the link is dead."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    dest = {"name": "Capitol Reef", "_transport_mode": "transit"}
    stops = [{"name": "Escalante", "geocode_lat": 37.7, "geocode_lng": -111.6}]

    url = assembler._build_route_gmaps_url("Bryce Canyon", dest, stops)

    assert "travelmode=transit" in url
    assert "waypoints=" not in url
    assert "destination=Capitol%20Reef" in url


def test_mixed_leg_keeps_driving_directions_and_its_waypoints() -> None:
    """Under `mixed` the drive is still the primary answer, so its roadside
    stops are still real."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    dest = {"name": "Capitol Reef", "_transport_mode": "mixed"}
    stops = [{"name": "Escalante", "geocode_lat": 37.7, "geocode_lng": -111.6}]

    url = assembler._build_route_gmaps_url("Bryce Canyon", dest, stops)

    assert "travelmode=driving" in url
    assert "waypoints=" in url


# ── GH #2: self-powered legs ───────────────────────────────────────────────

@pytest.mark.parametrize("mode, travelmode", [("bike", "bicycling"), ("hike", "walking")])
def test_self_powered_legs_get_their_own_maps_travelmode(mode, travelmode) -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    dest = {"name": "Capitol Reef", "_transport_mode": mode}

    url = assembler._build_route_gmaps_url("Bryce Canyon", dest, [])

    assert f"travelmode={travelmode}" in url


@pytest.mark.parametrize("mode", ["bike", "hike"])
def test_self_powered_legs_keep_their_waypoints(mode) -> None:
    """Transit returns early past the waypoint block because Google rejects
    waypoints there. Bicycling and walking accept them, and a self-powered
    leg is where they matter most -- the stops are the itinerary."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    dest = {"name": "Capitol Reef", "_transport_mode": mode}
    stops = [{"name": "Escalante", "geocode_lat": 37.7, "geocode_lng": -111.6}]

    url = assembler._build_route_gmaps_url("Bryce Canyon", dest, stops)

    assert "waypoints=" in url


def test_transit_still_drops_its_waypoints() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    dest = {"name": "Capitol Reef", "_transport_mode": "transit"}
    stops = [{"name": "Escalante", "geocode_lat": 37.7, "geocode_lng": -111.6}]

    url = assembler._build_route_gmaps_url("Bryce Canyon", dest, stops)

    assert "waypoints=" not in url


@pytest.mark.parametrize("mode, word", [("bike", "Riding"), ("hike", "Walking")])
def test_the_card_heading_matches_the_mode(mode, word) -> None:
    """A leg the traveler pedals should not sit under a car icon."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {"getting_here": {"route_summary": "Gravel and climbing.", "travel_time": "5 hours"}}

    html = assembler._build_getting_here(
        ai, {"name": "Capitol Reef", "_transport_mode": mode}, previous_name="Bryce"
    )

    assert word in html
    assert "Getting Here" not in html


def test_a_driving_leg_keeps_the_car_heading() -> None:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {"getting_here": {"route_summary": "US-89.", "travel_time": "2 hrs", "distance_miles": "95"}}

    html = assembler._build_getting_here(ai, {"name": "Bryce"}, previous_name="Zion")

    assert "Getting Here" in html


@pytest.mark.parametrize("mode, travelmode", [
    ("hike", "walking"), ("bike", "bicycling"), ("transit", "transit"), ("auto", "driving"),
])
def test_the_getting_here_link_matches_the_card_it_sits_in(mode, travelmode) -> None:
    """Through _build_getting_here, not through the URL helper in isolation.

    The helper builds its link from a synthetic route_destination dict, so a
    field it consults but that dict omits produces a card whose heading and
    whose link disagree. That happened once for booked transportation (11 of
    12 links on an all-rail itinerary opened car directions) and again for
    the resolved leg mode -- a PCT run rendered "Walking Here" above driving
    directions. Testing the helper alone cannot see either."""
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = {"getting_here": {"route_summary": "The leg.", "travel_time": "5 hrs"}}
    dest = {"name": "Trout Lake", "_transport_mode": mode}

    html = assembler._build_getting_here(ai, dest, previous_name="Cascade Locks")

    assert f"travelmode={travelmode}" in html
