"""Tests for AIContentGenerator.normalize_trip_content (cross-section and cross-destination dedup)."""
from unittest.mock import MagicMock
from generator.ai_content import AIContentGenerator


def _make_gen() -> AIContentGenerator:
    gen = AIContentGenerator.__new__(AIContentGenerator)
    gen._config = {}
    gen._llm = MagicMock()
    return gen


def test_cross_section_dedup_preserves_local_tip_and_strips_what_to_know_echo():
    """PR-015 policy: keep cultural_events.local_tip and strip duplicate prose from what_to_know."""
    gen = _make_gen()
    tip = "Check the Sheridan Opera House schedule for any live music events during your stay."
    trip = {
        "destinations": [
            {
                "name": "Telluride",
                "what_to_know": {
                    "summary": "Great mountain town.",
                    "local_customs": f"Local advice: {tip}",
                    "best_times_of_day": "Early morning.",
                    "transportation_quirks": "",
                    "safety_considerations": "",
                    "crowd_patterns": "",
                    "local_etiquette": "",
                },
                "cultural_events": {
                    "has_events": True,
                    "events": [],
                    "local_tip": tip,
                },
            }
        ]
    }
    gen._deduplicate_cross_section_tips(trip)
    assert trip["destinations"][0]["cultural_events"]["local_tip"] == tip
    assert tip not in trip["destinations"][0]["what_to_know"]["local_customs"]


def test_cross_section_dedup_keeps_tip_not_in_what_to_know():
    """Local tip that is NOT in what_to_know is preserved."""
    gen = _make_gen()
    tip = "Visit the local farmers market on Saturday mornings."
    trip = {
        "destinations": [
            {
                "name": "Telluride",
                "what_to_know": {
                    "summary": "Mountain destination.",
                    "local_customs": "Respect quiet hours.",
                    "best_times_of_day": "",
                    "transportation_quirks": "",
                    "safety_considerations": "",
                    "crowd_patterns": "",
                    "local_etiquette": "",
                },
                "cultural_events": {
                    "has_events": True,
                    "events": [],
                    "local_tip": tip,
                },
            }
        ]
    }
    gen._deduplicate_cross_section_tips(trip)
    assert trip["destinations"][0]["cultural_events"]["local_tip"] == tip


def test_cross_section_dedup_strips_honest_assessment_echo_from_local_etiquette():
    """PR-001/PR-015 regression: remove duplicated Cultural Events prose from What to Know fields."""
    gen = _make_gen()
    duplicate = (
        "St. George has a lively cultural scene in October, characterized by community gatherings "
        "and outdoor activities. Visitors can explore local art galleries and enjoy the warm weather "
        "while attending various informal events. The area is also known for its scenic beauty, "
        "making it a pleasant place for outdoor enthusiasts."
    )
    trip = {
        "destinations": [
            {
                "name": "St. George",
                "what_to_know": {
                    "summary": "Desert gateway with easy access to parks.",
                    "local_customs": "Friendly and relaxed pace.",
                    "best_times_of_day": "Morning and dusk.",
                    "transportation_quirks": "Parking is easier outside midday.",
                    "safety_considerations": "Carry water.",
                    "crowd_patterns": "Busy on weekends.",
                    "local_etiquette": f"Respect trail signage. {duplicate}",
                },
                "cultural_events": {
                    "has_events": False,
                    "honest_assessment": duplicate,
                    "events": [],
                },
            }
        ]
    }

    gen._deduplicate_cross_section_tips(trip)

    local_etiquette = trip["destinations"][0]["what_to_know"]["local_etiquette"]
    assert "Respect trail signage." in local_etiquette
    assert duplicate not in local_etiquette
    assert trip["destinations"][0]["cultural_events"]["honest_assessment"] == duplicate


def test_cross_destination_what_to_know_dedup_resets_repeated_field():
    """PR-001: Identical what_to_know field value in 2+ destinations is replaced with fallback."""
    gen = _make_gen()
    repeated = "Stay hydrated and carry sufficient water at all times during your hike."
    fallback = "Carry water, layers, and navigation backup; check alerts and avoid pushing exposure during heat or storms."
    trip = {
        "destinations": [
            {
                "name": "Zion",
                "what_to_know": {"safety_considerations": repeated, "summary": ""},
            },
            {
                "name": "Bryce Canyon",
                "what_to_know": {"safety_considerations": repeated, "summary": ""},
            },
            {
                "name": "Capitol Reef",
                "what_to_know": {"safety_considerations": "Watch for flash floods in canyon narrows.", "summary": ""},
            },
        ]
    }
    gen._deduplicate_cross_destination_what_to_know(trip)
    assert trip["destinations"][0]["what_to_know"]["safety_considerations"] == fallback
    assert trip["destinations"][1]["what_to_know"]["safety_considerations"] == fallback
    # Distinct value is left alone
    assert "flash floods" in trip["destinations"][2]["what_to_know"]["safety_considerations"]


def test_cross_destination_dedup_leaves_unique_values_unchanged():
    """Unique what_to_know values are preserved even when dedup runs."""
    gen = _make_gen()
    trip = {
        "destinations": [
            {"name": "Moab", "what_to_know": {"safety_considerations": "Watch for flash floods.", "summary": ""}},
            {"name": "Telluride", "what_to_know": {"safety_considerations": "Altitude acclimatization required.", "summary": ""}},
        ]
    }
    gen._deduplicate_cross_destination_what_to_know(trip)
    assert "flash floods" in trip["destinations"][0]["what_to_know"]["safety_considerations"]
    assert "altitude" in trip["destinations"][1]["what_to_know"]["safety_considerations"].lower()


def test_inject_travel_realism_uses_default_day_start_time_for_arrival_leg():
    gen = _make_gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Explore downtown."},
                {"period": "Afternoon", "summary": "Museum visit."},
                {"period": "Evening", "summary": "Dinner."},
            ],
        }
    ]

    out = gen._inject_travel_realism(
        days=days,
        getting_here={"drive_time": "2 hours"},
        previous_destination="Las Vegas Airport",
        next_destination="Zion National Park",
        default_day_start_time="10:00 AM",
    )

    morning = out[0]["periods"][0]["summary"]
    assert "Travel from Las Vegas Airport" in morning
    assert "depart around 10:00 AM" in morning
    assert "arrival around 12:00 PM" in morning


def test_inject_travel_realism_honors_destination_start_time_override():
    gen = _make_gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Explore downtown."},
                {"period": "Afternoon", "summary": "Museum visit."},
                {"period": "Evening", "summary": "Dinner."},
            ],
        }
    ]

    out = gen._inject_travel_realism(
        days=days,
        getting_here={"drive_time": "2 hours"},
        previous_destination="Las Vegas Airport",
        next_destination="Zion National Park",
        default_day_start_time="10:00 AM",
        destination_day_start_time="8:00 AM",
    )

    morning = out[0]["periods"][0]["summary"]
    assert "depart around 8:00 AM" in morning
    assert "arrival around 10:00 AM" in morning


def test_inject_travel_realism_packs_multiple_afternoon_activities_with_default_budget():
    gen = _make_gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Transit."},
                {"period": "Afternoon", "summary": "Old afternoon text."},
                {"period": "Evening", "summary": "Dinner."},
            ],
        }
    ]
    attractions = [
        {"name": "The Narrows", "duration": "2 hours"},
        {"name": "Emerald Pools Trail", "duration": "1.5 hours"},
        {"name": "Canyon Overlook Trail", "duration": "1 hour"},
    ]

    out = gen._inject_travel_realism(
        days=days,
        getting_here={"drive_time": "2 hours"},
        previous_destination="Las Vegas Airport",
        next_destination="Zion National Park",
        default_day_start_time="10:00 AM",
        attractions=attractions,
        default_daily_activity_hours=5,
    )

    afternoon = out[0]["periods"][1]["summary"]
    # The activity budget represents willingness/time for activities in a
    # normal full day -- on an arrival day the 2-hour drive itself eats
    # directly into that 5-hour allotment, leaving 3 hours: The Narrows (2h)
    # + Canyon Overlook Trail (1h) fit exactly; Emerald Pools Trail (1.5h)
    # does not.
    assert "consider one or more of the following" in afternoon
    assert "The Narrows" in afternoon
    assert "Canyon Overlook Trail" in afternoon
    assert "Emerald Pools Trail" not in afternoon


def test_inject_travel_realism_respects_destination_activity_hour_override():
    gen = _make_gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Transit."},
                {"period": "Afternoon", "summary": "Old afternoon text."},
                {"period": "Evening", "summary": "Dinner."},
            ],
        }
    ]
    attractions = [
        {"name": "The Narrows", "duration": "2 hours"},
        {"name": "Emerald Pools Trail", "duration": "1.5 hours"},
        {"name": "Canyon Overlook Trail", "duration": "1 hour"},
    ]

    out = gen._inject_travel_realism(
        days=days,
        getting_here={"drive_time": "2 hours"},
        previous_destination="Las Vegas Airport",
        next_destination="Zion National Park",
        default_day_start_time="10:00 AM",
        attractions=attractions,
        default_daily_activity_hours=5,
        destination_daily_activity_hours=2,
    )

    # A 2-hour destination override minus the 2-hour drive leaves no activity
    # budget at all -- packing must decline rather than force a plan into
    # zero remaining time, proving the override value is actually consulted
    # (a real day-vs-drive-time interaction, not just a smaller version of
    # the default-budget test above).
    afternoon = out[0]["periods"][1]["summary"]
    assert afternoon == "Old afternoon text."


def test_inject_travel_realism_extends_packing_to_day2_plus_with_rotated_attractions():
    """Regression: capacity-aware Afternoon packing previously only ever
    applied to Day 1's Afternoon of a non-first destination arriving via a
    recorded drive -- an in-stay Day 2+ just kept generic/rotated-name text
    even though it has the full activity budget available with zero transit
    friction. Day index must also rotate which attractions are considered
    first, so consecutive days don't greedily pick the identical set."""
    gen = _make_gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Transit."},
                {"period": "Afternoon", "summary": "Old afternoon text."},
                {"period": "Evening", "summary": "Dinner."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Free morning."},
                {"period": "Afternoon", "summary": "Old afternoon text."},
                {"period": "Evening", "summary": "Dinner."},
            ],
        },
        {
            "day_label": "Day 3",
            "periods": [
                {"period": "Morning", "summary": "Free morning."},
                {"period": "Afternoon", "summary": "Old afternoon text."},
                {"period": "Evening", "summary": "Dinner."},
            ],
        },
    ]
    attractions = [
        {"name": "The Narrows", "duration": "1 hour"},
        {"name": "Emerald Pools Trail", "duration": "1 hour"},
        {"name": "Canyon Overlook Trail", "duration": "1 hour"},
        {"name": "Weeping Rock", "duration": "1 hour"},
    ]

    out = gen._inject_travel_realism(
        days=days,
        getting_here={},
        previous_destination="none",
        next_destination="Bryce Canyon National Park",
        default_day_start_time="10:00 AM",
        attractions=attractions,
        default_daily_activity_hours=5,
    )

    day2_afternoon = out[1]["periods"][1]["summary"]
    day3_afternoon = out[2]["periods"][1]["summary"]
    assert "consider one or more of the following" in day2_afternoon.lower()
    assert "consider one or more of the following" in day3_afternoon.lower()
    # Rotated starting point means Day 2 and Day 3 don't pack the identical set.
    assert day2_afternoon != day3_afternoon


def test_inject_travel_realism_day2_plus_packing_declines_with_insufficient_attractions():
    gen = _make_gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Transit."},
                {"period": "Afternoon", "summary": "Old afternoon text."},
                {"period": "Evening", "summary": "Dinner."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Free morning."},
                {"period": "Afternoon", "summary": "Keep this specific text."},
                {"period": "Evening", "summary": "Dinner."},
            ],
        },
    ]

    out = gen._inject_travel_realism(
        days=days,
        getting_here={},
        previous_destination="none",
        next_destination="Bryce Canyon National Park",
        default_day_start_time="10:00 AM",
        attractions=[{"name": "Only One Attraction", "duration": "1 hour"}],
        default_daily_activity_hours=5,
    )

    assert out[1]["periods"][1]["summary"] == "Keep this specific text."


def test_normalize_schedule_multi_day_strips_arrival_checkin_from_day_two_plus():
    gen = _make_gen()

    schedule = {
        "morning": "Arrive at Bryce Canyon National Park and check in to your lodging at 4:00 PM.",
        "afternoon": "After arrival, explore Navajo Loop Trail and Bryce Point.",
        "evening": "Watch sunset from Bryce Point, then dinner at Bryce Canyon Lodge.",
    }

    out = gen._normalize_schedule(
        schedule=schedule,
        restaurants=[{"name": "Bryce Canyon Lodge"}],
        dates="June 1-3, 2026",
        attractions=[],
        getting_here={"drive_time": "2 hours"},
        previous_destination="Zion National Park",
        next_destination="Capitol Reef National Park",
    )

    assert len(out) == 3

    day2_morning = out[1]["periods"][0]["summary"].lower()
    day3_morning = out[2]["periods"][0]["summary"].lower()
    assert "check in" not in day2_morning
    assert "check-in" not in day2_morning
    assert "arrive at" not in day2_morning
    assert "check in" not in day3_morning
    assert "check-in" not in day3_morning
    assert "arrive at" not in day3_morning


def test_inject_travel_realism_does_not_duplicate_existing_dinner_phrase():
    gen = _make_gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Travel day."},
                {"period": "Afternoon", "summary": "Explore downtown."},
                {"period": "Evening", "summary": "Dine at Painted Pony Restaurant for a taste of local cuisine."},
            ],
        }
    ]

    out = gen._inject_travel_realism(
        days,
        {"drive_time": "2 hours"},
        "Las Vegas Airport",
        "St. George",
        restaurants=[{"name": "Painted Pony Restaurant"}],
    )

    evening = out[0]["periods"][2]["summary"]
    assert evening == "Dine at Painted Pony Restaurant for a taste of local cuisine."
    assert "Plan dinner at Painted Pony Restaurant" not in evening


def test_inject_travel_realism_does_not_duplicate_restaurant_name_around_dinner_word():
    """dipstick59: 'Head to Red Fort Cuisine for dinner and enjoy...' already
    names the restaurant before the word 'dinner' -- the rotation logic's
    'dinner' branch blindly appended 'at {restaurant_name}' regardless,
    producing 'Head to Red Fort Cuisine for dinner at Red Fort Cuisine'."""
    gen = _make_gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Explore downtown."},
                {"period": "Afternoon", "summary": "Visit a local park."},
                {"period": "Evening", "summary": "Head to Red Fort Cuisine for dinner and enjoy flavorful Indian dishes."},
            ],
        }
    ]

    out = gen._inject_travel_realism(
        days,
        {"drive_time": "2 hours"},
        "Las Vegas Airport",
        "St. George",
        restaurants=[{"name": "Red Fort Cuisine"}],
    )

    evening = out[0]["periods"][2]["summary"]
    assert evening == "Head to Red Fort Cuisine for dinner and enjoy flavorful Indian dishes."
    assert evening.count("Red Fort Cuisine") == 1


def test_inject_travel_realism_does_not_append_block_filler_phrases() -> None:
    gen = _make_gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Start early and avoid crowds."},
                {"period": "Afternoon", "summary": "Explore nearby highlights."},
                {"period": "Evening", "summary": "Dinner."},
            ],
        }
    ]

    out = gen._inject_travel_realism(
        days=days,
        getting_here={"drive_time": "2 hours"},
        previous_destination="Las Vegas Airport",
        next_destination="Zion National Park",
        attractions=[{"name": "The Narrows", "duration": "2 hours"}],
    )

    morning = out[0]["periods"][0]["summary"]
    afternoon = out[0]["periods"][1]["summary"]
    assert "in this block" not in morning.lower()
    assert "in this block" not in afternoon.lower()
    assert "center this block around" not in afternoon.lower()
