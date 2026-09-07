"""Days the itinerary does not account for, said out loud.

`multimodal-routing.md` §9 names the case and its own recommendation was *"do
not guess -- wait for a real manifest that contains one"*. This is the
measurement that recommendation asks for, and the smallest true thing that can
be built on it.

**Measured before anything was written.** A two-stop Adriatic manifest -- Venice
June 12-14, a seven-night sailing, Dubrovnik June 21-23 -- is a twelve-day trip.
The pipeline renders **six days** and nothing anywhere notices the other six.
§9 predicted exactly that: the manifest models travel and lodging as separate
things, a cruise is simultaneously both, so the sailing is absent from lodging
entirely and *"leaving it out produces a page where the traveler teleports
between stops."*

**This does not choose one of §9's three shapes**, and deliberately so. Whether
sea days become destinations, or a leg carries a duration and the days are
skipped, or a `carried` leg kind owns both -- that is the decision the note says
to defer. Noticing the days exist is the precondition for all three, and it is
useful on its own: the commonest cause of a gap is not a cruise at all, it is a
stop somebody forgot.

**It reports rather than refuses.** A gap is not invalid. It is a sailing, a
sleeper train, days deliberately left unplanned, or an omission -- and only the
traveler can tell those apart. What the engine owes them is the same posture it
takes everywhere else: name what is missing rather than quietly render a
shorter trip.
"""

from __future__ import annotations

import datetime as dt

from generator.date_span import day_count, span, unaccounted


# ------------------------------------------------------------------- the span


def test_a_date_string_yields_the_days_it_covers() -> None:
    assert span("June 12-14, 2026") == (dt.date(2026, 6, 12), dt.date(2026, 6, 14))


def test_a_range_across_a_month_boundary() -> None:
    """The case `date_span` exists for: "August 31" matched, then digits after
    the dash found "September", and the range collapsed to one day."""
    assert span("August 31 - September 1, 2026") == (
        dt.date(2026, 8, 31), dt.date(2026, 9, 1))


def test_a_range_across_a_year_boundary() -> None:
    """One year is stated and it belongs to the END of the range."""
    assert span("December 30 - January 2, 2027") == (
        dt.date(2026, 12, 30), dt.date(2027, 1, 2))


def test_an_iso_range() -> None:
    assert span("2026-10-17 to 2026-10-21") == (
        dt.date(2026, 10, 17), dt.date(2026, 10, 21))


def test_a_single_date_is_a_span_of_one_day() -> None:
    assert span("October 10, 2026") == (dt.date(2026, 10, 10), dt.date(2026, 10, 10))


def test_a_year_stated_elsewhere_in_the_string_is_used() -> None:
    """`_same_month` matches "June 12-14" and the year is often not beside it.
    Written after fault injection: every other fixture here carries a year in
    the matched position, so replacing the lookup with `today().year` changed
    nothing and the fallback branch was never executed by a test.

    The first fixture tried was "Summer 2026: June 12-14", which returns None --
    `[A-Za-z]+\s+\d{1,2}` matches "Summer 20" and "summer" is not a month. That
    is a real weakness in the same-month pattern and is not this change's to
    fix; recorded here so the next reader does not think it was overlooked."""
    # 2027, not 2026: with `today().year` substituted for the lookup the fault
    # was invisible, because the current year happened to equal the fixture's.
    # A year the clock cannot supply is what makes the assertion about the code.
    assert span("June 12-14 (2027)") == (dt.date(2027, 6, 12), dt.date(2027, 6, 14))


def test_an_unparseable_string_has_no_span() -> None:
    """None rather than a guess. `day_count` keeps its floor of 1 because a
    destination with no schedule is worse than one day of it; inventing dates
    would put a destination on days the traveler never named."""
    assert span("sometime next autumn") is None
    assert span("") is None


def test_the_count_still_answers_exactly_as_it_did() -> None:
    """The span is primary now and the count is derived from it. Every shape
    `day_count` already handled has to come back with the same number, or this
    refactor has changed what every day-scaled target is computed against."""
    for text, expected in (
        ("June 12-14, 2026", 3),
        ("August 31 - September 1, 2026", 2),
        ("2026-10-17 to 2026-10-21", 5),
        ("October 10, 2026", 1),
        ("December 30 - January 2, 2027", 4),
        ("sometime next autumn", 1),
        ("", 1),
    ):
        assert day_count(text) == expected, text


def test_the_maximum_still_caps() -> None:
    assert day_count("2026-10-01 to 2026-10-30", maximum=5) == 5


# --------------------------------------------------------------- the coverage


def test_the_sea_days_are_the_gap() -> None:
    """The measurement itself: six days of a twelve-day trip were rendered."""
    runs = unaccounted([
        ("Venice, Italy", "June 12-14, 2026"),
        ("Dubrovnik, Croatia", "June 21-23, 2026"),
    ])
    assert len(runs) == 1
    assert runs[0]["from"] == dt.date(2026, 6, 15)
    assert runs[0]["to"] == dt.date(2026, 6, 20)
    assert runs[0]["days"] == 6


def test_it_names_the_stays_either_side() -> None:
    """A run of dates is not actionable on its own; where in the trip it falls
    is what tells the traveler whether it is their sailing or their mistake."""
    run = unaccounted([
        ("Venice, Italy", "June 12-14, 2026"),
        ("Dubrovnik, Croatia", "June 21-23, 2026"),
    ])[0]
    assert run["after"] == "Venice, Italy"
    assert run["before"] == "Dubrovnik, Croatia"


def test_stays_that_share_a_day_have_no_gap() -> None:
    """The ordinary case, and the one a naive check gets wrong: you leave one
    place and reach the next on the same date."""
    assert unaccounted([
        ("Zion", "October 7-9, 2026"),
        ("Bryce", "October 9-11, 2026"),
    ]) == []


def test_stays_on_consecutive_days_have_no_gap() -> None:
    assert unaccounted([
        ("Zion", "October 7-9, 2026"),
        ("Bryce", "October 10-12, 2026"),
    ]) == []


def test_one_missing_day_is_still_a_gap() -> None:
    """No threshold. A single unaccounted day is a night the traveler has
    nowhere to sleep, which is the whole point of saying so."""
    runs = unaccounted([
        ("Zion", "October 7-9, 2026"),
        ("Bryce", "October 11-13, 2026"),
    ])
    assert len(runs) == 1 and runs[0]["days"] == 1


def test_every_gap_is_reported_not_only_the_first() -> None:
    runs = unaccounted([
        ("A", "June 1-2, 2026"),
        ("B", "June 6-7, 2026"),
        ("C", "June 12-13, 2026"),
    ])
    assert [r["days"] for r in runs] == [3, 4]


def test_stays_out_of_order_are_read_in_date_order() -> None:
    """The list is the itinerary's order and is usually chronological, but a
    manifest that groups by region is legal -- and a gap computed from list
    position would then be fiction."""
    runs = unaccounted([
        ("Dubrovnik, Croatia", "June 21-23, 2026"),
        ("Venice, Italy", "June 12-14, 2026"),
    ])
    assert len(runs) == 1 and runs[0]["after"] == "Venice, Italy"


def test_a_stay_whose_dates_do_not_parse_is_skipped() -> None:
    """Not guessed at. Inventing dates for a typo would report a gap the
    traveler does not have, and a false gap is worse than a missed one -- it
    sends them looking for a mistake that is not there."""
    runs = unaccounted([
        ("Venice", "June 12-14, 2026"),
        ("Somewhere", "TBC"),
        ("Dubrovnik", "June 21-23, 2026"),
    ])
    assert len(runs) == 1 and runs[0]["days"] == 6


def test_one_stay_has_nothing_to_be_between() -> None:
    assert unaccounted([("Venice", "June 12-14, 2026")]) == []
    assert unaccounted([]) == []
