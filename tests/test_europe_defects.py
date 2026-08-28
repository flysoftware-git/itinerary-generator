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


class TestBudgetKeywordMatching:
    """"low-cost" matched none of the original budget keywords.

    The manifest said dining: "low-cost", _normalize_restaurants tested for
    "budget"/"cheap"/"economy"/"value"/"frugal", so the budget-aware filter
    never ran and the brief looked ignored end to end (2026-08-27, Brussels).

    The contract is a CAP, not a re-sort: output is always cheapest-first, and
    the budget flags limit how many off-tier options survive. Tests assert the
    cap, since asserting order would pass whether or not the flag fired.
    """

    @staticmethod
    def _names(budget, items):
        from generator.ai_content import AIContentGenerator

        gen = AIContentGenerator.__new__(AIContentGenerator)
        return [r["name"] for r in gen._normalize_restaurants(items, budget)]

    TWO_SPLURGES = [
        {"name": "Cheap", "price_range": "$"},
        {"name": "Mid", "price_range": "$$"},
        {"name": "Splurge A", "price_range": "$$$"},
        {"name": "Splurge B", "price_range": "$$$$"},
    ]
    TWO_CASUALS = [
        {"name": "Casual A", "price_range": "$"},
        {"name": "Casual B", "price_range": "$$"},
        {"name": "Fancy", "price_range": "$$$$"},
    ]

    @pytest.mark.parametrize(
        "budget",
        ["low-cost", "low cost", "inexpensive", "affordable", "budget", "cheap", "shoestring"],
    )
    def test_low_budget_phrasings_cap_splurges_at_one(self, budget):
        names = self._names(budget, self.TWO_SPLURGES)
        assert sum(n.startswith("Splurge") for n in names) == 1
        assert "Cheap" in names and "Mid" in names

    @pytest.mark.parametrize("budget", ["luxury", "upscale", "splurge", "fine dining"])
    def test_high_budget_phrasings_cap_casuals_at_one(self, budget):
        names = self._names(budget, self.TWO_CASUALS)
        assert sum(n.startswith("Casual") for n in names) == 1
        assert "Fancy" in names

    def test_no_fine_dining_reads_as_low_not_high(self):
        """The substring "fine dining" made a low-budget instruction read high."""
        names = self._names("Cafes and markets. No fine dining.", self.TWO_SPLURGES)
        assert sum(n.startswith("Splurge") for n in names) == 1

    def test_absent_budget_keeps_everything(self):
        assert len(self._names("", self.TWO_SPLURGES)) == 4

    def test_the_original_keywords_still_work(self):
        assert sum(n.startswith("Splurge") for n in self._names("budget", self.TWO_SPLURGES)) == 1


class TestRestaurantUrlCollision:
    """One URL, one restaurant.

    Re-opening the per-item fallback (2026-08-27) published
    restaurantguru.com/9-Et-Voisins-Brussels under BOTH "9 et Voisins" and
    "Brasserie Signature". A search for a name the batch could not place will
    return a nearby restaurant's page, and nothing compared one item's URL
    against another's -- the risk the authoritative-batch design had been
    guarding against implicitly.
    """

    def test_variants_of_the_same_page_collide(self):
        from generator.url_discovery import URLDiscoverer

        k = URLDiscoverer._collision_key
        base = "https://restaurantguru.com/9-Et-Voisins-Brussels"
        for variant in [
            "http://restaurantguru.com/9-Et-Voisins-Brussels",
            "https://www.restaurantguru.com/9-Et-Voisins-Brussels/",
            "https://restaurantguru.com/9-Et-Voisins-Brussels?utm_source=x",
            "https://restaurantguru.com/9-Et-Voisins-Brussels#menu",
        ]:
            assert k(base) == k(variant), variant

    def test_distinct_pages_do_not_collide(self):
        from generator.url_discovery import URLDiscoverer

        k = URLDiscoverer._collision_key
        assert k("https://a.example/one") != k("https://a.example/two")

    def test_claim_check_is_empty_safe(self):
        from generator.url_discovery import URLDiscoverer

        assert URLDiscoverer._url_already_claimed("", {""}) is False
        assert URLDiscoverer._url_already_claimed("https://a.example/x", set()) is False
        assert URLDiscoverer._url_already_claimed("https://a.example/x", {"a.example/x"}) is True


class TestCollisionGuardDoesNotEatOwnUrl:
    """The guard must not reject an item for matching its own link.

    The first version pre-seeded the claimed set from URLs already attached,
    so every restaurant collided with itself the moment it was examined --
    rejecting exactly the items it was meant to leave alone. Claims are now
    recorded as items are processed.
    """

    def test_single_restaurant_keeps_its_pre_attached_url(self):
        from generator.url_discovery import URLDiscoverer

        d = URLDiscoverer.__new__(URLDiscoverer)
        claimed: set[str] = set()
        url = "https://restaurantguru.com/9-Et-Voisins-Brussels"
        assert URLDiscoverer._url_already_claimed(url, claimed) is False
        claimed.add(URLDiscoverer._collision_key(url))
        # A DIFFERENT item asking for the same page is refused.
        assert URLDiscoverer._url_already_claimed(url, claimed) is True

    def test_first_claimant_wins(self):
        from generator.url_discovery import URLDiscoverer

        claimed: set[str] = set()
        a = "https://example.test/shared"
        b = "https://www.example.test/shared/"
        claimed.add(URLDiscoverer._collision_key(a))
        assert URLDiscoverer._url_already_claimed(b, claimed) is True


class TestOfficialSiteDomainMatching:
    """A host list cannot tell a restaurant's own site from a blog about it.

    The 2026-08-27 upgrade accepted champagne-tastes.com as Rotisse's official
    site and tipsfromawaitress.be as Yummy Bowl's, because both cleared a
    not-on-the-list test. rotisse.be and eatyummybowl.com both exist. The
    discriminator is whether the DOMAIN corresponds to the name.
    """

    @pytest.mark.parametrize(
        "url, name",
        [
            ("https://rotisse.be/en", "Rotisse"),
            ("https://thaiburi.eu/", "Thaiburi"),
            ("https://chiconfarsi.com/en", "Chicon Farsi"),
            ("https://eatyummybowl.com/", "Yummy Bowl"),
            ("https://www.pastadivina.be/", "Pasta Divina"),
        ],
    )
    def test_official_domains_match(self, url, name):
        from generator.url_discovery import URLDiscoverer

        assert URLDiscoverer._domain_matches_item_name(url, name) is True

    @pytest.mark.parametrize(
        "url, name",
        [
            ("https://champagne-tastes.com/rotisse/", "Rotisse"),
            ("https://mindtrip.ai/restaurant/brussels-belgium/thaiburi/x", "Thaiburi"),
            ("https://halaharchi.com/en/businesses/chicon-farsi-brussels", "Chicon Farsi"),
            ("https://www.tipsfromawaitress.be/blog/yummy-bowl", "Yummy Bowl"),
            ("https://restaurantguru.com/9-Et-Voisins-Brussels", "Brasserie Signature"),
        ],
    )
    def test_pages_about_the_restaurant_do_not_match(self, url, name):
        """These all name the restaurant in the PATH, which is exactly why
        matching on the path rather than the domain would accept them."""
        from generator.url_discovery import URLDiscoverer

        assert URLDiscoverer._domain_matches_item_name(url, name) is False

    def test_short_names_are_refused_rather_than_guessed(self):
        """A three-character name matches almost any domain by accident."""
        from generator.url_discovery import URLDiscoverer

        assert URLDiscoverer._domain_matches_item_name("https://barcelona-guide.com", "Bar") is False

    def test_www_and_scheme_are_ignored(self):
        from generator.url_discovery import URLDiscoverer

        for u in ["http://www.rotisse.be", "https://rotisse.be/", "https://www.rotisse.be/en/menu"]:
            assert URLDiscoverer._domain_matches_item_name(u, "Rotisse") is True


class TestRailItinerariesHaveNoEnRouteStops:
    """En-route stops are a road-trip concept.

    On a booked train, ferry or flight the traveller cannot pull off and look
    at something. The 2026-08-27 five-city Europe run removed 14 of 41 en-route
    stops for lacking a verified URL -- the worst category in the run -- while
    every leg was rail.
    """

    @pytest.mark.parametrize("mode", ["train", "plane", "ship", "ferry", "bus", "shuttle"])
    def test_non_driving_arrivals_suppress_stops(self, mode):
        from generator.ai_content import AIContentGenerator

        assert AIContentGenerator._arrival_is_not_self_driven({"transportation": [{"type": mode}]}) is True

    def test_a_car_leg_keeps_them(self):
        from generator.ai_content import AIContentGenerator

        assert AIContentGenerator._arrival_is_not_self_driven({"transportation": [{"type": "car"}]}) is False

    @pytest.mark.parametrize("dest", [{}, None, {"transportation": []}])
    def test_silence_means_unknown_and_keeps_existing_behaviour(self, dest):
        """A manifest stating no transport is most likely a road trip, which is
        what this generator was built for."""
        from generator.ai_content import AIContentGenerator

        assert AIContentGenerator._arrival_is_not_self_driven(dest) is False

    def test_stops_are_cleared_for_a_rail_arrival(self):
        from generator.ai_content import AIContentGenerator

        gen = AIContentGenerator.__new__(AIContentGenerator)
        out = gen._normalize_getting_here(
            {"en_route_stops": [{"name": "Roadside Diner"}], "summary": "Take the ICE"},
            "Berlin, Germany",
            dest={"transportation": [{"type": "train"}]},
        )
        assert out["en_route_stops"] == []
        assert out["summary"] == "Take the ICE"


class TestReorderedAttractionNames:
    """Same place, words rearranged, two cards.

    albrechtsburg-meissen.de was published twice at one destination, as
    "Albrechtsburg Castle (Meissen)" and "Meissen Albrechtsburg Castle".
    """

    @staticmethod
    def _run(names):
        from generator.ai_content import AIContentGenerator

        gen = AIContentGenerator.__new__(AIContentGenerator)
        trip = {"destinations": [{"name": "X", "ai_content": {"top_attractions": [{"name": n} for n in names]}}]}
        gen._deduplicate_reordered_attraction_names(trip)
        return [a["name"] for a in trip["destinations"][0]["ai_content"]["top_attractions"]]

    def test_rearranged_duplicate_is_dropped(self):
        kept = self._run(["Albrechtsburg Castle (Meissen)", "Meissen Albrechtsburg Castle"])
        assert kept == ["Albrechtsburg Castle (Meissen)"]

    def test_different_places_sharing_words_are_kept(self):
        """Set EQUALITY, not overlap -- overlap is what matched Zion Lodge to
        Stargazing in Zion."""
        assert len(self._run(["Museum Island", "Island Museum Cafe"])) == 2

    def test_unrelated_attractions_untouched(self):
        names = ["Charles Bridge", "Prague Castle", "Old Town Square"]
        assert self._run(names) == names


class TestEnRouteSuppressionCoversBothSources:
    """Two independent sources produce en-route stops, not one.

    ai_content clears them for a rail arrival; url_discovery HARVESTS its own
    afterwards. Clearing only the first left Brussels with a 25km detour to
    Mechelen on an all-rail itinerary (2026-08-27 five-city run).
    """

    def test_both_modules_agree_on_the_modes(self):
        """Duplicated because importing across the two would be circular. A
        test is the cheapest thing that stops them drifting."""
        from generator.ai_content import AIContentGenerator
        from generator.url_discovery import URLDiscoverer

        assert URLDiscoverer._NON_DRIVING_ARRIVAL_MODES == AIContentGenerator._NON_DRIVING_ARRIVAL_MODES

    @pytest.mark.parametrize("mode", ["train", "plane", "ship", "ferry", "bus", "shuttle"])
    def test_discovery_side_suppresses_non_driving_arrivals(self, mode):
        from generator.url_discovery import URLDiscoverer

        assert URLDiscoverer._arrival_is_not_self_driven({"transportation": [{"type": mode}]}) is True

    @pytest.mark.parametrize("dest", [{"transportation": [{"type": "car"}]}, {}, None])
    def test_discovery_side_leaves_road_trips_alone(self, dest):
        from generator.url_discovery import URLDiscoverer

        assert URLDiscoverer._arrival_is_not_self_driven(dest) is False


class TestGettingHereMatchesTheBookedMode:
    """The Getting Here section described a drive on an all-rail itinerary.

    The 2026-08-27 Europe output read "Take the E19 from Antwerp", 95 mi,
    2 hrs 15 min -- for a booked 8-mile airport train. The prompt asked for
    highways and parking unconditionally, and the Maps link was hardcoded to
    travelmode=driving, so the link contradicted the section around it.
    """

    def test_all_rail_trip_links_use_transit(self):
        from generator.html_assembler import HTMLAssembler

        trip = {"destinations": [{"transportation": [{"type": "train"}]},
                                 {"transportation": [{"type": "train"}]}]}
        assert HTMLAssembler._maps_travelmode_for_trip(trip) == "transit"

    @pytest.mark.parametrize(
        "trip",
        [
            {"destinations": [{"transportation": [{"type": "car"}]}]},
            {"destinations": [{"transportation": [{"type": "train"}]},
                              {"transportation": [{"type": "car"}]}]},
            {"destinations": [{"name": "no legs stated"}]},
            {},
        ],
    )
    def test_road_mixed_and_unstated_stay_driving(self, trip):
        """Driving is this generator's original and still most common case, so
        anything not unambiguously transit keeps it."""
        from generator.html_assembler import HTMLAssembler

        assert HTMLAssembler._maps_travelmode_for_trip(trip) == "driving"

    def test_prompt_guidance_names_the_booked_leg(self):
        from generator.ai_content import AIContentGenerator

        gen = AIContentGenerator.__new__(AIContentGenerator)
        text = gen._build_arrival_mode_guidance(
            {"transportation": [{"type": "train", "provider": "Eurostar",
                                 "label": "Brussels-Midi to Amsterdam Centraal"}]}
        )
        assert "train" in text and "Eurostar" in text
        assert "no highways" in text and "parking" in text

    def test_prompt_guidance_keeps_drives_as_drives(self):
        from generator.ai_content import AIContentGenerator

        gen = AIContentGenerator.__new__(AIContentGenerator)
        for dest in ({"transportation": [{"type": "car"}]}, {}, None):
            assert "as a drive" in gen._build_arrival_mode_guidance(dest)
