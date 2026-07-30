"""Tests for AIContentGenerator.normalize_trip_content (cross-section and cross-destination dedup)."""
from unittest.mock import MagicMock, patch
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
