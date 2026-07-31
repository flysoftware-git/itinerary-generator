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
        "noschedule",
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


def test_build_destination_status_report_marks_quarantine_and_retry_triggers() -> None:
    trip = {
        "destinations": [
            {
                "id": "santafe",
                "name": "Santa Fe",
                "images": [{"path": "images/santafe-1.jpg"}],
                "cultural_events": {"events": [{"name": "Spanish Market"}]},
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

    assert by_destination["taos"]["status"] == "quarantined"
    assert by_destination["taos"]["retry_recommended"] is True
    assert "registry_quarantined_entities" in by_destination["taos"]["retry_triggers"]
    assert "image_shortfall" in by_destination["taos"]["retry_triggers"]
    assert by_destination["taos"]["stage_status"]["images"]["status"] == "shortfall"
    assert by_destination["taos"]["rejected_reasons"] == ["entity_removed"]


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
        run_events=_run_events,
        run_images=_run_images,
        run_urls=_run_urls,
    )

    assert retry_ids == ["taos"]
    assert called["events"] == 0
    assert called["images"] == 0
    assert called["urls"] == 1


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
