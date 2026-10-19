"""A journey's rail is as long as the journey takes.

THE DEFECT
----------
Movement between stops rendered as a short bar, sometimes with a word on it,
and everything else about the journey went into prose inside the arriving
stop's own section. So every journey was the same size on the page: a
nine-mile drive and a fourteen-hour overnight sailing drew identically,
because length carried nothing.

Duration is the one property of a journey a reader plans around.

WHAT THIS WAS SEEN TO FAIL AGAINST
----------------------------------
With the `_build_transit_spine` call removed from the section loop -- the
tree as it was -- 12 of the 28 tests here fail and 16 (the pure
length-arithmetic ones) pass. Every page-level assertion goes: no segments in
the page at all, no component CSS, no duration, no night, nothing on the way.

`test_longer_journeys_get_longer_rails` was also run against a stubbed
`rail_length_px` returning a constant -- which is exactly what the previous
rendering did, one bar height for every journey -- and gives
`[56, 56, 56, 56, 56]`, one distinct value where five are required.

EVERYTHING HERE GOES THROUGH `assemble`
---------------------------------------
Deliberately, and it is the expensive lesson this repository has already paid
for twice. `_build_route_gmaps_url` was correct in isolation while eleven of
twelve links on a rail itinerary opened car directions, because the dict the
caller passed it was missing a key; `_build_getting_here` repeated the shape
with a hike. A test that calls `render_segment` directly proves the function
works and says nothing about whether a page contains it.
"""

from __future__ import annotations

import pytest

from generator import transit_spine
from generator.html_assembler import HTMLAssembler
from generator.transit_routing import stamp_resolved_modes


# --------------------------------------------------------------------------
# Length
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,minutes", [
    ("2h40", 160),
    ("2h 40m", 160),
    ("2 h 40", 160),
    ("1:50", 110),
    ("14 hours", 840),
    ("1.5 hours", 90),
    ("50 min", 50),
    ("about 50 minutes", 50),
    ("", None),
    ("a long way", None),
    (None, None),
])
def test_an_authored_duration_is_read_for_scaling(text, minutes):
    assert transit_spine.duration_minutes(text) == minutes


def test_longer_journeys_get_longer_rails():
    """The whole point. Strictly increasing across the real cases."""
    lengths = [
        transit_spine.rail_length_px(transit_spine.duration_minutes(d))
        for d in ("", "50 min", "1h50", "2h40", "14 hours")
    ]
    assert lengths == sorted(lengths)
    assert len(set(lengths)) == len(lengths), (
        f"two journeys of different lengths drew the same: {lengths}"
    )


def test_a_very_long_journey_is_compressed_rather_than_unbounded():
    """Fourteen hours at forty minutes' scale would be metres of page.

    The rail carries the comparison; the figure is in the text beside it.
    """
    assert transit_spine.rail_length_px(14 * 60) <= transit_spine.MAX_LENGTH_PX
    assert transit_spine.rail_length_px(100 * 60) == transit_spine.MAX_LENGTH_PX


def test_an_unreadable_duration_still_draws_a_rail():
    """Never dropped because the scaler could not read it -- the string is
    the author's figure, and the scaler's job is pixels."""
    assert transit_spine.rail_length_px(None) == transit_spine.BARE_LENGTH_PX


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------

def _stop(dest_id: str, name: str, dates: str, **extra) -> dict:
    stop = {
        "id": dest_id, "name": name, "dates": dates,
        "lat": 45.0, "lng": 14.0,
        "planning_links": [{"label": "Notes", "url": "https://example.com"}],
        "ai_content": {"getting_here": {}},
    }
    stop.update(extra)
    return stop


def _trip(legs: list[dict], destinations: list[dict]) -> dict:
    trip = {
        "trip": {"title": "Adriatic", "theme_color": "#C0623E"},
        "_meta": {
            "generator_version": "9.9.9",
            "template_version": "2.5",
            "generated_at_utc": "2026-10-18T12:00:00+00:00",
            "llm": {"provider": "none", "model": "none",
                    "usage": {"models": [], "total_estimated_cost_usd": 0.0}},
        },
        "legs": legs,
        "destinations": destinations,
    }
    stamp_resolved_modes(trip)
    return trip


def _three_case_trip() -> dict:
    return _trip(
        legs=[
            # 1 -- within one day
            {"from": "venice", "to": "pula", "mode": "auto", "duration": "2h40"},
            # 2 -- spans a night
            {"from": "pula", "to": "koper", "mode": "transit",
             "duration": "14 hours", "depart_time": "18:00",
             "arrive_time": "08:00", "nights": 1},
            # 3 -- things on the way
            {"from": "koper", "to": "rijeka", "mode": "auto", "duration": "1h50"},
        ],
        destinations=[
            _stop("venice", "Venice, Italy", "May 1-3, 2026"),
            _stop("pula", "Pula, Croatia", "May 3, 2026"),
            _stop("koper", "Koper, Slovenia", "May 4, 2026",
                  transportation=[{"type": "ship", "provider": "Venezia Lines"}]),
            _stop("rijeka", "Rijeka, Croatia", "May 5-6, 2026",
                  en_route_seeds=["Skocjan Caves", "Opatija"],
                  ai_content={"getting_here": {"en_route_stops": [
                      {"title": "Skocjan Caves",
                       "distance_or_duration": "adds 35 min",
                       "description": "UNESCO cave system."},
                      {"title": "Opatija",
                       "distance_or_duration": "on the route",
                       "description": "Habsburg seafront promenade."},
                  ]}}),
        ],
    )


def _assemble(trip: dict) -> str:
    return HTMLAssembler(config_path="config.yaml").assemble(trip)


def test_a_built_page_draws_a_segment_for_every_journey():
    html = _assemble(_three_case_trip())
    assert html.count('class="spine"') == 3, (
        "four stops means three journeys between them"
    )


def test_case_one_a_leg_inside_one_day_shows_mode_and_duration():
    html = _assemble(_three_case_trip())
    assert "Venice, Italy → Pula, Croatia" in html
    assert "2h40" in html, "the author's figure, verbatim"
    assert 'data-mode="auto"' in html
    # It lands the same day, so there is no day rule across this one.
    segment = html.split('class="spine"')[1].split("</div>\n</div>")[0]
    assert 'class="spine-daybreak"' not in segment


def test_case_two_an_overnight_leg_shows_the_night_and_needs_no_bed_ashore():
    html = _assemble(_three_case_trip())
    assert "14 hours" in html
    assert "18:00 → 08:00" in html, (
        "the two clock times as the timetable states them, not subtracted"
    )
    assert "1 night spent in transit" in html
    assert "no lodging needed at either end" in html
    assert 'class="spine-daybreak"' in html, "the date boundary crosses the rail"


def test_case_two_draws_a_longer_rail_than_case_one():
    """The defect, stated as the page rather than as a function.

    A fourteen-hour sailing and a 2h40 drive drew the same before this.
    """
    html = _assemble(_three_case_trip())
    lengths = [
        int(chunk.split("px")[0])
        for chunk in html.split("--spine-length:")[1:]
    ]
    assert len(lengths) == 3
    drive, sailing, coast_road = lengths
    assert sailing > drive > 0
    assert sailing > coast_road


def test_case_three_shows_what_is_on_the_way_and_what_each_adds():
    html = _assemble(_three_case_trip())
    assert "On the way" in html
    assert "Skocjan Caves" in html
    assert "adds 35 min" in html, "what the detour costs"
    assert "UNESCO cave system." in html, "what it is"
    assert "Opatija" in html


def test_a_booking_decides_the_marker_rather_than_the_declared_mode():
    """A sailing is `mode: transit` with a `ship` booking. A train glyph over
    it would be wrong about the one thing the marker exists to say."""
    html = _assemble(_three_case_trip())
    assert transit_spine.BOOKED_MARKERS["ship"][0] in html
    # ...without rewriting the mode anywhere else.
    assert 'data-mode="transit"' in html


# --------------------------------------------------------------------------
# The quiet case, which is the common one
# --------------------------------------------------------------------------

def _quiet_trip() -> dict:
    return _trip(
        legs=[{"from": "rijeka", "to": "zagreb", "mode": "auto"}],
        destinations=[
            _stop("rijeka", "Rijeka, Croatia", "May 5-6, 2026"),
            _stop("zagreb", "Zagreb, Croatia", "May 7, 2026"),
        ],
    )


def test_a_leg_with_nothing_to_say_is_a_bare_rail():
    """No card, no header, no badges, no empty rows.

    This is the case that decides whether the page reads calmer or busier
    than before, so it is pinned rather than left to taste.
    """
    html = _assemble(_quiet_trip())
    assert html.count('class="spine"') == 1
    assert 'data-measured="no"' in html
    # Asserted against the MARKUP, not the page: the component's CSS names
    # every one of these classes, and a page that draws any segment at all
    # carries all of it.
    assert 'class="spine-facts"' not in html
    assert 'class="spine-overnight"' not in html
    assert 'class="spine-stops"' not in html
    assert f"--spine-length:{transit_spine.BARE_LENGTH_PX}px" in html


def test_a_leg_with_no_legs_block_at_all_still_draws_its_rail():
    """Every manifest written before `legs` gained its journey fields.

    The rail is the page's statement that the traveler moved; omitting it
    would put two stops flush together as though they were one place.
    """
    trip = _trip(legs=[], destinations=[
        _stop("rijeka", "Rijeka, Croatia", "May 5-6, 2026"),
        _stop("zagreb", "Zagreb, Croatia", "May 7, 2026"),
    ])
    html = _assemble(trip)
    assert html.count('class="spine"') == 1


def test_a_night_is_never_said_on_the_strength_of_two_clock_times():
    """`depart_time` and `arrive_time` are on two different clocks by design.

    18:00 to 08:00 with no `nights` is an author who has not said a night was
    spent. Subtracting them would produce a precise-looking wrong answer, so
    the page says nothing.
    """
    trip = _trip(
        legs=[{"from": "rijeka", "to": "zagreb", "mode": "transit",
               "depart_time": "18:00", "arrive_time": "08:00"}],
        destinations=[
            _stop("rijeka", "Rijeka, Croatia", "May 5-6, 2026"),
            _stop("zagreb", "Zagreb, Croatia", "May 7, 2026"),
        ],
    )
    html = _assemble(trip)
    assert "18:00 → 08:00" in html
    assert "spent in transit" not in html
    assert 'class="spine-daybreak"' not in html


def test_nights_zero_says_nothing_either():
    trip = _trip(
        legs=[{"from": "rijeka", "to": "zagreb", "mode": "auto", "nights": 0}],
        destinations=[
            _stop("rijeka", "Rijeka, Croatia", "May 5-6, 2026"),
            _stop("zagreb", "Zagreb, Croatia", "May 7, 2026"),
        ],
    )
    assert "spent in transit" not in _assemble(trip)


# --------------------------------------------------------------------------
# The partial's CSS reaches the page, once, and only when it is used
# --------------------------------------------------------------------------

def test_the_component_css_is_in_the_page_exactly_once():
    html = _assemble(_three_case_trip())
    assert html.count("Transit segment spine") == 1, (
        "the partial's styles are emitted once per page, not once per segment"
    )
    assert "<!--PARTIAL_STYLES-->" not in html, "the placeholder was not filled"


def test_a_page_with_no_segments_carries_none_of_the_component_css():
    """A partial must not be a tax on a page that does not draw it."""
    trip = _trip(legs=[], destinations=[_stop("solo", "Zagreb, Croatia", "May 7, 2026")])
    html = _assemble(trip)
    assert 'class="spine"' not in html
    assert "Transit segment spine" not in html
    assert "<!--PARTIAL_STYLES-->" not in html


# --------------------------------------------------------------------------
# What the segment must not claim
# --------------------------------------------------------------------------

def test_a_departure_naming_the_first_stop_draws_no_journey_into_it():
    """`trip.departure: "Zagreb"` on a trip starting in Zagreb is ordinary.

    A rail from a place to itself says a journey happened that did not.
    """
    trip = _trip(legs=[], destinations=[
        _stop("zagreb", "Zagreb, Croatia", "May 7, 2026"),
        _stop("rijeka", "Rijeka, Croatia", "May 8, 2026"),
    ])
    trip["trip"]["departure"] = "Zagreb, Croatia"
    html = _assemble(trip)
    assert html.count('class="spine"') == 1, (
        "one journey, Zagreb to Rijeka -- not a second one from Zagreb to "
        "itself"
    )


def test_an_authors_leg_note_is_not_repeated_onto_the_segment():
    """`stretch_note` renders verbatim on the getting-here card (requirements
    §3.3) and must not also render here. It is the author's note about exactly
    this leg, so it arguably belongs on the segment instead -- but putting it
    in both places puts one sentence on the page twice, which is wrong
    whichever way that question is later settled."""
    note = "The coast road is slower and worth it."
    trip = _trip(
        legs=[{"from": "rijeka", "to": "zagreb", "mode": "auto"}],
        destinations=[
            _stop("rijeka", "Rijeka, Croatia", "May 5-6, 2026"),
            _stop("zagreb", "Zagreb, Croatia", "May 7, 2026",
                  stretch_note=note,
                  # The getting-here card only renders when it has something
                  # to say; without this the note has no home at all and the
                  # count below would pass for the wrong reason.
                  ai_content={"getting_here": {
                      "route_summary": "A6 north through Karlovac.",
                      "travel_time": "2h 10m",
                  }}),
        ],
    )
    html = _assemble(trip)
    assert "A6 north through Karlovac." in html, "the card did not render"
    assert html.count(note) == 1
