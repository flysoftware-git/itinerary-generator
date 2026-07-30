from generator.ai_content import AIContentGenerator


def _gen() -> AIContentGenerator:
    return AIContentGenerator.__new__(AIContentGenerator)


def test_dedupes_similar_attraction_names() -> None:
    g = _gen()
    items = [
        {"name": "Kolob Canyons Road", "description": "Scenic canyon drive.", "must_see": False},
        {"name": "Kolb Canyons", "description": "Overview of Kolob district.", "must_see": True},
    ]

    deduped = g._normalize_attractions(items)

    assert len(deduped) == 1
    assert "kol" in deduped[0]["name"].lower()


def test_schedule_injects_arrival_and_departure_context() -> None:
    g = _gen()
    schedule = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Start at sunrise viewpoints."},
                {"period": "Afternoon", "summary": "Explore canyon trails."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Hit key overlooks."},
                {"period": "Evening", "summary": "Dinner and sunset."},
            ],
        },
    ]

    updated = g._inject_travel_realism(
        schedule,
        {"drive_time": "2 hrs 15 min", "route_summary": "US-89 to UT-12"},
        "Zion National Park",
        "Capitol Reef National Park",
    )

    first_text = updated[0]["periods"][0]["summary"].lower()
    last_text = updated[-1]["periods"][-1]["summary"].lower()

    assert "travel from zion national park" in first_text
    assert "onward drive to capitol reef national park" in last_text


def test_infer_day_count_single_day_date() -> None:
    g = _gen()
    assert g._infer_day_count("October 17, 2026") == 1


def test_expand_days_truncates_to_single_day() -> None:
    g = _gen()
    days = [
        {"day_label": "Day 1", "periods": [{"period": "Morning", "summary": "A"}]},
        {"day_label": "Day 2", "periods": [{"period": "Afternoon", "summary": "B"}]},
        {"day_label": "Day 3", "periods": [{"period": "Evening", "summary": "C"}]},
    ]

    trimmed = g._expand_days(days, 1)
    assert len(trimmed) == 1
    assert trimmed[0]["day_label"] == "Day 1"


def test_inject_travel_realism_no_leading_colon_when_arrival_already_present() -> None:
    g = _gen()
    days = [{
        "day_label": "Day 1",
        "periods": [{"period": "Morning", "summary": "Arrive at Bryce Canyon and settle in."}],
    }, {
        "day_label": "Day 2",
        "periods": [{"period": "Evening", "summary": "Dinner and sunset."}],
    }]

    updated = g._inject_travel_realism(
        days,
        {"drive_time": "1 hr 45 min"},
        "Zion National Park",
        "Capitol Reef National Park",
    )
    first_summary = updated[0]["periods"][0]["summary"]
    assert not first_summary.lstrip().startswith(":")
    assert "arrive at bryce canyon and settle in." in first_summary.lower()


def test_normalize_schedule_fills_sparse_multi_day_periods_and_departure_on_last_day() -> None:
    g = _gen()
    schedule = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Arrival and first hike."},
                {"period": "Afternoon", "summary": "Explore arches."},
                {"period": "Evening", "summary": "Dinner in town."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Afternoon", "summary": "Visit Island in the Sky viewpoint."},
            ],
        },
        {
            "day_label": "Day 3",
            "periods": [
                {"period": "Evening", "summary": "Sunset and dinner."},
            ],
        },
    ]

    out = g._normalize_schedule(
        schedule=schedule,
        restaurants=[{"name": "Zax Restaurant & Watering Hole"}],
        dates="October 10-12, 2026",
        getting_here={"drive_time": "1 hr 30 min"},
        previous_destination="Capitol Reef National Park",
        next_destination="Telluride",
    )

    assert len(out) == 3
    for day in out:
        labels = [p.get("period") for p in day.get("periods", [])]
        assert labels == ["Morning", "Afternoon", "Evening"]

    day2_text = " ".join(p.get("summary", "") for p in out[1]["periods"]).lower()
    assert "onward drive to telluride" not in day2_text

    day3_evening = out[2]["periods"][2]["summary"].lower()
    assert "onward drive to telluride" in day3_evening


def test_normalize_schedule_reserves_first_day_morning_for_origin_transport() -> None:
    g = _gen()
    schedule = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Start with a top attraction."},
                {"period": "Afternoon", "summary": "Explore nearby highlights."},
                {"period": "Evening", "summary": "Dinner in town."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Hike and viewpoints."},
                {"period": "Afternoon", "summary": "Scenic drive."},
                {"period": "Evening", "summary": "Stargazing."},
            ],
        },
    ]

    out = g._normalize_schedule(
        schedule=schedule,
        restaurants=[{"name": "Local Bistro"}],
        dates="October 7-8, 2026",
        getting_here={"drive_time": "2 hrs"},
        previous_destination="none",
        next_destination="Bryce Canyon National Park",
        trip_origin="Las Vegas",
        trip_return="Las Vegas",
    )

    morning = out[0]["periods"][0]["summary"].lower()
    assert "travel from las vegas" in morning


def test_normalize_schedule_reserves_last_day_afternoon_evening_for_return() -> None:
    g = _gen()
    schedule = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Trail start."},
                {"period": "Afternoon", "summary": "Canyon overlooks."},
                {"period": "Evening", "summary": "Dinner and sunset."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Museum visit."},
                {"period": "Afternoon", "summary": "Gallery walk."},
                {"period": "Evening", "summary": "Final dinner."},
            ],
        },
    ]

    out = g._normalize_schedule(
        schedule=schedule,
        restaurants=[{"name": "Local Bistro"}],
        dates="October 18-19, 2026",
        getting_here={"drive_time": "1 hr"},
        previous_destination="Pagosa Springs",
        next_destination="",
        trip_origin="Las Vegas",
        trip_return="Las Vegas",
    )

    last_afternoon = out[-1]["periods"][1]["summary"].lower()
    last_evening = out[-1]["periods"][2]["summary"].lower()
    assert "reserved for return travel to las vegas" in last_afternoon
    assert "reserved for return travel to las vegas" in last_evening


def test_normalize_schedule_ensures_each_day_has_unique_signal() -> None:
    g = _gen()
    repeated_day = {
        "day_label": "Day 1",
        "periods": [
            {"period": "Morning", "summary": "Explore arches and viewpoints."},
            {"period": "Afternoon", "summary": "Explore arches and viewpoints."},
            {"period": "Evening", "summary": "Explore arches and viewpoints."},
        ],
    }
    schedule = [
        repeated_day,
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Explore arches and viewpoints."},
                {"period": "Afternoon", "summary": "Explore arches and viewpoints."},
                {"period": "Evening", "summary": "Explore arches and viewpoints."},
            ],
        },
    ]

    out = g._normalize_schedule(
        schedule=schedule,
        restaurants=[{"name": "Zax Restaurant & Watering Hole"}],
        dates="October 10-11, 2026",
        getting_here={"drive_time": "1 hr 20 min"},
        previous_destination="Capitol Reef National Park",
        next_destination="Telluride",
    )

    day1_set = {p.get("summary", "") for p in out[0].get("periods", [])}
    day2_set = {p.get("summary", "") for p in out[1].get("periods", [])}

    # Guardrail: each additional day should introduce at least one non-identical summary.
    assert any(summary not in day1_set for summary in day2_set)


def test_filter_oversized_scenic_drives_removes_full_day_loop() -> None:
    g = _gen()
    g._config = {"url_discovery": {"max_scenic_drive_miles": 150}}
    trip = {
        "destinations": [
            {
                "name": "Pagosa Springs",
                "scenic_drives": [
                    {
                        "title": "San Juan Skyway Day Trip",
                        "distance_or_duration": "236-mile loop - allow a full day",
                    },
                    {
                        "title": "Piedra Road",
                        "distance_or_duration": "42 miles round-trip",
                    },
                ],
            }
        ]
    }

    g._filter_oversized_scenic_drives(trip)
    titles = [d["title"] for d in trip["destinations"][0]["scenic_drives"]]
    assert "San Juan Skyway Day Trip" not in titles
    assert "Piedra Road" in titles


def test_filter_oversized_scenic_drives_respects_mile_cap() -> None:
    g = _gen()
    g._config = {"url_discovery": {"max_scenic_drive_miles": 120}}
    trip = {
        "destinations": [
            {
                "name": "Pagosa Springs",
                "scenic_drives": [
                    {
                        "title": "Long Loop",
                        "distance_or_duration": "130 miles",
                    },
                    {
                        "title": "Short Loop",
                        "distance_or_duration": "95 miles",
                    },
                ],
            }
        ]
    }

    g._filter_oversized_scenic_drives(trip)
    titles = [d["title"] for d in trip["destinations"][0]["scenic_drives"]]
    assert "Long Loop" not in titles
    assert "Short Loop" in titles


def test_filter_departure_aligned_drives_moves_matching_one_way_drive_to_getting_there() -> None:
    g = _gen()
    trip = {
        "trip": {"return": "Albuquerque, NM"},
        "destinations": [
            {
                "name": "Santa Fe",
                "ai_content": {},
                "scenic_drives": [
                    {
                        "title": "Turquoise Trail Scenic Byway to Albuquerque",
                        "distance_or_duration": "50 miles one-way",
                        "description": "Historic route into Albuquerque.",
                    },
                    {
                        "title": "Hyde Memorial Loop",
                        "distance_or_duration": "32 miles round-trip",
                        "description": "Mountain loop.",
                    },
                ],
            }
        ],
    }

    g._filter_departure_aligned_drives(trip)

    scenic_titles = [d["title"] for d in trip["destinations"][0]["scenic_drives"]]
    assert "Turquoise Trail Scenic Byway to Albuquerque" not in scenic_titles
    assert "Hyde Memorial Loop" in scenic_titles

    options = trip["destinations"][0]["ai_content"]["getting_there"]["route_options"]
    option_titles = [o["title"] for o in options]
    assert "Turquoise Trail Scenic Byway to Albuquerque" in option_titles
    assert "departure leg toward albuquerque, nm" in trip["destinations"][0]["ai_content"]["getting_there"]["route_summary"].lower()


def test_remove_enroute_stops_from_attractions() -> None:
    g = _gen()
    attractions = [
        {"name": "Goblin Valley State Park", "type": "attraction"},
        {"name": "Capitol Reef Scenic Drive", "type": "scenic"},
    ]
    getting_here = {
        "en_route_stops": [
            {"name": "Goblin Valley"},
            {"name": "Hanksville"},
        ]
    }

    filtered = g._remove_enroute_stops_from_attractions(attractions, getting_here)
    names = [a["name"] for a in filtered]

    assert "Goblin Valley State Park" not in names
    assert "Capitol Reef Scenic Drive" in names


def test_remove_enroute_stops_preserves_seeded_attraction() -> None:
    g = _gen()
    attractions = [
        {"name": "Angels Landing", "type": "hike"},
        {"name": "Canyon Overlook Trail", "type": "hike"},
    ]
    getting_here = {
        "en_route_stops": [
            {"name": "Angels Landing"},
        ]
    }

    filtered = g._remove_enroute_stops_from_attractions(
        attractions,
        getting_here,
        protected_names=["Angels Landing"],
    )
    names = [a["name"] for a in filtered]

    assert "Angels Landing" in names


def test_ensure_seed_attractions_adds_missing_seed() -> None:
    g = _gen()
    attractions = [
        {"name": "The Narrows", "type": "hike", "must_see": False},
    ]

    out = g._ensure_seed_attractions(attractions, ["Angels Landing", "The Narrows"])
    names = [a.get("name") for a in out]

    assert "Angels Landing" in names
    assert "The Narrows" in names


def test_normalize_destination_content_preserves_seeded_angels_landing_through_enroute_filter() -> None:
    g = _gen()
    g._weather_cache = {}
    g._get_monthly_temperature_normals = lambda _lat, _lng, _month: None

    payload = {
        "expected_environment": {},
        "getting_here": {
            "en_route_stops": [{"name": "Angels Landing"}],
        },
        "top_attractions": [],
        "possible_daily_schedule": {},
        "dinner_recommendations": [],
    }
    dest = {
        "name": "Zion National Park",
        "dates": "October 7-9, 2026",
        "seeds": ["Angels Landing"],
    }

    out = g._normalize_destination_content(
        payload,
        dates=dest["dates"],
        dest=dest,
        trip_meta={},
        previous_destination="none",
        next_destination="Bryce Canyon National Park",
    )

    names = [str(a.get("name", "")) for a in out.get("top_attractions", [])]
    assert any("angels landing" == n.lower() for n in names)


def test_normalize_getting_here_returns_normalized_dict() -> None:
    g = _gen()
    getting_here = {
        "drive_time": "1 hr 30 min",
        "en_route_stops": [
            {"name": "Viewpoint", "detour_distance_miles": "", "detour_time_minutes": None}
        ],
    }

    out = g._normalize_getting_here(getting_here, "Moab")

    assert isinstance(out, dict)
    assert out["en_route_stops"][0]["detour_distance_miles"] == 0
    assert out["en_route_stops"][0]["detour_time_minutes"] == 0
    assert "Arrival leg into Moab" in out.get("route_summary", "")


def test_normalize_restaurants_filters_chain_and_fast_food() -> None:
    g = _gen()
    restaurants = [
        {
            "name": "Chick-fil-A",
            "cuisine": "Fast Food",
            "price_range": "$",
            "description": "Popular quick service chicken chain.",
        },
        {
            "name": "The Painted Pony",
            "cuisine": "Contemporary American",
            "price_range": "$$$",
            "description": "Independent downtown fine dining spot.",
        },
    ]

    normalized = g._normalize_restaurants(restaurants)
    names = [r.get("name") for r in normalized]

    assert "Chick-fil-A" not in names
    assert "The Painted Pony" in names


def test_normalize_restaurants_filters_ai_closure_signal() -> None:
    g = _gen()
    restaurants = [
        {
            "name": "Closed Bistro",
            "cuisine": "American",
            "price_range": "$$",
            "description": "A neighborhood favorite that is permanently closed.",
        },
        {
            "name": "Open Kitchen",
            "cuisine": "Contemporary",
            "price_range": "$$$",
            "description": "Popular dinner spot with a seasonal menu.",
        },
    ]

    normalized = g._normalize_restaurants(restaurants)
    names = [r.get("name") for r in normalized]

    assert "Closed Bistro" not in names
    assert "Open Kitchen" in names


def test_normalize_what_to_know_always_populates_required_fields() -> None:
    g = _gen()
    payload = {
        "summary": "Expect quick weather swings between trailhead and canyon floor.",
        "local_customs": "Greet shuttle drivers and queue patiently at popular stops.",
    }
    dest = {"name": "Zion National Park", "dates": "October 7-9, 2026"}

    normalized = g._normalize_what_to_know(payload, dest)

    required = [
        "summary",
        "local_customs",
        "best_times_of_day",
        "transportation_quirks",
        "safety_considerations",
        "crowd_patterns",
        "local_etiquette",
    ]
    for key in required:
        assert key in normalized
        assert isinstance(normalized[key], str)
        assert normalized[key].strip() != ""


def test_normalize_what_to_know_does_not_require_or_emit_legacy_weather_photo_fields() -> None:
    g = _gen()
    payload = {
        "summary": "Expect fast weather swings.",
        "local_customs": "Respect shuttle lines.",
    }
    dest = {"name": "Zion National Park", "dates": "October 7-9, 2026"}

    normalized = g._normalize_what_to_know(payload, dest)

    assert "typical_weather_patterns" not in normalized
    assert "photography_tips" not in normalized
    assert normalized["summary"] == "Expect fast weather swings."
    assert normalized["local_customs"] == "Respect shuttle lines."


def test_render_prompt_template_replaces_known_tokens_only() -> None:
    g = _gen()
    template = (
        "Destination: {destination_name}\n"
        "Dates: {dates}\n"
        "{\n"
        "  \"summary\": \"text\"\n"
        "}\n"
    )

    rendered = g._render_prompt_template(
        template,
        destination_name="Zion National Park",
        dates="October 7-9, 2026",
    )

    assert "Destination: Zion National Park" in rendered
    assert "Dates: October 7-9, 2026" in rendered
    assert '{\n  "summary": "text"\n}' in rendered
