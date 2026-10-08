"""An item the pipeline kept, and no card was drawn for, is now a recorded fact.

WHAT IT COST
------------
The Old Hickory guide rebuilt 2026-10-08 was missing 33 attractions that the
published page carries. Seventeen of them had a reason recorded -- 9
`alltrails_confidence_denied_no_corroboration`, 3 `no_verified_url_removed`,
3 `closure_removed`, 2 `interest_filter_skipped` -- and every one of those was
the system working as designed.

The other **sixteen had no reason recorded anywhere.** Their last event was an
acceptance (fourteen of them `secondary_maps_link_attached_maps_place_id`, which
is discovery attaching a map badge beside a URL it kept), and **eight were
declared seeds**: Andrew Jackson's Hermitage, Grand Ole Opry, Old Hickory Lake,
Carter House, Cedars of Lebanon State Park, Natchez Trace Parkway, Fiddlers
Grove Historic Village, Biltmore Christmas at Antler Hill Village. `Carter
House` and `Cedars of Lebanon State Park` appear nowhere in the file at all.
`Grand Ole Opry` appears three times, all of it prose.

Nothing was wrong with the reasons that WERE written. The defect is that
disappearing after discovery had no reason at all, so the only way to find it
was to compare a built page against a published one -- archaeology, on a $2.94
build, for a defect that should have announced itself.

WHAT IS PINNED
--------------
That the run states the difference about itself: an item kept by the pipeline
with no card drawn for it is reported, with its seed status, because a seed
going missing is the traveler's explicit request being dropped.

HOW IT KNOWS
------------
The assembler RECORDS what it draws; nothing re-reads the HTML. That is
deliberate and it is the second lesson of the same day. Diagnosing this by
parsing the built page took three wrong answers: an item named in schedule prose
looked rendered, a curly apostrophe looked like a different place, and a count
of matches got read as an answer twice before anyone looked at what the matches
were. The writer knows what it wrote.
"""

from __future__ import annotations

import pytest

from generator.html_assembler import HTMLAssembler


def _assembler():
    a = HTMLAssembler.__new__(HTMLAssembler)
    a._accounted_items = {}
    return a


def _trip(*items):
    return {"destinations": [{
        "name": "Old Hickory, Tennessee",
        "ai_content": {"top_attractions": list(items)},
    }]}


class TestTheKeyFoldsWhatPunctuationSeparates:
    """The bug that made the diagnosis take three attempts."""

    def test_both_apostrophes_are_one_place(self):
        assert (HTMLAssembler.render_key("Andrew Jackson’s Hermitage")
                == HTMLAssembler.render_key("Andrew Jackson's Hermitage"))

    @pytest.mark.parametrize("a,b", [
        ("Blue Ridge Parkway – Folk Art Center", "Blue Ridge Parkway - Folk Art Center"),
        ("Benja Thai & Sushi", "Benja Thai and Sushi"),
        ("Café  Pasqual’s", "Cafe Pasqual's"),
    ])
    def test_the_other_forms_that_differ_without_differing(self, a, b):
        assert HTMLAssembler.render_key(a) == HTMLAssembler.render_key(b)

    def test_two_real_places_stay_apart(self):
        """Folding must not merge distinct items, or the report goes quiet for
        the wrong reason."""
        assert (HTMLAssembler.render_key("Hermitage Memorial Gardens")
                != HTMLAssembler.render_key("Andrew Jackson's Hermitage"))


class TestAnAcceptedItemWithNoCardIsReported:
    def test_the_case_from_the_build(self):
        a = _assembler()
        trip = _trip(
            {"name": "Andrew Jackson's Hermitage", "is_seed": True,
             "url": "https://thehermitage.com/"},
            {"name": "Hermitage Memorial Gardens", "url": "https://example.test/x"},
        )
        a._note_rendered("attraction", "Old Hickory, Tennessee",
                         "Hermitage Memorial Gardens")

        missing = a.unrendered_items(trip)

        assert [m["item"] for m in missing] == ["Andrew Jackson's Hermitage"]
        assert missing[0]["is_seed"] == "yes"
        assert missing[0]["had_url"] == "yes"

    def test_a_rendered_item_is_not_reported_across_the_apostrophe(self):
        """The whole reason the key exists: the trip says `'` and the card may
        say `’`, and that must not read as a missing item."""
        a = _assembler()
        a._note_rendered("attraction", "Old Hickory, Tennessee",
                         "Andrew Jackson’s Hermitage")

        assert a.unrendered_items(
            _trip({"name": "Andrew Jackson's Hermitage"})) == []

    def test_everything_rendered_reports_nothing(self):
        a = _assembler()
        a._note_rendered("attraction", "Old Hickory, Tennessee", "Old Hickory Lake")

        assert a.unrendered_items(_trip({"name": "Old Hickory Lake"})) == []

    def test_a_card_drawn_under_another_destination_accounts_for_the_item(self):
        """Matching is trip-wide ON PURPOSE, and this is the test that says so.

        Multi-site grouping draws a child's landmark on the group base's card,
        and the base's own list is filtered against what its children cover --
        with Delicate Arch as the comment's own example. A per-destination key
        would report every one of those intentional moves as a mystery, which
        is the noise that teaches a reader to ignore the line. The cost is that
        an item moving between destinations is not caught; the benefit is that
        what IS reported is worth reading.
        """
        a = _assembler()
        a._note_rendered("attraction", "Nashville, Tennessee", "Andrew Jackson's Hermitage")

        assert a.unrendered_items(_trip({"name": "Andrew Jackson's Hermitage"})) == []

    def test_a_grouping_dedupe_is_an_accounted_reason_not_a_mystery(self):
        """The drop documented as deliberate is now also recorded as such."""
        a = _assembler()
        a._note_accounted("attraction", "Delicate Arch", "covered_by_grouped_child")

        assert a.unrendered_items(_trip({"name": "Delicate Arch"})) == []
        assert a._accounted_items[("attraction", "delicate arch")] == "covered_by_grouped_child"

    def test_the_kind_is_part_of_the_match(self):
        a = _assembler()
        a._note_rendered("restaurant", "Old Hickory, Tennessee", "Two Rivers Mansion")

        assert len(a.unrendered_items(_trip({"name": "Two Rivers Mansion"}))) == 1

    def test_restaurants_are_reconciled_too(self):
        a = _assembler()
        trip = {"destinations": [{
            "name": "Lebanon, Tennessee",
            "ai_content": {"dinner_recommendations": [
                {"name": "Cedar City Brewing Company"},
                {"name": "Lebanon Public House"},
            ]},
        }]}
        a._note_rendered("restaurant", "Lebanon, Tennessee", "Lebanon Public House")

        missing = a.unrendered_items(trip)

        assert [m["item"] for m in missing] == ["Cedar City Brewing Company"]
        assert missing[0]["kind"] == "restaurant"

    def test_a_nameless_item_is_not_reported(self):
        """It has nothing to match on, and reporting it would be noise that
        teaches a reader to ignore the line."""
        a = _assembler()

        assert a.unrendered_items(_trip({"name": ""}, {"url": "x"})) == []


class TestTheRecordCannotLeakBetweenRenders:
    def test_assemble_clears_what_the_last_render_drew(self):
        """One run assembles twice when it writes the private copy. A record
        carried over would report the second render as complete whatever it
        actually drew, which is the failure mode this whole file is about."""
        import inspect

        src = inspect.getsource(HTMLAssembler.assemble)
        assert "_accounted_items" in src, (
            "assemble must reset the record, or the private render inherits the "
            "shareable render's claims"
        )
        reset = src.index("_accounted_items")
        template = src.index("TEMPLATE_PATH.read_text")
        assert reset < template, "reset before anything is drawn"


class TestTheCheckReportsItsDenominator:
    """The defect in the first version of this module, found by spending a build.

    It returned a bare list, and on the 2026-10-08 Old Hickory build it returned
    `[]` while 7 attractions and 48 restaurants had no card. An empty list cannot
    tell "nothing missing" from "nothing examined" -- the same class the guard
    was built to catch, in the guard.
    """

    def test_it_says_how_many_it_examined(self):
        a = _assembler()
        a._note_rendered("attraction", "Old Hickory, Tennessee", "Old Hickory Lake")
        expected = [
            {"kind": "attraction", "item": "Old Hickory Lake"},
            {"kind": "attraction", "item": "Andrew Jackson's Hermitage"},
        ]

        out = a.render_reconciliation({}, expected)

        assert out["examined"] == 2
        assert out["accounted"] == 1
        assert [m["item"] for m in out["missing"]] == ["Andrew Jackson's Hermitage"]

    def test_examining_nothing_is_not_reported_as_nothing_missing(self):
        """The exact shape that went quiet: zero missing out of zero examined
        has to be distinguishable from zero missing out of ninety-two."""
        a = _assembler()

        out = a.render_reconciliation({}, [])

        assert out["missing"] == []
        assert out["examined"] == 0, "a reader must be able to see it checked nothing"

    def test_the_baseline_is_named(self):
        """Which baseline was used changes what the number means, so the result
        says which one it is rather than leaving the caller to infer it."""
        a = _assembler()

        assert a.render_reconciliation({}, [{"kind": "attraction", "item": "x"}]
                                       )["baseline"] == "accepted_upstream"
        assert a.render_reconciliation({"destinations": []})["baseline"] == "trip_at_render"

    def test_the_trip_baseline_is_the_fallback_and_still_counts(self):
        a = _assembler()
        trip = _trip({"name": "Two Rivers Mansion"}, {"name": "Cedar Hill Park"})
        a._note_rendered("attraction", "Old Hickory, Tennessee", "Cedar Hill Park")

        out = a.render_reconciliation(trip)

        assert out["examined"] == 2 and out["accounted"] == 1

    def test_the_upstream_baseline_survives_a_trip_trimmed_after_acceptance(self):
        """The reason the baseline moved. The per-day cap rewrites the trip's
        own list before assembly, so an item accepted upstream is absent from
        the trip by render time -- and measuring against the trip would call it
        accounted for."""
        a = _assembler()
        trimmed_trip = _trip()          # the cap left nothing behind
        expected = [{"kind": "restaurant", "item": "Cedar City Brewing Company"}]

        out = a.render_reconciliation(trimmed_trip, expected)

        assert out["examined"] == 1
        assert [m["item"] for m in out["missing"]] == ["Cedar City Brewing Company"]
