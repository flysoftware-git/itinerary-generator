import json

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
        "alltrails_apify_actor_id",
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
        "apify-single-call",
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
        alltrails_apify_actor_id=None,
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
        alltrails_apify_actor_id=None,
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
        alltrails_apify_actor_id=None,
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
