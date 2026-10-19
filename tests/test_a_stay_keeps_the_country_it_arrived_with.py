"""A part the structured answer left blank is topped up from the flat string.

WHAT IT COST
------------
`_locality` reached the flat `city` string only under `if not out.get("city")`.
So an extraction that answered

    {"locality": {"city": "Hvar", "region": "Split-Dalmatia", "country": ""},
     "city": "Hvar, Split-Dalmatia, Croatia"}

kept the empty country forever: a stay that had a city had every part it was
ever going to get, and the "Croatia" sitting in its own `city` string one key
away was unreachable. Models do answer that way -- a blank string for a key
they were asked for is the commonest shape of a partial answer, and `_part`
turns it into nothing just as it would a missing key.

What it costs is downstream, in `geocoder.town_level_query`, which asks
Nominatim for the parts joined with commas. "Hvar, Split-Dalmatia" instead of
"Hvar, Split-Dalmatia, Croatia" -- and a town name is not unique across
countries, so the pin can land in the wrong one. That is the confidently wrong
answer the geocoder's own `_MIN_PARTS_TO_REDUCE` comment says is worse than no
answer.

WHAT IS PINNED
--------------
That a blank part is filled from the flat string, that a stated part is never
overwritten by it, and -- the care this needs -- that two strings naming
DIFFERENT towns are not merged. A flat `city` for another town is not a fuller
version of the same place, and merging the two would file one town's country
under another town's name, which is the confidently wrong pin again by a
different route.
"""

from __future__ import annotations

import pytest

from generator.reservation_ingest import reservation_to_manifest_fragment


def _locality_of(answer: dict) -> dict:
    _, fragment = reservation_to_manifest_fragment(dict(answer, kind="lodging"))
    return fragment.get("locality", {})


class TestABlankPartIsFilledIn:
    def test_a_blank_country_is_taken_from_the_flat_string(self):
        """The case that was unreachable: a city was present, so the flat
        string was never looked at."""
        locality = _locality_of({
            "locality": {"city": "Hvar", "region": "Split-Dalmatia", "country": ""},
            "city": "Hvar, Split-Dalmatia, Croatia",
        })

        assert locality == {"city": "Hvar", "region": "Split-Dalmatia",
                            "country": "Croatia"}

    def test_a_missing_country_key_is_the_same_as_a_blank_one(self):
        locality = _locality_of({
            "locality": {"city": "Hvar", "region": "Split-Dalmatia"},
            "city": "Hvar, Split-Dalmatia, Croatia",
        })

        assert locality["country"] == "Croatia"

    def test_a_blank_region_is_filled_from_a_two_letter_code(self):
        locality = _locality_of({
            "locality": {"city": "Springdale", "region": ""},
            "city": "Springdale, UT",
        })

        assert locality == {"city": "Springdale", "region": "UT"}

    def test_the_stated_parts_still_win(self):
        """The flat string tops up; it does not correct. A model that answered
        the structured object was asked for it, and the string is the older,
        rougher source."""
        locality = _locality_of({
            "locality": {"city": "Hvar", "region": "Split-Dalmatia County"},
            "city": "Hvar, Split-Dalmatia, Croatia",
        })

        assert locality["region"] == "Split-Dalmatia County"


class TestTwoDifferentTownsAreNotMerged:
    def test_a_flat_string_for_another_town_is_left_alone(self):
        """The whole of the care here. `city` holding a different town is not
        a fuller version of this stay's place -- it is another place, often the
        booker's own address or a previous leg -- and taking its country would
        file Split's country under Seattle's name."""
        locality = _locality_of({
            "locality": {"city": "Seattle", "region": "WA"},
            "city": "Split, Split-Dalmatia, Croatia",
        })

        assert locality == {"city": "Seattle", "region": "WA"}
        assert "country" not in locality

    def test_agreement_is_not_case_sensitive(self):
        """One source shouting is not a different town."""
        locality = _locality_of({
            "locality": {"city": "HVAR"},
            "city": "Hvar, Split-Dalmatia, Croatia",
        })

        assert locality == {"city": "HVAR", "region": "Split-Dalmatia",
                            "country": "Croatia"}


class TestWhatItMustNotHaveBroken:
    @pytest.mark.parametrize("city, expected", [
        ("Moab", {"city": "Moab"}),
        ("Springdale, UT", {"city": "Springdale", "region": "UT"}),
        ("Split, Croatia", {"city": "Split"}),
        ("Kanab, Utah", {"city": "Kanab"}),
        ("Hvar, Split-Dalmatia, Croatia",
         {"city": "Hvar", "region": "Split-Dalmatia", "country": "Croatia"}),
        ("33221 State Road 20, Oak Harbor, WA", {}),
        ("", {}),
    ])
    def test_a_flat_string_alone_splits_exactly_as_before(self, city, expected):
        """The same table as test_reservation_ingest.py's, kept here because
        this change moved the code that reads it out from behind its guard.
        A two-part string still drops a second part that could be either a
        state or a country."""
        assert _locality_of({"city": city}) == expected

    def test_an_address_in_the_flat_string_tops_up_nothing(self):
        """A digit in the first part means the string is a street line, and a
        street line's later parts are not a region and a country to be filed."""
        locality = _locality_of({
            "locality": {"city": "Oak Harbor"},
            "city": "33221 State Road 20, Oak Harbor, WA",
        })

        assert locality == {"city": "Oak Harbor"}

    def test_a_structured_answer_with_no_flat_string_is_untouched(self):
        locality = _locality_of({
            "locality": {"city": "Anacortes", "region": "Washington",
                         "country": "United States"},
        })

        assert locality == {"city": "Anacortes", "region": "Washington",
                            "country": "United States"}
