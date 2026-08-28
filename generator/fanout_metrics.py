"""Scheduling instrumentation for the pipeline's thread pools.

WHY THIS EXISTS
---------------
A run's *cost* is measured to the cent: every LLM and search call is tagged with
an operation prefix and rolled up per stage. A run's *schedule* is measured with
six numbers, one per stage, and one of those -- ``stage_4_5_parallel`` -- covers a
three-branch fan-out over roughly twenty network-bound workers.

So when the parallel block takes 700 seconds, nothing in ``run_ledger.jsonl`` can
distinguish "all three branches took 700s" from "URL discovery took 700s while
events and images finished in 40". Those two worlds call for entirely different
work, and the usual assumption that URL discovery dominates is an inference from
*cost* share, not a measurement of *time* share. A cheap cached branch can still
be the straggler.

Underneath that sit nine thread pools whose worker caps (3, 4, 8) are all
plausible constants that have never been measured against their own utilisation.
Raising a cap that is already idle buys nothing; leaving a cap that is pinned at
100% in place leaves the run's duration on the table. Neither can be told apart
without a busy integral.

WHAT IT MEASURES
----------------
``BranchSpans``  -- per-branch start offset, duration and status within one
                    fan-out, plus the derived critical path, the straggler margin
                    (what fixing *only* the slowest branch would recover) and the
                    idle worker-seconds bought and not used.

``pool(...)``    -- a drop-in ``ThreadPoolExecutor`` wrapper accumulating, per
                    named pool: task count, busy worker-seconds, capacity
                    worker-seconds, utilisation, and the split between time a
                    task spent *queued* and time it spent *running*. That split
                    is the point: it separates "the pool is too small" from "the
                    provider is slow", which one fused duration will otherwise be
                    used to argue both ways.

WHAT IT COSTS
-------------
Two ``perf_counter()`` reads and one short lock acquisition per task. Tasks here
are network-bound and measured in seconds, so the overhead is not observable
against ``total_pipeline``. Nothing in this module changes scheduling: the
wrapper submits to a real ``ThreadPoolExecutor`` and returns its real ``Future``,
so ``as_completed`` and ``.result()`` behave exactly as before.

HONESTY
-------
Two of the most expensive instrument failures in this project's history -- web
search fees excluded from run cost, and a whole class of spend excluded from
stage cost because an operation prefix was not recognised -- were both
*unmeasured residual presented as a total*. The same trap is available here, so:

* ``skipped`` branches are recorded as skipped, never as a zero-second success;
* ``failed`` branches keep the duration they burned before raising;
* utilisation above 1.0 is impossible and would mean the probe is racing --
  ``bounds_violated`` says so in the record rather than clamping it away;
* percentile samples are capped, and ``samples_truncated`` says when that
  happened rather than quietly reporting a percentile of a prefix.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from time import perf_counter
from typing import Any, Callable, Iterator

# Percentiles are computed from retained samples. A ten-destination run submits
# a few hundred tasks to the busiest pool, so this is generous -- but the audit
# prewarm is unbounded in principle, and an instrument that grows without limit
# on a big manifest is a memory leak wearing a lab coat.
_MAX_SAMPLES_PER_POOL = 2000


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile. No numpy: this must not add a dependency."""
    if not sorted_values:
        return 0.0
    index = int(round(fraction * (len(sorted_values) - 1)))
    return sorted_values[max(0, min(index, len(sorted_values) - 1))]


class BranchSpans:
    """Timings for the named branches of one fan-out.

    Usage mirrors how the branches are already written -- one callable per
    branch -- so instrumenting a block is wrapping the callables, not
    restructuring the block::

        spans = BranchSpans()
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(spans.wrap(name, fn, skipped=flag))
                       for name, fn, flag in branches]
            ...
        runtime_metrics["stage_4_5_branches"] = spans.summary()
    """

    def __init__(self) -> None:
        self._origin = perf_counter()
        self._lock = threading.Lock()
        self._spans: dict[str, dict[str, Any]] = {}

    def wrap(
        self,
        name: str,
        fn: Callable[[], Any],
        *,
        skipped: bool = False,
    ) -> Callable[[], Any]:
        """Return ``fn`` wrapped so its span is recorded under ``name``.

        ``skipped`` is passed by the caller rather than inferred, because a
        skipped branch still runs its callable (which echoes "SKIPPED" and
        returns immediately). Timing alone cannot tell that apart from a branch
        that genuinely had no work, and conflating them is exactly how a
        never-invoked subsystem comes to look healthy.
        """

        def _instrumented() -> Any:
            started = perf_counter()
            status = "ok"
            try:
                return fn()
            except BaseException:
                status = "failed"
                raise
            finally:
                ended = perf_counter()
                with self._lock:
                    self._spans[name] = {
                        "start_offset_seconds": round(max(0.0, started - self._origin), 3),
                        "duration_seconds": round(max(0.0, ended - started), 3),
                        "status": "skipped" if skipped and status == "ok" else status,
                    }

        return _instrumented

    def summary(self, *, wall_seconds: float | None = None) -> dict[str, Any]:
        """The record written to the ledger.

        ``wall_seconds`` is the block's own measured wall time. When given it is
        used for the idle calculation and bounds-checked against the branches;
        when omitted the longest branch stands in for it.
        """
        with self._lock:
            spans = {name: dict(span) for name, span in self._spans.items()}

        timed = {
            name: span
            for name, span in spans.items()
            if span["status"] != "skipped"
        }
        durations = sorted(
            (span["duration_seconds"] for span in timed.values()), reverse=True
        )
        longest = durations[0] if durations else 0.0
        wall = float(wall_seconds) if wall_seconds is not None else longest

        critical_path = None
        if timed:
            critical_path = max(timed, key=lambda n: timed[n]["duration_seconds"])

        # What fixing ONLY the slowest branch would recover. If it is near zero
        # the branches finish together and the straggler is not the problem --
        # which is a finding, and the reason this is reported rather than just
        # the critical path's name.
        straggler_margin = longest - durations[1] if len(durations) > 1 else longest

        # Slot time bought and not used, over every branch including skipped
        # ones: a branch that skipped still occupied a worker slot for the
        # block's duration as far as capacity planning is concerned.
        idle = sum(max(0.0, wall - span["duration_seconds"]) for span in spans.values())

        return {
            "branches": spans,
            "wall_seconds": round(max(0.0, wall), 3),
            "critical_path": critical_path,
            "straggler_margin_seconds": round(max(0.0, straggler_margin), 3),
            "idle_worker_seconds": round(max(0.0, idle), 3),
            # A branch cannot outlast the block that contains it -- the block's
            # clock starts first and stops last by construction. If one does,
            # the wall time was measured around the wrong thing. The tolerance
            # covers the 3-decimal rounding above, nothing more.
            "bounds_violated": bool(wall > 0.0 and longest > wall + 0.01),
        }


class _PoolStats:
    """Accumulator for one named pool, summed across every instance of it.

    Pools such as URL discovery's per-destination category pool are constructed
    once per destination. Reporting them separately would produce ten rows
    saying the same thing, so instances sharing a name accumulate into one row
    and ``instances`` records how many there were.
    """

    __slots__ = (
        "name",
        "instances",
        "max_workers",
        "tasks",
        "busy_seconds",
        "capacity_seconds",
        "wall_seconds",
        "wait_samples",
        "service_samples",
        "samples_truncated",
        "failures",
    )

    def __init__(self, name: str) -> None:
        self.name = name
        self.instances = 0
        self.max_workers = 0
        self.tasks = 0
        self.busy_seconds = 0.0
        self.capacity_seconds = 0.0
        self.wall_seconds = 0.0
        self.wait_samples: list[float] = []
        self.service_samples: list[float] = []
        self.samples_truncated = False
        self.failures = 0

    def record_task(self, wait: float, service: float, failed: bool) -> None:
        self.tasks += 1
        self.busy_seconds += service
        if failed:
            self.failures += 1
        if len(self.service_samples) < _MAX_SAMPLES_PER_POOL:
            self.wait_samples.append(wait)
            self.service_samples.append(service)
        else:
            self.samples_truncated = True

    def record_instance(self, max_workers: int, wall: float) -> None:
        self.instances += 1
        self.max_workers = max(self.max_workers, int(max_workers))
        self.wall_seconds += wall
        self.capacity_seconds += max_workers * wall

    def summary(self) -> dict[str, Any]:
        waits = sorted(self.wait_samples)
        services = sorted(self.service_samples)
        utilisation = (
            self.busy_seconds / self.capacity_seconds
            if self.capacity_seconds > 0
            else 0.0
        )
        return {
            "instances": self.instances,
            "max_workers": self.max_workers,
            "tasks": self.tasks,
            "failures": self.failures,
            "wall_seconds": round(self.wall_seconds, 3),
            "busy_worker_seconds": round(self.busy_seconds, 3),
            "capacity_worker_seconds": round(self.capacity_seconds, 3),
            # The number the worker caps have never been checked against.
            "utilisation": round(utilisation, 4),
            "queue_wait_seconds": {
                "p50": round(_percentile(waits, 0.50), 3),
                "p90": round(_percentile(waits, 0.90), 3),
                "max": round(waits[-1], 3) if waits else 0.0,
            },
            "service_seconds": {
                "p50": round(_percentile(services, 0.50), 3),
                "p90": round(_percentile(services, 0.90), 3),
                "max": round(services[-1], 3) if services else 0.0,
            },
            "samples_truncated": self.samples_truncated,
            # Busy time cannot exceed capacity. If it does, max_workers was
            # misreported or two pools are sharing a name.
            "bounds_violated": bool(utilisation > 1.0001),
        }


class PoolRegistry:
    """Collects pool statistics for the current run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stats: dict[str, _PoolStats] = {}

    def _stats_for(self, name: str) -> _PoolStats:
        with self._lock:
            stats = self._stats.get(name)
            if stats is None:
                stats = _PoolStats(name)
                self._stats[name] = stats
            return stats

    def reset(self) -> None:
        with self._lock:
            self._stats.clear()

    def summary(self) -> dict[str, Any]:
        with self._lock:
            names = sorted(self._stats)
            stats = [self._stats[name] for name in names]
        return {name: s.summary() for name, s in zip(names, stats)}


_REGISTRY = PoolRegistry()


def reset() -> None:
    """Clear collected statistics. Called once at the start of a run."""
    _REGISTRY.reset()


def pool_summary() -> dict[str, Any]:
    """Per-pool statistics for ``runtime_metrics``."""
    return _REGISTRY.summary()


class InstrumentedPool:
    """A ``ThreadPoolExecutor`` that records what its workers were doing.

    Deliberately not a subclass: only ``submit`` is used at the call sites, and
    a narrow wrapper cannot accidentally change ``map``/``shutdown`` semantics.
    """

    def __init__(self, name: str, max_workers: int, registry: PoolRegistry) -> None:
        self._name = name
        self._max_workers = max(1, int(max_workers))
        self._stats = registry._stats_for(name)
        self._pool = ThreadPoolExecutor(max_workers=self._max_workers)
        self._opened_at = perf_counter()

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        submitted_at = perf_counter()

        def _instrumented(*inner_args: Any, **inner_kwargs: Any) -> Any:
            started_at = perf_counter()
            failed = False
            try:
                return fn(*inner_args, **inner_kwargs)
            except BaseException:
                failed = True
                raise
            finally:
                ended_at = perf_counter()
                self._stats.record_task(
                    wait=max(0.0, started_at - submitted_at),
                    service=max(0.0, ended_at - started_at),
                    failed=failed,
                )

        return self._pool.submit(_instrumented, *args, **kwargs)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)
        self._stats.record_instance(
            max_workers=self._max_workers,
            wall=max(0.0, perf_counter() - self._opened_at),
        )


@contextmanager
def pool(name: str, max_workers: int) -> Iterator[InstrumentedPool]:
    """Drop-in replacement for ``with ThreadPoolExecutor(max_workers=n) as p``.

    The instance's wall clock runs from entry to exit, matching the block the
    ``with`` statement already delimits, so utilisation is measured against the
    time the pool was actually open rather than against the whole run.
    """
    instrumented = InstrumentedPool(name, max_workers, _REGISTRY)
    try:
        yield instrumented
    finally:
        instrumented.shutdown(wait=True)
