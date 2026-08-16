from unittest.mock import patch

from generator.ai_content import AIContentGenerator


def _gen() -> AIContentGenerator:
    return AIContentGenerator.__new__(AIContentGenerator)


def _gen_with_bundle_templates() -> AIContentGenerator:
    g = _gen()
    g._dest_template = "Dest {destination_name} {dates} {trip_title} {previous_destination} {next_destination} {budget_guidance} {seeds}"
    g._what_to_know_template = "Know {destination_name} {dates} {season} {trip_type} {previous_destination} {next_destination} {budget_guidance}"
    g._drives_template = "Drives {destination_name} {dates} {region}"
    g._system_prompt = "system"
    g._config = {}
    g._enable_url_candidate_experiment = False
    return g


def test_generate_destination_bundle_retries_transient_errors() -> None:
    """Regression for issue #66 ('retries should be short and narrow for
    transient API issues only'): a network hiccup must still be retried."""
    g = _gen_with_bundle_templates()

    class _FlakyLLM:
        def __init__(self) -> None:
            self.calls = 0

        def generate_json(self, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise ConnectionError("transient network error")
            return {
                "destination_content": {"top_attractions": []},
                "what_to_know": {},
                "scenic_drives": [{"title": "Ok Drive"}],
            }

    g._llm = _FlakyLLM()
    with patch("tenacity.nap.time.sleep"):
        bundle = g._generate_destination_bundle(
            {"id": "zion", "name": "Zion", "dates": "June 1-2", "seeds": []},
            {"title": "Test Trip"},
            "none",
            "none",
        )

    assert bundle["scenic_drives"] == [{"title": "Ok Drive"}]
    assert g._llm.calls == 3


def test_generate_destination_bundle_does_not_retry_programming_errors() -> None:
    """A KeyError/TypeError from malformed destination data is a bug, not a
    transient condition -- retrying it 3x with backoff just delays surfacing
    the real error without ever fixing it."""
    g = _gen_with_bundle_templates()

    class _BuggyLLM:
        def __init__(self) -> None:
            self.calls = 0

        def generate_json(self, **kwargs):
            self.calls += 1
            raise KeyError("missing_field")

    g._llm = _BuggyLLM()
    with patch("tenacity.nap.time.sleep"):
        try:
            g._generate_destination_bundle(
                {"id": "zion", "name": "Zion", "dates": "June 1-2", "seeds": []},
                {"title": "Test Trip"},
                "none",
                "none",
            )
            assert False, "expected KeyError to propagate"
        except KeyError:
            pass

    assert g._llm.calls == 1


def test_llm_stage_max_workers_caps_grok_to_single() -> None:
    g = _gen()
    g._llm = type("MockLLM", (), {"provider": "grok"})()
    g._max_concurrent_destinations = 4
    g._grok_max_concurrent_destinations = 1

    assert g._llm_stage_max_workers(7) == 1


def test_llm_stage_max_workers_uses_general_cap_for_non_grok() -> None:
    g = _gen()
    g._llm = type("MockLLM", (), {"provider": "openai"})()
    g._max_concurrent_destinations = 3
    g._grok_max_concurrent_destinations = 1

    assert g._llm_stage_max_workers(7) == 3


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


def test_build_trip_timing_context_includes_lodging_checkin_anchor() -> None:
    lines = AIContentGenerator._build_trip_timing_context(
        trip_meta={"departure": "Las Vegas", "departure_datetime": "2026-10-07 08:30"},
        destination_name="Zion National Park",
        destination={"lodging": {"location": "Zion Lodge, Springdale, UT", "checkin_time": "4:00 PM"}},
        previous_destination="none",
        next_destination="Bryce Canyon National Park",
    )

    assert "Lodging anchor for Zion National Park: Zion Lodge, Springdale, UT @ 4:00 PM" in lines
    assert "keep first-day activities feasible around arrival, with lodging check-in near 4:00 PM" in lines


def test_normalize_attractions_filters_non_tourist_markers() -> None:
    g = _gen()
    out = g._normalize_attractions(
        [
            {
                "name": "Dixie Regional Medical Center",
                "type": "attraction",
                "description": "Hospital; not a tourist attraction.",
            },
            {
                "name": "Snow Canyon State Park",
                "type": "attraction",
                "description": "Red-rock trails and viewpoints.",
            },
        ]
    )

    assert [item["name"] for item in out] == ["Snow Canyon State Park"]


def test_strip_banned_marketing_language_removes_real_observed_violations() -> None:
    """Regression grounded in actual Dipstick48 output: system_prompt.txt's
    'Avoid without exception' list was pure LLM instruction with zero
    downstream enforcement -- a real run had 28 violations despite that
    wording. These are the exact phrasings observed."""
    cases = {
        "The city is known for its dinosaur history and stunning desert landscapes.":
            "The city is known for its dinosaur history and desert landscapes.",
        "The trail features sandy paths and stunning red rock formations.":
            "The trail features sandy paths and red rock formations.",
        "enhance the views with orange and red hues contrasting the iconic hoodoos.":
            "enhance the views with orange and red hues contrasting the hoodoos.",
        "Cedar Breaks offers breathtaking views of an amphitheater filled with vibrant rock formations.":
            "Cedar Breaks offers views of an amphitheater filled with vibrant rock formations.",
        "culminating in the charming arrival at Santa Fe.":
            "culminating in the arrival at Santa Fe.",
        "Key stops include the charming town of Chimayo, known for its sanctuary.":
            "Key stops include the town of Chimayo, known for its sanctuary.",
    }
    for original, expected in cases.items():
        assert AIContentGenerator._strip_banned_marketing_language(original) == expected


def test_strip_banned_marketing_language_drops_dangling_copula() -> None:
    """The common 'is a <cliche>' / 'is <cliche>' sentence-final predicate
    pattern must not leave a dangling verb behind after the phrase is
    removed."""
    assert AIContentGenerator._strip_banned_marketing_language(
        "This trail is a hidden gem."
    ) == "This trail."
    assert AIContentGenerator._strip_banned_marketing_language(
        "This spot is off the beaten path."
    ) == "This spot."


def test_strip_banned_marketing_language_counts_violations() -> None:
    counts: dict[str, int] = {}
    AIContentGenerator._strip_banned_marketing_language(
        "A stunning and truly stunning iconic view.", counts
    )
    assert counts == {"stunning": 2, "iconic": 1}


def test_strip_banned_marketing_language_leaves_clean_text_untouched() -> None:
    text = "A 3-mile round-trip hike to a large arch with views of La Sal Mountains."
    assert AIContentGenerator._strip_banned_marketing_language(text) == text


def test_strip_banned_marketing_language_excludes_must_see() -> None:
    """'must-see' is deliberately NOT on the enforcement list -- it's a
    structured badge label gated on verified rating data elsewhere
    (html_assembler.py), not a subjective prose claim to scrub."""
    text = "This trail is a must-see destination."
    assert AIContentGenerator._strip_banned_marketing_language(text) == text


def test_scrub_banned_language_in_place_only_touches_allowlisted_prose_fields() -> None:
    """A restaurant genuinely named 'The Charming Cafe' (a real, verifiable
    business name) must survive intact -- only known prose fields
    (description, practical_note, summary, ...) may be rewritten, never name/
    title/url/type/cuisine/enum/numeric fields."""
    ai_content = {
        "top_attractions": [
            {
                "name": "Stunning Overlook",  # a real place name containing a banned word
                "type": "viewpoint",
                "difficulty": "Easy",
                "description": "A stunning overlook with iconic views.",
                "practical_note": "Nestled parking area fills by 9am.",
            }
        ],
        "dinner_recommendations": [
            {
                "name": "The Charming Cafe",
                "cuisine": "Charming Bistro Fare",
                "description": "A charming spot with breathtaking patio seating.",
            }
        ],
    }
    counts: dict[str, int] = {}
    AIContentGenerator._scrub_banned_language_in_place(ai_content, counts)

    attr = ai_content["top_attractions"][0]
    rest = ai_content["dinner_recommendations"][0]
    assert attr["name"] == "Stunning Overlook"
    assert attr["type"] == "viewpoint"
    assert attr["difficulty"] == "Easy"
    assert attr["description"] == "A overlook with views."
    assert attr["practical_note"] == "parking area fills by 9am."
    assert rest["name"] == "The Charming Cafe"
    assert rest["cuisine"] == "Charming Bistro Fare"
    assert rest["description"] == "A spot with patio seating."
    assert counts["stunning"] == 1
    assert counts["iconic"] == 1
    assert counts["nestled"] == 1
    assert counts["charming"] == 1
    assert counts["breathtaking"] == 1


def test_enforce_banned_marketing_language_walks_full_trip_and_records_counts() -> None:
    g = _gen()
    trip = {
        "destinations": [
            {
                "name": "Zion",
                "ai_content": {
                    "top_attractions": [
                        {"name": "Arch", "description": "A stunning natural arch."}
                    ]
                },
                "what_to_know": {"summary": "A world-class destination for hiking."},
                "scenic_drives": [{"title": "Zion Canyon Drive", "description": "A majestic canyon road."}],
            }
        ]
    }

    counts = g._enforce_banned_marketing_language(trip)

    assert trip["destinations"][0]["ai_content"]["top_attractions"][0]["description"] == "A natural arch."
    assert trip["destinations"][0]["what_to_know"]["summary"] == "A destination for hiking."
    assert trip["destinations"][0]["scenic_drives"][0]["description"] == "A canyon road."
    assert trip["destinations"][0]["scenic_drives"][0]["title"] == "Zion Canyon Drive"
    assert counts == {"stunning": 1, "world-class": 1, "majestic": 1}
    assert g.last_banned_phrase_violations == counts


def test_enforce_banned_marketing_language_accumulates_across_calls() -> None:
    """Regression (2026-08-15, dipstick56+): main.py calls
    normalize_trip_content (which calls this) twice in a real run -- once
    unconditionally after initial generation, again after the selective-
    retry pass if anything was retried. Overwriting last_banned_phrase_violations
    each call meant runtime_metrics["banned_phrase_violations"], read right
    after the FIRST call, went stale/wrong the moment a real run's retry
    pass triggered a second call -- a real run's persisted metric and its
    own console log disagreed with each other and with neither call's
    actual findings."""
    g = _gen()
    first_trip = {
        "destinations": [
            {"name": "Zion", "ai_content": {"top_attractions": [{"name": "Arch", "description": "A stunning arch."}]}}
        ]
    }
    second_trip = {
        "destinations": [
            {"name": "Bryce", "ai_content": {"top_attractions": [{"name": "Point", "description": "A stunning, majestic point."}]}}
        ]
    }

    g._enforce_banned_marketing_language(first_trip)
    g._enforce_banned_marketing_language(second_trip)

    assert g.last_banned_phrase_violations == {"stunning": 2, "majestic": 1}


def test_manifest_attraction_target_prefers_highest_rated_candidates() -> None:
    g = _gen()
    items = [
        {"name": "Lower-rated Park", "type": "attraction", "rating": 4.2, "votes": 50, "must_see": False},
        {"name": "Top Pick", "type": "attraction", "rating": 4.9, "votes": 200, "must_see": False},
        {"name": "Must See", "type": "attraction", "rating": 4.7, "votes": 150, "must_see": True},
        {"name": "Mid-tier Stop", "type": "attraction", "rating": 4.5, "votes": 120, "must_see": False},
        {"name": "Backup", "type": "attraction", "rating": 4.4, "votes": 80, "must_see": False},
    ]

    out = g._apply_manifest_attraction_target(items, dates="October 7-8, 2026", attractions_per_day=2)

    assert [item["name"] for item in out] == ["Must See", "Top Pick", "Mid-tier Stop", "Backup"]


def test_manifest_attraction_target_keeps_seeded_names_in_output() -> None:
    g = _gen()
    items = [
        {"name": "Other Stop", "type": "attraction", "rating": 4.9, "votes": 200, "must_see": False},
        {"name": "Rim View", "type": "attraction", "rating": 4.8, "votes": 180, "must_see": False},
        {"name": "Sunrise Point", "type": "attraction", "rating": 4.3, "votes": 120, "must_see": False},
        {"name": "Queens Garden Trail", "type": "hike", "rating": 4.4, "votes": 140, "must_see": False},
        {"name": "Navajo Loop Trail", "type": "hike", "rating": 4.2, "votes": 130, "must_see": False},
    ]

    out = g._apply_manifest_attraction_target(
        items,
        dates="October 7-8, 2026",
        attractions_per_day=2,
        protected_names=["Navajo Loop Trail", "Queens Garden Trail", "Sunrise Point"],
    )

    names = [item["name"] for item in out]
    assert "Navajo Loop Trail" in names
    assert "Queens Garden Trail" in names
    assert "Sunrise Point" in names


from generator.ai_content import AIContentGenerator


class _FakeLLM:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_normalize_schedule_fills_sparse_multi_day_periods_and_departure_on_last_day() -> None:

    def test_generate_destination_bundle_uses_one_llm_call_and_normalizes_both_payloads() -> None:
        g = _gen()
        g._llm = _FakeLLM(
            {
                "destination_content": {
                    "top_attractions": [{"name": "Dale Ball Trail", "type": "hike"}],
                    "getting_here": {"en_route_stops": []},
                    "possible_daily_schedule": [],
                    "dinner_recommendations": [],
                },
                "what_to_know": {"summary": "Bring layers."},
            }
        )
        g._system_prompt = "system"
        g._dest_template = "Dest {destination_name} {dates} {trip_title} {previous_destination} {next_destination} {budget_guidance} {seeds}"
        g._what_to_know_template = "Know {destination_name} {dates} {season} {trip_type} {previous_destination} {next_destination} {budget_guidance}"

        bundle = g._generate_destination_bundle(
            {
                "id": "santafe",
                "name": "Santa Fe",
                "dates": "June 1-2, 2026",
                "seeds": ["Dale Ball Trail"],
            },
            {"title": "Southwest Road Trip", "subtitle": "Road Trip"},
            "none",
            "Taos",
        )

        assert len(g._llm.calls) == 1
        assert bundle["destination_content"]["top_attractions"][0]["name"] == "Dale Ball Trail"
        assert bundle["what_to_know"]["summary"] == "Bring layers."
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


def test_inject_travel_realism_removes_inbound_drive_and_checkin_from_day2_plus() -> None:
    g = _gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Drive from Zion National Park to Bryce Canyon National Park."},
                {"period": "Afternoon", "summary": "After arrival, explore nearby hoodoos."},
                {"period": "Evening", "summary": "Dinner in town."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Drive from Zion National Park to Bryce Canyon National Park, taking in US-89."},
                {"period": "Afternoon", "summary": "Check into your lodging, then explore Navajo Loop Trail."},
                {"period": "Evening", "summary": "Sunset and dinner."},
            ],
        },
        {
            "day_label": "Day 3",
            "periods": [
                {"period": "Morning", "summary": "Drive from Zion National Park to Bryce Canyon National Park."},
                {"period": "Afternoon", "summary": "Check into lodging and tour nearby highlights."},
                {"period": "Evening", "summary": "Wrap up."},
            ],
        },
    ]

    out = g._inject_travel_realism(
        days,
        {"drive_time": "1 hr 45 min"},
        "Zion National Park",
        "Capitol Reef National Park",
    )

    day2_morning = out[1]["periods"][0]["summary"].lower()
    day3_morning = out[2]["periods"][0]["summary"].lower()
    day2_afternoon = out[1]["periods"][1]["summary"].lower()
    day3_afternoon = out[2]["periods"][1]["summary"].lower()

    assert "drive from zion national park" not in day2_morning
    assert "drive from zion national park" not in day3_morning
    assert "check into" not in day2_afternoon
    assert "check into" not in day3_afternoon


def test_inject_travel_realism_removes_checkin_from_non_afternoon_day2_blocks() -> None:
    g = _gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Drive from Moab to Telluride."},
                {"period": "Afternoon", "summary": "After arrival, settle in and explore."},
                {"period": "Evening", "summary": "Dinner on Main Street."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Trailhead start."},
                {"period": "Afternoon", "summary": "Explore local highlights."},
                {"period": "Evening", "summary": "Check in at the hotel before dinner."},
            ],
        },
    ]

    out = g._inject_travel_realism(
        days,
        {"drive_time": "2 hr 30 min"},
        "Moab",
        "Pagosa Springs",
    )

    day2_evening = out[1]["periods"][2]["summary"].lower()
    assert "check in" not in day2_evening
    assert "check-in" in day2_evening or "destination-focused" in day2_evening or "onward drive" in day2_evening


def test_inject_travel_realism_single_day_transfer_includes_arrival_checkin_guidance() -> None:
    g = _gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Start with a scenic walk."},
                {"period": "Afternoon", "summary": "Visit local galleries."},
                {"period": "Evening", "summary": "Dinner downtown."},
            ],
        }
    ]

    out = g._inject_travel_realism(
        days,
        {"drive_time": "2 hr"},
        "Telluride",
        "Santa Fe",
    )

    afternoon = out[0]["periods"][1]["summary"].lower()
    assert "visit local galleries" in afternoon


def test_normalize_schedule_softens_first_day_heavy_afternoon_after_origin_travel() -> None:
    g = _gen()
    schedule = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Begin with signature viewpoints."},
                {"period": "Afternoon", "summary": "Complete a strenuous summit hike and long trail segment."},
                {"period": "Evening", "summary": "Dinner downtown."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Major trail day."},
                {"period": "Afternoon", "summary": "Scenic route."},
                {"period": "Evening", "summary": "Sunset stop."},
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

    first_afternoon = out[0]["periods"][1]["summary"].lower()
    assert "keep activity light after travel" in first_afternoon


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


def test_inject_travel_realism_day2_plus_scrub_uses_activity_aware_variation() -> None:
    g = _gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Travel from Page to Bryce Canyon National Park."},
                {"period": "Afternoon", "summary": "After arrival, check in and settle in."},
                {"period": "Evening", "summary": "Dinner in town."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Arrival and check-in logistics."},
                {"period": "Afternoon", "summary": "After arrival, check in and settle in."},
                {"period": "Evening", "summary": "Arrival and check-in logistics."},
            ],
        },
    ]

    out = g._inject_travel_realism(
        days,
        {"drive_time": "1 hr 45 min"},
        "Page",
        "Capitol Reef National Park",
        attractions=[
            {"name": "Navajo Loop Trail"},
            {"name": "Sunset Point"},
        ],
    )

    day2 = out[1]["periods"]
    day2_text = " ".join(str(period.get("summary", "") or "") for period in day2).lower()

    assert "start with" in day2[0]["summary"].lower()
    assert any(name in day2[0]["summary"].lower() for name in ("navajo loop trail", "sunset point"))
    # Day 2+ Afternoon now gets capacity-aware multi-activity packing rather
    # than the older cosmetic "allocate this block to X" rotation -- both
    # attractions have no explicit duration (falls back to 90min each) and
    # fit the default 5-hour budget, so packing supersedes the plain rotation.
    assert "fit multiple activities" in day2[1]["summary"].lower()
    assert any(name in day2[1]["summary"].lower() for name in ("navajo loop trail", "sunset point"))
    # Last-day evening for a transfer destination is reserved for onward-drive prep.
    assert "onward drive to capitol reef national park" in day2[2]["summary"].lower()
    assert "start with a different priority trailhead or district than day 1" not in day2_text


def test_inject_travel_realism_rotates_focus_to_reduce_adjacent_duplicates() -> None:
    g = _gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Start at Navajo Loop Trail for cooler temps."},
                {"period": "Afternoon", "summary": "Continue at Sunset Point with canyon views."},
                {"period": "Evening", "summary": "Dinner in town."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Return to Sunset Point for photos."},
                {"period": "Afternoon", "summary": "Hike Navajo Loop Trail if time allows."},
                {"period": "Evening", "summary": "Dinner and sunset."},
            ],
        },
    ]

    out = g._inject_travel_realism(
        days,
        {"drive_time": "1 hr 45 min"},
        "Page",
        "Capitol Reef National Park",
        attractions=[
            {"name": "Navajo Loop Trail"},
            {"name": "Sunset Point"},
            {"name": "Bryce Point"},
        ],
        restaurants=[{"name": "Local Bistro"}, {"name": "Rim Cafe"}],
    )

    day1_afternoon = out[0]["periods"][1]["summary"].lower()
    day2_morning = out[1]["periods"][0]["summary"].lower()
    assert day2_morning != day1_afternoon
    assert "bryce point" in day2_morning or "navajo loop trail" in day2_morning


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
    moved = options[0]
    assert moved["_registry"]["ownership_type"] == "transfer_leg"
    assert moved["_registry"]["section_target"] == "getting_there.route_options"
    assert moved["_registry"]["validation_status"] == "accepted"


def test_cross_destination_scenic_drive_dedup_keeps_zion_drive_under_zion() -> None:
    g = _gen()
    trip = {
        "destinations": [
            {
                "name": "St. George",
                "scenic_drives": [
                    {
                        "title": "Zion Canyon Scenic Drive",
                        "description": "Iconic route through Zion Canyon.",
                    }
                ],
            },
            {
                "name": "Zion National Park",
                "scenic_drives": [
                    {
                        "title": "Zion Canyon Scenic Drive",
                        "description": "Shuttle-season route in Zion Canyon.",
                    }
                ],
            },
        ]
    }

    g._deduplicate_cross_destination_scenic_drives(trip)

    st_george_titles = [d["title"] for d in trip["destinations"][0]["scenic_drives"]]
    zion_titles = [d["title"] for d in trip["destinations"][1]["scenic_drives"]]
    assert "Zion Canyon Scenic Drive" not in st_george_titles
    assert "Zion Canyon Scenic Drive" in zion_titles


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


def test_remove_enroute_stops_does_not_false_positive_on_generic_residual_word() -> None:
    """Regression found via the end-to-end smoke test: 'Overlook Point'
    normalizes to just 'point' once norm() strips the generic word
    'overlook' -- a bare 'point' is then a substring of nearly any
    attraction with 'Point' in its name, incorrectly removing unrelated
    attractions like 'Sunset Point'."""
    g = _gen()
    attractions = [
        {"name": "Sunset Point", "type": "viewpoint"},
        {"name": "Inspiration Point", "type": "viewpoint"},
    ]
    getting_here = {"en_route_stops": [{"name": "Overlook Point"}]}

    filtered = g._remove_enroute_stops_from_attractions(attractions, getting_here)
    names = [a["name"] for a in filtered]

    assert "Sunset Point" in names
    assert "Inspiration Point" in names


def test_ensure_seed_attractions_adds_missing_seed() -> None:
    g = _gen()
    attractions = [
        {"name": "The Narrows", "type": "hike", "must_see": False},
    ]

    out = g._ensure_seed_attractions(attractions, ["Angels Landing", "The Narrows"])
    names = [a.get("name") for a in out]

    assert "Angels Landing" in names
    assert "The Narrows" in names


def test_normalize_restaurants_deduplicates_canonical_name_variants() -> None:
    g = _gen()
    restaurants = [
        {"name": "Allred's Restaurant", "description": "Source", "cuisine": "American", "price_range": "$$"},
        {"name": "Allreds Restaurant", "description": "High-altitude dining with mountain views.", "cuisine": "American", "price_range": "$$"},
        {"name": "Riggatti's Wood Fired Pizza", "description": "Source", "cuisine": "Pizza", "price_range": "$$"},
        {"name": "Riggatti's Wood Fired Pizza", "description": "Wood-fired pizza in town.", "cuisine": "Pizza", "price_range": "$$"},
    ]

    normalized = g._normalize_restaurants(restaurants, budget=None)
    names = [str(r.get("name", "")) for r in normalized]

    assert len(names) == 2
    assert "Allred's Restaurant" in names
    assert "Riggatti's Wood Fired Pizza" in names


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
