"""A dated schedule says WHEN the traveller does the thing they chose.

The base's schedule gained real dates, and a chosen day trip carries dates of
its own -- and nothing put the two together. So a traveller with booked dates
received a dated schedule and a list of day trips, and still had to work out
for themselves which day each one belonged on.

**This does not give a grouped child a schedule of its own.** That refusal is
the constraint this had to respect rather than reverse: a day trip's plan is
covered by the base's own multi-day schedule, and a second schedule inside the
child would re-introduce the false parity the grouping restructure exists to
fix. What appears is a POINTER on the base's day, naming what happens on it and
linking to the card that already exists. The child gains nothing.

**Placement is by date, never by order.** A day trip written third in the
manifest is not thereby on the third day, and a guess dressed as a placement is
worse than none -- the same argument that made the dated day title read its
ordinal from the label rather than from the day's position in the list.

**Nothing is ever lost.** A day trip whose dates will not parse, or that falls
outside the base's stay, is simply not placed; it still renders its own card
exactly where it always did. At worst a guide fails to point at one.
"""
import datetime

from generator import date_span


STAY = "October 3-7, 2026"


def test_a_label_resolves_to_the_day_it_falls_on():
    assert date_span.day_of("Day 1", STAY) == datetime.date(2026, 10, 3)
    assert date_span.day_of("Day 3", STAY) == datetime.date(2026, 10, 5)


def test_day_of_declines_exactly_where_the_title_declines():
    """The two must agree, because a title that says Mon 5 Oct beside a
    placement that disagrees is worse than either alone."""
    for label, dates in (("Arrival day", STAY),      # not an ordinal
                         ("Day 1", None),            # no dates
                         ("Day 1", "next autumn"),   # unparseable
                         ("Day 9", STAY)):           # past the stay
        assert date_span.day_of(label, dates) is None
        assert date_span.dated_day_label(label, dates) == str(label).strip()


def test_a_day_trip_is_matched_by_its_own_dates():
    day = date_span.day_of("Day 3", STAY)
    assert date_span.covers("October 5, 2026", day)
    assert not date_span.covers("October 6, 2026", day)


def test_a_day_trip_whose_dates_will_not_parse_is_not_placed():
    """False rather than an exception: an unparseable date on one day trip
    must leave the whole guide renderable."""
    day = date_span.day_of("Day 3", STAY)
    assert not date_span.covers("whenever we feel like it", day)
    assert not date_span.covers(None, day)
    assert not date_span.covers("", day)


def test_a_day_trip_spanning_days_is_placed_on_each_one_it_covers():
    """Placed on every day it genuinely covers, rather than only its first.

    A there-and-back day trip covers one day and this is invisible; a manifest
    that gives one a range means it, and saying so on both days is true where
    picking one would be a choice nobody made.
    """
    covered = [d for d in (1, 2, 3, 4, 5)
               if date_span.covers("October 4-5, 2026",
                                   date_span.day_of(f"Day {d}", STAY))]
    assert covered == [2, 3]


def test_the_ordering_of_the_manifest_places_nothing():
    """The property that keeps a guess out of a placement.

    Two day trips, the LATER one written first. Placement must follow the
    dates, so the order they appear in cannot be read off the result.
    """
    first_written = "October 6, 2026"
    second_written = "October 4, 2026"

    on = {d: [name for name, dates in (("written first", first_written),
                                       ("written second", second_written))
              if date_span.covers(dates, date_span.day_of(f"Day {d}", STAY))]
          for d in (1, 2, 3, 4, 5)}

    assert on[2] == ["written second"], on
    assert on[4] == ["written first"], on
