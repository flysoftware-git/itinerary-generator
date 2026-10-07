"""Turning trails off omits an AllTrails link, not the attraction.

WHAT IT COST
------------
The Europe guide rebuilt 2026-10-06 dropped Manneken Pis and Charles Bridge.
Their entire decision trail was two events:

    trail_links_disabled
    no_verified_url_removed

Neither is a trail. `_is_trail_like_attraction` reads the model's own words as
well as the name, and Charles Bridge's description says "a historic stone
bridge; a short walk across" with difficulty Easy and duration 30 min -- which
classifies True. Europe has trails off, so the branch set `url = ""`, dropped
the maps fallback and skipped discovery entirely. With no url and no maps_url,
verified-link-or-seed then removed the item.

So a cost control for one vendor deleted a city's most famous landmark.

THE RULE IT SHOULD HAVE FOLLOWED WAS ALREADY WRITTEN
----------------------------------------------------
The paragraph immediately above that branch says what a SEED gets when trails
are off: "the ordinary hunt, over sources that are not the disabled vendor --
an official site, a park page. A seed needs a link. It does not need an
AllTrails link." That is right for any attraction, and the chokepoint in
`_retain_discovered_url` refuses an AllTrails URL whoever proposes it, so the
vendor stays switched off either way.
"""

from __future__ import annotations

import inspect

import pytest

from generator.url_discovery import URLDiscoverer

CHARLES_BRIDGE = {
    "name": "Charles Bridge",
    "type": "landmark",
    "difficulty": "Easy",
    "duration": "30 min",
    "description": "A historic stone bridge; a short walk across.",
}


def test_the_models_own_words_make_a_bridge_trail_like():
    """The premise. Without this the rest of the file proves nothing.

    The misclassification is not itself the defect -- a bridge somebody walks
    across is a reasonable thing to call walk-like -- but it is what put a
    landmark in front of a switch that deleted it.
    """
    context = URLDiscoverer._attraction_trail_context(CHARLES_BRIDGE)
    assert URLDiscoverer._is_trail_like_attraction("Charles Bridge", "landmark", context)


def test_what_the_itinerary_calls_a_trail_decides_the_drop():
    """The narrowing the first attempt of this fix got wrong.

    Letting every walk-like item through also let an actual trail through, and
    `test_a_trail_nobody_asked_for_is_still_dropped_when_trails_are_off` caught
    it -- a trail nobody asked for is exactly what the switch is for. So the
    DROP keys on the declared `type`, and the fuzzy classifier only decides
    whether AllTrails was worth asking.
    """
    src = inspect.getsource(URLDiscoverer._discover_attractions)
    assert "declared_trail" in src
    assert "DECLARED_TRAIL_TYPES" in src, "the switch must share the classifier's set"


def test_the_two_readers_of_trail_type_cannot_drift():
    """One set, used by the classifier and by the switch.

    Two copies of a type list is how today's other two link bugs began -- a
    matcher keying on something the place supplied, and a host list copied into
    two modules.
    """
    from generator import url_discovery

    assert "walk" in url_discovery.DECLARED_TRAIL_TYPES
    assert URLDiscoverer._is_trail_like_attraction("Something", "walk", "")
    assert not URLDiscoverer._is_trail_like_attraction("Manneken Pis", "landmark", "")


def test_the_decision_says_what_was_actually_done():
    """`trail_links_disabled` claimed a link was omitted while an item was being
    deleted. The reason code now says which of the two happened."""
    src = inspect.getsource(URLDiscoverer._discover_attractions)
    assert "trail_links_disabled_non_alltrails_search" in src
    assert "still searched over other sources" in src


def test_the_vendor_is_still_switched_off():
    """The cost control has to survive the fix, or this trades one defect for
    a bill. The chokepoint refuses AllTrails whoever proposes it."""
    d = URLDiscoverer.__new__(URLDiscoverer)
    d._disable_trails = True
    kept = d._retain_discovered_url(
        "https://www.alltrails.com/trail/czech-republic/prague/charles-bridge",
        "Charles Bridge", "Prague, Czech Republic",
        allow_alltrails=False, kind="attraction",
    )
    assert not kept, "an AllTrails URL must still be refused when trails are off"


def test_a_seed_keeps_its_override():
    """The seed path was already right and must stay as it is."""
    src = inspect.getsource(URLDiscoverer._discover_attractions)
    assert "trail_links_disabled_seed_override" in src
    assert "trails are off, but the traveler named this one" in src
