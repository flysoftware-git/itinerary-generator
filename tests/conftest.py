"""Shared pytest fixtures.

The autouse fixture here exists because of a real, measured cost bug rather
than as tidiness: the persistent URL-discovery cache path
(url_discovery.DEFAULT_PERSISTENT_CACHE_PATH) is *relative*
(".cache/url_discovery/persistent_cache.json"), so it resolves against the
current working directory -- which, for a pytest run started at the repo root,
is the same file a real generator run uses.

Any test that constructs a genuine URLDiscoverer (rather than via __new__)
loads that file, and any test that then marks the cache dirty rewrites it from
its own near-empty in-memory state. _save_persistent_caches writes a full
snapshot rather than merging, so the rewrite discards every real search result
and direct-batch harvest row the last real build accumulated.

Observed 2026-08-19: a `pytest tests/test_url_discovery.py` run rewrote the
production cache file, leaving it with 0 search_results and 0 harvest entries
despite a 168h search TTL. The next generator run then re-paid xAI's
$5/1000 web_search fee for queries it had already bought -- and since tests
run far more often than builds do, the cache was effectively never warm.
Search tool fees were ~78% of a real run's estimated cost ($0.2400 of $0.3077
across 48 calls), so this was not a rounding error.
"""
from __future__ import annotations

import pytest

import generator.url_discovery as url_discovery_mod


@pytest.fixture(autouse=True)
def isolate_persistent_url_cache(tmp_path_factory, monkeypatch):
    """Point the default persistent-cache path at a per-test temp file.

    Patching the module constant (rather than each instance) covers every way
    an instance can arrive at the path: __init__'s direct assignment,
    _load_interest_filters' config fallback -- config.yaml declares no
    persistent_cache_path, so it always falls back to this constant -- and
    _persistent_cache_file's getattr default.

    Tests that deliberately exercise cache round-tripping set an explicit
    _persistent_cache_path of their own and are unaffected.
    """
    cache_dir = tmp_path_factory.mktemp("url_discovery_cache")
    monkeypatch.setattr(
        url_discovery_mod,
        "DEFAULT_PERSISTENT_CACHE_PATH",
        str(cache_dir / "persistent_cache.json"),
    )
