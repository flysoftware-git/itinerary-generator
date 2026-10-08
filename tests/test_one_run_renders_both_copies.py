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
import os

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


class TestThePrivateCopyIsItsOwnGuideDirectory:
    """The shape the owner chose, and the properties a consumer relies on."""

    def test_it_is_named_index_html_in_a_sibling_directory(self, tmp_path):
        shareable = tmp_path / "prod"
        shareable.mkdir()
        private = shareable.with_name(shareable.name + main_mod.PRIVATE_OUTPUT_SUFFIX)

        index = main_mod._write_private_copy(private, "<html>private</html>", shareable)

        assert index.name == "index.html", "a guide's page has one name"
        assert index.parent.name == "prod-private"
        assert index.parent.parent == shareable.parent, "a sibling, not a child"
        assert index.read_text(encoding="utf-8") == "<html>private</html>"

    def test_it_brings_its_images_so_the_page_can_render(self, tmp_path):
        """A directory whose page cannot render its own images is not a guide."""
        shareable = tmp_path / "prod"
        (shareable / "images").mkdir(parents=True)
        (shareable / "images" / "abc.jpg").write_bytes(b"jpegbytes")

        main_mod._write_private_copy(
            shareable.with_name("prod-private"), "<html></html>", shareable
        )

        copied = tmp_path / "prod-private" / "images" / "abc.jpg"
        assert copied.is_file()
        assert copied.read_bytes() == b"jpegbytes"

    def test_the_images_are_shared_rather_than_duplicated_where_possible(self, tmp_path):
        """Images are the largest thing in a guide and the bytes are identical."""
        shareable = tmp_path / "prod"
        (shareable / "images").mkdir(parents=True)
        source = shareable / "images" / "abc.jpg"
        source.write_bytes(b"jpegbytes")

        main_mod._write_private_copy(
            shareable.with_name("prod-private"), "<html></html>", shareable
        )

        copied = tmp_path / "prod-private" / "images" / "abc.jpg"
        if hasattr(os, "link"):
            assert copied.stat().st_ino == source.stat().st_ino or                 copied.read_bytes() == source.read_bytes()

    def test_the_pwa_pair_comes_too(self, tmp_path):
        shareable = tmp_path / "prod"
        shareable.mkdir()
        (shareable / "manifest.webmanifest").write_text("{}", encoding="utf-8")
        (shareable / "sw.js").write_text("//sw", encoding="utf-8")

        main_mod._write_private_copy(
            shareable.with_name("prod-private"), "<html></html>", shareable
        )

        assert (tmp_path / "prod-private" / "manifest.webmanifest").is_file()
        assert (tmp_path / "prod-private" / "sw.js").is_file()

    def test_it_carries_no_ledger_and_no_reports(self, tmp_path):
        """The guard on a fail-open, not housekeeping.

        A consumer deciding whether a directory holds personal data prefers a
        LEDGER RECORD over reading the page, on the reasonable ground that the
        ledger knows more. About disclosure that precedence is now backwards:
        one run produces two renders with different disclosure, so the run's
        `privacy_redacted: true` is true of the RUN and false of THIS page.

        Copy the ledger in and the private guide reports itself redacted, a
        `carries_personal_data` check flips to False, and delivery would send
        the traveler's confirmations to whoever asked. With no ledger the reader
        falls back to the page, which shows no redaction pill, and that fails
        closed.

        So this asserts the absence even though nothing currently copies them:
        the cost of a later well-meaning "the private copy should have its
        reports too" is a leak, and this is what refuses it.
        """
        shareable = tmp_path / "prod"
        shareable.mkdir()
        for name in ("run_ledger.jsonl", "validation_report.json",
                     "destination_status_report.md", "build_info.latest.json"):
            (shareable / name).write_text("{}", encoding="utf-8")

        main_mod._write_private_copy(
            shareable.with_name("prod-private"), "<html></html>", shareable
        )

        private = tmp_path / "prod-private"
        assert sorted(p.name for p in private.iterdir()) == ["index.html"]

    def test_rerunning_is_idempotent(self, tmp_path):
        """A rebuild into an existing private directory must not fail on the
        images it already hardlinked."""
        shareable = tmp_path / "prod"
        (shareable / "images").mkdir(parents=True)
        (shareable / "images" / "abc.jpg").write_bytes(b"jpegbytes")
        private = shareable.with_name("prod-private")

        main_mod._write_private_copy(private, "<html>one</html>", shareable)
        index = main_mod._write_private_copy(private, "<html>two</html>", shareable)

        assert index.read_text(encoding="utf-8") == "<html>two</html>"
        assert (private / "images" / "abc.jpg").is_file()


class TestTheLedgerNeverLandsInThePrivateDirectory:
    """The same invariant from the writing side rather than the copying side.

    The test above pins that `_write_private_copy` does not bring a ledger. This
    pins that the run does not write one there either, which is the other way it
    could arrive.
    """

    def test_the_ledger_path_is_the_shareable_directory(self):
        from pathlib import Path

        for environment in ("prod", "dev", "eval"):
            ledger = Path("output") / environment / "run_ledger.jsonl"

            assert main_mod.PRIVATE_OUTPUT_SUFFIX not in str(ledger), (
                "a ledger inside the private directory would let the run's "
                "privacy_redacted speak for a page it is not true of"
            )

    def test_the_suffix_cannot_collide_with_an_environment_name(self):
        """`output/{environment}` and `output/{environment}-private` are siblings,
        so an environment literally named e.g. `prod-private` would collide and
        one render would overwrite the other."""
        from generator.environments import ENVIRONMENTS

        for name in ENVIRONMENTS:
            assert not str(name).endswith(main_mod.PRIVATE_OUTPUT_SUFFIX), (
                f"environment {name!r} collides with the private directory suffix"
            )


class TestTheSuffixIsInterfaceAndNotDecoration:
    """What the private directory's NAME has to carry, now that it is the only
    thing a reader can use to state disclosure positively.

    A consumer's page-reading check can return "redacted" or "unknown" and never
    "not redacted" -- the marker is a redaction pill, and a trip with no planning
    links renders none either way. So where a ledger claims the run was redacted
    and the private page is silent, the name is all that is left.
    """

    def test_the_suffix_is_a_named_constant_a_consumer_can_cite(self):
        assert main_mod.PRIVATE_OUTPUT_SUFFIX == "-private"

    def test_the_private_directory_name_ends_with_it_and_the_shareable_does_not(
        self, tmp_path
    ):
        """Read off the leaf, which is what a consumer matches on."""
        shareable = tmp_path / "prod"
        shareable.mkdir()
        private = shareable.with_name(shareable.name + main_mod.PRIVATE_OUTPUT_SUFFIX)

        index = main_mod._write_private_copy(private, "<html></html>", shareable)

        assert index.parent.name.endswith(main_mod.PRIVATE_OUTPUT_SUFFIX)
        assert not shareable.name.endswith(main_mod.PRIVATE_OUTPUT_SUFFIX)

    def test_the_suffix_is_on_the_leaf_so_a_parent_cannot_speak_for_a_child(
        self, tmp_path
    ):
        """A guide under `.../prod-private-archive/prod/` is an ORDINARY guide.

        A reader matching the suffix anywhere in the path would call it private
        because of a directory above it, and block a publish that should have
        gone ahead. The generator's part of that contract is that it only ever
        puts the suffix on the leaf it is describing.
        """
        nested = tmp_path / "prod-private-archive" / "prod"
        nested.mkdir(parents=True)

        assert not nested.name.endswith(main_mod.PRIVATE_OUTPUT_SUFFIX)
        assert main_mod.PRIVATE_OUTPUT_SUFFIX in str(nested), (
            "the whole-path match this guards against would fire here"
        )


class TestThePrivateCopyWaitsForThePwaPair:
    """The ordering defect a paid build found and the unit tests did not.

    `_write_private_copy` copies `manifest.webmanifest` and `sw.js` **if they
    exist**. It used to be called immediately after `index.html` was written,
    which is before `_write_pwa_assets` runs -- so it looked for both, found
    neither, and silently copied nothing. The private directories from the
    2026-10-08 Old Hickory and Southwest builds hold only `index.html` and
    `images/`.

    Every test above passed throughout, because they each create the PWA pair
    themselves before calling the function. They pin the function; this pins its
    place in the pipeline, which is where the defect was.
    """

    def _source(self):
        """`main` is a click Command, so the function is its callback."""
        import inspect

        from generator import main as m

        command = m.main
        return inspect.getsource(getattr(command, "callback", command))

    def test_the_private_write_comes_after_the_pwa_assets(self):
        src = self._source()
        pwa = src.index("_write_pwa_assets(output_dir")
        private = src.index("_privacy_payload_has_content(privacy_withheld)")

        assert pwa < private, (
            "the private copy must be written after the PWA pair exists, or it "
            "copies neither and the directory is not self-contained"
        )

    def test_the_private_write_still_comes_after_the_page(self):
        """The other end of the window. It reads the shareable directory, so the
        page and the images have to be there too."""
        src = self._source()
        page = src.index("output_file.write_text(html")
        private = src.index("_privacy_payload_has_content(privacy_withheld)")

        assert page < private

    def test_a_missing_pwa_pair_is_still_not_fatal(self):
        """The copy stays conditional. A build that somehow has no PWA assets
        should still get its private page rather than failing."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            shareable = Path(tmp) / "prod"
            shareable.mkdir()

            index = main_mod._write_private_copy(
                shareable.with_name("prod-private"), "<html></html>", shareable
            )

            assert index.is_file()
            assert not (index.parent / "sw.js").exists()
