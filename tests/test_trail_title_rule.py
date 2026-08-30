"""AllTrails-first applies to items that call themselves trails.

The trail_like flag is inferred from an item's description, so a rock
formation whose blurb mentions a short walk was held to the AllTrails-only
rule and had its correct nps.gov page stripped. Balanced Rock and Upheaval
Dome -- a formation and a crater -- were both classed entity_class=trail and
removed for having no verified URL, after search had found
nps.gov/arch/planyourvisit/balancedrock.htm.

Owner rule (2026-08-29): when a trail-type target has both available, prefer
AllTrails if the title contains "trail", otherwise prefer NPS. AllTrails-first
itself stays -- it exists because non-AllTrails trail URLs were being
generated badly.
"""

import re

import pytest

from generator.url_discovery import TRAIL_NAME_PATTERN, URLDiscoverer


@pytest.mark.parametrize("name", [
    "Navajo Loop Trail",
    "Queen's Garden Trail",
    "Delicate Arch Trail",
    "Riverside Walk Trails",
    "trail of the ancients",
])
def test_names_that_claim_a_trail(name):
    assert URLDiscoverer._title_claims_a_trail(name) is True


@pytest.mark.parametrize("name", [
    "Balanced Rock",
    "Upheaval Dome",
    "Windows Section",
    "Chimney Rock",
    "Bryce Point",
])
def test_names_that_do_not(name):
    assert URLDiscoverer._title_claims_a_trail(name) is False


def test_trailview_overlook_is_not_a_trail():
    """Word boundary, not substring.

    Read literally the rule sends "Trailview Overlook" to AllTrails because
    those five letters appear in it. It is an overlook.
    """
    assert URLDiscoverer._title_claims_a_trail("Trailview Overlook") is False


@pytest.mark.parametrize("name", ["", None, "   "])
def test_empty_input_is_safe(name):
    assert URLDiscoverer._title_claims_a_trail(name) is False


def test_the_pattern_is_a_word_boundary_not_a_control_character():
    """Guards the exact defect that shipped in this function's first version.

    Written through a patch script, the \b escape became a backspace (0x08),
    producing a pattern that matched nothing -- including real trails -- while
    the source still read as a correct regex. Assert the compiled behaviour,
    not the spelling.
    """
    assert "\x08" not in TRAIL_NAME_PATTERN
    assert re.search(TRAIL_NAME_PATTERN, "navajo loop trail")
    assert not re.search(TRAIL_NAME_PATTERN, "trailview overlook")
