"""One paid run emits the shareable guide and the traveler's own copy.

THE DECISION
------------
Owner ruling 2026-10-07, option C of three. A redacted guide is not a travel
document: `_apply_privacy_redaction` drops every `transportation` leg wholesale,
so the published page has no flights, no rental, no record locators -- and the
owner reads their own confirmations off their own guide. The rejected
alternatives were (A) an exemption flag for the owner's builds, refused because
it makes correctness depend on a flag being right every time and the same week's
review found two instances of that failing, and (B) the owner simply losing it.

WHY REDACTION DID NOT MOVE TO RENDER TIME
----------------------------------------
The obvious implementation of "two renders" is to redact late. It opens a leak.
`ai_content._booked_leg_guidance` builds a prompt from a leg's `provider` and
`label` -- "Booked leg: Alaska Airlines -- AS 212 SEA to LAS" -- and tells the
model to describe that journey by operator and terminal. Today prod never
reaches it, because redaction already emptied the list. Redact later and prod
prose starts naming the carrier and the flight, and no subsequent pass can
retract it: clearing a list does not unwrite a paragraph derived from it.

So redaction keeps its position and WITHHOLDS instead of destroying. The
shareable render is bit-for-bit what it was; the personal copy restores the
withheld payload into a COPY at assembly. The two differ in disclosure, never in
content, and the cost is one extra render rather than one extra run -- the search
and the model calls are already paid for by then.
"""

from __future__ import annotations

import copy

import pytest

from generator import main as main_mod


def _trip():
    return {
        "trip": {"transportation": [
            {"type": "flight", "provider": "Alaska Airlines",
             "label": "AS 212 SEA to LAS", "confirmation_number": "QJ7M2P"},
        ]},
        "destinations": [
            {
                "name": "Springdale, Utah",
                "planning_links": [{"label": "Our plans", "url": "https://docs.example/plan"}],
                "lodging": {
                    "name": "Cliffrose Lodge",
                    "website": "https://www.cliffroselodge.com/",
                    "confirmation_number": "ABC123",
                    "total_cost": 742.18,
                    "currency": "USD",
                    "location": "281 Zion Park Blvd",
                    "checkin_time": "16:00",
                },
                "transportation": [
                    {"type": "rail", "provider": "Amtrak", "label": "CS 11",
                     "confirmation_number": "ZZ9"},
                ],
            },
            {"name": "Bryce, Utah"},
        ],
    }


class TestTheShareableGuideIsUnchanged:
    """The published page is the one that must not move. Every assertion here
    held before this change and has to keep holding."""

    def test_everything_sensitive_is_still_gone_from_the_trip(self):
        trip = _trip()
        main_mod._apply_privacy_redaction(trip)
        dest = trip["destinations"][0]

        assert trip["trip"]["transportation"] == []
        assert dest["transportation"] == []
        assert dest["lodging"]["name"] == ""
        assert dest["lodging"]["website"] == ""
        assert dest["lodging"]["confirmation_number"] == ""
        assert "total_cost" not in dest["lodging"]
        assert "currency" not in dest["lodging"]
        assert dest["planning_links"] == [
            {"label": "Trip Plans", "url": "", "redacted": True}
        ]

    def test_what_drives_routing_and_schedule_is_still_kept(self):
        trip = _trip()
        main_mod._apply_privacy_redaction(trip)

        assert trip["destinations"][0]["lodging"]["location"] == "281 Zion Park Blvd"
        assert trip["destinations"][0]["lodging"]["checkin_time"] == "16:00"

    def test_the_counts_are_unchanged(self):
        """Callers echo these to the operator; the shape is part of the contract."""
        counts = main_mod._apply_privacy_redaction(_trip())

        assert counts == {
            "planning_links": 1, "lodging_names": 1, "lodging_websites": 1,
            "lodging_confirmations": 1, "transportation": 2,
        }

    def test_the_trip_never_carries_the_payload(self):
        """The invariant `test_redaction_still_removes_everything_sensitive`
        guards, and the reason this is an out-parameter rather than a key on the
        trip: the first version of this change parked the payload on the trip for
        one statement and that test caught it. No record locator may survive
        anywhere in the trip, not even somewhere nothing renders from.
        """
        trip = _trip()
        withheld = {}
        main_mod._apply_privacy_redaction(trip, withheld)

        assert "QJ7M2P" not in repr(trip) and "ABC123" not in repr(trip)
        assert "QJ7M2P" in repr(withheld), "the caller still gets it"

    def test_a_caller_that_wants_nothing_back_is_unaffected(self):
        """Every pre-existing caller and test passes one argument."""
        trip = _trip()
        counts = main_mod._apply_privacy_redaction(trip)

        assert counts["transportation"] == 2
        assert "QJ7M2P" not in repr(trip)


class TestThePersonalCopyGetsItAllBack:
    def test_every_withheld_field_is_restored(self):
        trip = _trip()
        withheld = {}
        main_mod._apply_privacy_redaction(trip, withheld)

        personal = copy.deepcopy(trip)
        main_mod._restore_privacy_payload(personal, withheld)
        dest = personal["destinations"][0]

        assert personal["trip"]["transportation"][0]["label"] == "AS 212 SEA to LAS"
        assert personal["trip"]["transportation"][0]["confirmation_number"] == "QJ7M2P"
        assert dest["transportation"][0]["provider"] == "Amtrak"
        assert dest["lodging"]["name"] == "Cliffrose Lodge"
        assert dest["lodging"]["website"] == "https://www.cliffroselodge.com/"
        assert dest["lodging"]["confirmation_number"] == "ABC123"
        assert dest["lodging"]["total_cost"] == 742.18
        assert dest["lodging"]["currency"] == "USD"
        assert dest["planning_links"][0]["url"] == "https://docs.example/plan"

    def test_restoring_does_not_disturb_the_redacted_original(self):
        """The two renders come from one run, so a restore that wrote through to
        the shared object would publish the confirmations."""
        trip = _trip()
        withheld = {}
        main_mod._apply_privacy_redaction(trip, withheld)

        personal = copy.deepcopy(trip)
        main_mod._restore_privacy_payload(personal, withheld)

        assert trip["destinations"][0]["lodging"]["confirmation_number"] == ""
        assert trip["destinations"][0]["transportation"] == []
        assert trip["trip"]["transportation"] == []

    def test_a_destination_that_withheld_nothing_is_left_alone(self):
        trip = _trip()
        withheld = {}
        main_mod._apply_privacy_redaction(trip, withheld)

        personal = copy.deepcopy(trip)
        main_mod._restore_privacy_payload(personal, withheld)

        assert personal["destinations"][1] == {"name": "Bryce, Utah"}

    def test_destinations_are_addressed_by_index_not_by_name(self):
        """Two destinations can share a name -- a multi-night stay split across
        groups does exactly that -- and a name key would restore into the wrong
        one or neither."""
        trip = _trip()
        trip["destinations"][1]["name"] = "Springdale, Utah"
        trip["destinations"][1]["lodging"] = {"name": "Other Lodge"}
        withheld = {}
        main_mod._apply_privacy_redaction(trip, withheld)

        personal = copy.deepcopy(trip)
        main_mod._restore_privacy_payload(personal, withheld)

        assert personal["destinations"][0]["lodging"]["name"] == "Cliffrose Lodge"
        assert personal["destinations"][1]["lodging"]["name"] == "Other Lodge"


class TestTheSecondRenderIsSkippedWhenItWouldBeIdentical:
    def test_a_manifest_with_nothing_private_gets_no_personal_copy(self):
        """A tester's trip with no confirmations and no booked legs renders the
        same bytes twice, which is waste rather than a feature."""
        trip = {"destinations": [{"name": "Bryce, Utah",
                                 "lodging": {"location": "somewhere"}}]}
        withheld = {}
        main_mod._apply_privacy_redaction(trip, withheld)

        assert not main_mod._privacy_payload_has_content(withheld)

    def test_a_real_trip_does_get_one(self):
        trip = _trip()
        withheld = {}
        main_mod._apply_privacy_redaction(trip, withheld)

        assert main_mod._privacy_payload_has_content(withheld)

    @pytest.mark.parametrize("value", [None, {}, "nonsense", 7])
    def test_nothing_withheld_reads_as_nothing_to_render(self, value):
        assert main_mod._privacy_payload_has_content(value) is False
