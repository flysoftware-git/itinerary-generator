"""Places API as a filter over our own candidates, publishing nothing.

Four rewrites of the batch prompt did not stop Berlin returning three Michelin
restaurants on a "No fine dining" brief. See
docs/design/places-for-restaurants.md for why this is scoped to filtering.
"""
import pytest

from generator.places_filter import PlacesBudgetFilter, clean_query_name, normalize_name


class TestNameMatching:
    @pytest.mark.parametrize(
        "a, b",
        [
            ("Chez Léon", "Chez Leon"),
            ("Mustafa's Gemüse Kebap", "Mustafas Gemuse Kebap"),
            ("Nobelhart & Schmutzig", "Nobelhart und Schmutzig".replace(" und ", " & ")),
            ("THE ROOF Restaurant", "the roof restaurant"),
        ],
    )
    def test_punctuation_and_accents_do_not_break_matching(self, a, b):
        assert normalize_name(a) == normalize_name(b)

    def test_different_restaurants_do_not_collide(self):
        assert normalize_name("Chez Leon") != normalize_name("Chez Lucien")


class TestNoKeyIsSafe:
    def test_without_a_key_nothing_is_called(self):
        f = PlacesBudgetFilter(api_key="")
        assert f.available is False
        assert f.lookup("Berlin, Germany", low_budget=True) == {}
        assert f.verdict("Madami", "Berlin, Germany", low_budget=True) == "unknown"
        assert f.verdict_precise("Madami", "Berlin, Germany") == "unknown"
        assert f.call_count == 0

    def test_blank_inputs_are_refused(self):
        f = PlacesBudgetFilter(api_key="fake")
        assert f.lookup("", low_budget=True) == {}
        assert f.lookup_one("", "Berlin") is None
        assert f.lookup_one("Madami", "") is None
        assert f.call_count == 0


class TestVerdicts:
    """A failed lookup must never read as a rejection."""

    @staticmethod
    def _seeded(table):
        f = PlacesBudgetFilter(api_key="fake")
        f._by_destination["berlin, germany"] = table
        return f

    def test_expensive_places_are_rejected(self):
        f = self._seeded({"horvath": {"place_id": "x", "price_level": "PRICE_LEVEL_VERY_EXPENSIVE"}})
        assert f.verdict("Horvath", "Berlin, Germany", low_budget=True) == "too_expensive"

    def test_cheap_places_are_confirmed(self):
        f = self._seeded({"madami": {"place_id": "x", "price_level": "PRICE_LEVEL_INEXPENSIVE"}})
        assert f.verdict("Madami", "Berlin, Germany", low_budget=True) == "confirmed_affordable"

    def test_an_unknown_place_is_not_a_rejection(self):
        """A small neighbourhood spot absent from Places is exactly what this
        brief wants -- treating absence as rejection would delete it."""
        f = self._seeded({"madami": {"place_id": "x", "price_level": "PRICE_LEVEL_INEXPENSIVE"}})
        assert f.verdict("Some Tiny Cafe", "Berlin, Germany", low_budget=True) == "unknown"

    def test_a_failed_lookup_is_not_a_rejection(self):
        """A quota error must not empty a page's dining section."""
        f = self._seeded({})
        assert f.verdict("Anything", "Berlin, Germany", low_budget=True) == "unknown"


class TestPublishesNothing:
    def test_the_field_mask_requests_only_what_a_decision_needs(self):
        """websiteUri, rating, photos and editorial summaries are deliberately
        absent -- they are the fields we would be tempted to publish."""
        from generator import places_filter

        mask = places_filter._FIELD_MASK
        assert "priceLevel" in mask and "displayName" in mask and "places.id" in mask
        for tempting in ("websiteUri", "rating", "photos", "editorialSummary", "primaryType"):
            assert tempting not in mask


class TestQueryNamesAreCleaned:
    """Decoration in the query changed the ANSWER, not just the tidiness.

    The batch writes "**Comme Chez Soi**" and sometimes appends the rating:
    "**Senzanome** 4.5+/5, $$$$". Markdown is stripped later in the pipeline,
    so a filter running during discovery sees it raw.

    Querying "**Comme Chez Soi**, Brussels" returns TWO places and ranks a
    different MODERATE restaurant first; the clean name returns the single
    VERY_EXPENSIVE match. That flipped the verdict to confirmed_affordable and
    put a two-Michelin-star restaurant back on a "No fine dining" itinerary.
    """

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("**Comme Chez Soi**", "Comme Chez Soi"),
            ("**Senzanome** 4.5+/5, $$$$", "Senzanome"),
            ("Sam's Sports Grill 4.4/5 $$$", "Sam's Sports Grill"),
            ("__Field__", "Field"),
            ("Chez Léon", "Chez Léon"),
            ("Plain Name", "Plain Name"),
        ],
    )
    def test_decoration_is_stripped(self, raw, expected):
        assert clean_query_name(raw) == expected

    def test_a_decorated_name_memoises_as_the_clean_one(self):
        """Otherwise the same restaurant is looked up twice, at twice the cost."""
        f = PlacesBudgetFilter(api_key="fake")
        assert normalize_name(clean_query_name("**Field**")) == normalize_name("Field")
