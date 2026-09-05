"""Saving the persistent cache must tolerate concurrent writers.

_save_persistent_caches iterated its dicts live while discovery threads were
still writing to them:

    RuntimeError: dictionary changed size during iteration
      for key, result in self._page_text_cache.items():

That aborted a full sw production run at the URL-discovery stage. The run
correctly skipped publishing, so nothing bad shipped -- but a paid run was
lost, and the crash is a race, so it appears only under enough concurrency to
interleave a write with the save.

Snapshotting with list() is the fix: the save writes a point-in-time view and
a concurrent write lands in the next save rather than corrupting this one.
"""

import re
import threading
from pathlib import Path

import pytest

SOURCE = (Path(__file__).resolve().parent.parent / "generator" / "url_discovery.py").read_text(encoding="utf-8")


def _save_body():
    start = SOURCE.index("def _save_persistent_caches")
    end = SOURCE.index("\n    def ", start + 10)
    return SOURCE[start:end]


def test_no_cache_dict_is_iterated_live_during_save():
    """Every .items() in the save must be wrapped in list()."""
    unguarded = [
        line.strip()
        for line in _save_body().splitlines()
        if re.search(r"for .*\bin\s+(?!list\()[^:]*\.items\(\):", line)
    ]
    assert not unguarded, f"unsnapshotted iteration(s): {unguarded}"


def test_a_write_during_iteration_does_not_raise():
    """The failure mode itself, reproduced on a plain dict."""
    d = {i: i for i in range(500)}

    def mutate():
        for i in range(500, 900):
            d[i] = i

    t = threading.Thread(target=mutate)
    t.start()
    try:
        # list() takes a snapshot; iterating d.items() directly is what raised
        for _ in list(d.items()):
            pass
    finally:
        t.join()


def test_iterating_live_is_what_fails():
    """Guards the guard: if this stops raising, the test above proves nothing."""
    d = {i: i for i in range(10)}
    with pytest.raises(RuntimeError, match="changed size"):
        for i, _ in d.items():
            d[100 + i] = i
