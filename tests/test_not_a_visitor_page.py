"""A restaurant link on the right domain must also be the right page.

Measured 2026-09-13 on two builds of one ten-stop route: of the restaurant
links upgraded from an aggregator to the official domain, about 1 in 7 was a
page no reader should be sent to. The homepage check accepted any one-segment
path on a name-matching host, and the relevance gate accepts any page that
names the restaurant, so a job listing passed both. Every URL below shipped in
a real guide.
"""
from __future__ import annotations

import pytest

from generator.url_discovery import _RETENTION_EXIT_LABELS, URLDiscoverer

SHIPPED_WRONG_PAGES = [
    ("Black Cow", "https://blackcowrestaurants.com/job-opportunities/"),
    ("Steeple Hall at Mission Oak Grill", "https://www.steeplehall.com/weddings/"),
    ("Thunderbird Cafe", "https://columbiathunderbirdcafe.com/2019/10/15/if-your-can-dream-it-your-can-do-it-2/"),
    ("The Capital Grille", "https://careers.thecapitalgrille.com/search/jobdetails/server/744bfa96-eb97-41bb-8a94-ead5437b4574"),
    ("Feng Chophouse", "https://www.fengchophouse.com/category/updates/"),
    ("Encore by Goodfellas", "https://www.encorerestaurantct.com/privacypolicy"),
    ("Lumi Asian Fusion", "https://www.luminorthhaven.com/privacy"),
    ("The Capital Grille", "https://www.thecapitalgrille.com/faqs"),
    ("Nardelli's Grinder Shoppe", "https://nardellis.com/our-history/"),
    ("Rosella", "https://www.rosellakpt.com/faqs"),
    ("Ultramar Restaurant", "https://ultramar.restaurant/gallery"),
    ("The Clam Shack", "https://www.theclamshack.net/our-story"),
    ("Depot Street Tavern", "https://depotsttavern.com/community"),
]

PAGES_A_READER_IS_SENT_TO = [
    "https://blackcowrestaurants.com/",
    "https://www.theclamshack.net",
    "https://www.rosellakpt.com/menu",
    "https://www.thecapitalgrille.com/locations/ct/hartford/hartford/8010",
    "https://losandesri.com/reservations/",
    # Kept on purpose: a restaurant's catering page is still the restaurant.
    "https://losandesri.com/catering/",
    "https://ultramar.restaurant/contact",
    "https://www.fengchophouse.com/hours",
    "https://www.historicinnkennebunk.com/",  # "history" inside a host is not a segment
    "https://www.example-diner.com/menus/2024/dinner",  # a year alone is not a dated post
]


@pytest.mark.parametrize("item,url", SHIPPED_WRONG_PAGES, ids=[i for i, _ in SHIPPED_WRONG_PAGES])
def test_a_page_that_shipped_as_a_restaurant_link_is_not_a_visitor_page(item, url):
    assert URLDiscoverer._is_not_a_visitor_page(url), f"{item}: {url}"


@pytest.mark.parametrize("url", PAGES_A_READER_IS_SENT_TO)
def test_a_home_menu_location_or_contact_page_still_is(url):
    assert not URLDiscoverer._is_not_a_visitor_page(url), url


def test_retention_refuses_the_job_page_under_its_own_exit_and_keeps_the_home_page():
    """The homepage check used to accept the job page before any other gate
    ran. It still accepts the home page, with no fetch."""
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_policy_mode = "enforce"
    discoverer._fetch_page_text = lambda *a, **k: pytest.fail("no fetch is needed to decide either URL")

    refused = discoverer._retain_discovered_url(
        "https://blackcowrestaurants.com/job-opportunities/",
        "Black Cow",
        "Newburyport, Massachusetts",
        allow_alltrails=False,
        kind="restaurant",
    )
    assert refused == ""
    assert discoverer._last_retention_rejection is not None
    assert "_is_not_a_visitor_page" in str(discoverer._last_retention_rejection)

    kept = discoverer._retain_discovered_url(
        "https://blackcowrestaurants.com/",
        "Black Cow",
        "Newburyport, Massachusetts",
        allow_alltrails=False,
        kind="restaurant",
    )
    assert kept == "https://blackcowrestaurants.com/"


def test_the_new_exit_is_labelled_with_its_guarding_condition():
    assert _RETENTION_EXIT_LABELS[32] == "if self._is_not_a_visitor_page(url)"
