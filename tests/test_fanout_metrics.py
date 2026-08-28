"""Tests for the fan-out scheduling instrumentation.

The instrument's whole value is that its numbers can be trusted, so most of
these assert the bounds rather than exact timings: a branch cannot outlast its
block, utilisation cannot exceed 1.0, and a skipped branch must never be
recorded as a zero-second success.
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


# ── BranchSpans ──────────────────────────────────────────────────────────────


def test_branch_spans_identify_the_critical_path():
    spans = BranchSpans()
    with pool("test_branches", 3) as p:
        futures = [
            p.submit(spans.wrap("slow", lambda: time.sleep(0.15))),
            p.submit(spans.wrap("fast", lambda: time.sleep(0.01))),
            p.submit(spans.wrap("faster", lambda: None)),
        ]
        for f in as_completed(futures):
            f.result()

    summary = spans.summary()
    assert summary["critical_path"] == "slow"
    assert summary["branches"]["slow"]["duration_seconds"] >= 0.15
    assert summary["branches"]["slow"]["status"] == "ok"
    assert not summary["bounds_violated"]


def test_straggler_margin_is_the_gap_to_the_next_longest():
    """The number that decides whether fixing the straggler is worth anything."""
    spans = BranchSpans()
    with pool("test_margin", 2) as p:
        futures = [
            p.submit(spans.wrap("slow", lambda: time.sleep(0.20))),
            p.submit(spans.wrap("quick", lambda: time.sleep(0.02))),
        ]
        for f in as_completed(futures):
            f.result()

    summary = spans.summary()
    # ~0.18s of gap; generous bounds so this does not flake on a loaded machine.
    assert 0.10 <= summary["straggler_margin_seconds"] <= 0.30


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


def test_queue_wait_separates_a_small_pool_from_a_slow_provider():
    """The split that stops one fused duration arguing both hypotheses."""
    with pool("test_wait", 1) as p:
        futures = [p.submit(time.sleep, 0.06) for _ in range(3)]
        for f in as_completed(futures):
            f.result()

    stats = fanout_metrics.pool_summary()["test_wait"]
    # Three 60ms tasks through one worker: the last one queues for ~120ms.
    assert stats["queue_wait_seconds"]["max"] >= 0.09
    # Service time is per task and must not have absorbed the queueing.
    assert stats["service_seconds"]["max"] < 0.09


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
