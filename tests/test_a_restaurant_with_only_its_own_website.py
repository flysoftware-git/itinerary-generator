"""A restaurant on neither Google Maps nor TripAdvisor still has a website.

WHAT WENT WRONG
---------------
The Old Hickory guide dropped fourteen real businesses from Lebanon, Tennessee
in one build -- essentially the whole town's restaurant list -- each one
"removed for no verified URL". They are real: Cedar City Brewing Company is on
Tennessee's own tourism site, Lebanon Public House is at 107 West Main St, and
The Mill at Lebanon is an 1908 wool mill on the National Register.

The per-item search had exactly two passes, `site_filter="google.com/maps"` and
`site_filter="tripadvisor.com"`. A small-town restaurant on neither got nothing
-- while `cedarcitybrewing.com` was the FIRST organic result for all three of
the engine's own query variants. Attractions have had a broad pass all along;
restaurants never did.

WHAT IS PINNED
--------------
That the third pass exists and admits only the restaurant's own site, by the
same test the official-site upgrade already applies. This is that rule reaching
an item with nothing to upgrade, rather than a new kind of guess.
"""

from __future__ import annotations

import pytest

from generator.url_discovery import URLDiscoverer

DEST = "Lebanon, Tennessee"


class TestTheDomainMustBeTheBusinessAndNotTheTown:
    """The gate the new pass leans on, and the hole that was in it.

    `_domain_matches_item_name` falls back to the first long token of the name.
    For "Lebanon Public House" in Lebanon that token is the TOWN, so any
    `*lebanon*` domain matched -- and sociallebanon.com, which is Town Square
    Social, matched a different business on the same square.
    """

    @pytest.mark.parametrize("url,name", [
        ("https://cedarcitybrewing.com/", "Cedar City Brewing Company"),
        ("https://themillatlebanon.com/", "The Mill At Lebanon"),
        ("https://benjathaistgeorge.com/", "Benja Thai & Sushi"),
    ])
    def test_a_restaurants_own_domain_is_recognised(self, url, name):
        assert URLDiscoverer._domain_matches_item_name(url, name, DEST)

    @pytest.mark.parametrize("url,name", [
        ("https://www.sociallebanon.com/", "Lebanon Public House"),
        ("https://www.tennlakesbrewing.com/", "Cedar City Brewing Company"),
        ("https://www.tennlakesbrewing.com/", "The Mill At Lebanon"),
    ])
    def test_another_business_is_not(self, url, name):
        assert not URLDiscoverer._domain_matches_item_name(url, name, DEST)

    def test_a_name_that_is_only_the_town_matches_nothing(self):
        """"Lebanon" alone says nothing about which Lebanon business this is."""
        assert not URLDiscoverer._domain_matches_item_name(
            "https://www.visitlebanontn.com/", "Lebanon", DEST
        )

    def test_without_a_destination_it_behaves_as_before(self):
        """The parameter is optional, so existing callers are untouched."""
        assert URLDiscoverer._domain_matches_item_name(
            "https://www.sociallebanon.com/", "Lebanon Public House"
        )


class TestTheThirdPassExists:
    def test_the_search_sequence_reaches_an_open_search(self):
        """Pass 1 maps, pass 2 TripAdvisor, pass 3 the open web.

        Asserted on the source because the sequence is the defect: the first
        two passes were present and correct, and the whole bug was the absence
        of the third.
        """
        import inspect

        src = inspect.getsource(URLDiscoverer._discover_restaurants)
        assert 'site_filter="google.com/maps"' in src
        assert 'site_filter="tripadvisor.com"' in src
        assert "own_site_accepted" in src, "no open-web pass for restaurants"
        # The open pass must not carry a site filter, or it is pass 1 again.
        after = src.split("own_site")[0].rsplit("if not url:", 1)[-1]
        assert "site_filter" not in after, "the third pass is still filtered to a host"

    def test_the_open_pass_is_gated_on_the_name_and_the_destination(self):
        import inspect

        src = inspect.getsource(URLDiscoverer._discover_restaurants)
        assert "_domain_matches_item_name(own_site, rest_name, dest_name)" in src, (
            "the open pass must admit only the restaurant's own domain, and must "
            "pass the destination so a town-named domain cannot stand in for it"
        )

    def test_a_third_party_page_is_not_taken_as_an_own_site(self):
        """Yelp and the like are pages ABOUT a restaurant, and pass 2 is where
        a listing belongs. The open pass is for the restaurant's own site."""
        import inspect

        src = inspect.getsource(URLDiscoverer._discover_restaurants)
        assert "_is_third_party_restaurant_page(own_site)" in src

    def test_a_rejected_open_result_says_so(self):
        """A dropped restaurant with nothing in its trail is what made this
        take a full build to find."""
        import inspect

        src = inspect.getsource(URLDiscoverer._discover_restaurants)
        assert "own_site_rejected_name_mismatch" in src
