"""A link a check found dead does not reach a card, whatever path attached it.

Reproduces the shape measured on a published 11-stop guide (engine 3.2.0):

* a restaurant whose homepage no longer resolves (DNS failure) survived the
  URL audit, because the retention gate accepts a restaurant URL whose host
  names the restaurant without fetching it -- while the audit's own prewarm
  had fetched it and recorded it dead -- and it rendered on a card;
* a leg's trail link, taken straight from the manifest, rendered with the
  same link icon and was in no liveness report, so it was neither checked
  nor counted.

No network: every fetch is stubbed at `_fetch_page_text_uncached`, the one
place the discoverer actually asks the web.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
import requests

from generator.html_assembler import HTMLAssembler
from generator.link_liveness_gate import card_links, withhold_dead_card_links
from generator.url_discovery import (
    LINK_LIVENESS_DEAD,
    LINK_LIVENESS_LIVE,
    LINK_LIVENESS_UNCHECKED,
    URLDiscoverer,
)

DNS_FAILURE = (
    "HTTPSConnectionPool(host='www.cafeclosed-example.com', port=443): Max retries exceeded "
    "with url: / (Caused by NameResolutionError(\"HTTPSConnection(host='www.cafeclosed-example.com', "
    "port=443): Failed to resolve 'www.cafeclosed-example.com' ([Errno 11001] getaddrinfo failed)\"))"
)

DEAD_HOMEPAGE = "https://www.cafeclosed-example.com/"
LIVE_HOMEPAGE = "https://www.harbourgrill-example.com/"
BLOCKED_LISTING = "https://www.tripadvisor.com/Restaurant_Review-g1-d2-Reviews-Blocked_Bistro.html"
LEG_TRAIL = "https://www.alltrails.com/trail/us/rhode-island/example-river-bikeway"
DEAD_LEG_TRAIL = "https://www.alltrails.com/trail/us/rhode-island/example-gone-rail-trail"
SEED_DEAD = "https://www.oldmill-example.org/"
LIVE_ATTRACTION = "https://www.harbourmuseum-example.org/"

# What the web says about each URL, in the shape _fetch_page_text_uncached returns.
RESPONSES: dict[str, tuple[bool, Any, str]] = {
    DEAD_HOMEPAGE: (False, DNS_FAILURE, ""),
    LIVE_HOMEPAGE: (True, 200, "<html>Harbour Grill, Newport</html>"),
    BLOCKED_LISTING: (False, 403, ""),
    LEG_TRAIL: (False, 403, ""),
    DEAD_LEG_TRAIL: (False, 404, ""),
    SEED_DEAD: (False, 404, ""),
    LIVE_ATTRACTION: (True, 200, "<html>Harbour Museum, Newport</html>"),
}


@pytest.fixture(autouse=True)
def _no_network():
    with patch.object(
        requests.sessions.Session,
        "request",
        side_effect=requests.exceptions.ConnectionError("network disabled in test"),
    ):
        yield


def _discoverer(fetched: list[str] | None = None) -> URLDiscoverer:
    d = URLDiscoverer.__new__(URLDiscoverer)
    d._alltrails_request_delay_seconds = 0.0

    def _fetch(url: str, timeout: int = 8):
        if fetched is not None:
            fetched.append(url)
        return RESPONSES.get(url, (False, "read timed out", ""))

    d._fetch_page_text_uncached = _fetch
    return d


def _trip() -> dict[str, Any]:
    return {
        "trip": {},
        "destinations": [
            {
                "id": "newport",
                "name": "Newport, Rhode Island",
                "seeds": ["Old Mill"],
                "ai_content": {
                    "top_attractions": [
                        {"name": "Harbour Museum", "type": "museum", "url": LIVE_ATTRACTION},
                        {"name": "Old Mill", "type": "historic", "url": SEED_DEAD},
                    ],
                    "getting_here": {
                        "en_route_stops": [],
                        "trail_url": LEG_TRAIL,
                        "trail_label": "Example River Bikeway",
                    },
                    "dinner_recommendations": [
                        {"name": "Cafe Closed", "cuisine": "Seafood", "url": DEAD_HOMEPAGE},
                        {"name": "Harbour Grill", "cuisine": "Seafood", "url": LIVE_HOMEPAGE},
                        {"name": "Blocked Bistro", "cuisine": "French", "url": BLOCKED_LISTING},
                    ],
                    "possible_daily_schedule": [],
                },
                "scenic_drives": [],
                "cultural_events": {"events": []},
            }
        ],
    }


def _dest(trip: dict[str, Any]) -> dict[str, Any]:
    return trip["destinations"][0]


def _restaurant_names(trip: dict[str, Any]) -> list[str]:
    return [r["name"] for r in _dest(trip)["ai_content"]["dinner_recommendations"]]


def _rendered_restaurants(trip: dict[str, Any]) -> str:
    return HTMLAssembler.__new__(HTMLAssembler)._build_restaurants(
        _dest(trip)["ai_content"], _dest(trip)["name"]
    )


def _rendered_cards(trip: dict[str, Any]) -> str:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    ai = _dest(trip)["ai_content"]
    return (
        assembler._build_restaurants(ai, _dest(trip)["name"])
        + assembler._build_attractions(ai, [], _dest(trip)["name"], dest=_dest(trip))
        + assembler._build_leg_trail_link_html(ai["getting_here"])
    )


def _audited_trip(d: URLDiscoverer) -> dict[str, Any]:
    trip = _trip()
    d.audit_discovered_urls(trip)
    return trip


# --- the measured shape ----------------------------------------------------


def test_the_audit_lets_a_dns_dead_homepage_through_and_the_ledger_knows_it():
    """The precondition, pinned so the fixture is known to reproduce the defect.

    If this stops holding, the audit itself has started enforcing the rule and
    this file's fixture no longer exercises the path the assembly check exists
    for -- change the fixture, do not delete the check.
    """
    d = _discoverer()
    trip = _audited_trip(d)
    assert "Cafe Closed" in _restaurant_names(trip)
    assert d.link_liveness_state(DEAD_HOMEPAGE) == LINK_LIVENESS_DEAD
    assert DEAD_HOMEPAGE in _rendered_restaurants(trip)


def test_a_dns_dead_restaurant_link_is_not_on_any_card_after_the_assembly_check():
    d = _discoverer()
    trip = _audited_trip(d)

    summary = withhold_dead_card_links(trip, d)

    html = _rendered_cards(trip)
    assert DEAD_HOMEPAGE not in html, "a link its check found dead was rendered on a card"
    assert summary["withheld"] >= 1
    assert DEAD_HOMEPAGE in trip["_link_liveness"]["withheld_dead"]
    assert "getaddrinfo failed" in trip["_link_liveness"]["withheld_dead"][DEAD_HOMEPAGE]


def test_a_restaurant_left_without_a_verified_link_is_removed_fail_closed():
    d = _discoverer()
    trip = _audited_trip(d)

    withhold_dead_card_links(trip, d)

    assert "Cafe Closed" not in _restaurant_names(trip)
    assert "Cafe Closed" not in _rendered_restaurants(trip)
    removals = [
        r for r in _dest(trip).get("_registry_decisions", [])
        if r.get("display_name") == "Cafe Closed"
    ]
    assert removals and removals[-1]["rejection_reasons"] == ["no_verified_url_removed"]


def test_the_travellers_own_seed_stays_but_loses_the_dead_link():
    d = _discoverer()
    trip = _audited_trip(d)
    # Put the seed back as it would arrive if its link had been attached after
    # the audit -- the assembly check must not depend on the audit's verdict.
    attractions = _dest(trip)["ai_content"]["top_attractions"]
    if not any(a["name"] == "Old Mill" for a in attractions):
        attractions.append({"name": "Old Mill", "type": "historic", "is_seed": True})
    for a in attractions:
        if a["name"] == "Old Mill":
            a["url"] = SEED_DEAD
            a["is_seed"] = True

    withhold_dead_card_links(trip, d)

    names = [a["name"] for a in _dest(trip)["ai_content"]["top_attractions"]]
    assert "Old Mill" in names
    seed = next(a for a in _dest(trip)["ai_content"]["top_attractions"] if a["name"] == "Old Mill")
    assert "url" not in seed
    assert SEED_DEAD not in _rendered_cards(trip)


def test_the_seed_that_kept_its_card_and_lost_its_link_says_so():
    """Staying is only half of it. The gate leaves a seed on the page with no
    link at all, and the card has to account for that in the reader's terms --
    a caution badge beside a description promising that "details will be
    refined by linked references" tells them to wait for something no later
    stage performs."""
    d = _discoverer()
    trip = _audited_trip(d)
    attractions = _dest(trip)["ai_content"]["top_attractions"]
    if not any(a["name"] == "Old Mill" for a in attractions):
        attractions.append({"name": "Old Mill", "type": "historic", "is_seed": True})
    for a in attractions:
        if a["name"] == "Old Mill":
            a["url"] = SEED_DEAD
            a["is_seed"] = True

    withhold_dead_card_links(trip, d)

    html = _rendered_cards(trip)
    assert "No source link found for this one" in html
    assert "will be refined" not in html


def test_a_blocked_link_still_publishes_and_is_counted_unchecked():
    d = _discoverer()
    trip = _audited_trip(d)

    withhold_dead_card_links(trip, d)

    assert "Blocked Bistro" in _restaurant_names(trip)
    assert BLOCKED_LISTING in _rendered_restaurants(trip)
    report = trip["_link_liveness"]
    assert report["states"][BLOCKED_LISTING] == LINK_LIVENESS_UNCHECKED
    assert BLOCKED_LISTING not in report["withheld_dead"]


def test_a_dns_failure_is_dead_and_a_block_is_not():
    d = _discoverer()
    assert d.classify_link_liveness(DEAD_HOMEPAGE, False, DNS_FAILURE) == LINK_LIVENESS_DEAD
    assert d.classify_link_liveness(BLOCKED_LISTING, False, 403) == LINK_LIVENESS_UNCHECKED


# --- links attached outside the checked set --------------------------------


def test_every_card_link_is_in_the_report_after_the_assembly_check():
    d = _discoverer()
    trip = _audited_trip(d)

    withhold_dead_card_links(trip, d)

    states = trip["_link_liveness"]["states"]
    missing = sorted({link.url for link in card_links(trip)} - set(states))
    assert missing == [], f"card links the liveness report does not contain: {missing}"
    assert LEG_TRAIL in states


def test_a_manifest_trail_link_is_checked_not_assumed():
    fetched: list[str] = []
    d = _discoverer(fetched)
    trip = _audited_trip(d)
    assert LEG_TRAIL not in (getattr(d, "_link_liveness", {}) or {}), (
        "fixture premise: the audit never sees the leg trail link"
    )

    summary = withhold_dead_card_links(trip, d)

    assert LEG_TRAIL in fetched
    assert summary["checked_at_assembly"] >= 1
    # Blocked by the host: unchecked, and still on the card.
    assert trip["_link_liveness"]["states"][LEG_TRAIL] == LINK_LIVENESS_UNCHECKED
    assert trip["_link_liveness"]["details"][LEG_TRAIL] != "never_fetched"
    assert LEG_TRAIL in _rendered_cards(trip)


def test_a_dead_trail_link_from_outside_the_audit_is_withheld():
    d = _discoverer()
    trip = _audited_trip(d)
    _dest(trip)["ai_content"]["getting_here"]["trail_url"] = DEAD_LEG_TRAIL

    withhold_dead_card_links(trip, d)

    getting_here = _dest(trip)["ai_content"]["getting_here"]
    assert "trail_url" not in getting_here and "trail_label" not in getting_here
    assert DEAD_LEG_TRAIL not in _rendered_cards(trip)
    assert DEAD_LEG_TRAIL in trip["_link_liveness"]["withheld_dead"]


# --- the wiring ------------------------------------------------------------


def test_the_run_applies_the_check_after_every_edit_and_before_assembly():
    """Source order, because the full run cannot be driven without a provider.

    This is a test of shape, and says so: the behaviour is pinned above, and
    this pins only that the run reaches it at the one point where links are
    final -- after the retry pass, registry reconciliation and the restaurant
    cap have all had their turn, and before the page is assembled.
    """
    import inspect

    import generator.main as main_module

    source = inspect.getsource(main_module)
    gate = source.find("withhold_dead_card_links(trip, url_discoverer)")
    assemble = source.find("html = assembler.assemble(trip)")
    assert gate != -1, "the run never applies the assembly link check"
    assert assemble != -1
    assert gate < assemble, "the check runs after the page is assembled"
    for earlier in (
        "_enforce_restaurant_per_day_cap(trip, ai_gen)",
        "_unify_names_sharing_a_place_id(trip)",
        "trip, registry = _reconcile_trip_via_registry(trip, return_registry=True)",
    ):
        assert source.rfind(earlier, 0, assemble) < gate, f"{earlier} can still edit links after the check"


# --- the footer ------------------------------------------------------------


def test_the_footer_promise_is_true_of_the_page_it_is_printed_on():
    d = _discoverer()
    trip = _audited_trip(d)
    # Before: the audit's own report counted a dead link that is on the page,
    # so the promise was (correctly) withheld.
    assert trip["_link_liveness"]["counts"][LINK_LIVENESS_DEAD] >= 1
    assert "No link that failed a check was published." not in (
        HTMLAssembler.__new__(HTMLAssembler)._link_liveness_note_text(trip)
    )

    withhold_dead_card_links(trip, d)

    report = trip["_link_liveness"]
    assert report["counts"][LINK_LIVENESS_DEAD] == 0
    assert report["counts"][LINK_LIVENESS_LIVE] >= 1
    note = HTMLAssembler.__new__(HTMLAssembler)._link_liveness_note_text(trip)
    assert note.endswith("No link that failed a check was published.")
    html = _rendered_cards(trip)
    for url, state in report["states"].items():
        assert state != LINK_LIVENESS_DEAD
    for url in report["withheld_dead"]:
        assert url not in html
