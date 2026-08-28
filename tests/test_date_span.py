"""Day counting for manifest date strings.

Brussels rendered ONE day of schedule for a two-day stay. "August 31 -
September 1, 2026" spans a month boundary, and the regex that had been
inlined in two modules matched only "Month D-D": it took "August 31", looked
for digits after the dash, found "September", and collapsed the range to a
single day.

Every day-scaled target was computed against that number too -- attractions
per day, scenic drives, batch sizing.
"""
import pytest

from generator.date_span import day_count


class TestTheBrusselsCase:
    def test_a_stay_crossing_a_month_boundary_counts_both_days(self):
        assert day_count("August 31 - September 1, 2026") == 2

    def test_the_same_stay_inside_one_month_still_works(self):
        """Frankfurt, September 11-12, rendered correctly throughout -- the
        defect only appeared when the months differed."""
        assert day_count("September 11-12, 2026") == 2


class TestRanges:
    @pytest.mark.parametrize(
        "dates, expected",
        [
            ("October 10, 2026", 1),
            ("September 2-4, 2026", 3),
            ("October 17-21, 2026", 5),
            ("Aug 31 - Sep 1, 2026", 2),
            ("October 17–21, 2026", 5),          # en dash
            ("2026-10-17 to 2026-10-21", 5),
        ],
    )
    def test_supported_forms(self, dates, expected):
        assert day_count(dates) == expected

    def test_iso_is_parsed_before_month_names(self):
        """"2026-10-17 to 2026-10-21" was read as "to 20" by the month-name
        pattern -- [A-Za-z]+ took "to", \d{1,2} took two digits of the year --
        giving 1. ISO is unambiguous and is tried first."""
        assert day_count("2026-10-17 to 2026-10-21") == 5


class TestYearBoundary:
    def test_an_explicit_year_on_each_end(self):
        assert day_count("December 30, 2026 - January 2, 2027") == 4

    def test_a_single_trailing_year_belongs_to_the_end(self):
        """"December 30 - January 2, 2027" means the 2026 December. Assuming
        the year applied to both ends made the span negative."""
        assert day_count("December 30 - January 2, 2027") == 4


class TestUnknownIsOneNotZero:
    @pytest.mark.parametrize("dates", ["", None, "nonsense", "TBD", "  "])
    def test_unparseable_yields_one_day(self, dates):
        """A destination with no schedule at all is a worse answer than one
        day of it."""
        assert day_count(dates) == 1

    def test_a_reversed_range_does_not_go_negative(self):
        assert day_count("October 21-17, 2026") >= 1


class TestCap:
    def test_the_cap_bounds_per_day_targets(self):
        """ai_content scales attractions and drives per day and must not run
        away on a long stay; url_discovery wants the true count."""
        assert day_count("October 17-25, 2026") == 9
        assert day_count("October 17-25, 2026", maximum=5) == 5


class TestBothCallersAgree:
    def test_the_two_modules_no_longer_carry_their_own_regex(self):
        from generator.ai_content import AIContentGenerator
        from generator.url_discovery import URLDiscoverer

        gen = AIContentGenerator.__new__(AIContentGenerator)
        assert gen._infer_day_count("August 31 - September 1, 2026") == 2
        assert URLDiscoverer._infer_destination_day_count("August 31 - September 1, 2026") == 2
