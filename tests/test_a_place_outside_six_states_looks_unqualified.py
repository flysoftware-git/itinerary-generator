"""A place name is qualified by its state, and there were only six states.

WHY
---
Two modules asked *does this string already say where it is?* and both answered
with the same hand-written six: Utah, Colorado, Arizona, New Mexico, Nevada,
California. Those were the trips the engine was first exercised on. A
single-token place in any of the other forty-four states read as **unqualified**
-- and nothing reported that it had, because the caller simply took the other
branch.

The literal was also duplicated, so the two copies were free to drift: the
`url_discovery` copy carried `downtown`, `historic district` and `visitor
center` that the assembler's copy never had, and no test compared them.

WHAT IS PINNED
--------------
That the state cues are shared rather than copied, and that a state outside the
original six is recognised. Not the exact membership of either caller's extra
cues -- they legitimately differ, and pinning them would turn a deliberate
difference into a failure.
"""

import pytest

from generator.html_assembler import HTMLAssembler
from generator.place_cues import COMMON_PLACE_CUES, US_STATES
from generator.url_discovery import LOCATION_CUE_TERMS


@pytest.mark.parametrize(
    "state",
    ["washington", "maine", "florida", "michigan", "alaska", "new york"],
    ids=lambda s: s.replace(" ", "-"),
)
def test_a_state_outside_the_original_six_is_a_location_cue(state):
    """The defect, stated as the states it used to exclude.

    Each of these is a real state that named a real place in a real trip and
    was read as saying nothing about where it was.
    """
    assert state in US_STATES
    assert state in LOCATION_CUE_TERMS, (
        f"{state!r} is not a location cue, so a place named only by it reads "
        "as unqualified"
    )


def test_all_fifty_states_and_dc_are_present():
    """A partial list is the defect; a short one is how it started."""
    assert len(US_STATES) == 51, f"{len(US_STATES)} entries, expected 50 + DC"
    for original in ("utah", "colorado", "arizona", "new mexico", "nevada", "california"):
        assert original in US_STATES, "the original six must still be cues"


def test_the_two_callers_share_the_states_rather_than_copying_them():
    """The duplication was half the defect: two lists, free to drift.

    Asserted on the source rather than on behaviour, because two identical
    hand-written copies behave identically right up until someone edits one.
    """
    import inspect

    from generator import html_assembler, url_discovery

    for module in (html_assembler, url_discovery):
        src = inspect.getsource(module)
        assert "US_STATES" in src, f"{module.__name__} does not use the shared states"
        # The copied *cue* run specifically, not any mention of a state.
        # `url_discovery` has other lists where a state name legitimately means
        # "too generic a token to distinguish a name" -- a different question
        # with a different right answer, and not this one's to police.
        copied_run = '"utah",\n                "colorado",'
        assert copied_run not in src.replace("\r\n", "\n"), (
            f"{module.__name__} still carries the hand-written six-state cue "
            "list, which is the copy that drifts"
        )


def test_the_shared_cues_are_shared_and_the_local_ones_are_not():
    """The split is the design: every caller means the same by a state name,
    and they may legitimately differ about `downtown`."""
    for cue in COMMON_PLACE_CUES:
        assert cue in LOCATION_CUE_TERMS
    # url_discovery's own additions stay its own.
    assert "visitor center" in LOCATION_CUE_TERMS
    assert "visitor center" not in COMMON_PLACE_CUES


def test_the_assembler_reads_a_single_token_state_as_qualified():
    """End to end on the assembler's own predicate, which is what callers use."""
    looks_qualified = getattr(HTMLAssembler, "_looks_location_qualified", None)
    if looks_qualified is None:
        pytest.skip("predicate is not exposed on the class in this revision")
    assert looks_qualified("Olympic National Park Washington")
    assert looks_qualified("Moab, Utah")
