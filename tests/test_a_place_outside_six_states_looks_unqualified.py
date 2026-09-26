"""A place name is qualified by its state, and there were only six states.

WHY
---
Two modules asked *does this string already say where it is?* and both answered
with the same hand-written six: Utah, Colorado, Arizona, New Mexico, Nevada,
California. Those were the trips the engine was first exercised on. A
single-token place in any of the other forty-four states read as **unqualified**
-- and nothing reported that it had, because the caller simply took the other
branch.

The literal was also duplicated -- three times, not twice. Two of the copies
were live: `html_assembler._looks_location_qualified`, and
`url_discovery._looks_location_qualified`'s own `strong_location_terms`, which
is the one this module's callers actually reach. The third,
`url_discovery.LOCATION_CUE_TERMS`, looked like the rule and was read by
nothing in production -- so a fix that shared the states there and left
`strong_location_terms` alone made the two modules DISAGREE for forty-four
states, which is worse than the duplication it set out to remove. The decoy
constant is gone.

WHAT IS PINNED
--------------
That both live predicates give the same answer for a state-qualified name, and
that a state outside the original six is recognised. Asserted by calling them,
not by grepping their source: the first version of this file looked for the
literal `'"utah",
                "colorado",'` -- sixteen spaces of
indentation, while the live copy in `strong_location_terms` is indented twelve
-- so the assertion passed over the very copy it was written to catch.

Not pinned: the exact membership of either caller's extra cues. They
legitimately differ (`downtown` is url_discovery's business, `junction` too),
and pinning them would turn a deliberate difference into a failure.
"""

import pytest

from generator.html_assembler import HTMLAssembler
from generator.place_cues import COMMON_PLACE_CUES, US_STATES
from generator.url_discovery import URLDiscoverer

#: Both live predicates, so every behavioural assertion runs against each.
PREDICATES = {
    "html_assembler": HTMLAssembler._looks_location_qualified,
    "url_discovery": URLDiscoverer._looks_location_qualified,
}

#: Real places in states the original six excluded. Single-token state name,
#: no comma, no "national park" -- nothing else for the predicate to catch.
OUTSIDE_THE_ORIGINAL_SIX = [
    "Olympia Washington",
    "Bend Oregon",
    "Austin Texas",
    "Burlington Vermont",
    "Traverse City Michigan",
    "Portland Maine",
]


@pytest.mark.parametrize("module", sorted(PREDICATES))
@pytest.mark.parametrize("name", OUTSIDE_THE_ORIGINAL_SIX)
def test_a_state_outside_the_original_six_reads_as_qualified(module, name):
    """The defect, stated as behaviour, in both places it lived.

    Each of these is a place named by a state that used to say nothing about
    where it was. Deliberately free of "national park" and of a comma: an
    earlier assertion here used "Olympic National Park Washington", which
    passes on the six-state list because of the two words before the state.
    """
    assert PREDICATES[module](name), (
        f"{module} reads {name!r} as unqualified, so a place named only by its "
        "state gets context it does not need or no link at all"
    )


@pytest.mark.parametrize(
    "name",
    OUTSIDE_THE_ORIGINAL_SIX + ["Moab Utah", "Moab, Utah", "Temple View", "Saint George"],
)
def test_the_two_predicates_agree(name):
    """The duplication was half the defect, and this is what it cost.

    Asserted by calling both, which is the only form of this assertion that
    could have failed on the state of the tree it was written against: sharing
    the constant in one module and not the other left these two answering
    differently for forty-four states.
    """
    answers = {module: predicate(name) for module, predicate in PREDICATES.items()}
    assert len(set(answers.values())) == 1, f"{name!r}: {answers}"


def test_all_fifty_states_and_dc_are_present():
    """A partial list is the defect; a short one is how it started."""
    assert len(US_STATES) == 51, f"{len(US_STATES)} entries, expected 50 + DC"
    for original in ("utah", "colorado", "arizona", "new mexico", "nevada", "california"):
        assert original in US_STATES, "the original six must still be cues"


@pytest.mark.parametrize("module", sorted(PREDICATES))
def test_every_state_is_a_cue_in_both_predicates(module):
    """Fifty-one assertions per predicate, rather than a spot check.

    The defect was a list that was right for the states somebody happened to
    test with, so the test enumerates them all.
    """
    predicate = PREDICATES[module]
    missing = sorted(state for state in US_STATES if not predicate(f"Somewhere {state}"))
    assert not missing, f"{module} does not recognise: {missing}"


@pytest.mark.parametrize("module", sorted(PREDICATES))
def test_the_shared_cues_reach_both_predicates(module):
    for cue in COMMON_PLACE_CUES:
        assert PREDICATES[module](f"Somewhere {cue}")


def test_the_local_cues_stay_local():
    """The split is the design: every caller means the same by a state name,
    and they may legitimately differ about `downtown`."""
    assert "visitor center" not in COMMON_PLACE_CUES
    assert URLDiscoverer._looks_location_qualified("The Junction")
    assert not HTMLAssembler._looks_location_qualified("The Junction")


def test_a_name_with_no_location_cue_still_reads_as_unqualified():
    """The predicate has to keep saying no, or it stops meaning anything."""
    for module, predicate in PREDICATES.items():
        assert not predicate("Temple View"), module
        assert not predicate("Quiet Overlook"), module


def test_the_decoy_constant_is_gone():
    """LOCATION_CUE_TERMS read like the rule and was read by nothing.

    Keeping it would mean keeping a second list in sync with the predicate
    forever, and it is what the first fix updated instead of the live copy.
    """
    import generator.url_discovery as ud

    assert not hasattr(ud, "LOCATION_CUE_TERMS"), (
        "a module-level cue list that no caller reads is the trap this fix "
        "fell into; the predicate is the only place the rule should live"
    )


#: Names that contain a state and say nothing about where they are. Every one
#: read as location-qualified when the fifty states were matched as plain
#: substrings, which is how "Georgia O'Keeffe Museum" lost the "Santa Fe" its
#: maps query needed -- caught by an existing test, not by this file.
A_STATE_INSIDE_A_NAME = [
    "Georgia O'Keeffe Museum",
    "Texas Roadhouse",
    "Washington Monument",
    "Indiana Dunes Visitor Center",
    "Virginia's Diner",
    "Montana Ave Books",
    "Ohio Street Overlook",
]


@pytest.mark.parametrize("module", sorted(PREDICATES))
@pytest.mark.parametrize("name", A_STATE_INSIDE_A_NAME)
def test_a_state_inside_a_name_does_not_qualify_it(module, name):
    """The cost of widening six states to fifty, and the reason for the anchor.

    A person called Georgia and a steakhouse called Texas are not places. The
    six original states were nearly free of this because few things are named
    Utah or Nevada; forty-four more are not, and the predicate guards a query
    that then goes out with no city in it.
    """
    assert not PREDICATES[module](name), (
        f"{module} reads {name!r} as already located, so its search loses the "
        "destination it needed"
    )


@pytest.mark.parametrize("module", sorted(PREDICATES))
def test_a_trailing_state_still_qualifies_after_a_comma_or_a_space(module):
    """Both separators, since a manifest writes either."""
    assert PREDICATES[module]("Bend Oregon")
    assert PREDICATES[module]("Bend, Oregon")
