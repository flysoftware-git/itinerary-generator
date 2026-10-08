r"""A booked taxi is its own kind of transportation, not a car the traveler drives.

Most airport and port transfers are made by taxi, minicab or a ride-hailing
trip. The schema had no word for one, so such a leg had to be entered as `car` --
which renders as though the traveler were driving it themselves, with the
driving icon and the driving category -- or as `other`, which renders a generic
*Travel* chip and loses the one detail that makes the leg legible.

`taxi` is the word, and it sits beside `shuttle` rather than replacing it: a
shuttle is a shared service on a fixed route, a taxi is a door-to-door ride for
this traveler.

**Deliberately NOT one of `transit_estimate.TRANSIT_MODES`**, and that is the
load-bearing half of this change. Those are scheduled services an estimator can
look a timetable up for; a taxi has no timetable to find, so adding it there
would send the estimator looking for services that do not exist. The same
applies to `transit_routing`'s booked-mode set. A booked taxi is a leg whose
details the traveler already holds, exactly like a booked `car`.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from generator.manifest_parser import TRANSPORTATION_ITEM_SCHEMA  # noqa: E402


def _enum() -> list[str]:
    return TRANSPORTATION_ITEM_SCHEMA["properties"]["type"]["enum"]


def test_a_manifest_may_say_a_leg_is_a_taxi():
    """The change itself. Red before it: a transfer had to claim to be a `car`."""
    assert "taxi" in _enum(), (
        f"a manifest cannot say a leg is a taxi; it must choose between `car`, "
        f"which draws as self-driving, and `other`, which draws as Travel: "
        f"{_enum()}")


def test_other_is_still_the_last_resort():
    """`other` keeps its place as the fallback, so an unrecognized booking still
    renders with its details intact. Red if adding a kind displaced it."""
    assert _enum()[-1] == "other", _enum()


def test_the_kinds_that_were_already_there_are_untouched():
    """A schema is a contract. Red if a kind were renamed or dropped while
    adding one -- every existing manifest must still validate."""
    for kind in ("plane", "train", "car", "ship", "ferry", "bus", "shuttle"):
        assert kind in _enum(), f"{kind} has gone from the schema"


def test_a_booked_taxi_renders_with_its_own_chip():
    """A kind the schema accepts and the page cannot draw is worse than no kind:
    it validates and then renders as nothing. Red without the
    `_TRANSPORT_KINDS` entry."""
    from generator import html_assembler

    kinds = getattr(html_assembler.HTMLAssembler, "_TRANSPORT_KINDS", None)
    if kinds is None:                      # pragma: no cover - layout changed
        source = (ROOT / "generator" / "html_assembler.py").read_text(encoding="utf-8")
        assert '"taxi": (' in source, "no taxi chip anywhere in the assembler"
        return
    assert "taxi" in kinds, f"no chip for a booked taxi: {sorted(kinds)}"
    icon, label = kinds["taxi"]
    assert label == "Taxi" and icon, (icon, label)


def test_a_taxi_is_not_a_scheduled_service():
    """The half that is easy to get wrong, and the reason this file argues it.

    `TRANSIT_MODES` is what an estimator can look a timetable up for. A taxi has
    none. Red if `taxi` is added there: the estimator would start searching for
    services that do not exist for a leg the traveler has already booked.
    """
    from generator import transit_estimate

    assert "taxi" not in transit_estimate.TRANSIT_MODES, (
        "a taxi has been added to the scheduled-service modes, so the "
        "estimator will look for a timetable that cannot exist")
