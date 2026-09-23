"""A page the server will serve over https is not published as http.

The Southwest guide of 2026-09-22 carried two plaintext links,
`http://desertbistro.com/menu-spring-2026` and `http://graftonheritage.org/`,
and both hosts answer https. They got there two different ways, and both are
covered here:

  * desertbistro serves http and https, each 200, with no redirect. The
    scheme is whichever one the search result happened to carry.
  * graftonheritage redirects http to https. `URLValidator._check` follows
    redirects but discarded where it landed, so the pre-redirect form was
    published.

A generated guide outlives its run and gets read on hotel wifi.
"""

from __future__ import annotations

from typing import Any

import pytest

from generator.link_liveness_gate import prefer_https_card_links


class FakeValidator:
    def __init__(self) -> None:
        self._last_final_url = ""


class FakeDiscoverer:
    """Answers verification from a table, and records what it was asked."""

    def __init__(self, reachable: dict[str, bool], redirects: dict[str, str] | None = None) -> None:
        self._reachable = reachable
        self._redirects = redirects or {}
        self._url_validator = FakeValidator()
        self.asked: list[str] = []

    def _verify_url_cached(self, url: str) -> tuple[bool, int | str]:
        self.asked.append(url)
        self._url_validator._last_final_url = self._redirects.get(url, url)
        return self._reachable.get(url, False), 200 if self._reachable.get(url) else 404


def trip_with(url: str, kind: str = "attraction") -> dict[str, Any]:
    item = {"name": "A place", "url": url}
    section = {
        "attraction": {"ai_content": {"top_attractions": [item]}},
        "restaurant": {"ai_content": {"dinner_recommendations": [item]}},
        "en_route_stop": {"ai_content": {"getting_here": {"en_route_stops": [item]}}},
        "event": {"cultural_events": {"events": [item]}},
    }[kind]
    return {"destinations": [{"name": "Moab", **section}]}


def only_url(trip: dict[str, Any], kind: str = "attraction") -> str:
    ai = trip["destinations"][0].get("ai_content", {})
    lists = {
        "attraction": ai.get("top_attractions"),
        "restaurant": ai.get("dinner_recommendations"),
        "en_route_stop": ai.get("getting_here", {}).get("en_route_stops"),
        "event": trip["destinations"][0].get("cultural_events", {}).get("events"),
    }[kind]
    return lists[0]["url"]


class TestTheHttpsTwinIsPreferred:
    def test_the_desert_bistro_case(self) -> None:
        """Both schemes serve the page; publish the secure one."""
        trip = trip_with("http://desertbistro.com/menu-spring-2026")
        d = FakeDiscoverer({
            "http://desertbistro.com/menu-spring-2026": True,
            "https://desertbistro.com/menu-spring-2026": True,
        })
        assert prefer_https_card_links(trip, d) == 1
        assert only_url(trip) == "https://desertbistro.com/menu-spring-2026"

    def test_a_query_string_survives_the_upgrade(self) -> None:
        trip = trip_with("http://example.test/events?id=7&m=10")
        d = FakeDiscoverer({"https://example.test/events?id=7&m=10": True})
        prefer_https_card_links(trip, d)
        assert only_url(trip) == "https://example.test/events?id=7&m=10"

    def test_an_https_link_is_left_alone_and_never_probed(self) -> None:
        trip = trip_with("https://www.nps.gov/zion/")
        d = FakeDiscoverer({})
        assert prefer_https_card_links(trip, d) == 0
        assert only_url(trip) == "https://www.nps.gov/zion/"
        assert d.asked == [], "an https link must cost no requests"

    def test_an_http_only_site_keeps_its_link(self) -> None:
        """No https anywhere: the link still publishes, unchanged."""
        trip = trip_with("http://oldsite.test/page")
        d = FakeDiscoverer({"http://oldsite.test/page": True})
        assert prefer_https_card_links(trip, d) == 0
        assert only_url(trip) == "http://oldsite.test/page"


class TestTheRedirectTargetIsPublished:
    def test_the_grafton_heritage_case(self) -> None:
        """http redirects to https, and both answer 403 to our client.

        The twin probe fails, so the upgrade has to come from where the site
        sent us -- which is the whole reason _check now records it.
        """
        trip = trip_with("http://graftonheritage.org/")
        d = FakeDiscoverer(
            reachable={},
            redirects={"http://graftonheritage.org/": "https://graftonheritage.org/"},
        )
        assert prefer_https_card_links(trip, d) == 1
        assert only_url(trip) == "https://graftonheritage.org/"

    def test_a_www_prefix_is_not_a_different_page(self) -> None:
        trip = trip_with("http://graftonheritage.org/")
        d = FakeDiscoverer(
            reachable={},
            redirects={"http://graftonheritage.org/": "https://www.graftonheritage.org/"},
        )
        assert prefer_https_card_links(trip, d) == 1
        assert only_url(trip) == "https://www.graftonheritage.org/"

    @pytest.mark.parametrize("final", [
        "https://othersite.test/",
        "https://graftonheritage.org/somewhere-else",
        "http://graftonheritage.org/",
    ])
    def test_a_redirect_elsewhere_is_not_followed(self, final: str) -> None:
        """Only a scheme change is taken here.

        A redirect that also changes host or path is a different question --
        whether the link still points at what it claimed -- and belongs to the
        audit, not to this pass.
        """
        trip = trip_with("http://graftonheritage.org/")
        d = FakeDiscoverer(reachable={}, redirects={"http://graftonheritage.org/": final})
        assert prefer_https_card_links(trip, d) == 0
        assert only_url(trip) == "http://graftonheritage.org/"


class TestEveryCardKindIsCovered:
    @pytest.mark.parametrize("kind", ["attraction", "restaurant", "en_route_stop", "event"])
    def test_the_upgrade_reaches_this_card(self, kind: str) -> None:
        trip = trip_with("http://example.test/x", kind)
        d = FakeDiscoverer({"https://example.test/x": True})
        assert prefer_https_card_links(trip, d) == 1
        assert only_url(trip, kind) == "https://example.test/x"

    def test_a_leg_trail_link_is_upgraded(self) -> None:
        trip = {"destinations": [{
            "name": "Ashland",
            "ai_content": {"getting_here": {"trail_url": "http://example.test/pct-b"}},
        }]}
        d = FakeDiscoverer({"https://example.test/pct-b": True})
        assert prefer_https_card_links(trip, d) == 1
        assert trip["destinations"][0]["ai_content"]["getting_here"]["trail_url"] == (
            "https://example.test/pct-b"
        )

    def test_a_local_tip_link_is_upgraded(self) -> None:
        trip = {"destinations": [{
            "name": "Moab",
            "cultural_events": {"local_tip_url": "http://example.test/tip"},
        }]}
        d = FakeDiscoverer({"https://example.test/tip": True})
        assert prefer_https_card_links(trip, d) == 1
        assert trip["destinations"][0]["cultural_events"]["local_tip_url"] == (
            "https://example.test/tip"
        )


class TestTheCostIsBounded:
    def test_a_page_of_https_links_costs_nothing(self) -> None:
        trip = {"destinations": [{
            "name": "Moab",
            "ai_content": {"top_attractions": [
                {"name": f"P{i}", "url": f"https://example.test/{i}"} for i in range(200)
            ]},
        }]}
        d = FakeDiscoverer({})
        assert prefer_https_card_links(trip, d) == 0
        assert d.asked == []

    def test_an_upgradable_link_costs_one_request(self) -> None:
        trip = trip_with("http://example.test/x")
        d = FakeDiscoverer({"https://example.test/x": True})
        prefer_https_card_links(trip, d)
        assert d.asked == ["https://example.test/x"]

    def test_a_link_needing_the_redirect_costs_two(self) -> None:
        trip = trip_with("http://example.test/x")
        d = FakeDiscoverer({}, {"http://example.test/x": "https://example.test/x"})
        prefer_https_card_links(trip, d)
        assert d.asked == ["https://example.test/x", "http://example.test/x"]
