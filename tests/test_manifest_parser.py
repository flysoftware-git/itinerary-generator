"""Tests for generator.manifest_parser"""
import pytest
from pathlib import Path
from generator.parser import ManifestParser

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_valid_manifest():
    parser = ManifestParser()
    trip = parser.load(str(FIXTURES / "sample_manifest.yaml"))
    assert trip["trip"]["title"] == "Test Road Trip"
    assert len(trip["destinations"]) == 2
    assert trip["destinations"][0]["id"] == "zion"


def test_destinations_have_required_fields():
    parser = ManifestParser()
    trip = parser.load(str(FIXTURES / "sample_manifest.yaml"))
    for dest in trip["destinations"]:
        assert "id" in dest
        assert "name" in dest
        assert "dates" in dest
        assert "planning_links" in dest


def test_seed_urls_rejected(tmp_path):
    manifest_content = """
trip:
  title: "Test"
  subtitle: "Test"
  theme_color: "#123456"
destinations:
  - id: test
    name: "Test Destination"
    dates: "Jan 1–3, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com"
    seeds:
      - "https://alltrails.com/trail/test"
"""
    f = tmp_path / "bad_manifest.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    with pytest.raises(ValueError, match="URL"):
        parser.load(str(f))


def test_en_route_seed_urls_rejected(tmp_path):
    manifest_content = """
trip:
  title: "Test"
  subtitle: "Test"
  theme_color: "#123456"
destinations:
  - id: test
    name: "Test Destination"
    dates: "Jan 1–3, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com"
    en_route_seeds:
      - "https://example.com/enchanted-circle"
"""
    f = tmp_path / "bad_en_route_seed_manifest.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    with pytest.raises(ValueError, match="URL"):
        parser.load(str(f))


def test_en_route_seeds_valid_manifest_parses(tmp_path):
    manifest_content = """
trip:
  title: "Test"
  subtitle: "Test"
  theme_color: "#123456"
destinations:
  - id: taos
    name: "Taos"
    dates: "Jan 1–3, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com/taos"
  - id: pagosa_springs
    name: "Pagosa Springs"
    dates: "Jan 3–5, 2026"
    en_route_seeds:
      - "Enchanted Circle Scenic Drive"
    planning_links:
      - label: "Notes"
        url: "https://example.com/pagosa"
"""
    f = tmp_path / "en_route_seed_manifest.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    trip = parser.load(str(f))
    dests = {d["id"]: d for d in trip["destinations"]}
    assert dests["pagosa_springs"]["en_route_seeds"] == ["Enchanted Circle Scenic Drive"]
    assert "en_route_seeds" not in dests["taos"]


def test_duplicate_ids_rejected(tmp_path):
    manifest_content = """
trip:
  title: "Test"
  subtitle: "Test"
  theme_color: "#123456"
destinations:
  - id: dup
    name: "Destination A"
    dates: "Jan 1–2, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com/a"
  - id: dup
    name: "Destination B"
    dates: "Jan 3–4, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com/b"
"""
    f = tmp_path / "dup_manifest.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    with pytest.raises(ValueError, match="duplicate"):
        parser.load(str(f))


def test_missing_required_trip_field(tmp_path):
    manifest_content = """
trip:
  subtitle: "Missing title"
  theme_color: "#000"
destinations: []
"""
    f = tmp_path / "missing_field.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    with pytest.raises(Exception):
        parser.load(str(f))


def test_trip_llm_override_schema_valid(tmp_path):
    manifest_content = """
trip:
  title: "LLM Test"
  subtitle: "Schema"
  theme_color: "#123456"
  llm:
    provider: "anthropic"
    model: "claude-3-5-sonnet-latest"
    temperature: 0.4
    max_tokens: 2048
destinations:
  - id: test
    name: "Test Destination"
    dates: "Jan 1–3, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com"
"""
    f = tmp_path / "llm_manifest.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    trip = parser.load(str(f))
    assert trip["trip"]["llm"]["provider"] == "anthropic"


def test_trip_llm_provider_and_features_schema_valid(tmp_path):
    manifest_content = """
trip:
  title: "Grok Test"
  subtitle: "Schema"
  theme_color: "#123456"
  llm_provider: "grok"
  llm_features:
    code_execution: true
destinations:
  - id: test
    name: "Test Destination"
    dates: "Jan 1-3, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com"
"""
    f = tmp_path / "grok_manifest.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    trip = parser.load(str(f))
    assert trip["trip"]["llm_provider"] == "grok"
    assert trip["trip"]["llm_features"]["code_execution"] is True


def test_trip_datetime_anchors_schema_valid(tmp_path):
    manifest_content = """
trip:
  title: "Timing Test"
  subtitle: "Schema"
  theme_color: "#123456"
  departure: "Las Vegas"
  departure_datetime: "2026-10-07 08:30"
  return: "Salt Lake City"
  return_datetime: "2026-10-18 17:15"
destinations:
  - id: test
    name: "Test Destination"
    dates: "Oct 7-9, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com"
"""
    f = tmp_path / "timing_manifest.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    trip = parser.load(str(f))
    assert trip["trip"]["departure_datetime"] == "2026-10-07 08:30"
    assert trip["trip"]["return_datetime"] == "2026-10-18 17:15"


def _moab_group_manifest(extra_arches: str = "") -> str:
    return f"""
trip:
  title: "Moab Group Test"
  subtitle: "Schema"
  theme_color: "#123456"
destinations:
  - id: moab
    name: "Moab"
    dates: "August 1-4, 2026"
    lodging:
      name: "Moab Springs Ranch"
      location: "Moab Springs Ranch, Moab, UT"
      checkin_time: "4:00 PM"
    planning_links:
      - label: "Notes"
        url: "https://example.com/moab"
  - id: arches
    name: "Arches National Park"
    dates: "August 2, 2026"
    group_with: moab
    {extra_arches}
    planning_links:
      - label: "Notes"
        url: "https://example.com/arches"
  - id: canyonlands
    name: "Canyonlands National Park"
    dates: "August 3, 2026"
    group_with: moab
    planning_links:
      - label: "Notes"
        url: "https://example.com/canyonlands"
"""


def test_group_with_valid_manifest_parses(tmp_path):
    f = tmp_path / "moab_group.yaml"
    f.write_text(_moab_group_manifest(), encoding="utf-8")
    parser = ManifestParser()
    trip = parser.load(str(f))
    dests = {d["id"]: d for d in trip["destinations"]}
    assert dests["arches"]["group_with"] == "moab"
    assert dests["canyonlands"]["group_with"] == "moab"
    assert "group_with" not in dests["moab"]


def test_group_with_self_reference_rejected(tmp_path):
    manifest_content = """
trip:
  title: "Test"
  subtitle: "Test"
  theme_color: "#123456"
destinations:
  - id: moab
    name: "Moab"
    dates: "August 1-4, 2026"
    group_with: moab
    planning_links:
      - label: "Notes"
        url: "https://example.com"
"""
    f = tmp_path / "self_ref.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    with pytest.raises(ValueError, match="cannot reference itself"):
        parser.load(str(f))


def test_group_with_missing_target_rejected(tmp_path):
    manifest_content = """
trip:
  title: "Test"
  subtitle: "Test"
  theme_color: "#123456"
destinations:
  - id: arches
    name: "Arches National Park"
    dates: "August 2, 2026"
    group_with: nonexistent
    planning_links:
      - label: "Notes"
        url: "https://example.com"
"""
    f = tmp_path / "missing_target.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    with pytest.raises(ValueError, match="does not match any destination id"):
        parser.load(str(f))


def test_group_with_chain_rejected(tmp_path):
    manifest_content = """
trip:
  title: "Test"
  subtitle: "Test"
  theme_color: "#123456"
destinations:
  - id: moab
    name: "Moab"
    dates: "August 1-4, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com/a"
  - id: arches
    name: "Arches National Park"
    dates: "August 2, 2026"
    group_with: moab
    planning_links:
      - label: "Notes"
        url: "https://example.com/b"
  - id: canyon_overlook
    name: "Canyon Overlook"
    dates: "August 2, 2026"
    group_with: arches
    planning_links:
      - label: "Notes"
        url: "https://example.com/c"
"""
    f = tmp_path / "chain.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    with pytest.raises(ValueError, match="is itself grouped"):
        parser.load(str(f))


def test_group_with_dates_outside_base_range_warns_not_raises(tmp_path, caplog):
    manifest_content = """
trip:
  title: "Test"
  subtitle: "Test"
  theme_color: "#123456"
destinations:
  - id: moab
    name: "Moab"
    dates: "August 1-4, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com/a"
  - id: arches
    name: "Arches National Park"
    dates: "September 20, 2026"
    group_with: moab
    planning_links:
      - label: "Notes"
        url: "https://example.com/b"
"""
    f = tmp_path / "out_of_range.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    with caplog.at_level("WARNING"):
        trip = parser.load(str(f))  # must NOT raise
    assert trip["destinations"][1]["id"] == "arches"
    assert any("fall outside group base" in rec.message for rec in caplog.records)


def test_base_owned_categories_per_entry_override_schema_valid(tmp_path):
    f = tmp_path / "moab_override.yaml"
    f.write_text(
        _moab_group_manifest(extra_arches="base_owned_categories: [\"restaurant\", \"scenic_drive\"]"),
        encoding="utf-8",
    )
    parser = ManifestParser()
    trip = parser.load(str(f))
    dests = {d["id"]: d for d in trip["destinations"]}
    assert dests["arches"]["base_owned_categories"] == ["restaurant", "scenic_drive"]
    assert "base_owned_categories" not in dests["canyonlands"]


def test_base_owned_categories_rejects_invalid_category(tmp_path):
    manifest_content = """
trip:
  title: "Test"
  subtitle: "Test"
  theme_color: "#123456"
destinations:
  - id: moab
    name: "Moab"
    dates: "August 1-4, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com/a"
  - id: arches
    name: "Arches National Park"
    dates: "August 2, 2026"
    group_with: moab
    base_owned_categories: ["not_a_real_category"]
    planning_links:
      - label: "Notes"
        url: "https://example.com/b"
"""
    f = tmp_path / "bad_category.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    with pytest.raises(Exception):
        parser.load(str(f))


def test_destination_lodging_anchor_schema_valid(tmp_path):
    manifest_content = """
trip:
  title: "Lodging Anchor Test"
  subtitle: "Schema"
  theme_color: "#123456"
destinations:
  - id: zion
    name: "Zion National Park"
    dates: "Oct 7-9, 2026"
    lodging:
      name: "Zion Lodge"
      location: "Zion Lodge, Springdale, UT"
      checkin_time: "4:00 PM"
    planning_links:
      - label: "Notes"
        url: "https://example.com"
"""
    f = tmp_path / "lodging_anchor_manifest.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    trip = parser.load(str(f))
    lodging = trip["destinations"][0]["lodging"]
    assert lodging["location"] == "Zion Lodge, Springdale, UT"
    assert lodging["checkin_time"] == "4:00 PM"
