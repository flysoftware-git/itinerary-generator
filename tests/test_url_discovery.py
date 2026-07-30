"""Tests for generator.url_discovery"""
import pytest
from unittest.mock import MagicMock, patch
from generator.url_discovery import URLDiscoverer, _build_query_variants


def test_build_query_variants_returns_four():
    variants = _build_query_variants("Angels Landing", "Zion National Park", "trail")
    assert len(variants) == 4


def test_build_query_variants_specificity():
    variants = _build_query_variants("Spotted Dog Cafe", "Springdale", "restaurant")
    # First variant should be most specific (quoted name)
    assert '"Spotted Dog Cafe"' in variants[0]
    # Last variant should be broadest (no category)
    assert "restaurant" not in variants[-1]


def test_build_query_variants_compacts_overly_long_categories():
    variants = _build_query_variants(
        "Piedra Falls",
        "Pagosa Springs",
        "trail hike attraction official site",
    )
    assert len(variants) == 4
    # Category is intentionally compacted to avoid over-constraining search.
    assert "official" not in variants[0].lower()
    assert "site" not in variants[0].lower()
    assert "trail" in variants[0].lower()
    assert "hike" in variants[0].lower()


def test_discover_all_adds_urls_to_attractions():
    trip = {
        "destinations": [
            {
                "name": "Zion National Park",
                "nps_park_code": "zion",
                "ai_content": {
                    "top_attractions": [{"name": "Angels Landing", "description": "Great hike"}],
                    "dinner_recommendations": [],
                    "getting_here": {"en_route_stops": []},
                },
                "scenic_drives": [],
            }
        ]
    }
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._key = "fake_key"
    discoverer._session = MagicMock()

    with patch.object(discoverer, "_search_first", return_value="https://www.nps.gov/zion/angels"):
        discoverer.discover_all(trip)
    
    attr = trip["destinations"][0]["ai_content"]["top_attractions"][0]
    assert attr["url"] == "https://www.nps.gov/zion/angels"


def test_discover_all_uses_google_fallback_for_missing_url():
    trip = {
        "destinations": [
            {
                "name": "Moab, Utah",
                "nps_park_code": None,
                "ai_content": {
                    "top_attractions": [{"name": "Dead Horse Point", "description": "Viewpoint"}],
                    "dinner_recommendations": [],
                    "getting_here": {"en_route_stops": []},
                },
                "scenic_drives": [],
            }
        ]
    }
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._key = "fake_key"
    discoverer._session = MagicMock()

    with patch.object(discoverer, "_search_first", return_value=None):
        discoverer.discover_all(trip)

    attr = trip["destinations"][0]["ai_content"]["top_attractions"][0]
    # When all variants fail, url is empty string (fallback is Google search URL)
    assert isinstance(attr["url"], str)


def test_restaurant_discovery_two_pass():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._key = "fake_key"
    discoverer._session = MagicMock()

    call_log = []

    def fake_search(variants, site_filter=None, site_hint="", **_kwargs):
        call_log.append(site_filter)
        if site_filter == "google.com/maps":
            return None  # First pass fails
        if site_filter == "tripadvisor.com":
            return "https://www.tripadvisor.com/Restaurant_Test"
        return None

    ai = {
        "dinner_recommendations": [{"name": "Test Restaurant"}],
        "top_attractions": [],
        "getting_here": {"en_route_stops": []},
    }

    with patch.object(discoverer, "_search_first", side_effect=fake_search):
        discoverer._discover_restaurants(ai, dest_name="Moab")

    assert "google.com/maps" in call_log
    assert "tripadvisor.com" in call_log
    assert ai["dinner_recommendations"][0]["url"] == "https://www.tripadvisor.com/Restaurant_Test"


def test_restaurant_discovery_uses_ai_url_candidates_before_search_passes():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    ai = {
        "dinner_recommendations": [
            {
                "name": "The Spotted Dog Cafe",
                "url_candidates": [
                    "https://www.tripadvisor.com/Restaurant_Review-g57119-d123456-Reviews-The_Spotted_Dog_Cafe-Springdale_Utah.html"
                ],
            }
        ],
        "top_attractions": [],
        "getting_here": {"en_route_stops": []},
    }

    with patch.object(discoverer, "_search_first", return_value=None) as mock_search:
        with patch.object(discoverer, "_retain_discovered_url", side_effect=lambda url, *_args, **_kwargs: url):
            discoverer._discover_restaurants(ai, dest_name="Zion National Park")

    assert "tripadvisor.com" in ai["dinner_recommendations"][0]["url"]
    mock_search.assert_not_called()


def test_alltrails_trail_url_requires_matching_page_content():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Navajo Loop Trail Bryce Canyon hike details and reviews",
    )

    url = "https://www.alltrails.com/trail/us/utah/navajo-loop-trail"
    assert discoverer._is_alltrails_trail_url(url)
    assert discoverer._is_relevant_result(url, "Navajo Loop Trail", "Bryce Canyon National Park")


def test_search_strict_accepts_live_alltrails_trail_with_matching_content():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Navajo Loop Trail in Bryce Canyon National Park hiking guide",
    )
    discoverer._search.search.return_value = [
        {"url": "https://www.alltrails.com/trail/us/utah/navajo-loop-trail"}
    ]

    result = discoverer._search_first_strict(
        query_variants=['"Navajo Loop Trail" Bryce Canyon National Park trail hiking'],
        site_filter="alltrails.com",
        site_hint=None,
        item_name="Navajo Loop Trail",
        dest_name="Bryce Canyon National Park",
        allow_alltrails=True,
    )

    assert result == "https://www.alltrails.com/trail/us/utah/navajo-loop-trail"
    discoverer._url_validator.verify_url.assert_not_called()


def test_search_strict_rejects_alltrails_non_trail_paths_under_alltrails_filter():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._search.search.return_value = [
        {"url": "https://www.alltrails.com/blog/angels-landing-zion"},
        {
            "url": "https://www.alltrails.com/trail/us/utah/angels-landing-trail",
            "name": "Angels Landing Trail in Zion National Park | AllTrails",
            "snippet": "2.0 mile out and back trail with reviews, maps, and route details.",
        },
    ]

    with patch.object(
        discoverer,
        "_fetch_page_text",
        return_value=(
            True,
            200,
            "Angels Landing Trail in Zion National Park. 2.0 mile out and back trail with route details and reviews.",
        ),
    ):
        result = discoverer._search_first_strict(
            query_variants=['"Angels Landing" Zion National Park trail hiking'],
            site_filter="alltrails.com",
            site_hint=None,
            item_name="Angel's Landing",
            dest_name="Zion National Park",
            allow_alltrails=True,
        )

    assert result == "https://www.alltrails.com/trail/us/utah/angels-landing-trail"


def test_search_strict_prefers_exact_alltrails_slug_over_via_variant():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Angels Landing Trail in Zion with route details and reviews.",
    )
    discoverer._search.search.return_value = [
        {"url": "https://www.alltrails.com/trail/us/utah/angels-landing-via-west-rim-trail"},
        {"url": "https://www.alltrails.com/trail/us/utah/angels-landing-trail"},
    ]

    result = discoverer._search_first_strict(
        query_variants=['"Angels Landing" Zion National Park trail hiking'],
        site_filter="alltrails.com",
        site_hint=None,
        item_name="Angel's Landing",
        dest_name="Zion National Park",
        allow_alltrails=True,
    )

    assert result == "https://www.alltrails.com/trail/us/utah/angels-landing-trail"


def test_search_alltrails_for_trail_upgrades_via_variant_to_verified_canonical_slug():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    with patch.object(
        discoverer,
        "_search_first",
        return_value="https://www.alltrails.com/trail/us/utah/angels-landing-via-west-rim-trail",
    ):
        with patch.object(discoverer, "_fetch_page_text") as mock_fetch:
            def fake_fetch(url, timeout=8):
                if url.endswith("/angels-landing-trail"):
                    return True, 200, "Angels Landing Trail route details and reviews"
                return False, "timeout", ""

            mock_fetch.side_effect = fake_fetch
            result = discoverer._search_alltrails_for_trail("Angels Landing", "Zion National Park")

    assert result == "https://www.alltrails.com/trail/us/utah/angels-landing-trail"


def test_search_alltrails_for_trail_upgrades_destination_suffixed_slug_to_canonical():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    with patch.object(
        discoverer,
        "_search_first",
        return_value="https://www.alltrails.com/trail/us/utah/canyon-overlook-trail-zion-national-park",
    ):
        with patch.object(discoverer, "_fetch_page_text") as mock_fetch:
            def fake_fetch(url, timeout=8):
                if url.endswith("/canyon-overlook-trail"):
                    return True, 200, "Canyon Overlook Trail route details and reviews"
                return False, "timeout", ""

            mock_fetch.side_effect = fake_fetch
            result = discoverer._search_alltrails_for_trail("Canyon Overlook Trail", "Zion National Park")

    assert result == "https://www.alltrails.com/trail/us/utah/canyon-overlook-trail"


def test_search_alltrails_for_trail_strips_tracking_query_from_result_url():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    with patch.object(
        discoverer,
        "_search_first",
        return_value="https://www.alltrails.com/trail/us/utah/canyon-overlook-trail?u=i",
    ):
        with patch.object(discoverer, "_fetch_page_text", return_value=(False, "timeout", "")):
            result = discoverer._search_alltrails_for_trail("Canyon Overlook Trail", "Zion National Park")

    assert result == "https://www.alltrails.com/trail/us/utah/canyon-overlook-trail"


def test_alltrails_relevance_does_not_require_destination_name_in_page_text():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Bear Creek Trail Colorado hiking route details and reviews",
    )

    url = "https://www.alltrails.com/trail/us/colorado/bear-creek-trail"
    assert discoverer._is_relevant_result(url, "Bear Creek Trail", "Telluride")


def test_search_first_alltrails_can_use_variant_beyond_default_attempt_limit():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Jud Wiebe Trail Colorado hiking details and route",
    )

    call_count = {"n": 0}

    def fake_search(_query, count=10):
        call_count["n"] += 1
        if call_count["n"] == 5:
            return [{"url": "https://www.alltrails.com/trail/us/colorado/jud-wiebe-trail"}]
        return []

    discoverer._search.search.side_effect = fake_search

    variants = [
        '"Jud Wiebe Trail" Telluride trail hiking',
        '"Jud Wiebe Trail" Telluride',
        'Jud Wiebe Trail Telluride trail',
        'Jud Wiebe Trail Telluride',
        '"Jud Wiebe Trail" trail',
        'Jud Wiebe Trail',
    ]

    result = discoverer._search_first(
        variants,
        site_filter="alltrails.com",
        item_name="Jud Wiebe Trail",
        dest_name="Telluride",
        max_attempts=len(variants),
    )

    assert result == "https://www.alltrails.com/trail/us/colorado/jud-wiebe-trail"
    assert call_count["n"] >= 5


def test_search_strict_accepts_alltrails_with_strong_metadata_when_page_fetch_fails():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.side_effect = Exception("timeout")
    discoverer._search.search.return_value = [
        {
            "url": "https://www.alltrails.com/trail/us/colorado/jud-wiebe-memorial-trail",
            "name": "Jud Wiebe Memorial Trail, Colorado - 4,059 Reviews, Map | AllTrails",
            "snippet": "Popular hiking trail near Telluride with route details and reviews.",
        }
    ]

    result = discoverer._search_first_strict(
        query_variants=['"Jud Wiebe Trail" Telluride trail hiking'],
        site_filter="alltrails.com",
        site_hint=None,
        item_name="Jud Wiebe Trail",
        dest_name="Telluride",
        allow_alltrails=True,
    )

    assert result == "https://www.alltrails.com/trail/us/colorado/jud-wiebe-memorial-trail"


def test_search_strict_rejects_alltrails_on_fetch_failure_without_metadata_when_slug_matches():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.side_effect = Exception("timeout")
    discoverer._search.search.return_value = [
        {
            "url": "https://www.alltrails.com/trail/us/utah/angels-landing-trail",
        }
    ]

    result = discoverer._search_first_strict(
        query_variants=['"Angels Landing" Zion National Park trail hiking'],
        site_filter="alltrails.com",
        site_hint=None,
        item_name="Angel's Landing",
        dest_name="Zion National Park",
        allow_alltrails=True,
    )

    assert result is None


def test_search_strict_rejects_alltrails_on_401_without_candidate_metadata():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._search.search.return_value = [
        {
            "url": "https://www.alltrails.com/trail/us/utah/angels-landing-trail",
        }
    ]

    with patch.object(discoverer, "_fetch_page_text", return_value=(False, 401, "")):
        result = discoverer._search_first_strict(
            query_variants=['"Angels Landing" Zion National Park trail hiking'],
            site_filter="alltrails.com",
            site_hint=None,
            item_name="Angel's Landing",
            dest_name="Zion National Park",
            allow_alltrails=True,
        )

    assert result is None


def test_search_strict_rejects_single_token_alltrails_on_403_without_destination_metadata():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._search.search.return_value = [
        {
            "url": "https://www.alltrails.com/trail/us/utah/the-narrows-top-down",
            "name": "The Narrows Top Down - 500 reviews | AllTrails",
            "snippet": "Popular hiking route with permits and river crossing notes.",
        }
    ]

    with patch.object(discoverer, "_fetch_page_text", return_value=(False, 403, "")):
        result = discoverer._search_first_strict(
            query_variants=['"The Narrows" Zion National Park trail hiking'],
            site_filter="alltrails.com",
            site_hint=None,
            item_name="The Narrows",
            dest_name="Zion National Park",
            allow_alltrails=True,
        )

    assert result is None


def test_search_strict_accepts_single_token_alltrails_on_403_with_destination_metadata():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._search.search.return_value = [
        {
            "url": "https://www.alltrails.com/trail/us/utah/the-narrows-top-down",
            "name": "The Narrows Top Down in Zion National Park | AllTrails",
            "snippet": "Classic Zion river hike with permit logistics and shuttle context.",
        }
    ]

    with patch.object(discoverer, "_fetch_page_text", return_value=(False, 403, "")):
        result = discoverer._search_first_strict(
            query_variants=['"The Narrows" Zion National Park trail hiking'],
            site_filter="alltrails.com",
            site_hint=None,
            item_name="The Narrows",
            dest_name="Zion National Park",
            allow_alltrails=True,
        )

    assert result == "https://www.alltrails.com/trail/us/utah/the-narrows-top-down"


def test_search_strict_rejects_blocked_alltrails_when_config_disabled():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._allow_blocked_alltrails = False
    discoverer._search.search.return_value = [
        {
            "url": "https://www.alltrails.com/trail/us/utah/the-narrows-top-down",
            "name": "The Narrows Top Down in Zion National Park | AllTrails",
            "snippet": "Classic Zion river hike with permit logistics and shuttle context.",
        }
    ]

    with patch.object(discoverer, "_fetch_page_text", return_value=(False, 403, "")):
        result = discoverer._search_first_strict(
            query_variants=['"The Narrows" Zion National Park trail hiking'],
            site_filter="alltrails.com",
            site_hint=None,
            item_name="The Narrows",
            dest_name="Zion National Park",
            allow_alltrails=True,
        )

    assert result is None


def test_alltrails_rejects_candidate_when_miles_exceed_configured_max():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._max_trail_miles = 3.0

    candidate = {
        "url": "https://www.alltrails.com/trail/us/utah/fairyland-loop-trail-bryce-canyon-national-park",
        "name": "Fairyland Loop Trail - Bryce Canyon National Park",
        "snippet": "8.0 mile heavily trafficked loop trail in Bryce Canyon National Park",
    }

    with patch.object(discoverer, "_fetch_page_text", return_value=(False, 403, "")):
        ok = discoverer._is_relevant_result(
            candidate["url"],
            "Fairyland Loop Trail",
            "Bryce Canyon National Park",
            candidate=candidate,
        )

    assert ok is False


def test_alltrails_accepts_candidate_when_miles_within_configured_max():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._max_trail_miles = 3.0
    discoverer._allow_blocked_alltrails = True

    candidate = {
        "url": "https://www.alltrails.com/trail/us/utah/canyon-overlook-trail",
        "name": "Canyon Overlook Trail",
        "snippet": "1.0 mile out and back trail in Zion National Park",
    }

    with patch.object(discoverer, "_fetch_page_text", return_value=(False, 403, "")):
        ok = discoverer._is_relevant_result(
            candidate["url"],
            "Canyon Overlook Trail",
            "Zion National Park",
            candidate=candidate,
        )

    assert ok is True


def test_search_strict_rejects_alltrails_soft_404_and_falls_back_to_none():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="404 We've reached the end of the trail. The page you're looking for either doesn't exist or has a new link.",
    )
    discoverer._search.search.return_value = [
        {"url": "https://www.alltrails.com/trail/us/utah/st-george-dinosaur-discovery-site"}
    ]

    result = discoverer._search_first_strict(
        query_variants=['"St. George Dinosaur Discovery Site" St. George Utah trail hiking'],
        site_filter="alltrails.com",
        site_hint=None,
        item_name="St. George Dinosaur Discovery Site",
        dest_name="St. George, Utah",
        allow_alltrails=True,
    )

    assert result is None


def test_alltrails_relevance_does_not_reject_generic_marketing_phrase_only():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Angels Landing Trail route details and reviews. Find your next trail nearby.",
    )

    url = "https://www.alltrails.com/trail/us/utah/angels-landing-trail"
    assert discoverer._is_relevant_result(url, "Angel's Landing", "Zion National Park")


def test_search_strict_rejects_wrong_but_live_alltrails_trail_page():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Red Cliffs Recreation Area trail guide for hikers in St. George, Utah.",
    )
    discoverer._search.search.return_value = [
        {"url": "https://www.alltrails.com/trail/us/utah/red-cliffs-recreation-area-trail"}
    ]

    result = discoverer._search_first_strict(
        query_variants=['"Red Cliffs Desert Reserve" St. George Utah trail hiking'],
        site_filter="alltrails.com",
        site_hint=None,
        item_name="Red Cliffs Desert Reserve",
        dest_name="St. George, Utah",
        allow_alltrails=True,
    )

    assert result is None


def test_search_strict_accepts_alltrails_when_slug_uses_possessive_variant():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Queen's Garden Trail Bryce Canyon hiking route details and reviews",
    )
    discoverer._search.search.return_value = [
        {"url": "https://www.alltrails.com/trail/us/utah/queen-s-garden-trail"}
    ]

    result = discoverer._search_first_strict(
        query_variants=['"Queens Garden Trail" Bryce Canyon National Park trail hiking'],
        site_filter="alltrails.com",
        site_hint=None,
        item_name="Queens Garden Trail",
        dest_name="Bryce Canyon National Park",
        allow_alltrails=True,
    )

    assert result == "https://www.alltrails.com/trail/us/utah/queen-s-garden-trail"


def test_search_strict_rejects_generic_404_landing_page():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.verify_url.return_value = (True, 200)
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Visit Utah 404 page",
    )
    discoverer._search.search.return_value = [
        {"url": "https://www.visitutah.com/404errorpage"}
    ]

    result = discoverer._search_first_strict(
        query_variants=['"Pioneer Park" St. George Utah trail hike attraction official site'],
        site_filter=None,
        site_hint=None,
        item_name="Pioneer Park",
        dest_name="St. George, Utah",
        allow_alltrails=False,
    )

    assert result is None


def test_search_strict_rejects_generic_nps_things2do_page():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.verify_url.return_value = (True, 200)
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Capitol Reef National Park things to do overview page",
    )
    discoverer._search.search.return_value = [
        {"url": "https://www.nps.gov/care/planyourvisit/things2do.htm"}
    ]

    result = discoverer._search_first_strict(
        query_variants=['"Red Canyon" Capitol Reef National Park attraction'],
        site_filter="nps.gov",
        site_hint="site:nps.gov/care",
        item_name="Red Canyon",
        dest_name="Capitol Reef National Park",
        allow_alltrails=False,
    )

    assert result is None


def test_search_strict_accepts_blm_url_when_ssl_fallback_fetch_succeeds():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.verify_url.return_value = (True, 200)
    discoverer._url_validator.get_text.return_value = (True, 200, "Wilson Arch trailhead and visitor information near Moab Utah")
    discoverer._search.search.return_value = [
        {
            "url": "https://www.blm.gov/visit/wilson-arch",
            "name": "Wilson Arch",
            "snippet": "BLM information page",
        }
    ]

    result = discoverer._search_first_strict(
        query_variants=['"Wilson Arch" Moab Utah attraction'],
        site_filter=None,
        site_hint=None,
        item_name="Wilson Arch",
        dest_name="Moab, Utah",
        allow_alltrails=False,
    )

    assert result == "https://www.blm.gov/visit/wilson-arch"


def test_search_strict_rejects_npgallery_asset_detail_page():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.verify_url.return_value = (True, 200)
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="National Register of Historic Places asset detail page",
    )
    discoverer._search.search.return_value = [
        {"url": "https://npgallery.nps.gov/NRHP/AssetDetail/7c8e5f3a-8b2d-4f1e-9c6a-2d5e8f7a9b0c"}
    ]

    result = discoverer._search_first_strict(
        query_variants=['"St. George Historic District" St. George Utah attraction landmark official site'],
        site_filter=None,
        site_hint=None,
        item_name="St. George Historic District",
        dest_name="St. George, Utah",
        allow_alltrails=False,
    )

    assert result is None


def test_search_strict_rejects_hallucinated_trail_with_partial_text_match():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.verify_url.return_value = (True, 200)
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Dixie trail in St. George, Utah.",
    )
    discoverer._search.search.return_value = [
        {"url": "https://www.dixie.edu/trails/dixie-trail"}
    ]

    result = discoverer._search_first_strict(
        query_variants=['"Dixie State University Trail" St. George Utah trail hike attraction official site'],
        site_filter=None,
        site_hint=None,
        item_name="Dixie State University Trail",
        dest_name="St. George, Utah",
        allow_alltrails=False,
    )

    assert result is None


def test_search_strict_rejects_numbered_suffix_alltrails_slug_variant():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._search.search.return_value = [
        {
            "url": "https://www.alltrails.com/trail/us/utah/cassidy-arch-trail--2",
            "name": "Cassidy Arch Trail | AllTrails",
            "snippet": "2.7 mile out and back trail near Capitol Reef National Park.",
        }
    ]

    with patch.object(discoverer, "_fetch_page_text", return_value=(False, 403, "")):
        result = discoverer._search_first_strict(
            query_variants=['"Cassidy Arch" Capitol Reef National Park trail hiking'],
            site_filter="alltrails.com",
            site_hint=None,
            item_name="Cassidy Arch",
            dest_name="Capitol Reef National Park",
            allow_alltrails=True,
        )

    assert result is None


def test_normalize_restaurant_url_rejects_maps_directions_links():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    url = "https://www.google.com/maps/dir//Bit+%26+Spur+Restaurant+%26+Saloon,+1212+Zion+Park+Blvd,+Springdale,+UT+84767"
    assert discoverer._normalize_restaurant_url(url) == ""


def test_normalize_restaurant_url_rejects_maps_place_links():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    url = "https://www.google.com/maps/place/Zion+Pizza+%26+Noodle+Co./@37.1886,-112.9985,17z"
    assert discoverer._normalize_restaurant_url(url) == ""


def test_restaurant_discovery_falls_back_when_maps_place_result_is_returned():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    ai = {
        "dinner_recommendations": [{"name": "Zion Pizza & Noodle Co."}],
        "top_attractions": [],
        "getting_here": {"en_route_stops": []},
    }

    def fake_search(variants, site_filter=None, **_kwargs):
        if site_filter == "google.com/maps":
            return "https://www.google.com/maps/place/Zion+Pizza+%26+Noodle+Co./@37.1886,-112.9985,17z"
        if site_filter == "tripadvisor.com":
            return None
        return None

    with patch.object(discoverer, "_search_first", side_effect=fake_search):
        with patch.object(discoverer, "_resolve_ai_candidate_url", return_value=None):
            discoverer._discover_restaurants(ai, dest_name="Zion National Park")

    out_url = ai["dinner_recommendations"][0]["url"]
    assert out_url.startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "maps/place/" not in out_url
    assert "Zion%20National%20Park" in out_url


def test_restaurant_maps_query_text_always_keeps_destination_context():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    query = discoverer._restaurant_maps_query_text("Zion Pizza & Noodle Co.", "Zion National Park")
    assert "Zion National Park" in query
    assert "restaurant" in query.lower()


def test_non_hike_attractions_disallow_alltrails_results():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()

    discoverer._search.search.return_value = [
        {"url": "https://www.alltrails.com/trail/us/utah/st-george-dinosaur-discovery-site"},
        {"url": "https://utahdinosaurtracks.com/discovery-site"},
    ]
    discoverer._url_validator.verify_url.return_value = (True, None)
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="St. George Dinosaur Discovery Site official information for St. George, Utah.",
    )

    result = discoverer._search_first_strict(
        query_variants=['"St. George Dinosaur Discovery Site" St. George Utah attraction'],
        site_filter=None,
        site_hint=None,
        item_name="St. George Dinosaur Discovery Site",
        dest_name="St. George, Utah",
        allow_alltrails=False,
    )

    assert result == "https://utahdinosaurtracks.com/discovery-site"


def test_search_strict_rejects_google_maps_place_restaurant_urls():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()

    discoverer._search.search.return_value = [
        {
            "url": "https://www.google.com/maps/place/Zion+Pizza+%26+Noodle+Co./@37.1885,-112.9995,17z",
            "name": "Zion Pizza & Noodle Co.",
            "snippet": "4.4 stars 1200 reviews",
        }
    ]

    with patch.object(discoverer, "_is_specific_result_url", return_value=True):
        result = discoverer._search_first_strict(
            query_variants=['"Zion Pizza & Noodle Co." "Zion National Park" restaurant'],
            site_filter="google.com/maps",
            site_hint=None,
            item_name="Zion Pizza & Noodle Co.",
            dest_name="Zion National Park",
            allow_alltrails=False,
        )

    assert result is None


def test_trail_like_attraction_prefers_alltrails_even_when_type_is_not_hike():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    call_order = []

    def fake_search(variants, site_filter=None, **kwargs):
        call_order.append(site_filter or "")
        if site_filter == "alltrails.com":
            return "https://www.alltrails.com/trail/us/utah/the-narrows-top-down"
        return None

    ai = {
        "top_attractions": [
            {
                "name": "The Narrows",
                "type": "attraction",
                "description": "Iconic Zion hike through the Virgin River canyon.",
            }
        ]
    }

    with patch.object(discoverer, "_search_first", side_effect=fake_search):
        discoverer._discover_attractions(ai, "Zion National Park", "zion")

    assert ai["top_attractions"][0]["url"].startswith("https://www.alltrails.com/trail/")
    assert call_order[0] == "alltrails.com"


def test_trail_like_attraction_uses_ai_url_candidates_before_search():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    ai = {
        "top_attractions": [
            {
                "name": "Canyon Overlook Trail",
                "type": "hike",
                "description": "Short trail with expansive canyon views.",
                "url_candidates": [
                    "https://www.alltrails.com/trail/us/utah/canyon-overlook-trail?u=i",
                ],
            }
        ]
    }

    with patch.object(discoverer, "_search_alltrails_for_trail", return_value=None) as mock_alltrails_search:
        with patch.object(discoverer, "_retain_discovered_url", side_effect=lambda url, *_args, **_kwargs: url):
            discoverer._discover_attractions(ai, "Zion National Park", "zion")

    assert ai["top_attractions"][0]["url"] == "https://www.alltrails.com/trail/us/utah/canyon-overlook-trail"
    mock_alltrails_search.assert_not_called()


def test_trail_like_attraction_prefers_alltrails_for_riverside_walk_name():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    seen_site_filters = []

    def fake_search(variants, site_filter=None, **kwargs):
        seen_site_filters.append(site_filter or "")
        if site_filter == "alltrails.com":
            return "https://www.alltrails.com/trail/us/utah/the-zion-narrows-riverside-walk"
        return None

    ai = {
        "top_attractions": [
            {
                "name": "The Zion Narrows Riverside Walk",
                "type": "attraction",
                "description": "Iconic canyon route with river views.",
            }
        ]
    }

    with patch.object(discoverer, "_search_first", side_effect=fake_search):
        discoverer._discover_attractions(ai, "Zion National Park", "zion")

    assert ai["top_attractions"][0]["url"] == "https://www.alltrails.com/trail/us/utah/the-zion-narrows-riverside-walk"
    assert seen_site_filters[0] == "alltrails.com"


def test_trail_like_attraction_uses_description_phrase_this_trail_for_alltrails_first():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    seen_site_filters = []

    def fake_search(variants, site_filter=None, **kwargs):
        seen_site_filters.append(site_filter or "")
        if site_filter == "alltrails.com":
            return "https://www.alltrails.com/trail/us/utah/angels-landing-trail"
        return None

    ai = {
        "top_attractions": [
            {
                "name": "Angels Landing",
                "type": "attraction",
                "description": "This trail climbs through steep switchbacks to panoramic views.",
            }
        ]
    }

    with patch.object(discoverer, "_search_first", side_effect=fake_search):
        discoverer._discover_attractions(ai, "Zion National Park", "zion")

    assert ai["top_attractions"][0]["url"] == "https://www.alltrails.com/trail/us/utah/angels-landing-trail"
    assert seen_site_filters[0] == "alltrails.com"


def test_trail_like_attraction_handles_apostrophe_name_variant():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    seen_site_filters = []

    def fake_search(variants, site_filter=None, **kwargs):
        seen_site_filters.append(site_filter or "")
        if site_filter == "alltrails.com":
            return "https://www.alltrails.com/trail/us/utah/angels-landing-trail"
        return None

    ai = {
        "top_attractions": [
            {
                "name": "Angel's Landing",
                "type": "attraction",
                "description": "Iconic chain section and canyon views.",
            }
        ]
    }

    with patch.object(discoverer, "_search_first", side_effect=fake_search):
        discoverer._discover_attractions(ai, "Zion National Park", "zion")

    assert ai["top_attractions"][0]["url"] == "https://www.alltrails.com/trail/us/utah/angels-landing-trail"
    assert seen_site_filters[0] == "alltrails.com"


def test_place_level_attraction_not_forced_to_alltrails_from_generic_trail_wording():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    seen_site_filters = []

    def fake_search(variants, site_filter=None, **kwargs):
        seen_site_filters.append(site_filter or "")
        if site_filter == "nps.gov":
            return "https://stateparks.utah.gov/parks/snow-canyon/"
        return None

    ai = {
        "top_attractions": [
            {
                "name": "Snow Canyon State Park",
                "type": "attraction",
                "description": "This trail-rich park has lava tubes and overlooks.",
            }
        ]
    }

    with patch.object(discoverer, "_search_first", side_effect=fake_search):
        discoverer._discover_attractions(ai, "St. George, Utah", None)

    assert ai["top_attractions"][0]["url"].startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "alltrails.com" not in seen_site_filters


def test_place_level_snow_canyon_search_disallows_alltrails_candidates():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    observed_allow_alltrails: list[bool] = []

    def fake_search(variants, site_filter=None, **kwargs):
        observed_allow_alltrails.append(bool(kwargs.get("allow_alltrails", True)))
        return None

    ai = {
        "top_attractions": [
            {
                "name": "Snow Canyon State Park",
                "type": "hike",
                "description": "Trail-rich desert park with overlooks.",
            }
        ]
    }

    with patch.object(discoverer, "_search_first", side_effect=fake_search):
        discoverer._discover_attractions(ai, "St. George, Utah", None)

    assert observed_allow_alltrails
    assert all(flag is False for flag in observed_allow_alltrails)


def test_plain_park_name_not_forced_to_alltrails():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    seen_site_filters = []

    def fake_search(variants, site_filter=None, **kwargs):
        seen_site_filters.append(site_filter or "")
        return None

    ai = {
        "top_attractions": [
            {
                "name": "Pioneer Park",
                "type": "hike",
                "description": "Local sandstone park with short connectors.",
            }
        ]
    }

    with patch.object(discoverer, "_search_first", side_effect=fake_search):
        discoverer._discover_attractions(ai, "St. George, Utah", None)

    assert "alltrails.com" not in seen_site_filters


def test_place_level_attraction_not_forced_to_alltrails_even_when_type_is_hike():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    seen_site_filters = []

    def fake_search(variants, site_filter=None, **kwargs):
        seen_site_filters.append(site_filter or "")
        return None

    ai = {
        "top_attractions": [
            {
                "name": "Red Cliffs Desert Reserve",
                "type": "hike",
                "description": "Large protected landscape with multiple trailheads.",
            }
        ]
    }

    with patch.object(discoverer, "_search_first", side_effect=fake_search):
        discoverer._discover_attractions(ai, "St. George, Utah", None)

    assert ai["top_attractions"][0]["url"].startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "alltrails.com" not in seen_site_filters


def test_petroglyph_place_level_attraction_not_forced_to_alltrails():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()

    seen_site_filters: list[str | None] = []

    def fake_search_first(variants, site_filter=None, **kwargs):
        seen_site_filters.append(site_filter)
        if site_filter == "nps.gov":
            return "https://www.nps.gov/care/learn/historyculture/fremont-culture.htm"
        return None

    with patch.object(discoverer, "_search_first", side_effect=fake_search_first):
        ai = {
            "top_attractions": [
                {
                    "name": "Fremont Petroglyphs",
                    "type": "attraction",
                    "description": "Rock art panels accessible from a short pullout stop.",
                }
            ],
            "dinner_recommendations": [],
            "getting_here": {"en_route_stops": []},
        }
        discoverer._discover_attractions(ai, "Capitol Reef National Park", "care", "October 11-13, 2026")

    assert "alltrails.com" not in [s for s in seen_site_filters if s]
    assert ai["top_attractions"][0]["url"].startswith("https://www.nps.gov/")


def test_viewpoint_place_level_attraction_not_forced_to_alltrails():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()

    seen_site_filters: list[str | None] = []

    def fake_search_first(variants, site_filter=None, **kwargs):
        seen_site_filters.append(site_filter)
        if site_filter == "nps.gov":
            return "https://www.nps.gov/care/planyourvisit/chimney-rock-trail.htm"
        return None

    with patch.object(discoverer, "_search_first", side_effect=fake_search_first):
        ai = {
            "top_attractions": [
                {
                    "name": "Capitol Reef Viewpoint",
                    "type": "attraction",
                    "description": "Roadside viewpoint with broad canyon panoramas.",
                }
            ],
            "dinner_recommendations": [],
            "getting_here": {"en_route_stops": []},
        }
        discoverer._discover_attractions(ai, "Capitol Reef National Park", "care", "October 11-13, 2026")

    assert "alltrails.com" not in [s for s in seen_site_filters if s]


def test_search_strict_nps_broad_pass_rejects_generic_index_page_without_item_signal():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.verify_url.return_value = (True, 200)
    discoverer._search.search.return_value = [
        {
            "url": "https://www.nps.gov/care/index.htm",
            "name": "Capitol Reef National Park",
            "snippet": "Official National Park Service site",
        }
    ]

    result = discoverer._search_first_strict(
        query_variants=['"Gifford Homestead" Capitol Reef National Park attraction'],
        site_filter="nps.gov",
        site_hint=None,
        item_name="Gifford Homestead",
        dest_name="Capitol Reef National Park",
        allow_alltrails=False,
    )

    assert result is None


def test_audit_keeps_alltrails_for_trail_like_non_hike_attraction():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="The Narrows trail in Zion National Park hiking guide and route details.",
    )

    trip = {
        "destinations": [
            {
                "name": "Zion National Park",
                "ai_content": {
                    "top_attractions": [
                        {
                            "name": "The Narrows",
                            "type": "attraction",
                            "description": "Classic river hike.",
                            "url": "https://www.alltrails.com/trail/us/utah/the-narrows-top-down",
                        }
                    ],
                    "getting_here": {"en_route_stops": []},
                    "dinner_recommendations": [],
                },
                "scenic_drives": [],
                "cultural_events": {"has_events": False, "events": []},
            }
        ]
    }

    discoverer.audit_discovered_urls(trip)

    attraction = trip["destinations"][0]["ai_content"]["top_attractions"][0]
    assert attraction.get("url", "").startswith("https://www.alltrails.com/trail/")


def test_audit_keeps_alltrails_for_trail_like_when_text_fetch_fails_but_url_live():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.verify_url.return_value = (True, 200)
    discoverer._url_validator.session.get.side_effect = Exception("timeout")

    trip = {
        "destinations": [
            {
                "name": "Bryce Canyon National Park",
                "ai_content": {
                    "top_attractions": [
                        {
                            "name": "Queens Garden Trail",
                            "type": "attraction",
                            "description": "This trail descends through hoodoos.",
                            "url": "https://www.alltrails.com/trail/us/utah/queens-garden-trail",
                        }
                    ],
                    "getting_here": {"en_route_stops": []},
                    "dinner_recommendations": [],
                },
                "scenic_drives": [],
                "cultural_events": {"has_events": False, "events": []},
            }
        ]
    }

    discoverer.audit_discovered_urls(trip)

    attraction = trip["destinations"][0]["ai_content"]["top_attractions"][0]
    assert attraction.get("url", "").startswith("https://www.alltrails.com/trail/")


def test_audit_keeps_alltrails_when_fetch_fails_even_if_liveness_probe_would_fail():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.verify_url.return_value = (False, "blocked")
    discoverer._url_validator.session.get.side_effect = Exception("timeout")

    trip = {
        "destinations": [
            {
                "name": "St. George, Utah",
                "ai_content": {
                    "top_attractions": [
                        {
                            "name": "Angels Landing",
                            "type": "attraction",
                            "description": "Iconic canyon route.",
                            "practical_note": "This trail has chains and exposure.",
                            "url": "https://www.alltrails.com/trail/us/utah/angels-landing-via-west-rim-trail",
                        }
                    ],
                    "getting_here": {"en_route_stops": []},
                    "dinner_recommendations": [],
                },
                "scenic_drives": [],
                "cultural_events": {"has_events": False, "events": []},
            }
        ]
    }

    discoverer.audit_discovered_urls(trip)

    attraction = trip["destinations"][0]["ai_content"]["top_attractions"][0]
    assert attraction.get("url", "").startswith("https://www.alltrails.com/trail/")


def test_audit_uses_same_trail_context_fields_as_discovery():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Angels Landing trail Zion hiking route details",
    )

    trip = {
        "destinations": [
            {
                "name": "Zion National Park",
                "ai_content": {
                    "top_attractions": [
                        {
                            "name": "Angels Landing",
                            "type": "attraction",
                            "description": "Iconic canyon viewpoint.",
                            "practical_note": "This trail requires a permit.",
                            "url": "https://www.alltrails.com/trail/us/utah/zion-national-park-angels-landing",
                        }
                    ],
                    "getting_here": {"en_route_stops": []},
                    "dinner_recommendations": [],
                },
                "scenic_drives": [],
                "cultural_events": {"has_events": False, "events": []},
            }
        ]
    }

    discoverer.audit_discovered_urls(trip)

    attraction = trip["destinations"][0]["ai_content"]["top_attractions"][0]
    assert attraction.get("url", "").startswith("https://www.alltrails.com/trail/")


def test_audit_keeps_alltrails_when_slug_matches_but_page_text_is_sparse():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="<html><body>AllTrails</body></html>",
    )

    trip = {
        "destinations": [
            {
                "name": "Bryce Canyon National Park",
                "ai_content": {
                    "top_attractions": [
                        {
                            "name": "Navajo Loop Trail",
                            "type": "hike",
                            "description": "Classic hoodoo descent.",
                            "url": "https://www.alltrails.com/trail/us/utah/navajo-loop-trail",
                        }
                    ],
                    "getting_here": {"en_route_stops": []},
                    "dinner_recommendations": [],
                },
                "scenic_drives": [],
                "cultural_events": {"has_events": False, "events": []},
            }
        ]
    }

    discoverer.audit_discovered_urls(trip)

    attraction = trip["destinations"][0]["ai_content"]["top_attractions"][0]
    assert attraction.get("url", "").startswith("https://www.alltrails.com/trail/")


def test_trail_like_attraction_uses_extended_alltrails_sweep_before_fallback():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    seen_calls = []

    def fake_search(variants, site_filter=None, **kwargs):
        seen_calls.append((site_filter, list(variants)))
        if site_filter == "alltrails.com":
            # Simulate only a broad trailing variant finding a match.
            if any(v.strip().lower() == "canyon overlook trail" for v in variants):
                return "https://www.alltrails.com/trail/us/utah/canyon-overlook-trail"
            return None
        return "https://www.nps.gov/zion/planyourvisit/canyon-overlook-trail.htm"

    ai = {
        "top_attractions": [
            {
                "name": "Canyon Overlook Trail",
                "type": "attraction",
                "description": "Short trail with great views.",
            }
        ]
    }

    with patch.object(discoverer, "_search_first", side_effect=fake_search):
        discoverer._discover_attractions(ai, "Zion National Park", "zion")

    attr_url = ai["top_attractions"][0]["url"]
    assert attr_url == "https://www.alltrails.com/trail/us/utah/canyon-overlook-trail"
    assert seen_calls[0][0] == "alltrails.com"
    assert any(v.strip().lower() == "canyon overlook trail" for v in seen_calls[0][1])


def test_search_alltrails_for_trail_includes_explicit_alltrails_variants():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    captured = {}

    def fake_search_first(variants, **kwargs):
        captured["variants"] = list(variants)
        return None

    with patch.object(discoverer, "_search_first", side_effect=fake_search_first):
        discoverer._search_alltrails_for_trail("Angels Landing", "Zion National Park")

    variants = [v.lower() for v in captured["variants"]]
    assert any("alltrails" in v for v in variants)
    assert any(v.strip() == '"angels landing" alltrails' for v in variants)


def test_trail_like_attraction_falls_back_to_maps_not_non_alltrails():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    def fake_search(variants, site_filter=None, **kwargs):
        if site_filter == "alltrails.com":
            return None
        return "https://www.nps.gov/care/planyourvisit/scenicdrive.htm"

    ai = {
        "top_attractions": [
            {
                "name": "Grand Wash Trail",
                "type": "hike",
                "description": "Canyon walk.",
            }
        ]
    }

    with patch.object(discoverer, "_search_first", side_effect=fake_search):
        discoverer._discover_attractions(ai, "Bryce Canyon National Park", "blca")

    url = ai["top_attractions"][0]["url"]
    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")


def test_trail_like_attraction_falls_back_when_alltrails_confidence_below_threshold():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._alltrails_min_confidence_for_publish = "high"

    ai = {
        "top_attractions": [
            {
                "name": "Canyon Overlook Trail",
                "type": "hike",
                "description": "Short trail with views.",
            }
        ]
    }

    with patch.object(
        discoverer,
        "_search_alltrails_for_trail",
        return_value="https://www.alltrails.com/trail/us/utah/canyon-overlook-trail",
    ):
        # Simulate bot-protected page fetch; this should only reach medium confidence.
        with patch.object(discoverer, "_fetch_page_text", return_value=(False, 403, "")):
            discoverer._discover_attractions(ai, "Zion National Park", "zion")

    url = ai["top_attractions"][0]["url"]
    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")


def test_retain_discovered_url_rejects_low_confidence_alltrails_for_trails():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._alltrails_min_confidence_for_publish = "high"

    with patch.object(discoverer, "_fetch_page_text", return_value=(False, 403, "")):
        retained = discoverer._retain_discovered_url(
            "https://www.alltrails.com/trail/us/utah/canyon-overlook-trail",
            "Canyon Overlook Trail",
            "Zion National Park",
            allow_alltrails=True,
        )

    assert retained == ""


def test_fetch_page_text_caches_alltrails_fetches():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Angels Landing trail details",
    )
    discoverer._alltrails_request_delay_seconds = 0
    discoverer._alltrails_block_cooldown_seconds = 0

    url = "https://www.alltrails.com/trail/us/utah/angels-landing-trail"
    first = discoverer._fetch_page_text(url, timeout=8)
    second = discoverer._fetch_page_text(url, timeout=8)

    assert first[0] is True
    assert second[0] is True
    assert discoverer._url_validator.session.get.call_count == 1


def test_filtered_alltrails_strategy_prefers_highest_rated_candidate_with_constraints():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._enable_filtered_alltrails_selection = True
    discoverer._max_alltrails_query_attempts = 5
    discoverer._alltrails_filter_max_miles = 3.0
    discoverer._alltrails_filter_max_gain_feet = 300
    discoverer._alltrails_filter_min_reviews = 5
    discoverer._alltrails_filter_allowed_difficulties = ("easy", "moderate", "moderately challenging")

    discoverer._search.search.return_value = [
        {
            "url": "https://www.alltrails.com/trail/us/utah/canyon-overlook-trail",
            "name": "Canyon Overlook Trail",
            "snippet": "moderate 1.0 mi 213 ft elevation gain 4.8 stars 16200 reviews",
        },
        {
            "url": "https://www.alltrails.com/trail/us/utah/lower-emerald-pool-trail",
            "name": "Lower Emerald Pool Trail",
            "snippet": "moderately challenging 1.3 mi 120 ft elevation gain 4.6 stars 3924 reviews",
        },
    ]

    with patch.object(discoverer, "_prefer_canonical_alltrails_url", side_effect=lambda url, _name: url):
        url = discoverer._search_alltrails_for_trail("Canyon Overlook Trail", "Zion National Park")

    assert url == "https://www.alltrails.com/trail/us/utah/canyon-overlook-trail"


def test_filtered_alltrails_strategy_rejects_candidates_outside_constraints():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._enable_filtered_alltrails_selection = True
    discoverer._max_alltrails_query_attempts = 5
    discoverer._alltrails_filter_max_miles = 3.0
    discoverer._alltrails_filter_max_gain_feet = 300
    discoverer._alltrails_filter_min_reviews = 5
    discoverer._alltrails_filter_allowed_difficulties = ("easy", "moderate", "moderately challenging")

    discoverer._search.search.return_value = [
        {
            "url": "https://www.alltrails.com/trail/us/utah/sand-bench-trail",
            "name": "Sand Bench Trail",
            "snippet": "hard 5.7 mi 1000 ft elevation gain 4.7 stars 1200 reviews",
        },
        {
            "url": "https://www.alltrails.com/trail/us/utah/parus-trail",
            "name": "Pa'rus Trail",
            "snippet": "easy 3.3 mi 80 ft elevation gain 4.6 stars 4000 reviews",
        },
    ]

    with patch.object(discoverer, "_search_first", return_value=None):
        url = discoverer._search_alltrails_for_trail("Canyon Overlook Trail", "Zion National Park")

    assert url is None


def test_filtered_alltrails_does_not_pad_with_weak_matches_when_only_one_candidate_passes():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._enable_filtered_alltrails_selection = True
    discoverer._alltrails_filtered_selection_cache = {}
    discoverer._strict_filtered_alltrails_names = ("canyon overlook trail", "sand bench trail")
    discoverer._uninterested_keywords = ()
    discoverer._seasonal_ski_keywords = ()
    discoverer._ski_in_season_months = ()

    ai = {
        "top_attractions": [
            {
                "name": "Canyon Overlook Trail",
                "type": "hike",
                "description": "Short canyon overlook hike.",
            },
            {
                "name": "Sand Bench Trail",
                "type": "hike",
                "description": "Longer strenuous desert trail.",
            },
        ]
    }

    def fake_filtered(*, item_name, dest_name, query_variants):
        _ = dest_name, query_variants
        if item_name == "Canyon Overlook Trail":
            return "https://www.alltrails.com/trail/us/utah/canyon-overlook-trail"
        return None

    with patch.object(discoverer, "_search_alltrails_for_trail_filtered", side_effect=fake_filtered):
        with patch.object(discoverer, "_resolve_ai_candidate_url", return_value=None):
            with patch.object(discoverer, "_meets_alltrails_publish_confidence", return_value=True):
                discoverer._discover_attractions(ai, "Zion National Park", "zion")

    first_url = ai["top_attractions"][0]["url"]
    second_url = ai["top_attractions"][1]["url"]

    assert first_url == "https://www.alltrails.com/trail/us/utah/canyon-overlook-trail"
    assert second_url.startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "alltrails.com/trail/us/utah/sand-bench-trail" not in second_url


def test_load_interest_filters_applies_rating_threshold_and_boost_controls(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
url_discovery:
  alltrails_rating_min: 4.9
  alltrails_rating_min_votes: 1000
  alltrails_rating_boost: 25
  restaurant_rating_min: 4.8
  restaurant_rating_min_votes: 500
  restaurant_rating_boost: 20
""".strip(),
        encoding="utf-8",
    )

    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._alltrails_rating_min = 4.5
    discoverer._alltrails_rating_min_votes = 200
    discoverer._alltrails_rating_boost = 8
    discoverer._restaurant_rating_min = 4.4
    discoverer._restaurant_rating_min_votes = 100
    discoverer._restaurant_rating_boost = 6

    discoverer._load_interest_filters(str(config_file))

    assert discoverer._alltrails_rating_min == 4.9
    assert discoverer._alltrails_rating_min_votes == 1000
    assert discoverer._alltrails_rating_boost == 25
    assert discoverer._restaurant_rating_min == 4.8
    assert discoverer._restaurant_rating_min_votes == 500
    assert discoverer._restaurant_rating_boost == 20

    item = {
        "url": "https://www.alltrails.com/trail/us/utah/example-trail",
        "name": "Example Trail",
        "snippet": "4.9 stars 1200 reviews scenic trail",
    }
    strict_score = discoverer._score_candidate_result(
        item,
        "Example Trail",
        "Zion National Park",
        specific=True,
        site_filter="alltrails.com",
    )

    discoverer._alltrails_rating_min = 5.0
    discoverer._alltrails_rating_min_votes = 5000
    discoverer._alltrails_rating_boost = 25
    tighter_score = discoverer._score_candidate_result(
        item,
        "Example Trail",
        "Zion National Park",
        specific=True,
        site_filter="alltrails.com",
    )

    assert strict_score > tighter_score


def test_audit_fail_closed_removes_named_entity_url_when_policy_blocks_only_candidate():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_policy_mode = "enforce"
    discoverer._url_policy_blocked_classes = {"google_maps_search"}
    discoverer._url_policy_allowlisted_urls = set()
    discoverer._url_domain_denylist = frozenset()
    discoverer._alltrails_slug_denylist = frozenset()
    discoverer._max_trail_miles = 10.0
    discoverer._allow_blocked_alltrails = True
    discoverer._alltrails_min_confidence_for_publish = "low"

    trip = {
        "destinations": [
            {
                "name": "Capitol Reef National Park",
                "ai_content": {
                    "top_attractions": [
                        {
                            "name": "Capitol Reef Cafe",
                            "type": "attraction",
                            "description": "Popular stop.",
                            "url": "https://www.google.com/maps/search/?api=1&query=Capitol+Reef+Cafe",
                        }
                    ],
                    "getting_here": {"en_route_stops": []},
                    "dinner_recommendations": [],
                },
                "scenic_drives": [],
                "cultural_events": {"has_events": False, "events": []},
            }
        ]
    }

    discoverer.audit_discovered_urls(trip)

    attraction = trip["destinations"][0]["ai_content"]["top_attractions"][0]
    assert attraction.get("url", "") == ""


def test_load_url_policy_allowlist_merges_manual_and_output_urls(tmp_path):
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    allowlist_file = tmp_path / "allowlist.txt"
    allowlist_file.write_text(
        "https://manual.example.com/kept\n# comment\n",
        encoding="utf-8",
    )

    output_file = tmp_path / "index.html"
    output_file.write_text(
        '<a href="https://output.example.com/from-baseline">One</a>'
        '<a href="/local/path">Local</a>',
        encoding="utf-8",
    )

    discoverer._url_policy_allowlist_path = str(allowlist_file)
    discoverer._url_policy_auto_allow_from_output = True
    discoverer._url_policy_output_path = str(output_file)

    discoverer._load_url_policy_allowlist()

    assert "https://manual.example.com/kept" in discoverer._url_policy_allowlisted_urls
    assert "https://output.example.com/from-baseline" in discoverer._url_policy_allowlisted_urls
    assert "/local/path" not in discoverer._url_policy_allowlisted_urls


def test_load_url_policy_allowlist_can_disable_output_auto_seed(tmp_path):
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    allowlist_file = tmp_path / "allowlist.txt"
    allowlist_file.write_text("", encoding="utf-8")

    output_file = tmp_path / "index.html"
    output_file.write_text(
        '<a href="https://output.example.com/from-baseline">One</a>',
        encoding="utf-8",
    )

    discoverer._url_policy_allowlist_path = str(allowlist_file)
    discoverer._url_policy_auto_allow_from_output = False
    discoverer._url_policy_output_path = str(output_file)

    discoverer._load_url_policy_allowlist()

    assert "https://output.example.com/from-baseline" not in discoverer._url_policy_allowlisted_urls


def test_retain_url_rejects_wikipedia_wrong_entity():
    """PR-009: Wikipedia link to wrong entity is rejected via URL-path token check."""
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    result = discoverer._retain_discovered_url(
        "https://en.wikipedia.org/wiki/Bryce_Canyon_National_Park",
        "Mammoth Cave",
        "Bryce Canyon National Park",
        allow_alltrails=False,
    )
    assert result == ""


def test_retain_url_rejects_domain_in_denylist():
    """PR-020/021/025: Known-untrusted domains are rejected before relevance checks."""
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_domain_denylist = frozenset({"visitpagosasprings.com", "pagosabrewing.com"})
    for url in [
        "https://visitpagosasprings.com/lizard-head-pass-area",
        "https://www.visitpagosasprings.com/listing/pagosa-springs-center-for-the-arts/204/",
        "https://www.pagosabrewing.com",
    ]:
        result = discoverer._retain_discovered_url(
            url,
            "Test Item",
            "Pagosa Springs",
            allow_alltrails=False,
        )
        assert result == "", f"Expected denylist rejection for {url}"


def test_retain_url_rejects_google_maps_search_in_enforce_mode():
    """PR-022: Maps-search URLs are blocked as final links in enforce mode."""
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_policy_mode = "enforce"
    discoverer._url_policy_blocked_classes = {"google_maps_search"}
    discoverer._url_policy_allowlisted_urls = set()
    discoverer._url_domain_denylist = frozenset()

    result = discoverer._retain_discovered_url(
        "https://www.google.com/maps/search/?api=1&query=San+Juan+River+Fly+Fishing+Pagosa+Springs",
        "San Juan River Fly Fishing",
        "Pagosa Springs",
        allow_alltrails=False,
        kind="attraction",
    )
    assert result == ""


def test_retain_url_rejects_google_maps_search_for_named_restaurant_in_enforce_mode():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_policy_mode = "enforce"
    discoverer._url_policy_blocked_classes = {"google_maps_search"}
    discoverer._url_policy_allowlisted_urls = set()
    discoverer._url_domain_denylist = frozenset()

    result = discoverer._retain_discovered_url(
        "https://www.google.com/maps/search/?api=1&query=Capitol+Reef+Cafe",
        "Capitol Reef Cafe",
        "Capitol Reef National Park",
        allow_alltrails=False,
        kind="restaurant",
    )
    assert result == ""


def test_retain_url_rejects_google_maps_search_for_named_waypoint_in_enforce_mode():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_policy_mode = "enforce"
    discoverer._url_policy_blocked_classes = {"google_maps_search"}
    discoverer._url_policy_allowlisted_urls = set()
    discoverer._url_domain_denylist = frozenset()

    result = discoverer._retain_discovered_url(
        "https://www.google.com/maps/search/?api=1&query=Wilson+Arch+Moab",
        "Wilson Arch",
        "Moab",
        allow_alltrails=False,
        kind="en-route stop",
    )
    assert result == ""


def test_retain_url_rejects_google_maps_dir_in_enforce_mode():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_policy_mode = "enforce"
    discoverer._url_policy_blocked_classes = {"google_maps_dir"}
    discoverer._url_policy_allowlisted_urls = set()
    discoverer._url_domain_denylist = frozenset()

    result = discoverer._retain_discovered_url(
        "https://www.google.com/maps/dir/Capitol+Reef/Capitol+Reef+Cafe",
        "Capitol Reef Cafe",
        "Capitol Reef National Park",
        allow_alltrails=False,
        kind="restaurant",
    )
    assert result == ""


def test_retain_url_rejects_google_search_in_enforce_mode():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_policy_mode = "enforce"
    discoverer._url_policy_blocked_classes = {"google_search"}
    discoverer._url_policy_allowlisted_urls = set()
    discoverer._url_domain_denylist = frozenset()

    result = discoverer._retain_discovered_url(
        "https://www.google.com/search?q=Capitol+Reef+Cafe",
        "Capitol Reef Cafe",
        "Capitol Reef National Park",
        allow_alltrails=False,
        kind="restaurant",
    )
    assert result == ""


def test_retain_url_rejects_social_media_in_enforce_mode():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_policy_mode = "enforce"
    discoverer._url_policy_blocked_classes = {"social_media"}
    discoverer._url_policy_allowlisted_urls = set()
    discoverer._url_domain_denylist = frozenset()
    discoverer._is_relevant_result = lambda *_args, **_kwargs: True

    result = discoverer._retain_discovered_url(
        "https://www.facebook.com/pagosacenterforthearts",
        "Pagosa Springs Center for the Arts",
        "Pagosa Springs",
        allow_alltrails=False,
        kind="attraction",
    )
    assert result == ""


def test_retain_url_keeps_blocked_class_in_monitor_mode():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_policy_mode = "monitor"
    discoverer._url_policy_blocked_classes = {"social_media"}
    discoverer._url_policy_allowlisted_urls = set()
    discoverer._url_domain_denylist = frozenset()
    discoverer._is_relevant_result = lambda *_args, **_kwargs: True

    url = "https://www.instagram.com/example-trail/"
    result = discoverer._retain_discovered_url(
        url,
        "Example Trail",
        "Telluride",
        allow_alltrails=False,
        kind="attraction",
    )
    assert result == url


def test_retain_url_keeps_wikipedia_matching_entity():
    """Wikipedia link whose slug contains item tokens passes entity check and proceeds to relevance."""
    from unittest.mock import MagicMock
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Bryce Canyon National Park is a national park in Utah.",
        url="https://en.wikipedia.org/wiki/Bryce_Canyon_National_Park",
    )
    result = discoverer._retain_discovered_url(
        "https://en.wikipedia.org/wiki/Bryce_Canyon_National_Park",
        "Bryce Canyon National Park",
        "Bryce Canyon National Park",
        allow_alltrails=False,
    )
    assert result == "https://en.wikipedia.org/wiki/Bryce_Canyon_National_Park"


def test_retain_url_rejects_alltrails_slug_in_denylist():
    """PR-017/019: Slug in denylist is rejected even before any network fetch."""
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._alltrails_slug_denylist = frozenset({"ajax-peak-trail", "jud-wiebe-trail"})
    for slug, item in [
        ("ajax-peak-trail", "Ajax Peak"),
        ("jud-wiebe-trail", "Jud Wiebe Trail"),
    ]:
        result = discoverer._retain_discovered_url(
            f"https://www.alltrails.com/trail/us/colorado/{slug}",
            item,
            "Telluride",
            allow_alltrails=True,
        )
        assert result == "", f"Expected denylist rejection for {slug}"


def test_is_relevant_result_rejects_alltrails_slug_in_denylist():
    """Slug denylist is also applied in the relevance gate during discovery."""
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._alltrails_slug_denylist = frozenset({"ajax-peak-trail"})
    result = discoverer._is_relevant_result(
        "https://www.alltrails.com/trail/us/colorado/ajax-peak-trail",
        "Ajax Peak",
        "Telluride",
    )
    assert result is False


def test_is_relevant_result_rejects_alltrails_redirect_to_different_entity():
    """PR-018: When AllTrails redirects to a different trail slug, it is rejected."""
    from unittest.mock import MagicMock

    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._alltrails_slug_denylist = frozenset()
    discoverer._alltrails_min_confidence_for_publish = "medium"
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Penrose Trail Colorado hiking details and reviews.",
        url="https://www.alltrails.com/trail/us/colorado/penrose-trail",
    )
    discoverer._alltrails_request_delay_seconds = 0
    discoverer._alltrails_block_cooldown_seconds = 0
    discoverer._alltrails_fetch_cache = {}
    discoverer._alltrails_fetch_lock = __import__("threading").Lock()
    discoverer._alltrails_last_request_ts = 0.0
    discoverer._alltrails_blocked_until_ts = 0.0
    discoverer._fetch_final_url_cache = {}
    discoverer._allow_blocked_alltrails = True
    discoverer._max_trail_miles = 10.0

    result = discoverer._is_relevant_result(
        "https://www.alltrails.com/trail/us/colorado/bear-creek-trail",
        "Bear Creek Trail",
        "Telluride",
    )
    assert result is False


def test_is_relevant_result_rejects_alltrails_redirect_mismatch_when_blocked_fetch():
    """PR-018 hardening: redirect mismatch is rejected even when AllTrails fetch is blocked (403)."""
    from unittest.mock import MagicMock

    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._alltrails_slug_denylist = frozenset()
    discoverer._alltrails_min_confidence_for_publish = "medium"
    discoverer._url_validator = MagicMock()

    def _fake_get_text(url, timeout=8):
        discoverer._url_validator._last_final_url = "https://www.alltrails.com/trail/us/colorado/penrose-trail"
        return False, 403, ""

    discoverer._url_validator.get_text.side_effect = _fake_get_text
    discoverer._alltrails_request_delay_seconds = 0
    discoverer._alltrails_block_cooldown_seconds = 0
    discoverer._alltrails_fetch_cache = {}
    discoverer._alltrails_fetch_lock = __import__("threading").Lock()
    discoverer._alltrails_last_request_ts = 0.0
    discoverer._alltrails_blocked_until_ts = 0.0
    discoverer._fetch_final_url_cache = {}
    discoverer._allow_blocked_alltrails = True
    discoverer._max_trail_miles = 10.0

    result = discoverer._is_relevant_result(
        "https://www.alltrails.com/trail/us/colorado/bear-creek-trail",
        "Bear Creek Trail",
        "Telluride",
    )
    assert result is False


# ── Epic 4: Restaurant freshness gate ────────────────────────────────────────

def test_is_restaurant_ineligible_via_name_denylist():
    """PR-025/026/030: Restaurant name in denylist is immediately ineligible."""
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._restaurant_name_denylist = frozenset({"nello's bistro", "la casa sena", "pagosa brewing"})
    assert discoverer._is_restaurant_ineligible({"name": "Nello's Bistro"}, "Pagosa Springs")
    assert discoverer._is_restaurant_ineligible({"name": "La Casa Sena"}, "Santa Fe")
    assert discoverer._is_restaurant_ineligible({"name": "Pagosa Brewing"}, "Pagosa Springs")


def test_is_restaurant_ineligible_via_closure_page_text():
    """Restaurant with closure marker in page text is ineligible."""
    from unittest.mock import MagicMock
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._restaurant_name_denylist = frozenset()
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="<html><body>Nello's Bistro — this business is permanently closed.</body></html>",
        url="https://www.tripadvisor.com/Restaurant_Review-123",
    )
    rest = {"name": "Nello's Bistro", "url": "https://www.tripadvisor.com/Restaurant_Review-123"}
    assert discoverer._is_restaurant_ineligible(rest, "Pagosa Springs")


def test_is_restaurant_ineligible_via_pre_opening_page_text():
    """Restaurant with pre-opening marker in page text is ineligible."""
    from unittest.mock import MagicMock
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._restaurant_name_denylist = frozenset()
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Welcome to La Casa Sena. Opening soon — stay tuned for our grand opening!",
        url="https://www.lacasasena.com",
    )
    rest = {"name": "La Casa Sena", "url": "https://www.lacasasena.com"}
    assert discoverer._is_restaurant_ineligible(rest, "Santa Fe")


def test_is_restaurant_ineligible_skips_fallback_urls():
    """Restaurants with only a fallback maps URL skip page-text check (returns False)."""
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._restaurant_name_denylist = frozenset()
    rest = {
        "name": "Some Restaurant",
        "url": "https://www.google.com/maps/search/?api=1&query=Some+Restaurant+Pagosa",
    }
    assert not discoverer._is_restaurant_ineligible(rest, "Pagosa Springs")


def test_audit_removes_ineligible_restaurant_from_destination():
    """Full audit pass removes restaurant matching denylist from dinner_recommendations."""
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._restaurant_name_denylist = frozenset({"nello's bistro", "pagosa brewing"})
    discoverer._url_policy_mode = "off"
    discoverer._url_policy_blocked_classes = set()
    discoverer._url_policy_allowlisted_urls = set()
    discoverer._alltrails_slug_denylist = frozenset()
    discoverer._max_trail_miles = 10.0
    discoverer._allow_blocked_alltrails = True
    discoverer._alltrails_min_confidence_for_publish = "low"

    trip = {
        "destinations": [
            {
                "name": "Pagosa Springs",
                "ai_content": {
                    "top_attractions": [],
                    "getting_here": {"en_route_stops": []},
                    "dinner_recommendations": [
                        {"name": "Nello's Bistro", "url": ""},
                        {"name": "Pagosa Brewing", "url": ""},
                    ],
                },
                "scenic_drives": [],
                "cultural_events": {"has_events": False, "events": []},
            }
        ]
    }
    discoverer.audit_discovered_urls(trip)
    names = [r["name"] for r in trip["destinations"][0]["ai_content"]["dinner_recommendations"]]
    assert "Nello's Bistro" not in names
    assert "Pagosa Brewing" not in names


# ── Epic 3: Content deduplication ────────────────────────────────────────────

def test_retain_url_rejects_compound_entity_name():
    """PR-027: Compound entity name with ' & ' gets URL rejected (fail-closed)."""
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    result = discoverer._retain_discovered_url(
        "https://www.santafenm.gov",
        "Santa Fe Plaza & Palace of the Governors",
        "Santa Fe",
        allow_alltrails=False,
    )
    assert result == ""


def test_retain_url_keeps_non_compound_entity():
    """Single-entity name without ' & ' is not rejected by compound check."""
    from unittest.mock import MagicMock
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Santa Fe Plaza historic square in Santa Fe New Mexico.",
        url="https://www.santafenm.gov/plaza",
    )
    result = discoverer._retain_discovered_url(
        "https://www.santafenm.gov/plaza",
        "Santa Fe Plaza",
        "Santa Fe",
        allow_alltrails=False,
    )
    assert result == "https://www.santafenm.gov/plaza"


def test_deduplicate_within_destination_removes_drive_matching_attraction():
    """PR-013/023: Scenic drive whose title overlaps an attraction is removed."""
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    dest = {
        "name": "Moab, UT",
        "ai_content": {
            "top_attractions": [
                {"name": "Dead Horse Point State Park", "type": "attraction"},
            ]
        },
        "scenic_drives": [
            {"title": "Dead Horse Point State Park", "category": "viewpoint"},
            {"title": "Colorado River Scenic Byway", "category": "drive"},
        ],
    }
    discoverer._deduplicate_within_destination(dest)
    titles = [d["title"] for d in dest["scenic_drives"]]
    assert "Dead Horse Point State Park" not in titles
    assert "Colorado River Scenic Byway" in titles


def test_deduplicate_within_destination_removes_partial_drive_overlap():
    """PR-023: 'Wolf Creek Pass Scenic Drive' removed when 'Wolf Creek Pass' is an attraction."""
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    dest = {
        "name": "Pagosa Springs",
        "ai_content": {
            "top_attractions": [
                {"name": "Wolf Creek Pass", "type": "viewpoint"},
            ]
        },
        "scenic_drives": [
            {"title": "Wolf Creek Pass Scenic Drive", "category": "drive"},
            {"title": "Treasure Falls", "category": "viewpoint"},
        ],
    }
    discoverer._deduplicate_within_destination(dest)
    titles = [d["title"] for d in dest["scenic_drives"]]
    assert "Wolf Creek Pass Scenic Drive" not in titles
    assert "Treasure Falls" in titles


def test_deduplicate_within_destination_keeps_unrelated_drives():
    """Unrelated scenic drives are not affected by within-destination dedup."""
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    dest = {
        "name": "Zion National Park",
        "ai_content": {
            "top_attractions": [
                {"name": "Angels Landing", "type": "hike"},
            ]
        },
        "scenic_drives": [
            {"title": "Zion Canyon Scenic Drive", "category": "drive"},
        ],
    }
    discoverer._deduplicate_within_destination(dest)
    assert len(dest["scenic_drives"]) == 1


def test_deduplicate_cross_destination_drives_removes_overlap_with_other_destination_attraction():
    """PR-008: scenic drive is removed when it duplicates another destination's attraction concept."""
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    trip = {
        "destinations": [
            {
                "name": "St. George",
                "ai_content": {"top_attractions": []},
                "scenic_drives": [{"title": "Kolob Canyons Road", "category": "drive"}],
            },
            {
                "name": "Zion National Park",
                "ai_content": {"top_attractions": [{"name": "Kolob Canyons", "type": "attraction"}]},
                "scenic_drives": [{"title": "Zion Canyon Scenic Drive", "category": "drive"}],
            },
        ]
    }

    discoverer._deduplicate_cross_destination_drives(trip)

    st_george_titles = [d["title"] for d in trip["destinations"][0]["scenic_drives"]]
    zion_titles = [d["title"] for d in trip["destinations"][1]["scenic_drives"]]
    assert "Kolob Canyons Road" not in st_george_titles
    assert "Zion Canyon Scenic Drive" in zion_titles


def test_deduplicate_cross_destination_drives_keeps_unrelated_concepts():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    trip = {
        "destinations": [
            {
                "name": "A",
                "ai_content": {"top_attractions": [{"name": "Angels Landing", "type": "hike"}]},
                "scenic_drives": [{"title": "Kolob Terrace Road", "category": "drive"}],
            },
            {
                "name": "B",
                "ai_content": {"top_attractions": [{"name": "Bryce Amphitheater", "type": "viewpoint"}]},
                "scenic_drives": [{"title": "Scenic Byway 12", "category": "drive"}],
            },
        ]
    }

    discoverer._deduplicate_cross_destination_drives(trip)

    assert len(trip["destinations"][0]["scenic_drives"]) == 1
    assert len(trip["destinations"][1]["scenic_drives"]) == 1


def test_trail_ai_candidate_rejected_when_filtered_constraints_fail_then_falls_back_maps():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._enable_filtered_alltrails_selection = True
    discoverer._alltrails_filtered_selection_cache = {}

    ai = {
        "top_attractions": [
            {
                "name": "The Narrows",
                "type": "hike",
                "description": "River hike through canyon walls.",
                "url_candidates": [
                    "https://www.alltrails.com/trail/us/utah/the-narrows",
                ],
            }
        ]
    }

    with patch.object(discoverer, "_search_alltrails_for_trail_filtered", return_value=None):
        discoverer._discover_attractions(ai, "Zion National Park", "zion")

    url = ai["top_attractions"][0]["url"]
    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")


def test_angels_landing_seed_fails_filtered_constraints_and_falls_back_maps():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._enable_filtered_alltrails_selection = True
    discoverer._alltrails_filtered_selection_cache = {}

    ai = {
        "top_attractions": [
            {
                "name": "Angels Landing",
                "type": "hike",
                "description": "Iconic but strenuous route with significant elevation gain.",
                "url_candidates": [
                    "https://www.alltrails.com/trail/us/utah/angels-landing",
                ],
            }
        ]
    }

    with patch.object(discoverer, "_search_alltrails_for_trail_filtered", return_value=None):
        discoverer._discover_attractions(ai, "Zion National Park", "zion")

    url = ai["top_attractions"][0]["url"]
    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")


def test_non_strict_trail_ai_candidate_can_pass_when_filtered_metadata_missing():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._enable_filtered_alltrails_selection = True
    discoverer._strict_filtered_alltrails_names = ("angels landing", "the narrows")
    discoverer._alltrails_filtered_selection_cache = {}

    ai = {
        "top_attractions": [
            {
                "name": "Canyon Overlook Trail",
                "type": "hike",
                "description": "Short canyon overlook hike.",
                "url_candidates": [
                    "https://www.alltrails.com/trail/us/utah/canyon-overlook-trail",
                ],
            }
        ]
    }

    with patch.object(discoverer, "_search_alltrails_for_trail_filtered", return_value=None):
        with patch.object(discoverer, "_retain_discovered_url", side_effect=lambda url, *_args, **_kwargs: url):
            discoverer._discover_attractions(ai, "Zion National Park", "zion")

    assert ai["top_attractions"][0]["url"] == "https://www.alltrails.com/trail/us/utah/canyon-overlook-trail"


def test_en_route_discovery_disallows_alltrails_results_upfront():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    captured = {}

    def fake_search(variants, **kwargs):
        captured.update(kwargs)
        return None

    ai = {
        "getting_here": {
            "en_route_stops": [{"name": "Wilson Arch"}],
        }
    }

    with patch.object(discoverer, "_search_first", side_effect=fake_search):
        discoverer._discover_en_route_stops(ai, "Moab")

    assert captured.get("allow_alltrails") is False


def test_scenic_drive_discovery_disallows_alltrails_results_upfront():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    captured = {}

    def fake_search(variants, **kwargs):
        captured.update(kwargs)
        return None

    dest = {
        "scenic_drives": [{"title": "Kolob Terrace Road"}],
    }

    with patch.object(discoverer, "_search_first", side_effect=fake_search):
        discoverer._discover_scenic_drives(dest, "St. George, Utah")

    assert captured.get("allow_alltrails") is False


def test_attraction_fallback_maps_avoids_contradictory_destination_append():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)

    with patch.object(discoverer, "_search_first", return_value=None):
        ai = {
            "top_attractions": [
                {
                    "name": "Historic Downtown St. George",
                    "type": "attraction",
                    "description": "Historic district walk.",
                }
            ]
        }
        discoverer._discover_attractions(ai, "Zion National Park", "zion")

    url = ai["top_attractions"][0]["url"]
    assert "Historic%20Downtown%20St.%20George" in url
    assert "Zion%20National%20Park" not in url


def test_discover_attractions_skips_blacklisted_interest_keywords():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._uninterested_keywords = ("golf course",)
    discoverer._seasonal_ski_keywords = (" ski",)
    discoverer._ski_in_season_months = (11, 12, 1, 2, 3, 4)

    ai = {
        "top_attractions": [
            {
                "name": "Telluride Golf Course",
                "type": "attraction",
                "description": "Championship greens and club house.",
            }
        ]
    }

    with patch.object(discoverer, "_search_first", return_value="https://example.com/should-not-be-used"):
        discoverer._discover_attractions(ai, "Telluride, Colorado", None, "July 10-12, 2026")

    assert ai["top_attractions"][0]["url"] == ""


def test_discover_attractions_skips_ski_out_of_season():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._uninterested_keywords = ()
    discoverer._seasonal_ski_keywords = ("ski resort", "snowboarding")
    discoverer._ski_in_season_months = (11, 12, 1, 2, 3, 4)

    ai = {
        "top_attractions": [
            {
                "name": "Telluride Ski Resort",
                "type": "attraction",
                "description": "Alpine ski terrain.",
            }
        ]
    }

    with patch.object(discoverer, "_search_first", return_value="https://example.com/should-not-be-used"):
        discoverer._discover_attractions(ai, "Telluride, Colorado", None, "July 10-12, 2026")

    assert ai["top_attractions"][0]["url"] == ""


def test_discover_attractions_allows_ski_in_season():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._uninterested_keywords = ()
    discoverer._seasonal_ski_keywords = ("ski resort",)
    discoverer._ski_in_season_months = (11, 12, 1, 2, 3, 4)

    ai = {
        "top_attractions": [
            {
                "name": "Telluride Ski Resort",
                "type": "attraction",
                "description": "Alpine ski terrain.",
            }
        ]
    }

    with patch.object(discoverer, "_search_first", return_value="https://www.tellurideskiresort.com"):
        discoverer._discover_attractions(ai, "Telluride, Colorado", None, "December 10-12, 2026")

    assert ai["top_attractions"][0]["url"] == "https://www.tellurideskiresort.com"


def test_discover_scenic_drives_leaves_url_empty_when_no_match():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    dest = {
        "scenic_drives": [
            {
                "title": "Scenic Byway 12",
                "category": "drive",
            }
        ]
    }

    with patch.object(discoverer, "_search_first", return_value=None):
        discoverer._discover_scenic_drives(dest, "Bryce Canyon National Park")

    assert dest["scenic_drives"][0]["url"] == ""


def test_audit_retains_verified_scenic_drive_url():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Scenic Byway 12 details near Bryce Canyon route viewpoints",
    )

    trip = {
        "destinations": [
            {
                "name": "Bryce Canyon National Park",
                "ai_content": {
                    "top_attractions": [],
                    "getting_here": {"en_route_stops": []},
                    "dinner_recommendations": [],
                },
                "scenic_drives": [
                    {
                        "title": "Scenic Byway 12",
                        "url": "https://www.visitutah.com/places-to-go/scenic-drives/scenic-byway-12",
                    }
                ],
                "cultural_events": {"has_events": False, "events": []},
            }
        ]
    }

    discoverer.audit_discovered_urls(trip)

    assert trip["destinations"][0]["scenic_drives"][0]["url"].startswith("https://www.visitutah.com/")


def test_audit_rejects_scenic_drive_place_page_url_without_route_intent():
    """PR-004: scenic-drive URLs should be route-specific, not generic place pages."""
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Snow Canyon State Park official visitor information.",
    )

    trip = {
        "destinations": [
            {
                "name": "St. George, Utah",
                "ai_content": {
                    "top_attractions": [],
                    "getting_here": {"en_route_stops": []},
                    "dinner_recommendations": [],
                },
                "scenic_drives": [
                    {
                        "title": "Snow Canyon Scenic Drive",
                        "url": "https://stateparks.utah.gov/parks/snow-canyon/",
                    }
                ],
                "cultural_events": {"has_events": False, "events": []},
            }
        ]
    }

    discoverer.audit_discovered_urls(trip)

    assert trip["destinations"][0]["scenic_drives"][0].get("url", "") == ""


def test_audit_strips_non_alltrails_url_for_trail_like_attraction():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.verify_url.return_value = (True, 200)
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Generic scenic drive page",
    )

    trip = {
        "destinations": [
            {
                "name": "Bryce Canyon National Park",
                "ai_content": {
                    "top_attractions": [
                        {
                            "name": "Grand Wash Trail",
                            "type": "hike",
                            "description": "Canyon walk.",
                            "url": "https://www.nps.gov/care/planyourvisit/scenicdrive.htm",
                        }
                    ],
                    "getting_here": {"en_route_stops": []},
                    "dinner_recommendations": [],
                },
                "scenic_drives": [],
                "cultural_events": {"has_events": False, "events": []},
            }
        ]
    }

    discoverer.audit_discovered_urls(trip)

    attraction = trip["destinations"][0]["ai_content"]["top_attractions"][0]
    assert "url" not in attraction


def test_semantic_scoring_prefers_cultural_domain_over_preserve_domain():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._search = MagicMock()
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.verify_url.return_value = (True, 200)
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Canyon Road district arts galleries and culture in Santa Fe",
    )
    discoverer._search.search.return_value = [
        {
            "url": "https://example-canyon-preserve.org/visit",
            "name": "Canyon Preserve",
            "snippet": "Nature preserve and wildlife habitat",
        },
        {
            "url": "https://santafe.org/visit/arts/canyon-road",
            "name": "Canyon Road Arts District | Visit Santa Fe",
            "snippet": "Explore galleries and culture",
        },
    ]

    result = discoverer._search_first_strict(
        query_variants=['"Canyon Road" Santa Fe attraction'],
        site_filter=None,
        site_hint=None,
        item_name="Canyon Road",
        dest_name="Santa Fe",
        allow_alltrails=False,
    )

    assert result == "https://santafe.org/visit/arts/canyon-road"


def test_candidate_scoring_applies_path_and_domain_hints():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    item = {
        "url": "https://visit-santafe.example/visit/culture/gallery/canyon-road",
        "name": "Visit Santa Fe - Canyon Road Arts",
        "snippet": "Culture and galleries district",
    }
    score = discoverer._score_candidate_result(
        item,
        item_name="Canyon Road",
        dest_name="Santa Fe",
        specific=True,
    )
    bad_item = {
        "url": "https://canyon-preserve.example/wildlife",
        "name": "Canyon Preserve",
        "snippet": "nature preserve and conservation",
    }
    bad_score = discoverer._score_candidate_result(
        bad_item,
        item_name="Canyon Road",
        dest_name="Santa Fe",
        specific=True,
    )
    assert score > bad_score


def test_candidate_scoring_prefers_destination_country_tld_for_international_destinations():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    local_item = {
        "url": "https://visitdubrovnik.hr/visit/old-town",
        "name": "Visit Dubrovnik",
        "snippet": "Explore the old town district",
    }
    foreign_item = {
        "url": "https://visitdubrovnik.com/visit/old-town",
        "name": "Visit Dubrovnik",
        "snippet": "Explore the old town district",
    }

    local_score = discoverer._score_candidate_result(
        local_item,
        item_name="Old Town Dubrovnik",
        dest_name="Dubrovnik, Croatia",
        specific=True,
    )
    foreign_score = discoverer._score_candidate_result(
        foreign_item,
        item_name="Old Town Dubrovnik",
        dest_name="Dubrovnik, Croatia",
        specific=True,
    )

    assert local_score > foreign_score


def test_restaurant_rating_priority_requires_sufficient_votes():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._restaurant_rating_min = 4.4
    discoverer._restaurant_rating_min_votes = 100
    discoverer._restaurant_rating_boost = 10

    low_votes_item = {
        "url": "https://www.google.com/maps/place/Test+Cafe/",
        "name": "Test Cafe",
        "snippet": "Rated 4.9 stars with 18 reviews",
    }
    enough_votes_item = {
        "url": "https://www.google.com/maps/place/Test+Cafe/",
        "name": "Test Cafe",
        "snippet": "Rated 4.6 stars with 320 reviews",
    }

    low_votes_score = discoverer._score_candidate_result(
        low_votes_item,
        item_name="Test Cafe",
        dest_name="Moab",
        specific=True,
        site_filter="google.com/maps",
    )
    enough_votes_score = discoverer._score_candidate_result(
        enough_votes_item,
        item_name="Test Cafe",
        dest_name="Moab",
        specific=True,
        site_filter="google.com/maps",
    )

    assert enough_votes_score > low_votes_score


def test_alltrails_rating_priority_requires_sufficient_votes():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._alltrails_rating_min = 4.5
    discoverer._alltrails_rating_min_votes = 200
    discoverer._alltrails_rating_boost = 12

    low_votes_item = {
        "url": "https://www.alltrails.com/trail/us/utah/canyon-overlook-trail",
        "name": "Canyon Overlook Trail",
        "snippet": "4.9 stars 26 reviews",
    }
    enough_votes_item = {
        "url": "https://www.alltrails.com/trail/us/utah/canyon-overlook-trail",
        "name": "Canyon Overlook Trail",
        "snippet": "4.7 stars 1,420 reviews",
    }

    low_votes_score = discoverer._score_candidate_result(
        low_votes_item,
        item_name="Canyon Overlook Trail",
        dest_name="Zion National Park",
        specific=True,
        site_filter="alltrails.com",
    )
    enough_votes_score = discoverer._score_candidate_result(
        enough_votes_item,
        item_name="Canyon Overlook Trail",
        dest_name="Zion National Park",
        specific=True,
        site_filter="alltrails.com",
    )

    assert enough_votes_score > low_votes_score


def test_audit_discovered_urls_strips_weak_hallucinated_links():
    discoverer = URLDiscoverer.__new__(URLDiscoverer)
    discoverer._url_validator = MagicMock()
    discoverer._url_validator.verify_url.return_value = (True, 200)
    discoverer._url_validator.session.get.return_value = MagicMock(
        status_code=200,
        text="Dixie trail in St. George, Utah.",
    )

    trip = {
        "destinations": [
            {
                "name": "St. George, Utah",
                "ai_content": {
                    "top_attractions": [
                        {
                            "name": "Dixie State University Trail",
                            "type": "hike",
                            "url": "https://www.dixie.edu/trails/dixie-trail",
                        }
                    ],
                    "getting_here": {"en_route_stops": []},
                    "dinner_recommendations": [],
                },
                "scenic_drives": [
                    {
                        "title": "Kolob Canyons Drive",
                        "url": "https://example.com/should-be-removed",
                    }
                ],
                "cultural_events": {
                    "has_events": True,
                    "events": [
                        {
                            "name": "Bad Event",
                            "url": "https://example.com/bad-event",
                        }
                    ],
                },
            }
        ]
    }

    discoverer.audit_discovered_urls(trip)

    attraction = trip["destinations"][0]["ai_content"]["top_attractions"][0]
    assert "url" not in attraction
    assert "url" not in trip["destinations"][0]["scenic_drives"][0]
    assert "url" not in trip["destinations"][0]["cultural_events"]["events"][0]
