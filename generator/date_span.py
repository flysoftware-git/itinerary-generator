"""date_span.py — how many days a manifest date string covers.

Why this exists as its own module
---------------------------------
The same day-count regex lived in two files, `ai_content._infer_day_count` and
`url_discovery._infer_destination_day_count`, and both had the same defect: they
matched `Month D-D` and nothing else.

    "August 31 - September 1, 2026"   ->  1     (should be 2)

The pattern matches "August 31", then looks for digits after the dash and finds
"September", so the range collapses to a single day. Brussels rendered ONE day
of schedule for a two-day stay, and every day-scaled target -- attractions,
scenic drives, batch sizing -- was computed against the wrong number.

Neither module imports the other, so a leaf module is the honest place for it.
Duplicating it a third time and adding a test that the copies agree would have
worked, but the copies are the problem.
"""
from __future__ import annotations

import calendar
import datetime as _dt
import re

_MONTHS = {name.lower(): num for num, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): num for num, name in enumerate(calendar.month_abbr) if name})

_DASHES = ("–", "—", "−")


def _normalize(text: str) -> str:
    out = str(text or "")
    for dash in _DASHES:
        out = out.replace(dash, "-")
    return " ".join(out.split())


def _year_in(text: str, default: int | None = None) -> int | None:
    years = re.findall(r"\b(\d{4})\b", text)
    if years:
        return int(years[-1])
    return default


def span(dates: str) -> tuple[_dt.date, _dt.date] | None:
    """The first and last day a manifest date string covers, or None.

    The count was the primary answer here and the span was thrown away three
    times over -- each parser worked out two dates and returned their
    difference. That was enough while the only question was "how many days of
    schedule does this destination get", and it cannot answer the question a
    trip asks: **which** days does the itinerary account for.

    None rather than a guess when nothing parses. `day_count` keeps its floor of
    1 because a destination with no schedule is worse than one day of it; a
    *span* has no such fallback, since inventing dates would put a destination on
    days the traveler never named.
    """
    text = _normalize(dates)
    if not text:
        return None
    # ISO first: it is the only unambiguous form. Tried later, "2026-10-17 to
    # 2026-10-21" was matched by the month-name pattern as "to 20" -- [A-Za-z]+
    # took "to" and \d{1,2} took the first two digits of the year -- yielding 1.
    return _iso_range(text) or _cross_month(text) or _same_month(text)


def day_count(dates: str, *, maximum: int | None = None) -> int:
    """Days covered by a manifest date string, inclusive of both ends.

    Always at least 1: an unparseable string means "we do not know", and a
    destination with no schedule at all is a worse answer than one day of it.

    `maximum` caps the result for callers that scale a per-day target and must
    not run away on a long stay.
    """
    found = span(dates)
    count = ((found[1] - found[0]).days + 1) if found else 1
    if maximum is not None:
        count = min(count, maximum)
    return max(1, count)


def _cross_month(text: str) -> tuple[_dt.date, _dt.date] | None:
    """"August 31 - September 1, 2026", and the year-boundary case."""
    match = re.search(
        r"([A-Za-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?\s*-\s*([A-Za-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?",
        text,
    )
    if not match:
        return None

    start_month = _MONTHS.get(match.group(1).lower())
    end_month = _MONTHS.get(match.group(4).lower())
    if not (start_month and end_month):
        return None

    trailing_year = _year_in(text, _dt.date.today().year) or _dt.date.today().year
    start_year = int(match.group(3)) if match.group(3) else trailing_year
    end_year = int(match.group(6)) if match.group(6) else trailing_year

    # "December 30 - January 2, 2027" carries one year, and it belongs to the
    # END of the range. Without this the span is negative and falls back to 1.
    if not match.group(3) and end_month < start_month:
        start_year = end_year - 1

    try:
        start = _dt.date(start_year, start_month, int(match.group(2)))
        end = _dt.date(end_year, end_month, int(match.group(5)))
    except ValueError:
        return None
    if end < start:
        return None
    return start, end


def _same_month(text: str) -> tuple[_dt.date, _dt.date] | None:
    """"September 2-4, 2026" and the single-date "October 10, 2026"."""
    match = re.search(
        r"([A-Za-z]+)\s+(\d{1,2})(?:\s*-\s*(\d{1,2}))?(?:,\s*(\d{4}))?", text)
    if not match:
        return None
    # The month and year are captured now rather than discarded. Counting only
    # ever needed the two day numbers; saying WHICH days needs the rest, and
    # taking them from the same match keeps one reading of the string.
    month = _MONTHS.get(match.group(1).lower())
    if not month:
        return None
    year = int(match.group(4)) if match.group(4) else _year_in(text, _dt.date.today().year)
    first = int(match.group(2))
    last = int(match.group(3) or match.group(2))
    if last < first:
        return None
    try:
        return _dt.date(year, month, first), _dt.date(year, month, last)
    except ValueError:
        return None


def _iso_range(text: str) -> tuple[_dt.date, _dt.date] | None:
    """"2026-10-17 to 2026-10-21"."""
    found = re.findall(r"(\d{4}-\d{2}-\d{2})", text)
    if len(found) < 2:
        return None
    try:
        start = _dt.datetime.strptime(found[0], "%Y-%m-%d").date()
        end = _dt.datetime.strptime(found[1], "%Y-%m-%d").date()
    except ValueError:
        return None
    if end < start:
        return None
    return start, end


def unaccounted(stays: list[tuple[str, str]]) -> list[dict[str, object]]:
    """Runs of days between consecutive stays that no stay covers.

    `stays` is `(name, dates)` in itinerary order. Returns one entry per run,
    each naming the days and the two stays it falls between.

    **Why the engine needs this at all.** `multimodal-routing.md` SS9: a cruise or
    a sleeper train carries the traveler for days, and the manifest models
    travel and lodging as separate things -- so a sailing is either duplicated
    as a lodging entry with no address, or absent, and "absent" is what really
    happens. Measured on a two-stop Adriatic manifest whose stays run June 12-14
    and June 21-23: the pipeline renders six days of a twelve-day trip and
    nothing anywhere notices the other six.

    **It reports rather than refuses.** A gap is not invalid. It is a sailing, a
    sleeper, a few days the traveler is deliberately not planning, or a stop
    somebody forgot -- and only they can say which. What the engine owes them is
    to say the days are unaccounted for, in the same posture it takes everywhere
    else: name what is missing rather than quietly rendering a shorter trip.

    Adjacent stays may share a day and usually do -- you leave one place and
    reach the next on the same date -- so a run starts only where the next stay
    begins more than one day after the previous one ends. A stay whose dates do
    not parse is skipped rather than guessed at: `span` returns None there, and
    inventing dates would put a gap where the traveler put a typo.
    """
    dated: list[tuple[str, _dt.date, _dt.date]] = []
    for name, dates in stays or []:
        found = span(dates)
        if found:
            dated.append((str(name or ""), found[0], found[1]))
    dated.sort(key=lambda row: row[1])

    runs: list[dict[str, object]] = []
    for (before, _, ends), (after, begins, _) in zip(dated, dated[1:]):
        first = ends + _dt.timedelta(days=1)
        last = begins - _dt.timedelta(days=1)
        if last < first:
            continue
        runs.append({
            "from": first,
            "to": last,
            "days": (last - first).days + 1,
            "after": before,
            "before": after,
        })
    return runs
