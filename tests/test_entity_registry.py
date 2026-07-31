from generator.entity_registry import build_entity_registry


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