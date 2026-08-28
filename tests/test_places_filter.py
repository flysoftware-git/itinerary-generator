"""Places API as a filter over our own candidates, publishing nothing.

Four rewrites of the batch prompt did not stop Berlin returning three Michelin
restaurants on a "No fine dining" brief. See
docs/design/places-for-restaurants.md for why this is scoped to filtering.
"""
import pytest

from generator.places_filter import PlacesBudgetFilter, normalize_name


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
