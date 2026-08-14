import pytest

from generator.ai_content import AIContentGenerator
from generator.url_discovery import URLDiscoverer


def _gen() -> AIContentGenerator:
    return AIContentGenerator.__new__(AIContentGenerator)


def test_policy_multi_day_day2_plus_no_checkin_or_arrival_language() -> None:
    gen = _gen()
    schedule = {
        "morning": "Arrive at Bryce Canyon and check in to your lodging.",
        "afternoon": "After arrival, explore Navajo Loop Trail.",
        "evening": "Dinner in town.",
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

    for day in out[1:]:
        text = " ".join(str(p.get("summary", "") or "") for p in day.get("periods", [])).lower()
        assert "check in" not in text
        assert "check-in" not in text
        assert "arrive" not in text


def test_policy_single_day_transfer_includes_one_arrival_logistics_block() -> None:
    gen = _gen()
    schedule = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Local coffee and short walk."},
                {"period": "Afternoon", "summary": "Visit a local museum."},
                {"period": "Evening", "summary": "Dinner downtown."},
            ],
        }
    ]

    out = gen._normalize_schedule(
        schedule=schedule,
        restaurants=[{"name": "Local Bistro"}],
        dates="October 17, 2026",
        attractions=[],
        getting_here={"drive_time": "3 hours"},
        previous_destination="Telluride",
        next_destination="",
    )

    day_text = " ".join(str(p.get("summary", "") or "") for p in out[0].get("periods", [])).lower()
    assert "after arriving" in day_text or "travel from" in day_text


def test_policy_departure_day_reserves_return_blocks() -> None:
    gen = _gen()
    schedule = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Museum visit."},
                {"period": "Afternoon", "summary": "Gallery walk."},
                {"period": "Evening", "summary": "Final dinner."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Short hike."},
                {"period": "Afternoon", "summary": "Park stop."},
                {"period": "Evening", "summary": "Sunset."},
            ],
        },
    ]

    out = gen._normalize_schedule(
        schedule=schedule,
        restaurants=[{"name": "Local Bistro"}],
        dates="October 18-19, 2026",
        attractions=[],
        getting_here={"drive_time": "1 hour"},
        previous_destination="Pagosa Springs",
        next_destination="",
        trip_return="Las Vegas",
    )

    last = out[-1]
    afternoon = str(last["periods"][1]["summary"] or "").lower()
    evening = str(last["periods"][2]["summary"] or "").lower()
    assert "reserved for return travel" in afternoon
    assert "reserved for return travel" in evening


def test_policy_schedule_should_not_reference_filtered_entities() -> None:
    """Schedule reconciliation now runs against the final entity registry
    state (generator.entity_registry.reconcile_schedule_from_registry)
    rather than URLDiscoverer._reconcile_schedule_after_entity_filter, which
    was superseded because it only ever ran at audit time (before final
    reconciliation) and only ever covered top_attractions."""
    from generator.entity_registry import build_entity_registry, reconcile_schedule_from_registry, reconcile_trip_from_registry

    trip = {
        "destinations": [
            {
                "id": "zion",
                "name": "Zion National Park",
                "ai_content": {
                    "top_attractions": [
                        {"name": "Riverside Walk", "duration": "1.5 hours"},
                        {"name": "Canyon Overlook Trail", "duration": "1 hour"},
                    ],
                    "possible_daily_schedule": [
                        {
                            "day_label": "Day 1",
                            "periods": [
                                {"period": "Morning", "summary": "Travel and settle in."},
                                {"period": "Afternoon", "summary": "Continue canyon highlights."},
                                {"period": "Evening", "summary": "Dinner in Springdale."},
                            ],
                        },
                        {
                            "day_label": "Day 2",
                            "periods": [
                                {"period": "Morning", "summary": "Start at The Narrows before crowds."},
                                {"period": "Afternoon", "summary": "Continue at The Narrows and riverside viewpoints."},
                                {"period": "Evening", "summary": "Dinner in town."},
                            ],
                        },
                    ],
                },
                "_registry_decisions": [
                    {
                        "section_target": "top_attractions",
                        "validation_status": "rejected",
                        "display_name": "The Narrows",
                        "rejection_reasons": ["threshold_removed"],
                    }
                ],
            }
        ]
    }

    registry = build_entity_registry(trip)
    reconciled = reconcile_trip_from_registry(trip, registry)
    reconcile_schedule_from_registry(reconciled, registry)

    combined = " ".join(
        str(p.get("summary", "") or "")
        for day in reconciled["destinations"][0]["ai_content"]["possible_daily_schedule"]
        for p in day.get("periods", [])
    ).lower()
    assert "the narrows" not in combined


def test_policy_schedule_reconciliation_keeps_allowed_entities() -> None:
    from generator.entity_registry import build_entity_registry, reconcile_schedule_from_registry, reconcile_trip_from_registry

    trip = {
        "destinations": [
            {
                "id": "zion",
                "name": "Zion National Park",
                "ai_content": {
                    "top_attractions": [{"name": "Canyon Overlook Trail"}],
                    "possible_daily_schedule": [
                        {
                            "day_label": "Day 1",
                            "periods": [
                                {"period": "Morning", "summary": "Start at Canyon Overlook Trail."},
                                {"period": "Afternoon", "summary": "Continue canyon highlights."},
                                {"period": "Evening", "summary": "Dinner in town."},
                            ],
                        }
                    ],
                },
                "_registry_decisions": [
                    {
                        "section_target": "top_attractions",
                        "validation_status": "rejected",
                        "display_name": "The Narrows",
                        "rejection_reasons": ["threshold_removed"],
                    }
                ],
            }
        ]
    }

    registry = build_entity_registry(trip)
    reconciled = reconcile_trip_from_registry(trip, registry)
    reconcile_schedule_from_registry(reconciled, registry)

    combined = " ".join(
        str(p.get("summary", "") or "")
        for day in reconciled["destinations"][0]["ai_content"]["possible_daily_schedule"]
        for p in day.get("periods", [])
    )
    assert "Canyon Overlook Trail" in combined


def test_dedupe_schedule_day_content_fixes_partial_day_duplication() -> None:
    """Regression: the day-level dedup check used to only fire when EVERY
    period in a day was already a duplicate -- a day with 2 of 3 periods
    repeated (but one genuinely new) triggered nothing at all."""
    gen = _gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Start early at a priority attraction."},
                {"period": "Afternoon", "summary": "Continue with a second major stop."},
                {"period": "Evening", "summary": "Dinner at a local favorite."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Start early at a priority attraction."},
                {"period": "Afternoon", "summary": "Continue with a second major stop."},
                {"period": "Evening", "summary": "Something genuinely different tonight."},
            ],
        },
    ]

    out = gen._dedupe_schedule_day_content(days)

    day2 = out[1]["periods"]
    assert day2[0]["summary"] != "Start early at a priority attraction."
    assert "different trailhead or district" in day2[0]["summary"].lower()
    assert day2[1]["summary"] != "Continue with a second major stop."
    assert "different area" in day2[1]["summary"].lower()
    # The genuinely new Evening summary must be left untouched.
    assert day2[2]["summary"] == "Something genuinely different tonight."


def test_dedupe_schedule_day_content_leaves_distinct_days_untouched() -> None:
    gen = _gen()
    days = [
        {
            "day_label": "Day 1",
            "periods": [
                {"period": "Morning", "summary": "Hike Angels Landing."},
                {"period": "Afternoon", "summary": "Explore the Narrows."},
                {"period": "Evening", "summary": "Dinner in Springdale."},
            ],
        },
        {
            "day_label": "Day 2",
            "periods": [
                {"period": "Morning", "summary": "Drive Kolob Canyons Road."},
                {"period": "Afternoon", "summary": "Visit Emerald Pools."},
                {"period": "Evening", "summary": "Dinner at Bit & Spur."},
            ],
        },
    ]

    out = gen._dedupe_schedule_day_content(days)

    assert out[1]["periods"][0]["summary"] == "Drive Kolob Canyons Road."
    assert out[1]["periods"][1]["summary"] == "Visit Emerald Pools."
    assert out[1]["periods"][2]["summary"] == "Dinner at Bit & Spur."
