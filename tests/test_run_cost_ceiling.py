"""Tests for the per-run cost ceiling and unpriced-model reporting.

Two things are being guarded here, and they are the same concern from two
sides: a run should not be able to spend without bound, and it should not be
able to spend invisibly. A ceiling is only as honest as the prices behind it,
so a model with no pricing entry is reported rather than silently costed at
zero.
"""
from __future__ import annotations

import pytest

from generator.llm_client import (
    DEFAULT_PRICING_USD_PER_1M,
    RunCostCeilingExceeded,
    UsageTracker,
)

# Any priced model will do; the arithmetic under test is the tracker's, not
# the price list's.
PRICED_KEY = sorted(DEFAULT_PRICING_USD_PER_1M)[0]
PROVIDER, MODEL = PRICED_KEY.split(":", 1)


def _spend(tracker: UsageTracker, prompt_tokens: int) -> None:
    tracker.add(
        provider=PROVIDER,
        model=MODEL,
        operation="test",
        prompt_tokens=prompt_tokens,
        completion_tokens=0,
    )


def test_no_ceiling_by_default():
    """A guard that can stop a build partway through belongs to whoever asked
    for it, so it is off until configured."""
    tracker = UsageTracker()
    assert tracker.ceiling_usd is None
    _spend(tracker, 5_000_000)
    tracker.check_ceiling()  # must not raise


@pytest.mark.parametrize("configured", [0, 0.0, None, -1])
def test_non_positive_ceilings_are_treated_as_off(configured):
    """`0` is the documented way to disable it, and a negative value is a
    mistake that must not disable spending entirely."""
    tracker = UsageTracker(ceiling_usd=configured)
    assert tracker.ceiling_usd is None
    _spend(tracker, 5_000_000)
    tracker.check_ceiling()


def test_the_ceiling_stops_the_next_call_not_the_one_that_crossed_it():
    """Checked before the request rather than after it. A guard that notices
    afterwards has already paid, and this is the difference between crossing
    the ceiling once and crossing it on every remaining call."""
    tracker = UsageTracker(ceiling_usd=1.0)
    tracker.check_ceiling()  # nothing spent yet

    _spend(tracker, 5_000_000)
    assert tracker.total_cost_usd() > 1.0

    with pytest.raises(RunCostCeilingExceeded) as excinfo:
        tracker.check_ceiling()
    assert excinfo.value.ceiling_usd == 1.0
    assert excinfo.value.spent_usd == tracker.total_cost_usd()


def test_the_message_names_the_setting_that_caused_it():
    """An operator seeing a build stop needs to know which knob did it."""
    tracker = UsageTracker(ceiling_usd=0.01)
    _spend(tracker, 5_000_000)
    with pytest.raises(RunCostCeilingExceeded, match="run_cost_ceiling_usd"):
        tracker.check_ceiling()


def test_a_breach_is_remembered_after_it_is_raised():
    """Nothing in the pipeline catches this, so the process dies and the
    ledger's atexit guard records only that it exited. The flag is what lets
    the record say why."""
    tracker = UsageTracker(ceiling_usd=0.01)
    assert tracker.ceiling_hit is False
    _spend(tracker, 5_000_000)
    with pytest.raises(RunCostCeilingExceeded):
        tracker.check_ceiling()
    assert tracker.ceiling_hit is True


def test_an_unpriced_model_is_reported_not_just_warned():
    """The 2026-08-16 blind spot: a configured model with no pricing entry
    costs $0.00 per call, so the total reads healthy and the ceiling guards
    nothing. The warning already existed; nothing could act on it."""
    tracker = UsageTracker()
    tracker.add(
        provider="someprovider",
        model="model-with-no-price",
        operation="test",
        prompt_tokens=1_000,
        completion_tokens=1_000,
    )
    summary = tracker.summary()

    assert summary["unpriced_models"] == ["someprovider:model-with-no-price"]
    # And the total it undercounts is still reported, as a floor.
    assert summary["total_estimated_cost_usd"] == 0.0


def test_priced_runs_report_no_unpriced_models():
    tracker = UsageTracker()
    _spend(tracker, 1_000)
    assert tracker.summary()["unpriced_models"] == []


def test_total_cost_matches_the_summary_total():
    """`total_cost_usd()` is called before every request, so it must not be a
    second, drifting implementation of the number in the summary."""
    tracker = UsageTracker()
    _spend(tracker, 1_000_000)
    _spend(tracker, 250_000)
    assert tracker.total_cost_usd() == tracker.summary()["total_estimated_cost_usd"]
