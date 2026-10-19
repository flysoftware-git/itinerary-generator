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
     morning. Needs the two times AND `nights`, because the times alone are
     the same pair of strings whether the arrival is next morning or the same
     day read off two clocks, and the difference is whether the traveler needs
     a bed ashore.
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
Run against the schema before the fields were added, every case-1, case-2 and
`nights` assertion here fails at parse time with
"Additional properties are not allowed ('duration' was unexpected)" /
"('arrive_time', 'depart_time', 'nights' were unexpected)" -- i.e. the
manifest was refused, not quietly degraded. `test_a_leg_may_still_be_only_a_mode`
passes both before and after, which is the point of it.
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


@pytest.mark.parametrize("field", ["duration", "depart_time", "arrive_time", "nights"])
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
    """Drive Venice to Pula, 2h40: mode, duration, and no night on it.

    `nights` omitted is the claim that it lands the same day. The duration is
    the author's figure, not a build's: it survives a rebuild, which the
    AI-generated `travel_time` on the arriving stop does not.
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

def test_case_two_an_overnight_leg_states_the_night_it_spends(tmp_path):
    """Sail Venice 18:00 to Koper 08:00 next morning, one night aboard.

    All four fields at once, which is the case that could not be expressed in
    any form before: `nights: 1` is what lets a guide say no bed ashore is
    needed, and it is stated because it cannot be read off the two times.
    """
    data = _load(tmp_path, _manifest(
        "legs:\n"
        "  - from: venice\n"
        "    to: koper\n"
        "    mode: transit\n"
        "    duration: 14 hours\n"
        "    depart_time: '18:00'\n"
        "    arrive_time: '08:00'\n"
        "    nights: 1\n"
    ))
    leg = data["legs"][0]
    assert leg["duration"] == "14 hours"
    assert leg["depart_time"] == "18:00"
    assert leg["arrive_time"] == "08:00"
    assert leg["nights"] == 1


def test_zero_nights_is_distinguishable_from_omitting_nights(tmp_path):
    """`0` is an author saying 'same day'; absent is an author not saying.

    They render the same today. They are kept distinguishable because the
    first is a fact a renderer may repeat and the second is not, and
    collapsing them now would make that unrecoverable later.
    """
    data = _load(tmp_path, _manifest(
        "legs:\n"
        "  - from: venice\n"
        "    to: koper\n"
        "    mode: auto\n"
        "    nights: 0\n"
        "  - from: koper\n"
        "    to: rijeka\n"
        "    mode: auto\n"
    ))
    assert data["legs"][0]["nights"] == 0
    assert "nights" not in data["legs"][1]


@pytest.mark.parametrize("bad", ["one", "'1'", "1.5", "-1", "31"])
def test_nights_refuses_what_is_not_a_count_of_nights(tmp_path, bad):
    """A quoted '1' included: YAML quoting is how this field would be got
    wrong, and a count that silently became text would compare false against
    every integer a renderer tests it with."""
    with pytest.raises(Exception):
        _load(tmp_path, _manifest(
            "legs:\n"
            "  - from: venice\n"
            "    to: koper\n"
            "    mode: transit\n"
            f"    nights: {bad}\n"
        ))


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
