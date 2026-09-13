"""`destination.stretch_note`: the author's judgement about the leg arriving here.

A manifest could already say *which* trail section a leg follows, and nothing
about what that stretch is like. An author who knew that half a route is
traffic-free rail-trail and half on-road connector -- and that the connectors
are where the day gets long -- could write it only as a YAML comment, which
the parser discards, so the published guide never said it.

The key is optional free text, rendered verbatim and escaped on the leg's
getting-here card. Without it the card must render exactly as it did before.
"""

from __future__ import annotations

import pytest

from generator.html_assembler import HTMLAssembler
from generator.parser import ManifestParser


GREENWAY_NOTE = (
    "Half traffic-free rail-trail, half on-road connector; "
    "the connectors are where the day gets long."
)


def _manifest_yaml(bryce_extra: str = "") -> str:
    return f"""
trip:
  title: "Test"
  subtitle: "Test"
  theme_color: "#123456"
destinations:
  - id: zion
    name: "Zion National Park"
    dates: "Jan 1-3, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com"
  - id: bryce_canyon
    name: "Bryce Canyon National Park"
    dates: "Jan 3-5, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com"
{bryce_extra}"""


def _load(tmp_path, content: str):
    f = tmp_path / "manifest.yaml"
    f.write_text(content, encoding="utf-8")
    return ManifestParser().load(str(f))


# --- schema ---------------------------------------------------------------


def test_the_parser_carries_a_stretch_note_through(tmp_path):
    data = _load(tmp_path, _manifest_yaml(f'    stretch_note: "{GREENWAY_NOTE}"\n'))
    assert data["destinations"][1]["stretch_note"] == GREENWAY_NOTE


def test_omitting_the_stretch_note_leaves_the_destination_untouched(tmp_path):
    data = _load(tmp_path, _manifest_yaml())
    assert all("stretch_note" not in d for d in data["destinations"])


@pytest.mark.parametrize("value", [
    "42",                 # a number, not a sentence
    "[on-road, long]",    # a list
    '""',                 # empty
    '"   "',              # blank: says nothing, would render an empty row
    "'" + "x" * 501 + "'",  # an essay, not a note on a card
])
def test_the_parser_refuses_a_stretch_note_that_is_not_a_note(tmp_path, value):
    with pytest.raises(ValueError) as exc_info:
        _load(tmp_path, _manifest_yaml(f"    stretch_note: {value}\n"))
    assert "stretch_note" in str(exc_info.value)


# --- rendering ------------------------------------------------------------


def _assembler() -> HTMLAssembler:
    return HTMLAssembler.__new__(HTMLAssembler)


def _ai() -> dict:
    return {"getting_here": {
        "route_summary": "Along the coast.",
        "travel_time": "about 5 hrs",
        "distance_miles": "28",
        "trail_url": "https://www.alltrails.com/trail/us/maine/eastern-trail",
        "trail_label": "Eastern Trail",
    }}


def test_the_stretch_note_renders_on_the_leg_card():
    dest = {"name": "Kennebunk, Maine", "_transport_mode": "bike",
            "stretch_note": GREENWAY_NOTE}

    html = _assembler()._build_getting_here(_ai(), dest, previous_name="Portland, Maine")

    assert "the connectors are where the day gets long." in html
    # It describes the leg, so it sits with the leg: after the route summary,
    # before the trail link and the stops.
    assert html.index("Along the coast.") < html.index("where the day gets long")
    assert html.index("where the day gets long") < html.index("leg-trail-link")


def test_without_a_stretch_note_the_card_is_byte_identical():
    """Adding the note adds exactly one row and changes nothing else -- so the
    card without it is the card as it rendered before the key existed."""
    assembler = _assembler()
    bare = {"name": "Kennebunk, Maine", "_transport_mode": "bike"}
    noted = dict(bare, stretch_note=GREENWAY_NOTE)

    without = assembler._build_getting_here(_ai(), bare, previous_name="Portland, Maine")
    with_note = assembler._build_getting_here(_ai(), noted, previous_name="Portland, Maine")

    assert "leg-stretch-note" not in without
    row = assembler._leg_stretch_note_html(noted)
    assert row and row in with_note
    assert with_note.replace(row, "", 1) == without


@pytest.mark.parametrize("note", ["", "   ", None, 42, ["on-road"]])
def test_a_note_that_says_nothing_renders_nothing(note):
    """Only reachable for a trip dict that bypassed the parser, but the card
    must not grow an empty paragraph or a stringified list."""
    dest = {"name": "B", "stretch_note": note}
    bare = {"name": "B"}

    html = _assembler()._build_getting_here(_ai(), dest, previous_name="A")

    assert "leg-stretch-note" not in html
    assert html == _assembler()._build_getting_here(_ai(), bare, previous_name="A")


def test_the_stretch_note_is_escaped():
    dest = {"name": "B", "stretch_note": '<script>alert(1)</script> & "busy" road'}

    html = _assembler()._build_getting_here(_ai(), dest, previous_name="A")

    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;busy&quot; road" in html


def test_a_leg_with_only_a_note_still_gets_a_card():
    """The card is dropped when it has nothing to describe. An author's note
    is something to describe, so a leg with no figures still shows it."""
    ai = {"getting_here": {"route_summary": "", "travel_time": "", "distance_miles": ""}}
    dest = {"name": "Kennebunk, Maine", "stretch_note": GREENWAY_NOTE}

    html = _assembler()._build_getting_here(ai, dest, previous_name="")

    assert "where the day gets long" in html
