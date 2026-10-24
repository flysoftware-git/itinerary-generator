"""The journey between two stops, drawn as a rail whose length is its duration.

WHAT THIS REPLACES
------------------
Movement between stops rendered as a short bar wedged under a day rule,
sometimes with a word on it. Everything else about the journey either had
nowhere to go or went into prose inside the arriving stop's own section. The
consequence is that every journey was the same size: a nine-mile drive and a
fourteen-hour overnight sailing drew identically, because length carried
nothing.

Duration is the one property of a journey a reader plans around, and it was the
one the rendering threw away.

WHAT IS DRAWN
-------------
A segment: a vertical rail in the left margin between two stop nodes.

  * the rail BETWEEN two nodes is the journey, and its length is scaled from
    the leg's duration;
  * the mode rides ON the rail as a marker rather than as a word in a
    sentence;
  * a date boundary CROSSES the rail rather than interrupting it -- one
    journey with a day rule over it, which is what the traveler has;
  * things on the way hang off the rail.

THE QUIET CASE IS THE COMMON ONE
--------------------------------
Most legs have nothing to say beyond a mode. A segment with no duration, no
times, no nights and no stops renders as a bare rail with its marker: no card,
no header, no badges, no empty rows. That is why this is not a card, and it is
the case that decides whether the page reads calmer or busier than before.

WHAT IS NOT INVENTED HERE
-------------------------
Two rules, both from `docs/design/transit-segment-spine.md`:

1. **The duration string is displayed as the author wrote it, always.** It is
   parsed only to pick a pixel length. An unparseable duration still renders
   its text and takes the default length -- the figure is never dropped
   because the scaler could not read it.

2. **The number of nights in transit comes from the destinations' `dates`.**
   They are the authority on how long anything lasts, so nothing carries a
   second count that could disagree with them -- there is no `nights` field on
   a leg. `nights_in_transit` reads the dates and asks the leg only whether it
   accounts for occupying them.

   It is still never derived from `depart_time` and `arrive_time` together:
   those are on two different clocks by design
   (`manifest_parser.TRANSPORTATION_ITEM_SCHEMA` carries the rule), so
   subtracting them produces a precise-looking wrong answer. `arrive_time` is
   displayed and never computed with.

Markup and CSS live in `templates/partials/transit_spine.html`, loaded through
`generator/partials.py`, so a second consumer of this engine draws the same
component rather than its own copy of the idea.
"""

from __future__ import annotations

import html as html_escape
import re
from typing import Any

from generator import partials
from generator.transit_routing import (
    BOOKED_TYPE_KEY,
    booked_arrival_type,
    resolved_mode,
)

PARTIAL = "transit_spine"

#: The marker that rides on the rail, per declared leg mode. `mixed` gets a
#: compass rather than a vehicle: the manifest is saying the leg is covered
#: several ways, and picking one of them to draw would be choosing an answer
#: the author declined to give.
MODE_MARKERS: dict[str, tuple[str, str]] = {
    "auto": ("\N{AUTOMOBILE}", "Drive"),
    "transit": ("\N{STATION}", "Public transport"),
    "mixed": ("\N{COMPASS}", "Mixed modes"),
    "bike": ("\N{BICYCLE}", "Ride"),
    "hike": ("\N{HIKING BOOT}", "Walk"),
}

#: A BOOKING beats the declared mode for the marker only. A Croatia sailing is
#: `mode: transit` with a `ship` booking, and a train glyph over it would be
#: wrong about the one thing the marker exists to say. It does not change the
#: mode anywhere else -- `data-mode` still reports what the manifest declared.
BOOKED_MARKERS: dict[str, tuple[str, str]] = {
    "plane": ("\N{AIRPLANE}", "Flight"),
    "train": ("\N{STATION}", "Train"),
    "ship": ("\N{PASSENGER SHIP}", "Sailing"),
    "ferry": ("\N{FERRY}", "Ferry"),
    "bus": ("\N{BUS}", "Coach"),
    "shuttle": ("\N{BUS}", "Shuttle"),
    "taxi": ("\N{TAXI}", "Taxi"),
    "car": ("\N{AUTOMOBILE}", "Drive"),
}

_FALLBACK_MARKER = ("\N{BLACK RIGHT-POINTING TRIANGLE}", "Journey")

# ── Length ────────────────────────────────────────────────────────────────

#: A rail with nothing measured on it. Tall enough to read as a journey, short
#: enough that a page of them is not a page of whitespace.
BARE_LENGTH_PX = 72

#: Hard ceiling. Fourteen hours drawn at the same pixels-per-minute as forty
#: would be metres of page.
MAX_LENGTH_PX = 420

#: Compression. `BARE + COEFFICIENT * minutes ** EXPONENT`, so the rail says
#: *longer* reliably and *how much longer* only approximately:
#:
#:     40 min  -> 103px        1h50  -> 137px
#:     2h40    -> 157px        14h   -> 352px
#:
#: An exponent of 1 (linear) puts 14h at 20x the height of 40min, which is a
#: page nobody scrolls; 0 makes every journey the same size again, which is
#: the defect. The reader gets the figure from the text either way -- the rail
#: carries the comparison, not the measurement.
LENGTH_COEFFICIENT = 2.2
LENGTH_EXPONENT = 0.72

_DURATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 2h40, 2h 40m, 2 h 40
    re.compile(r"(?P<h>\d{1,3})\s*h(?:ou)?r?s?\.?\s*(?P<m>\d{1,2})\s*m?i?n?s?\.?\b"),
    # 2:40
    re.compile(r"\b(?P<h>\d{1,3}):(?P<m>\d{2})\b"),
    # 14 hours, 2h, 1.5 hours
    re.compile(r"(?P<h>\d{1,3}(?:\.\d+)?)\s*h(?:ou)?r?s?\b"),
    # 50 min, 50 minutes
    re.compile(r"(?P<m>\d{1,4})\s*m(?:in)?(?:ute)?s?\b"),
)


def duration_minutes(text: Any) -> int | None:
    """Minutes in an authored duration string, or None when unreadable.

    For scaling ONLY. Nothing displays this number and nothing schedules from
    it: the string the author wrote is what the page shows, and an
    unreadable one still shows, at the default length. This is the whole
    reason the parse is allowed to be approximate -- being wrong about pixels
    is recoverable, being wrong about a figure in a guide is not.
    """
    raw = str(text or "").strip().lower()
    if not raw:
        return None
    for pattern in _DURATION_PATTERNS:
        match = pattern.search(raw)
        if not match:
            continue
        groups = match.groupdict()
        hours = float(groups.get("h") or 0)
        minutes = float(groups.get("m") or 0)
        total = int(round(hours * 60 + minutes))
        return total if total > 0 else None
    return None


def rail_length_px(minutes: int | None) -> int:
    """Pixel height of the rail for a journey of this many minutes."""
    if not minutes or minutes <= 0:
        return BARE_LENGTH_PX
    scaled = BARE_LENGTH_PX + LENGTH_COEFFICIENT * (minutes ** LENGTH_EXPONENT)
    return int(min(MAX_LENGTH_PX, round(scaled)))


# ── Rendering ─────────────────────────────────────────────────────────────

def _marker(dest: dict[str, Any], mode: str) -> tuple[str, str]:
    """The glyph and its title. A booking wins, for the marker only.

    `booked_arrival_type` already prefers the live booking and falls back to
    the type stamped at parse time, which is what privacy redaction leaves
    behind -- so a prod build of a sailing still shows a ship rather than
    reverting to the declared mode's train.
    """
    booked = booked_arrival_type(dest)
    if booked in BOOKED_MARKERS:
        return BOOKED_MARKERS[booked]
    return MODE_MARKERS.get(mode, _FALLBACK_MARKER)


def _fact(text: str, extra_class: str = "") -> str:
    return (
        partials.block(PARTIAL, "fact")
        .replace("__FACT_CLASS__", extra_class)
        .replace("__FACT__", html_escape.escape(text))
    )


#: Clock times a leg states, read for ONE purpose: where the journey starts on
#: the departure day's clock. Never compared with `arrive_time` -- see the
#: module docstring.
_CLOCK_RE = re.compile(
    r"^\s*(?P<h>\d{1,2})[:.]?(?P<m>\d{2})?\s*(?P<ampm>am|pm)?\s*$", re.IGNORECASE
)


def _minutes_into_day(text: Any) -> int | None:
    """"18:00" -> 1080. None when the string is not a clock time.

    One clock only. This never meets `arrive_time`, so no two zones are ever
    brought into the same sum.
    """
    match = _CLOCK_RE.match(str(text or ""))
    if not match:
        return None
    hour = int(match.group("h"))
    minute = int(match.group("m") or 0)
    ampm = (match.group("ampm") or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def nights_in_transit(
    leg: dict[str, Any] | None,
    from_dates: str = "",
    to_dates: str = "",
) -> int:
    """Nights the traveler spends ON this leg rather than in a bed.

    **The dates are the authority.** A manifest expresses how long anything
    lasts through the destinations' `dates` and nowhere else, so this reads
    them rather than a number stated beside them. A leg carries no `nights`
    field, and could not be given one without creating a second answer free to
    disagree with the first.

    Two things have to be true, and the dates alone are not enough:

    1. **The dates must leave a night between the two stays.** The last day
       `from` claims and the first day `to` claims, differenced. Adjacent
       stays usually share a day -- you leave one place and reach the next on
       the same date -- and that is nought nights. Either span failing to
       parse is nought too: `date_span.span` returns None rather than guessing,
       and a guide must not assert a night aboard off a typo.

    2. **The leg must itself account for occupying that night.** Koper on the
       4th and Rijeka on the 5th leaves a night between them, but the leg
       joining them is a 1h50 coast road and the traveler slept in a bed
       somewhere. So the leg's own `depart_time` plus its `duration` must
       still be running when the date turns over. That is arithmetic on ONE
       clock -- a departure time and an elapsed duration -- and never touches
       `arrive_time`, which is on the other end's clock and is only ever
       displayed.

    The smaller of the two wins. The dates can veto what the duration claims
    and cannot be overridden by it, which is what "the dates are the
    authority" means in practice.

    A leg that states neither a departure time nor a duration says nothing
    about where its traveler slept, and this returns 0 rather than reading the
    dates alone.
    """
    from generator import date_span

    from_span = date_span.span(str(from_dates or ""))
    to_span = date_span.span(str(to_dates or ""))
    if not from_span or not to_span:
        return 0
    by_dates = (to_span[0] - from_span[1]).days
    if by_dates <= 0:
        return 0

    leg = leg if isinstance(leg, dict) else {}
    start = _minutes_into_day(leg.get("depart_time"))
    minutes = duration_minutes(leg.get("duration"))
    if start is None or not minutes:
        return 0
    by_journey = (start + minutes) // (24 * 60)
    if by_journey <= 0:
        return 0

    return min(by_dates, by_journey)


def overnight_sentence(nights: Any) -> str:
    """What a guide may say about nights in transit, and only that.

    The second clause -- that no bed is needed at either end of the leg --
    follows from the count alone: a night spent in transit is a night not
    spent ashore.

    PART OF THIS MODULE'S PUBLIC SURFACE, and named without an underscore for
    that reason. A second consumer of this engine renders its own markup for
    the same journey and calls this for the wording, so that the two surfaces
    cannot word the same fact differently -- which is the whole point of
    sharing it rather than each writing the sentence out. That makes the
    EXACT STRING part of the contract, not an implementation detail: changing
    it changes what a reader is told on more than one page.

    See `docs/design/transit-segment-spine.md` §4.2.
    """
    if not isinstance(nights, int) or isinstance(nights, bool) or nights <= 0:
        return ""
    label = "1 night" if nights == 1 else f"{nights} nights"
    return f"{label} spent in transit — no lodging needed at either end"


#: The name this was published under until 2026-10-23, kept so that a consumer
#: already calling it does not break the moment it takes this change.
#:
#: It is a compatibility shim and nothing in this repository uses it. Removable
#: once no consumer does either -- which is a thing to go and check rather than
#: assume, since the reason the rename happened is that an upstream rename of a
#: private name breaks a caller nothing here knows about.
_overnight_sentence = overnight_sentence


def en_route_lines(stops: Any) -> list[str]:
    """One line per thing on the way: what it is, and what it adds.

    Reads the en-route stops discovery already produced for the ARRIVING
    destination. There is no leg-level list in the manifest and deliberately
    so (docs/design/transit-segment-spine.md §2.3): `en_route_seeds` on the
    arriving stop already nominates places on the leg into it, and a second
    list on the leg would be one value in two places.

    Returns text, not links. The linked cards in the Getting Here section are
    unchanged and still carry the verified URL; this is the summary a reader
    scanning the rail for "what happens between these two places" needs.
    Whether the spine should take those cards over entirely is open.
    """
    lines: list[str] = []
    for stop in stops or []:
        if not isinstance(stop, dict):
            continue
        name = str(stop.get("title", "") or stop.get("name", "") or "").strip()
        if not name:
            continue
        adds = str(stop.get("distance_or_duration", "") or "").strip()
        description = str(stop.get("description", "") or "").strip()
        parts = [f"<strong>{html_escape.escape(name)}</strong>"]
        if adds:
            parts.append(
                f'<span class="spine-stop-adds">({html_escape.escape(adds)})</span>'
            )
        if description:
            parts.append(html_escape.escape(description))
        lines.append(" ".join(parts))
    return lines


def render_segment(
    *,
    dest: dict[str, Any],
    previous_name: str,
    arrival_name: str,
    leg: dict[str, Any] | None = None,
    en_route_stops: Any = None,
    from_dates: str = "",
    to_dates: str = "",
) -> str:
    """One transit segment: the journey arriving at `dest`.

    `from_dates` and `to_dates` are the two stops' `dates` strings. They are
    passed in rather than read off `dest`, because half of the pair lives on
    the previous stop and the authority on how long this journey is belongs to
    both of them together -- see `nights_in_transit`.

    Returns "" only when there is no journey to draw at all -- no previous
    stop. A journey with nothing said about it still renders, as a bare rail:
    the rail is the page's statement that the traveler moved, and omitting it
    would put two stops flush against each other as though they were the same
    place.
    """
    previous_name = str(previous_name or "").strip()
    arrival_name = str(arrival_name or "").strip()
    if not previous_name:
        return ""
    # `trip.departure` naming the first destination is an ordinary manifest --
    # a trip that starts where the traveler lives. There is no journey into
    # it, and a rail from a place to itself says there was one.
    if previous_name.casefold() == arrival_name.casefold():
        return ""

    leg = leg if isinstance(leg, dict) else {}
    mode = resolved_mode(dest) or "auto"
    icon, mode_label = _marker(dest, mode)

    duration = str(leg.get("duration", "") or "").strip()
    minutes = duration_minutes(duration)
    depart = str(leg.get("depart_time", "") or "").strip()
    arrive = str(leg.get("arrive_time", "") or "").strip()
    nights = nights_in_transit(leg, from_dates, to_dates)
    overnight = overnight_sentence(nights)
    stop_lines = en_route_lines(en_route_stops)

    # ── body ──
    body = ""
    route = f"{previous_name} → {arrival_name}".strip(" →")
    if route:
        body += partials.block(PARTIAL, "route").replace(
            "__ROUTE__", html_escape.escape(route)
        ) + "\n"

    facts: list[str] = []
    if duration:
        facts.append(_fact(duration, "spine-fact-duration"))
    if depart and arrive:
        # Shown as the pair the timetable states, on their own clocks. Not
        # subtracted, not relabelled with a zone, not turned into a date.
        facts.append(_fact(f"{depart} → {arrive}"))
    elif depart:
        facts.append(_fact(f"departs {depart}"))
    elif arrive:
        facts.append(_fact(f"arrives {arrive}"))
    if facts:
        body += partials.block(PARTIAL, "facts").replace(
            "__FACTS__", "".join(facts)
        ) + "\n"

    if overnight:
        body += partials.block(PARTIAL, "overnight").replace(
            "__OVERNIGHT__", html_escape.escape(overnight)
        ) + "\n"

    # `stretch_note` is NOT drawn here, though it is the author's note about
    # exactly this leg and this is the leg's rendering. `requirements.md`
    # §3.3 says it renders verbatim on the getting-here card, and it still
    # does -- putting it in both places puts the same sentence on the page
    # twice. Whether it should move here instead is open; duplicating it
    # while that is decided is the one option that is wrong either way.

    if stop_lines:
        body += (
            partials.block(PARTIAL, "stops")
            .replace("__STOPS_HEAD__", "On the way")
            .replace(
                "__STOPS__",
                "".join(
                    partials.block(PARTIAL, "stop").replace("__STOP__", line)
                    for line in stop_lines
                ),
            )
            + "\n"
        )

    # A date boundary is drawn only where the dates say one is crossed. It
    # marks THAT the boundary is crossed, not when: the two clock times cannot
    # say where midnight falls between them, and placing the rule by
    # arithmetic over them would be the invented precision this component
    # exists to avoid.
    daybreak = partials.block(PARTIAL, "daybreak") if overnight else ""

    return (
        partials.block(PARTIAL, "segment")
        .replace("__MODE__", html_escape.escape(mode, quote=True))
        .replace("__MEASURED__", "yes" if minutes else "no")
        .replace("__LENGTH__", f"{rail_length_px(minutes)}px")
        .replace("__MODE_LABEL__", html_escape.escape(mode_label, quote=True))
        .replace("__MODE_ICON__", icon)
        .replace("__DAYBREAK__", daybreak)
        .replace("__BODY__", body.rstrip("\n"))
    ) + "\n"


def styles() -> str:
    """The component's CSS, emitted once per page."""
    return partials.block(PARTIAL, "styles")
