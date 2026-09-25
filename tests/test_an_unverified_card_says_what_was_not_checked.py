"""An Unverified badge must not read as a statement about the link alone.

WHY
---
An attraction rendered without a verified source URL gets `⚠ Unverified`. Until
this change its title read *"No verified source link found for this
recommendation"* -- true, and scoped to the href. Beside it, on the same card,
the model's own duration, difficulty, distance, elevation and rating rendered as
ordinary badges with nothing said about them at all.

A reader takes the badge to cover the card. It covered the link. So the one
element announcing doubt was also the one drawing attention away from the
figures that deserved it: a duration nobody had checked was read as a fact
*because* a warning sat beside it saying something else was the problem. That is
how a trail description reading "2-3 hours one way" is trusted on a card whose
whole point is that nothing about it was confirmed.

WHAT IS PINNED
--------------
That the scope is stated in both places -- on the badge, and on each figure --
and that a verified card is left completely alone. The wording is not pinned
beyond the word "checked": a test asserting a sentence would fail on every
rewording and would be edited to green rather than read.

Asserted through the real builders, for all three card kinds that can reach a
page unlinked. The first version of this file checked the attraction path only,
by rebuilding the renderer's f-string inside the test and by grepping the
renderer's source -- and the two paths it did not render, en-route stops and
restaurants, were the two where the widened badge claimed the figures beside it
were unchecked while every figure on the card said nothing.
"""

import re

import pytest

from generator.html_assembler import (
    UNCHECKED_FIGURE_TITLE,
    UNVERIFIED_CARD_TITLE,
    HTMLAssembler,
)


def test_the_badge_title_covers_the_figures_and_not_only_the_link():
    """The defect was a true sentence that named too little.

    "No verified source link found" is accurate and says nothing about the
    duration printed next to it, which is the number a reader acts on.
    """
    lowered = UNVERIFIED_CARD_TITLE.lower()
    assert "link" in lowered, "the missing link is still the primary fact"
    assert "figures" in lowered or "checked" in lowered, (
        "the badge names only the link, which is the defect: "
        f"{UNVERIFIED_CARD_TITLE!r}"
    )


def test_each_figure_states_its_own_status():
    """A badge two positions away is not where a reader looks for provenance.

    The card carries several independent claims and one warning; without this
    the warning is attached to the wrong one.
    """
    assert "checked" in UNCHECKED_FIGURE_TITLE.lower()


CARD_FIGURE_CLASSES = {
    "attraction": ("badge-rating", "badge-hike-easy", "badge-duration", "badge-distance"),
    "en_route_stop": ("badge-rating",),
    "restaurant": ("badge-rating", "badge-price", "cuisine-badge"),
}


def _render(kind: str, *, url: str) -> str:
    """One card of `kind`, through the real builder, with and without a link.

    Built through the assembler rather than assembled in the test. An earlier
    version of this file constructed the expected markup with the same f-string
    the renderer uses and asserted on its own output, which passes whatever
    html_assembler does -- and a companion test pinned the renderer's source
    text, which breaks on renaming a local and still says nothing about what
    the page shows. The repo's own
    test_build_attractions_seed_no_url_attraction_renders_caution_badge already
    calls the builder; this follows it.
    """
    assembler = HTMLAssembler.__new__(HTMLAssembler)
    if kind == "attraction":
        item = {
            "name": "Rail Trail", "difficulty": "Easy", "duration": "2-3 hrs one way",
            "distance_miles": 130, "rating": 4.6,
            "description": "A long multi-segment rail trail.", "is_seed": True,
        }
        if url:
            item["url"] = url
        return assembler._build_attractions(
            {"top_attractions": [item]}, drives=[], dest_name="Somewhere"
        )
    if kind == "en_route_stop":
        stop = {"name": "Adobe Plaza", "rating": 4.4, "description": "A plaza.", "is_seed": True}
        if url:
            stop["url"] = url
        return assembler._build_getting_here(
            {"getting_here": {"en_route_stops": [stop]}},
            {"name": "Santa Fe"},
            previous_name="Albuquerque",
        )
    rest = {
        "name": "The Grill", "price_range": "$$", "rating": 4.5,
        "cuisine": "American", "description": "A grill.", "is_seed": True,
    }
    if url:
        rest["url"] = url
    return assembler._build_restaurants({"dinner_recommendations": [rest]}, dest_name="Moab")


def _badges(html: str) -> list[str]:
    return re.findall(r'<span class="badge[^>]*>[^<]*</span>', html)


@pytest.mark.parametrize("kind", sorted(CARD_FIGURE_CLASSES))
def test_every_figure_on_an_unverified_card_states_its_own_status(kind: str) -> None:
    """Asserted on what the builder emits, for all three card kinds.

    The en-route stop is the case that made this worth parametrizing: its only
    figure is the rating, so a card whose badge says the figures were not
    checked had nothing at all saying so. The restaurant card had three such
    figures. Both were missed when only the attraction path was changed.
    """
    html = _render(kind, url="")
    assert UNVERIFIED_CARD_TITLE in html, "the card under test is not the unverified one"
    for css_class in CARD_FIGURE_CLASSES[kind]:
        badge = next((b for b in _badges(html) if f'"badge {css_class}"' in b), None)
        assert badge is not None, f"{kind} rendered no {css_class} badge to check"
        assert UNCHECKED_FIGURE_TITLE in badge, (
            f"{kind}'s {css_class} carries no scope, so a reader gets no signal on it "
            f"while the badge beside it says the figures were not checked: {badge}"
        )


@pytest.mark.parametrize("kind", sorted(CARD_FIGURE_CLASSES))
def test_a_verified_card_is_left_completely_alone(kind: str) -> None:
    """Marking a checked figure unchecked is the same defect pointing the other way."""
    html = _render(kind, url="https://example.org/a-real-page")
    assert UNCHECKED_FIGURE_TITLE not in html
    assert UNVERIFIED_CARD_TITLE not in html


@pytest.mark.parametrize("kind", sorted(CARD_FIGURE_CLASSES))
def test_the_figures_themselves_still_render(kind: str) -> None:
    """The scope is added to the figures, not in place of them.

    _clear_unanchored_first_leg removes arrival badges that are known wrong.
    These are unchecked, which is a different claim, and the whole point is
    that the number stays and says what it is.
    """
    html = _render(kind, url="")
    for figure in {"attraction": ("4.6", "2-3 hrs one way", "130"),
                   "en_route_stop": ("4.4",),
                   "restaurant": ("4.5", "$$", "American")}[kind]:
        assert figure in html, f"{kind} lost {figure!r} instead of qualifying it"


@pytest.mark.parametrize("kind", sorted(CARD_FIGURE_CLASSES))
def test_the_scope_is_an_attribute_and_not_a_class(kind: str) -> None:
    """templates/ keeps its checksum, so the scope must not restyle anything.

    The badge classes are what the stylesheet matches on; a new class here
    would mean a template change and a checksum bump.
    """
    unverified = _render(kind, url="")
    verified = _render(kind, url="https://example.org/a-real-page")
    strip = lambda html: html.replace(f' title="{UNCHECKED_FIGURE_TITLE}"', "")  # noqa: E731
    for css_class in CARD_FIGURE_CLASSES[kind]:
        assert f'"badge {css_class}"' in strip(unverified), (
            f"{css_class} lost or changed its class on the unverified card"
        )
        assert f'"badge {css_class}"' in verified
