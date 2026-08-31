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
        {"travel_time": "2 hrs 15 min", "route_summary": "US-89 to UT-12"},
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
        {"travel_time": "1 hr 45 min"},
        "Zion National Park",
        "Capitol Reef National Park",
    )
    first_summary = updated[0]["periods"][0]["summary"]
    assert not first_summary.lstrip().startswith(":")
    assert "arrive at bryce canyon and settle in." in first_summary.lower()


def test_inject_travel_realism_corrects_implausible_morning_activity_claim_on_arrival_day() -> None:
    """Regression grounded in real SW2026-dipstick69 output, Bryce Canyon
    National Park Day 1 (arrival day from Zion, 2 hr 15 min / 135 min
    drive): 'Arrive at Bryce Canyon National Park and check in to your
    lodging. After settling in, head to Sunrise Point for morning views of
    the canyon.' The AI's own Morning text already narrates arrival
    (morning_already_arrival_aware), so the deterministic 'Travel from
    X...arrival around Y' override never fires -- but the same sentence
    also claims a 'morning views' activity that the actual computed
    arrival time (10:00 AM default day start + 135 min drive = 12:15 PM)
    makes physically impossible: you cannot catch morning views at a
    viewpoint you don't reach until just after noon.

    The real attraction mention (Sunrise Point) and the AI's own arrival/
    check-in narration must survive -- only the false time-of-day claim is
    corrected, per this codebase's established preference for keeping
    real, specific AI-authored content over generic filler.
    """
    g = _gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {
                    "period": "Morning",
                    "summary": (
                        "Arrive at Bryce Canyon National Park and check in to your lodging. "
                        "After settling in, head to Sunrise Point for morning views of the canyon."
                    ),
                },
                {"period": "Afternoon", "summary": "Explore the visitor center."},
                {"period": "Evening", "summary": "Dinner in town."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Free morning."},
                {"period": "Afternoon", "summary": "Free afternoon."},
                {"period": "Evening", "summary": "Dinner in town."},
            ],
        },
    ]

    out = g._inject_travel_realism(
        days,
        {"travel_time": "2 hrs 15 min"},
        "Zion National Park",
        "Capitol Reef National Park",
        default_day_start_time="10:00 AM",
    )

    morning_summary = out[0]["periods"][0]["summary"].lower()

    # The false, physically-impossible claim must be gone.
    assert "morning views" not in morning_summary
    # The AI's own real content -- arrival/check-in narration and the real
    # attraction name -- must survive; this is a targeted correction, not a
    # rewrite into generic filler.
    assert "arrive at bryce canyon national park" in morning_summary
    assert "check in to your lodging" in morning_summary
    assert "sunrise point" in morning_summary
    # The corrected text is honest about when arrival actually happens.
    assert "12:15 pm" in morning_summary


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


def test_generate_destination_content_applies_scenic_drive_cap_before_url_discovery_would_see_it() -> None:
    """Integration-level check that generate_destination_content trims
    dest['scenic_drives'] to the 1/day cap itself (via
    _apply_manifest_scenic_drive_target), not just that a later
    normalize_trip_content pass eventually cleans it up -- URL discovery
    reads dest['scenic_drives'] before normalize_trip_content ever runs, so
    an uncapped list here would still cost one live search call per extra
    drive. A 1-day destination (dates spans a single day) caps at 1."""
    g = _gen_with_bundle_templates()
    g._llm = type("MockLLM", (), {"provider": "openai"})()
    g._max_concurrent_destinations = 5
    with patch.object(
        g,
        "_generate_destination_bundle",
        return_value={
            "destination_content": {},
            "what_to_know": {},
            "scenic_drives": [{"title": f"Drive {i}"} for i in range(5)],
        },
    ):
        trip = {
            "trip": {},
            "destinations": [{"name": "St. George, Utah", "dates": "October 17, 2026"}],
        }
        g.generate_destination_content(trip)

    dest = trip["destinations"][0]
    assert [d["title"] for d in dest["scenic_drives"]] == ["Drive 0"]


def test_resolve_grouping_aware_prev_next_names_skips_day_trip_children() -> None:
    """Regression grounded in the real Sandbox/sw_manifest.yaml shape and a
    real project owner finding: Moab's own schedule's last evening read
    'Enjoy a relaxed, local evening; the drive to Arches National Park
    happens the next morning, not tonight.' Project owner: 'The algorithm
    for the schedule does not understand the notion of day trips... The
    scheduler hasn't incorporated the idea of a day trip into its
    scheduling.'

    Manifest order: Moab (base), Arches National Park (`group_with: moab`,
    a day trip FROM Moab), Canyonlands National Park (`group_with: moab`,
    also a day trip FROM Moab), Telluride (the real next destination). The
    naive "adjacent list entry" resolution this replaced gave Moab's
    next_destination="Arches National Park" -- wrong on two counts: Arches
    was already visited as a day trip, and the real next-morning drive is
    to Telluride. It also gave Canyonlands's previous_destination="Arches
    National Park", implying a direct Arches-to-Canyonlands drive that
    never happens (both are day trips FROM the shared Moab lodging)."""
    destinations = [
        {"id": "capitolreef", "name": "Capitol Reef National Park"},
        {"id": "moab", "name": "Moab"},
        {"id": "arches", "name": "Arches National Park", "group_with": "moab"},
        {"id": "canyonlands", "name": "Canyonlands National Park", "group_with": "moab"},
        {"id": "telluride", "name": "Telluride"},
    ]

    prev_names, next_names = AIContentGenerator._resolve_grouping_aware_prev_next_names(destinations)

    assert prev_names == ["none", "Capitol Reef National Park", "Moab", "Moab", "Moab"]
    assert next_names == ["Moab", "Telluride", "Telluride", "Telluride", ""]


def test_resolve_group_day_trip_names_gives_base_both_children_only() -> None:
    """Real gap this closes (GH #68 x schedule generation): Moab's own
    schedule-generation candidate pool (top_attractions) is built purely
    from Moab's own AI-generated content -- nothing ever merged in Arches
    National Park's or Canyonlands National Park's names, despite both
    being real, dated `group_with: moab` day trips FROM Moab. A real
    published run showed the resulting asymmetry: Canyonlands got one
    schedule mention (by pure AI-generation luck -- see the docstring on
    _resolve_group_day_trip_names for the full trace showing that name
    isn't even present in Moab's own top_attractions), Arches got none at
    all. _resolve_group_day_trip_names resolves, per destination, which
    day-trip children's NAMES a base destination should be given as extra
    nameable candidates -- manifest-only (id/name/group_with), since a
    grouped child's own AI-generated attractions don't exist yet at the
    point this is needed (generate_destination_content runs every
    destination's LLM call in parallel)."""
    destinations = [
        {"id": "capitolreef", "name": "Capitol Reef National Park"},
        {"id": "moab", "name": "Moab"},
        {"id": "arches", "name": "Arches National Park", "group_with": "moab"},
        {"id": "canyonlands", "name": "Canyonlands National Park", "group_with": "moab"},
        {"id": "telluride", "name": "Telluride"},
    ]

    day_trip_names = AIContentGenerator._resolve_group_day_trip_names(destinations)

    assert day_trip_names == [
        [],  # Capitol Reef -- ungrouped, no children
        ["Arches National Park", "Canyonlands National Park"],  # Moab -- the base
        [],  # Arches -- itself a grouped child, never gets its own siblings
        [],  # Canyonlands -- same
        [],  # Telluride -- ungrouped, no children
    ]


def test_inject_travel_realism_moab_schedule_can_name_arches_not_just_canyonlands() -> None:
    """End-to-end companion to the resolver test above, grounded in the
    real published-run asymmetry: with only Moab's own real top_attractions
    (Moab Giants Dinosaur Park, Corona and Bowtie Arch via Corona Arch
    Trail, Windows Loop and Turret Arch Trail -- the real rendered set for
    Moab) fed in as `attractions`, and Moab's real day-trip children fed in
    as `group_day_trip_names`, the day-level Morning/Afternoon/Evening
    focus rotation (which already rotates among `attractions`) must also be
    able to land on a day-trip child's own name -- proving the names are
    genuinely wired into the same rotation mechanism that names real
    attractions, not merely accepted as an unused parameter."""
    g = _gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Start at Moab Giants Dinosaur Park for cooler temps."},
                {"period": "Afternoon", "summary": "Continue at Corona and Bowtie Arch via Corona Arch Trail."},
                {"period": "Evening", "summary": "Dinner in town."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Return to Corona and Bowtie Arch via Corona Arch Trail for photos."},
                {"period": "Afternoon", "summary": "Hike Moab Giants Dinosaur Park if time allows."},
                {"period": "Evening", "summary": "Dinner and sunset."},
            ],
        },
    ]

    out = g._inject_travel_realism(
        days,
        {"travel_time": "1 hr 45 min"},
        "Capitol Reef National Park",
        "Telluride",
        attractions=[
            {"name": "Moab Giants Dinosaur Park"},
            {"name": "Corona and Bowtie Arch via Corona Arch Trail"},
            {"name": "Windows Loop and Turret Arch Trail"},
        ],
        restaurants=[{"name": "Desert Bistro"}, {"name": "Moab Brewery"}],
        group_day_trip_names=["Arches National Park", "Canyonlands National Park"],
    )

    all_summaries = " ".join(
        period["summary"] for day in out for period in day["periods"]
    ).lower()
    # At least one of the two real day-trip children must be nameable
    # somewhere in the rotated schedule -- before this fix, neither name
    # existed anywhere in `attraction_names`, so the rotation/scrub
    # mechanisms had structurally zero chance of ever naming either one.
    assert "arches national park" in all_summaries or "canyonlands national park" in all_summaries


def test_generate_destination_content_moab_gets_telluride_not_arches_as_next_destination() -> None:
    """Integration-level companion to the direct resolver test above: the
    real _generate_destination_bundle call for Moab must receive
    next_destination="Telluride", not "Arches National Park", so
    _inject_travel_realism's last-evening framing points at the real next
    relocation destination instead of a grouped day-trip child."""
    g = _gen_with_bundle_templates()
    g._llm = type("MockLLM", (), {"provider": "openai"})()
    g._max_concurrent_destinations = 5
    calls: dict[str, tuple[str, str]] = {}

    def _fake_bundle(
        dest: dict,
        trip_meta: dict,
        previous_destination: str,
        next_destination: str,
        group_day_trip_names: list | None = None,
    ) -> dict:
        calls[dest["name"]] = (previous_destination, next_destination)
        return {"destination_content": {}, "what_to_know": {}, "scenic_drives": []}

    with patch.object(g, "_generate_destination_bundle", side_effect=_fake_bundle):
        trip = {
            "trip": {},
            "destinations": [
                {"id": "moab", "name": "Moab", "dates": "October 22-24, 2026"},
                {"id": "arches", "name": "Arches National Park", "dates": "October 23, 2026", "group_with": "moab"},
                {
                    "id": "canyonlands",
                    "name": "Canyonlands National Park",
                    "dates": "October 24, 2026",
                    "group_with": "moab",
                },
                {"id": "telluride", "name": "Telluride", "dates": "October 24-26, 2026"},
            ],
        }
        g.generate_destination_content(trip)

    assert calls["Moab"] == ("none", "Telluride")
    assert calls["Arches National Park"] == ("Moab", "Telluride")
    assert calls["Canyonlands National Park"] == ("Moab", "Telluride")
    assert calls["Telluride"] == ("Moab", "")


def test_normalize_schedule_moab_day_trip_days_stay_local_only_last_evening_mentions_real_next_destination() -> None:
    """With next_destination now correctly resolved to "Telluride" (not the
    "Arches National Park" day-trip child -- see the two tests above), this
    verifies _inject_travel_realism's existing day-level machinery already
    produces the two genuinely different behaviors a day-trip stay needs
    for its own multi-day schedule, once given the right next_destination:

    - Day 2 (the Arches day-trip day, chronologically BEFORE Moab's actual
      departure) must carry no onward-drive/next-destination language at
      all -- the traveler returns to the same Moab lodging that night, not
      Telluride.
    - Only Day 3 (Moab's genuine last evening before the real relocation)
      gets the "drive to X next morning" framing, and it must name the real
      next destination (Telluride), never the day-trip child.

    This is the existing scrub-vs-last-day architecture (unchanged by the
    grouping fix) verified against the specific real-shaped Moab/Arches/
    Canyonlands/Telluride data that motivated it.
    """
    g = _gen()
    schedule = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Settle into Moab and explore downtown."},
                {"period": "Afternoon", "summary": "Visit the Moab Museum."},
                {"period": "Evening", "summary": "Dinner at a local restaurant."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Head out early for the Arches day trip."},
                {"period": "Afternoon", "summary": "Continue exploring Arches National Park."},
                {"period": "Evening", "summary": "Return to Moab for dinner."},
            ],
        },
        {
            "day_label": "Day 3",
            "periods": [
                {"period": "Morning", "summary": "Head out early for the Canyonlands day trip."},
                {"period": "Afternoon", "summary": "Continue exploring Canyonlands National Park."},
                {"period": "Evening", "summary": "Return to Moab for a final dinner."},
            ],
        },
    ]

    out = g._normalize_schedule(
        schedule=schedule,
        restaurants=[{"name": "Moab Kitchen"}, {"name": "Desert Bistro"}],
        dates="October 22-24, 2026",
        attractions=[{"name": "Delicate Arch"}, {"name": "Corona Arch Trail"}],
        getting_here={"travel_time": "2 hr"},
        previous_destination="Capitol Reef National Park",
        next_destination="Telluride",
    )

    day2_evening = out[1]["periods"][2]["summary"].lower()
    day3_evening = out[2]["periods"][2]["summary"].lower()

    # Day 2 (the Arches day-trip day) stays local -- no mention of the real
    # next destination, no onward-drive framing, since the traveler returns
    # to the same Moab lodging that night.
    assert "telluride" not in day2_evening
    assert "next morning" not in day2_evening

    # Only Day 3 (Moab's genuine last evening) carries the onward-travel
    # note, and it must name the real next destination.
    assert "telluride" in day3_evening
    assert "next morning" in day3_evening
    assert "arches" not in day3_evening
    assert "canyonlands" not in day3_evening


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


def test_resolve_attraction_target_default_is_five_per_day() -> None:
    """Cost-reduction pass raised attractions 2 -> 4 to share a uniform
    ceiling with restaurants/en-route stops; 2026-08-21 raised it again to 5,
    paired with dropping scenic drives to 1/day. See
    docs/design/per-day-item-caps.md."""
    g = _gen()
    assert g._resolve_attraction_target({}, {}) == 5


def test_resolve_attraction_target_destination_override_wins_over_trip() -> None:
    g = _gen()
    assert g._resolve_attraction_target({"attractions_per_day": 2}, {"attractions_per_day": 6}) == 2


def test_manifest_attraction_target_scales_with_bryce_canyon_three_day_stay() -> None:
    """Grounded in the real sw_manifest.yaml Bryce Canyon National Park entry
    (a 3-day stay, 'October 19-21, 2026'): at the 2026-08-21 default of
    5/day, up to 15 attractions should survive, against 12 at the previous
    4/day and 6 at the original 2/day."""
    g = _gen()
    items = [
        {"name": f"Attraction {i}", "type": "attraction", "rating": 4.9 - i * 0.05, "votes": 100, "must_see": False}
        for i in range(15)
    ]

    out = g._apply_manifest_attraction_target(
        items, dates="October 19-21, 2026", attractions_per_day=g._resolve_attraction_target({}, {})
    )

    assert len(out) == 15


def test_manifest_attraction_target_scales_with_st_george_one_day_stopover() -> None:
    """Grounded in the real sw_manifest.yaml St. George, Utah entry (a 1-day
    stopover, 'October 17, 2026'): at the 2026-08-21 default of 5/day, up to
    5 attractions should survive, against 4 at the previous 4/day."""
    g = _gen()
    items = [
        {"name": f"Attraction {i}", "type": "attraction", "rating": 4.9 - i * 0.05, "votes": 100, "must_see": False}
        for i in range(8)
    ]

    out = g._apply_manifest_attraction_target(
        items, dates="October 17, 2026", attractions_per_day=g._resolve_attraction_target({}, {})
    )

    assert len(out) == 5


def test_resolve_restaurant_target_default_is_four_per_day() -> None:
    g = _gen()
    assert g._resolve_restaurant_target({}, {}) == 4


def test_resolve_restaurant_target_destination_override_wins_over_trip() -> None:
    g = _gen()
    assert g._resolve_restaurant_target({"restaurants_per_day": 2}, {"restaurants_per_day": 6}) == 2


def test_resolve_restaurant_target_falls_back_to_trip_value() -> None:
    g = _gen()
    assert g._resolve_restaurant_target({}, {"restaurants_per_day": 5}) == 5


def test_manifest_restaurant_target_prefers_highest_rated_candidates() -> None:
    g = _gen()
    items = [
        {"name": "Lower-rated Spot", "cuisine": "American", "rating": 4.1, "votes": 40},
        {"name": "Top Pick", "cuisine": "Italian", "rating": 4.9, "votes": 300},
        {"name": "Mid-tier Diner", "cuisine": "Thai", "rating": 4.5, "votes": 120},
        {"name": "Backup Grill", "cuisine": "Steakhouse", "rating": 4.3, "votes": 90},
    ]

    out = g._apply_manifest_restaurant_target(items, dates="October 7-8, 2026", restaurants_per_day=1)

    assert [item["name"] for item in out] == ["Top Pick", "Mid-tier Diner"]


def test_manifest_restaurant_target_keeps_protected_names_in_output() -> None:
    g = _gen()
    items = [
        {"name": "Other Spot", "rating": 4.9, "votes": 200},
        {"name": "Second Spot", "rating": 4.8, "votes": 180},
        {"name": "Traveler's Pick", "rating": 3.9, "votes": 10},
    ]

    out = g._apply_manifest_restaurant_target(
        items,
        dates="October 7-8, 2026",
        restaurants_per_day=1,
        protected_names=["Traveler's Pick"],
    )

    names = [item["name"] for item in out]
    assert "Traveler's Pick" in names


def test_manifest_restaurant_target_scales_with_santa_fe_two_day_stay() -> None:
    """Grounded in the real sw_manifest.yaml Santa Fe entry (a 2-day stay,
    'October 27-29, 2026' spans 3 calendar days in that manifest, but this
    uses a plain 2-day range to keep the assertion focused on the day-count
    formula itself: 4/day * 2 days = 8)."""
    g = _gen()
    items = [
        {"name": f"Restaurant {i}", "cuisine": "American", "rating": 4.9 - i * 0.05, "votes": 50}
        for i in range(12)
    ]

    out = g._apply_manifest_restaurant_target(items, dates="October 27-28, 2026", restaurants_per_day=4)

    assert len(out) == 8


def test_resolve_enroute_target_default_is_four() -> None:
    g = _gen()
    assert g._resolve_enroute_target({}, {}) == 4


def test_resolve_enroute_target_destination_override_wins_over_trip() -> None:
    g = _gen()
    assert g._resolve_enroute_target({"en_route_stops_per_day": 1}, {"en_route_stops_per_day": 6}) == 1


def test_manifest_enroute_target_prioritizes_shorter_detours_when_trimming() -> None:
    """Real project-owner ask: "Can we prioritize the enroutes to keep it to
    the top 4 or less? Could also save calls." Unlike the day-count-scaled
    attraction/restaurant/scenic-drive caps, en-route stops are capped flat
    (a single arrival leg happens once regardless of length of stay -- see
    _resolve_enroute_target's docstring) and prioritized by
    detour_distance_miles (the AI's own self-reported estimate, always
    available before any search call) -- a shorter detour is objectively
    more worth keeping as a quick "can't-miss" stop. A stop with no
    parseable detour figure sorts last, not first, so an unknown-length
    detour can't win a keep slot over a real, short, verified one."""
    g = _gen()
    stops = [
        {"name": "Far Overlook", "detour_distance_miles": 25.0},
        {"name": "Quick Pull-Off", "detour_distance_miles": 2.0},
        {"name": "Unknown Detour"},  # no detour_distance_miles at all
        {"name": "Medium Stop", "detour_distance_miles": 8.5},
        {"name": "Roadside Marker", "detour_distance_miles": 0.5},
        {"name": "Distant Trailhead", "detour_distance_miles": "not a number"},
    ]

    out = g._apply_manifest_enroute_target(stops, dates="October 19-21, 2026", en_route_stops_per_day=4)

    assert [s["name"] for s in out] == ["Roadside Marker", "Quick Pull-Off", "Medium Stop", "Far Overlook"]


def test_manifest_enroute_target_keeps_seeded_stops_regardless_of_detour() -> None:
    """A manifest en_route_seeds entry (the traveler's own explicit pick)
    must survive the cap even with a long detour that would otherwise lose
    out to shorter ones."""
    g = _gen()
    stops = [
        {"name": "Stop 0", "detour_distance_miles": 1.0},
        {"name": "Stop 1", "detour_distance_miles": 2.0},
        {"name": "Stop 2", "detour_distance_miles": 3.0},
        {"name": "Stop 3", "detour_distance_miles": 4.0},
        {"name": "Stop 4", "detour_distance_miles": 5.0},
        {"name": "Traveler's En-Route Pick", "detour_distance_miles": 40.0},
    ]

    out = g._apply_manifest_enroute_target(
        stops,
        dates="October 17, 2026",
        en_route_stops_per_day=4,
        protected_names=["Traveler's En-Route Pick"],
    )

    names = [s["name"] for s in out]
    assert "Traveler's En-Route Pick" in names
    assert len(out) == 4


def test_manifest_enroute_target_is_flat_not_scaled_by_day_count() -> None:
    """Unlike attractions/restaurants/scenic drives, the en-route-stop cap
    does NOT scale with the arriving destination's length of stay -- the
    single arrival leg happens once regardless of how many days the
    traveler then stays (see _resolve_enroute_target's docstring)."""
    g = _gen()
    stops = [{"name": f"Stop {i}", "detour_distance_miles": float(i)} for i in range(10)]

    one_day = g._apply_manifest_enroute_target(stops, dates="October 17, 2026", en_route_stops_per_day=4)
    three_day = g._apply_manifest_enroute_target(stops, dates="October 19-21, 2026", en_route_stops_per_day=4)

    assert len(one_day) == 4
    assert len(three_day) == 4


def test_resolve_scenic_drive_target_default_is_one_per_day() -> None:
    """The lowest cap of the four types, not a typo. Originally 2/day per
    the explicit 'cap scenic drives at 2/day' ask, lowered to 1/day on
    2026-08-21 to favour trails.

    Scenic drives are capped hardest because they are the most expensive
    type per published item: no direct-batch harvest fallback exists, so
    each drive costs its own individual paid web_search, while attractions
    and trails arrive ~5 per batch call."""
    g = _gen()
    assert g._resolve_scenic_drive_target({}, {}) == 1


def test_resolve_scenic_drive_target_destination_override_wins_over_trip() -> None:
    g = _gen()
    assert g._resolve_scenic_drive_target({"scenic_drives_per_day": 1}, {"scenic_drives_per_day": 6}) == 1


def test_manifest_scenic_drive_target_preserves_relative_order_when_trimming() -> None:
    """Scenic drives have no rating/votes/must_see signal to rank on, and
    the prompt's own convention makes list order meaningful (the
    well-known named drive is always first) -- trimming must be a stable
    truncation, not a re-sort."""
    g = _gen()
    drives = [{"title": f"Drive {i}"} for i in range(5)]

    out = g._apply_manifest_scenic_drive_target(drives, dates="October 17, 2026", scenic_drives_per_day=2)

    assert [d["title"] for d in out] == ["Drive 0", "Drive 1"]


def test_manifest_scenic_drive_target_scales_with_day_count() -> None:
    g = _gen()
    drives = [{"title": f"Drive {i}"} for i in range(5)]

    one_day = g._apply_manifest_scenic_drive_target(drives, dates="October 17, 2026", scenic_drives_per_day=2)
    three_day = g._apply_manifest_scenic_drive_target(drives, dates="October 19-21, 2026", scenic_drives_per_day=2)

    assert len(one_day) == 2
    assert len(three_day) == 5  # capped at min(len(drives), 2*3=6) -> all 5 survive


def test_manifest_scenic_drive_target_no_trim_when_under_cap() -> None:
    g = _gen()
    drives = [{"title": "Only Drive"}]

    out = g._apply_manifest_scenic_drive_target(drives, dates="October 17, 2026", scenic_drives_per_day=2)

    assert out == drives


def test_normalize_getting_here_applies_enroute_cap_and_preserves_seed() -> None:
    """Integration-level check that _normalize_getting_here (called from
    _normalize_destination_content at the correct pre-schedule point, unlike
    the restaurant path before this fix) wires dest/trip_meta through to the
    new en-route cap."""
    g = _gen()
    getting_here = {
        "travel_time": "45 min",
        "en_route_stops": [{"name": f"Stop {i}"} for i in range(6)] + [{"name": "Seeded Overlook"}],
    }
    dest = {"name": "Moab", "en_route_seeds": ["Seeded Overlook"]}

    out = g._normalize_getting_here(getting_here, "Moab", dates="October 17, 2026", trip_meta={}, dest=dest)

    names = [s["name"] for s in out["en_route_stops"]]
    assert "Seeded Overlook" in names
    assert len(names) == 4


def test_normalize_getting_here_applies_en_route_exclude() -> None:
    """Real bug: 'Confluence Park' geocodes to a real, live-verified match
    that's a different, wrong same-named place in St. George -- a genuine
    Nominatim same-name collision no automated heuristic could reliably
    catch without also rejecting legitimate stops (see
    docs/design/url-discovery-and-audit.md). en_route_exclude gives the
    traveler a durable, manifest-level way to blocklist a specific
    known-bad name -- applied before the cap/prioritization and before any
    search call, so an excluded name is dropped even if there's room left
    under the cap, not just deprioritized."""
    g = _gen()
    getting_here = {
        "travel_time": "1 hr 30 min",
        "en_route_stops": [
            {"name": "Confluence Park", "detour_distance_miles": 1.0},
            {"name": "La Verkin Overlook", "detour_distance_miles": 3.9},
            {"name": "Toquerville Falls", "detour_distance_miles": 10.6},
        ],
    }
    dest = {"name": "Zion National Park", "en_route_exclude": ["Confluence Park"]}

    out = g._normalize_getting_here(getting_here, "Zion National Park", dates="October 18, 2026", trip_meta={}, dest=dest)

    names = [s["name"] for s in out["en_route_stops"]]
    assert "Confluence Park" not in names
    assert "La Verkin Overlook" in names
    assert "Toquerville Falls" in names


def test_normalize_getting_here_en_route_exclude_matches_case_and_punctuation_insensitively() -> None:
    g = _gen()
    getting_here = {
        "travel_time": "1 hr",
        "en_route_stops": [
            {"name": "confluence park", "detour_distance_miles": 1.0},
            {"name": "Real Stop", "detour_distance_miles": 2.0},
        ],
    }
    dest = {"name": "Zion National Park", "en_route_exclude": ["Confluence Park!"]}

    out = g._normalize_getting_here(getting_here, "Zion National Park", dates="October 18, 2026", trip_meta={}, dest=dest)

    names = [s["name"] for s in out["en_route_stops"]]
    assert "confluence park" not in [n.lower() for n in names]
    assert "Real Stop" in names


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
        getting_here={"travel_time": "1 hr 30 min"},
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
        getting_here={"travel_time": "2 hrs"},
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
        {"travel_time": "1 hr 45 min"},
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
        {"travel_time": "2 hr 30 min"},
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
        {"travel_time": "2 hr"},
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
        getting_here={"travel_time": "2 hrs"},
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
        getting_here={"travel_time": "1 hr"},
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
        getting_here={"travel_time": "1 hr 20 min"},
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
        {"travel_time": "1 hr 45 min"},
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
        {"travel_time": "2 hr 20 min"},
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
        {"travel_time": "1 hr 45 min"},
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
        {"travel_time": "1 hr 45 min"},
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


def test_inject_travel_realism_dipstick69_evening_attraction_not_repeated_in_later_day_afternoon_pack() -> None:
    """Regression grounded in real SW2026-dipstick69 output, Bryce Canyon
    National Park's 3-day schedule (project owner-flagged): Day 1 Evening
    read 'Watch the sunset from Natural Bridge, experiencing the changing
    colors of the canyon. Enjoy dinner at Bryce Canyon Pines Restaurant for
    a local meal.' -- then Day 2 Afternoon's capacity-aware packer named
    Natural Bridge again: 'Consider one or more of the following, within
    about 1h 30m: Natural Bridge (30m), Inspiration Point (30m), Bryce
    Point (30m). Keep transfer/parking buffers between stops.' A traveler
    who already saw Natural Bridge Day 1 evening has no reason to see it
    suggested again Day 2.

    The pre-existing `used_multi_activity_names` cross-day dedup set (see
    test_inject_travel_realism_moab_schedule_avoids_repeats_and_multi_park_
    blocks above) only ever gets populated when _build_multi_activity_
    afternoon_summary itself picks a name -- Day 1's raw AI-authored
    Evening prose names Natural Bridge through a completely different code
    path that never touches that set, so the Day 2+ packer had no way to
    know it was already used.
    """
    g = _gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Arrive at Bryce Canyon and check in to your lodging."},
                {"period": "Afternoon", "summary": "Settle in and explore the visitor center."},
                {
                    "period": "Evening",
                    "summary": (
                        "Watch the sunset from Natural Bridge, experiencing the changing "
                        "colors of the canyon. Enjoy dinner at Bryce Canyon Pines Restaurant "
                        "for a local meal."
                    ),
                },
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Free morning to explore."},
                {"period": "Afternoon", "summary": "Old afternoon text."},
                {"period": "Evening", "summary": "Dinner in town."},
            ],
        },
        {
            "day_label": "Day 3",
            "periods": [
                {"period": "Morning", "summary": "Free morning to explore."},
                {"period": "Afternoon", "summary": "Old afternoon text."},
                {"period": "Evening", "summary": "Dinner in town."},
            ],
        },
    ]
    # Real Bryce Canyon top_attractions shape from dipstick69's output.
    attractions = [
        {"name": "Sunrise Point", "duration": "30 min"},
        {"name": "Inspiration Point", "duration": "30 min"},
        {"name": "Natural Bridge", "duration": "30 min"},
        {"name": "Bryce Point", "duration": "30 min"},
    ]

    out = g._inject_travel_realism(
        days,
        getting_here={},  # isolate Day 2+ capacity-aware packing from the arrival-day drive discount
        previous_destination="Zion National Park",
        next_destination="Capitol Reef National Park",
        attractions=attractions,
        default_daily_activity_hours=5,
    )

    day1_evening = out[0]["periods"][2]["summary"].lower()
    day2_afternoon = out[1]["periods"][1]["summary"].lower()
    day3_afternoon = out[2]["periods"][1]["summary"].lower()

    # Sanity check: the fixture actually reproduces the scenario -- Day 1
    # evening still names Natural Bridge, and Day 2 actually got a
    # capacity-aware pack (not left untouched for some unrelated reason).
    assert "natural bridge" in day1_evening
    assert "consider one or more of the following" in day2_afternoon

    # The actual bug: Natural Bridge must not be packed into any later
    # day's Afternoon block once it's already been named on an earlier day.
    assert "natural bridge" not in day2_afternoon
    assert "natural bridge" not in day3_afternoon


def test_pick_unused_focus_name_skips_names_already_registered_as_used() -> None:
    """Real Moab regression this closes: a real published 3-day Moab
    schedule had Day 1 Afternoon name 'Moab Giants Dinosaur Park' and Day 3
    Morning independently read 'Start with Moab Giants Dinosaur Park, then
    pivot to a different nearby area before midday crowds.' -- the SAME
    attraction, because the Day 2+ 'Morning was cloned from Day 1
    arrival/check-in text' scrub in _inject_travel_realism (the source of
    that exact 'Start with {name}, then pivot...' template) picked its
    focus via bare day-index rotation (_day_focus_name), with no awareness
    of used_multi_activity_names -- the same cross-day dedup set
    _register_attraction_mentions and the Afternoon multi-activity packer
    both already maintained and consulted.

    AIContentGenerator._pick_unused_focus_name is the extracted, directly
    testable selection logic behind the scrub's fix
    (_day_focus_name_excluding_used, in _inject_travel_realism). With 2
    real Moab top_attractions and day_index=3 -- the exact index whose bare
    rotation formula ((day_index - 1) % len(names)) wraps back around to
    index 0, reproducing the real collision -- this must skip the
    already-used name and pick the other real attraction instead.
    """
    names = ["Moab Giants Dinosaur Park", "Windows Loop and Turret Arch Trail"]
    used = {"moab giants dinosaur park"}

    # Sanity check: bare rotation (the pre-fix behavior) really does
    # reproduce the collision for this day_index/name-count combination.
    assert names[(3 - 1 + 0) % len(names)] == "Moab Giants Dinosaur Park"

    picked = AIContentGenerator._pick_unused_focus_name(names, used, day_index=3, offset=0)
    assert picked == "Windows Loop and Turret Arch Trail"


def test_pick_unused_focus_name_falls_back_to_repeat_when_pool_exhausted() -> None:
    """Mirrors this repo's established round-robin philosophy elsewhere
    (see docs/design/schedule-normalization.md's 'Small-attraction-pool
    follow-up' -- reconcile_schedule_from_registry tolerates eventual reuse
    once every real candidate has had a turn, rather than falling back to
    fully generic filler). When every known attraction is already used,
    _pick_unused_focus_name must still return a real, named attraction
    (the plain rotation pick) rather than an empty string -- a repeated
    real name is a smaller defect than naming nothing at all."""
    names = ["Moab Giants Dinosaur Park", "Windows Loop and Turret Arch Trail"]
    used = {"moab giants dinosaur park", "windows loop and turret arch trail"}

    picked = AIContentGenerator._pick_unused_focus_name(names, used, day_index=3, offset=0)
    assert picked == "Moab Giants Dinosaur Park"  # the bare-rotation fallback, not ""


def test_normalize_schedule_dipstick68_leaked_instruction_never_reaches_rendered_evening_text() -> None:
    """Regression grounded in the real SW2026-dipstick68 output for Bryce
    Canyon National Park, Day 2 Evening, exactly as the project owner found
    it: 'Visit Bryce Point for sunset views, then enjoy dinner at Bryce
    Canyon Pines Restaurant. Choose a different sunset zone or dining pocket
    than earlier nights.' Their words: 'the first sentence duplicates the
    prior evening, the second is a silly thing to tell users.'

    _dedupe_schedule_day_content used to append that second sentence
    (period_variation_suffix['Evening']) directly onto the rendered summary
    as a stopgap flag for _inject_travel_realism's rotation pass to replace
    with real content -- but the flag text itself could survive verbatim
    whenever rotation didn't end up changing that period (here: only one
    real accepted attraction, Bryce Point, so nothing to rotate the sunset
    viewpoint to). This exercises the full _normalize_schedule pipeline
    (not just _dedupe_schedule_day_content in isolation) with that exact
    real single-attraction, single-restaurant shape -- a genuine dead end
    for the underlying duplicate -- and asserts the leaked instruction can
    never appear in the rendered output regardless."""
    g = _gen()
    schedule = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Hike the Rim Trail in the cool morning air."},
                {"period": "Afternoon", "summary": "Explore Inspiration Point overlooks."},
                {
                    "period": "Evening",
                    "summary": "Visit Bryce Point for sunset views, then enjoy dinner at Bryce Canyon Pines Restaurant.",
                },
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Return to the Rim Trail for a different stretch."},
                {"period": "Afternoon", "summary": "Revisit Inspiration Point at a different time of day."},
                {
                    "period": "Evening",
                    "summary": "Visit Bryce Point for sunset views, then enjoy dinner at Bryce Canyon Pines Restaurant.",
                },
            ],
        },
    ]

    out = g._normalize_schedule(
        schedule=schedule,
        restaurants=[{"name": "Bryce Canyon Pines Restaurant"}],
        dates="October 9-11, 2026",
        attractions=[{"name": "Bryce Point"}],
        getting_here={"travel_time": "1 hr 45 min"},
        previous_destination="Zion National Park",
        next_destination="Capitol Reef National Park",
    )

    full_text = " ".join(
        str(period.get("summary", "") or "")
        for day in out
        for period in day.get("periods", []) or []
    ).lower()
    # The leaked internal instruction must never appear anywhere in
    # rendered output, no matter which period it would have targeted.
    assert "choose a different sunset zone" not in full_text
    assert "dining pocket" not in full_text
    assert "prioritize a different trailhead" not in full_text
    assert "shift focus to a different area" not in full_text
    assert "vary stops and pacing" not in full_text
    # No private/internal marker keys leak into the returned period dicts.
    for day in out:
        for period in day.get("periods", []) or []:
            assert not any(str(key).startswith("_") for key in period)

    # With genuinely only one real attraction and one real restaurant for
    # this destination, the underlying Day 1/Day 2 Evening duplicate has no
    # data left to vary -- an honest, undecorated duplicate is acceptable
    # (no fragile forced rewrite), as long as it never carries the leaked
    # instruction sentence.
    day1_evening = out[0]["periods"][2]["summary"]
    day2_evening = out[1]["periods"][2]["summary"]
    assert day1_evening == "Visit Bryce Point for sunset views, then enjoy dinner at Bryce Canyon Pines Restaurant."
    assert day2_evening == day1_evening


def test_normalize_schedule_dipstick68_evening_duplicate_resolves_via_restaurant_rotation_when_possible() -> None:
    """Companion to the dead-end case above: when a second real restaurant
    candidate genuinely exists for the destination (unlike the true
    dipstick68 dead end), the existing restaurant-rotation mechanism
    (_rotate_restaurant_summary) already resolves the Day 2 Evening
    duplicate on its own -- and, now that the leaked-instruction suffix is
    gone entirely, nothing gets appended on top of that already-fixed text
    either."""
    g = _gen()
    schedule = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Hike the Rim Trail in the cool morning air."},
                {"period": "Afternoon", "summary": "Explore Inspiration Point overlooks."},
                {
                    "period": "Evening",
                    "summary": "Visit Bryce Point for sunset views, then enjoy dinner at Bryce Canyon Pines Restaurant.",
                },
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Return to the Rim Trail for a different stretch."},
                {"period": "Afternoon", "summary": "Revisit Inspiration Point at a different time of day."},
                {
                    "period": "Evening",
                    "summary": "Visit Bryce Point for sunset views, then enjoy dinner at Bryce Canyon Pines Restaurant.",
                },
            ],
        },
    ]

    out = g._normalize_schedule(
        schedule=schedule,
        restaurants=[
            {"name": "Bryce Canyon Pines Restaurant"},
            {"name": "Bryce Canyon Lodge Dining Room"},
        ],
        dates="October 9-11, 2026",
        attractions=[{"name": "Bryce Point"}],
        getting_here={"travel_time": "1 hr 45 min"},
        previous_destination="Zion National Park",
        next_destination="Capitol Reef National Park",
    )

    day1_evening = out[0]["periods"][2]["summary"].lower()
    day2_evening = out[1]["periods"][2]["summary"].lower()
    assert "choose a different sunset zone" not in day2_evening
    assert "dining pocket" not in day2_evening
    assert day2_evening != day1_evening
    assert "bryce canyon lodge dining room" in day2_evening
    assert "bryce point" in day2_evening


def test_normalize_schedule_restaurant_rotation_substitutes_dining_phrasing_not_appends() -> None:
    """Real bug: Bryce Canyon Day 2 Evening rendered "Consider dining at the
    Bryce Canyon Pines Restaurant, known for its homemade pies and hearty
    meals. Plan dinner at Ruby's Inn Cowboy Buffet & Grill." -- two
    contradictory restaurants in one evening. Root cause: the AI's own text
    used "dining", not the literal word "dinner" _rotate_restaurant_summary
    checked for, and used no "dine/eat at X" verb phrase either, so its
    existing-mention detection missed a real, complete dinner
    recommendation and fell through to blindly appending a second one. The
    day-assigned restaurant should replace the already-named one, not pile
    on top of it, regardless of the exact verb phrasing the AI chose."""
    g = _gen()
    schedule = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Explore the Rim Trail at sunrise."},
                {"period": "Afternoon", "summary": "Visit Inspiration Point."},
                {"period": "Evening", "summary": "Watch the sunset from Bryce Point, then head back to town."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Hike the Navajo Loop Trail."},
                {"period": "Afternoon", "summary": "Revisit Inspiration Point at a different time of day."},
                {
                    "period": "Evening",
                    "summary": (
                        "Consider dining at the Bryce Canyon Pines Restaurant, "
                        "known for its homemade pies and hearty meals."
                    ),
                },
            ],
        },
    ]

    out = g._normalize_schedule(
        schedule=schedule,
        restaurants=[
            {"name": "Bryce Canyon Pines Restaurant"},
            {"name": "Ruby's Inn Cowboy Buffet & Grill"},
        ],
        dates="October 9-11, 2026",
        attractions=[{"name": "Bryce Point"}],
        getting_here={"travel_time": "1 hr 45 min"},
        previous_destination="Zion National Park",
        next_destination="Capitol Reef National Park",
    )

    day2_evening = out[1]["periods"][2]["summary"]
    # Never both restaurants in the same evening.
    assert not ("bryce canyon pines restaurant" in day2_evening.lower() and "ruby's inn" in day2_evening.lower())
    # The day-2-assigned restaurant (round-robin index 1 of 2) should
    # replace the already-named one in place, not get appended as a second
    # sentence -- the rest of the original text (the "known for its
    # homemade pies..." clause) is preserved, just naming the correct
    # rotated restaurant.
    assert "ruby's inn cowboy buffet & grill" in day2_evening.lower()
    assert "bryce canyon pines restaurant" not in day2_evening.lower()
    assert "Plan dinner at" not in day2_evening


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
    # Real bug (published eval run): this exact drive rendered as a
    # departure route option labeled "(50 miles one-way)", reusing
    # en-route-stop detour-style framing for what is actually a full
    # alternate route for the whole leg, not a side detour off it. The
    # "one-way" qualifier (a scenic-drive-specific meaning: point-to-point,
    # not round-trip) must not survive into the route-option label.
    assert moved["distance_or_duration"] == "~50 mi total route"
    assert "one-way" not in moved["distance_or_duration"]
    assert "one way" not in moved["distance_or_duration"]


def test_reframe_route_option_distance_label_rewrites_one_way_miles() -> None:
    g = _gen()
    assert g._reframe_route_option_distance_label("50 miles one-way") == "~50 mi total route"
    assert g._reframe_route_option_distance_label("17.5 mi one way") == "~17.5 mi total route"
    assert g._reframe_route_option_distance_label("44 miles one-way") == "~44 mi total route"


def test_reframe_route_option_distance_label_handles_whole_number_with_decimal() -> None:
    g = _gen()
    # A ".0" formatted mileage collapses to a clean integer-looking label.
    assert g._reframe_route_option_distance_label("50.0 miles one-way") == "~50 mi total route"


def test_reframe_route_option_distance_label_falls_back_to_stripping_qualifier() -> None:
    """When the text doesn't cleanly parse as "<number> mi(les) ... one-way"
    (unexpected AI phrasing), fail safe: strip only the misleading
    qualifier itself rather than fabricating a rewritten label."""
    g = _gen()
    result = g._reframe_route_option_distance_label("scenic byway, one-way")
    assert "one-way" not in result
    assert "one way" not in result
    assert "scenic byway" in result


def test_reframe_route_option_distance_label_also_normalizes_non_one_way_mileage() -> None:
    """The helper doesn't gate on "one-way" being present -- it always
    normalizes a parseable "<number> mi(les)" into the same honest
    "~X mi total route" shape. This is harmless in practice: the only real
    call site (_filter_departure_aligned_drives) only ever invokes it on
    text already confirmed to contain "one-way" by its own is_one_way gate."""
    g = _gen()
    assert g._reframe_route_option_distance_label("32 miles round-trip") == "~32 mi total route"


def test_reframe_route_option_distance_label_empty_input() -> None:
    g = _gen()
    assert g._reframe_route_option_distance_label("") == ""
    assert g._reframe_route_option_distance_label(None) == ""


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


def test_normalize_destination_content_trims_restaurants_before_schedule_generation() -> None:
    """Ordering-fix regression test: restaurant per-day capping must happen
    BEFORE _normalize_schedule runs, exactly like attractions already do.

    Before this fix, _normalize_schedule (which uses whatever restaurant
    list it's handed to fill in an unnamed 'dinner' mention -- see its
    clean_text() closure) was called with the UN-trimmed dinner_recommendations
    list, and restaurants were only capped afterward. That meant the schedule
    could end up referencing a restaurant that the cap then dropped from the
    final dinner_recommendations output -- an itinerary naming a dinner spot
    that doesn't appear anywhere else in the rendered page.

    This spies on _normalize_schedule to capture exactly which restaurant
    list it was actually called with, and asserts it's identical (same
    names, same order, same length) to the final, capped
    dinner_recommendations output -- proving the two can never disagree.

    Grounded in the real sw_manifest.yaml St. George, Utah entry: a 1-day
    stopover ('October 17, 2026'), so the default 4/day cap should trim the
    8 candidate restaurants down to 4.
    """
    g = _gen()
    g._weather_cache = {}
    g._get_monthly_temperature_normals = lambda _lat, _lng, _month: None

    captured: dict[str, list[str]] = {}

    def _spy_normalize_schedule(schedule, restaurants, *args, **kwargs):
        captured["restaurant_names"] = [r.get("name") for r in restaurants]
        return []

    g._normalize_schedule = _spy_normalize_schedule

    restaurants = [
        {"name": f"Restaurant {i}", "cuisine": "American", "rating": 4.9 - i * 0.05, "votes": 50}
        for i in range(8)
    ]
    payload = {
        "expected_environment": {},
        "getting_here": {},
        "top_attractions": [],
        "possible_daily_schedule": {},
        "dinner_recommendations": restaurants,
    }
    dest = {"name": "St. George, Utah", "dates": "October 17, 2026"}

    out = g._normalize_destination_content(
        payload,
        dates=dest["dates"],
        dest=dest,
        trip_meta={},
        previous_destination="none",
        next_destination="Zion National Park",
    )

    final_names = [r.get("name") for r in out["dinner_recommendations"]]
    assert captured["restaurant_names"] == final_names
    assert len(final_names) == 4


def test_normalize_destination_content_scales_restaurants_with_bryce_canyon_three_day_stay() -> None:
    """Grounded in the real sw_manifest.yaml Bryce Canyon National Park entry
    (a 3-day stay, 'October 19-21, 2026'): 4/day * 3 days = 12 restaurants
    should survive out of a larger candidate pool."""
    g = _gen()
    g._weather_cache = {}
    g._get_monthly_temperature_normals = lambda _lat, _lng, _month: None

    payload = {
        "expected_environment": {},
        "getting_here": {},
        "top_attractions": [],
        "possible_daily_schedule": {},
        "dinner_recommendations": [
            {"name": f"Restaurant {i}", "cuisine": "American", "rating": 4.9 - i * 0.02, "votes": 50}
            for i in range(20)
        ],
    }
    dest = {"name": "Bryce Canyon National Park", "dates": "October 19-21, 2026"}

    out = g._normalize_destination_content(
        payload,
        dates=dest["dates"],
        dest=dest,
        trip_meta={},
        previous_destination="St. George, Utah",
        next_destination="Capitol Reef National Park",
    )

    assert len(out["dinner_recommendations"]) == 12


def test_normalize_getting_here_returns_normalized_dict() -> None:
    g = _gen()
    getting_here = {
        "travel_time": "1 hr 30 min",
        "en_route_stops": [
            {"name": "Viewpoint", "detour_distance_miles": "", "detour_time_minutes": None}
        ],
    }

    out = g._normalize_getting_here(getting_here, "Moab")

    assert isinstance(out, dict)
    assert out["en_route_stops"][0]["detour_distance_miles"] == 0
    assert out["en_route_stops"][0]["detour_time_minutes"] == 0
    assert "Arrival leg into Moab" in out.get("route_summary", "")


def test_normalize_getting_here_renames_model_drive_time_to_travel_time() -> None:
    """docs/design/multimodal-routing.md 4.1: the prompt asks for
    `travel_time`, but a model asked for one key sometimes emits the other.
    The normalizer is the single boundary that tolerates it, and nothing
    downstream ever sees `drive_time` again."""
    g = _gen()

    out = g._normalize_getting_here({"drive_time": "2 hrs 15 min"}, "Bryce Canyon")

    assert out["travel_time"] == "2 hrs 15 min"
    assert "drive_time" not in out


def test_normalize_getting_here_prefers_travel_time_over_drive_time() -> None:
    """A model emitting BOTH keys is not a coin flip: the canonical name
    wins, and the legacy one is dropped rather than left to be read by
    something that still remembers it."""
    g = _gen()

    out = g._normalize_getting_here(
        {"travel_time": "3 hrs", "drive_time": "2 hrs 15 min"}, "Bryce Canyon"
    )

    assert out["travel_time"] == "3 hrs"
    assert "drive_time" not in out


def test_normalize_getting_here_renames_drive_time_on_a_booked_rail_leg() -> None:
    """The not-self-driven branch returns early to drop en-route stops. The
    rename has to happen ahead of it, or a rail leg keeps the legacy key and
    renders no duration badge at all."""
    g = _gen()
    dest = {"transportation": [{"type": "train", "provider": "SNCF"}]}

    out = g._normalize_getting_here(
        {"drive_time": "2 hrs 16 min", "en_route_stops": [{"name": "Mechelen"}]},
        "Brussels",
        dest=dest,
    )

    assert out["travel_time"] == "2 hrs 16 min"
    assert "drive_time" not in out
    assert out["en_route_stops"] == []


def test_override_grouped_child_distance_from_geocode_fixes_impossible_ai_guess() -> None:
    """Real dipstick68 regression: Arches National Park (group_with: moab)
    rendered distance_miles=212 / travel_time="30 min" from the AI's own
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
                "ai_content": {"getting_here": {"distance_miles": 5, "travel_time": "10 min"}},
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
                        "travel_time": "30 min",
                        "route_summary": "Take US-191 N from Moab.",
                    }
                },
            },
        ]
    }

    g._override_grouped_child_distance_from_geocode(trip)

    arches_gh = trip["destinations"][1]["ai_content"]["getting_here"]
    assert arches_gh["distance_miles"] != 212
    assert arches_gh["travel_time"] != "30 min"
    assert arches_gh["distance_miles"] < 20
    assert "hr" not in arches_gh["travel_time"]  # under an hour, minutes-only string
    # route_summary (real prose, not a derived number) is left untouched.
    assert arches_gh["route_summary"] == "Take US-191 N from Moab."
    # The base entry itself is never a grouped child -- its own numbers
    # (however implausible) are out of scope for this override.
    moab_gh = trip["destinations"][0]["ai_content"]["getting_here"]
    assert moab_gh["distance_miles"] == 5
    assert moab_gh["travel_time"] == "10 min"


def test_estimate_haversine_route_moab_to_arches_is_not_timed_at_highway_speed() -> None:
    """Direct check of the pure Haversine helper against the exact real
    coordinates from the dipstick68 run, independent of the trip-level
    override wiring above.

    This leg was measured against a routing engine on 2026-08-24: **18.2 road
    miles, 34 minutes**. The previous assertion here was ``"14 min"``, which is
    -60% against that -- the flat 60 mph model applied to a winding park
    approach. The suite was holding the systematic lean in place, so the number
    is corrected rather than the model bent to fit it.

    24 min is still -31%, and honestly so: the remaining error is *distance*,
    not speed (13.8 estimated road miles against 18.2 actual, because park roads
    wind more than a 1.30 factor allows). Closing that needs real routing, which
    generator/road_estimate.py explicitly does not attempt."""
    from generator.ai_content import _estimate_haversine_route

    miles, time_str = _estimate_haversine_route(38.5738, -109.5462, 38.7265, -109.5630)

    assert miles is not None and time_str is not None
    assert 1 <= miles <= 20
    assert time_str == "24 min"


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


def test_normalize_what_to_know_suppresses_generic_boilerplate_for_grouped_day_trip_child() -> None:
    """Real GH #68 evidence (project owner: 'The "what to know" about Day
    Trips does not need the repetitive [generic boilerplate] if it is just
    duplicates earlier direction in Moab, just offer unique comments to
    that locality'): a real published run's What to Know cards for Moab,
    Arches National Park (`group_with: moab`), and Canyonlands National
    Park (`group_with: moab`) each independently generated the SAME six
    categories with substantively identical (not verbatim-identical, so
    the pre-existing exact-string _deduplicate_cross_destination_what_to_know
    never caught it) generic seasonal-desert-park advice -- e.g. Arches and
    Canyonlands both independently said 'Early morning and late afternoon
    provide the best light/lighting for photography' for best_times_of_day.

    For a grouped child, local_customs/best_times_of_day/
    safety_considerations/crowd_patterns/local_etiquette must be left empty
    (not filled with yet another boilerplate fallback sentence) so the
    renderer (html_assembler.py's _build_intro_note, which already skips
    empty fields) simply omits them -- summary and transportation_quirks
    are kept since real data showed those are the two categories that can
    carry genuinely site-specific content (e.g. a park-specific access/
    facilities note)."""
    g = _gen()
    payload = {
        "summary": "Arches National Park in late October features cool temperatures and fewer visitors.",
        "local_customs": "Respect wildlife and maintain a safe distance from animals encountered along trails.",
        "best_times_of_day": "Early morning and late afternoon provide the best light for photography.",
        "transportation_quirks": "Parking can fill up quickly at popular trailheads, so plan to arrive early.",
        "safety_considerations": "Stay hydrated and watch for sudden weather changes, especially in the fall.",
        "crowd_patterns": "Expect fewer crowds compared to summer, but popular areas can still be busy.",
        "local_etiquette": "Leave no trace; pack out all trash and stay on designated trails.",
    }
    dest = {
        "id": "arches",
        "name": "Arches National Park",
        "dates": "October 23, 2026",
        "group_with": "moab",
    }

    normalized = g._normalize_what_to_know(payload, dest)

    assert normalized["summary"] == payload["summary"]
    assert normalized["transportation_quirks"] == payload["transportation_quirks"]
    assert normalized["local_customs"] == ""
    assert normalized["best_times_of_day"] == ""
    assert normalized["safety_considerations"] == ""
    assert normalized["crowd_patterns"] == ""
    assert normalized["local_etiquette"] == ""


def test_normalize_what_to_know_keeps_full_boilerplate_for_ungrouped_base_destination() -> None:
    """Companion to the suppression test above: Moab itself (no
    `group_with`) is the group base, not a grouped child -- it must keep
    the full six-category card exactly as before this fix, since it has no
    other destination's card to defer to."""
    g = _gen()
    payload = {
        "summary": "Moab in October offers a pleasant climate for outdoor activities.",
    }
    dest = {"id": "moab", "name": "Moab", "dates": "October 22-24, 2026"}

    normalized = g._normalize_what_to_know(payload, dest)

    for key in ("local_customs", "best_times_of_day", "safety_considerations", "crowd_patterns", "local_etiquette"):
        assert normalized[key].strip() != ""


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


def test_parse_duration_minutes_handles_real_badge_formats() -> None:
    """Grounded against real `badge-duration` strings observed in a real
    published run's HTML (C:\\Users\\...\\eval\\index.html) rather than an
    assumed single format: plain ranges with a hyphen or an en-dash, both
    'hr(s)'/'hour(s)' and 'min(s)'/'minute(s)' unit spellings, a bare
    single value, and trailing free text like 'round-trip' after the unit."""
    assert AIContentGenerator._parse_duration_minutes("30 min") == 30
    assert AIContentGenerator._parse_duration_minutes("1 hr") == 60
    assert AIContentGenerator._parse_duration_minutes("1-2 hours") == 90
    assert AIContentGenerator._parse_duration_minutes("4\u20138 hrs round-trip") == 360
    assert AIContentGenerator._parse_duration_minutes("1.5\u20132 hrs round-trip") == 105
    assert AIContentGenerator._parse_duration_minutes("") == 0


def test_is_evening_unsuitable_duration_flags_multi_hour_hikes_only() -> None:
    """Real example that motivated this (project owner: 'Are these
    estimates being really factored in?'): a previous run's Evening period
    suggested 'The Narrows' -- a real Zion hike whose own duration badge,
    elsewhere on the same real published page, reads '4\u20138 hrs
    round-trip'. That is a genuine multi-hour undertaking, not something to
    start after dinner. A short, evening-compatible activity (e.g. a 30-45
    minute sunset viewpoint) must NOT be flagged just for having a
    duration field at all."""
    assert AIContentGenerator._is_evening_unsuitable_duration(
        {"name": "The Narrows", "duration": "4\u20138 hrs round-trip"}
    )
    assert not AIContentGenerator._is_evening_unsuitable_duration(
        {"name": "Sunset Point", "duration": "30 min"}
    )
    assert not AIContentGenerator._is_evening_unsuitable_duration(
        {"name": "Canyon Overlook Trail", "duration": "1-2 hours"}
    )
    # No duration data at all must never be treated as "too long" --
    # _parse_duration_minutes returns 0 for an empty/missing field, which
    # is not > the threshold.
    assert not AIContentGenerator._is_evening_unsuitable_duration({"name": "Unknown Spot"})


def test_inject_travel_realism_strips_multi_hour_hike_mention_from_evening_schedule() -> None:
    """Real motivating pattern (this specific line may not reproduce
    identically run to run, but the underlying issue is real -- see the
    project owner's question 'Are these estimates being really factored
    in?'): an Evening period naming 'The Narrows', a real Zion hike whose
    own duration is '4-8 hours round-trip' elsewhere in the same real
    output -- physically not something to start after dinner. Reuses the
    same evening-unsuitable-venue strip/fallback mechanism (previously
    duration-blind) rather than a separate new pass."""
    g = _gen()
    days = [{
        "day_label": "Day 1",
        "periods": [
            {"period": "Morning", "summary": "Start early at Angels Landing before the heat builds."},
            {"period": "Afternoon", "summary": "Cool off at the Zion Human History Museum exhibits."},
            {
                "period": "Evening",
                "summary": "Enjoy dinner at Zion Pizza. Head into The Narrows for an evening hike.",
            },
        ],
    }]
    attractions = [
        {"name": "Angels Landing", "type": "hike", "duration": "4-5 hours"},
        {"name": "The Narrows", "type": "hike", "duration": "4-8 hrs round-trip"},
    ]

    updated = g._inject_travel_realism(
        days,
        {},
        "Capitol Reef National Park",
        "Bryce Canyon National Park",
        attractions=attractions,
        restaurants=[{"name": "Zion Pizza"}],
    )

    evening_summary = updated[0]["periods"][2]["summary"]
    assert "The Narrows" not in evening_summary
    assert "Zion Pizza" in evening_summary
    # Morning is untouched -- a multi-hour hike is a perfectly realistic
    # Morning pick; only Evening candidacy is affected.
    assert "Angels Landing" in updated[0]["periods"][0]["summary"]


def test_inject_travel_realism_does_not_repeat_focus_two_days_later() -> None:
    """Real Old Hickory run (Dec 2026): the focus lookback was 2 periods --
    under a single day -- so an attraction named on Day 3 was already
    forgotten by Day 4 and got named again. With a pool this size the window
    now spans two days, while staying capped below the pool size so at least
    one candidate is always eligible (see _focus_lookback)."""
    g = _gen()
    days = [
        {
            "day_label": f"Day {n}",
            "periods": [
                {"period": "Morning", "summary": "Start with Andrew Jackson's Hermitage and keep parking buffers."},
                {"period": "Afternoon", "summary": "Spend the afternoon at Bledsoe Creek State Park."},
                {"period": "Evening", "summary": "Dinner near your base."},
            ],
        }
        for n in range(1, 5)
    ]

    out = g._inject_travel_realism(
        days,
        {"travel_time": "40 min"},
        "Nashville",
        "Asheville",
        attractions=[
            {"name": "Andrew Jackson's Hermitage"},
            {"name": "Bledsoe Creek State Park"},
            {"name": "Old Hickory Lake"},
            {"name": "Long Hunter State Park"},
            {"name": "Lock 3 Recreation Area"},
            {"name": "Cheekwood Estate"},
        ],
        restaurants=[{"name": "Flat Tire Diner"}, {"name": "Old Hickory Grill"}],
    )

    # With 6 attractions across 4 three-period days some reuse is
    # arithmetically forced, so this asserts what the widened window
    # actually buys: an attraction named on one day is not named again the
    # very next day. Under the old 2-period lookback it was.
    for earlier, later in zip(out, out[1:]):
        earlier_names = " ".join(x["summary"].lower() for x in earlier["periods"])
        later_morning = later["periods"][0]["summary"].lower()
        for name in ("andrew jackson's hermitage", "bledsoe creek state park"):
            if name in later_morning:
                assert name not in earlier_names, (
                    f"{name!r} headlines a morning the day after it already appeared"
                )


def test_pick_lunch_stop_fires_only_above_the_threshold() -> None:
    gh = {
        "travel_time": "4 hr 45 min",
        "en_route_stops": [{"name": "Knoxville", "route_progress_ratio": 0.62}],
    }
    assert AIContentGenerator._pick_lunch_stop(gh)["name"] == "Knoxville"
    assert AIContentGenerator._pick_lunch_stop({**gh, "travel_time": "1 hr 10 min"}) is None


def test_pick_lunch_stop_prefers_a_seeded_stop_over_a_closer_one() -> None:
    """A seed is an explicit human intent -- it outranks whatever discovery
    happened to place nearest the midpoint."""
    gh = {
        "travel_time": "5 hr",
        "en_route_stops": [
            {"name": "Discovered Midpoint", "route_progress_ratio": 0.50},
            {"name": "Oak Ridge", "route_progress_ratio": 0.58, "is_seed": True},
        ],
    }
    assert AIContentGenerator._pick_lunch_stop(gh)["name"] == "Oak Ridge"


def test_pick_lunch_stop_is_silent_without_verified_candidates() -> None:
    """Option A by design: it never names a place of its own, so with
    en-route discovery disabled there is nothing to recommend."""
    assert AIContentGenerator._pick_lunch_stop({"travel_time": "6 hr", "en_route_stops": []}) is None


def test_pick_lunch_stop_skips_stops_with_no_resolved_position() -> None:
    """Same reasoning as _route_waypoint_sort_key: an unresolved ratio must
    not be treated as position zero."""
    gh = {
        "travel_time": "5 hr",
        "en_route_stops": [
            {"name": "Unplaced"},
            {"name": "Placed", "route_progress_ratio": 0.55},
        ],
    }
    assert AIContentGenerator._pick_lunch_stop(gh)["name"] == "Placed"


def test_departure_leg_gets_distance_and_time_badges() -> None:
    """html_assembler gates the Departure Route Options mileage/duration
    badges on getting_there.distance_miles + travel_time, and nothing ever set
    them, so the card rendered a bare label."""
    g = _gen()
    trip = {
        "trip": {
            "return": "Charlotte Douglas International Airport",
            "return_lat": 35.2144, "return_lng": -80.9473,
        },
        "destinations": [
            {"id": "asheville", "name": "Asheville, North Carolina",
             "lat": 35.5951, "lng": -82.5515, "ai_content": {"getting_there": {}}},
        ],
    }

    g._populate_departure_leg_distance(trip)

    gt = trip["destinations"][0]["ai_content"]["getting_there"]
    assert gt["distance_miles"]
    assert gt["travel_time"]


def test_departure_leg_measures_from_the_base_when_the_last_stop_is_a_day_trip() -> None:
    """The traveler departs from where they slept, not from the day trip that
    happens to be last in the list."""
    g = _gen()
    trip = {
        "trip": {"return": "Nashville International Airport",
                 "return_lat": 36.1263, "return_lng": -86.6774},
        "destinations": [
            {"id": "oldhickory", "name": "Old Hickory", "lat": 36.2506, "lng": -86.6144},
            {"id": "franklin", "name": "Franklin", "lat": 35.9251, "lng": -86.8689,
             "group_with": "oldhickory", "ai_content": {"getting_there": {}}},
        ],
    }

    g._populate_departure_leg_distance(trip)

    from_base = trip["destinations"][1]["ai_content"]["getting_there"]["distance_miles"]

    trip2 = {
        "trip": {"return": "Nashville International Airport",
                 "return_lat": 36.1263, "return_lng": -86.6774},
        "destinations": [
            {"id": "oldhickory", "name": "Old Hickory", "lat": 36.2506, "lng": -86.6144},
            {"id": "franklin", "name": "Franklin", "lat": 35.9251, "lng": -86.8689,
             "ai_content": {"getting_there": {}}},
        ],
    }
    g._populate_departure_leg_distance(trip2)
    from_day_trip = trip2["destinations"][1]["ai_content"]["getting_there"]["distance_miles"]

    assert from_base != from_day_trip


def test_departure_leg_leaves_an_existing_value_alone() -> None:
    g = _gen()
    trip = {
        "trip": {"return": "Charlotte", "return_lat": 35.2144, "return_lng": -80.9473},
        "destinations": [
            {"id": "asheville", "name": "Asheville", "lat": 35.5951, "lng": -82.5515,
             "ai_content": {"getting_there": {"distance_miles": 130, "travel_time": "2 hr 5 min"}}},
        ],
    }

    g._populate_departure_leg_distance(trip)

    gt = trip["destinations"][0]["ai_content"]["getting_there"]
    assert gt["distance_miles"] == 130
    assert gt["travel_time"] == "2 hr 5 min"
