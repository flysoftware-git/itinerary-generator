"""When the manifest names a seed's page, that page is the one published.

#189 widened `seeds` to accept `{name, url}` and said plainly that nothing read
it: *"The field is parsed, validated and exposed, and no caller reads it yet."*
The owner approved full implementation on 2026-10-01, so this is the consumer.

WHY IT GOES FIRST IN THE LOOP
-----------------------------
The paths below it in `_discover_attractions` exist to BEAT an incumbent link --
a TripAdvisor row upgraded to an official site, a remembered direct-batch row
preferred over a search result, an AI-proposed candidate recovered. Every one of
them would otherwise beat the author's own answer with a guess. design.md 1.4
bars the MODEL from producing a URL; a human may, which is the footing
`planning_links` and a leg's `trail_url` already stand on.

So the assertions here are mostly about what does NOT happen: no search, no
batch lookup, and no later path overwriting it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from generator.url_discovery import URLDiscoverer

AUTHORED = "https://www.alltrails.com/trail/us/washington/olympic-discovery-trail"
SEED = "Olympic Discovery Trail"


def _discoverer() -> URLDiscoverer:
    d = URLDiscoverer.__new__(URLDiscoverer)
    d._attraction_source = "search"
    d._alltrails_source = "search"
    d._disable_trails = False
    return d


def _run(discoverer: URLDiscoverer, attractions, dest) -> dict:
    ai = {"top_attractions": attractions}
    discoverer._discover_attractions(
        ai,
        dest.get("name", "Port Angeles"),
        None,
        "October 3-5, 2026",
        seed_names=dest.get("seeds", []),
        dest=dest,
    )
    return ai


def _dest(**over) -> dict:
    base = {
        "name": "Port Angeles",
        "seeds": [SEED],
        "seed_links": {SEED: AUTHORED},
    }
    base.update(over)
    return base


def test_the_authored_page_is_the_published_link():
    d = _discoverer()
    ai = _run(d, [{"name": SEED, "type": "hike"}], _dest())

    assert ai["top_attractions"][0]["url"] == AUTHORED


def test_nothing_is_searched_for_a_seed_whose_page_is_named():
    """The point is not only the link: it is the search nobody has to pay for."""
    d = _discoverer()
    with patch.object(URLDiscoverer, "_search_first_strict") as search:
        _run(d, [{"name": SEED, "type": "hike"}], _dest())

    assert not search.called, "discovery ran for an item the manifest had already answered"


def test_the_decision_is_in_the_items_trail():
    """A link nothing explains is the thing the next person debugging will find."""
    d = _discoverer()
    with patch.object(URLDiscoverer, "_log_decision") as logged:
        _run(d, [{"name": SEED, "type": "hike"}], _dest())

    reasons = [call.kwargs.get("reason") for call in logged.call_args_list]
    assert "seed_named_the_page" in reasons, reasons
    said = [c for c in logged.call_args_list if c.kwargs.get("reason") == "seed_named_the_page"][0]
    assert said.kwargs.get("url") == AUTHORED
    assert said.kwargs.get("item_name") == SEED


def test_punctuation_does_not_have_to_be_guessed():
    """The manifest and the generated item rarely agree on a trailing period."""
    d = _discoverer()
    ai = _run(
        d,
        [{"name": "Olympic Discovery Trail.", "type": "hike"}],
        _dest(seeds=["Olympic Discovery Trail."]),
    )

    assert ai["top_attractions"][0]["url"] == AUTHORED


def _took_the_authored_path(dest: dict, item_name: str = SEED) -> bool:
    """Did the authored-page branch fire for this item?

    Asserted on the decision rather than on "a search method was called":
    which search runs depends on the item's type and the source modes, so
    naming one of them would pin the wrong thing and pass for the wrong reason.
    """
    d = _discoverer()
    with patch.object(URLDiscoverer, "_log_decision") as logged:
        _run(d, [{"name": item_name, "type": "hike"}], dest)
    return any(c.kwargs.get("reason") == "seed_named_the_page" for c in logged.call_args_list)


class TestItIsNotAppliedWhereItWasNotMeant:
    def test_an_item_that_is_not_a_seed_gets_nothing(self):
        """`seed_links` answers for the seed it names, not for a lookalike.

        A manifest naming a page for "Olympic Discovery Trail" has said nothing
        about some other attraction that happens to be in the same list.
        """
        d = _discoverer()
        with patch.object(URLDiscoverer, "_search_first_strict", return_value="") as search:
            ai = _run(d, [{"name": "Hurricane Ridge", "type": "viewpoint"}], _dest())

        assert ai["top_attractions"][0].get("url") != AUTHORED
        assert search.called, "an ordinary attraction must still be discovered"

    def test_a_seed_with_no_page_named_is_discovered_as_before(self):
        assert not _took_the_authored_path(_dest(seed_links={}))

    def test_a_destination_with_no_seed_links_at_all_is_unchanged(self):
        """The field is absent when no seed named a page, so this is the norm."""
        dest = _dest()
        dest.pop("seed_links")

        assert not _took_the_authored_path(dest)

    def test_an_empty_url_is_not_a_page(self):
        assert not _took_the_authored_path(_dest(seed_links={SEED: "   "}))


class TestItSurvivesWhatComesAfter:
    def test_the_audit_is_given_evidence_so_it_does_not_discard_it(self):
        """The audit re-runs retention over a URL discovery already accepted.

        Without evidence recorded against it, it judges the author's page with
        no context -- which is how the audit came to reject its own replacement
        for Upheaval Dome. Same mechanism, same limits: the hard gates still
        apply and a dead link is still dead.
        """
        d = _discoverer()
        _run(d, [{"name": SEED, "type": "hike"}], _dest())

        key = d._retention_evidence_key(SEED, AUTHORED)
        assert key in d._retention_evidence
        assert d._retention_evidence[key]["allow_shallow_relevance"] is True

    def test_a_remembered_direct_batch_row_does_not_overwrite_it(self):
        """The strongest of the paths that exist to beat an incumbent link."""
        d = _discoverer()
        with patch.object(
            URLDiscoverer, "_is_remembered_direct_batch_authoritative_url", return_value=True
        ):
            ai = _run(d, [{"name": SEED, "type": "hike"}], _dest())

        assert ai["top_attractions"][0]["url"] == AUTHORED

    def test_the_interest_filter_does_not_reach_it(self):
        """"Olympic Discovery Trail" matches the `cycling trail` keywords.

        A seed already overrides that filter; this pins that the authored page
        is taken before the question is even asked.
        """
        d = _discoverer()
        with patch.object(URLDiscoverer, "_is_uninterested_attraction", return_value=True):
            ai = _run(d, [{"name": SEED, "type": "hike"}], _dest())

        assert ai["top_attractions"][0]["url"] == AUTHORED
