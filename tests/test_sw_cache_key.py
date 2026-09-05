"""The service worker cache key must change when the build does.

Regression: the key was the literal 'roadtrip-shell-v2'. The activate handler
deletes every cache whose key is not the current one, so a constant key meant
the old shell was never purged -- a republished site kept serving the previous
build indefinitely.

The symptoms did not look like one bug. Seed badges appeared on a trip whose
seeds had been removed, a freshly pushed site looked unpushed, and fixes
verified over HTTP appeared not to have landed in the browser. Each was
investigated as its own problem.
"""

import pathlib
import re
import tempfile

import pytest

from generator.main import _write_pwa_assets

TRIP = {"trip": {"title": "T", "subtitle": "s", "theme_color": "#3A5F8A"}}


def _cache_key(build_id, body="<html>x</html>"):
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "index.html").write_text(body, encoding="utf-8")
    _write_pwa_assets(d, TRIP, build_id=build_id)
    sw = (d / "sw.js").read_text(encoding="utf-8")
    match = re.search(r"const CACHE = '([^']+)'", sw)
    assert match, "sw.js has no CACHE constant"
    return match.group(1)


def test_two_builds_do_not_share_a_cache_key():
    assert _cache_key("run-A") != _cache_key("run-B")


def test_the_key_is_never_the_old_constant():
    for build_id in ("run-A", ""):
        assert _cache_key(build_id) != "roadtrip-shell-v2"


def test_no_build_id_falls_back_to_content_not_a_constant():
    """Absent a build id the key must still track the page, not go constant."""
    assert _cache_key("", body="<html>one</html>") != _cache_key("", body="<html>two</html>")


def test_same_content_and_no_build_id_is_stable():
    assert _cache_key("", body="<html>same</html>") == _cache_key("", body="<html>same</html>")


@pytest.mark.parametrize("dirty,expected_absent", [
    ("run/../../etc", "/"),
    ("run id with spaces", " "),
    ("run'quote", "'"),
])
def test_the_key_cannot_break_out_of_the_js_string(dirty, expected_absent):
    assert expected_absent not in _cache_key(dirty)


def test_activate_still_purges_other_caches():
    """The key only helps because activate deletes non-matching caches."""
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "index.html").write_text("<html>x</html>", encoding="utf-8")
    _write_pwa_assets(d, TRIP, build_id="run-A")
    sw = (d / "sw.js").read_text(encoding="utf-8")
    assert "keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))" in sw
