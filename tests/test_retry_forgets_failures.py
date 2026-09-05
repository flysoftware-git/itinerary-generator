"""A retry that remembers its failures cannot resolve anything.

The retry pass reuses the discoverer so it does not re-buy work already paid
for. The caches hold negatives as well as positives, so a lookup that failed
returned its cached failure and the retry re-derived the first pass's
conclusions. Measured across 9 runs and 58 destination-instances before this
existed: 27 retries, 0 resolved.
"""

from __future__ import annotations

import time

from generator import url_discovery as ud
from generator.url_discovery import URLDiscoverer, _cached_ok


def _bare() -> URLDiscoverer:
    obj = object.__new__(URLDiscoverer)
    obj._verify_url_cache = {}
    obj._alltrails_fetch_cache = {}
    obj._wayback_fetch_cache = {}
    obj._direct_batch_html_failure_ts = {}
    return obj


def test_a_cached_failure_is_forgotten_and_a_cached_success_is_not():
    """Successes stay because re-buying them would make a retry cost as much as
    a run. Only the record of *not* finding something goes -- it is the one
    thing a second attempt could change."""
    ud._url_cache.clear()
    ud._url_cache[("found", "", "")] = "https://example.com/real"
    ud._url_cache[("not-found", "", "")] = None

    dropped = _bare().forget_failures()

    assert dropped["searches"] == 1
    assert ("found", "", "") in ud._url_cache
    assert ("not-found", "", "") not in ud._url_cache
    ud._url_cache.clear()


def test_the_batch_failure_cooldown_does_not_outlive_the_pass_that_set_it():
    """It exists to stop a repeat *within* one pass. Carrying it into the retry
    short-circuits the network call the retry exists to make."""
    obj = _bare()
    obj._direct_batch_html_failure_ts = {"zion|html|trail": time.monotonic()}

    dropped = obj.forget_failures()

    assert dropped["batch_cooldowns"] == 1
    assert obj._direct_batch_html_failure_ts == {}


def test_failed_verifications_and_fetches_are_forgotten_by_their_ok_flag():
    obj = _bare()
    obj._verify_url_cache = {"good": (True, 200), "bad": (False, 404)}
    obj._alltrails_fetch_cache = {"a": (True, 200, "html"), "b": (False, 0, "")}
    obj._wayback_fetch_cache = {"c": (False, 500, "")}

    dropped = obj.forget_failures()

    assert dropped["verifications"] == 1
    assert dropped["fetches"] == 2
    assert set(obj._verify_url_cache) == {"good"}
    assert set(obj._alltrails_fetch_cache) == {"a"}
    assert obj._wayback_fetch_cache == {}


def test_the_ok_flag_helper_reads_the_tuples_the_caches_actually_hold():
    assert _cached_ok((True, 200, "text")) is True
    assert _cached_ok((False, 0, "")) is False
    assert _cached_ok(None) is False
    assert _cached_ok("https://example.com") is True


def test_forgetting_nothing_is_not_an_error():
    """A pass with no failures has nothing to forget, and the counts say so --
    which is how a caller can tell 'the retry could not have helped' from 'the
    retry was not tried'."""
    assert _bare().forget_failures() == {
        "searches": 0, "batch_cooldowns": 0, "verifications": 0, "fetches": 0
    }


def test_the_harvest_caches_are_left_alone():
    """They hold rows, and an empty harvest is already never written to them --
    'an empty batch is often a transient upstream hiccup, not "this destination
    has no attractions"'. There are no negatives in them to forget."""
    obj = _bare()
    obj._attraction_direct_batch_cache = {"zion": [{"name": "Angels Landing"}]}

    obj.forget_failures()

    assert obj._attraction_direct_batch_cache == {"zion": [{"name": "Angels Landing"}]}
