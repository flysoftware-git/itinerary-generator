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


def test_en_route_exclude_urls_rejected(tmp_path):
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
    en_route_exclude:
      - "https://example.com/confluence-park"
"""
    f = tmp_path / "bad_en_route_exclude_manifest.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    with pytest.raises(ValueError, match="URL"):
        parser.load(str(f))


def test_en_route_exclude_valid_manifest_parses(tmp_path):
    """Real bug: 'Confluence Park' geocodes to a real, live-verified match
    that's a different, wrong same-named place -- no automated check can
    reliably distinguish this from a legitimate stop (see
    docs/design/url-discovery-and-audit.md). en_route_exclude gives the
    traveler a durable way to blocklist a specific known-bad name, mirroring
    en_route_seeds' exact schema shape with the opposite effect."""
    manifest_content = """
trip:
  title: "Test"
  subtitle: "Test"
  theme_color: "#123456"
destinations:
  - id: st_george
    name: "St. George, Utah"
    dates: "Oct 17, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com/stgeorge"
  - id: zion
    name: "Zion National Park"
    dates: "Oct 18, 2026"
    en_route_exclude:
      - "Confluence Park"
    planning_links:
      - label: "Notes"
        url: "https://example.com/zion"
"""
    f = tmp_path / "en_route_exclude_manifest.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    trip = parser.load(str(f))
    dests = {d["id"]: d for d in trip["destinations"]}
    assert dests["zion"]["en_route_exclude"] == ["Confluence Park"]
    assert "en_route_exclude" not in dests["st_george"]


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


def test_has_high_clearance_vehicle_false_parses(tmp_path):
    manifest_content = """
trip:
  title: "Test"
  subtitle: "Test"
  theme_color: "#123456"
  has_high_clearance_vehicle: false
destinations:
  - id: zion
    name: "Zion National Park"
    dates: "Oct 7-9, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com"
"""
    f = tmp_path / "no_clearance_manifest.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    trip = parser.load(str(f))
    assert trip["trip"]["has_high_clearance_vehicle"] is False


def test_has_high_clearance_vehicle_true_parses(tmp_path):
    manifest_content = """
trip:
  title: "Test"
  subtitle: "Test"
  theme_color: "#123456"
  has_high_clearance_vehicle: true
destinations:
  - id: zion
    name: "Zion National Park"
    dates: "Oct 7-9, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com"
"""
    f = tmp_path / "has_clearance_manifest.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    trip = parser.load(str(f))
    assert trip["trip"]["has_high_clearance_vehicle"] is True


def test_has_high_clearance_vehicle_absent_by_default():
    parser = ManifestParser()
    trip = parser.load(str(FIXTURES / "sample_manifest.yaml"))
    assert "has_high_clearance_vehicle" not in trip["trip"]


def test_has_high_clearance_vehicle_rejects_non_boolean(tmp_path):
    manifest_content = """
trip:
  title: "Test"
  subtitle: "Test"
  theme_color: "#123456"
  has_high_clearance_vehicle: "no"
destinations:
  - id: zion
    name: "Zion National Park"
    dates: "Oct 7-9, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com"
"""
    f = tmp_path / "bad_clearance_manifest.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    with pytest.raises(Exception):
        parser.load(str(f))


def test_yaml_syntax_error_gives_clean_line_column_message(tmp_path):
    """Regression: a real manifest with one destination's `dates:` key
    indented one space deeper than its siblings previously surfaced as a raw
    Python traceback bottoming out in PyYAML's internal composer/parser
    frames, with no indication which manifest line to look at. Real example
    hit twice in one session against C:\\Dev\\Sandbox\\Croatia_manifest.yaml."""
    manifest_content = """
trip:
  title: "Test"
  subtitle: "Test"
  theme_color: "#123456"
destinations:
  - id: bled
    name: "Bled, Slovenia"
     dates: "October 10-11, 2027"
"""
    f = tmp_path / "bad_indent.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    with pytest.raises(ValueError) as exc_info:
        parser.load(str(f))
    message = str(exc_info.value)
    assert "YAML syntax error" in message
    assert str(f) in message
    assert "line 9" in message
    # Must NOT be a raw jsonschema/PyYAML repr with internal frame noise.
    assert "Traceback" not in message
    assert "composer.py" not in message


def test_schema_error_names_destination_not_just_index(tmp_path):
    """Regression: jsonschema.ValidationError's default str() embeds the
    entire sub-schema being validated against (every property's full
    description text included) as library-author context -- a real user
    hitting a missing required field saw several hundred lines of schema
    dump for one missing 'planning_links' field. The clean message should
    name the destination by its own id, not just a bare numeric index, and
    must not include the schema dump."""
    manifest_content = """
trip:
  title: "Test"
  subtitle: "Test trip"
  theme_color: "#000000"
destinations:
  - id: ljubljana
    name: "Ljubljana, Slovenia"
    dates: "October 7-9, 2027"
    planning_links:
      - label: "Notes"
        url: "https://example.com"
  - id: kotor
    name: "Kotor, Montenegro"
    dates: "October 21-22, 2027"
"""
    f = tmp_path / "missing_planning_links.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    with pytest.raises(ValueError) as exc_info:
        parser.load(str(f))
    message = str(exc_info.value)
    assert "destinations[1]" in message
    assert "kotor" in message
    assert "'planning_links' is a required property" in message
    # Must NOT be the raw jsonschema dump (which includes every property's
    # own description text, e.g. this unrelated field's help text).
    assert "GH #68" not in message
    assert len(message.splitlines()) == 1


def test_schema_error_id_pattern_names_destination_by_its_own_id(tmp_path):
    """A second real bug hit in the same session: destination ids were
    capitalized (e.g. 'Kotor'), failing the schema's lowercase-only id
    pattern. The failing value itself (the bad id) should still be
    findable in the message even though the destination's own 'id' field
    is exactly what's invalid."""
    manifest_content = """
trip:
  title: "Test"
  subtitle: "Test trip"
  theme_color: "#000000"
destinations:
  - id: Kotor
    name: "Kotor, Montenegro"
    dates: "October 21-22, 2027"
    planning_links:
      - label: "Notes"
        url: "https://example.com"
"""
    f = tmp_path / "bad_id_case.yaml"
    f.write_text(manifest_content, encoding="utf-8")
    parser = ManifestParser()
    with pytest.raises(ValueError) as exc_info:
        parser.load(str(f))
    message = str(exc_info.value)
    assert "destinations[0]" in message
    assert "does not match" in message


def test_transportation_type_enum_accepts_carried_travel() -> None:
    """`ship`/`ferry` cover a cruise or crossing that carries the traveler
    between stops. `cruise` is deliberately NOT a value -- one spelling per
    concept, and `ship` also covers a repositioning sailing that nobody would
    call a cruise."""
    import jsonschema

    from generator.manifest_parser import TRANSPORTATION_ITEM_SCHEMA

    for accepted in ("plane", "train", "car", "ship", "ferry", "bus", "shuttle", "other"):
        jsonschema.validate({"type": accepted}, TRANSPORTATION_ITEM_SCHEMA)

    for rejected in ("cruise", "boat", ""):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"type": rejected}, TRANSPORTATION_ITEM_SCHEMA)


def _manifest_yaml(*, trip_extra: str = "", zion_extra: str = "", bryce_extra: str = "",
                   legs: str = "") -> str:
    """Two adjacent destinations, the smallest shape that has a leg at all."""
    return f"""
trip:
  title: "Test"
  subtitle: "Test"
  theme_color: "#123456"
{trip_extra}destinations:
  - id: zion
    name: "Zion National Park"
    dates: "Jan 1-3, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com"
{zion_extra}  - id: bryce_canyon
    name: "Bryce Canyon National Park"
    dates: "Jan 3-5, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com"
{bryce_extra}{legs}"""


def _write(tmp_path, content: str):
    f = tmp_path / "manifest.yaml"
    f.write_text(content, encoding="utf-8")
    return f


@pytest.mark.parametrize("mode", ["auto", "transit", "mixed"])
def test_transport_mode_accepted_at_both_levels(tmp_path, mode):
    parser = ManifestParser()
    f = _write(tmp_path, _manifest_yaml(
        trip_extra=f"  transport_mode: {mode}\n",
        bryce_extra=f"    transport_mode: {mode}\n",
    ))
    data = parser.load(str(f))
    assert data["trip"]["transport_mode"] == mode
    assert data["destinations"][1]["transport_mode"] == mode


def test_transport_mode_unknown_value_fails_naming_the_destination(tmp_path):
    parser = ManifestParser()
    f = _write(tmp_path, _manifest_yaml(bryce_extra="    transport_mode: teleport\n"))
    with pytest.raises(ValueError) as exc_info:
        parser.load(str(f))
    message = str(exc_info.value)
    assert "bryce_canyon" in message


def test_omitting_transport_mode_leaves_the_manifest_untouched(tmp_path):
    """The default must be indistinguishable from today's behaviour, not
    'auto' quietly written in."""
    parser = ManifestParser()
    f = _write(tmp_path, _manifest_yaml())
    data = parser.load(str(f))
    assert "transport_mode" not in data["trip"]
    assert all("transport_mode" not in d for d in data["destinations"])


def test_legs_accepted_when_referencing_adjacent_ids(tmp_path):
    parser = ManifestParser()
    f = _write(tmp_path, _manifest_yaml(
        legs="legs:\n  - from: zion\n    to: bryce_canyon\n    mode: transit\n"
    ))
    data = parser.load(str(f))
    assert data["legs"][0]["mode"] == "transit"


@pytest.mark.parametrize("bad_from,bad_to,expected", [
    ("zion_np", "bryce_canyon", "zion_np"),
    ("zion", "bryce", "bryce"),
])
def test_legs_unknown_id_raises_naming_the_leg(tmp_path, bad_from, bad_to, expected):
    """The typo case, and the whole reason for the id contract: free-text
    matching would leave the leg silently `auto`."""
    parser = ManifestParser()
    f = _write(tmp_path, _manifest_yaml(
        legs=f"legs:\n  - from: {bad_from}\n    to: {bad_to}\n    mode: transit\n"
    ))
    with pytest.raises(ValueError) as exc_info:
        parser.load(str(f))
    message = str(exc_info.value)
    assert expected in message
    assert "does not match any destination id" in message


def test_legs_display_names_fail_loudly_rather_than_matching_nothing(tmp_path):
    """'Zion NP' against 'Zion National Park' is exactly the silent-fallback
    the id contract exists to prevent. It must not parse."""
    parser = ManifestParser()
    f = _write(tmp_path, _manifest_yaml(
        legs='legs:\n  - from: "Zion NP"\n    to: "Bryce Canyon NP"\n    mode: transit\n'
    ))
    with pytest.raises(ValueError):
        parser.load(str(f))


def test_legs_self_reference_raises(tmp_path):
    parser = ManifestParser()
    f = _write(tmp_path, _manifest_yaml(
        legs="legs:\n  - from: zion\n    to: zion\n    mode: transit\n"
    ))
    with pytest.raises(ValueError) as exc_info:
        parser.load(str(f))
    assert "not a leg" in str(exc_info.value)


def test_legs_non_adjacent_pair_raises(tmp_path):
    parser = ManifestParser()
    content = _manifest_yaml() + """  - id: moab
    name: "Moab"
    dates: "Jan 5-7, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com"
legs:
  - from: zion
    to: moab
    mode: transit
"""
    with pytest.raises(ValueError) as exc_info:
        parser.load(str(_write(tmp_path, content)))
    message = str(exc_info.value)
    assert "not adjacent" in message
    assert "zion" in message and "moab" in message


def test_legs_backwards_pair_raises(tmp_path):
    """Adjacency is directional: the leg runs the way the traveler does."""
    parser = ManifestParser()
    f = _write(tmp_path, _manifest_yaml(
        legs="legs:\n  - from: bryce_canyon\n    to: zion\n    mode: transit\n"
    ))
    with pytest.raises(ValueError) as exc_info:
        parser.load(str(f))
    assert "not adjacent" in str(exc_info.value)


def test_legs_duplicate_leg_raises(tmp_path):
    parser = ManifestParser()
    f = _write(tmp_path, _manifest_yaml(
        legs="legs:\n  - from: zion\n    to: bryce_canyon\n    mode: transit\n"
             "  - from: zion\n    to: bryce_canyon\n    mode: mixed\n"
    ))
    with pytest.raises(ValueError) as exc_info:
        parser.load(str(f))
    message = str(exc_info.value)
    assert "duplicates" in message
    assert "legs[0]" in message


def test_legs_agreeing_with_destination_transport_mode_parses_clean(tmp_path):
    parser = ManifestParser()
    f = _write(tmp_path, _manifest_yaml(
        bryce_extra="    transport_mode: transit\n",
        legs="legs:\n  - from: zion\n    to: bryce_canyon\n    mode: transit\n",
    ))
    data = parser.load(str(f))
    assert data["legs"][0]["mode"] == "transit"
    assert data["destinations"][1]["transport_mode"] == "transit"


def test_legs_disagreeing_with_destination_transport_mode_raises_naming_both(tmp_path):
    """Agreement is fine, disagreement raises -- not last-wins. Silent
    precedence would mean an author edits transport_mode, sees no change,
    and has no way to discover why."""
    parser = ManifestParser()
    f = _write(tmp_path, _manifest_yaml(
        bryce_extra="    transport_mode: mixed\n",
        legs="legs:\n  - from: zion\n    to: bryce_canyon\n    mode: transit\n",
    ))
    with pytest.raises(ValueError) as exc_info:
        parser.load(str(f))
    message = str(exc_info.value)
    assert "transit" in message
    assert "mixed" in message
    assert "bryce_canyon" in message


def test_legs_rejects_unknown_key(tmp_path):
    parser = ManifestParser()
    f = _write(tmp_path, _manifest_yaml(
        legs="legs:\n  - from: zion\n    to: bryce_canyon\n    mode: transit\n"
             "    depart: '09:00'\n"
    ))
    with pytest.raises(ValueError):
        parser.load(str(f))


def test_transport_mode_on_grouped_entry_warns_and_is_ignored(tmp_path, caplog):
    """A group_with entry is a there-and-back day trip, not an arriving leg,
    so a mode on it describes nothing. Warn rather than raise -- the same
    posture as grouped dates outside the base's range."""
    import logging

    parser = ManifestParser()
    content = _manifest_yaml() + """  - id: arches
    name: "Arches National Park"
    dates: "Jan 3-4, 2026"
    group_with: bryce_canyon
    transport_mode: transit
    planning_links:
      - label: "Notes"
        url: "https://example.com"
"""
    with caplog.at_level(logging.WARNING):
        data = parser.load(str(_write(tmp_path, content)))
    assert data["destinations"][2]["transport_mode"] == "transit"
    assert any("'arches'" in r.getMessage() for r in caplog.records)


def test_the_issues_rejected_key_name_raises_rather_than_being_ignored(tmp_path):
    """`transport_mode_from_previous` is the name GH #2's issue proposed and
    the design rejected. Destination items allow unknown keys, so accepting
    it silently means a manifest written to the issue's spelling validates
    clean, resolves every leg to the trip-wide default, and ships an
    itinerary telling the traveler to drive a leg they meant as a train.

    Not hypothetical: the Japan acceptance manifest was written that way.
    """
    parser = ManifestParser()
    f = _write(tmp_path, _manifest_yaml(
        trip_extra="  transport_mode: mixed\n",
        bryce_extra="    transport_mode_from_previous: transit\n",
    ))
    with pytest.raises(ValueError) as exc_info:
        parser.load(str(f))
    message = str(exc_info.value)
    assert "transport_mode_from_previous" in message
    assert "bryce_canyon" in message
    # Names the right key, and what would have happened instead.
    assert "'transport_mode'" in message
    assert "mixed" in message


def test_the_correct_key_still_parses(tmp_path):
    parser = ManifestParser()
    f = _write(tmp_path, _manifest_yaml(bryce_extra="    transport_mode: transit\n"))
    data = parser.load(str(f))
    assert data["destinations"][1]["transport_mode"] == "transit"


@pytest.mark.parametrize("mode", ["bike", "hike"])
def test_self_powered_modes_are_accepted_at_both_levels(tmp_path, mode):
    parser = ManifestParser()
    f = _write(tmp_path, _manifest_yaml(
        trip_extra=f"  transport_mode: {mode}\n",
        bryce_extra=f"    transport_mode: {mode}\n",
    ))
    data = parser.load(str(f))
    assert data["trip"]["transport_mode"] == mode
    assert data["destinations"][1]["transport_mode"] == mode


@pytest.mark.parametrize("mode", ["bike", "hike"])
def test_legs_accept_the_self_powered_modes_too(tmp_path, mode):
    """`legs:` and `transport_mode` share one enum; a mode accepted by one
    and rejected by the other would be a trap."""
    parser = ManifestParser()
    f = _write(tmp_path, _manifest_yaml(
        legs=f"legs:\n  - from: zion\n    to: bryce_canyon\n    mode: {mode}\n"
    ))
    assert parser.load(str(f))["legs"][0]["mode"] == mode


def test_a_near_miss_spelling_is_still_rejected(tmp_path):
    """Widening the enum must not widen it to anything plausible-looking."""
    parser = ManifestParser()
    f = _write(tmp_path, _manifest_yaml(bryce_extra="    transport_mode: biking\n"))
    with pytest.raises(ValueError) as exc_info:
        parser.load(str(f))
    assert "bryce_canyon" in str(exc_info.value)
