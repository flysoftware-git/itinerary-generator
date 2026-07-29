"""Tests for AIContentGenerator.normalize_trip_content (cross-section and cross-destination dedup)."""
from unittest.mock import MagicMock, patch
from generator.ai_content import AIContentGenerator


def _make_gen() -> AIContentGenerator:
    gen = AIContentGenerator.__new__(AIContentGenerator)
    gen._config = {}
    gen._llm = MagicMock()
    return gen


def test_cross_section_dedup_removes_local_tip_present_in_what_to_know():
    """PR-015: local_tip that duplicates what_to_know text is removed from cultural_events."""
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
    assert "local_tip" not in trip["destinations"][0]["cultural_events"]


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
