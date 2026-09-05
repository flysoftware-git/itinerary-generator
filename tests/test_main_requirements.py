import json

import pytest

import generator.main as main_mod


def _option_by_name(name: str):
    for param in main_mod.main.params:
        if getattr(param, "name", None) == name:
            return param
    raise AssertionError(f"Option not found: {name}")


def test_cli_declares_required_options() -> None:
    expected_names = {
        "manifest",
        "output",
        "config_path",
        "llm_provider",
        "environment",
        "env_file",
        "privacy_mode",
        "llm_model",
        "dry_run",
        "skip_images",
        "refresh_image_cache",
        "skip_events",
        "skip_url_discovery",
        "no_trails",
        "alltrails_source",
        "attraction_source",
        "restaurant_source",
        "en_route_source",
        "noschedule",
        "noseed",
        "destinations",
        "first_destination_only",
        "log_level",
        "verbose",
    }
    actual_names = {param.name for param in main_mod.main.params}
    assert expected_names.issubset(actual_names)


def test_cli_llm_provider_and_log_level_choices() -> None:
    provider = _option_by_name("llm_provider")
    levels = _option_by_name("log_level")

    assert set(provider.type.choices) == {
        "openai",
        "anthropic",
        "deepseek",
        "gemini",
        "grok",
        "azure_openai",
    }
    assert set(levels.type.choices) == {
        "debug",
        "info",
        "warning",
        "error",
        "critical",
    }


def test_cli_source_option_choices_include_direct_link_batch() -> None:
    alltrails = _option_by_name("alltrails_source")
    attractions = _option_by_name("attraction_source")
    restaurants = _option_by_name("restaurant_source")
    en_route = _option_by_name("en_route_source")

    assert set(alltrails.type.choices) == {
        "direct-link-batch",
        "search",
    }
    assert set(attractions.type.choices) == {"search", "direct-link-batch"}
    assert set(restaurants.type.choices) == {"search", "direct-link-batch"}
    assert set(en_route.type.choices) == {"search", "direct-link-batch"}


def test_is_us_coordinates_covers_us_and_non_us() -> None:
    assert main_mod._is_us_coordinates(37.2982, -113.0263) is True
    assert main_mod._is_us_coordinates(64.2008, -149.4937) is True
    assert main_mod._is_us_coordinates(45.8150, 15.9819) is False


def test_write_pwa_assets_creates_manifest_and_service_worker(tmp_path) -> None:
    trip = {
        "trip": {
            "title": "Test Trip",
            "subtitle": "Test Subtitle",
            "theme_color": "#123456",
        }
    }

    main_mod._write_pwa_assets(tmp_path, trip)

    manifest_path = tmp_path / "manifest.webmanifest"
    sw_path = tmp_path / "sw.js"

    assert manifest_path.exists()
    assert sw_path.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sw_js = sw_path.read_text(encoding="utf-8")

    assert manifest["name"] == "Test Trip"
    assert manifest["description"] == "Test Subtitle"
    assert manifest["theme_color"] == "#123456"
    assert manifest["start_url"] == "./index.html"
    assert manifest["display"] == "standalone"
    assert len(manifest["icons"]) == 2

    assert "manifest.webmanifest" in sw_js
    assert "self.addEventListener('fetch'" in sw_js


def test_write_pwa_assets_defaults_when_trip_metadata_missing(tmp_path) -> None:
    main_mod._write_pwa_assets(tmp_path, {})
    manifest = json.loads((tmp_path / "manifest.webmanifest").read_text(encoding="utf-8"))

    assert manifest["name"] == "Road Trip Itinerary"
    assert manifest["short_name"] == "Road Trip Itinerary"
    assert manifest["description"] == "Interactive road trip itinerary"
    assert manifest["theme_color"] == "#C0623E"


def test_filter_destinations_can_limit_to_first_destination() -> None:
    trip = {
        "destinations": [
            {"id": "st-george", "name": "St. George, Utah"},
            {"id": "zion", "name": "Zion National Park"},
        ]
    }

    main_mod._filter_destinations(trip, (), first_destination_only=True)

    assert [d["id"] for d in trip["destinations"]] == ["st-george"]


def test_filter_destinations_applies_destination_filter_before_first_destination() -> None:
    trip = {
        "destinations": [
            {"id": "st-george", "name": "St. George, Utah"},
            {"id": "zion", "name": "Zion National Park"},
            {"id": "bryce", "name": "Bryce Canyon National Park"},
        ]
    }

    main_mod._filter_destinations(trip, ("zion", "bryce"), first_destination_only=True)

    assert [d["id"] for d in trip["destinations"]] == ["zion"]


def test_strip_destination_seeds_clears_all_manifest_seed_lists() -> None:
    trip = {
        "destinations": [
            {"id": "zion", "seeds": ["Angels Landing", "The Narrows"]},
            {"id": "moab", "seeds": ["Delicate Arch"]},
            {"id": "santafe"},
        ]
    }

    stripped = main_mod._strip_destination_seeds(trip)

    assert stripped == 3
    assert trip["destinations"][0]["seeds"] == []
    assert trip["destinations"][1]["seeds"] == []
    assert trip["destinations"][2]["seeds"] == []


def test_apply_privacy_redaction_replaces_planning_links_with_placeholder() -> None:
    trip = {
        "destinations": [
            {"id": "zion", "planning_links": [{"label": "Zion Trip Notes", "url": "https://www.notion.so/x"}]},
            {"id": "moab", "planning_links": [{"label": "A", "url": "https://a"}, {"label": "B", "url": "https://b"}]},
            {"id": "santafe"},
        ]
    }

    counts = main_mod._apply_privacy_redaction(trip)

    assert counts["planning_links"] == 3
    assert trip["destinations"][0]["planning_links"] == [{"label": "Trip Plans", "url": "", "redacted": True}]
    assert trip["destinations"][1]["planning_links"] == [{"label": "Trip Plans", "url": "", "redacted": True}]
    assert trip["destinations"][2].get("planning_links", []) == []


def test_apply_privacy_redaction_blanks_lodging_name_keeps_location_and_checkin() -> None:
    trip = {
        "destinations": [
            {
                "id": "zion",
                "lodging": {"name": "Zion Lodge", "location": "Springdale, UT", "checkin_time": "4:00 PM"},
            },
            {"id": "moab"},
        ]
    }

    counts = main_mod._apply_privacy_redaction(trip)

    assert counts["lodging_names"] == 1
    assert trip["destinations"][0]["lodging"]["name"] == ""
    assert trip["destinations"][0]["lodging"]["location"] == "Springdale, UT"
    assert trip["destinations"][0]["lodging"]["checkin_time"] == "4:00 PM"


def test_apply_privacy_redaction_blanks_lodging_website_with_the_name() -> None:
    """A link to the property's own site names the property just as precisely
    as lodging.name does, so it is redacted alongside the name rather than
    treated as harmless-public-URL and exempted."""
    trip = {
        "destinations": [
            {
                "id": "zion",
                "lodging": {
                    "name": "Zion Lodge",
                    "location": "Springdale, UT",
                    "checkin_time": "4:00 PM",
                    "website": "https://www.zionlodge.com/",
                },
            },
            {"id": "moab", "lodging": {"location": "Moab, UT"}},
        ]
    }

    counts = main_mod._apply_privacy_redaction(trip)

    assert counts["lodging_websites"] == 1
    assert trip["destinations"][0]["lodging"]["website"] == ""
    assert trip["destinations"][0]["lodging"]["name"] == ""
    # Routing/scheduling inputs stay intact -- redaction is display-only.
    assert trip["destinations"][0]["lodging"]["location"] == "Springdale, UT"
    assert trip["destinations"][0]["lodging"]["checkin_time"] == "4:00 PM"
    assert "website" not in trip["destinations"][1]["lodging"]


def test_resolve_privacy_redaction_auto_follows_environment() -> None:
    assert main_mod._resolve_privacy_redaction("auto", "prod") is True
    assert main_mod._resolve_privacy_redaction("auto", "dev") is False
    assert main_mod._resolve_privacy_redaction("auto", "eval") is False
    assert main_mod._resolve_privacy_redaction(None, "prod") is True
    assert main_mod._resolve_privacy_redaction(None, "dev") is False


def test_resolve_privacy_redaction_explicit_override_wins() -> None:
    assert main_mod._resolve_privacy_redaction("on", "dev") is True
    assert main_mod._resolve_privacy_redaction("off", "prod") is False
    assert main_mod._resolve_privacy_redaction("OFF", "prod") is False


def test_registry_roundtrip_preserves_destination_shaped_sections() -> None:
    trip = {
        "trip": {"return": "Albuquerque, NM"},
        "destinations": [
            {
                "id": "santafe",
                "name": "Santa Fe",
                "ai_content": {
                    "top_attractions": [
                        {"name": "Canyon Road", "type": "attraction", "url": "https://example.com/canyon-road"},
                        {"name": "Dale Ball Trail", "type": "hike", "url": ""},
                    ],
                    "getting_here": {
                        "en_route_stops": [
                            {"name": "Madrid", "url": "https://example.com/madrid"},
                        ],
                    },
                    "getting_there": {
                        "route_summary": "Departure leg toward Albuquerque, NM.",
                        "route_options": [
                            {"title": "Turquoise Trail Scenic Byway", "description": "Historic route.", "url": "https://example.com/turquoise"},
                        ],
                    },
                    "dinner_recommendations": [
                        {"name": "La Choza", "url": "https://example.com/la-choza"},
                    ],
                },
                "scenic_drives": [
                    {"title": "Hyde Memorial Loop", "description": "Mountain drive.", "url": "https://example.com/hyde"},
                ],
                "cultural_events": {
                    "has_events": True,
                    "events": [
                        {"name": "Spanish Market", "description": "Annual market.", "url": "https://example.com/spanish-market"},
                    ],
                    "honest_assessment": "Seasonal events vary.",
                    "local_tip": "Check plaza posters.",
                },
            }
        ],
    }

    reconciled = main_mod._reconcile_trip_via_registry(trip)

    dest = reconciled["destinations"][0]
    assert [item["name"] for item in dest["ai_content"]["top_attractions"]] == ["Canyon Road", "Dale Ball Trail"]
    assert [item["name"] for item in dest["ai_content"]["getting_here"]["en_route_stops"]] == ["Madrid"]
    assert [item["title"] for item in dest["ai_content"]["getting_there"]["route_options"]] == ["Turquoise Trail Scenic Byway"]
    assert [item["name"] for item in dest["ai_content"]["dinner_recommendations"]] == ["La Choza"]
    assert [item["title"] for item in dest["scenic_drives"]] == ["Hyde Memorial Loop"]
    assert [item["name"] for item in dest["cultural_events"]["events"]] == ["Spanish Market"]
    assert dest["ai_content"]["getting_there"]["route_summary"] == "Departure leg toward Albuquerque, NM."


def test_registry_roundtrip_can_strip_url_and_reassign_section_via_directives() -> None:
    trip = {
        "destinations": [
            {
                "id": "santafe",
                "name": "Santa Fe",
                "ai_content": {
                    "top_attractions": [
                        {
                            "name": "Dale Ball Trail",
                            "type": "hike",
                            "url": "https://example.com/dale-ball",
                            "_registry": {
                                "validation_status": "accepted",
                                "rendered_url": "",
                            },
                        }
                    ],
                    "getting_there": {"route_options": []},
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
                "cultural_events": {"events": []},
            }
        ]
    }

    reconciled = main_mod._reconcile_trip_via_registry(trip)

    dest = reconciled["destinations"][0]
    assert dest["scenic_drives"] == []
    assert [item["title"] for item in dest["ai_content"]["getting_there"]["route_options"]] == ["Turquoise Trail Scenic Byway"]
    assert dest["ai_content"]["top_attractions"][0]["url"] == ""


def test_write_entity_registry_debug_report_creates_summary_and_payload(tmp_path) -> None:
    registry = {
        "entities": [
            {"entity_id": "santafe:route_option:turquoise-trail", "display_name": "Turquoise Trail Scenic Byway"},
            {"entity_id": "santafe:trail:dale-ball-trail", "display_name": "Dale Ball Trail"},
        ],
        "destination_view": {
            "santafe": {
                "getting_there.route_options": ["santafe:route_option:turquoise-trail"],
                "top_attractions": ["santafe:trail:dale-ball-trail"],
            }
        },
        "reports": [
            {
                "destination_id": "santafe",
                "accepted": ["santafe:trail:dale-ball-trail"],
                "rejected": [{"entity_id": "santafe:trail:ghost-ranch", "reasons": ["url_rejected"]}],
                "reassigned": [{"entity_id": "santafe:route_option:turquoise-trail", "from": "scenic_drives", "to": "getting_there.route_options"}],
                "quarantined": [],
            }
        ],
    }

    report_path = main_mod._write_entity_registry_debug_report(tmp_path, registry)

    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["entity_count"] == 2
    assert payload["summary"]["destination_count"] == 1
    assert payload["summary"]["accepted_count"] == 1
    assert payload["summary"]["rejected_count"] == 1
    assert payload["summary"]["reassigned_count"] == 1
    assert payload["registry"]["entities"][0]["entity_id"] == "santafe:route_option:turquoise-trail"


def test_build_destination_status_report_counts_excluded_entries() -> None:
    trip = {
        "destinations": [
            {
                "id": "moab",
                "name": "Moab",
                "images": [],
                "cultural_events": {"events": []},
                "_url_discovery": {
                    "reason_counts": {},
                    "source_counts": {},
                    "thread_count": 0,
                    "event_count": 0,
                    "disposition_threads": {},
                },
                "ai_content": {
                    "top_attractions": [],
                    "getting_here": {"en_route_stops": []},
                    "dinner_recommendations": [],
                },
            }
        ]
    }
    registry = {
        "entities": [
            {
                "entity_id": "moab:trail:deleted-trail",
                "destination_id": "moab",
                "entity_class": "trail",
                "display_name": "Deleted Trail",
                "validation_status": "excluded",
                "rejection_reasons": ["dead_url_404"],
                "rendered_url": "",
                "section_target": "top_attractions",
            }
        ],
        "destination_view": {"moab": {"top_attractions": ["moab:trail:deleted-trail"]}},
        "reports": [
            {
                "destination_id": "moab",
                "accepted": [],
                "rejected": [{"entity_id": "moab:trail:deleted-trail", "reasons": ["dead_url_404"]}],
                "reassigned": [],
                "quarantined": [],
            }
        ],
    }

    report = main_mod._build_destination_status_report(
        trip=trip,
        registry=registry,
        run_id="test-run",
        skip_events=False,
        skip_images=True,
        skip_url_discovery=False,
        retry_policy={},
    )
    dest = report["destinations"][0]
    assert dest["validation_counts"]["excluded"] == 1
    assert dest["stage_status"]["url_discovery"]["excluded_count"] == 1


def test_build_destination_status_report_marks_quarantine_and_retry_triggers() -> None:
    trip = {
        "destinations": [
            {
                "id": "santafe",
                "name": "Santa Fe",
                "images": [{"path": "images/santafe-1.jpg"}],
                "cultural_events": {"events": [{"name": "Spanish Market"}]},
                "_url_discovery": {
                    "reason_counts": {"direct_batch_accepted": 2},
                    "source_counts": {"direct_batch": 2},
                    "thread_count": 1,
                    "event_count": 2,
                    "disposition_threads": {
                        "attraction-santa-fe-plaza": [
                            {
                                "seq": 1,
                                "reason": "direct_batch_accepted",
                                "source": "direct_batch",
                                "item": "Santa Fe Plaza",
                                "url": "https://example.com/sf-plaza",
                            }
                        ]
                    },
                },
            },
            {
                "id": "taos",
                "name": "Taos",
                "images": [],
                "cultural_events": {"events": []},
            },
        ]
    }
    registry = {
        "entities": [
            {
                "entity_id": "santafe:trail:dale-ball-trail",
                "destination_id": "santafe",
                "validation_status": "accepted",
            },
            {
                "entity_id": "taos:trail:williams-lake-trail",
                "destination_id": "taos",
                "validation_status": "quarantined",
            },
            {
                "entity_id": "taos:restaurant:closed-cafe",
                "destination_id": "taos",
                "validation_status": "rejected",
            },
        ],
        "reports": [
            {
                "destination_id": "santafe",
                "accepted": ["santafe:trail:dale-ball-trail"],
                "rejected": [],
                "reassigned": [],
                "quarantined": [],
            },
            {
                "destination_id": "taos",
                "accepted": [],
                "rejected": [{"entity_id": "taos:restaurant:closed-cafe", "reasons": ["entity_removed"]}],
                "reassigned": [],
                "quarantined": ["taos:trail:williams-lake-trail"],
            },
        ],
    }

    payload = main_mod._build_destination_status_report(
        trip=trip,
        registry=registry,
        run_id="run-123",
        skip_events=False,
        skip_images=False,
        skip_url_discovery=False,
    )

    assert payload["run_id"] == "run-123"
    assert payload["summary"]["destination_count"] == 2
    assert payload["summary"]["status_counts"]["healthy"] == 1
    assert payload["summary"]["status_counts"]["quarantined"] == 1
    assert payload["summary"]["retry_recommended_count"] == 1

    by_destination = {row["destination_id"]: row for row in payload["destinations"]}
    assert by_destination["santafe"]["status"] == "healthy"
    assert by_destination["santafe"]["stage_status"]["images"]["status"] == "completed"
    assert by_destination["santafe"]["stage_status"]["url_discovery"]["source_counts"] == {"direct_batch": 2}
    assert by_destination["santafe"]["stage_status"]["url_discovery"]["reason_counts"] == {"direct_batch_accepted": 2}
    assert by_destination["santafe"]["stage_status"]["url_discovery"]["disposition_thread_count"] == 1
    assert by_destination["santafe"]["stage_status"]["url_discovery"]["en_route_reliability"] == {
        "total_en_route_stops": 0,
        "resolved_with_url": 0,
        "exhaustion_or_no_match": 0,
        "resolution_rate": 1.0,
    }
    assert by_destination["santafe"]["url_discovery_disposition_threads"]["attraction-santa-fe-plaza"][0]["source"] == "direct_batch"

    assert by_destination["taos"]["status"] == "quarantined"
    assert by_destination["taos"]["retry_recommended"] is True
    assert "registry_quarantined_entities" in by_destination["taos"]["retry_triggers"]
    assert "image_shortfall" in by_destination["taos"]["retry_triggers"]
    assert by_destination["taos"]["stage_status"]["images"]["status"] == "shortfall"
    assert by_destination["taos"]["rejected_reasons"] == ["entity_removed"]


def test_build_destination_status_report_tracks_en_route_reliability() -> None:
    trip = {
        "destinations": [
            {
                "id": "zion",
                "name": "Zion National Park",
                "images": [{"path": "images/zion-1.jpg"}],
                "cultural_events": {"events": []},
                "_url_discovery": {
                    "reason_counts": {
                        "discovery_completed": 2,
                        "direct_batch_source_locked_no_match": 1,
                    },
                    "source_counts": {"direct_batch": 3},
                    "thread_count": 3,
                    "event_count": 6,
                    "disposition_threads": {
                        "en-route-stop-zion-wilson-arch": [
                            {
                                "seq": 1,
                                "kind": "en_route_stop",
                                "reason": "direct_batch_selected_authoritative",
                                "source": "direct_batch",
                                "item": "Wilson Arch",
                                "url": "https://example.com/wilson-arch",
                            },
                            {
                                "seq": 2,
                                "kind": "en_route_stop",
                                "reason": "discovery_completed",
                                "source": "direct_batch",
                                "item": "Wilson Arch",
                                "url": "https://example.com/wilson-arch",
                            },
                        ],
                        "en-route-stop-zion-leeds": [
                            {
                                "seq": 3,
                                "kind": "en_route_stop",
                                "reason": "direct_batch_no_match",
                                "source": "direct_batch",
                                "item": "Leeds Historic District",
                                "url": "",
                            },
                            {
                                "seq": 4,
                                "kind": "en_route_stop",
                                "reason": "direct_batch_source_locked_no_match",
                                "source": "direct_batch",
                                "item": "Leeds Historic District",
                                "url": "",
                            },
                        ],
                        "en-route-stop-zion-ghost": [
                            {
                                "seq": 5,
                                "kind": "en_route_stop",
                                "reason": "no_canonical_url",
                                "source": "search",
                                "item": "Ghost Stop",
                                "url": "",
                            }
                        ],
                    },
                },
            }
        ]
    }
    registry = {
        "entities": [
            {
                "entity_id": "zion:attraction:angels-landing",
                "destination_id": "zion",
                "validation_status": "accepted",
            }
        ],
        "reports": [
            {
                "destination_id": "zion",
                "accepted": ["zion:attraction:angels-landing"],
                "rejected": [],
                "reassigned": [],
                "quarantined": [],
            }
        ],
    }

    payload = main_mod._build_destination_status_report(
        trip=trip,
        registry=registry,
        run_id="run-en-route-metrics",
        skip_events=False,
        skip_images=False,
        skip_url_discovery=False,
    )

    row = payload["destinations"][0]
    metrics = row["stage_status"]["url_discovery"]["en_route_reliability"]
    assert metrics["total_en_route_stops"] == 3
    assert metrics["resolved_with_url"] == 1
    assert metrics["exhaustion_or_no_match"] == 2
    assert metrics["source_locked_no_match"] == 1
    assert metrics["no_canonical_url"] == 1
    assert metrics["resolution_rate"] == 0.3333


def test_write_destination_status_report_writes_json(tmp_path) -> None:
    status_report = {
        "run_id": "run-abc",
        "summary": {"destination_count": 1},
        "destinations": [{"destination_id": "santafe", "status": "healthy"}],
    }

    report_path = main_mod._write_destination_status_report(tmp_path, status_report)

    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-abc"
    assert payload["destinations"][0]["destination_id"] == "santafe"


def test_write_destination_status_markdown_report_includes_attention_section(tmp_path) -> None:
    status_report = {
        "run_id": "run-abc",
        "generated_at_utc": "2026-07-30T00:00:00+00:00",
        "summary": {
            "destination_count": 2,
            "retry_recommended_count": 1,
            "retry_outcomes": {
                "attempted_destination_count": 1,
                "resolved_after_retry_count": 0,
                "unresolved_after_retry_count": 1,
                "not_retried_due_to_cap_count": 0,
            },
        },
        "destinations": [
            {
                "destination_id": "santafe",
                "destination_name": "Santa Fe",
                "status": "healthy",
                "retry_triggers": [],
                "retry_outcome": {"terminal_state": "stable_without_retry"},
            },
            {
                "destination_id": "taos",
                "destination_name": "Taos",
                "status": "needs_retry",
                "retry_triggers": ["url_collapse", "retry_cap_reached"],
                "retry_outcome": {"terminal_state": "retry_cap_reached_unresolved"},
            },
        ],
    }

    report_path = main_mod._write_destination_status_markdown_report(tmp_path, status_report)

    assert report_path.exists()
    text = report_path.read_text(encoding="utf-8")
    assert "# Destination Status Summary" in text
    assert "## Needs Attention (1)" in text
    assert "Taos (taos)" in text
    assert "retry_cap_reached_unresolved" in text
    assert "url_collapse, retry_cap_reached" in text


def test_write_destination_status_markdown_report_includes_en_route_reliability_summary(tmp_path) -> None:
    status_report = {
        "run_id": "run-en-route-md",
        "generated_at_utc": "2026-08-06T00:00:00+00:00",
        "summary": {
            "destination_count": 1,
            "retry_recommended_count": 0,
        },
        "destinations": [
            {
                "destination_id": "zion",
                "destination_name": "Zion National Park",
                "status": "degraded",
                "retry_triggers": [],
                "retry_outcome": {"terminal_state": "stable_without_retry"},
                "stage_status": {
                    "url_discovery": {
                        "en_route_reliability": {
                            "total_en_route_stops": 4,
                            "resolved_with_url": 3,
                            "exhaustion_or_no_match": 1,
                        }
                    }
                },
            }
        ],
    }

    report_path = main_mod._write_destination_status_markdown_report(tmp_path, status_report)

    text = report_path.read_text(encoding="utf-8")
    assert "en_route_resolved=3/4" in text
    assert "en_route_exhaustion_or_no_match=1" in text


def test_resolve_llm_overrides_reads_flat_manifest_model_key() -> None:
    """Regression: a manifest using flat trip.llm_model (this project's own
    sw_manifest.yaml does: llm_provider: openai / llm_model: gpt-4o-mini) had
    its model override silently dropped -- only nested trip.llm.model or the
    --llm-model CLI flag worked, so the run silently fell back to config.yaml's
    default model instead of what the manifest actually asked for."""
    trip = {"trip": {"llm_provider": "openai", "llm_model": "gpt-4o-mini"}}

    overrides = main_mod._resolve_llm_overrides(trip, cli_provider=None, cli_model=None)

    assert overrides["provider"] == "openai"
    assert overrides["model"] == "gpt-4o-mini"


def test_resolve_llm_overrides_precedence_cli_beats_flat_beats_nested() -> None:
    trip = {
        "trip": {
            "llm": {"model": "nested-model", "provider": "nested-provider"},
            "llm_provider": "flat-provider",
            "llm_model": "flat-model",
        }
    }

    nested_only = main_mod._resolve_llm_overrides(
        {"trip": {"llm": {"model": "nested-model"}}}, cli_provider=None, cli_model=None
    )
    assert nested_only["model"] == "nested-model"

    flat_over_nested = main_mod._resolve_llm_overrides(trip, cli_provider=None, cli_model=None)
    assert flat_over_nested["model"] == "flat-model"
    assert flat_over_nested["provider"] == "flat-provider"

    cli_over_flat = main_mod._resolve_llm_overrides(trip, cli_provider="cli-provider", cli_model="cli-model")
    assert cli_over_flat["model"] == "cli-model"
    assert cli_over_flat["provider"] == "cli-provider"


def test_resolve_llm_overrides_cli_provider_alone_does_not_clobber_manifest_model() -> None:
    """Overriding only the provider via CLI must not blow away a model the
    manifest explicitly asked for."""
    trip = {"trip": {"llm_provider": "grok", "llm_model": "grok-4.5"}}

    overrides = main_mod._resolve_llm_overrides(trip, cli_provider="openai", cli_model=None)

    assert overrides["provider"] == "openai"
    assert overrides["model"] == "grok-4.5"


def test_destination_ids_for_selective_retry_prefers_retry_recommended_and_status() -> None:
    status_report = {
        "destinations": [
            {"destination_id": "santafe", "status": "healthy", "retry_recommended": False},
            {"destination_id": "taos", "status": "needs_retry", "retry_recommended": False},
            {"destination_id": "durango", "status": "healthy", "retry_recommended": True},
            {"destination_id": "taos", "status": "quarantined", "retry_recommended": True},
        ]
    }

    retry_ids = main_mod._destination_ids_for_selective_retry(status_report)

    assert retry_ids == ["taos", "durango"]


def test_selective_retry_runs_only_flagged_destinations() -> None:
    trip = {
        "trip": {"title": "Test"},
        "destinations": [
            {"id": "santafe", "name": "Santa Fe"},
            {"id": "taos", "name": "Taos"},
            {"id": "durango", "name": "Durango"},
        ],
    }
    status_report = {
        "destinations": [
            {"destination_id": "santafe", "status": "healthy", "retry_recommended": False},
            {"destination_id": "taos", "status": "needs_retry", "retry_recommended": True},
        ]
    }

    called = {"events": [], "images": [], "urls": []}

    def _run_events(subset_trip):
        called["events"].append([d["id"] for d in subset_trip["destinations"]])

    def _run_images(subset_trip):
        called["images"].append([d["id"] for d in subset_trip["destinations"]])

    def _run_urls(subset_trip):
        called["urls"].append([d["id"] for d in subset_trip["destinations"]])

    retry_ids = main_mod._selective_retry_destinations(
        trip=trip,
        status_report=status_report,
        config_path="config.yaml",
        llm_client=None,
        output_dir=main_mod.Path("output"),
        refresh_image_cache=False,
        skip_events=False,
        skip_images=False,
        skip_url_discovery=False,
        no_trails=False,
        alltrails_source="direct_link_batch",
        attraction_source="search",
        restaurant_source="search",
        en_route_source="direct_link_batch",
        run_events=_run_events,
        run_images=_run_images,
        run_urls=_run_urls,
    )

    assert retry_ids == ["taos"]
    assert called["events"] == [["taos"]]
    assert called["images"] == [["taos"]]
    assert called["urls"] == [["taos"]]


def test_selective_retry_skips_url_stage_when_breaker_reopens_after_outer_gate() -> None:
    """Regression for a real, observed run (2026-08-15, all-Grok
    --search-provider comparison): the outer caller only checks the
    circuit breaker once, before _selective_retry_destinations is even
    entered. Events/images retry (which run first, inside this function)
    can take real time, and the breaker can reopen in that gap -- firing
    url retry anyway burned 231s across 8 destinations for 0% improvement
    in that run. is_search_circuit_open is re-checked immediately before
    the url retry sub-stage specifically to catch that gap."""
    trip = {
        "trip": {"title": "Test"},
        "destinations": [{"id": "taos", "name": "Taos"}],
    }
    status_report = {
        "destinations": [
            {"destination_id": "taos", "status": "needs_retry", "retry_recommended": True},
        ]
    }
    called = {"events": 0, "images": 0, "urls": 0}

    retry_ids = main_mod._selective_retry_destinations(
        trip=trip,
        status_report=status_report,
        config_path="config.yaml",
        llm_client=None,
        output_dir=main_mod.Path("output"),
        refresh_image_cache=False,
        skip_events=False,
        skip_images=False,
        skip_url_discovery=False,
        no_trails=False,
        alltrails_source="direct_link_batch",
        attraction_source="search",
        restaurant_source="search",
        en_route_source="direct_link_batch",
        is_search_circuit_open=lambda: True,
        run_events=lambda _subset_trip: called.__setitem__("events", called["events"] + 1),
        run_images=lambda _subset_trip: called.__setitem__("images", called["images"] + 1),
        run_urls=lambda _subset_trip: called.__setitem__("urls", called["urls"] + 1),
    )

    # Events/images retry still ran (they don't depend on the search
    # breaker); only the url retry sub-stage was skipped.
    assert retry_ids == ["taos"]
    assert called["events"] == 1
    assert called["images"] == 1
    assert called["urls"] == 0


def test_selective_retry_runs_url_stage_when_breaker_check_returns_false() -> None:
    trip = {
        "trip": {"title": "Test"},
        "destinations": [{"id": "taos", "name": "Taos"}],
    }
    status_report = {
        "destinations": [
            {"destination_id": "taos", "status": "needs_retry", "retry_recommended": True},
        ]
    }
    called = {"urls": 0}

    main_mod._selective_retry_destinations(
        trip=trip,
        status_report=status_report,
        config_path="config.yaml",
        llm_client=None,
        output_dir=main_mod.Path("output"),
        refresh_image_cache=False,
        skip_events=True,
        skip_images=True,
        skip_url_discovery=False,
        no_trails=False,
        alltrails_source="direct_link_batch",
        attraction_source="search",
        restaurant_source="search",
        en_route_source="direct_link_batch",
        is_search_circuit_open=lambda: False,
        run_urls=lambda _subset_trip: called.__setitem__("urls", called["urls"] + 1),
    )

    assert called["urls"] == 1


def test_selective_retry_respects_skip_flags() -> None:
    trip = {
        "trip": {"title": "Test"},
        "destinations": [
            {"id": "taos", "name": "Taos"},
        ],
    }
    status_report = {
        "destinations": [
            {"destination_id": "taos", "status": "quarantined", "retry_recommended": True},
        ]
    }

    called = {"events": 0, "images": 0, "urls": 0}

    def _run_events(_subset_trip):
        called["events"] += 1

    def _run_images(_subset_trip):
        called["images"] += 1

    def _run_urls(_subset_trip):
        called["urls"] += 1

    retry_ids = main_mod._selective_retry_destinations(
        trip=trip,
        status_report=status_report,
        config_path="config.yaml",
        llm_client=None,
        output_dir=main_mod.Path("output"),
        refresh_image_cache=False,
        skip_events=True,
        skip_images=True,
        skip_url_discovery=False,
        no_trails=False,
        alltrails_source="direct_link_batch",
        attraction_source="search",
        restaurant_source="search",
        en_route_source="direct_link_batch",
        run_events=_run_events,
        run_images=_run_images,
        run_urls=_run_urls,
    )

    assert retry_ids == ["taos"]
    assert called["events"] == 0
    assert called["images"] == 0
    assert called["urls"] == 1


def test_build_destination_status_report_scopes_retry_to_image_shortfall_only() -> None:
    """Regression for issue #66: selective retry used to blanket-rerun
    events+images+urls for any flagged destination. A destination whose only
    problem is a missing image shouldn't also trigger a fresh (costly) events
    and URL-discovery pass."""
    trip = {
        "destinations": [
            {"id": "taos", "name": "Taos", "images": [], "cultural_events": {"events": [{"name": "Fiesta"}]}},
        ]
    }
    registry = {
        "entities": [
            {
                "entity_id": "taos:trail:williams-lake-trail",
                "destination_id": "taos",
                "validation_status": "accepted",
                "section_target": "top_attractions",
            },
        ],
        "reports": [
            {"destination_id": "taos", "accepted": ["taos:trail:williams-lake-trail"], "rejected": [], "reassigned": [], "quarantined": []},
        ],
    }

    payload = main_mod._build_destination_status_report(
        trip=trip,
        registry=registry,
        run_id="run-1",
        skip_events=False,
        skip_images=False,
        skip_url_discovery=False,
    )

    row = payload["destinations"][0]
    assert row["retry_triggers"] == ["image_shortfall"]
    assert row["retry_stage_scope"] == {"events": False, "images": True, "urls": False}


def test_build_destination_status_report_scopes_retry_to_cultural_events_section_only() -> None:
    trip = {
        "destinations": [
            {"id": "taos", "name": "Taos", "images": [{"path": "images/taos-1.jpg"}], "cultural_events": {"events": []}},
        ]
    }
    registry = {
        "entities": [
            {
                "entity_id": "taos:event:fiesta",
                "destination_id": "taos",
                "validation_status": "quarantined",
                "section_target": "cultural_events",
            },
        ],
        "reports": [
            {"destination_id": "taos", "accepted": [], "rejected": [], "reassigned": [], "quarantined": ["taos:event:fiesta"]},
        ],
    }

    payload = main_mod._build_destination_status_report(
        trip=trip,
        registry=registry,
        run_id="run-1",
        skip_events=False,
        skip_images=False,
        skip_url_discovery=False,
    )

    row = payload["destinations"][0]
    assert row["retry_stage_scope"] == {"events": True, "images": False, "urls": False}


def test_build_destination_status_report_scopes_retry_to_urls_section_only() -> None:
    trip = {
        "destinations": [
            {"id": "taos", "name": "Taos", "images": [{"path": "images/taos-1.jpg"}], "cultural_events": {"events": [{"name": "Fiesta"}]}},
        ]
    }
    registry = {
        "entities": [
            {
                "entity_id": "taos:trail:williams-lake-trail",
                "destination_id": "taos",
                "validation_status": "quarantined",
                "section_target": "top_attractions",
            },
        ],
        "reports": [
            {"destination_id": "taos", "accepted": [], "rejected": [], "reassigned": [], "quarantined": ["taos:trail:williams-lake-trail"]},
        ],
    }

    payload = main_mod._build_destination_status_report(
        trip=trip,
        registry=registry,
        run_id="run-1",
        skip_events=False,
        skip_images=False,
        skip_url_discovery=False,
    )

    row = payload["destinations"][0]
    assert row["retry_stage_scope"] == {"events": False, "images": False, "urls": True}


def test_selective_retry_narrows_stages_per_destination() -> None:
    """taos only needs an images retry; durango only needs a urls retry --
    events must not run for either, and each stage callback must only see the
    destination that actually needs it."""
    trip = {
        "trip": {"title": "Test"},
        "destinations": [
            {"id": "taos", "name": "Taos"},
            {"id": "durango", "name": "Durango"},
        ],
    }
    status_report = {
        "destinations": [
            {
                "destination_id": "taos",
                "status": "needs_retry",
                "retry_recommended": True,
                "retry_stage_scope": {"events": False, "images": True, "urls": False},
            },
            {
                "destination_id": "durango",
                "status": "needs_retry",
                "retry_recommended": True,
                "retry_stage_scope": {"events": False, "images": False, "urls": True},
            },
        ]
    }

    called = {"events": [], "images": [], "urls": []}

    def _run_events(subset_trip):
        called["events"].append([d["id"] for d in subset_trip["destinations"]])

    def _run_images(subset_trip):
        called["images"].append([d["id"] for d in subset_trip["destinations"]])

    def _run_urls(subset_trip):
        called["urls"].append([d["id"] for d in subset_trip["destinations"]])

    retry_ids = main_mod._selective_retry_destinations(
        trip=trip,
        status_report=status_report,
        config_path="config.yaml",
        llm_client=None,
        output_dir=main_mod.Path("output"),
        refresh_image_cache=False,
        skip_events=False,
        skip_images=False,
        skip_url_discovery=False,
        no_trails=False,
        alltrails_source="direct_link_batch",
        attraction_source="search",
        restaurant_source="search",
        en_route_source="direct_link_batch",
        run_events=_run_events,
        run_images=_run_images,
        run_urls=_run_urls,
    )

    assert retry_ids == ["taos", "durango"]
    assert called["events"] == []
    assert called["images"] == [["taos"]]
    assert called["urls"] == [["durango"]]


def test_build_destination_status_report_adds_threshold_retry_triggers() -> None:
    trip = {
        "destinations": [
            {
                "id": "durango",
                "name": "Durango",
                "images": [{"path": "images/durango-1.jpg"}],
                "cultural_events": {"events": []},
            }
        ]
    }
    registry = {
        "entities": [
            {
                "entity_id": "durango:attraction:mesa-verde",
                "destination_id": "durango",
                "validation_status": "accepted",
            },
            {
                "entity_id": "durango:attraction:animas-river-trail",
                "destination_id": "durango",
                "validation_status": "rejected",
            },
        ],
        "destination_view": {
            "durango": {
                "top_attractions": [
                    "durango:attraction:mesa-verde",
                    "durango:attraction:animas-river-trail",
                ],
                "scenic_drives": [],
                "getting_here.en_route_stops": [],
                "getting_there.route_options": [],
                "dinner_recommendations": [],
                "cultural_events": [],
            }
        },
        "reports": [
            {
                "destination_id": "durango",
                "accepted": ["durango:attraction:mesa-verde"],
                "rejected": [{"entity_id": "durango:attraction:animas-river-trail", "reasons": ["url_rejected"]}],
                "reassigned": [],
                "quarantined": [],
            }
        ],
    }

    payload = main_mod._build_destination_status_report(
        trip=trip,
        registry=registry,
        run_id="run-threshold",
        skip_events=False,
        skip_images=False,
        skip_url_discovery=False,
        retry_policy={
            "min_url_acceptance_ratio": 0.8,
            "min_accepted_by_section": {
                "top_attractions": 2,
            },
        },
    )

    row = payload["destinations"][0]
    assert row["status"] == "needs_retry"
    assert "url_acceptance_ratio_below_threshold" in row["retry_triggers"]
    assert "section_minimum_not_met:top_attractions" in row["retry_triggers"]
    assert row["stage_status"]["url_discovery"]["acceptance_ratio"] == 0.5
    assert row["stage_status"]["url_discovery"]["acceptance_ratio_threshold"] == 0.8
    assert row["section_counts"]["top_attractions"]["total"] == 2
    assert row["section_counts"]["top_attractions"]["accepted"] == 1


def test_build_destination_status_report_flags_rendered_items_missing_links() -> None:
    trip = {
        "destinations": [
            {
                "id": "stg",
                "name": "St. George, Utah",
                "images": [{"path": "images/stg-1.jpg"}],
                "cultural_events": {"events": []},
                "ai_content": {
                    "top_attractions": [{"name": "Snow Canyon State Park", "url": ""}],
                    "dinner_recommendations": [{"name": "Wood Ash Rye", "url": ""}],
                    "getting_here": {"en_route_stops": [{"name": "Pioneer Park", "url": ""}]},
                },
            }
        ]
    }
    registry = {
        "entities": [
            {
                "entity_id": "stg:attraction:snow-canyon",
                "destination_id": "stg",
                "validation_status": "accepted",
            }
        ],
        "destination_view": {
            "stg": {
                "top_attractions": ["stg:attraction:snow-canyon"],
                "scenic_drives": [],
                "getting_here.en_route_stops": [],
                "getting_there.route_options": [],
                "dinner_recommendations": [],
                "cultural_events": [],
            }
        },
        "reports": [
            {
                "destination_id": "stg",
                "accepted": ["stg:attraction:snow-canyon"],
                "rejected": [],
                "reassigned": [],
                "quarantined": [],
            }
        ],
    }

    payload = main_mod._build_destination_status_report(
        trip=trip,
        registry=registry,
        run_id="run-missing-links",
        skip_events=False,
        skip_images=False,
        skip_url_discovery=False,
        retry_policy={},
    )

    row = payload["destinations"][0]
    assert "rendered_items_missing_links" in row["retry_triggers"]
    assert row["stage_status"]["url_discovery"]["rendered_no_url_attractions"] == 1
    assert row["stage_status"]["url_discovery"]["rendered_no_url_restaurants"] == 1
    assert row["stage_status"]["url_discovery"]["rendered_no_url_stops"] == 1


def test_load_destination_retry_policy_reads_thresholds(tmp_path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "destination_retry:",
                "  min_url_acceptance_ratio: 0.55",
                "  min_accepted_by_section:",
                "    top_attractions: 2",
                "    scenic_drives: 1",
                "    unknown_section: 4",
                "  max_retries_per_destination_per_run: 1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    policy = main_mod._load_destination_retry_policy(cfg_path)

    assert policy["min_url_acceptance_ratio"] == 0.55
    assert policy["min_accepted_by_section"]["top_attractions"] == 2
    assert policy["min_accepted_by_section"]["scenic_drives"] == 1
    assert "unknown_section" not in policy["min_accepted_by_section"]
    assert policy["max_retries_per_destination_per_run"] == 1


def test_annotate_retry_outcomes_marks_terminal_status() -> None:
    status_report = {
        "summary": {"destination_count": 3},
        "destinations": [
            {
                "destination_id": "santafe",
                "retry_recommended": False,
                "retry_triggers": [],
            },
            {
                "destination_id": "taos",
                "retry_recommended": True,
                "retry_triggers": ["image_shortfall"],
            },
            {
                "destination_id": "durango",
                "retry_recommended": True,
                "retry_triggers": ["url_collapse"],
            },
        ],
    }

    payload = main_mod._annotate_retry_outcomes(
        status_report=status_report,
        attempted_destination_ids=["taos"],
        max_retries_per_destination_per_run=1,
    )

    by_destination = {row["destination_id"]: row for row in payload["destinations"]}
    assert by_destination["santafe"]["retry_outcome"]["terminal_state"] == "stable_without_retry"
    assert by_destination["taos"]["retry_outcome"]["terminal_state"] == "retry_cap_reached_unresolved"
    assert "retry_cap_reached" in by_destination["taos"]["retry_triggers"]
    assert by_destination["durango"]["retry_outcome"]["terminal_state"] == "not_retried_due_to_cap"

    outcomes = payload["summary"]["retry_outcomes"]
    assert outcomes["max_retries_per_destination_per_run"] == 1
    assert outcomes["attempted_destination_count"] == 1
    assert outcomes["unresolved_after_retry_count"] == 1
    assert outcomes["not_retried_due_to_cap_count"] == 1


def test_destination_ids_needing_attention_returns_unresolved_only() -> None:
    status_report = {
        "destinations": [
            {
                "destination_id": "santafe",
                "retry_outcome": {"terminal_state": "stable_without_retry"},
            },
            {
                "destination_id": "taos",
                "retry_outcome": {"terminal_state": "retry_cap_reached_unresolved"},
            },
            {
                "destination_id": "durango",
                "retry_outcome": {"terminal_state": "not_retried_due_to_cap"},
            },
        ]
    }

    unresolved = main_mod._destination_ids_needing_attention(status_report)

    assert unresolved == ["taos", "durango"]


def test_annotate_retry_outcomes_mixed_destinations_tracks_resolved_and_unresolved() -> None:
    status_report = {
        "summary": {"destination_count": 4},
        "destinations": [
            {
                "destination_id": "santafe",
                "retry_recommended": False,
                "retry_triggers": [],
            },
            {
                "destination_id": "taos",
                "retry_recommended": False,
                "retry_triggers": [],
            },
            {
                "destination_id": "durango",
                "retry_recommended": True,
                "retry_triggers": ["url_collapse"],
            },
            {
                "destination_id": "moab",
                "retry_recommended": True,
                "retry_triggers": ["section_minimum_not_met:top_attractions"],
            },
        ],
    }

    payload = main_mod._annotate_retry_outcomes(
        status_report=status_report,
        attempted_destination_ids=["taos", "durango"],
        max_retries_per_destination_per_run=1,
    )

    by_destination = {row["destination_id"]: row for row in payload["destinations"]}
    assert by_destination["santafe"]["retry_outcome"]["terminal_state"] == "stable_without_retry"
    assert by_destination["taos"]["retry_outcome"]["terminal_state"] == "resolved_after_retry"
    assert by_destination["durango"]["retry_outcome"]["terminal_state"] == "retry_cap_reached_unresolved"
    assert by_destination["moab"]["retry_outcome"]["terminal_state"] == "not_retried_due_to_cap"

    outcomes = payload["summary"]["retry_outcomes"]
    assert outcomes["attempted_destination_count"] == 2
    assert outcomes["resolved_after_retry_count"] == 1
    assert outcomes["unresolved_after_retry_count"] == 1
    assert outcomes["not_retried_due_to_cap_count"] == 1


def test_annotate_retry_outcomes_zero_cap_marks_retry_cap_reached() -> None:
    status_report = {
        "summary": {"destination_count": 2},
        "destinations": [
            {
                "destination_id": "taos",
                "retry_recommended": True,
                "retry_triggers": ["image_shortfall"],
            },
            {
                "destination_id": "durango",
                "retry_recommended": False,
                "retry_triggers": [],
            },
        ],
    }

    payload = main_mod._annotate_retry_outcomes(
        status_report=status_report,
        attempted_destination_ids=[],
        max_retries_per_destination_per_run=0,
    )

    by_destination = {row["destination_id"]: row for row in payload["destinations"]}
    assert by_destination["taos"]["retry_outcome"]["terminal_state"] == "not_retried_due_to_cap"
    assert by_destination["taos"]["retry_outcome"]["attempt_cap"] == 0
    assert "retry_cap_reached" in by_destination["taos"]["retry_triggers"]
    assert by_destination["durango"]["retry_outcome"]["terminal_state"] == "stable_without_retry"

    outcomes = payload["summary"]["retry_outcomes"]
    assert outcomes["max_retries_per_destination_per_run"] == 0
    assert outcomes["attempted_destination_count"] == 0
    assert outcomes["not_retried_due_to_cap_count"] == 1


def test_build_retry_efficiency_metrics_calculates_scope_reduction() -> None:
    metrics = main_mod._build_retry_efficiency_metrics(
        destination_count=5,
        retry_candidate_ids=["a", "b", "c"],
        retried_destination_ids=["b"],
        unresolved_destination_ids=["b"],
        max_retries_per_destination_per_run=1,
    )

    assert metrics["destination_count"] == 5
    assert metrics["retry_candidate_count"] == 3
    assert metrics["retried_destination_count"] == 1
    assert metrics["unresolved_destination_count"] == 1
    assert metrics["retry_scope_ratio"] == 0.2
    assert metrics["retry_scope_reduction_percent"] == 80.0


def test_build_retry_efficiency_metrics_handles_zero_destinations() -> None:
    metrics = main_mod._build_retry_efficiency_metrics(
        destination_count=0,
        retry_candidate_ids=[],
        retried_destination_ids=[],
        unresolved_destination_ids=[],
        max_retries_per_destination_per_run=0,
    )

    assert metrics["destination_count"] == 0
    assert metrics["retry_scope_ratio"] == 0.0
    assert metrics["retry_scope_reduction_percent"] == 0.0
    assert metrics["max_retries_per_destination_per_run"] == 0


def test_counter_delta_handles_missing_keys() -> None:
    before = {"a": 2, "b": 3}
    after = {"a": 5, "c": 4}

    delta = main_mod._counter_delta(before, after)

    assert delta["a"] == 3
    assert delta["b"] == 0
    assert delta["c"] == 4


def test_build_gate_a_metrics_includes_stage_calls_cost_and_batch_ratio() -> None:
    trip = {
        "destinations": [
            {
                "id": "zion",
                "name": "Zion National Park",
                "nps_park_code": "zion",
                "scenic_drives": [{"title": "Zion Canyon Scenic Drive"}],
                "ai_content": {
                    "top_attractions": [{"name": "Angels Landing"}, {"name": "The Narrows"}],
                    "dinner_recommendations": [{"name": "Bit & Spur"}],
                    "getting_here": {"en_route_stops": [{"name": "Kolob Canyons"}]},
                    "getting_there": {"route_options": [{"title": "US-89"}]},
                },
            }
        ]
    }
    usage_summary = {
        "total_estimated_cost_usd": 1.75,
        "records": [
            {"operation": "destination_content:zion", "estimated_cost_usd": 0.4},
            {"operation": "what_to_know:zion", "estimated_cost_usd": 0.3},
            {"operation": "scenic_drives:zion", "estimated_cost_usd": 0.2},
            {"operation": "cultural_events:search", "estimated_cost_usd": 0.15},
            {"operation": "cultural_events:zion", "estimated_cost_usd": 0.25},
            {"operation": "url_discovery:search", "estimated_cost_usd": 0.45},
        ],
    }
    stage_timings = {
        "stage_3_ai_generation": 60.0,
        "stage_4_5_parallel": 120.0,
    }

    metrics = main_mod._build_gate_a_metrics(
        trip=trip,
        usage_summary=usage_summary,
        stage_timings=stage_timings,
        skip_events=False,
        skip_images=False,
        skip_url_discovery=False,
        image_counter_delta={
            "nps_api_calls": 1,
            "unsplash_api_calls": 1,
            "wikimedia_api_calls": 2,
            "image_download_requests": 3,
            "cache_hits": 0,
        },
        url_validator_counter_delta={
            "head_requests": 8,
            "get_requests": 2,
            "get_text_requests": 5,
        },
    )

    assert metrics["measurement_coverage"]["stage_cost_attribution"] is True
    assert metrics["provider_calls_by_stage"]["stage_3_ai_generation"]["llm_generate_json_calls"] == 3
    assert metrics["provider_calls_by_stage"]["stage_4_5_parallel"]["url_discovery_search_calls"] == 1
    assert metrics["provider_calls_by_stage"]["stage_4_5_parallel"]["image_provider_calls"]["wikimedia_api_calls"] == 2
    assert metrics["stage_cost_usd"]["stage_3_ai_generation"] == 0.9
    assert metrics["stage_cost_usd"]["stage_4_5_parallel"] == 0.85
    assert metrics["throughput_entities_per_minute"]["stage_3_destinations_per_minute"] == 1.0
    assert metrics["batch_work_ratio"]["ai_generation"]["naive_calls"] == 3
    assert metrics["batch_work_ratio"]["ai_generation"]["actual_calls"] == 3


def test_build_gate_a_metrics_attributes_current_operation_names() -> None:
    """Regression for a real cost-accounting bug: ai_content.py was refactored
    to emit 'destination_bundle:{id}' (dest_content + what_to_know merged into
    one call) instead of the old 'destination_content:'/'what_to_know:' pair,
    and url_discovery.py's direct-batch HTML harvest calls emit
    'url_discovery:chat_completion' -- but the stage-attribution filter was
    never updated for either, so on a real run ~30% of total cost (every
    destination bundle call, plus every harvest call) was silently excluded
    from stage_cost_usd and the batch-ratio metrics entirely."""
    trip = {"destinations": [{"id": "zion", "name": "Zion National Park"}]}
    usage_summary = {
        "total_estimated_cost_usd": 0.9,
        "records": [
            {"operation": "destination_bundle:zion", "estimated_cost_usd": 0.5},
            {"operation": "url_discovery:chat_completion", "estimated_cost_usd": 0.4},
        ],
    }
    stage_timings = {"stage_3_ai_generation": 30.0, "stage_4_5_parallel": 60.0}

    metrics = main_mod._build_gate_a_metrics(
        trip=trip,
        usage_summary=usage_summary,
        stage_timings=stage_timings,
        skip_events=True,
        skip_images=True,
        skip_url_discovery=False,
        image_counter_delta={},
        url_validator_counter_delta={},
    )

    assert metrics["provider_calls_by_stage"]["stage_3_ai_generation"]["llm_generate_json_calls"] == 1
    assert metrics["provider_calls_by_stage"]["stage_4_5_parallel"]["url_discovery_search_calls"] == 1
    assert metrics["stage_cost_usd"]["stage_3_ai_generation"] == 0.5
    assert metrics["stage_cost_usd"]["stage_4_5_parallel"] == 0.4


def test_build_gate_a_metrics_attributes_fallback_client_operation_prefix() -> None:
    """Regression for the 2026-08-15 finding: url_discovery.py's fallback
    client (self._search_fallback) uses its own "url_discovery_fallback:*"
    operation prefix, distinct from the primary batch client's
    "url_discovery:*" -- but the stage-attribution filter only recognized
    the primary's prefix, so every fallback call (both the non-batch
    .search() path and the cross-provider batch retry) was silently
    excluded from stage_cost_usd and url_discovery_search_calls, making a
    run that leaned heavily on the fallback (e.g. during a primary-provider
    outage) look far cheaper and less active than it actually was."""
    trip = {"destinations": [{"id": "zion", "name": "Zion National Park"}]}
    usage_summary = {
        "total_estimated_cost_usd": 0.3,
        "records": [
            {"operation": "url_discovery:chat_completion", "estimated_cost_usd": 0.1},
            {"operation": "url_discovery_fallback:search", "estimated_cost_usd": 0.08},
            {"operation": "url_discovery_fallback:chat_completion_search", "estimated_cost_usd": 0.12},
        ],
    }
    stage_timings = {"stage_3_ai_generation": 30.0, "stage_4_5_parallel": 60.0}

    metrics = main_mod._build_gate_a_metrics(
        trip=trip,
        usage_summary=usage_summary,
        stage_timings=stage_timings,
        skip_events=True,
        skip_images=True,
        skip_url_discovery=False,
        image_counter_delta={},
        url_validator_counter_delta={},
    )

    assert metrics["provider_calls_by_stage"]["stage_4_5_parallel"]["url_discovery_search_calls"] == 3
    assert metrics["stage_cost_usd"]["stage_4_5_parallel"] == 0.3


def test_direct_batch_parity_summary_identifies_missing_destination_by_name(tmp_path) -> None:
    """Full-pipeline proof of the output-parity contract: given captured direct-batch
    HTML inputs and a final assembled HTML, the parity summary must report which
    destination(s) lost a captured URL on the way to final output, not just a bare
    count. Zion's captured URL survives into final output; Moab's does not."""
    capture_dir = tmp_path / "dev" / "url_discovery_direct_batch_html"
    capture_dir.mkdir(parents=True)

    (capture_dir / "zion.html").write_text(
        '<a href="https://zionnps.example/angels-landing">Angels Landing</a>', encoding="utf-8"
    )
    (capture_dir / "zion.meta.json").write_text(
        json.dumps({"destination": "Zion National Park"}), encoding="utf-8"
    )

    (capture_dir / "moab.html").write_text(
        '<a href="https://moab.example/arches">Arches</a>', encoding="utf-8"
    )
    (capture_dir / "moab.meta.json").write_text(
        json.dumps({"destination": "Moab"}), encoding="utf-8"
    )

    (tmp_path / "index.html").write_text(
        '<a href="https://zionnps.example/angels-landing">Angels Landing</a>', encoding="utf-8"
    )

    summary = main_mod._latest_direct_batch_parity_summary(output_dir=tmp_path)

    assert summary["captured_html_input_count"] == 2
    assert summary["unique_captured_urls"] == 2
    assert summary["unique_final_html_urls"] == 1
    assert summary["destinations_missing_at_least_one_captured_url"] == 1
    assert summary["destinations_missing_captured_url_names"] == ["Moab"]


def test_write_direct_batch_parity_report_persists_summary_to_disk(tmp_path) -> None:
    """The parity contract must be a durable validation-step artifact, not only a
    console log line, so it can be inspected or diffed after the run finishes."""
    parity_summary = {
        "captured_html_input_count": 2,
        "unique_captured_urls": 2,
        "unique_final_html_urls": 1,
        "destinations_missing_at_least_one_captured_url": 1,
        "destinations_missing_captured_url_names": ["Moab"],
    }

    report_path = main_mod._write_direct_batch_parity_report(
        output_dir=tmp_path,
        parity_summary=parity_summary,
        run_id="run-xyz",
    )

    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-xyz"
    assert payload["destinations_missing_captured_url_names"] == ["Moab"]


def test_apply_privacy_redaction_empties_transportation_legs() -> None:
    """Dropped wholesale rather than field-by-field: there is no routing or
    scheduling consumer to keep alive, and carrier + record locator is enough
    to view or change someone else's booking."""
    trip = {
        "destinations": [
            {
                "id": "vegas",
                "transportation": [
                    {"type": "plane", "provider": "United", "confirmation_number": "XR7Q2M"},
                    {"type": "car", "provider": "Hertz", "confirmation_number": "H99120"},
                ],
            },
            {"id": "zion"},
        ]
    }

    counts = main_mod._apply_privacy_redaction(trip)

    assert counts["transportation"] == 2
    assert trip["destinations"][0]["transportation"] == []
    assert trip["destinations"][1].get("transportation", []) == []


class TestCulturalEventsOffByDefault:
    """Cultural events are the worst value-per-token category in the pipeline.

    Measured across three cold-start runs on 2026-08-22: 16 calls and
    97K-113K tokens to deliver between 1 and 4 event listings -- 24,000 to
    113,000 tokens per delivered item, and more tokens than generating the
    entire itinerary for all ten destinations (10 calls, 68K). It is also the
    content most likely to be stale by departure.
    """

    def test_config_without_the_key_means_off(self, tmp_path):
        from generator.main import _cultural_events_enabled
        cfg = tmp_path / "c.yaml"
        cfg.write_text("cultural_events:\n  max_results: 8\n", encoding="utf-8")
        assert _cultural_events_enabled(str(cfg)) is False

    def test_explicit_true_turns_it_on(self, tmp_path):
        from generator.main import _cultural_events_enabled
        cfg = tmp_path / "c.yaml"
        cfg.write_text("cultural_events:\n  enabled: true\n", encoding="utf-8")
        assert _cultural_events_enabled(str(cfg)) is True

    def test_unreadable_config_fails_closed(self, tmp_path):
        """An unreadable config must not silently buy the most expensive
        category in the pipeline."""
        from generator.main import _cultural_events_enabled
        assert _cultural_events_enabled(str(tmp_path / "does-not-exist.yaml")) is False
        bad = tmp_path / "bad.yaml"
        bad.write_text("cultural_events: [this is not a mapping\n", encoding="utf-8")
        assert _cultural_events_enabled(str(bad)) is False

    def test_shipped_config_has_it_off(self):
        """The default this project ships with."""
        from generator.main import _cultural_events_enabled
        assert _cultural_events_enabled("config.yaml") is False


class TestOptionalCategorySwitches:
    """Three priced enrichments, each independently switchable.

    Measured 2026-08-22: trails were 124 of 246 paid fallback calls;
    en-route stops were 253 of 301 batch candidate rejections before moving
    to Maps links; cultural events spent 16 calls and 97K-113K tokens for 1-4
    delivered items. None of them is the core product -- the itinerary,
    schedule, lodging and drives are unaffected by all three.
    """

    def test_shipped_config_matches_the_intended_switch_state(self):
        """Guards against a priced category being switched on by accident.

        All three stay off globally. Trails were wanted back for the
        Southwest trip (2026-08-29) but that answer belongs in its manifest,
        not here -- flipping the global switch would also have bought trails
        for Europe, whose manifest asks for no hikes. See
        TestPerTripCategorySwitches.
        """
        from generator.main import _category_enabled
        for section in ("trails", "en_route_stops", "cultural_events"):
            assert _category_enabled("config.yaml", section) is False, section

    def test_each_switch_is_independent(self, tmp_path):
        from generator.main import _category_enabled
        cfg = tmp_path / "c.yaml"
        cfg.write_text(
            "trails:\n  enabled: true\n"
            "en_route_stops:\n  enabled: false\n"
            "cultural_events:\n  enabled: true\n",
            encoding="utf-8",
        )
        assert _category_enabled(str(cfg), "trails") is True
        assert _category_enabled(str(cfg), "en_route_stops") is False
        assert _category_enabled(str(cfg), "cultural_events") is True

    def test_missing_section_means_off(self, tmp_path):
        from generator.main import _category_enabled
        cfg = tmp_path / "c.yaml"
        cfg.write_text("url_discovery:\n  search_model: grok-4.3\n", encoding="utf-8")
        for section in ("trails", "en_route_stops", "cultural_events"):
            assert _category_enabled(str(cfg), section) is False, section

    def test_unreadable_config_fails_closed(self, tmp_path):
        """A config this cannot read must not silently buy a priced category."""
        from generator.main import _category_enabled
        assert _category_enabled(str(tmp_path / "nope.yaml"), "trails") is False


class TestEnRouteStopsCanBeSwitchedOffEntirely:
    """en_route_stops.enabled=false suppresses discovery AND rendering.

    Distinct from the group-level deferral, which moves stops to a base
    destination rather than removing them.
    """

    def test_disabled_discoverer_empties_the_section(self):
        from unittest.mock import patch
        from generator.url_discovery import URLDiscoverer
        mock_llm = type("M", (), {"provider": "grok", "model": "grok-4.5", "usage_tracker": None})()
        with patch("generator.search_provider.GrokSearch"), patch("generator.search_provider.ClaudeSearch"):
            disc = URLDiscoverer(config_path="config.yaml", llm_client=mock_llm, disable_en_route=True)
        ai = {"getting_here": {"en_route_stops": [{"name": "Some Pullout"}, {"name": "Another"}]}}
        disc._discover_en_route_stops(ai, "Moab", "October 22-24, 2026")
        assert ai["getting_here"]["en_route_stops"] == []

    def test_enabled_discoverer_leaves_the_section_alone(self):
        from unittest.mock import patch
        from generator.url_discovery import URLDiscoverer
        mock_llm = type("M", (), {"provider": "grok", "model": "grok-4.5", "usage_tracker": None})()
        with patch("generator.search_provider.GrokSearch"), patch("generator.search_provider.ClaudeSearch"):
            disc = URLDiscoverer(config_path="config.yaml", llm_client=mock_llm, disable_en_route=False)
        assert disc._disable_en_route is False


class TestPerTripCategorySwitches:
    """A priced category can be answered by the trip, not just globally.

    A single config flag meant enabling trails for the Southwest trip also
    bought them for Europe, whose manifest asks for no hikes. Precedence is
    CLI, then manifest, then config: the run-specific answer wins, the trip's
    own answer is sticky across runs, and config remains the default.
    """

    def _manifest(self, tmp_path, body):
        p = tmp_path / "m.yaml"
        p.write_text(body, encoding="utf-8")
        return str(p)

    def test_manifest_can_enable_a_category_config_leaves_off(self, tmp_path):
        from generator.main import _resolve_category
        cfg = tmp_path / "c.yaml"
        cfg.write_text("trails:\n  enabled: false\n", encoding="utf-8")
        m = self._manifest(tmp_path, "categories:\n  trails: true\n")
        assert _resolve_category(None, str(cfg), "trails", manifest_path=m) is True

    def test_manifest_can_disable_a_category_config_leaves_on(self, tmp_path):
        from generator.main import _resolve_category
        cfg = tmp_path / "c.yaml"
        cfg.write_text("trails:\n  enabled: true\n", encoding="utf-8")
        m = self._manifest(tmp_path, "categories:\n  trails: false\n")
        assert _resolve_category(None, str(cfg), "trails", manifest_path=m) is False

    def test_cli_still_beats_the_manifest(self, tmp_path):
        from generator.main import _resolve_category
        cfg = tmp_path / "c.yaml"
        cfg.write_text("trails:\n  enabled: false\n", encoding="utf-8")
        m = self._manifest(tmp_path, "categories:\n  trails: true\n")
        assert _resolve_category(False, str(cfg), "trails", manifest_path=m) is False

    def test_silent_manifest_falls_through_to_config(self, tmp_path):
        from generator.main import _resolve_category
        cfg = tmp_path / "c.yaml"
        cfg.write_text("trails:\n  enabled: true\n", encoding="utf-8")
        m = self._manifest(tmp_path, "trip:\n  title: No categories here\n")
        assert _resolve_category(None, str(cfg), "trails", manifest_path=m) is True

    def test_nested_under_trip_is_accepted(self, tmp_path):
        from generator.main import _resolve_category
        cfg = tmp_path / "c.yaml"
        cfg.write_text("trails:\n  enabled: false\n", encoding="utf-8")
        m = self._manifest(tmp_path, "trip:\n  categories:\n    trails: true\n")
        assert _resolve_category(None, str(cfg), "trails", manifest_path=m) is True

    def test_enabled_subkey_form_is_accepted(self, tmp_path):
        from generator.main import _resolve_category
        cfg = tmp_path / "c.yaml"
        cfg.write_text("trails:\n  enabled: false\n", encoding="utf-8")
        m = self._manifest(tmp_path, "categories:\n  trails:\n    enabled: true\n")
        assert _resolve_category(None, str(cfg), "trails", manifest_path=m) is True

    def test_unreadable_manifest_does_not_raise(self, tmp_path):
        from generator.main import _manifest_category_override
        assert _manifest_category_override(str(tmp_path / "missing.yaml"), "trails") is None

    def test_malformed_manifest_does_not_raise(self, tmp_path):
        from generator.main import _manifest_category_override
        m = self._manifest(tmp_path, "categories: [this, is, not, a, mapping\n")
        assert _manifest_category_override(m, "trails") is None

    def test_non_boolean_value_is_ignored(self, tmp_path):
        from generator.main import _manifest_category_override
        m = self._manifest(tmp_path, "categories:\n  trails: maybe\n")
        assert _manifest_category_override(m, "trails") is None


# ── GH #2 Phase 1: transit options in the pipeline ────────────────────────

def test_generated_transit_options_are_not_redacted() -> None:
    """Asserted explicitly so a later reader does not "fix" the asymmetry
    with booked legs. A booked leg carries a record locator that is enough to
    change someone else's reservation; a generated option is a guess about a
    bus, with no personal data in it at all."""
    import generator.main as main_mod

    trip = {
        "trip": {},
        "destinations": [{
            "id": "bryce",
            "name": "Bryce Canyon",
            "planning_links": [{"label": "Plans", "url": "https://example.com"}],
            "transportation": [{"type": "train", "confirmation_number": "XR7Q2M"}],
            "ai_content": {"getting_here": {"transit_options": {
                "has_transit": True,
                "options": [{"label": "Regional bus via Panguitch"}],
            }}},
        }],
    }

    main_mod._apply_privacy_redaction(trip)

    gh = trip["destinations"][0]["ai_content"]["getting_here"]
    assert gh["transit_options"]["options"][0]["label"] == "Regional bus via Panguitch"
    # The booked leg beside it still goes.
    assert trip["destinations"][0]["transportation"] == []


def test_transit_routing_spend_is_attributed_to_a_stage() -> None:
    """design.md 4.4 records two incidents of spend silently excluded from
    stage attribution because its operation prefix matched no branch."""
    import generator.main as main_mod

    metrics = main_mod._build_gate_a_metrics(
        trip={"trip": {}, "destinations": [{"id": "zion", "name": "Zion"}]},
        usage_summary={"records": [
            {"operation": "transit_routing:Bryce Canyon", "estimated_cost_usd": 0.12},
        ], "total_estimated_cost_usd": 0.12},
        stage_timings={"stage_3_ai_generation": 60.0, "stage_4_5_parallel": 60.0},
        skip_events=True,
        skip_images=True,
        skip_url_discovery=True,
        image_counter_delta={},
        url_validator_counter_delta={},
    )

    assert metrics["stage_cost_usd"]["stage_3_ai_generation"] == 0.12


def test_transit_routing_only_pays_for_legs_the_manifest_asked_about() -> None:
    import generator.main as main_mod
    from generator.transit_routing import stamp_resolved_modes

    calls = []

    class _Provider:
        def generate_transit_options(self, origin, dest, trip_meta):
            calls.append((origin.get("id"), dest.get("id")))
            return {"has_transit": False, "honest_assessment": "None."}

    trip = {
        "trip": {},
        "destinations": [
            {"id": "zion", "name": "Zion", "ai_content": {}},
            {"id": "bryce", "name": "Bryce", "transport_mode": "transit", "ai_content": {}},
            {"id": "moab", "name": "Moab", "ai_content": {}},
        ],
    }
    stamp_resolved_modes(trip)

    import generator.transit_routing as tr
    original = tr.build_transit_provider
    tr.build_transit_provider = lambda *a, **k: _Provider()
    try:
        updated = main_mod._apply_transit_routing(trip)
    finally:
        tr.build_transit_provider = original

    assert updated == 1
    assert calls == [("zion", "bryce")]
    assert "transit_options" in trip["destinations"][1]["ai_content"]["getting_here"]
    assert "getting_here" not in trip["destinations"][2].get("ai_content", {})


def test_a_booked_leg_costs_no_transit_call() -> None:
    """multimodal-routing.md 4.6: the cheapest branch in the design."""
    import generator.main as main_mod
    import generator.transit_routing as tr
    from generator.transit_routing import stamp_resolved_modes

    calls = []

    class _Provider:
        def generate_transit_options(self, origin, dest, trip_meta):
            calls.append(dest.get("id"))
            return {}

    trip = {
        "trip": {"transport_mode": "transit"},
        "destinations": [
            {"id": "tokyo", "name": "Tokyo", "ai_content": {}},
            {"id": "kyoto", "name": "Kyoto", "ai_content": {},
             "transportation": [{"type": "train", "confirmation_number": "XR7Q2M"}]},
        ],
    }
    stamp_resolved_modes(trip)

    original = tr.build_transit_provider
    tr.build_transit_provider = lambda *a, **k: _Provider()
    try:
        assert main_mod._apply_transit_routing(trip) == 0
    finally:
        tr.build_transit_provider = original
    assert calls == []


def test_transit_routing_kill_switch_skips_the_stage(tmp_path) -> None:
    import generator.main as main_mod
    from generator.transit_routing import stamp_resolved_modes

    cfg = tmp_path / "config.yaml"
    cfg.write_text("transit_routing:\n  enabled: false\n", encoding="utf-8")
    trip = {
        "trip": {"transport_mode": "transit"},
        "destinations": [
            {"id": "a", "name": "A", "ai_content": {}},
            {"id": "b", "name": "B", "ai_content": {}},
        ],
    }
    stamp_resolved_modes(trip)

    assert main_mod._apply_transit_routing(trip, config_path=str(cfg)) == 0


# ── GH #2: no driving figure survives on a leg that is not a drive ────────

def _transit_trip(travel_time="2 hrs 15 min", distance="95", transit_options=None,
                  duration_is_estimate=False):
    from generator.transit_routing import stamp_resolved_modes

    gh = {"travel_time": travel_time, "distance_miles": distance,
          "route_summary": "US-89 to UT-12."}
    if transit_options is not None:
        gh["transit_options"] = transit_options
    if duration_is_estimate:
        gh["duration_is_estimate"] = True
    trip = {
        "trip": {},
        "destinations": [
            {"id": "bryce", "name": "Bryce Canyon", "ai_content": {}},
            {"id": "capitol_reef", "name": "Capitol Reef", "transport_mode": "transit",
             "ai_content": {"getting_here": gh}},
        ],
    }
    stamp_resolved_modes(trip)
    return trip


_BAND = {
    "has_transit": True,
    "confidence": "unverified",
    "options": [{"label": "Regional bus via Panguitch", "duration": "3-4 hours", "transfers": 1}],
}


def test_transit_leg_takes_its_duration_from_the_suggested_band() -> None:
    """The card and the schedule must agree. Left alone, this leg showed a
    3-4 hour bus in one card and scheduled a 2h15 drive in the next."""
    import generator.main as main_mod

    trip = _transit_trip(transit_options=_BAND)
    assert main_mod._enforce_transit_leg_durations(trip) == 1

    gh = trip["destinations"][1]["ai_content"]["getting_here"]
    assert gh["travel_time"] == "3-4 hours"
    assert gh["distance_miles"] == ""
    assert gh["duration_is_estimate"] is True
    assert gh["travel_mode"] == "transit"


def test_the_schedule_reads_the_band_the_card_shows() -> None:
    """The point of the whole fix: one number, both places. The normalizer's
    existing range handling takes the midpoint of 3-4 hours."""
    import generator.main as main_mod
    from generator.ai_content import AIContentGenerator

    trip = _transit_trip(transit_options=_BAND)
    main_mod._enforce_transit_leg_durations(trip)
    gh = trip["destinations"][1]["ai_content"]["getting_here"]

    assert AIContentGenerator._parse_duration_minutes(gh["travel_time"]) == 210


def test_a_transit_leg_with_no_band_is_left_blank_not_guessed() -> None:
    """An unstated arrival is a gap; an invented one is a wrong answer the
    reader cannot see is wrong."""
    import generator.main as main_mod

    trip = _transit_trip(transit_options={"has_transit": False, "honest_assessment": "None."})
    assert main_mod._enforce_transit_leg_durations(trip) == 1

    gh = trip["destinations"][1]["ai_content"]["getting_here"]
    assert gh["travel_time"] == ""
    assert gh["distance_miles"] == ""


def test_a_real_routes_estimate_outranks_the_band() -> None:
    """A priced figure from the Routes API beats a model's guess at a range,
    and must not be overwritten by it."""
    import generator.main as main_mod

    trip = _transit_trip(travel_time="2 hrs 16 min", distance="187",
                         transit_options=_BAND, duration_is_estimate=True)
    assert main_mod._enforce_transit_leg_durations(trip) == 0

    gh = trip["destinations"][1]["ai_content"]["getting_here"]
    assert gh["travel_time"] == "2 hrs 16 min"
    assert gh["distance_miles"] == "187"


def test_a_driving_leg_is_untouched() -> None:
    import generator.main as main_mod
    from generator.transit_routing import stamp_resolved_modes

    trip = {
        "trip": {},
        "destinations": [
            {"id": "zion", "name": "Zion", "ai_content": {}},
            {"id": "bryce", "name": "Bryce", "ai_content": {"getting_here": {
                "travel_time": "2 hrs 15 min", "distance_miles": "95"}}},
        ],
    }
    stamp_resolved_modes(trip)
    assert main_mod._enforce_transit_leg_durations(trip) == 0
    gh = trip["destinations"][1]["ai_content"]["getting_here"]
    assert gh["travel_time"] == "2 hrs 15 min"
    assert gh["distance_miles"] == "95"


def test_a_mixed_leg_keeps_its_car_estimate() -> None:
    """Under `mixed` the drive is still the answer and the transit options
    sit beside it -- so the drive figures stay."""
    import generator.main as main_mod
    from generator.transit_routing import stamp_resolved_modes

    trip = {
        "trip": {},
        "destinations": [
            {"id": "zion", "name": "Zion", "ai_content": {}},
            {"id": "bryce", "name": "Bryce", "transport_mode": "mixed",
             "ai_content": {"getting_here": {
                 "travel_time": "2 hrs 15 min", "distance_miles": "95",
                 "transit_options": _BAND}}},
        ],
    }
    stamp_resolved_modes(trip)
    assert main_mod._enforce_transit_leg_durations(trip) == 0
    gh = trip["destinations"][1]["ai_content"]["getting_here"]
    assert gh["travel_time"] == "2 hrs 15 min"
    assert gh["distance_miles"] == "95"


# ── GH #2: the retry pass must not re-price legs the first pass priced ────

class _CountingEstimator:
    """Mimics TransitEstimator's memo, which is the thing under test."""

    available = True

    def __init__(self, result=None):
        self.result = result
        self.call_count = 0
        self.modes_seen = []
        self._memo = {}

    def estimate(self, origin, destination, *, departure_iso="", travel_mode="TRANSIT"):
        # Mode is part of the real memo key: the same endpoints have a
        # different answer by bike than by train.
        key = (origin, destination, departure_iso, travel_mode)
        self.modes_seen.append(travel_mode)
        if key in self._memo:
            return self._memo[key]
        self.call_count += 1
        self._memo[key] = self.result
        return self.result


def _two_transit_legs():
    from generator.transit_routing import stamp_resolved_modes

    trip = {
        "trip": {"transport_mode": "transit"},
        "destinations": [
            {"id": "kyoto", "name": "Kyoto", "ai_content": {"getting_here": {}}},
            {"id": "kanazawa", "name": "Kanazawa", "ai_content": {"getting_here": {}}},
            {"id": "tokyo", "name": "Tokyo", "ai_content": {"getting_here": {}}},
        ],
    }
    stamp_resolved_modes(trip)
    return trip


def test_a_shared_estimator_prices_each_leg_once_across_both_passes():
    """main calls _apply_transit_estimates a second time after a selective
    retry. With a fresh estimator per pass that doubled the Routes API bill;
    the Japan run made 8 calls for 4 legs, every one of them empty."""
    import generator.main as main_mod

    trip = _two_transit_legs()
    estimator = _CountingEstimator(result={"minutes": 136, "miles": 133, "estimated": True})

    main_mod._apply_transit_estimates(trip, estimator=estimator)
    after_first = estimator.call_count
    main_mod._apply_transit_estimates(trip, estimator=estimator)

    assert after_first == 2          # kyoto->kanazawa, kanazawa->tokyo
    assert estimator.call_count == 2  # and not 4


def test_an_uncovered_corridor_is_only_discovered_once():
    """The expensive case: every call returns nothing, so without the shared
    memo the run pays twice to learn the same negative."""
    import generator.main as main_mod

    trip = _two_transit_legs()
    estimator = _CountingEstimator(result=None)

    main_mod._apply_transit_estimates(trip, estimator=estimator)
    main_mod._apply_transit_estimates(trip, estimator=estimator)

    assert estimator.call_count == 2


def test_without_a_shared_estimator_it_still_builds_its_own():
    """Callers that pass nothing keep working -- the parameter is an
    optimisation, not a new requirement."""
    import generator.main as main_mod

    trip = _two_transit_legs()
    # No GOOGLE_MAPS_PLATFORM_KEY in the test environment, so this takes the
    # unavailable path and returns 0 rather than reaching the network.
    assert main_mod._apply_transit_estimates(trip) == 0


# ── GH #2: self-powered legs are priced, never guessed ────────────────────

def _self_powered_trip(mode, travel_time="2 hrs 15 min", distance="95"):
    from generator.transit_routing import stamp_resolved_modes

    trip = {
        "trip": {},
        "destinations": [
            {"id": "zion", "name": "Zion", "ai_content": {}},
            {"id": "bryce", "name": "Bryce", "transport_mode": mode,
             "ai_content": {"getting_here": {
                 "travel_time": travel_time, "distance_miles": distance}}},
        ],
    }
    stamp_resolved_modes(trip)
    return trip


@pytest.mark.parametrize("mode, expected", [("bike", "BICYCLE"), ("hike", "WALK")])
def test_a_self_powered_leg_is_priced_by_its_own_travel_mode(mode, expected):
    import generator.main as main_mod

    trip = _self_powered_trip(mode)
    estimator = _CountingEstimator(result={"minutes": 300, "miles": 62, "estimated": True})

    assert main_mod._apply_transit_estimates(trip, estimator=estimator) == 1
    assert estimator.modes_seen == [expected]

    gh = trip["destinations"][1]["ai_content"]["getting_here"]
    assert gh["travel_time"] == "5 hrs"
    assert gh["distance_miles"] == 62
    assert gh["travel_mode"] == mode


@pytest.mark.parametrize("mode", ["bike", "hike"])
def test_an_unpriced_self_powered_leg_is_blank_not_a_drive(mode):
    """No options card exists for a leg nobody operates, so the Routes figure
    is the only honest source. Without it, nothing."""
    import generator.main as main_mod

    trip = _self_powered_trip(mode)
    assert main_mod._enforce_transit_leg_durations(trip) == 1

    gh = trip["destinations"][1]["ai_content"]["getting_here"]
    assert gh["travel_time"] == ""
    assert gh["distance_miles"] == ""
    assert gh["travel_mode"] == mode


@pytest.mark.parametrize("mode", ["bike", "hike"])
def test_a_priced_self_powered_leg_is_left_alone(mode):
    import generator.main as main_mod

    trip = _self_powered_trip(mode, travel_time="5 hrs", distance="62")
    trip["destinations"][1]["ai_content"]["getting_here"]["duration_is_estimate"] = True

    assert main_mod._enforce_transit_leg_durations(trip) == 0
    gh = trip["destinations"][1]["ai_content"]["getting_here"]
    assert gh["travel_time"] == "5 hrs"
    assert gh["distance_miles"] == "62"


def test_a_multi_day_walk_is_scheduled_in_days_not_walking_hours():
    """The PCT run's headline defect: Google's continuous WALK duration
    rendered as the leg duration for a leg spanning a week."""
    import generator.main as main_mod
    from generator.transit_routing import stamp_resolved_modes

    trip = {
        "trip": {"transport_mode": "hike", "default_daily_activity_hours": 8,
                 "departure": "Cascade Locks, Oregon"},
        "destinations": [
            {"id": "cascade_locks", "name": "Cascade Locks", "ai_content": {}},
            {"id": "trout_lake", "name": "Trout Lake", "ai_content": {}},
        ],
    }
    stamp_resolved_modes(trip)
    estimator = _CountingEstimator(result={"minutes": 2755, "miles": 123, "estimated": True})

    main_mod._apply_transit_estimates(
        trip, departure_hint="Cascade Locks, Oregon", estimator=estimator
    )

    gh = trip["destinations"][1]["ai_content"]["getting_here"]
    assert gh["travel_time"] == "about 6 days"
    assert "hrs" not in gh["travel_time"]


def test_a_transit_leg_still_reads_in_hours():
    """Only the self-powered modes convert. A train really does take 2 hrs
    16 min, and rendering that as a fraction of a day would be absurd."""
    import generator.main as main_mod
    from generator.transit_routing import stamp_resolved_modes

    trip = {
        "trip": {"transport_mode": "transit", "default_daily_activity_hours": 8,
                 "departure": "Tokyo"},
        "destinations": [
            {"id": "tokyo", "name": "Tokyo", "ai_content": {}},
            {"id": "kyoto", "name": "Kyoto", "ai_content": {}},
        ],
    }
    stamp_resolved_modes(trip)
    estimator = _CountingEstimator(result={"minutes": 136, "miles": 133, "estimated": True})

    main_mod._apply_transit_estimates(trip, departure_hint="Tokyo", estimator=estimator)

    gh = trip["destinations"][1]["ai_content"]["getting_here"]
    assert gh["travel_time"] == "2 hrs 16 min"
