"""Route freshness: was this run's harvesting already paid for on an earlier day?

The number exists because the same cost means a different thing on a cold route
than on a warm one. A run that does not record which it was cannot be re-read
later, and the cache-hit information is gone the moment the process exits.
"""

from __future__ import annotations

import json

import pytest

from generator.url_discovery import HARVEST_CACHE_SECTIONS, URLDiscoverer


@pytest.fixture
def discoverer() -> URLDiscoverer:
    obj = object.__new__(URLDiscoverer)
    obj._harvest_freshness = {"warm": 0, "repeat": 0, "cold": 0}
    obj._harvest_preloaded_keys = {}
    obj._attraction_direct_batch_cache = {}
    obj._restaurant_direct_batch_cache = {}
    return obj


def test_the_section_map_is_named_once():
    """It was written out twice -- the load path and the save path -- and a
    fifth section added to one would silently not be persisted by the other."""
    assert set(HARVEST_CACHE_SECTIONS) == {
        "direct_batch_harvest_alltrails",
        "direct_batch_harvest_attractions",
        "direct_batch_harvest_restaurants",
        "direct_batch_harvest_en_route",
    }


def test_a_cache_is_identified_by_identity_not_by_a_parameter(discoverer):
    assert discoverer._harvest_section_for(
        discoverer._attraction_direct_batch_cache
    ) == "direct_batch_harvest_attractions"


def test_an_unrecognised_cache_counts_cold_rather_than_raising(discoverer):
    """Under-reporting warmth is the direction that fails safe: the number
    exists to explain why a run was *cheap*."""
    stranger: dict = {}
    assert discoverer._harvest_section_for(stranger) == ""
    discoverer._note_harvest_lookup(stranger, "k", served_from_cache=True)
    assert discoverer.harvest_freshness()["warm"] == 0
    assert discoverer.harvest_freshness()["repeat"] == 1


def test_warm_means_inherited_from_disk_and_repeat_means_bought_this_run(discoverer):
    """The distinction is the whole point. A plain hit rate counts both together
    and therefore rises with the number of destinations sharing a query, which
    says nothing about how well-travelled the route is."""
    cache = discoverer._attraction_direct_batch_cache
    discoverer._harvest_preloaded_keys["direct_batch_harvest_attractions"] = {"zion"}

    discoverer._note_harvest_lookup(cache, "zion", served_from_cache=True)
    discoverer._note_harvest_lookup(cache, "moab", served_from_cache=False)
    discoverer._note_harvest_lookup(cache, "moab", served_from_cache=True)

    freshness = discoverer.harvest_freshness()
    assert freshness["warm"] == 1
    assert freshness["cold"] == 1
    assert freshness["repeat"] == 1


def test_the_ratio_excludes_repeats_from_the_denominator(discoverer):
    """A second lookup of a key bought moments ago was never going to be a
    separate purchase, so counting it would inflate the saving."""
    cache = discoverer._attraction_direct_batch_cache
    discoverer._harvest_preloaded_keys["direct_batch_harvest_attractions"] = {"a", "b", "c"}
    for key in ("a", "b", "c"):
        discoverer._note_harvest_lookup(cache, key, served_from_cache=True)
    discoverer._note_harvest_lookup(cache, "d", served_from_cache=False)
    for _ in range(10):
        discoverer._note_harvest_lookup(cache, "d", served_from_cache=True)

    freshness = discoverer.harvest_freshness()
    assert freshness["lookups"] == 14
    assert freshness["warm_ratio"] == 0.75      # 3 warm of 4 that could have been bought
    assert freshness["repeat"] == 10


def test_a_run_that_harvested_nothing_has_no_ratio_rather_than_a_zero(discoverer):
    """Zero would read as "entirely cold", which is a claim. No lookups is not."""
    assert discoverer.harvest_freshness()["warm_ratio"] is None
    assert discoverer.harvest_freshness()["lookups"] == 0


def test_the_counters_survive_an_instance_built_without_init():
    """The same `hasattr` guard the rest of the class uses. A counter must never
    be the reason a harvest raises."""
    bare = object.__new__(URLDiscoverer)
    bare._attraction_direct_batch_cache = {}
    bare._note_harvest_lookup(bare._attraction_direct_batch_cache, "k", served_from_cache=False)
    assert bare.harvest_freshness()["cold"] == 1


def test_preloaded_keys_are_recorded_before_any_lookup_can_overwrite_them(tmp_path, monkeypatch):
    """After the first miss writes to the same dict there is no way to tell a
    restored entry from one this run just bought, so the set is captured at load."""
    import time

    cache_file = tmp_path / "persistent_cache.json"
    cache_file.write_text(json.dumps({
        "direct_batch_harvest_attractions": {
            "zion": {"ts": time.time(), "rows": [{"name": "Angels Landing"}]},
        }
    }), encoding="utf-8")

    obj = object.__new__(URLDiscoverer)
    obj._persistent_cache_enabled = True
    obj._persistent_harvest_cache_ttl_hours = 168
    monkeypatch.setattr(URLDiscoverer, "_persistent_cache_file", lambda self: cache_file)
    obj._load_persistent_caches()

    assert "zion" in obj._harvest_preloaded_keys["direct_batch_harvest_attractions"]
    assert obj._attraction_direct_batch_cache["zion"] == [{"name": "Angels Landing"}]

    obj._note_harvest_lookup(obj._attraction_direct_batch_cache, "zion", served_from_cache=True)
    assert obj.harvest_freshness()["warm"] == 1


def test_an_expired_harvest_is_neither_loaded_nor_counted_warm(tmp_path, monkeypatch):
    """A TTL-expired entry is not in the cache, so the lookup that follows is a
    miss -- and it must not be remembered as though it had been available."""
    cache_file = tmp_path / "persistent_cache.json"
    cache_file.write_text(json.dumps({
        "direct_batch_harvest_attractions": {
            "zion": {"ts": 0, "rows": [{"name": "Angels Landing"}]},
        }
    }), encoding="utf-8")

    obj = object.__new__(URLDiscoverer)
    obj._persistent_cache_enabled = True
    obj._persistent_harvest_cache_ttl_hours = 168
    monkeypatch.setattr(URLDiscoverer, "_persistent_cache_file", lambda self: cache_file)
    obj._load_persistent_caches()

    assert obj._attraction_direct_batch_cache == {}
    assert getattr(obj, "_harvest_preloaded_keys", {}) == {}

    # And the behaviour that matters: a harvest bought after the expiry counts
    # as this run's, never as one inherited.
    obj._note_harvest_lookup(obj._attraction_direct_batch_cache, "zion", served_from_cache=False)
    obj._note_harvest_lookup(obj._attraction_direct_batch_cache, "zion", served_from_cache=True)
    assert obj.harvest_freshness()["warm"] == 0
    assert obj.harvest_freshness()["cold"] == 1
    assert obj.harvest_freshness()["repeat"] == 1
