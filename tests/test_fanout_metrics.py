"""Tests for the fan-out scheduling instrumentation.

The instrument's whole value is that its numbers can be trusted, so most of
these assert the bounds rather than exact timings: a branch cannot outlast its
block, utilisation cannot exceed 1.0, and a skipped branch must never be
recorded as a zero-second success.

Where a test is about a *number* or an *ordering* rather than a bound, it drives
the clock instead of the machine -- see `_ScriptedClock` and `_PerThreadClock`
below. A wall-clock assertion on a busy machine is an assertion about the
machine, and a suite that can go red for reasons unrelated to the code makes
every red an investigation before it is a signal.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import as_completed

import pytest

from generator import fanout_metrics
from generator.fanout_metrics import BranchSpans, pool


@pytest.fixture(autouse=True)
def _clean_registry():
    fanout_metrics.reset()
    yield
    fanout_metrics.reset()


class _ScriptedClock:
    """A clock the test moves, so the numbers are the test's and not the load's.

    `fanout_metrics` reads time through the module-global `perf_counter`, so
    replacing that name is enough -- both `BranchSpans` and `InstrumentedPool`
    go through it, and no production code needs a seam of its own.

    Reading never advances it. The only mover is whoever calls `advance`, and a
    threaded test parks every worker on a gate before touching it, which is what
    makes such a test deterministic rather than merely usually right.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.now = 0.0

    def __call__(self) -> float:
        with self._lock:
            return self.now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.now += seconds


class _PerThreadClock:
    """A scripted clock with one timeline per thread.

    For tests whose branches run concurrently in a real pool and each need to
    take a known amount of time: a thread's `advance` moves only its own
    reading, so no branch's duration can be stretched or shrunk by another
    branch, by the scheduler, or by the machine. Every thread starts at 0.0.
    """

    def __init__(self) -> None:
        self._local = threading.local()

    def __call__(self) -> float:
        return getattr(self._local, "now", 0.0)

    def advance(self, seconds: float) -> None:
        self._local.now = self() + seconds


# ── BranchSpans ──────────────────────────────────────────────────────────────


def test_branch_spans_identify_the_critical_path(monkeypatch):
    """The slowest branch is named, and its duration is the one it took.

    Driven by a per-thread clock rather than by sleeps. The old form slept
    0.15s and 0.01s in a three-worker pool and asserted the ordering, which a
    loaded scheduler can invert: a 0.01s sleep that is descheduled for longer
    than the 0.15s one wins the critical path. The pool is still real -- what is
    under test is that spans recorded from worker threads land under the right
    names -- but each branch advances only its own thread's time, so the
    durations are exact.
    """
    clock = _PerThreadClock()
    monkeypatch.setattr(fanout_metrics, "perf_counter", clock)

    spans = BranchSpans()
    with pool("test_branches", 3) as p:
        futures = [
            p.submit(spans.wrap("slow", lambda: clock.advance(0.15))),
            p.submit(spans.wrap("fast", lambda: clock.advance(0.01))),
            p.submit(spans.wrap("faster", lambda: None)),
        ]
        for f in as_completed(futures):
            f.result()

    summary = spans.summary()
    assert summary["critical_path"] == "slow"
    assert summary["branches"]["slow"]["duration_seconds"] == 0.15
    assert summary["branches"]["fast"]["duration_seconds"] == 0.01
    assert summary["branches"]["faster"]["duration_seconds"] == 0.0
    assert summary["branches"]["slow"]["status"] == "ok"
    assert not summary["bounds_violated"]


def test_straggler_margin_is_the_gap_to_the_next_longest(monkeypatch):
    """The number that decides whether fixing the straggler is worth anything.

    On the clock rather than on the wall. The old form slept 0.20 and 0.02 in a
    two-worker pool and accepted a margin anywhere in `0.10 .. 0.30`, with a
    comment saying the bounds were "generous so this does not flake on a loaded
    machine". The upper bound is 0.10s of headroom on a 0.20s sleep, on machines
    where a 0.06s sleep has been seen to take 0.101s.

    The margin is arithmetic over recorded spans, so the pool was never part of
    what it asserts -- the spans are recorded here by calling the wrapped
    callables directly, which removes the threads as well as the wall. What the
    pool does to spans is asserted by the critical-path test above and by
    `test_concurrent_pools_do_not_corrupt_each_other`.
    """
    clock = _ScriptedClock()
    monkeypatch.setattr(fanout_metrics, "perf_counter", clock)

    spans = BranchSpans()
    spans.wrap("slow", lambda: clock.advance(0.20))()
    spans.wrap("quick", lambda: clock.advance(0.02))()

    summary = spans.summary()
    assert summary["branches"]["slow"]["duration_seconds"] == 0.20
    assert summary["branches"]["quick"]["duration_seconds"] == 0.02
    assert summary["straggler_margin_seconds"] == 0.18
    assert summary["critical_path"] == "slow"


def test_a_skipped_branch_is_not_a_zero_second_success():
    """The trap that let a never-invoked subsystem look healthy."""
    spans = BranchSpans()
    with pool("test_skip", 2) as p:
        futures = [
            p.submit(spans.wrap("images", lambda: None, skipped=True)),
            p.submit(spans.wrap("urls", lambda: time.sleep(0.02))),
        ]
        for f in as_completed(futures):
            f.result()

    summary = spans.summary()
    assert summary["branches"]["images"]["status"] == "skipped"
    # A skipped branch must not be able to win the critical path by default.
    assert summary["critical_path"] == "urls"


def test_a_failed_branch_keeps_the_time_it_burned():
    spans = BranchSpans()

    def _boom():
        time.sleep(0.05)
        raise RuntimeError("provider exploded")

    with pytest.raises(RuntimeError):
        with pool("test_fail", 1) as p:
            p.submit(spans.wrap("urls", _boom)).result()

    span = spans.summary()["branches"]["urls"]
    assert span["status"] == "failed"
    assert span["duration_seconds"] >= 0.05


def test_branch_cannot_outlast_the_block_it_reports_against():
    spans = BranchSpans()
    with pool("test_bounds", 1) as p:
        p.submit(spans.wrap("only", lambda: time.sleep(0.05))).result()

    # A wall time smaller than the branch means the block was measured around
    # the wrong thing; the record must say so rather than absorb it.
    assert spans.summary(wall_seconds=0.001)["bounds_violated"] is True
    assert spans.summary(wall_seconds=10.0)["bounds_violated"] is False


def test_summary_of_an_empty_fanout_is_inert():
    summary = BranchSpans().summary()
    assert summary["branches"] == {}
    assert summary["critical_path"] is None
    assert summary["straggler_margin_seconds"] == 0.0


# ── Pool statistics ──────────────────────────────────────────────────────────


def test_pool_records_tasks_and_bounded_utilisation():
    with pool("test_util", 2) as p:
        futures = [p.submit(time.sleep, 0.05) for _ in range(4)]
        for f in as_completed(futures):
            f.result()

    stats = fanout_metrics.pool_summary()["test_util"]
    assert stats["tasks"] == 4
    assert stats["max_workers"] == 2
    assert stats["instances"] == 1
    assert stats["busy_worker_seconds"] >= 0.20
    assert 0.0 < stats["utilisation"] <= 1.0
    assert not stats["bounds_violated"]


def test_queue_wait_separates_a_small_pool_from_a_slow_provider(monkeypatch):
    """The split that stops one fused duration arguing both hypotheses.

    Measured against injected time rather than against the wall. The property
    is real and worth holding -- a small pool and a slow provider must produce
    distinguishable queue waits -- but the old form asserted it with three
    `time.sleep(0.06)` calls and `service_seconds["max"] < 0.09`, which is an
    assertion that a 60ms sleep does not overrun by half on a busy machine. It
    does: the test has failed in a long full-suite run and passed alone on the
    same tree.

    Widening the tolerance would buy a quieter instrument rather than a correct
    one, and `skip` would remove the property. Driving the clock keeps the
    property and removes the machine from the assertion, so the numbers below
    are exact rather than bounded: three 60ms tasks through one worker wait 0s,
    60ms and 120ms, and each is served in 60ms.

    The gate is what makes it deterministic. Every task parks on `release`
    before it touches the clock, so all three `submit` times and the first
    `start` time are read at 0.0 while nothing can advance it; after `release`
    the single worker runs them strictly in order and each advance is its own.
    """
    clock = _ScriptedClock()
    monkeypatch.setattr(fanout_metrics, "perf_counter", clock)
    release = threading.Event()

    def a_slow_provider():
        assert release.wait(30), "the gate was never released"
        clock.advance(0.06)

    with pool("test_wait", 1) as p:
        futures = [p.submit(a_slow_provider) for _ in range(3)]
        release.set()
        for f in as_completed(futures):
            f.result()

    stats = fanout_metrics.pool_summary()["test_wait"]
    assert stats["tasks"] == 3
    # Three 60ms tasks through one worker: the last one queues for 120ms.
    assert stats["queue_wait_seconds"]["max"] == 0.12
    assert stats["queue_wait_seconds"]["p50"] == 0.06
    # Service time is per task and must not have absorbed the queueing.
    assert stats["service_seconds"]["max"] == 0.06
    assert stats["service_seconds"]["p50"] == 0.06
    assert stats["busy_worker_seconds"] == 0.18


def test_repeated_instances_of_one_pool_accumulate_into_one_row():
    """URL discovery builds its category pool once per destination."""
    for _ in range(3):
        with pool("test_repeat", 2) as p:
            p.submit(lambda: None).result()

    stats = fanout_metrics.pool_summary()["test_repeat"]
    assert stats["instances"] == 3
    assert stats["tasks"] == 3
    assert stats["utilisation"] <= 1.0


def test_failures_are_counted_without_losing_the_task():
    def _boom():
        raise ValueError("nope")

    with pool("test_failures", 1) as p:
        future = p.submit(_boom)
        with pytest.raises(ValueError):
            future.result()

    stats = fanout_metrics.pool_summary()["test_failures"]
    assert stats["tasks"] == 1
    assert stats["failures"] == 1


def test_sample_truncation_is_reported_not_hidden():
    cap = fanout_metrics._MAX_SAMPLES_PER_POOL
    with pool("test_truncate", 4) as p:
        futures = [p.submit(lambda: None) for _ in range(cap + 5)]
        for f in as_completed(futures):
            f.result()

    stats = fanout_metrics.pool_summary()["test_truncate"]
    assert stats["tasks"] == cap + 5
    assert stats["samples_truncated"] is True


def test_reset_clears_statistics_between_runs():
    with pool("test_reset", 1) as p:
        p.submit(lambda: None).result()
    assert "test_reset" in fanout_metrics.pool_summary()

    fanout_metrics.reset()
    assert fanout_metrics.pool_summary() == {}


def test_wrapper_does_not_change_executor_semantics():
    """Futures, ordering and exception propagation must behave as before."""
    with pool("test_semantics", 3) as p:
        futures = {p.submit(lambda v=v: v * 2): v for v in range(5)}
        results = sorted(f.result() for f in as_completed(futures))
    assert results == [0, 2, 4, 6, 8]


def test_concurrent_pools_do_not_corrupt_each_other():
    def _work(name: str) -> None:
        with pool(name, 2) as p:
            for f in as_completed([p.submit(time.sleep, 0.01) for _ in range(4)]):
                f.result()

    threads = [
        threading.Thread(target=_work, args=(f"test_concurrent_{i}",))
        for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    summary = fanout_metrics.pool_summary()
    for i in range(4):
        assert summary[f"test_concurrent_{i}"]["tasks"] == 4


# ── Percentiles ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "values,fraction,expected",
    [
        ([], 0.5, 0.0),
        ([1.0], 0.9, 1.0),
        ([1.0, 2.0, 3.0, 4.0, 5.0], 0.5, 3.0),
        ([1.0, 2.0, 3.0, 4.0, 5.0], 0.9, 5.0),
    ],
)
def test_percentile_edges(values, fraction, expected):
    assert fanout_metrics._percentile(values, fraction) == expected


# ── The pipeline stays instrumented ──────────────────────────────────────────


def test_no_pipeline_pool_escapes_instrumentation():
    """A pool added later without a name is a hole in the decomposition.

    The failure this guards against is silent: the run still works, the ledger
    still renders, and the new pool's time simply does not appear anywhere. That
    is the same shape as the cost prefixes that went unrecognised, so it gets a
    test rather than a convention.
    """
    import pathlib
    import re

    package = pathlib.Path(__file__).resolve().parent.parent / "generator"
    offenders = []
    for path in sorted(package.glob("*.py")):
        if path.name == "fanout_metrics.py":  # the wrapper owns the real executor
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bThreadPoolExecutor\s*\(", line):
                offenders.append(f"{path.name}:{number}")

    assert not offenders, (
        "these pools are not instrumented -- construct them with "
        f"fanout_metrics.pool(name, workers): {offenders}"
    )
