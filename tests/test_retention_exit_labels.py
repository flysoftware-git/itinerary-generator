"""Every retention rejection must say which check refused.

_retain_discovered_url has 30 rejection exits and recorded none of them, so
why a URL was dropped could only be inferred from outside the function. Three
separate fixes for Upheaval Dome's discarded AllTrails link each targeted a
gate that turned out not to be the one firing -- confidence, post-search
filters, and the discovery/audit evidence asymmetry all passed in isolation
while the link kept being discarded.

Instrumentation over inference: the removal audit, the candidate trail and the
cross-stage trail each found a defect the same day that reasoning had missed.
"""

import ast
import inspect

from generator.url_discovery import _RETENTION_EXIT_LABELS, URLDiscoverer


def test_every_rejection_exit_is_routed_through_the_recorder():
    """No bare `return ""` may remain -- one would be an unlabelled exit."""
    src = inspect.getsource(URLDiscoverer._retain_discovered_url)
    tree = ast.parse(src.strip())
    bare = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and node.value.value == ""
    ]
    assert not bare, f"{len(bare)} unlabelled rejection exit(s) remain"


def test_labels_cover_every_exit_id_used_in_the_source():
    src = inspect.getsource(URLDiscoverer._retain_discovered_url)
    tree = ast.parse(src.strip())
    used = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_reject_retention"
        and node.args and isinstance(node.args[0], ast.Constant)
    }
    assert used, "no _reject_retention calls found"
    assert used <= set(_RETENTION_EXIT_LABELS), (
        f"exit ids without labels: {sorted(used - set(_RETENTION_EXIT_LABELS))}"
    )


def test_recorder_returns_empty_and_records_the_branch():
    d = URLDiscoverer.__new__(URLDiscoverer)
    assert d._reject_retention(3) == ""
    exit_id, label = d._last_retention_rejection
    assert exit_id == 3
    assert label == _RETENTION_EXIT_LABELS[3]


def test_an_unknown_id_does_not_raise():
    d = URLDiscoverer.__new__(URLDiscoverer)
    assert d._reject_retention(9999) == ""
    assert d._last_retention_rejection[1] == "unknown"


def test_labels_are_non_empty_strings():
    assert all(isinstance(v, str) and v.strip() for v in _RETENTION_EXIT_LABELS.values())


def test_the_exit_label_reaches_the_removal_trail():
    """The label is carried in the event message, which the trail was dropping.

    The instrumentation was added to name the branch that discards a URL, and
    the first version put the label in the disposition event's message while
    _removal_trail recorded only reason/url/source/stage. It never reached the
    report it was written for -- the tool being narrower than the question,
    for the fourth time in this feature's short life.
    """
    from threading import Lock

    from generator.url_discovery import URLDiscoverer

    d = URLDiscoverer.__new__(URLDiscoverer)
    d._decision_threads_by_destination = {}
    d._request_cache_lock = Lock()
    d._decision_event_sequence = 0

    ctx = dict(kind="attraction", dest_name="Canyonlands", item_name="Upheaval Dome")
    d._record_disposition_thread_event(
        trace_id=d._trace_id(**ctx), reason_code="audit_discarded_previously_accepted_url",
        source_code="direct_batch",
        message="url accepted during discovery was rejected [retention exit (17, 'if not ok:')]",
        rendered_url="https://www.alltrails.com/trail/us/utah/x", **ctx,
    )
    trail = d._removal_trail(**ctx)
    assert "retention exit (17" in trail[0]["detail"]


def test_labels_match_the_conditions_they_describe():
    """Regenerated from the AST, so they cannot drift from the source.

    The first version took the nearest preceding code line, which for a
    multi-line condition is a bare ')'. 18 of 30 labels were meaningless, and
    the diagnosis they were built for had to read around them.
    """
    import ast as _ast

    src = inspect.getsource(URLDiscoverer._retain_discovered_url)
    fn = _ast.parse(src.strip()).body[0]
    parent = {c: n for n in _ast.walk(fn) for c in _ast.iter_child_nodes(n)}

    for node in _ast.walk(fn):
        if not (isinstance(node, _ast.Call) and isinstance(node.func, _ast.Attribute)
                and node.func.attr == "_reject_retention" and node.args):
            continue
        exit_id = node.args[0].value
        cur, expected = node, None
        while cur in parent:
            cur = parent[cur]
            if isinstance(cur, _ast.If):
                expected = "if " + _ast.unparse(cur.test)
                break
            if isinstance(cur, _ast.ExceptHandler):
                expected = "except " + (_ast.unparse(cur.type) if cur.type else "Exception")
                break
        assert expected, f"exit {exit_id} has no enclosing condition"
        import re as _re
        assert _RETENTION_EXIT_LABELS[exit_id] == _re.sub(r"\s+", " ", expected)[:120], (
            f"exit {exit_id} label is stale: {_RETENTION_EXIT_LABELS[exit_id]!r}"
        )


def test_no_label_is_a_bare_punctuation_fragment():
    for exit_id, label in _RETENTION_EXIT_LABELS.items():
        assert label.strip() not in (")", "):", "("), f"exit {exit_id}: {label!r}"
        assert len(label.strip()) > 5, f"exit {exit_id}: {label!r}"


def test_the_rejection_record_is_cleared_per_call():
    """Instance state must not leak a previous call's branch into a later one.

    The first run reported exit 1 ("if not url") for items that plainly had
    URLs -- a stale value from an earlier rejection being read by a later
    discard.
    """
    d = URLDiscoverer.__new__(URLDiscoverer)
    d._reject_retention(25)
    assert d._last_retention_rejection[0] == 25
    d._retain_discovered_url("", "Item", "Dest", allow_alltrails=False)
    assert d._last_retention_rejection is None or d._last_retention_rejection[0] == 1
