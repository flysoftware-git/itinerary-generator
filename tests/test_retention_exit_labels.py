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
