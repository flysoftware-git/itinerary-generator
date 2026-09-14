"""A card link the run could not check says so, beside its icon.

The footer's liveness statement gives the totals: so many links fetched and
found working, so many that could not be reached to check. It could not say
*which*. A reader looking at a TripAdvisor link on a card had no way to tell it
from a link that was fetched a minute before publishing.

`_link_unchecked_mark` renders a quiet, visible "not checked" beside the icon of
a card link whose liveness state is `unchecked`. A live link renders exactly as
before, and a link with no liveness record renders with no mark either way --
absent is not a state, and the page must not decide one for it.

No network: the liveness report is built by the real gate over a stubbed fetch,
the same way `test_link_liveness_gate.py` builds it.
"""
from __future__ import annotations

import re
from typing import Any
from unittest.mock import patch

import pytest
import requests

from generator.html_assembler import HTMLAssembler
from generator.link_liveness_gate import withhold_dead_card_links
from generator.url_discovery import LINK_LIVENESS_UNCHECKED, URLDiscoverer

_MARK = '<span class="link-unchecked-mark"'
_LEGEND = '<div class="link-icon-legend"'

TRAIL = "https://www.alltrails.com/trail/us/utah/angels-landing"
LIVE_HOMEPAGE = "https://www.harbourgrill-example.com/"
BLOCKED_LISTING = "https://www.tripadvisor.com/Restaurant_Review-g1-d2-Reviews-Blocked_Bistro.html"
LIVE_ATTRACTION = "https://www.harbourmuseum-example.org/"
BLOCKED_TRAIL = "https://www.alltrails.com/trail/us/rhode-island/example-river-bikeway"

RESPONSES: dict[str, tuple[bool, Any, str]] = {
    LIVE_HOMEPAGE: (True, 200, "<html>Harbour Grill, Newport</html>"),
    BLOCKED_LISTING: (False, 403, ""),
    LIVE_ATTRACTION: (True, 200, "<html>Harbour Museum, Newport</html>"),
    BLOCKED_TRAIL: (False, 403, ""),
}


@pytest.fixture(autouse=True)
def _no_network():
    with patch.object(
        requests.sessions.Session,
        "request",
        side_effect=requests.exceptions.ConnectionError("network disabled in test"),
    ):
        yield


def _assembler() -> HTMLAssembler:
    return HTMLAssembler.__new__(HTMLAssembler)


def _report(states: dict[str, str]) -> dict[str, Any]:
    counts = {"live": 0, "dead": 0, "unchecked": 0}
    for state in states.values():
        counts[state] += 1
    return {
        "states": states,
        "counts": counts,
        "published_count": len(states),
        "unchecked_by_domain": {"www.alltrails.com": counts["unchecked"]} if counts["unchecked"] else {},
    }


def _trail_card(assembler: HTMLAssembler, url: str = TRAIL) -> str:
    return assembler._build_leg_trail_link_html({"trail_url": url, "trail_label": "Angels Landing"})


def _rendered_with(trip: dict[str, Any] | None, url: str = TRAIL) -> str:
    assembler = _assembler()
    if trip is not None:
        assembler._set_link_liveness_states(trip)
    return _trail_card(assembler, url)


# --- the mark ----------------------------------------------------------------


def test_an_unchecked_card_link_is_marked_quietly():
    """Owner direction, 2026-09-14: "tone down the marks".

    The visible words "not checked" sat beside 55 of 120 card links on a
    Nashville build. Now the icon fades, its hover text gives the reason, and
    the words survive only as screen-reader text -- quieter, not gone.
    """
    card = _rendered_with({"_link_liveness": _report({TRAIL: "unchecked"})})

    icon = re.search(r'<span class="attr-external-link"([^>]*)>', card).group(1)
    assert 'style="opacity:0.45;"' in icon
    assert 'title="opens the trail page — could not be checked before publishing"' in icon

    assert _MARK in card
    mark = card[card.index(_MARK):]
    mark = mark[: mark.index("</span>") + len("</span>")]
    assert re.sub(r"<[^>]+>", "", mark) == "(not checked before publishing)"
    assert "position:absolute" in mark and "clip:rect(0 0 0 0)" in mark, "must be visually hidden"
    assert card.index('<span class="attr-external-link"') < card.index(_MARK)


def test_a_google_maps_link_is_never_marked():
    """A Maps link is its own state, designated by 🗺️, never "not checked".

    On a Nashville build 15 of 62 unchecked links were geocode-built Maps
    links recorded `never_fetched`; they were marked on 11 cards.
    """
    maps = "https://www.google.com/maps/search/?api=1&query=36.2883133%2C-86.6623429"
    card = _rendered_with({"_link_liveness": {"states": {maps: "map"}}}, url=maps)
    icon = re.search(r'<span class="attr-external-link"([^>]*)>', card).group(1)

    assert _MARK not in card
    assert "opacity" not in icon
    assert 'title="opens in Google Maps"' in icon


def test_the_report_puts_maps_links_in_their_own_state():
    from generator.url_discovery import LINK_LIVENESS_MAP

    d = URLDiscoverer.__new__(URLDiscoverer)
    d._link_liveness = {LIVE_HOMEPAGE: ("live", "200")}
    maps = "https://www.google.com/maps/search/?api=1&query=36.2883133%2C-86.6623429"
    trip = {
        "destinations": [{
            "name": "Old Hickory, Tennessee",
            "ai_content": {
                "dinner_recommendations": [
                    {"name": "Harbour Grill", "url": LIVE_HOMEPAGE},
                    {"name": "No Website Diner", "url": maps},
                ],
                "getting_here": {},
            },
        }],
    }
    report = d.link_liveness_report(trip)

    assert report["states"][maps] == LINK_LIVENESS_MAP
    assert report["map_count"] == 1
    assert report["published_count"] == 1, "only links that could be checked are counted"
    assert report["counts"] == {"live": 1, "dead": 0, "unchecked": 0}
    assert "www.google.com" not in report["unchecked_by_domain"]
    assert report["unchecked_share"] == 0.0


def test_a_live_card_link_renders_exactly_as_before():
    card = _rendered_with({"_link_liveness": _report({TRAIL: "live"})})

    assert _MARK not in card
    assert card == _rendered_with(None)


def test_a_link_the_report_does_not_list_carries_no_mark():
    other = "https://www.alltrails.com/trail/us/utah/observation-point"
    card = _rendered_with({"_link_liveness": _report({other: "unchecked"})})

    assert _MARK not in card


def test_a_guide_built_without_a_report_marks_nothing():
    for trip in ({}, {"_link_liveness": {}}, {"_link_liveness": {"counts": {"unchecked": 3}}}):
        assert _MARK not in _rendered_with(trip)
    # And an assembler that was never told about a trip at all.
    assert _MARK not in _trail_card(_assembler())


def test_the_mark_matches_the_url_as_the_card_renders_it():
    """The report keys a URL as the trip holds it; an attraction card renders
    the normalised form `_select_preferred_external_link` returns."""
    raw = "www.harbourmuseum-example.org/"
    assembler = _assembler()
    assembler._set_link_liveness_states({"_link_liveness": _report({raw: "unchecked"})})
    ai = {"top_attractions": [{"name": "Harbour Museum", "type": "museum", "url": raw}]}

    card = assembler._build_attractions(ai, [], "Newport, Rhode Island", dest={"name": "Newport, Rhode Island"})

    assert 'href="https://www.harbourmuseum-example.org/"' in card
    assert _MARK in card


# --- the count ---------------------------------------------------------------


def _gated_trip() -> dict[str, Any]:
    d = URLDiscoverer.__new__(URLDiscoverer)
    d._alltrails_request_delay_seconds = 0.0
    d._fetch_page_text_uncached = lambda url, timeout=8: RESPONSES.get(url, (False, "read timed out", ""))
    trip = {
        "trip": {},
        "destinations": [
            {
                "id": "newport",
                "name": "Newport, Rhode Island",
                "ai_content": {
                    "top_attractions": [
                        {"name": "Harbour Museum", "type": "museum", "url": LIVE_ATTRACTION},
                    ],
                    "getting_here": {
                        "en_route_stops": [],
                        "trail_url": BLOCKED_TRAIL,
                        "trail_label": "Example River Bikeway",
                    },
                    "dinner_recommendations": [
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
    d.audit_discovered_urls(trip)
    withhold_dead_card_links(trip, d)
    return trip


def _cards(trip: dict[str, Any]) -> str:
    assembler = _assembler()
    assembler._set_link_liveness_states(trip)
    dest = trip["destinations"][0]
    ai = dest["ai_content"]
    return (
        assembler._build_restaurants(ai, dest["name"])
        + assembler._build_attractions(ai, [], dest["name"], dest=dest)
        + assembler._build_leg_trail_link_html(ai["getting_here"])
    )


_LINK_AND_ICON = re.compile(
    r'<a href="([^"]+)"[^>]*>.*?</a>\s*'
    r'<span class="attr-external-link"[^>]*>.*?</span>'
    r'(\s*<span class="link-unchecked-mark")?'
)


def test_the_marks_on_the_cards_are_the_footers_unchecked_count():
    """Every link in this guide renders with an icon, so the two must agree.

    They can differ on a guide whose report also lists links that carry no icon
    -- a scenic drive, an event, a local tip -- which the footer counts and no
    card marks. The footer stays the source of the totals.
    """
    trip = _gated_trip()
    report = trip["_link_liveness"]
    html = _cards(trip)

    rendered = {m.group(1): bool(m.group(2)) for m in _LINK_AND_ICON.finditer(html)}
    assert set(rendered) == set(report["states"]), "fixture premise: every reported link has an icon"
    marked = {url for url, has_mark in rendered.items() if has_mark}
    assert marked == {url for url, s in report["states"].items() if s == LINK_LIVENESS_UNCHECKED}
    assert len(marked) == report["counts"]["unchecked"] == 2
    assert html.count(_MARK) == report["counts"]["unchecked"]


def test_assembly_reads_the_trips_report():
    trip = {
        "trip": {"title": "Test Trip"},
        "_meta": {"generator_version": "9.9.9", "generated_at_utc": "2026-07-26T17:41:23+00:00"},
        "destinations": [],
        "_link_liveness": _report({TRAIL: "unchecked"}),
    }
    assembler = HTMLAssembler(config_path="config.yaml")

    assembler.assemble(trip)

    assert _MARK in _trail_card(assembler)


# --- the legend --------------------------------------------------------------


def _footer_trip(report: dict[str, Any] | None) -> dict[str, Any]:
    trip = {
        "trip": {"title": "Test Trip"},
        "_meta": {"generator_version": "9.9.9", "generated_at_utc": "2026-07-26T17:41:23+00:00"},
        "destinations": [],
    }
    if report is not None:
        trip["_link_liveness"] = report
    return trip


def _legend_text(html: str) -> str:
    legend = html[html.index(_LEGEND):]
    legend = legend[: legend.index("</div>")]
    return re.sub(r"<[^>]+>", "", legend)


def test_a_page_with_a_mark_explains_it_and_still_points_at_the_count():
    report = _report({TRAIL: "unchecked"})
    assembler = _assembler()
    assembler._set_link_liveness_states({"_link_liveness": report})
    page = f"<html><body>\n{_trail_card(assembler)}\n</body></html>"

    html = assembler._inject_generator_footer(page, _footer_trip(report))
    text = _legend_text(html)

    assert text == (
        "🔗 opens the source page, 🥾 a trail page, 🗺️ a location in Google Maps, "
        "used when a place has no page of its own. "
        "The icon shows where a link goes, not whether it was checked. "
        "A faded icon means that link could not be checked before publishing, "
        "usually because its site blocks automated checks. "
        "How many links could be checked is below."
    )


def test_a_page_without_a_mark_does_not_describe_one():
    for report in (None, _report({TRAIL: "live"})):
        assembler = _assembler()
        if report is not None:
            assembler._set_link_liveness_states({"_link_liveness": report})
        page = f"<html><body>\n{_trail_card(assembler)}\n</body></html>"

        text = _legend_text(assembler._inject_generator_footer(page, _footer_trip(report)))

        assert "not checked" not in text
        assert "not whether it was checked" in text


def test_the_legend_never_claims_a_link_was_verified_with_or_without_marks():
    assembler = _assembler()
    for report in (None, _report({TRAIL: "unchecked"})):
        for marks in (False, True):
            footer = assembler._build_generator_footer(
                _footer_trip(report), has_link_icons=True, has_unchecked_marks=marks
            )
            text = _legend_text(footer).lower()
            for claim in ("verified", "confirmed", "working", "valid", "tested", "checked and"):
                assert claim not in text, f"legend says {claim!r}: {text!r}"
