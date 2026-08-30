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


def test_a_url_the_audit_substitutes_carries_its_own_evidence():
    """The audit can introduce a URL discovery never saw.

    audit_discovered_urls prefers an AllTrails link the trail batch already
    harvested over whatever the attraction category found. That URL has no
    discovery-side retention evidence by construction, so the retention check
    a few lines later judged it with no context and could discard it -- the
    audit rejecting its own replacement.

    Upheaval Dome, sw 2026-08-30: the batch had
    alltrails.com/trail/us/utah/upheaval-dome-via-crater-view-trail, the audit
    preferred it over an unrelated nps.gov photography-permit page, then
    discarded it and removed the item for having no verified URL. The
    retention-evidence handoff added earlier did not cover this, because it
    only records what discovery passes through.
    """
    d = _discoverer()
    # nothing recorded during discovery for this URL
    assert d._recall_retention_evidence(ITEM, URL) == {}
    # the audit's substitution records its own
    d._remember_retention_evidence(ITEM, URL, candidate=None, allow_shallow_relevance=True)
    recalled = d._recall_retention_evidence(ITEM, URL)
    assert recalled["allow_shallow_relevance"] is True
    assert recalled["candidate"] is None


def test_shallow_only_evidence_is_recorded_without_a_candidate():
    """allow_shallow_relevance alone must be enough to record.

    The guard skips recording when there is nothing to record. A substitution
    has no candidate row, so keying that guard on the candidate alone would
    silently drop exactly this case.
    """
    d = _discoverer()
    d._remember_retention_evidence("Some Item", URL, candidate=None, allow_shallow_relevance=True)
    assert d._recall_retention_evidence("Some Item", URL) != {}
