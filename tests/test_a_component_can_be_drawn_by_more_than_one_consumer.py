"""There is somewhere to put a component, and it is verified like the template.

THE DEFECT
----------
`templates/` held three files: `v2.5_template.html` (the whole guide),
`checksums.txt` and `template_versions.json`. So there was nowhere to put a
component. A thing drawn on a page was either inline markup built by a Python
function in `html_assembler`, or more of that one monolithic template.

That is fine for a page nobody else draws. It is not fine for a component meant
to be drawn the same way by more than one consumer of this engine: there is
nothing to include, so the second consumer reimplements the idea from the look
of it and the two drift apart with nothing failing.

WHAT THIS WAS SEEN TO FAIL AGAINST
----------------------------------
With `generator/partials.py` and `templates/partials/` moved out of the tree,
the whole file fails at collection: `ImportError: cannot import name 'partials'
from 'generator'`. That is the defect stated as plainly as it can be -- there
was no mechanism to test.

The two checksum guards were also checked against a real edit rather than only
against a monkeypatched digest: appending one comment line to
`templates/partials/transit_spine.html` makes `load()` raise "Partial checksum
mismatch for templates/partials/transit_spine.html", and restoring the file
makes it load again.
"""

from __future__ import annotations

import pytest

from generator import partials


# --------------------------------------------------------------------------
# The mechanism exists and is addressable by name
# --------------------------------------------------------------------------

def test_there_is_a_partials_directory_with_at_least_one_partial():
    found = partials.partial_paths()
    assert found, (
        "templates/partials/ holds no partials. The directory existing is the "
        "whole point: a component more than one consumer draws needs a file to "
        "include."
    )


def test_a_partial_loads_as_named_blocks():
    blocks = partials.load("transit_spine")
    assert "styles" in blocks
    assert "segment" in blocks
    assert blocks["segment"].strip(), "the segment block loaded empty"


def test_a_block_can_be_fetched_on_its_own():
    assert partials.block("transit_spine", "styles") == partials.load("transit_spine")["styles"]


def test_asking_for_a_block_that_is_not_there_names_the_ones_that_are():
    with pytest.raises(KeyError) as exc:
        partials.block("transit_spine", "no_such_block")
    message = str(exc.value)
    assert "no_such_block" in message
    assert "segment" in message, (
        "the error should list the blocks the partial does declare -- a bare "
        "KeyError sends the reader to the file to find out"
    )


def test_a_missing_partial_raises_rather_than_rendering_nothing():
    """A component that silently renders nothing is the failure the whole
    mechanism exists to avoid, and it is invisible in a built page."""
    with pytest.raises(FileNotFoundError):
        partials.load("there_is_no_such_partial")


def test_substitution_needs_no_template_language():
    """Placeholders are `__NAME__`, filled with str.replace.

    Pinned because it is a compatibility promise, not a style preference: a
    consumer of this engine written in another language has to be able to draw
    this component, and a Jinja dependency in the partial would stop it.
    """
    segment = partials.block("transit_spine", "segment")
    assert "__MODE__" in segment
    assert "{{" not in segment and "{%" not in segment


# --------------------------------------------------------------------------
# Verified like the main template
# --------------------------------------------------------------------------

def test_every_partial_has_a_recorded_checksum():
    recorded = partials.recorded_checksums()
    missing = [
        path.name for path in partials.partial_paths()
        if f"templates/partials/{path.name}" not in recorded
    ]
    assert not missing, (
        f"partials with no row in templates/checksums.txt: {missing}. Run "
        "python scripts/regenerate_template_checksums.py"
    )


def test_the_recorded_checksum_matches_the_partial_on_disk():
    for path in partials.partial_paths():
        row = f"templates/partials/{path.name}"
        assert partials.recorded_checksums()[row] == partials.sha256_of(path), (
            f"{row} has changed since its checksum was recorded. Run "
            "python scripts/regenerate_template_checksums.py"
        )


def test_an_edited_partial_fails_until_its_checksum_is_regenerated(monkeypatch):
    """The guard is the reason a partial can be trusted by a second consumer."""
    row = "templates/partials/transit_spine.html"
    real = partials.recorded_checksums()
    stale = dict(real, **{row: "0" * 64})
    monkeypatch.setattr(partials, "recorded_checksums", lambda: stale)

    with pytest.raises(RuntimeError) as exc:
        partials.load("transit_spine")
    message = str(exc.value)
    assert "mismatch" in message.lower()
    assert "regenerate" in message.lower(), (
        "the message must point at the script -- the alternative is pasting "
        "the digest it printed, which always 'works' and checks nothing"
    )


def test_a_partial_with_no_recorded_checksum_is_refused(monkeypatch):
    without = {
        row: digest for row, digest in partials.recorded_checksums().items()
        if not row.startswith("templates/partials/")
    }
    monkeypatch.setattr(partials, "recorded_checksums", lambda: without)
    with pytest.raises(RuntimeError) as exc:
        partials.load("transit_spine")
    assert "no row" in str(exc.value)


# --------------------------------------------------------------------------
# The main template's own check, which now shares the file
# --------------------------------------------------------------------------

def test_the_checksum_file_is_read_by_path_not_by_position():
    """`checksums.txt` used to be read as `text.split()[0]`.

    That returns the first line's digest, which was the main template's only
    because it happened to be on the first line. Now that the file carries a
    row per partial, order must not mean anything -- so this reverses the rows
    and asserts the main template's digest is still found.
    """
    rows = partials.recorded_checksums()
    assert partials.MAIN_TEMPLATE_ROW in rows
    assert len(rows) > 1, "no partial rows, so this test is not testing anything"

    reversed_text = "".join(
        f"{digest}  {row}\n" for row, digest in reversed(list(rows.items()))
    )
    monkey = partials.CHECKSUM_PATH.read_text(encoding="utf-8")
    try:
        partials.CHECKSUM_PATH.write_text(reversed_text, encoding="utf-8")
        assert partials.recorded_checksums()[partials.MAIN_TEMPLATE_ROW] == (
            rows[partials.MAIN_TEMPLATE_ROW]
        )
        # And the real consumer, through the real call path.
        from generator.html_assembler import _verify_checksum
        template = (partials.TEMPLATES_DIR / "v2.5_template.html").read_text(
            encoding="utf-8"
        )
        _verify_checksum(template)
    finally:
        partials.CHECKSUM_PATH.write_text(monkey, encoding="utf-8")


# --------------------------------------------------------------------------
# What a second consumer may call
# --------------------------------------------------------------------------
#
# The partial exists so a second consumer can draw the same MARKUP. A consumer
# that draws its own markup for the same journey still wants the same
# arithmetic and the same wording, and reaches into this module for them --
# which is fine, and is the cheaper half of sharing a component.
#
# What is not fine is reaching past the public surface to do it. A name with a
# leading underscore says "this may be renamed without notice", and renaming it
# breaks a caller no test here knows about: nothing in this repository fails,
# and the break surfaces on whatever page that consumer renders. These pin the
# surface so the rename happens here, loudly, instead of there, silently.

#: Everything outside this repository is expected to call. Adding to this list
#: is a promise; it is not a list to grow casually.
PUBLIC_SURFACE = (
    "render_segment",
    "styles",
    "rail_length_px",
    "duration_minutes",
    "nights_in_transit",
    "overnight_sentence",
    "en_route_lines",
)


@pytest.mark.parametrize("name", PUBLIC_SURFACE)
def test_the_spine_publishes_what_another_consumer_calls(name):
    from generator import transit_spine

    assert hasattr(transit_spine, name), (
        f"transit_spine.{name} is published surface and is missing"
    )
    assert not name.startswith("_"), (
        f"{name} is in PUBLIC_SURFACE but spelled private. A leading "
        "underscore tells a consumer the name may move without notice, which "
        "is the opposite of what this list says about it."
    )


def test_the_overnight_wording_is_the_shared_contract():
    """The exact string, not just the function's existence.

    Two surfaces render this same fact, and they share the function precisely
    so they cannot word it differently. That makes the wording the contract:
    changing it changes what a reader is told in more than one place, and this
    test is where that gets noticed.
    """
    from generator import transit_spine

    assert transit_spine.overnight_sentence(1) == (
        "1 night spent in transit — no lodging needed at either end"
    )
    assert transit_spine.overnight_sentence(3) == (
        "3 nights spent in transit — no lodging needed at either end"
    )
    assert transit_spine.overnight_sentence(0) == ""


def test_the_name_this_was_published_under_before_still_resolves():
    """`_overnight_sentence` was the only name until 2026-10-23.

    A consumer was already calling it under that name, so renaming it without
    a shim would have broken that consumer on the merge that delivered the
    fix. The shim is why this change is safe to take in either order.
    """
    from generator import transit_spine

    assert transit_spine._overnight_sentence is transit_spine.overnight_sentence
