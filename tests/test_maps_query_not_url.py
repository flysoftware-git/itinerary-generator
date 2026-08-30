"""An attraction must not be "resolved" by a link that cannot verify.

_item_has_verified_url refuses a Google Maps text query: it is a best guess,
never confirmed to be about the right place. The direct batch accepted one as
an attraction's url anyway. That did two things at once -- stored a url
guaranteed to be rejected downstream, and marked the item resolved, which
suppresses the paid per-item search that only fires for unresolved items
(url_discovery.py, "It fires only for items the direct batch already failed
to resolve").

Measured on the 2026-08-29 production runs: Prague Castle, St. Vitus
Cathedral, Balanced Rock, Navajo Loop Trail and Queen's Garden Trail were all
deleted as unfindable while their official pages ranked second in a plain
search (nps.gov/arch/planyourvisit/balancedrock.htm, hrad.cz/en/prague-castle).

test-coverage.md documents Maps links as usable "only after direct-batch and
ordinary web search paths are exhausted", so this restores documented
behaviour rather than changing policy. The equivalent rule already existed and
was tested for restaurants
(test_retain_url_rejects_google_maps_search_for_named_restaurant_in_enforce_mode);
only the attraction path lacked it.
"""

import pytest

from generator.url_discovery import URLDiscoverer

TEXT_QUERY = "https://www.google.com/maps/search/?api=1&query=Balanced%20Rock"
COORD_QUERY = "https://www.google.com/maps/search/?api=1&query=38.7013,-109.5645"
REAL_PAGE = "https://www.nps.gov/arch/planyourvisit/balancedrock.htm"
MAPS_PLACE = "https://www.google.com/maps/place/Balanced+Rock/"


class TestUnverifiableMapsQuery:
    def test_a_text_query_is_refused(self):
        assert URLDiscoverer._is_unverifiable_maps_query(TEXT_QUERY) is True

    def test_a_coordinate_query_is_kept(self):
        """Owner decision 2026-08-22: a lat,lng link names a point, not a guess."""
        assert URLDiscoverer._is_unverifiable_maps_query(COORD_QUERY) is False

    def test_a_real_page_is_untouched(self):
        assert URLDiscoverer._is_unverifiable_maps_query(REAL_PAGE) is False

    def test_a_maps_place_url_is_untouched(self):
        assert URLDiscoverer._is_unverifiable_maps_query(MAPS_PLACE) is False

    @pytest.mark.parametrize("url", ["", "not a url", "https://example.com"])
    def test_non_maps_input_is_safe(self, url):
        assert URLDiscoverer._is_unverifiable_maps_query(url) is False


class TestBarsAgree:
    """The selection gate and the verification gate must refuse the same links.

    The defect was two different bars at the two ends of the pipeline. Any
    URL this refuses must also fail _item_has_verified_url, and any Maps link
    it permits must pass -- otherwise the mismatch reopens.
    """

    @pytest.mark.parametrize("url", [TEXT_QUERY, COORD_QUERY, REAL_PAGE, MAPS_PLACE])
    def test_refusal_and_verification_agree(self, url):
        refused = URLDiscoverer._is_unverifiable_maps_query(url)
        verified = URLDiscoverer._item_has_verified_url({"url": url})
        assert refused != verified, (
            f"{url}: selection refuses={refused}, verification accepts={verified}"
        )
