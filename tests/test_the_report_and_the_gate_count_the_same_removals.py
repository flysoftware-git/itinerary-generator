"""An item the policy dropped appears in the report, not only in the gate.

WHAT WAS WRONG
--------------
`_keep_item_if_verified_or_seed` writes the removal twice, one line apart: a
`_log_decision(reason="no_verified_url_removed")` that the status report reads,
and a `_record_registry_entity_removal(...)` that the quality gate reads. They
could not disagree about what happened -- and they disagreed about how much.

Measured on the two guides published 2026-10-06: the gate counted 25 removals
on Old Hickory and 31 on Southwest, and both `destination_status_report.json`
files listed **none**.

The reason is timing, not bookkeeping. `dest["_url_discovery"]` is snapshotted
when a destination finishes DISCOVERY; `_keep_item_if_verified_or_seed` runs in
the AUDIT, afterwards. A later refresh existed and updated only
`retention_exit_counts`, so the threads stayed frozen at their pre-audit state.
Whether a removal showed up at all depended on whether a destination happened to
be re-discovered after the audit, which is why an earlier build of the same
guide listed fourteen and a later one listed zero.

WHY IT MATTERS MORE THAN A COUNT
--------------------------------
The report is where an item's trail lives. The gate says a number; only the
report says WHICH places were dropped and what was tried for them. Finding that
Lebanon lost fourteen real businesses took reading those threads -- and on a
build where they were empty, that investigation would have found nothing and
concluded there was nothing to find.
"""

from __future__ import annotations

import inspect

from generator.url_discovery import URLDiscoverer


def test_the_snapshot_is_taken_from_the_live_log():
    """Rebuilt on demand rather than accumulated, so taking it twice is safe."""
    src = inspect.getsource(URLDiscoverer._url_discovery_snapshot)
    assert "_decision_threads_by_destination" in src
    assert "disposition_threads" in src
    assert "retention_exit_counts" in src


def test_the_audit_refreshes_the_whole_snapshot_and_not_only_the_counts():
    """The defect was a refresh that updated one key of several.

    Pinned on the assignment, because the bug is precisely that the old line
    reached into the dict for a single key instead of replacing it.
    """
    src = inspect.getsource(URLDiscoverer.audit_discovered_urls)
    assert 'dest["_url_discovery"] = self._url_discovery_snapshot(' in src, (
        "the audit must retake the whole snapshot; refreshing only "
        "retention_exit_counts is what left every removal out of the report"
    )
    assert '_url_discovery"]["retention_exit_counts"] =' not in src, (
        "the single-key refresh is still there"
    )


def test_both_ledgers_are_still_written_together():
    """The two records of a removal must stay one decision, not two.

    If these ever separate, the report and the gate can disagree again for a
    reason no refresh can fix.
    """
    src = inspect.getsource(URLDiscoverer._keep_item_if_verified_or_seed)
    assert 'reason="no_verified_url_removed"' in src
    assert 'rejection_reason="no_verified_url_removed"' in src


def test_a_removal_logged_after_the_snapshot_reaches_the_report():
    """The behaviour, end to end, over the two calls that used to disagree."""
    d = URLDiscoverer.__new__(URLDiscoverer)
    dest = {"name": "Lebanon, Tennessee"}

    # Discovery takes its snapshot...
    d._log_decision(
        kind="restaurant", dest_name=dest["name"], item_name="Cedar City Brewing Company",
        reason="search_no_match", message="rejected/no-match (tripadvisor.com)",
    )
    dest["_url_discovery"] = d._url_discovery_snapshot(dest["name"])
    before = dest["_url_discovery"]["event_count"]

    # ...then the audit removes the item, as it does in a real run.
    d._log_decision(
        kind="restaurant", dest_name=dest["name"], item_name="Cedar City Brewing Company",
        reason="no_verified_url_removed", message="non-seed item removed",
    )
    assert d._url_discovery_snapshot(dest["name"])["event_count"] > before

    reasons = {
        ev.get("reason")
        for evs in d._url_discovery_snapshot(dest["name"])["disposition_threads"].values()
        for ev in evs
    }
    assert "no_verified_url_removed" in reasons, (
        "a removal written after the snapshot must appear once it is retaken"
    )
