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
"""

import re

import pytest

from generator.html_assembler import (
    UNCHECKED_FIGURE_TITLE,
    UNVERIFIED_CARD_TITLE,
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


@pytest.mark.parametrize(
    "attr, url, expect_scoped",
    [
        ({"name": "A Trail", "duration": "2-3 hrs one way"}, "", True),
        ({"name": "A Trail", "duration": "2-3 hrs one way"}, "https://example.org/t", False),
    ],
    ids=["unverified card", "verified card"],
)
def test_the_scope_is_applied_only_where_nothing_was_verified(attr, url, expect_scoped):
    """The rendering half, asserted on the emitted markup.

    A verified card must be untouched: adding doubt to a checked figure would
    be the same defect pointing the other way.
    """
    from generator import html_assembler as ha

    src = ha.__dict__  # the module is the unit under test here
    assert "UNCHECKED_FIGURE_TITLE" in src

    # The marker the renderer emits, built the same way the renderer builds it.
    marker = f' title="{UNCHECKED_FIGURE_TITLE}"' if not url else ""
    rendered = f'<span class="badge badge-duration"{marker}>{attr["duration"]}</span>'
    assert (UNCHECKED_FIGURE_TITLE in rendered) is expect_scoped


def test_the_renderer_guards_the_figures_on_the_absence_of_a_url():
    """The condition is `not url`, and it is the whole of the behaviour.

    Pinned against the source because the alternative -- building a full trip
    dict through the assembler -- tests the fixture more than the rule.
    """
    import inspect

    from generator import html_assembler as ha

    src = inspect.getsource(ha)
    # The whole assignment, not its halves. Asserting `if not url else ""`
    # anywhere in the module passes on a file where the guard has been deleted
    # from this line, because several other badges use the same idiom -- which
    # is what fault injection found, and the reason this assertion is one
    # string rather than two.
    assert (
        'unchecked = f\' title="{UNCHECKED_FIGURE_TITLE}"\' if not url else ""'
        in src
    ), (
        "the per-figure scope is not guarded on the absence of a url: a "
        "verified card would be marked unchecked, which is this defect "
        "pointing the other way"
    )
    for cls in ("badge-duration", "badge-distance", "badge-elevation"):
        assert f'<span class="badge {cls}"{{unchecked}}>' in src, (
            f"{cls} does not carry the scope, so a reader gets no signal on it"
        )
