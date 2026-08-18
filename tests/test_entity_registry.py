from generator.entity_registry import build_entity_registry, reconcile_schedule_from_registry, reconcile_trip_from_registry


def test_build_entity_registry_captures_section_targets_and_ownership() -> None:
    trip = {
        "destinations": [
            {
                "id": "santafe",
                "name": "Santa Fe",
                "ai_content": {
                    "top_attractions": [
                        {"name": "Dale Ball Trail", "type": "hike", "url": ""},
                    ],
                    "getting_here": {
                        "en_route_stops": [{"name": "Madrid"}],
                    },
                    "getting_there": {
                        "route_options": [{"title": "Turquoise Trail Scenic Byway"}],
                    },
                    "dinner_recommendations": [{"name": "La Choza"}],
                },
                "scenic_drives": [{"title": "Hyde Memorial Loop"}],
                "cultural_events": {
                    "events": [{"name": "Spanish Market"}],
                },
            }
        ]
    }

    registry = build_entity_registry(trip)
    entities = {entity["section_target"]: entity for entity in registry["entities"]}

    assert entities["top_attractions"]["entity_class"] == "trail"
    assert entities["top_attractions"]["ownership_type"] == "destination"
    assert entities["getting_here.en_route_stops"]["entity_class"] == "en_route_stop"
    assert entities["getting_there.route_options"]["entity_class"] == "route_option"
    assert entities["getting_there.route_options"]["ownership_type"] == "transfer_leg"
    assert entities["dinner_recommendations"]["entity_class"] == "restaurant"
    assert entities["scenic_drives"]["entity_class"] == "scenic_drive"
    assert entities["cultural_events"]["entity_class"] == "event"

    destination_view = registry["destination_view"]["santafe"]
    assert len(destination_view["top_attractions"]) == 1
    assert len(destination_view["getting_here.en_route_stops"]) == 1
    assert len(destination_view["getting_there.route_options"]) == 1
    assert len(destination_view["dinner_recommendations"]) == 1
    assert len(destination_view["scenic_drives"]) == 1
    assert len(destination_view["cultural_events"]) == 1


def test_build_entity_registry_tracks_reassignment_and_rejection_directives() -> None:
    trip = {
        "destinations": [
            {
                "id": "santafe",
                "name": "Santa Fe",
                "ai_content": {
                    "top_attractions": [
                        {
                            "name": "Ghost Ranch Trail",
                            "type": "hike",
                            "url": "https://example.com/ghost-ranch-trail",
                            "_registry": {
                                "validation_status": "rejected",
                                "rejection_reasons": ["url_rejected"],
                                "rendered_url": "",
                            },
                        }
                    ],
                },
                "scenic_drives": [
                    {
                        "title": "Turquoise Trail Scenic Byway",
                        "description": "Historic route.",
                        "url": "https://example.com/turquoise",
                        "_registry": {
                            "ownership_type": "transfer_leg",
                            "section_target": "getting_there.route_options",
                        },
                    }
                ],
            }
        ]
    }

    registry = build_entity_registry(trip)
    report = registry["reports"][0]
    destination_view = registry["destination_view"]["santafe"]
    entities = {entity["display_name"]: entity for entity in registry["entities"]}

    assert destination_view["scenic_drives"] == []
    assert len(destination_view["getting_there.route_options"]) == 1
    assert report["reassigned"][0]["from"] == "scenic_drives"
    assert report["reassigned"][0]["to"] == "getting_there.route_options"
    assert report["rejected"][0]["reasons"] == ["url_rejected"]
    assert entities["Ghost Ranch Trail"]["rendered_url"] == ""


def test_build_entity_registry_includes_removed_entity_decisions() -> None:
    trip = {
        "destinations": [
            {
                "id": "pagosa",
                "name": "Pagosa Springs",
                "ai_content": {
                    "top_attractions": [],
                    "getting_here": {"en_route_stops": []},
                    "getting_there": {"route_options": []},
                    "dinner_recommendations": [],
                },
                "scenic_drives": [],
                "cultural_events": {"events": []},
                "_registry_decisions": [
                    {
                        "entity_class": "restaurant",
                        "display_name": "Nello's Bistro",
                        "section_target": "dinner_recommendations",
                        "validation_status": "rejected",
                        "rejection_reasons": ["entity_removed"],
                        "rendered_url": "",
                        "metadata": {"removed": True},
                    }
                ],
            }
        ]
    }

    registry = build_entity_registry(trip)
    report = registry["reports"][0]
    entities = {entity["display_name"]: entity for entity in registry["entities"]}

    assert registry["destination_view"]["pagosa"]["dinner_recommendations"] == []
    assert report["rejected"][0]["reasons"] == ["entity_removed"]
    assert entities["Nello's Bistro"]["metadata"]["removed"] is True


def test_reconcile_schedule_from_registry_scrubs_rejected_attraction_mention() -> None:
    trip = {
        "destinations": [
            {
                "id": "telluride",
                "name": "Telluride",
                "ai_content": {
                    "top_attractions": [
                        {
                            "name": "Bear Creek Falls",
                            "type": "hike",
                            "url": "https://example.com/bear-creek",
                            "_registry": {"validation_status": "rejected", "rejection_reasons": ["url_rejected"]},
                        },
                    ],
                    "possible_daily_schedule": [
                        {
                            "day_label": "Day 1",
                            "periods": [
                                {"period": "Morning", "summary": "Start with Bear Creek Falls before lunch."},
                                {"period": "Afternoon", "summary": "Explore nearby highlights."},
                                {"period": "Evening", "summary": "Dinner in town."},
                            ],
                        }
                    ],
                },
            }
        ]
    }

    registry = build_entity_registry(trip)
    reconciled = reconcile_trip_from_registry(trip, registry)
    reconcile_schedule_from_registry(reconciled, registry)

    morning = reconciled["destinations"][0]["ai_content"]["possible_daily_schedule"][0]["periods"][0]["summary"].lower()
    assert "bear creek falls" not in morning
    assert "currently eligible" in morning


def test_reconcile_schedule_from_registry_covers_non_attraction_sections() -> None:
    """Regression: the old audit-time reconciler only ever looked at
    top_attractions -- a rejected restaurant/scenic-drive/en-route-stop
    mention was never scrubbed from the schedule. The registry-based version
    spans every section."""
    trip = {
        "destinations": [
            {
                "id": "moab",
                "name": "Moab",
                "ai_content": {
                    "dinner_recommendations": [
                        {
                            "name": "Sunset Grill",
                            "url": "https://example.com/sunset-grill",
                            "_registry": {"validation_status": "rejected", "rejection_reasons": ["closed_business"]},
                        },
                    ],
                    "possible_daily_schedule": [
                        {
                            "day_label": "Day 1",
                            "periods": [
                                {"period": "Morning", "summary": "Explore town."},
                                {"period": "Afternoon", "summary": "Free time."},
                                {"period": "Evening", "summary": "Dinner at Sunset Grill overlooking the canyon."},
                            ],
                        }
                    ],
                },
            }
        ]
    }

    registry = build_entity_registry(trip)
    reconciled = reconcile_trip_from_registry(trip, registry)
    reconcile_schedule_from_registry(reconciled, registry)

    evening = reconciled["destinations"][0]["ai_content"]["possible_daily_schedule"][0]["periods"][2]["summary"].lower()
    assert "sunset grill" not in evening
    assert "eligible" in evening


def test_reconcile_schedule_from_registry_scrubs_threshold_demoted_mention_even_though_accepted() -> None:
    """A trail demoted to a plain attraction for exceeding the mileage
    threshold stays 'accepted' in the registry (it's still present, just
    re-typed) -- but a schedule mention still implies the original hike
    recommendation, which is no longer accurate and must still be scrubbed.

    A second, still-fully-accepted attraction (Bridal Veil Falls) survives
    for this destination, so the scrub should re-anchor the period to that
    real, concrete attraction rather than falling back to vague "currently
    eligible" filler -- the generic fallback is reserved for when no real
    substitute is available at all (see the "rejected mention" test below,
    where the only attraction present is the blocked one)."""
    trip = {
        "destinations": [
            {
                "id": "telluride",
                "name": "Telluride",
                "ai_content": {
                    "top_attractions": [
                        {
                            "name": "San Miguel River Trail",
                            "type": "attraction",
                            "_registry": {
                                "validation_status": "accepted",
                                "rejection_reasons": ["threshold_demoted_to_attraction"],
                            },
                        },
                        {"name": "Bridal Veil Falls", "type": "attraction"},
                    ],
                    "possible_daily_schedule": [
                        {
                            "day_label": "Day 1",
                            "periods": [
                                {"period": "Morning", "summary": "Start with San Miguel River Trail before lunch."},
                                {"period": "Afternoon", "summary": "Explore nearby highlights."},
                                {"period": "Evening", "summary": "Dinner in town."},
                            ],
                        }
                    ],
                },
            }
        ]
    }

    registry = build_entity_registry(trip)
    reconciled = reconcile_trip_from_registry(trip, registry)
    reconcile_schedule_from_registry(reconciled, registry)

    morning = reconciled["destinations"][0]["ai_content"]["possible_daily_schedule"][0]["periods"][0]["summary"].lower()
    assert "san miguel river trail" not in morning
    assert "bridal veil falls" in morning
    assert "currently eligible" not in morning


def test_reconcile_schedule_from_registry_preserves_mentions_of_accepted_entities() -> None:
    trip = {
        "destinations": [
            {
                "id": "telluride",
                "name": "Telluride",
                "ai_content": {
                    "top_attractions": [
                        {"name": "Bridal Veil Falls", "type": "attraction"},
                    ],
                    "possible_daily_schedule": [
                        {
                            "day_label": "Day 1",
                            "periods": [
                                {"period": "Morning", "summary": "Visit Bridal Veil Falls before lunch."},
                            ],
                        }
                    ],
                },
            }
        ]
    }

    registry = build_entity_registry(trip)
    reconciled = reconcile_trip_from_registry(trip, registry)
    reconcile_schedule_from_registry(reconciled, registry)

    morning = reconciled["destinations"][0]["ai_content"]["possible_daily_schedule"][0]["periods"][0]["summary"]
    assert "Bridal Veil Falls" in morning


def test_reconcile_schedule_from_registry_afternoon_names_a_real_substitute_not_generic_filler() -> None:
    """Regression grounded in the project owner's real review finding:
    'Still disappointed in the scheduler falling back to generic statements
    like "Focus on currently eligible nearby highlights and realistic
    transition time between stops." for multiple afternoons ... Make
    decisions about recommendations, they are just recommendations.'

    Root cause: when a schedule period names an attraction that later gets
    rejected by the entity registry (bad URL, dedup, etc.), the old
    reconciler discarded the ENTIRE period summary and replaced it with
    this generic, non-committal filler -- even when another real, accepted
    attraction for the same destination was sitting right there unused.
    This is a genuine fallback-path bug (not the LLM echoing prompt
    instructions -- the phrase lives in entity_registry.py's
    _SCHEDULE_FALLBACK_BY_PERIOD, never in any ai_content.py prompt). The
    fix re-anchors the period to a real substitute attraction whenever one
    is available, and only falls back to the generic phrase when nothing
    concrete is left to name."""
    trip = {
        "destinations": [
            {
                "id": "moab",
                "name": "Moab",
                "ai_content": {
                    "top_attractions": [
                        {
                            "name": "Corona Arch Trail",
                            "type": "hike",
                            "url": "https://example.com/corona-arch",
                            "_registry": {"validation_status": "rejected", "rejection_reasons": ["url_rejected"]},
                        },
                        {"name": "Dead Horse Point State Park", "type": "viewpoint"},
                    ],
                    "possible_daily_schedule": [
                        {
                            "day_label": "Day 1",
                            "periods": [
                                {"period": "Morning", "summary": "Start the day in town."},
                                {"period": "Afternoon", "summary": "Hike Corona Arch Trail before sunset."},
                                {"period": "Evening", "summary": "Dinner in town."},
                            ],
                        }
                    ],
                },
            }
        ]
    }

    registry = build_entity_registry(trip)
    reconciled = reconcile_trip_from_registry(trip, registry)
    reconcile_schedule_from_registry(reconciled, registry)

    afternoon = reconciled["destinations"][0]["ai_content"]["possible_daily_schedule"][0]["periods"][1][
        "summary"
    ].lower()
    assert "corona arch trail" not in afternoon
    assert "focus on currently eligible nearby highlights" not in afternoon
    assert "dead horse point state park" in afternoon


def test_reconcile_schedule_from_registry_reuses_real_attractions_when_pool_is_small() -> None:
    """Regression grounded in the real SW2026-dipstick67 output for Bryce
    Canyon National Park: three periods (Day 1 Morning, Day 2 Afternoon,
    Day 3 Morning) named a since-rejected attraction and needed a
    substitute. Only three real accepted attractions exist for the
    destination, and each one already legitimately appears once elsewhere
    in the schedule's untouched periods (Day 1 Evening, Day 2 Morning, Day
    2 Evening) -- exactly like a real multi-day stay with a small
    attraction pool.

    The old reconciler treated 'already mentioned anywhere in the
    schedule' as permanently unavailable for substitution, so by the time
    it reached the first blocked period every real candidate was already
    marked used and it fell through to the fully generic 'currently
    eligible' filler for all three -- even though naming a real,
    already-elsewhere-mentioned attraction again would have been strictly
    more informative and is exactly what the rest of the schedule already
    does naturally (the untouched periods reuse nothing here, but the
    pipeline elsewhere tolerates a highlight like a sunset viewpoint being
    named on more than one evening of a stay). The fix spreads reuse
    round-robin by least-used candidate instead of refusing reuse
    outright, only forbidding a duplicate within the same day."""
    trip = {
        "destinations": [
            {
                "id": "bryce",
                "name": "Bryce Canyon National Park",
                "ai_content": {
                    "top_attractions": [
                        {
                            "name": "Rainbow Point",
                            "type": "viewpoint",
                            "url": "https://example.com/rainbow-point",
                            "_registry": {"validation_status": "rejected", "rejection_reasons": ["url_rejected"]},
                        },
                        {"name": "Sunrise Point", "type": "viewpoint"},
                        {"name": "Navajo Loop Trail", "type": "hike"},
                        {"name": "Queens Garden Trail", "type": "hike"},
                    ],
                    "possible_daily_schedule": [
                        {
                            "day_label": "Day 1",
                            "periods": [
                                {"period": "Morning", "summary": "Start with Rainbow Point sunrise views."},
                                {"period": "Afternoon", "summary": "Relax in town."},
                                {"period": "Evening", "summary": "Watch the sunset from Sunrise Point."},
                            ],
                        },
                        {
                            "day_label": "Day 2",
                            "periods": [
                                {"period": "Morning", "summary": "Hike Navajo Loop Trail."},
                                {"period": "Afternoon", "summary": "Return to Rainbow Point for photos."},
                                {"period": "Evening", "summary": "Stroll Queens Garden Trail before dinner."},
                            ],
                        },
                        {
                            "day_label": "Day 3",
                            "periods": [
                                {"period": "Morning", "summary": "Explore Rainbow Point again."},
                                {"period": "Afternoon", "summary": "Free time in town."},
                                {"period": "Evening", "summary": "Dinner in town."},
                            ],
                        },
                    ],
                },
            }
        ]
    }

    registry = build_entity_registry(trip)
    reconciled = reconcile_trip_from_registry(trip, registry)
    reconcile_schedule_from_registry(reconciled, registry)

    schedule = reconciled["destinations"][0]["ai_content"]["possible_daily_schedule"]
    day1_morning = schedule[0]["periods"][0]["summary"].lower()
    day2_afternoon = schedule[1]["periods"][1]["summary"].lower()
    day3_morning = schedule[2]["periods"][0]["summary"].lower()

    for text in (day1_morning, day2_afternoon, day3_morning):
        assert "rainbow point" not in text
        assert "currently eligible" not in text
        assert any(
            name in text for name in ("sunrise point", "navajo loop trail", "queens garden trail")
        ), text
