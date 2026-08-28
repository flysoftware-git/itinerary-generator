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


def day_count(dates: str, *, maximum: int | None = None) -> int:
    """Days covered by a manifest date string, inclusive of both ends.

    Always at least 1: an unparseable string means "we do not know", and a
    destination with no schedule at all is a worse answer than one day of it.

    `maximum` caps the result for callers that scale a per-day target and must
    not run away on a long stay.
    """
    text = _normalize(dates)
    if not text:
        return 1

    # ISO first: it is the only unambiguous form. Tried later, "2026-10-17 to
    # 2026-10-21" was matched by the month-name pattern as "to 20" -- [A-Za-z]+
    # took "to" and \d{1,2} took the first two digits of the year -- yielding 1.
    count = _iso_range(text) or _cross_month(text) or _same_month(text) or 1
    if maximum is not None:
        count = min(count, maximum)
    return max(1, count)


def _cross_month(text: str) -> int | None:
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
    return (end - start).days + 1


def _same_month(text: str) -> int | None:
    """"September 2-4, 2026" and the single-date "October 10, 2026"."""
    match = re.search(r"[A-Za-z]+\s+(\d{1,2})(?:\s*-\s*(\d{1,2}))?(?:,\s*\d{4})?", text)
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2) or match.group(1))
    if end < start:
        return None
    return end - start + 1


def _iso_range(text: str) -> int | None:
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
    return (end - start).days + 1
