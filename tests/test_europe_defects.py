"""Defects found by the first non-US itinerary (Brussels, 2026-08-27).

See docs/design/destination-type-coverage.md.
"""
import pytest

from generator.cultural_events import CulturalEventsDiscoverer
from generator.html_assembler import HTMLAssembler


class TestMonthSpan:
    """A stay crossing a month boundary must search both months."""

    @pytest.mark.parametrize(
        "dates, expected",
        [
            ("August 31 - September 1, 2026", "August September"),
            ("September 2-4, 2026", "September"),
            ("October 10, 2026", "October"),
            ("December 30, 2026 - January 2, 2027", "January December"),
            ("", "October"),
        ],
    )
    def test_all_touched_months_are_searched(self, dates, expected):
        got = CulturalEventsDiscoverer._months_in_range(dates)
        assert sorted(got.split()) == sorted(expected.split())

    def test_the_brussels_case_no_longer_searches_only_august(self):
        """Aug 31 - Sep 1 searched August, then dropped every result for
        falling before arrival, and reported zero events."""
        assert "September" in CulturalEventsDiscoverer._months_in_range(
            "August 31 - September 1, 2026"
        )


class TestDestinationClassification:
    """Unrecognised destinations must not be assumed quiet."""

    @pytest.mark.parametrize(
        "name",
        ["Brussels, Belgium", "Amsterdam, Netherlands", "Berlin, Germany", "Prague, Czech Republic"],
    )
    def test_non_us_cities_are_not_called_small_towns(self, name):
        """The prompt treats small_town as near-evidence of no events, so a
        four-city US allowlist made every other destination self-fulfilling."""
        inst = CulturalEventsDiscoverer.__new__(CulturalEventsDiscoverer)
        assert inst._classify_destination(name) == "unknown"

    @pytest.mark.parametrize(
        "name, expected",
        [
            ("Zion National Park", "national_park"),
            ("Telluride", "resort_town"),
            ("Denver", "city"),
        ],
    )
    def test_known_classifications_still_hold(self, name, expected):
        inst = CulturalEventsDiscoverer.__new__(CulturalEventsDiscoverer)
        assert inst._classify_destination(name) == expected


class TestRestaurantTeaser:
    """Price and cuisine already appear as badges; the teaser repeated them."""

    @pytest.mark.parametrize(
        "description, cuisine, expected_start",
        [
            ("$$-$$$, Belgian Seafood. . Fresh lobster and seafood platters.", "Seafood", "Fresh lobster"),
            ("$$-$$$, Belgian. . Traditional stoemp and carbonnades.", "Belgian", "Traditional stoemp"),
            ("$$-$$$, French Belgian. . Refined French-Belgian cuisine.", "French", "Refined French-Belgian"),
        ],
    )
    def test_ranged_price_and_cuisine_echo_are_stripped(self, description, cuisine, expected_start):
        out = HTMLAssembler._restaurant_description(
            {"description": description, "name": "X", "cuisine": cuisine}, "Brussels", True, False
        )
        assert out.startswith(expected_start)
        assert "$" not in out
        assert ". ." not in out

    def test_a_real_sentence_naming_the_cuisine_survives(self):
        """Bounded to a short clause so genuine prose is never eaten."""
        out = HTMLAssembler._restaurant_description(
            {
                "description": "Belgian classics have anchored this family kitchen for eighty years.",
                "name": "Y",
                "cuisine": "Belgian",
            },
            "Brussels",
            True,
            False,
        )
        assert out.startswith("Belgian classics")
