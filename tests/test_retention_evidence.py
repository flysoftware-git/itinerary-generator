"""Discovery and the audit must judge a URL on the same evidence.

audit_discovered_urls takes `trip` as its only input. Candidate rows were
transient to discovery and never persisted, so the audit could not pass
`candidate` or `allow_shallow_relevance` -- and `deep_check = not
allow_shallow_relevance` silently read that absence as "be stricter", while the
preservation branch gated on `isinstance(candidate, dict)` was skipped outright.
The asymmetry was a parameter default, not a decision anyone made.

Consequence (2026-08-29, sw run): Upheaval Dome's
alltrails.com/trail/us/utah/upheaval-dome-via-crater-view-trail was accepted
during discovery, then discarded by the audit, leaving the item with no URL and
removing it under verified-link-or-seed.
"""

from generator.url_discovery import URLDiscoverer

URL = "https://www.alltrails.com/trail/us/utah/upheaval-dome-via-crater-view-trail"
ITEM = "Upheaval Dome"
ROW = {"title": "Upheaval Dome via Crater View Trail", "snippet": "Canyonlands"}


def _discoverer():
    return URLDiscoverer.__new__(URLDiscoverer)


def test_evidence_recorded_during_discovery_is_recalled_later():
    d = _discoverer()
    d._remember_retention_evidence(ITEM, URL, candidate=ROW, allow_shallow_relevance=True)
    got = d._recall_retention_evidence(ITEM, URL)
    assert got["candidate"] == ROW
    assert got["allow_shallow_relevance"] is True


def test_recall_is_empty_for_something_never_seen():
    assert _discoverer()._recall_retention_evidence(ITEM, URL) == {}


def test_a_bare_call_does_not_erase_richer_evidence():
    """The audit's own call must not overwrite what discovery recorded.

    Both stages go through _retain_discovered_url, and the audit's call arrives
    with no candidate. Recording it unconditionally would clear the very
    evidence the audit is about to look up.
    """
    d = _discoverer()
    d._remember_retention_evidence(ITEM, URL, candidate=ROW, allow_shallow_relevance=True)
    d._remember_retention_evidence(ITEM, URL, candidate=None, allow_shallow_relevance=False)
    assert d._recall_retention_evidence(ITEM, URL)["candidate"] == ROW


def test_evidence_is_keyed_per_item_and_url():
    d = _discoverer()
    d._remember_retention_evidence(ITEM, URL, candidate=ROW, allow_shallow_relevance=True)
    assert d._recall_retention_evidence("Balanced Rock", URL) == {}
    assert d._recall_retention_evidence(ITEM, "https://example.com/other") == {}


def test_item_name_matching_ignores_punctuation_and_case():
    d = _discoverer()
    d._remember_retention_evidence("Queen's Garden Trail", URL, candidate=ROW, allow_shallow_relevance=True)
    assert d._recall_retention_evidence("queens garden trail", URL)["candidate"] == ROW
