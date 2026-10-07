"""An event whose page the audit rejects still has a venue worth mapping.

FOUND ON A REAL PAGE
--------------------
The Europe guide rebuilt 2026-10-06 rendered "Evanescence + Poppy" at
**Festhalle Messe Frankfurt** with no link at all -- a venue that names a place
perfectly well, and that `_venue_names_a_place` agrees is mappable.

The cause is an order of operations, not a rule. `_verify_event_urls` offers its
maps fallback only to an event that has NO url when it runs, during discovery.
This event had one, so it was offered nothing. The audit then judged that url
unverifiable and took it away -- and there was no fallback left to fall back to.

It is one step further along the same path as dipstick73/74, which taught the
audit to PRESERVE a maps fallback through the retention gate. Preserving one
only helps if one was ever assigned.

WHAT IS PINNED
--------------
That the audit assigns the fallback itself in that case, under exactly the rule
the original fallback uses -- a venue that merely restates the destination still
earns no link, because a map of the city the reader is standing in is not a
lookup.
"""

from __future__ import annotations

import pytest

from generator.cultural_events import CulturalEventsDiscoverer
from generator.url_discovery import URLDiscoverer

FRANKFURT = "Frankfurt, Germany"


def _audit(events, dest_name=FRANKFURT, retain=""):
    """Run the audit's event branch over one destination's events."""
    d = URLDiscoverer.__new__(URLDiscoverer)
    d._retain_discovered_url = lambda url, *a, **k: retain          # the gate's verdict
    d._classify_url_policy_class = lambda url: ""
    d._log_rejected_url = lambda *a, **k: None
    d._annotate_registry_url_decision = lambda *a, **k: None
    d._log_decision = lambda **k: None
    d._prewarm_url_validation_cache = lambda trip: None
    d._deduplicate_within_destination = lambda dest: None
    d._deduplicate_cross_destination_drives = lambda trip: None
    d._deduplicate_attractions_against_en_route_stops_tripwide = lambda trip: None
    d.link_liveness_report = lambda trip: {"counts": {"live": 0, "dead": 0, "unchecked": 0},
                                           "published_count": 0, "unchecked_share": 0.0,
                                           "unchecked_by_domain": {}}
    d._url_discovery_snapshot = lambda name: {}
    trip = {"destinations": [{"name": dest_name, "cultural_events": {"events": events}}]}
    d.audit_discovered_urls(trip)
    return trip["destinations"][0]["cultural_events"]["events"]


def test_a_rejected_page_leaves_the_venue_mapped():
    """The case from the Europe guide."""
    out = _audit([{
        "name": "Evanescence + Poppy",
        "venue": "Festhalle Messe Frankfurt",
        "url": "https://example.test/a-page-the-audit-will-reject",
    }])

    assert not out[0].get("url"), "the rejected page must still go"
    assert out[0].get("maps_url", "").startswith("https://www.google.com/maps/search/")
    assert "Festhalle" in out[0]["maps_url"]


def test_a_venue_that_is_only_the_city_still_earns_nothing():
    """The rule is unchanged, not loosened.

    A map of the city the reader is standing in is not a lookup, which is the
    whole of #184's second gate.
    """
    out = _audit([{
        "name": "Museumsuferfest",
        "venue": "Frankfurt",
        "url": "https://example.test/rejected",
    }])

    assert not out[0].get("url")
    assert not out[0].get("maps_url")


def test_an_event_with_no_venue_earns_nothing():
    out = _audit([{"name": "Dutch Theatre Festival", "venue": "",
                   "url": "https://example.test/rejected"}])

    assert not out[0].get("maps_url")


def test_a_page_the_audit_keeps_is_untouched():
    """The fallback is for an event that LOST its page, not for every event."""
    kept = "https://example.test/a-real-event-page"
    out = _audit(
        [{"name": "Frankfurter Oktoberfest", "venue": "Festzelt am Deutsche Bank Park",
          "url": kept}],
        retain=kept,
    )

    assert out[0]["url"] == kept
    assert not out[0].get("maps_url"), "a kept page needs no fallback beside it"


def test_an_existing_fallback_is_not_replaced():
    """A maps_url already preserved through the gate wins; this adds, never overrides."""
    existing = "https://www.google.com/maps/search/?api=1&query=Already%20Here"
    out = _audit([{
        "name": "Evanescence + Poppy",
        "venue": "Festhalle Messe Frankfurt",
        "url": "https://example.test/rejected",
        "maps_url": existing,
    }])

    assert out[0]["maps_url"] == existing


def test_it_uses_the_same_rule_as_the_original_fallback():
    """Both paths must agree about what counts as a place, or a venue could be
    mapped by one and refused by the other on the same page."""
    for venue, expected in (
        ("Festhalle Messe Frankfurt", True),
        ("Frankfurt", False),
        ("", False),
    ):
        assert CulturalEventsDiscoverer._venue_names_a_place(venue, FRANKFURT) is expected
