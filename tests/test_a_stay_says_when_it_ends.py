"""A lodging card says when the stay ends, not only when it may begin.

WHAT WAS MISSING
----------------
`lodging` accepted `checkin_time` and had no counterpart, so a guide could tell
a traveler when they could get into a room and never when they had to be out of
it -- and of the two, check-out is the one with a deadline attached.

WHAT WAS ALREADY THERE, and is worth recording because it was nearly rebuilt
----------------------------------------------------------------------------
Vendor links already render, both kinds:

  * `_build_lodging_card` draws `lodging.website` as a **Property** row linking
    to the property's own site.
  * `_build_transportation_pills` draws each booked leg as a chip linking to
    `leg.website` -- the carrier's manage-booking page -- and falls back to a
    non-link chip carrying the same tooltip when no website is stored, because
    the confirmation code is worth keeping even with nowhere to click.

So "link reservations to the vendor for updated status" needed no rendering
work. What it needs is `website` present in the DATA: the sidecars carry it for
lodging and carry none for transportation legs.
"""

from __future__ import annotations

import pytest

from generator.html_assembler import HTMLAssembler
from generator.manifest_parser import MANIFEST_SCHEMA


def _lodging_schema():
    def find(node):
        if isinstance(node, dict):
            if "checkin_time" in (node.get("properties") or {}):
                return node["properties"]
            for v in node.values():
                if (found := find(v)) is not None:
                    return found
        elif isinstance(node, list):
            for v in node:
                if (found := find(v)) is not None:
                    return found
        return None

    return find(MANIFEST_SCHEMA)


def _card(**lodging):
    a = HTMLAssembler.__new__(HTMLAssembler)
    return a._build_lodging_card({"lodging": lodging})


class TestTheSchemaAcceptsIt:
    def test_checkout_time_sits_beside_checkin_time(self):
        props = _lodging_schema()

        assert "checkout_time" in props
        assert props["checkout_time"]["type"] == "string"

    def test_it_is_free_text_like_its_counterpart(self):
        """A confirmation says "11:00 AM" or "by 11am". Parsing that into a time
        would invent precision the booking does not have."""
        props = _lodging_schema()

        assert props["checkout_time"] == props["checkin_time"]


class TestTheCardShowsIt:
    def test_check_out_renders(self):
        html = _card(name="Hyatt Moab", checkout_time="11:00 AM")

        assert "Check-out" in html
        assert "11:00 AM" in html

    def test_it_sits_beside_check_in_not_after_the_confirmation(self):
        """The two times are one fact about the stay."""
        html = _card(name="Hyatt Moab", checkin_time="4:00 PM",
                     checkout_time="11:00 AM", confirmation_number="21176490")

        assert html.index("Check-in") < html.index("Check-out") < html.index("Confirmation")

    def test_a_time_alone_still_does_not_conjure_a_card(self):
        """Both times survive redaction, so keying the card on either would
        resurrect it in exactly the builds meant not to have one -- the rule
        `checkin_time` already follows."""
        assert _card(checkout_time="11:00 AM") == ""
        assert _card(checkin_time="4:00 PM", checkout_time="11:00 AM") == ""

    def test_it_is_escaped(self):
        html = _card(name="Hotel", checkout_time='11:00 <script>alert(1)</script>')

        assert "<script>" not in html

    def test_a_stay_with_no_checkout_is_unchanged(self):
        html = _card(name="Hyatt Moab", checkin_time="4:00 PM")

        assert "Check-out" not in html


class TestTheVendorLinksThatAlreadyWorked:
    """Pinned because they were nearly rebuilt. The gap was data, not code."""

    def test_lodging_links_to_the_property(self):
        html = _card(name="Capitol Reef Resort", website="https://capitolreefresort.com/")

        assert "Property" in html
        assert 'href="https://capitolreefresort.com/"' in html

    def test_a_booked_leg_links_to_the_carrier(self):
        a = HTMLAssembler.__new__(HTMLAssembler)
        pills = a._build_transportation_pills({"transportation": [
            {"type": "plane", "provider": "Alaska Airlines", "label": "AS 212",
             "confirmation_number": "KGXQLP", "website": "https://www.alaskaair.com/"},
        ]})

        assert len(pills) == 1
        assert 'href="https://www.alaskaair.com/"' in pills[0]
        assert "KGXQLP" in pills[0], "the confirmation rides in the tooltip"

    def test_a_leg_with_no_website_still_renders_its_confirmation(self):
        """Which is the state every leg in the sidecars is actually in."""
        a = HTMLAssembler.__new__(HTMLAssembler)
        pills = a._build_transportation_pills({"transportation": [
            {"type": "car", "provider": "Avis", "confirmation_number": "14912484US6"},
        ]})

        assert len(pills) == 1
        assert "<a " not in pills[0]
        assert "14912484US6" in pills[0]


class TestTheIngestCanCaptureIt:
    @pytest.mark.parametrize("needle", ['"checkout_time": string', "checkout_time"])
    def test_the_extraction_prompt_and_field_list_know_the_field(self, needle):
        import inspect

        from generator import reservation_ingest

        assert needle in inspect.getsource(reservation_ingest)
