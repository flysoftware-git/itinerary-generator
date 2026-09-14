"""A refused link a search index returned this run says so -- and is still not checked.

A published link whose host refuses automated fetches is `unchecked`. When the
run's own per-item searches already returned that exact URL from a search-engine
index, the report records `corroborated_by: search_index`: evidence the page
exists, paid for already, and reported as weaker than a fetch. It never changes a
state, it never covers a timeout or a resolver's temporary failure, and a card's
Maps link is a different URL that corroborates nothing about the card's direct
link.

No network: fetches are stubbed at `_fetch_page_text_uncached` and searches at
the search client, the two places the discoverer asks the outside world.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
import yaml

from generator.html_assembler import HTMLAssembler
from generator.link_corroboration import (
    CORROBORATED_BY_SEARCH_INDEX,
    DEFAULT_LINK_CORROBORATION_SEARCH_ENABLED,
    LINK_CORROBORATION_CALL_SITE,
    is_refused_fetch,
    normalize_for_corroboration,
    search_for_uncorroborated,
)
from generator.link_liveness_gate import withhold_dead_card_links
from generator.serper_search import SerperSearch
from generator.url_discovery import LINK_LIVENESS_UNCHECKED, URLDiscoverer

_MARK = '<span class="link-unchecked-mark"'

BLOCKED_LISTING = "https://www.tripadvisor.com/Restaurant_Review-g1-d2-Reviews-Blocked_Bistro.html"
BLOCKED_TRAIL = "https://www.alltrails.com/trail/us/rhode-island/example-river-bikeway"
BLOCKED_UNLISTED = "https://www.yelp.com/biz/quiet-cafe-newport"
TIMED_OUT = "https://www.slowsite-example.com/menu"
TEMP_DNS = "https://www.flakydns-example.com/"
LIVE_HOMEPAGE = "https://www.harbourgrill-example.com/"
MAPS_BADGE = "https://www.google.com/maps/search/?api=1&query=Blocked%20Bistro%20Newport"
MAPS_PRIMARY = "https://www.google.com/maps/search/?api=1&query=Harbour%20Lookout%20Newport"

TEMP_DNS_FAILURE = (
    "HTTPSConnectionPool(host='www.flakydns-example.com', port=443): Max retries exceeded with url: / "
    "(Caused by NameResolutionError(\"<urllib3.connection.HTTPSConnection object>: Failed to resolve "
    "'www.flakydns-example.com' ([Errno -3] Temporary failure in name resolution)\"))"
)

RESPONSES: dict[str, tuple[bool, Any, str]] = {
    BLOCKED_LISTING: (False, 403, ""),
    BLOCKED_TRAIL: (False, 403, ""),
    BLOCKED_UNLISTED: (False, 429, ""),
    TIMED_OUT: (False, "HTTPSConnectionPool(host='www.slowsite-example.com', port=443): Read timed out. (read timeout=8)", ""),
    TEMP_DNS: (False, TEMP_DNS_FAILURE, ""),
    LIVE_HOMEPAGE: (True, 200, "<html>Harbour Grill</html>"),
}


@pytest.fixture(autouse=True)
def _no_network():
    with patch.object(
        requests.sessions.Session,
        "request",
        side_effect=requests.exceptions.ConnectionError("network disabled in test"),
    ):
        yield


class IndexClient:
    """A search client whose rows come from a search-engine index, like Serper."""

    RESULTS_ARE_SEARCH_INDEX_ROWS = True

    def __init__(self, rows_by_query: dict[str, list[str]] | None = None, default: list[str] | None = None):
        self.rows_by_query = rows_by_query or {}
        self.default = default or []
        self.queries: list[str] = []

    def search(self, query: str, count: int | None = None) -> list[dict[str, Any]]:
        self.queries.append(query)
        urls = self.rows_by_query.get(query, self.default)
        return [{"name": "result", "snippet": "", "url": url} for url in urls]


class ModelClient(IndexClient):
    """A model-backed search: its URLs were written down by a model."""

    RESULTS_ARE_SEARCH_INDEX_ROWS = False


def _discoverer(client: Any = None) -> URLDiscoverer:
    d = URLDiscoverer.__new__(URLDiscoverer)
    d._alltrails_request_delay_seconds = 0.0
    d._domain_block_cooldown_seconds = 0
    d._fetch_page_text_uncached = lambda url, timeout=8: RESPONSES.get(url, (False, "read timed out", ""))
    d._search_fallback = client
    return d


def _search(d: URLDiscoverer, *urls: str) -> None:
    """One per-item search this run, whose results were `urls`."""
    client = d._search_fallback
    query = f"query {len(client.queries)}"
    client.rows_by_query[query] = list(urls)
    d._search_cached(query)


def _trip(*, restaurants: list[dict[str, Any]] | None = None, attractions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "trip": {},
        "destinations": [
            {
                "id": "newport",
                "name": "Newport, Rhode Island",
                "ai_content": {
                    "top_attractions": attractions or [],
                    "getting_here": {
                        "en_route_stops": [],
                        "trail_url": BLOCKED_TRAIL,
                        "trail_label": "Example River Bikeway",
                    },
                    "dinner_recommendations": restaurants if restaurants is not None else [
                        {"name": "Blocked Bistro", "cuisine": "French", "url": BLOCKED_LISTING, "maps_url": MAPS_BADGE},
                        {"name": "Quiet Cafe", "cuisine": "Cafe", "url": BLOCKED_UNLISTED},
                        {"name": "Slow Site", "cuisine": "Diner", "url": TIMED_OUT},
                        {"name": "Flaky DNS", "cuisine": "Diner", "url": TEMP_DNS},
                        {"name": "Harbour Grill", "cuisine": "Seafood", "url": LIVE_HOMEPAGE},
                    ],
                    "possible_daily_schedule": [],
                },
                "scenic_drives": [],
                "cultural_events": {"events": []},
            }
        ],
    }


def _gate(d: URLDiscoverer, trip: dict[str, Any]) -> dict[str, Any]:
    withhold_dead_card_links(trip, d)
    return trip["_link_liveness"]


# --- normalisation -------------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    [
        "http://www.tripadvisor.com/Restaurant_Review-g1-d2-Reviews-Blocked_Bistro.html",
        "https://tripadvisor.com/Restaurant_Review-g1-d2-Reviews-Blocked_Bistro.html/",
        "https://WWW.TripAdvisor.com/Restaurant_Review-g1-d2-Reviews-Blocked_Bistro.html?utm_source=x&fbclid=abc",
        "https://www.tripadvisor.com:443/Restaurant_Review-g1-d2-Reviews-Blocked_Bistro.html#reviews",
    ],
)
def test_scheme_www_trailing_slash_and_tracking_do_not_distinguish_a_page(variant):
    assert normalize_for_corroboration(variant) == normalize_for_corroboration(BLOCKED_LISTING)


@pytest.mark.parametrize(
    "different",
    [
        "https://www.tripadvisor.com/Restaurant_Review-g1-d3-Reviews-Other_Bistro.html",
        "https://www.tripadvisor.com/restaurant_review-g1-d2-reviews-blocked_bistro.html",
        "https://www.yelp.com/biz/quiet-cafe-newport?page=2",
        "https://m.tripadvisor.com/Restaurant_Review-g1-d2-Reviews-Blocked_Bistro.html",
    ],
)
def test_a_different_page_does_not_normalise_to_the_same_key(different):
    keys = {normalize_for_corroboration(BLOCKED_LISTING), normalize_for_corroboration(BLOCKED_UNLISTED)}
    assert normalize_for_corroboration(different) not in keys


def test_a_blocked_link_returned_under_a_variant_url_is_corroborated():
    d = _discoverer(IndexClient())
    _search(d, "http://tripadvisor.com/Restaurant_Review-g1-d2-Reviews-Blocked_Bistro.html/?utm_medium=serp")

    report = _gate(d, _trip())

    assert report["corroborated_by"] == {BLOCKED_LISTING: CORROBORATED_BY_SEARCH_INDEX}


# --- what is corroborated ------------------------------------------------------


def test_a_blocked_link_this_runs_index_returned_is_corroborated_and_still_unchecked():
    d = _discoverer(IndexClient())
    _search(d, BLOCKED_LISTING, "https://www.example-unrelated.com/")

    report = _gate(d, _trip())

    assert report["corroborated_by"] == {BLOCKED_LISTING: "search_index"}
    assert report["corroborated_count"] == 1
    assert report["states"][BLOCKED_LISTING] == LINK_LIVENESS_UNCHECKED


def test_a_blocked_link_no_search_returned_is_not_corroborated():
    d = _discoverer(IndexClient())
    _search(d, BLOCKED_LISTING)

    report = _gate(d, _trip())

    assert BLOCKED_UNLISTED not in report["corroborated_by"]
    assert BLOCKED_TRAIL not in report["corroborated_by"]
    assert report["states"][BLOCKED_UNLISTED] == LINK_LIVENESS_UNCHECKED


def test_a_timeout_or_a_temporary_dns_failure_is_never_corroborated():
    d = _discoverer(IndexClient())
    _search(d, TIMED_OUT, TEMP_DNS, BLOCKED_LISTING)

    report = _gate(d, _trip())

    assert report["states"][TIMED_OUT] == LINK_LIVENESS_UNCHECKED
    assert report["states"][TEMP_DNS] == LINK_LIVENESS_UNCHECKED
    assert TIMED_OUT not in report["corroborated_by"]
    assert TEMP_DNS not in report["corroborated_by"]
    assert list(report["corroborated_by"]) == [BLOCKED_LISTING]


@pytest.mark.parametrize(
    "detail, refused",
    [
        ("403", True),
        ("401", True),
        ("429", True),
        ("domain_cooldown", True),
        ("('Connection aborted.', ConnectionResetError(10054, 'An existing connection was forcibly closed'))", True),
        ("HTTPSConnectionPool(host='x', port=443): Read timed out. (read timeout=8)", False),
        ("('Connection aborted.', TimeoutError('The read operation timed out'))", False),
        (TEMP_DNS_FAILURE, False),
        ("Failed to resolve 'x' ([Errno 11002] getaddrinfo failed)", False),
        ("never_fetched", False),
        ("500", False),
        ("", False),
    ],
)
def test_only_a_refusal_is_eligible(detail, refused):
    assert is_refused_fetch("https://www.example.com/page", detail) is refused


def test_a_live_link_in_the_results_is_not_listed_as_corroborated():
    d = _discoverer(IndexClient())
    _search(d, LIVE_HOMEPAGE)

    report = _gate(d, _trip())

    assert report["corroborated_by"] == {}


def test_rows_a_model_wrote_down_corroborate_nothing():
    d = _discoverer(ModelClient())
    _search(d, BLOCKED_LISTING, BLOCKED_UNLISTED, BLOCKED_TRAIL)

    report = _gate(d, _trip())

    assert report["corroborated_by"] == {}
    assert report["corroborated_count"] == 0


def test_a_mock_client_is_not_read_as_an_index():
    d = _discoverer(MagicMock())
    d._search_fallback.search.return_value = [{"url": BLOCKED_LISTING}]
    d._search_cached("anything")

    assert _gate(d, _trip())["corroborated_by"] == {}


def test_rows_from_an_earlier_runs_cache_are_not_this_runs_evidence():
    client = IndexClient()
    d = _discoverer(client)
    # Loaded from the persistent cache, as `_load_persistent_caches` does.
    d._search_results_cache = {"cached query": [{"name": "", "snippet": "", "url": BLOCKED_LISTING}]}

    assert d._search_cached("cached query")[0]["url"] == BLOCKED_LISTING
    assert client.queries == [], "fixture premise: served from cache, no search made"
    assert _gate(d, _trip())["corroborated_by"] == {}


def test_serper_declares_index_rows():
    assert SerperSearch.RESULTS_ARE_SEARCH_INDEX_ROWS is True


def test_counts_still_add_up_and_corroboration_is_a_subset_of_unchecked():
    d = _discoverer(IndexClient())
    _search(d, BLOCKED_LISTING, BLOCKED_TRAIL, TIMED_OUT, LIVE_HOMEPAGE)

    report = _gate(d, _trip())

    counts = report["counts"]
    assert set(counts) == {"live", "dead", "unchecked"}
    assert counts["live"] + counts["dead"] + counts["unchecked"] == report["published_count"] == 6
    assert report["corroborated_count"] == len(report["corroborated_by"]) == 2
    assert all(report["states"][url] == LINK_LIVENESS_UNCHECKED for url in report["corroborated_by"])


# --- the Maps link is its own link ---------------------------------------------


def test_a_maps_link_in_the_results_does_not_corroborate_the_cards_direct_link():
    d = _discoverer(IndexClient())
    _search(d, MAPS_BADGE, "https://www.google.com/maps/place/?q=place_id:ChIJblockedbistro")

    report = _gate(d, _trip())

    assert BLOCKED_LISTING not in report["corroborated_by"]
    assert report["corroborated_by"] == {}


def test_an_engine_built_maps_link_is_not_counted_marked_or_named_as_a_refusing_site():
    d = _discoverer(IndexClient())
    trip = _trip(
        restaurants=[{"name": "Blocked Bistro", "cuisine": "French", "url": BLOCKED_LISTING, "maps_url": MAPS_BADGE}],
        attractions=[{"name": "Harbour Lookout", "type": "viewpoint", "url": MAPS_PRIMARY}],
    )

    report = _gate(d, trip)

    assert MAPS_PRIMARY not in report["states"]
    assert report["engine_built_not_counted"] == 1
    assert "www.google.com" not in report["unchecked_by_domain"]
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._set_link_liveness_states(trip)
    dest = trip["destinations"][0]
    html = assembler._build_attractions(dest["ai_content"], [], dest["name"], dest=dest)
    lookout = html[html.index("Harbour Lookout"):]
    lookout = lookout[: lookout.index("</div>")]
    assert _MARK not in lookout, "a map link the engine built and never fetched was marked not checked"
    footer = assembler._link_liveness_note_text(trip)
    assert "google" not in footer.lower()


# --- the footer ------------------------------------------------------------------


def _note(unchecked: int, corroborated: int, live: int = 0, domains: dict[str, int] | None = None) -> str:
    report = {
        "counts": {"live": live, "dead": 0, "unchecked": unchecked},
        "published_count": live + unchecked,
        "unchecked_by_domain": domains if domains is not None else {"www.tripadvisor.com": unchecked},
        "corroborated_count": corroborated,
    }
    return HTMLAssembler.__new__(HTMLAssembler)._link_liveness_note_text({"_link_liveness": report})


def test_the_footer_with_no_corroboration_is_unchanged():
    base = _note(28, 0, live=87)
    report_without_field = {
        "counts": {"live": 87, "dead": 0, "unchecked": 28},
        "published_count": 115,
        "unchecked_by_domain": {"www.tripadvisor.com": 28},
    }
    before = HTMLAssembler.__new__(HTMLAssembler)._link_liveness_note_text({"_link_liveness": report_without_field})
    assert base == before
    assert "search" not in base


def test_the_footer_says_how_many_unreached_links_the_index_returned():
    text = _note(28, 19, live=87)

    assert text == (
        "About the links. 87 of the 115 links in this guide were fetched and found working. "
        "The other 28 could not be reached to check from here — mostly a site that refuses "
        "automated requests: TripAdvisor — and nothing has been guessed in place of checking. "
        "19 of those 28 were among this run's web search results, so a search engine lists "
        "them; they were still not fetched here. No link that failed a check was published."
    )


@pytest.mark.parametrize(
    "unchecked, corroborated, live, expected",
    [
        (1, 1, 5, " That one was among this run's web search results, so a search engine lists it; it was still not fetched here."),
        (4, 4, 5, " All 4 were among this run's web search results, so a search engine lists them; they were still not fetched here."),
        (4, 1, 5, " One of those 4 was among this run's web search results, so a search engine lists it; it was still not fetched here."),
        (6, 3, 0, " 3 of those 6 were among this run's web search results, so a search engine lists them; they were still not fetched here."),
    ],
)
def test_the_corroboration_sentence_reads_for_each_count(unchecked, corroborated, live, expected):
    text = _note(unchecked, corroborated, live=live)

    assert expected + " No link that failed a check was published." in text


def test_a_count_larger_than_the_unchecked_count_is_not_repeated():
    assert "search" not in _note(3, 4, live=2)


def test_the_footer_never_claims_a_corroborated_link_was_verified_or_working():
    for unchecked, corroborated, live in ((28, 19, 87), (4, 4, 0), (1, 1, 0), (1, 1, 3)):
        text = _note(unchecked, corroborated, live=live)
        sentence = text[text.index("search engine") - 60:].lower()
        for claim in ("verified", "working", "confirmed", "checked", "valid", "exists and"):
            assert claim not in sentence.replace("no link that failed a check was published.", ""), (claim, text)


def test_the_promise_is_still_withdrawn_when_a_dead_link_is_on_the_page():
    report = {
        "counts": {"live": 2, "dead": 1, "unchecked": 2},
        "published_count": 5,
        "unchecked_by_domain": {"www.yelp.com": 2},
        "corroborated_count": 2,
    }
    text = HTMLAssembler.__new__(HTMLAssembler)._link_liveness_note_text({"_link_liveness": report})

    assert "search engine lists them" in text
    assert "No link that failed a check was published." not in text


# --- the mark --------------------------------------------------------------------


def _mark_of(html: str, url: str) -> str:
    """The mark directly after this link's icon, or "" -- never a neighbour's."""
    match = re.search(
        r'href="' + re.escape(url) + r'"[^>]*>.*?</a>\s*<span class="attr-external-link"[^>]*>.*?</span>'
        r'(\s*<span class="link-unchecked-mark"[^>]*>[^<]*</span>)?',
        html,
    )
    assert match, f"link not rendered: {url}"
    return (match.group(1) or "").strip()


def test_a_corroborated_link_is_still_marked_not_checked_and_its_title_says_why():
    d = _discoverer(IndexClient())
    _search(d, BLOCKED_LISTING, MAPS_BADGE)
    trip = _trip()
    _gate(d, trip)
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    assembler._set_link_liveness_states(trip)
    dest = trip["destinations"][0]
    html = assembler._build_restaurants(dest["ai_content"], dest["name"])

    blocked = _mark_of(html, BLOCKED_LISTING)
    assert re.sub(r"<[^>]+>", "", blocked) == "not checked"
    assert "It was among this run&#x27;s web search results, so a search engine lists it" in blocked

    unlisted = _mark_of(html, BLOCKED_UNLISTED)
    assert re.sub(r"<[^>]+>", "", unlisted) == "not checked"
    assert "search" not in unlisted

    # The Maps badge beside the corroborated link is its own link, with its own
    # title, and carries no mark of either kind.
    badge = re.search(r'<a href="[^"]*google\.com/maps[^"]*" class="badge badge-map"[^>]*>', html)
    assert badge and 'title="Open in Google Maps"' in badge.group(0)
    assert html.count(_MARK) == 4  # tripadvisor, yelp, timeout, temporary DNS -- never the badge


# --- the opt-in search -----------------------------------------------------------


def test_the_extra_search_is_off_by_default():
    assert DEFAULT_LINK_CORROBORATION_SEARCH_ENABLED is False
    shipped = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    assert shipped["url_discovery"]["link_corroboration_search"]["enabled"] is False

    client = IndexClient(default=[BLOCKED_UNLISTED, BLOCKED_TRAIL])
    d = _discoverer(client)
    report = _gate(d, _trip())

    assert client.queries == [], "a search was made that nobody turned on"
    assert report["corroborated_by"] == {}


def _constructed(tmp_path: Path, section: Any) -> URLDiscoverer:
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"url_discovery": {"link_corroboration_search": section}}), encoding="utf-8")
    with patch("generator.search_provider.GrokSearch"), patch("generator.search_provider.ClaudeSearch"), patch(
        "generator.search_provider.OpenAiSearch"
    ), patch("generator.serper_search.SerperSearch"), patch.object(
        URLDiscoverer, "_read_search_model_override", staticmethod(lambda _p: "")
    ):
        llm = type("MockLLM", (), {"provider": "grok", "model": "grok-test", "usage_tracker": None})()
        return URLDiscoverer(config_path=str(config), llm_client=llm)


@pytest.mark.parametrize(
    "section, enabled, cap",
    [
        ({}, False, 10),
        ({"enabled": "yes"}, False, 10),
        ({"enabled": 1}, False, 10),
        ({"enabled": True, "max_searches_per_run": 3}, True, 3),
        ({"enabled": True, "max_searches_per_run": "many"}, True, 10),
    ],
)
def test_only_an_explicit_true_turns_the_extra_search_on(tmp_path, section, enabled, cap):
    d = _constructed(tmp_path, section)

    assert d._link_corroboration_search_enabled is enabled
    assert d._link_corroboration_search_max == cap


def _many_blocked_trip(n: int) -> tuple[dict[str, Any], list[str]]:
    urls = [f"https://www.yelp.com/biz/blocked-{i}-newport" for i in range(n)]
    for url in urls:
        RESPONSES[url] = (False, 403, "")
    trip = _trip(restaurants=[{"name": f"Blocked {i}", "cuisine": "Diner", "url": u} for i, u in enumerate(urls)])
    return trip, urls


class EchoIndexClient(IndexClient):
    """Returns the one blocked page a site-restricted query names, as an index would."""

    def __init__(self, urls: list[str]):
        super().__init__()
        self.urls = urls

    def search(self, query: str, count: int | None = None) -> list[dict[str, Any]]:
        self.queries.append(query)
        match = re.search(r"Blocked (\d+)", query)
        return [{"name": "", "snippet": "", "url": self.urls[int(match.group(1))]}] if match else []


def test_when_on_the_extra_search_is_capped_per_run_and_its_results_corroborate():
    trip, urls = _many_blocked_trip(6)
    client = EchoIndexClient(urls)
    d = _discoverer(client)
    d._link_corroboration_search_enabled = True
    d._link_corroboration_search_max = 3

    summary = withhold_dead_card_links(trip, d)

    assert summary["corroboration_searches"] == 3
    assert len(client.queries) == 3
    assert all(q.startswith("site:") for q in client.queries)
    assert d._fallback_call_sites == {LINK_CORROBORATION_CALL_SITE: 3}
    # The cap is per run: a second pass spends nothing more.
    assert search_for_uncorroborated(d, []) == 0
    withhold_dead_card_links(trip, d)
    assert len(client.queries) == 3
    # What the three searches returned is recorded like any other index row;
    # the links past the cap stay uncorroborated.
    corroborated = trip["_link_liveness"]["corroborated_by"]
    assert set(corroborated) == set(urls[:3])
    assert trip["_link_liveness"]["corroborated_count"] == 3


def test_when_on_no_search_is_spent_on_a_link_already_corroborated_or_not_refused():
    client = IndexClient()
    d = _discoverer(client)
    _search(d, BLOCKED_LISTING, BLOCKED_UNLISTED, BLOCKED_TRAIL)
    d._link_corroboration_search_enabled = True
    d._link_corroboration_search_max = 10
    before = list(client.queries)

    withhold_dead_card_links(_trip(), d)

    # Timeout, temporary DNS and live links are not searched for; the three
    # refused links were already corroborated.
    assert client.queries == before


def test_when_on_a_model_backed_client_is_not_paid_to_search():
    trip, _urls = _many_blocked_trip(2)
    client = ModelClient(default=_urls)
    d = _discoverer(client)
    d._link_corroboration_search_enabled = True

    withhold_dead_card_links(trip, d)

    assert client.queries == []

# --- one icon, one meaning, on every card type -----------------------------------

_ICON_SPAN = re.compile(r'<span class="attr-external-link" title="([^"]*)">([^<]*)</span>')
_LEGEND_WORDS = {"🔗": "opens the source page", "🥾": "opens a trail page", "🗺️": "opens a map"}
DIRECT = "https://www.harbourmuseum-example.org/exhibits"
PLACE_MAP = "https://www.google.com/maps/place/?q=place_id:ChIJexampleplace"
SEARCH_MAP = "https://www.google.com/maps/search/?api=1&query=Harbour%20Lookout%20Newport"


def _every_card_type() -> dict[str, str]:
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    dest = {"name": "Newport, Rhode Island"}
    both = {"url": DIRECT, "maps_url": SEARCH_MAP}
    ai = {
        "top_attractions": [
            {"name": "Harbour Museum", "type": "museum", **both},
            {"name": "Harbour Lookout", "type": "viewpoint", "maps_url": PLACE_MAP},
        ],
        "dinner_recommendations": [
            {"name": "Harbour Grill", "cuisine": "Seafood", **both},
            {"name": "Lookout Diner", "cuisine": "Diner", "maps_url": PLACE_MAP},
        ],
        "getting_here": {
            "route_summary": "Along the coast.",
            "en_route_stops": [
                {"name": "Harbour Museum", "description": "A museum.", **both},
                {"name": "Harbour Lookout", "description": "A lookout.", "maps_url": SEARCH_MAP},
            ],
            "trail_url": BLOCKED_TRAIL,
            "trail_label": "Example River Bikeway",
        },
        "getting_there": {
            "route_summary": "Home.",
            "route_options": [
                {"title": "Coast Road", "description": "Slow.", **both},
                {"title": "Lookout Road", "description": "Map only.", "url": SEARCH_MAP},
            ],
        },
    }
    return {
        "attraction": assembler._build_attractions(ai, [], dest["name"], dest=dest),
        "restaurant": assembler._build_restaurants(ai, dest["name"]),
        "en_route_stop": assembler._build_getting_here(ai, dest, previous_name="Boston"),
        "route_option": assembler._build_getting_there(ai, dest, {"return": "Boston"}),
        "leg_trail": assembler._build_leg_trail_link_html(ai["getting_here"]),
    }


def test_every_card_types_link_icon_title_is_the_legends_word_for_that_icon():
    seen: dict[str, set[str]] = {}
    for kind, html in _every_card_type().items():
        icons = _ICON_SPAN.findall(html)
        assert icons, f"fixture premise: {kind} rendered no link icon"
        for title, icon in icons:
            assert title == _LEGEND_WORDS[icon], f"{kind}: {icon} titled {title!r}"
            seen.setdefault(kind, set()).add(icon)
    # The fixture reaches both kinds of link on every card type that can hold both.
    for kind in ("attraction", "restaurant", "en_route_stop", "route_option"):
        # (The getting-here block also renders the leg's trail link, hence >=.)
        assert seen[kind] >= {"🔗", "🗺️"}, (kind, seen[kind])
    assert seen["leg_trail"] == {"🥾"}


def test_the_separate_maps_badge_is_the_same_on_every_card_type_and_never_marked():
    for kind, html in _every_card_type().items():
        for badge in re.findall(r'<a [^>]*class="badge badge-map"[^>]*>[^<]*</a>', html):
            assert 'title="Open in Google Maps"' in badge, (kind, badge)
            assert badge.endswith(">🗺️</a>"), (kind, badge)
        assert _MARK not in html
