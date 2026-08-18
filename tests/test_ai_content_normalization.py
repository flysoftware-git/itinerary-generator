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
    # The final evening at a transfer destination must not imply an
    # after-dinner drive -- the onward drive to the next destination happens
    # the following morning (that destination's own Day 1 arrival leg), so
    # this evening stays local. See docs/design/schedule-normalization.md
    # Case 4.
    assert "onward drive" not in last_text
    assert "capitol reef national park" in last_text
    assert "next morning" in last_text


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


def test_strip_markdown_name_wrapping_removes_real_observed_wrapping() -> None:
    """Real dipstick68 regression: the model routinely wraps its own name
    output in markdown bold ('**Cliffside Restaurant**',
    '**White Rim Overlook Trail**') -- 34 occurrences in one real run,
    rendering literal asterisks directly in visible card text and feeding
    the wrapped text into URL discovery/geocoding, causing wrong-place
    matches downstream."""
    assert AIContentGenerator._strip_markdown_name_wrapping("**Cliffside Restaurant**") == "Cliffside Restaurant"
    assert AIContentGenerator._strip_markdown_name_wrapping("**White Rim Overlook Trail**") == "White Rim Overlook Trail"
    assert AIContentGenerator._strip_markdown_name_wrapping("__Some Place__") == "Some Place"
    assert AIContentGenerator._strip_markdown_name_wrapping("*Some Place*") == "Some Place"


def test_strip_markdown_name_wrapping_leaves_clean_names_untouched() -> None:
    assert AIContentGenerator._strip_markdown_name_wrapping("Cliffside Restaurant") == "Cliffside Restaurant"
    assert AIContentGenerator._strip_markdown_name_wrapping("") == ""


def test_strip_markdown_name_wrapping_does_not_touch_internal_asterisk() -> None:
    """A name with an asterisk somewhere in the middle (not wrapping the
    whole string) must survive intact -- this only strips a marker that
    spans the entire string start-to-end, never a partial/internal one."""
    text = "Bob's *Famous* BBQ"
    assert AIContentGenerator._strip_markdown_name_wrapping(text) == text


def test_scrub_markdown_name_wrapping_in_place_only_touches_name_and_title_fields() -> None:
    """Only name/title fields are cleaned -- description/practical_note and
    every other field are left alone, even if they also contain markdown."""
    ai_content = {
        "top_attractions": [
            {
                "name": "**White Rim Overlook Trail**",
                "description": "**Bold** description text stays as-is.",
            }
        ],
        "dinner_recommendations": [{"name": "**Cliffside Restaurant**"}],
        "getting_here": {
            "en_route_stops": [{"name": "**Some Stop**"}],
        },
    }
    scenic_drives = [{"title": "**Zion Canyon Drive**", "description": "**Bold** stays."}]

    AIContentGenerator._scrub_markdown_name_wrapping_in_place(ai_content)
    AIContentGenerator._scrub_markdown_name_wrapping_in_place(scenic_drives)

    assert ai_content["top_attractions"][0]["name"] == "White Rim Overlook Trail"
    assert ai_content["top_attractions"][0]["description"] == "**Bold** description text stays as-is."
    assert ai_content["dinner_recommendations"][0]["name"] == "Cliffside Restaurant"
    assert ai_content["getting_here"]["en_route_stops"][0]["name"] == "Some Stop"
    assert scenic_drives[0]["title"] == "Zion Canyon Drive"
    assert scenic_drives[0]["description"] == "**Bold** stays."


def test_generate_destination_content_strips_markdown_before_url_discovery_would_see_it() -> None:
    """The scrub must run inside generate_destination_content itself (not
    a later normalize_trip_content pass), since URL discovery reads
    dest['ai_content']/dest['scenic_drives'] before normalize_trip_content
    ever runs -- a markdown-wrapped name would already have been used for
    matching/geocoding by then."""
    g = _gen_with_bundle_templates()
    g._llm = type("MockLLM", (), {"provider": "openai"})()
    g._max_concurrent_destinations = 5
    with patch.object(
        g,
        "_generate_destination_bundle",
        return_value={
            "destination_content": {
                "top_attractions": [{"name": "**White Rim Overlook Trail**"}],
            },
            "what_to_know": {},
            "scenic_drives": [{"title": "**Zion Canyon Drive**"}],
        },
    ):
        trip = {
            "trip": {},
            "destinations": [{"name": "Canyonlands", "dates": "Oct 1"}],
        }
        g.generate_destination_content(trip)

    dest = trip["destinations"][0]
    assert dest["ai_content"]["top_attractions"][0]["name"] == "White Rim Overlook Trail"
    assert dest["scenic_drives"][0]["title"] == "Zion Canyon Drive"


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

    # The final evening at a transfer destination stays local -- no
    # after-dinner drive implied; the onward drive to the next destination
    # is explicitly framed as happening the following morning instead.
    day3_evening = out[2]["periods"][2]["summary"].lower()
    assert "onward drive" not in day3_evening
    assert "telluride" in day3_evening
    assert "next morning" in day3_evening


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
    # Day 2 is this destination's last day before moving on to Pagosa
    # Springs -- the deterministic last-evening override applies here, and
    # it must not imply an after-dinner drive (the onward drive happens the
    # next morning instead).
    assert "onward drive" not in day2_evening
    assert "pagosa springs" in day2_evening
    assert "next morning" in day2_evening


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


def test_normalize_schedule_reserves_last_day_afternoon_for_return_and_drops_evening() -> None:
    """Regression grounded in the project owner's real review finding: 'Last
    day still repeats afternoon and evening, once headed to airport in the
    afternoon, there doesn't need to be an evening.' Afternoon carries the
    return-travel note; Evening must be suppressed (empty, so the renderer
    skips it) rather than repeating a near-duplicate reserved-for-return
    sentence for a period that no longer exists once the traveler has left."""
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
    last_evening = out[-1]["periods"][2]["summary"]
    assert "reserved for return travel to las vegas" in last_afternoon
    assert last_evening == ""


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
            # Two extra attractions so Day 2's Afternoon pack has fresh,
            # not-yet-used material: Day 1's arrival-day pack already
            # consumes Navajo Loop Trail + Sunset Point (both fit the
            # drive-discounted budget), and the cross-day dedup guard now
            # excludes those from Day 2's own pack rather than allowing a
            # repeat.
            {"name": "Mossy Cave Trail"},
            {"name": "Bryce Point"},
        ],
    )

    day2 = out[1]["periods"]
    day2_text = " ".join(str(period.get("summary", "") or "") for period in day2).lower()

    assert "start with" in day2[0]["summary"].lower()
    assert any(
        name in day2[0]["summary"].lower()
        for name in ("navajo loop trail", "sunset point", "mossy cave trail", "bryce point")
    )
    # Day 2+ Afternoon now gets capacity-aware multi-activity packing rather
    # than the older cosmetic "allocate this block to X" rotation -- both
    # attractions have no explicit duration (falls back to 90min each) and
    # fit the default 5-hour budget, so packing supersedes the plain rotation.
    assert "consider one or more of the following" in day2[1]["summary"].lower()
    # Cross-day dedup guard: Day 2's pack must draw from attractions NOT
    # already used by Day 1's arrival-day pack (Navajo Loop Trail, Sunset
    # Point), not repeat them.
    assert any(name in day2[1]["summary"].lower() for name in ("mossy cave trail", "bryce point"))
    assert "navajo loop trail" not in day2[1]["summary"].lower()
    assert "sunset point" not in day2[1]["summary"].lower()
    # Last-day evening for a transfer destination stays local and relaxed --
    # no after-dinner drive implied; the onward drive is explicitly framed
    # as happening the next morning instead.
    last_evening = day2[2]["summary"].lower()
    assert "onward drive" not in last_evening
    assert "capitol reef national park" in last_evening
    assert "next morning" in last_evening
    assert "start with a different priority trailhead or district than day 1" not in day2_text


def test_inject_travel_realism_scrubs_premature_departure_mentions_on_earlier_days() -> None:
    """Regression grounded in the project owner's real review finding: 'The
    scheduler is also suggesting departing Capitol Reef each of the 3 days
    for Moab.' The LLM's own schedule text is not otherwise touched by
    normalization -- if it echoes departure/onward-drive framing on Day 1
    or Day 2 of a 3-day stay (not just the actual last day), that text must
    be scrubbed and replaced with something that doesn't reference leaving,
    since the traveler isn't departing yet on those days."""
    g = _gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Explore Cathedral Valley at sunrise."},
                {"period": "Afternoon", "summary": "Hike Capitol Gorge and Grand Wash."},
                {
                    "period": "Evening",
                    "summary": "Wrap up and prepare for the onward drive to Moab tomorrow; keep departure buffers.",
                },
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Drive the Scenic Drive to Capitol Gorge."},
                {"period": "Afternoon", "summary": "Visit Fruita Historic District."},
                {
                    "period": "Evening",
                    "summary": "Sunset at Panorama Point, then head to Moab in preparation for departure.",
                },
            ],
        },
        {
            "day_label": "Day 3",
            "periods": [
                {"period": "Morning", "summary": "Final morning at Hickman Bridge."},
                {"period": "Afternoon", "summary": "Last stop at Chimney Rock."},
                {"period": "Evening", "summary": "Dinner in Torrey."},
            ],
        },
    ]

    out = g._inject_travel_realism(
        days,
        {"drive_time": "2 hr 20 min"},
        "Bryce Canyon National Park",
        "Moab",
    )

    day1_evening = out[0]["periods"][2]["summary"].lower()
    day2_evening = out[1]["periods"][2]["summary"].lower()
    day3_evening = out[2]["periods"][2]["summary"].lower()

    # Days 1 and 2 are not the departure day -- no mention of Moab, driving
    # onward, or departure buffers should survive.
    assert "moab" not in day1_evening
    assert "onward drive" not in day1_evening
    assert "departure buffer" not in day1_evening
    assert "moab" not in day2_evening
    assert "onward drive" not in day2_evening

    # Only Day 3 (the actual last day here) carries the onward-travel note,
    # and it must not imply an after-dinner drive -- the drive happens the
    # next morning instead.
    assert "moab" in day3_evening
    assert "onward drive" not in day3_evening
    assert "next morning" in day3_evening


def test_inject_travel_realism_moab_schedule_avoids_repeats_and_multi_park_blocks() -> None:
    """Regression grounded in the real dipstick62 Moab output (project
    owner's exact words): 'Schedule for Moab should avoid repeats (Moab
    Giants Dinosaur Park repeated multiple times), and should focus on one
    of two subareas, not go back and forth.' Concrete example given: dance
    cards like 'Moab Giants Dinosaur Park (1h 30m), Canyonlands National
    Park (1h 30m), Arches National Park (1h 30m)' packed into one time
    block, not tuned to minimal driving time.

    No real inter-attraction distance matrix exists in this codebase (see
    docs/design/schedule-normalization.md's v2.1 activity-budget section),
    so this doesn't verify true geographic realism -- it verifies the two
    narrow, cheap-to-check guards that ARE fixable without that data:
    (1) an attraction packed into one day's block is never re-packed into
    a later day's block for the same multi-day stay, and (2) no single
    time block ever combines more than one named National/State
    Park/Monument/Forest/Recreation Area -- the strong, cheap signal that
    two items are genuinely separate, multi-mile drives in different
    directions, not co-located in-town stops.
    """
    g = _gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Free morning."},
                {"period": "Afternoon", "summary": "Old afternoon text."},
                {"period": "Evening", "summary": "Dinner in Moab."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Free morning."},
                {"period": "Afternoon", "summary": "Old afternoon text."},
                {"period": "Evening", "summary": "Dinner in Moab."},
            ],
        },
        {
            "day_label": "Day 3",
            "periods": [
                {"period": "Morning", "summary": "Free morning."},
                {"period": "Afternoon", "summary": "Old afternoon text."},
                {"period": "Evening", "summary": "Dinner in Moab."},
            ],
        },
    ]
    # Real Moab-area top_attractions shape: one in-town attraction and two
    # genuinely separate national parks, each ~1.5 hours if you count the
    # round-trip drive plus time on-site -- exactly the dipstick62 example.
    attractions = [
        {"name": "Moab Giants Dinosaur Park", "duration": "1 hour 30 min"},
        {"name": "Canyonlands National Park", "duration": "1 hour 30 min"},
        {"name": "Arches National Park", "duration": "1 hour 30 min"},
        {"name": "Moab Museum", "duration": "1 hour"},
        {"name": "Moab Skydive", "duration": "1 hour"},
    ]

    out = g._inject_travel_realism(
        days,
        getting_here={},  # no drive -- isolates Day 2+ capacity-aware packing
        previous_destination="Capitol Reef National Park",
        # Not the trip's last destination -- otherwise the last-day
        # return-travel reservation overwrites Day 3's Afternoon/Evening
        # after packing runs, which isn't what this test is isolating.
        next_destination="Telluride",
        attractions=attractions,
        default_daily_activity_hours=5,
    )

    major_park_names = ("canyonlands national park", "arches national park")

    day2_afternoon = out[1]["periods"][1]["summary"].lower()
    day3_afternoon = out[2]["periods"][1]["summary"].lower()

    # Both days actually got a capacity-aware pack (sanity check the fixture
    # produces the scenario under test at all).
    assert "consider one or more of the following" in day2_afternoon
    assert "consider one or more of the following" in day3_afternoon

    # Guard 1 (dedup): no attraction packed on Day 2 is repeated on Day 3.
    for attr in attractions:
        name = attr["name"].lower()
        assert not (name in day2_afternoon and name in day3_afternoon), (
            f"'{attr['name']}' was packed into both Day 2 and Day 3 -- "
            "cross-day dedup guard failed to exclude an already-used attraction."
        )

    # Guard 2 (one major destination per block): neither day's block names
    # both Canyonlands AND Arches together -- two separate national parks in
    # different directions must never share a single time block.
    for day_afternoon in (day2_afternoon, day3_afternoon):
        major_count = sum(1 for name in major_park_names if name in day_afternoon)
        assert major_count <= 1, (
            f"Block names {major_count} major/off-site parks together: {day_afternoon!r}"
        )

    # The exact reported bad pattern (all three landmarks in one block) must
    # never occur, in either day.
    for day_afternoon in (day2_afternoon, day3_afternoon):
        assert not (
            "moab giants dinosaur park" in day_afternoon
            and "canyonlands national park" in day_afternoon
            and "arches national park" in day_afternoon
        )


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


def test_inject_travel_realism_rotates_evening_focus_across_a_multi_day_stay() -> None:
    """Regression grounded in the project owner's real review finding
    ('still repetition in evenings across days') and the real SW2026-
    dipstick67 output for Bryce Canyon National Park: Day 1 and Day 2
    Evening both read 'Enjoy a sunset from Sunrise Point...' -- byte-
    identical apart from the dinner restaurant, which already rotates.

    Morning and Afternoon periods already rotate which attraction they
    name across days (see test_inject_travel_realism_rotates_focus_to_
    reduce_adjacent_duplicates above) so a multi-day stay doesn't repeat
    the same highlight -- Evening had no equivalent rotation, only the
    dinner restaurant varied while the pre-dinner activity clause stayed
    fixed to whichever attraction the source schedule happened to name."""
    g = _gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Start at Navajo Loop Trail for cooler temps."},
                {"period": "Afternoon", "summary": "Continue at Queens Garden Trail."},
                {
                    "period": "Evening",
                    "summary": "Enjoy a sunset from Sunrise Point. Afterward, have dinner at Bryce Canyon Lodge Restaurant.",
                },
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Return to Queens Garden Trail for photos."},
                {"period": "Afternoon", "summary": "Hike Navajo Loop Trail if time allows."},
                {
                    "period": "Evening",
                    "summary": "Enjoy a sunset from Sunrise Point. Afterward, have dinner at Bryce Canyon Lodge Restaurant.",
                },
            ],
        },
        {
            "day_label": "Day 3",
            "periods": [
                {"period": "Morning", "summary": "Explore Navajo Loop Trail once more."},
                {"period": "Afternoon", "summary": "Relax at Queens Garden Trail."},
                {
                    "period": "Evening",
                    "summary": "Enjoy a sunset from Sunrise Point. Afterward, have dinner at Bryce Canyon Lodge Restaurant.",
                },
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
            {"name": "Queens Garden Trail"},
            {"name": "Sunrise Point"},
        ],
        restaurants=[{"name": "Bryce Canyon Lodge Restaurant"}, {"name": "The Pizza Place"}],
    )

    day1_evening = out[0]["periods"][2]["summary"].lower()
    day2_evening = out[1]["periods"][2]["summary"].lower()
    assert "sunrise point" in day1_evening
    # Day 2's evening must name a different attraction than Day 1's --
    # not just a different restaurant -- to actually fix the repetition
    # rather than just varying the dinner half of the sentence.
    assert day2_evening != day1_evening
    assert "sunrise point" not in day2_evening
    assert "navajo loop trail" in day2_evening or "queens garden trail" in day2_evening


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


def test_filter_high_clearance_drives_removed_when_manifest_declares_no_vehicle() -> None:
    g = _gen()
    trip = {
        "trip": {"has_high_clearance_vehicle": False},
        "destinations": [
            {
                "name": "Canyonlands",
                "scenic_drives": [
                    {"title": "Paved Overlook Road", "vehicle_requirement": "Any vehicle"},
                    {"title": "White Rim Road", "vehicle_requirement": "4WD required"},
                    {"title": "Elephant Hill", "vehicle_requirement": "High-clearance recommended"},
                    {"title": "Scenic Byway", "vehicle_requirement": "Paved — any vehicle"},
                    {"title": "Mountain Village Gondola", "vehicle_requirement": "Gondola (no vehicle needed)"},
                    {"title": "No Field Set"},
                ],
            }
        ],
    }

    g._filter_drives_requiring_high_clearance_vehicle(trip)

    titles = [d["title"] for d in trip["destinations"][0]["scenic_drives"]]
    assert "White Rim Road" not in titles
    assert "Elephant Hill" not in titles
    assert "Paved Overlook Road" in titles
    assert "Scenic Byway" in titles
    assert "Mountain Village Gondola" in titles
    assert "No Field Set" in titles


def test_filter_high_clearance_drives_noop_when_manifest_field_absent() -> None:
    g = _gen()
    trip = {
        "trip": {},
        "destinations": [
            {
                "name": "Canyonlands",
                "scenic_drives": [
                    {"title": "White Rim Road", "vehicle_requirement": "4WD required"},
                    {"title": "Elephant Hill", "vehicle_requirement": "High-clearance recommended"},
                    {"title": "Paved Overlook Road", "vehicle_requirement": "Any vehicle"},
                ],
            }
        ],
    }

    g._filter_drives_requiring_high_clearance_vehicle(trip)

    titles = [d["title"] for d in trip["destinations"][0]["scenic_drives"]]
    assert titles == ["White Rim Road", "Elephant Hill", "Paved Overlook Road"]


def test_filter_high_clearance_drives_noop_when_manifest_declares_has_vehicle() -> None:
    g = _gen()
    trip = {
        "trip": {"has_high_clearance_vehicle": True},
        "destinations": [
            {
                "name": "Canyonlands",
                "scenic_drives": [
                    {"title": "White Rim Road", "vehicle_requirement": "4WD required"},
                    {"title": "Paved Overlook Road", "vehicle_requirement": "Any vehicle"},
                ],
            }
        ],
    }

    g._filter_drives_requiring_high_clearance_vehicle(trip)

    titles = [d["title"] for d in trip["destinations"][0]["scenic_drives"]]
    assert titles == ["White Rim Road", "Paved Overlook Road"]


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


def test_override_grouped_child_distance_from_geocode_fixes_impossible_ai_guess() -> None:
    """Real dipstick68 regression: Arches National Park (group_with: moab)
    rendered distance_miles=212 / drive_time="30 min" from the AI's own
    getting_here guess -- physically impossible (424 mph) and nowhere close
    to the real ~7-minute drive from Moab. Real geocoded coordinates from
    that run's console log (Moab lat=38.5738 lng=-109.5462, Arches
    lat=38.7265 lng=-109.5630) should produce a small, sane distance/time
    instead once the override runs."""
    g = _gen()
    trip = {
        "destinations": [
            {
                "id": "moab",
                "name": "Moab",
                "lat": 38.5738,
                "lng": -109.5462,
                "ai_content": {"getting_here": {"distance_miles": 5, "drive_time": "10 min"}},
            },
            {
                "id": "arches",
                "name": "Arches National Park",
                "group_with": "moab",
                "lat": 38.7265,
                "lng": -109.5630,
                "ai_content": {
                    "getting_here": {
                        "distance_miles": 212,
                        "drive_time": "30 min",
                        "route_summary": "Take US-191 N from Moab.",
                    }
                },
            },
        ]
    }

    g._override_grouped_child_distance_from_geocode(trip)

    arches_gh = trip["destinations"][1]["ai_content"]["getting_here"]
    assert arches_gh["distance_miles"] != 212
    assert arches_gh["drive_time"] != "30 min"
    assert arches_gh["distance_miles"] < 20
    assert "hr" not in arches_gh["drive_time"]  # under an hour, minutes-only string
    # route_summary (real prose, not a derived number) is left untouched.
    assert arches_gh["route_summary"] == "Take US-191 N from Moab."
    # The base entry itself is never a grouped child -- its own numbers
    # (however implausible) are out of scope for this override.
    moab_gh = trip["destinations"][0]["ai_content"]["getting_here"]
    assert moab_gh["distance_miles"] == 5
    assert moab_gh["drive_time"] == "10 min"


def test_estimate_haversine_route_moab_to_arches_is_a_few_miles_under_15_minutes() -> None:
    """Direct check of the pure Haversine helper against the exact real
    coordinates from the dipstick68 run, independent of the trip-level
    override wiring above."""
    from generator.ai_content import _estimate_haversine_route

    miles, time_str = _estimate_haversine_route(38.5738, -109.5462, 38.7265, -109.5630)

    assert miles is not None and time_str is not None
    assert 1 <= miles <= 20
    assert time_str == "14 min"


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


def test_split_sentences_keeps_st_abbreviation_attached_to_next_word() -> None:
    r"""dipstick58 regression: naive `re.split(r"(?<=[.!?])\s+")` treats "St."
    as its own one-word sentence, inflating the real sentence count for text
    like "...St. George Dinosaur Discovery Site at Johnson Farm...". The
    abbreviation-aware splitter must keep "St." attached to what follows."""
    text = (
        "Enjoy dinner at Painted Pony Restaurant. St. George Dinosaur Discovery "
        "Site at Johnson Farm. Explore the interactive exhibits."
    )

    sentences = AIContentGenerator._split_sentences(text)

    assert sentences == [
        "Enjoy dinner at Painted Pony Restaurant.",
        "St. George Dinosaur Discovery Site at Johnson Farm.",
        "Explore the interactive exhibits.",
    ]


def test_split_sentences_handles_other_common_abbreviations() -> None:
    text = "Meet Dr. Smith at the trailhead. Then drive to Mt. Rainier."
    assert AIContentGenerator._split_sentences(text) == [
        "Meet Dr. Smith at the trailhead.",
        "Then drive to Mt. Rainier.",
    ]


def test_cap_period_sentences_does_not_truncate_when_abbreviation_inflates_count() -> None:
    """Before the fix, this exact 3-sentence summary was miscounted as 4
    "sentences" (because of the mid-string "St."), and the cap at
    max_sentences=3 wrongly dropped the real third sentence."""
    days = [
        {
            "periods": [
                {
                    "period": "Evening",
                    "summary": (
                        "Enjoy dinner at Painted Pony Restaurant. St. George Dinosaur "
                        "Discovery Site at Johnson Farm. Explore the interactive exhibits."
                    ),
                }
            ]
        }
    ]

    out = AIContentGenerator._cap_period_sentences(days)

    assert out[0]["periods"][0]["summary"] == (
        "Enjoy dinner at Painted Pony Restaurant. St. George Dinosaur "
        "Discovery Site at Johnson Farm. Explore the interactive exhibits."
    )


def test_cap_period_sentences_still_truncates_genuinely_long_summaries() -> None:
    days = [
        {
            "periods": [
                {
                    "period": "Morning",
                    "summary": "One sentence. Two sentence. Three sentence. Four sentence.",
                }
            ]
        }
    ]

    out = AIContentGenerator._cap_period_sentences(days, max_sentences=3)

    assert out[0]["periods"][0]["summary"] == "One sentence. Two sentence. Three sentence."


def test_is_evening_unsuitable_venue_matches_museum_style_keywords() -> None:
    assert AIContentGenerator._is_evening_unsuitable_venue(
        {"name": "St. George Dinosaur Discovery Site at Johnson Farm", "type": "attraction"}
    )
    assert AIContentGenerator._is_evening_unsuitable_venue(
        {"name": "Zion Human History Museum", "type": "attraction"}
    )
    assert AIContentGenerator._is_evening_unsuitable_venue({"name": "Any Old Place", "type": "museum"})
    assert not AIContentGenerator._is_evening_unsuitable_venue(
        {"name": "Sunrise Point", "type": "viewpoint"}
    )
    assert not AIContentGenerator._is_evening_unsuitable_venue(
        {"name": "Navajo Loop Trail", "type": "hike"}
    )


def test_inject_travel_realism_strips_museum_mention_from_evening_schedule() -> None:
    """dipstick58 regression: St. George Day 1 Evening text sent travelers to
    a paleontology discovery site after dinner -- realistically closed by
    then. The mention should be stripped from Evening, keeping dinner."""
    g = _gen()
    days = [{
        "day_label": "Day 1",
        "periods": [
            {"period": "Morning", "summary": "Departure prep, airport transfer, and logistics before the main travel leg."},
            {"period": "Afternoon", "summary": "Travel from Las Vegas International Airport (depart around 1:30 PM)."},
            {
                "period": "Evening",
                "summary": (
                    "Enjoy dinner at Painted Pony Restaurant. St. George Dinosaur "
                    "Discovery Site at Johnson Farm. Explore the interactive exhibits."
                ),
            },
        ],
    }]
    attractions = [
        {"name": "Jenny's Canyon Trail", "type": "trail"},
        {"name": "St. George Dinosaur Discovery Site at Johnson Farm", "type": "attraction"},
    ]

    updated = g._inject_travel_realism(
        days,
        {},
        "none",
        "Zion National Park",
        attractions=attractions,
        restaurants=[{"name": "Painted Pony Restaurant"}],
    )

    evening_summary = updated[0]["periods"][2]["summary"]
    assert "Discovery Site" not in evening_summary
    assert "Painted Pony Restaurant" in evening_summary


def test_inject_travel_realism_leaves_evening_unchanged_when_no_unsuitable_venue() -> None:
    g = _gen()
    days = [{
        "day_label": "Day 1",
        "periods": [
            {"period": "Morning", "summary": "Start at Santa Fe Plaza."},
            {"period": "Afternoon", "summary": "Browse Canyon Road galleries."},
            {"period": "Evening", "summary": "Enjoy dinner at The Shed, then a sunset walk around the plaza."},
        ],
    }]
    attractions = [{"name": "Santa Fe Plaza", "type": "viewpoint"}]

    updated = g._inject_travel_realism(
        days, {}, "Albuquerque", "Taos", attractions=attractions, restaurants=[{"name": "The Shed"}],
    )

    assert updated[0]["periods"][2]["summary"] == "Enjoy dinner at The Shed, then a sunset walk around the plaza."
