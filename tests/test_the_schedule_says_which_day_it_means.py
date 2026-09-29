"""A day title says which day it is, when the trip has dates to say it with.

A guide's daily schedule printed generic ordinals -- `Day 1`, `Day 2` -- so a
traveller who had supplied a booked flight and a booked stay still received a
schedule they had to count out against their own calendar by hand. The dates
were in the manifest the whole time: every destination carries a `dates`
string, and `date_span.span()` already turns it into a real `(start, end)`.

`Day 1 - Fri 3 Oct`: the ordinal is what a reader counts by, the date is what
they pack and book against.

**The ordinal is read from the LABEL, not from the day's position.** The
renderer skips days with no periods, so the third rendered day is not reliably
`Day 3`. Dating by position would print a confident wrong date, which is worse
than printing none -- the failure this whole change exists to remove.

**What this does NOT do** is give a grouped child a schedule of its own. That
refusal stands untouched: a day trip's plan is covered by the base's own
multi-day schedule, and dating that schedule does not ask it to stop. Placing
an identified day trip onto one of these days is a separate, deferred question.
"""
from generator.date_span import dated_day_label


DATES = "October 3-7, 2026"


def test_a_day_says_the_date_it_falls_on():
    assert dated_day_label("Day 1", DATES) == "Day 1 · Sat 3 Oct"
    assert dated_day_label("Day 3", DATES) == "Day 3 · Mon 5 Oct"


def test_a_destination_with_no_parseable_dates_reads_exactly_as_before():
    """The widest fallback, and the reason this needs no flag or migration.

    Nothing should look broken where nothing is.
    """
    assert dated_day_label("Day 1", None) == "Day 1"
    assert dated_day_label("Day 1", "") == "Day 1"
    assert dated_day_label("Day 1", "sometime in the autumn") == "Day 1"


def test_a_label_that_is_not_a_plain_ordinal_is_left_alone():
    """A schedule may title a day in words. Appending a date to a label whose
    ordinal was never read would attach a date to the wrong thing."""
    assert dated_day_label("Arrival day", DATES) == "Arrival day"
    assert dated_day_label("Day one", DATES) == "Day one"
    assert dated_day_label("", DATES) == ""


def test_a_day_past_the_end_of_the_stay_gets_no_date():
    """A schedule generated for more days than the dates cover.

    Inventing a date beyond the stay would claim something the manifest does
    not say -- the same defect as dating by position, arrived at differently.
    """
    assert dated_day_label("Day 9", DATES) == "Day 9"


def test_the_last_day_of_the_stay_still_gets_one():
    """The boundary the check above must not overshoot."""
    assert dated_day_label("Day 5", DATES) == "Day 5 · Wed 7 Oct"


def test_a_single_day_destination_dates_its_only_day():
    assert dated_day_label("Day 1", "October 3, 2026") == "Day 1 · Sat 3 Oct"


def test_the_ordinal_comes_first_and_the_date_second():
    """Ruled 2026-09-27, both halves in that order: the ordinal is what a
    reader counts by and the date is what they pack against.

    Asserted as an ordering rather than a format so a later change to how the
    date reads cannot silently put it in front.
    """
    said = dated_day_label("Day 2", DATES)
    assert said.index("Day 2") < said.index("Sun 4 Oct")
