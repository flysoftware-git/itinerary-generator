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
    recommendation, which is no longer accurate and must still be scrubbed."""
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
    assert "currently eligible" in morning


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
