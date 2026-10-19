"""A leg can state how long the journey is, and whether a night is spent on it.

THE DEFECT
----------
`LEG_ITEM_SCHEMA` accepted exactly `{from, to, mode}` with
`additionalProperties: False`. So a leg could say where it starts, where it
ends and how it is travelled, and nothing else -- which made every journey the
same size. A nine-mile drive between two towns and a fourteen-hour overnight
sailing carried identical fields, and no renderer could tell one from the
other, because the manifest had not been told.

Three journeys a real itinerary contains, and what each needs:

  1. A drive inside one day -- Venice to Pula, 2h40. Needs `duration`.
  2. A sailing across a night -- Venice (Fusina) 18:00 to Koper 08:00 the next
     morning. Needs `depart_time`, `arrive_time` and `duration`. It needs NO
     count of nights: the destinations' `dates` are the authority on how long
     anything lasts, and a number on the leg would be a second answer free to
     disagree with them. `transit_spine.nights_in_transit` derives it, and
     `tests/test_a_journey_is_as_long_as_it_takes.py` pins what it derives.
  3. A drive with things on it -- Trieste to Rijeka past Skocjan Caves and
     Opatija. This one needs no new leg field: `en_route_seeds` on the
     ARRIVING destination already nominates them and discovery already
     returns a description and a detour cost per stop. What it needed was
     somewhere to render, which is the spine's job, not the schema's. The test
     below pins that the leg and those seeds describe the same journey, so a
     renderer can put them on one segment.

`additionalProperties: False` is why this had to be a schema change rather
than extra keys: a manifest carrying `duration` was REJECTED, not ignored.
That is the better of the two failures -- an author found out -- but it still
means the fact could not be carried at all.

WHAT THIS WAS SEEN TO FAIL AGAINST
----------------------------------
Run against the schema before the fields were added, every case-1 and case-2
assertion here fails at parse time with "Additional properties are not allowed
('duration' was unexpected)" -- i.e. the manifest was refused, not quietly
degraded. `test_a_leg_may_still_be_only_a_mode` passes both before and after,
which is the point of it.

`test_a_leg_may_not_state_a_count_of_nights` is the opposite shape: it fails
against the schema as it shipped on 2026-10-18, which accepted `nights` as an
integer 0-30. That field came back out on 2026-10-19, because a length
expressed as a number beside `dates` is a second answer to a question `dates`
already answers -- see docs/design/transit-segment-spine.md §2.2.
"""

from __future__ import annotations

import textwrap

import pytest

from generator.manifest_parser import LEG_ITEM_SCHEMA, ManifestParser


_HEAD = textwrap.dedent("""\
    trip:
      title: "Adriatic"
      subtitle: "Three journeys"
      theme_color: "#123456"
    """)

_STOPS = textwrap.dedent("""\
    destinations:
      - id: venice
        name: "Venice, Italy"
        dates: "May 1-3, 2026"
        planning_links:
          - label: Notes
            url: https://example.com/venice
      - id: koper
        name: "Koper, Slovenia"
        dates: "May 4, 2026"
        planning_links:
          - label: Notes
            url: https://example.com/koper
      - id: rijeka
        name: "Rijeka, Croatia"
        dates: "May 5-6, 2026"
        planning_links:
          - label: Notes
            url: https://example.com/rijeka
    """)


def _manifest(legs: str) -> str:
    """Three adjacent stops, so two legs are addressable between them."""
    return _HEAD + legs + _STOPS


def _load(tmp_path, text: str) -> dict:
    path = tmp_path / "trip.yaml"
    path.write_text(text, encoding="utf-8")
    return ManifestParser().load(str(path))


# --------------------------------------------------------------------------
# The shape itself
# --------------------------------------------------------------------------

def test_the_leg_schema_still_refuses_an_unknown_key():
    """The fields are additive; the closed shape is not relaxed.

    Opening `additionalProperties` would have carried `duration` too, and
    would also have carried every typo of it silently. This pins that the
    widening was four named fields rather than the door.
    """
    assert LEG_ITEM_SCHEMA["additionalProperties"] is False


def test_the_required_three_are_unchanged():
    """A reviewer taking the schema commit alone should see nothing move."""
    assert LEG_ITEM_SCHEMA["required"] == ["from", "to", "mode"]


@pytest.mark.parametrize("field", ["duration", "depart_time", "arrive_time"])
def test_each_journey_field_is_accepted(field):
    assert field in LEG_ITEM_SCHEMA["properties"]


def test_a_leg_may_still_be_only_a_mode(tmp_path):
    """Every leg authored before these fields existed, unchanged.

    Passes before and after the change. It is here so that a regression in
    the additive-ness shows up as a failure rather than as a manifest nobody
    re-ran.
    """
    data = _load(tmp_path, _manifest(
        "legs:\n"
        "  - from: venice\n"
        "    to: koper\n"
        "    mode: transit\n"
    ))
    leg = data["legs"][0]
    assert leg == {"from": "venice", "to": "koper", "mode": "transit"}


# --------------------------------------------------------------------------
# Case 1 -- within one day
# --------------------------------------------------------------------------

def test_case_one_a_leg_inside_one_day_states_its_duration(tmp_path):
    """Drive Venice to Pula, 2h40: mode and duration, and nothing else needed.

    The duration is the author's figure, not a build's: it survives a rebuild,
    which the AI-generated `travel_time` on the arriving stop does not.
    """
    data = _load(tmp_path, _manifest(
        "legs:\n"
        "  - from: venice\n"
        "    to: koper\n"
        "    mode: auto\n"
        "    duration: 2h40\n"
    ))
    leg = data["legs"][0]
    assert leg["mode"] == "auto"
    assert leg["duration"] == "2h40"
    assert "nights" not in leg


# --------------------------------------------------------------------------
# Case 2 -- spans a night
# --------------------------------------------------------------------------

def test_case_two_an_overnight_leg_states_its_times_and_its_length(tmp_path):
    """Sail Venice 18:00 to Koper 08:00 next morning, fourteen hours.

    Three fields, and NOT a fourth saying how many nights that is. The stops'
    `dates` already say the traveler leaves on one day and arrives on the
    next; a count here would be a second answer to that, free to disagree
    with it and with nothing to say which wins.

    What the leg contributes is where the journey starts on the departure
    day's clock and how long it runs. That plus the dates is enough to derive
    the night -- see `transit_spine.nights_in_transit` -- and the derivation
    is pinned by the page-level tests, where it belongs, because it is the
    guide's claim rather than the schema's.
    """
    data = _load(tmp_path, _manifest(
        "legs:\n"
        "  - from: venice\n"
        "    to: koper\n"
        "    mode: transit\n"
        "    duration: 14 hours\n"
        "    depart_time: '18:00'\n"
        "    arrive_time: '08:00'\n"
    ))
    leg = data["legs"][0]
    assert leg["duration"] == "14 hours"
    assert leg["depart_time"] == "18:00"
    assert leg["arrive_time"] == "08:00"


@pytest.mark.parametrize("value", ["1", "0", "2"])
def test_a_leg_may_not_state_a_count_of_nights(tmp_path, value):
    """The manifest expresses length through `dates` and nowhere else.

    `nights` shipped on the leg on 2026-10-18 as an integer 0-30 and came back
    out on 2026-10-19. The rule it broke is about the format rather than about
    any one field: the destinations' `dates` are the authority on how long
    anything lasts, so a number beside them answering the same question is a
    second answer with no mechanism to keep the two in step and no rule for
    which wins.

    Refused rather than ignored, which is what `additionalProperties: False`
    buys: an author who writes it finds out, instead of writing a figure the
    guide silently drops.

    `duration` is deliberately NOT caught by this and is tested above. It is a
    transcribed display string like `dates` itself -- "2h40", "about fifty
    minutes" -- never computed, summed or compared. The rule is about numbers
    that express a length, not about saying what a ticket says.
    """
    with pytest.raises(ValueError) as exc_info:
        _load(tmp_path, _manifest(
            "legs:\n"
            "  - from: venice\n"
            "    to: koper\n"
            "    mode: transit\n"
            "    duration: 14 hours\n"
            f"    nights: {value}\n"
        ))
    message = str(exc_info.value)
    assert "nights" in message
    assert "Additional properties are not allowed" in message


def test_the_leg_schema_names_no_length_in_days_or_nights():
    """Stated against the schema as a whole, not one spelling of it.

    `nights` was the field that existed; `days`, `duration_days` and
    `nights_aboard` are the ones someone reaches for next. All of them are the
    same mistake, and a test naming only the first would not notice.
    """
    forbidden = {"nights", "days", "duration_days", "nights_aboard", "length_days"}
    present = forbidden & set(LEG_ITEM_SCHEMA["properties"])
    assert not present, (
        f"{sorted(present)} express a length as a number on a leg. The "
        "destinations' `dates` are the authority on length -- see "
        "docs/design/transit-segment-spine.md §2.2."
    )


# --------------------------------------------------------------------------
# Case 3 -- things on the way
# --------------------------------------------------------------------------

def test_case_three_the_leg_and_its_en_route_seeds_describe_one_journey(tmp_path):
    """Trieste to Rijeka past Skocjan Caves and Opatija.

    No new leg field. `en_route_seeds` on the ARRIVING destination is already
    the manifest's way of nominating places on the leg into it, so the leg
    `{from: koper, to: rijeka}` and `rijeka.en_route_seeds` are two statements
    about the same journey. A renderer can therefore draw one segment carrying
    both the leg's figures and those stops.

    Deliberately NOT a second `see_on_the_way` list on the leg: this project's
    recurring defect is one value restated in two places, free to diverge
    (see `_validate_legs` on mode disagreement), and a leg-level list would be
    exactly that for en-route content. Raised as open rather than settled
    here.
    """
    text = _manifest(
        "legs:\n"
        "  - from: koper\n"
        "    to: rijeka\n"
        "    mode: auto\n"
        "    duration: 1h50\n"
    ).replace(
        '    dates: "May 5-6, 2026"\n',
        '    dates: "May 5-6, 2026"\n'
        "    en_route_seeds:\n"
        "      - Skocjan Caves\n"
        "      - Opatija\n",
    )
    data = _load(tmp_path, text)

    leg = next(l for l in data["legs"] if l["to"] == "rijeka")
    arriving = next(d for d in data["destinations"] if d["id"] == "rijeka")

    assert leg["duration"] == "1h50"
    assert arriving["en_route_seeds"] == ["Skocjan Caves", "Opatija"]
    # The join a renderer makes: the leg's `to` is the stop whose seeds these
    # are. Pinned so a future change to either field's home breaks here
    # rather than in a page nobody rebuilt.
    assert leg["to"] == arriving["id"]
