"""The service worker serves the saved guide when the server answers badly.

The navigation handler went to the network first and fell back to the cache
only when fetch REJECTED -- which is what being offline looks like. A server
that answered with an error had that error shown in place of the guide already
saved on the device: a 5xx from a host having a bad minute, a 403 from a front
door whose session had lapsed, a 404 from a guide moved while the reader was
away. For an installed guide, that is a trip companion that stops opening while
a good copy sits unused in its own cache.

These tests EXECUTE the generated `sw.js` under Node, with `fetch` and `caches`
stubbed, rather than reading its text: a check on the source proves what the
code says, not what it does. They skip cleanly where Node is not installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from generator import main as main_mod

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="executing the service worker needs Node")

#: Loads sw.js with a fake `self`, `caches` and `fetch`, dispatches one
#: navigation, and prints what `respondWith` resolved to as JSON.
HARNESS = r"""
const fs = require('fs');
const [swPath, scenarioJson] = process.argv.slice(2);
const scenario = JSON.parse(scenarioJson);

const listeners = {};
const stored = new Map(Object.entries(scenario.cached || {}));
const self = {
  location: { origin: 'https://guide.example' },
  addEventListener: (type, fn) => { listeners[type] = fn; },
  skipWaiting: () => {},
  clients: { claim: () => Promise.resolve() },
};
const cacheApi = {
  put: (req, res) => { stored.set(typeof req === 'string' ? req : req.url, res); return Promise.resolve(); },
  addAll: () => Promise.resolve(),
};
const caches = {
  open: () => Promise.resolve(cacheApi),
  match: (req) => Promise.resolve(stored.get(typeof req === 'string' ? req : req.url)),
  keys: () => Promise.resolve([]),
  delete: () => Promise.resolve(true),
};
function answer(status, body) {
  return { ok: status >= 200 && status < 300, status, body, clone() { return this; } };
}
for (const key of Object.keys(scenario.cached || {})) {
  stored.set(key, answer(200, scenario.cached[key]));
}
const fetch = () => scenario.network === 'offline'
  ? Promise.reject(new TypeError('Failed to fetch'))
  : Promise.resolve(answer(scenario.network.status, scenario.network.body));

new Function('self', 'caches', 'fetch', fs.readFileSync(swPath, 'utf8'))(self, caches, fetch);

const url = scenario.url || 'https://guide.example/trip/index.html';
listeners.fetch({
  request: { url, mode: 'navigate', method: 'GET', destination: 'document' },
  respondWith: (p) => Promise.resolve(p).then((res) => {
    process.stdout.write(JSON.stringify({ status: res && res.status, body: res && res.body }));
  }),
});
"""

URL = "https://guide.example/trip/index.html"


@pytest.fixture
def sw(tmp_path):
    main_mod._write_pwa_assets(tmp_path, {"trip": {"title": "Test Trip"}}, build_id="t1")
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    return tmp_path / "sw.js", harness


def _navigate(sw, network, cached=None, url=None):
    sw_path, harness = sw
    scenario = json.dumps({"network": network, "cached": cached or {}, "url": url})
    done = subprocess.run(["node", str(harness), str(sw_path), scenario],
                          capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.parametrize("status", [403, 500, 503, 404])
def test_a_bad_answer_serves_the_copy_already_saved(sw, status):
    """The defect: the error was shown and the saved guide ignored."""
    got = _navigate(sw, {"status": status, "body": "error page"},
                    cached={URL: "the saved guide"})
    assert got == {"status": 200, "body": "the saved guide"}, got


def test_a_bad_answer_with_nothing_saved_still_says_why(sw):
    """A first visit that fails must not pretend: with no copy there is nothing
    better to show than the error itself."""
    got = _navigate(sw, {"status": 403, "body": "not allowed"})
    assert got == {"status": 403, "body": "not allowed"}, got


def test_a_good_answer_is_still_the_one_shown(sw):
    """Network first is unchanged: a republished guide must win over the copy,
    which is why the handler went network-first to begin with."""
    got = _navigate(sw, {"status": 200, "body": "the new guide"},
                    cached={URL: "the old guide"})
    assert got == {"status": 200, "body": "the new guide"}, got


def test_offline_still_serves_the_copy(sw):
    """The path that already worked, kept as a control."""
    got = _navigate(sw, "offline", cached={URL: "the saved guide"})
    assert got == {"status": 200, "body": "the saved guide"}, got


# -- a link that carries a query string ------------------------------------
#
# `caches.match(request)` compares the whole URL, query included, so a guide
# opened from a shared link -- `?utm_source=...`, or a cache-buster -- never
# matches the copy saved under its bare address. The offline path already
# knew this and fell back to `./index.html`, the shell every install
# precaches. The bad-answer path, as first written, stopped at the exact
# match: the same reader, the same saved guide, a 503 instead of a dropped
# connection, and they got the error.

SHARED = "https://guide.example/trip/index.html?utm_source=message"


def test_offline_with_a_query_string_falls_back_to_the_shell(sw):
    """Control: the offline path's second tier, which already worked."""
    got = _navigate(sw, "offline", cached={"./index.html": "the saved guide"}, url=SHARED)
    assert got == {"status": 200, "body": "the saved guide"}, got


def test_a_bad_answer_with_a_query_string_falls_back_to_the_shell_too(sw):
    """Seen red with the error path stopping at the exact match."""
    got = _navigate(sw, {"status": 503, "body": "error page"},
                    cached={"./index.html": "the saved guide"}, url=SHARED)
    assert got == {"status": 200, "body": "the saved guide"}, got
