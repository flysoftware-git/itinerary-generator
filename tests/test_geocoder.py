"""The geocoder must not ask twice, and must not ask too fast.

Nominatim is free and rate-limited to one request per second. On 2026-09-06
two builds started seconds apart earned a block that outlasted the retry
ladder: the run died in stage 2, after paying for stage 1, and the retry
of that run re-fetched every coordinate the dead run had already resolved.

Three separate faults, one incident:

  - nothing spaced the requests, so the policy was broken by accident;
  - the cache lived on the class, so it died with the process and a rerun
    asked for everything again -- five European cities were fetched three
    times in one afternoon;
  - the backoff ladder totalled 90 seconds against a block that took about
    five minutes to clear, so waiting was never going to work.
"""

import json
import pathlib
import threading
import time

import pytest
from geopy.exc import GeocoderRateLimited

from generator import geocoder as geocoder_module
from generator.geocoder import Geocoder


class _FakeLocation:
    def __init__(self, lat, lng):
        self.latitude = lat
        self.longitude = lng


class _FakeGeolocator:
    """Counts calls and can be told to fail."""

    def __init__(self, coords=(1.5, 2.5), raises=None):
        self.calls = []
        self._coords = coords
        self._raises = list(raises or [])

    def geocode(self, query):
        self.calls.append(query)
        if self._raises:
            exc = self._raises.pop(0)
            if exc is not None:
                raise exc
        return _FakeLocation(*self._coords)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Every test gets its own cache file and a clean class state."""
    monkeypatch.setattr(
        geocoder_module, "CACHE_PATH", tmp_path / "geocode" / "coordinates.json"
    )
    Geocoder.clear_cache()
    Geocoder._last_request_at = None
    yield
    Geocoder.clear_cache()
    Geocoder._last_request_at = None


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    """Nothing in these tests should actually wait."""
    monkeypatch.setattr(geocoder_module.time, "sleep", lambda _s: None)


def _geocoder(fake):
    g = Geocoder.__new__(Geocoder)
    g.geolocator = fake
    Geocoder._load_cache()
    return g


# ── The cache survives the process ───────────────────────────────────────

def test_a_second_run_does_not_re_ask_for_a_coordinate_it_has():
    """The incident's most wasteful part: a rerun paid for every lookup again."""
    first = _FakeGeolocator(coords=(50.85, 4.35))
    assert _geocoder(first)._geocode("Brussels, Belgium") == (50.85, 4.35)
    assert len(first.calls) == 1

    # A new process: in-memory state gone, disk cache intact.
    Geocoder._cache = {}
    Geocoder._cache_loaded = False

    second = _FakeGeolocator(coords=(0.0, 0.0))
    assert _geocoder(second)._geocode("Brussels, Belgium") == (50.85, 4.35)
    assert second.calls == [], "asked Nominatim for a coordinate already on disk"


def test_the_cache_is_written_where_it_can_be_found():
    _geocoder(_FakeGeolocator(coords=(1.0, 2.0)))._geocode("Somewhere")
    written = json.loads(geocoder_module.CACHE_PATH.read_text(encoding="utf-8"))
    assert written == {"Somewhere": [1.0, 2.0]}


def test_a_corrupt_cache_is_a_miss_and_not_a_crash():
    """The worst a bad cache file may cost is the requests it would have saved."""
    geocoder_module.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    geocoder_module.CACHE_PATH.write_text("{not json", encoding="utf-8")
    Geocoder._cache = {}
    Geocoder._cache_loaded = False

    fake = _FakeGeolocator(coords=(3.0, 4.0))
    assert _geocoder(fake)._geocode("Somewhere") == (3.0, 4.0)
    assert len(fake.calls) == 1


def test_a_changed_hint_does_not_reuse_the_old_coordinate(monkeypatch):
    """The key is the query sent, not the name asked about."""
    fake = _FakeGeolocator(coords=(35.68, -105.93))
    _geocoder(fake)._geocode("Santa Fe")
    assert fake.calls == ["Santa Fe, New Mexico, USA"]

    monkeypatch.setitem(
        geocoder_module.GEOCODE_COUNTRY_HINTS, "santa fe", "Argentina"
    )
    again = _FakeGeolocator(coords=(-31.6, -60.7))
    assert _geocoder(again)._geocode("Santa Fe") == (-31.6, -60.7)
    assert again.calls == ["Santa Fe, Argentina"]


def test_a_name_with_no_results_is_not_cached():
    """A manifest typo must stay visible after it is fixed."""
    class _NoResults(_FakeGeolocator):
        def geocode(self, query):
            self.calls.append(query)
            return None

    fake = _NoResults()
    with pytest.raises(ValueError):
        _geocoder(fake)._geocode("Nowhere At All")
    assert not geocoder_module.CACHE_PATH.exists() or json.loads(
        geocoder_module.CACHE_PATH.read_text(encoding="utf-8")
    ) == {}


# ── Requests are spaced ──────────────────────────────────────────────────

def test_requests_are_spaced_by_at_least_the_policy_interval(monkeypatch):
    clock = {"t": 1000.0}
    slept = []
    monkeypatch.setattr(geocoder_module.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(geocoder_module.time, "sleep", lambda s: slept.append(s))

    fake = _FakeGeolocator()
    g = _geocoder(fake)
    g._geocode("A")
    g._geocode("B")

    assert slept, "second request went out with no wait at all"
    assert slept[-1] >= 1.0, f"waited only {slept[-1]}s between requests"


def test_the_interval_is_shared_across_instances(monkeypatch):
    """The limit belongs to the service, not to one Geocoder object."""
    clock = {"t": 500.0}
    slept = []
    monkeypatch.setattr(geocoder_module.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(geocoder_module.time, "sleep", lambda s: slept.append(s))

    _geocoder(_FakeGeolocator())._geocode("A")
    _geocoder(_FakeGeolocator())._geocode("B")

    assert slept and slept[-1] >= 1.0


def test_the_first_request_of_a_build_never_waits(monkeypatch):
    """"Never asked" is not "asked at time zero"."""
    slept = []
    monkeypatch.setattr(geocoder_module.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(geocoder_module.time, "sleep", lambda s: slept.append(s))
    _geocoder(_FakeGeolocator())._geocode("A")
    assert slept == [], "the first lookup of the build slept for nothing"


def test_a_slow_request_has_already_paid_the_interval(monkeypatch):
    """The floor is between request STARTS, not a sleep after each one."""
    clock = {"t": 0.0}
    slept = []
    monkeypatch.setattr(geocoder_module.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(geocoder_module.time, "sleep", lambda s: slept.append(s))

    fake = _FakeGeolocator()
    g = _geocoder(fake)
    g._geocode("A")
    clock["t"] = 30.0  # that request took half a minute
    g._geocode("B")

    assert not slept, "slept after a request that already outlasted the interval"


def test_a_cache_hit_costs_no_wait(monkeypatch):
    slept = []
    monkeypatch.setattr(geocoder_module.time, "sleep", lambda s: slept.append(s))
    fake = _FakeGeolocator(coords=(9.0, 9.0))
    g = _geocoder(fake)
    g._geocode("A")
    slept.clear()
    g._geocode("A")
    assert slept == [], "throttled a request that was never made"
    assert len(fake.calls) == 1


# ── The backoff outlasts a real block ────────────────────────────────────

def test_the_ladder_waits_longer_than_the_block_that_prompted_it():
    """The old 15/30/45 gave up after 90s; the block took about five minutes."""
    assert sum(geocoder_module.RATE_LIMIT_BACKOFF_SECONDS) >= 300


def test_it_retries_once_per_rung_and_then_raises(monkeypatch):
    slept = []
    monkeypatch.setattr(geocoder_module.time, "sleep", lambda s: slept.append(s))
    rungs = len(geocoder_module.RATE_LIMIT_BACKOFF_SECONDS)

    fake = _FakeGeolocator(raises=[GeocoderRateLimited("429")] * (rungs + 1))
    with pytest.raises(GeocoderRateLimited):
        _geocoder(fake)._geocode("Brussels, Belgium")

    assert len(fake.calls) == rungs + 1
    backoffs = [s for s in slept if s in geocoder_module.RATE_LIMIT_BACKOFF_SECONDS]
    assert backoffs == list(geocoder_module.RATE_LIMIT_BACKOFF_SECONDS)


def test_a_block_that_clears_lets_the_build_continue(monkeypatch):
    monkeypatch.setattr(geocoder_module.time, "sleep", lambda _s: None)
    fake = _FakeGeolocator(
        coords=(50.85, 4.35),
        raises=[GeocoderRateLimited("429"), GeocoderRateLimited("429"), None],
    )
    assert _geocoder(fake)._geocode("Brussels, Belgium") == (50.85, 4.35)


# ── Concurrency ──────────────────────────────────────────────────────────

def test_a_slow_write_cannot_land_an_older_snapshot_on_top_of_a_newer_one(monkeypatch):
    """The lost update, made deterministic.

    `_remember` used to snapshot the cache under `_cache_lock`, release it, and
    then write -- so two threads could serialise their *writes* in the opposite
    order to their *snapshots*, and the older snapshot landed last. Every entry
    it did not contain vanished from the file while sitting correctly in
    memory, so a build looked up eight places and cached seven.

    The sibling test below catches this at about one run in six, which is the
    worst rate a test can have: often enough to be seen, rarely enough to be
    called flaky and re-run. This one makes it certain by holding the first
    writer inside the write for as long as the second one needs -- the delay
    changes the timing, not the arithmetic, and the arithmetic is what was
    wrong.

    It also covers the second half. Every thread wrote the same
    `place_coords.json.tmp`, so two of them could interleave bytes into one file
    and `replace` could move a half-written one into place -- the corruption
    this module's other test is named for. Serialising the write fixes both,
    which is why they are one fix and one test.
    """
    fake = _FakeGeolocator(coords=(1.0, 1.0))
    g = _geocoder(fake)

    real_write = pathlib.Path.write_text
    first = threading.Event()
    released = threading.Event()

    def slow_write(self, *args, **kwargs):
        # The first writer parks inside the write, which is exactly the window
        # the old code left open between snapshotting and landing the file.
        if not first.is_set():
            first.set()
            released.wait(timeout=5.0)
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", slow_write)

    slow = threading.Thread(target=lambda: g._geocode("Place 0"))
    slow.start()
    assert first.wait(timeout=5.0), "the first write never started"

    # A second lookup completes entirely while the first is still inside its
    # write. Without the fix it snapshots two entries, writes them, and is then
    # overwritten by the first thread's one-entry snapshot.
    quick = threading.Thread(target=lambda: g._geocode("Place 1"))
    quick.start()
    # It may finish (the old code let it write straight past the first) or it
    # may block on the write lock (the fix). Either is fine; what matters is
    # that both have finished before the file is read, which is why it is
    # joined again after the release rather than only here.
    quick.join(timeout=1.0)

    released.set()
    slow.join(timeout=5.0)
    quick.join(timeout=5.0)
    assert not slow.is_alive() and not quick.is_alive(), "a writer never finished"

    written = json.loads(geocoder_module.CACHE_PATH.read_text(encoding="utf-8"))
    assert set(written) == {"Place 0", "Place 1"}, (
        "an older snapshot landed on top of a newer one, so a place that was "
        f"looked up is not in the cache: {sorted(written)}"
    )


#: Enough writers, released together, to make the unsynchronised window
#: certain rather than lucky. See the test below for why the number matters.
_RACE_THREADS = 16
_RACE_ROUNDS = 6

#: Every writer pauses this long *inside* its write. It widens the window the
#: unfixed code left between snapshotting the cache and landing the file; it
#: does not change what either version computes, and the arithmetic was what
#: was wrong. Waited on an Event rather than slept: the autouse
#: `_no_real_sleeping` fixture replaces `time.sleep` on the shared `time`
#: module, so a sleep here would not happen at all.
_WRITE_PAUSE_SECONDS = 0.002


def test_concurrent_lookups_do_not_corrupt_the_cache_file(monkeypatch):
    """Every coordinate resolved concurrently is in the file afterwards.

    This test named the corruption it was guarding against and could not
    detect it. Measured on 2026-09-09 against `_remember` reverted to its
    pre-fix form -- the snapshot taken under `_cache_lock`, released, then
    written with no write lock and a temporary path every thread shared --
    the previous version of this test was **red 0 times in 20**. It had
    reported the defect exactly once, during a full-suite run under machine
    load, as `assert 6 == 8`, and passed three times in a row when re-run
    alone. A test that needs an overloaded machine to notice a lost update is
    a lottery ticket rather than an instrument, and that single sighting was
    the only work it ever did.

    Three changes make the window reliable instead of lucky:

      - a `threading.Barrier`, so the writers contend rather than queue --
        eight threads started in a loop mostly finish in the order they were
        started, which is the one ordering that cannot lose an update;
      - more writers and repeated rounds, because one lost entry anywhere in
        the run is a failure and the chances compound;
      - a pause inside every write, widening the gap between a thread's
        snapshot and its `replace` to something a second thread can fit in.

    What is asserted is the property, not a count: the file must parse, and
    the set of names in it must be exactly the set that was looked up. A
    missing name is the lost update; a file that will not parse is the torn
    temporary file, which is the worse half and the one this test is named
    for.
    """
    real_write = pathlib.Path.write_text

    def paused_write(self, *args, **kwargs):
        threading.Event().wait(_WRITE_PAUSE_SECONDS)
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", paused_write)

    for round_index in range(_RACE_ROUNDS):
        Geocoder.clear_cache()
        g = _geocoder(_FakeGeolocator(coords=(1.0, 1.0)))
        expected = {f"Place {i}" for i in range(_RACE_THREADS)}
        gate = threading.Barrier(_RACE_THREADS)
        errors: list[Exception] = []

        def work(i):
            try:
                gate.wait(timeout=5.0)
                g._geocode(f"Place {i}")
            except Exception as exc:  # pragma: no cover - failure detail
                errors.append(exc)

        threads = [
            threading.Thread(target=work, args=(i,)) for i in range(_RACE_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        assert not [t for t in threads if t.is_alive()], "a lookup never finished"
        assert not errors, f"a concurrent lookup raised: {errors[0]!r}"

        raw = geocoder_module.CACHE_PATH.read_text(encoding="utf-8")
        try:
            written = json.loads(raw)
        except ValueError as exc:
            raise AssertionError(
                "the cache file does not parse, so two writers interleaved "
                f"bytes into the shared temporary path (round {round_index}): {exc}"
            ) from exc

        missing = expected - set(written)
        assert not missing, (
            "a coordinate that was looked up is not in the cache file, so an "
            "older snapshot landed on top of a newer one (round "
            f"{round_index}, {len(missing)} of {_RACE_THREADS} lost): "
            f"{sorted(missing)}"
        )
