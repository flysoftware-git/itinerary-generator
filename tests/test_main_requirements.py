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
