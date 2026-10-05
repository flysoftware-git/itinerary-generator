"""The routing cache is parsed once per version of the file, not once per leg.

Every leg lookup used to read and parse the whole cache file again. On a trip of
thirty legs, each asked about more than once, a 2 MB cache was parsed over a
hundred times to draw one page: 11.3 s of a 15.7 s first view, with no router
call at all. The file only changes when a route is saved.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from generator import routing


def _write(path: Path, contents: dict) -> None:
    path.write_text(json.dumps(contents), encoding="utf-8")


def _count_reads(monkeypatch):
    reads = []
    real = Path.read_text

    def counting(self, *args, **kwargs):
        if self.name == "routes.json":
            reads.append(self)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    return reads


def test_an_unchanged_file_is_read_once(tmp_path, monkeypatch):
    """Seen red with `_load_cache` reading the file on every call."""
    cache = tmp_path / "routes.json"
    _write(cache, {"a": {"miles": 1}})
    reads = _count_reads(monkeypatch)
    for _ in range(5):
        assert routing._load_cache(cache) == {"a": {"miles": 1}}
    assert len(reads) == 1, f"parsed {len(reads)} times for one version of the file"


def test_a_write_by_anyone_is_noticed(tmp_path):
    """Another process saving a route changes the file's time and size."""
    cache = tmp_path / "routes.json"
    _write(cache, {"a": {"miles": 1}})
    assert "b" not in routing._load_cache(cache)
    time.sleep(0.01)
    _write(cache, {"a": {"miles": 1}, "b": {"miles": 2}})
    os.utime(cache, ns=(time.time_ns(), time.time_ns()))
    assert routing._load_cache(cache)["b"] == {"miles": 2}


def test_a_callers_edit_does_not_reach_the_next_reader(tmp_path):
    """Seen red with `_load_cache` handing out the held dict itself."""
    cache = tmp_path / "routes.json"
    _write(cache, {"a": {"miles": 1}})
    first = routing._load_cache(cache)
    first["phantom"] = {"miles": 99}
    assert "phantom" not in routing._load_cache(cache)


def test_a_save_is_what_the_next_load_returns(tmp_path, monkeypatch):
    cache = tmp_path / "routes.json"
    routing._save_cache(cache, {"a": {"miles": 1}})
    reads = _count_reads(monkeypatch)
    assert routing._load_cache(cache) == {"a": {"miles": 1}}
    assert reads == [], "the file just written was parsed again"
