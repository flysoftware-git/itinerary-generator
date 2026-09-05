"""Park-vs-city default for GH #68 grouped entries.

The config default (restaurants and cultural events defer to the group base)
came from a park case -- Canyonlands generating Cultural Events that were
really about Moab. It does not generalize to a city day trip, where the
grouped entry is the larger place and owns both.
"""
from generator.multi_site_grouping import (
    DEFAULT_BASE_OWNED_CATEGORIES,
    category_deferred_to_base,
    is_park_like,
    resolve_base_owned_categories,
)


def test_grouped_park_still_defers_by_default() -> None:
    dest = {"id": "canyonlands", "name": "Canyonlands National Park", "group_with": "moab"}
    assert resolve_base_owned_categories(dest) == frozenset(DEFAULT_BASE_OWNED_CATEGORIES)
    assert category_deferred_to_base(dest, "cultural_events")
    assert category_deferred_to_base(dest, "restaurant")


def test_grouped_city_owns_its_restaurants_and_events() -> None:
    """Nashville reached from a base in Old Hickory is the bigger place;
    deferring its dining and music calendar to the base inverts that."""
    dest = {"id": "nashville", "name": "Nashville, Tennessee", "group_with": "oldhickory"}
    assert resolve_base_owned_categories(dest) == frozenset()
    assert not category_deferred_to_base(dest, "cultural_events")
    assert not category_deferred_to_base(dest, "restaurant")


def test_state_park_counts_as_park_without_an_nps_code() -> None:
    """nps_park_code is US-only and empty for state parks, so keying off it
    alone would classify every non-NPS park as a city."""
    dest = {"id": "bledsoe", "name": "Bledsoe Creek State Park", "group_with": "oldhickory"}
    assert is_park_like(dest)
    assert category_deferred_to_base(dest, "restaurant")


def test_non_us_park_counts_as_park() -> None:
    dest = {"id": "banff", "name": "Banff National Park", "group_with": "calgary"}
    assert is_park_like(dest)


def test_nps_code_alone_is_enough_when_the_name_lacks_a_keyword() -> None:
    dest = {"id": "zion", "name": "Zion", "group_with": "springdale", "nps_park_code": "zion"}
    assert is_park_like(dest)


def test_explicit_override_still_wins_for_a_park() -> None:
    dest = {
        "id": "canyonlands",
        "name": "Canyonlands National Park",
        "group_with": "moab",
        "base_owned_categories": [],
    }
    assert resolve_base_owned_categories(dest) == frozenset()
    assert not category_deferred_to_base(dest, "restaurant")


def test_explicit_override_still_wins_for_a_city() -> None:
    dest = {
        "id": "nashville",
        "name": "Nashville, Tennessee",
        "group_with": "oldhickory",
        "base_owned_categories": ["restaurant"],
    }
    assert category_deferred_to_base(dest, "restaurant")


def test_base_entry_never_defers_regardless_of_type() -> None:
    """A base entry has nothing to defer to."""
    park_base = {"id": "moab", "name": "Arches National Park"}
    assert not category_deferred_to_base(park_base, "restaurant")


def test_unrecognized_place_is_treated_as_a_town() -> None:
    """Failing open to "town" costs a redundant restaurant list; failing the
    other way costs a day trip with no dining and no events at all."""
    dest = {"id": "somewhere", "name": "Marfa, Texas", "group_with": "elpaso"}
    assert not is_park_like(dest)
