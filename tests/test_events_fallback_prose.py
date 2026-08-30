"""A populated ambient_scene must not be discarded for boilerplate.

prompts/cultural_events.txt defines two shapes: Format A (has_events true,
events[], ambient_scene) and Format B (has_events false, honest_assessment).
The model returns hybrids -- Format B's flag with Format A's prose field.

The no-events branch read only honest_assessment, so those destinations
printed a generic "check visitor center and local calendars" sentence while
carrying real prose about the place. Eight destinations across the three trips
on 2026-08-30, each logging a warning about a missing field while holding a
usable one. The content was generated and billed either way.
"""

import logging

import pytest

from generator.html_assembler import HTMLAssembler

AMBIENT = "St. George has a vibrant community with local markets and concerts through October."
HONEST = "Few ticketed events run here in October; the visitor center programs are the draw."
BOILERPLATE = "No ticketed events were confidently verified"


def _build(events):
    return HTMLAssembler._build_events(
        HTMLAssembler.__new__(HTMLAssembler), events, "St. George, Utah"
    )


def test_honest_assessment_is_preferred_when_present():
    html = _build({"has_events": False, "events": [], "honest_assessment": HONEST, "ambient_scene": AMBIENT})
    assert HONEST in html
    assert AMBIENT not in html


def test_ambient_scene_is_used_when_honest_assessment_is_absent():
    html = _build({"has_events": False, "events": [], "ambient_scene": AMBIENT})
    assert AMBIENT in html
    assert BOILERPLATE not in html


def test_boilerplate_only_when_neither_is_present():
    html = _build({"has_events": False, "events": []})
    assert BOILERPLATE in html


def test_a_warning_is_still_logged_for_a_real_gap(caplog):
    with caplog.at_level(logging.WARNING):
        _build({"has_events": False, "events": []})
    assert "No honest_assessment or ambient_scene" in caplog.text


def test_no_warning_when_ambient_scene_carries_it(caplog):
    """The warning was firing on destinations that had usable prose."""
    with caplog.at_level(logging.WARNING):
        _build({"has_events": False, "events": [], "ambient_scene": AMBIENT})
    assert "honest_assessment" not in caplog.text


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_blank_fields_fall_through(blank):
    html = _build({"has_events": False, "events": [], "honest_assessment": blank, "ambient_scene": AMBIENT})
    assert AMBIENT in html
