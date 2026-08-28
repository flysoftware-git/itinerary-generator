"""Every committed manifest must stay valid against the current schema.

These files are coverage fixtures -- they exist to exercise option
combinations the unit tests reach only through hand-built dicts. Until this
module existed nothing ran them, so a schema change could invalidate all of
them and the suite would stay green. `--dry-run` in CI covers the same
ground through the CLI; this covers it without a subprocess, and adds the
assertions about WHAT each fixture is supposed to exercise -- a fixture that
quietly stops covering its case is as bad as one that fails to parse.
"""
from pathlib import Path

import pytest

from generator.manifest_parser import ManifestParser
from generator.multi_site_grouping import (
    category_deferred_to_base,
    is_park_like,
    resolve_base_owned_categories,
)

MANIFEST_DIR = Path(__file__).resolve().parent.parent / "manifests"
# Gitignored companions that are not manifests and must not be parsed as
# one: *.local.yaml (private, machine-specific), *.private.yaml (the
# substitution overrides scripts/sync_local_manifest.py reads) and
# *.reservations.yaml (the ingestion sidecar). None is guaranteed to exist
# on any given machine.
_NOT_A_MANIFEST = (".local.", ".private.", ".reservations.")
MANIFESTS = sorted(
    p for p in MANIFEST_DIR.glob("*.yaml")
    if not any(marker in p.name for marker in _NOT_A_MANIFEST)
)


def _parse(path: Path) -> dict:
    return ManifestParser(config_path="config.yaml").parse(path)


def test_manifest_directory_is_not_empty() -> None:
    """Guards the glob itself: a rename that emptied it would otherwise make
    every parametrized test below silently vacuous."""
    assert MANIFESTS, f"no manifests found in {MANIFEST_DIR}"


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.name)
def test_committed_manifest_parses(path: Path) -> None:
    trip = _parse(path)
    assert trip["destinations"], f"{path.name} has no destinations"


def test_alpine_fixture_covers_the_park_vs_city_mixed_case() -> None:
    """The reason this fixture exists: one base with a park child and a city
    child, deferring differently. Nothing else covers it."""
    trip = _parse(MANIFEST_DIR / "alpine_grouped.yaml")
    by_id = {d["id"]: d for d in trip["destinations"]}

    park = by_id["berchtesgaden_np"]
    city = by_id["salzburg"]
    assert park["group_with"] == city["group_with"] == "berchtesgaden"

    # A non-US park has no nps_park_code, so this must hold on the name alone.
    assert not park.get("nps_park_code")
    assert is_park_like(park)
    assert not is_park_like(city)

    assert category_deferred_to_base(park, "restaurant")
    assert category_deferred_to_base(park, "cultural_events")
    assert not category_deferred_to_base(city, "restaurant")
    assert not category_deferred_to_base(city, "cultural_events")

    # The only explicit non-default override in any committed manifest.
    assert "scenic_drive" in resolve_base_owned_categories(park)


def test_alpine_fixture_is_a_round_trip_with_lodging() -> None:
    trip = _parse(MANIFEST_DIR / "alpine_grouped.yaml")["trip"]
    assert trip["departure"] == trip["return"]


def test_tuning_fixture_sets_every_previously_unused_knob() -> None:
    """The audit that motivated this fixture listed these by name. If a knob
    is dropped from the manifest, this fails rather than quietly reducing
    coverage back to zero."""
    trip = _parse(MANIFEST_DIR / "tuning_surface.yaml")
    by_id = {d["id"]: d for d in trip["destinations"]}
    per_day = ("attractions_per_day", "restaurants_per_day",
               "scenic_drives_per_day", "en_route_stops_per_day")

    for key in per_day + ("has_high_clearance_vehicle",):
        assert key in trip["trip"], f"trip-level {key} missing"

    override = by_id["grand_lake"]
    for key in per_day + ("schedule_start_time", "daily_activity_hours"):
        assert key in override, f"destination-level {key} missing"

    # Precedence is only demonstrated if the two levels actually differ.
    for key in per_day:
        assert override[key] != trip["trip"][key], (
            f"{key} override equals the trip-level value, so it proves nothing"
        )

    # ...and one destination must inherit, or there is no contrast.
    assert not any(k in by_id["estes_park"] for k in per_day)
