"""`trip.access_notes`: what a guide says about getting in.

A traveller with a mobility need, or travelling with somebody who has one,
needs to know how far a place is on foot and whether step-free entry exists
before they set out -- and needs to be told plainly when that is not known.

The tests here are mostly about the second half. The failure mode for this
feature is not omission, it is confident invention: a model asked whether a
place is accessible will answer "yes, wheelchair accessible" from nothing but
the name. That answer is worse than silence, because it is discovered at the
door after the journey.
"""

from __future__ import annotations

import pytest

from generator.ai_content import AIContentGenerator
from generator.manifest_parser import MANIFEST_SCHEMA


DEST = [{
    "id": "d1", "name": "Astoria", "dates": "May 1-3",
    "planning_links": [{"label": "Visitor centre", "url": "https://example.test/astoria"}],
}]


def _guidance(trip_meta):
    return AIContentGenerator._build_access_guidance(None, trip_meta)


# ------------------------------------------------------------------ schema

def test_the_flag_is_optional_and_boolean():
    field = MANIFEST_SCHEMA["properties"]["trip"]["properties"]["access_notes"]
    assert field["type"] == "boolean"
    assert "access_notes" not in MANIFEST_SCHEMA["properties"]["trip"]["required"]


def test_a_manifest_without_the_flag_still_validates(tmp_path):
    """Additive, and existing manifests are the proof."""
    import jsonschema

    manifest = {
        "trip": {"title": "A drive", "subtitle": "west", "theme_color": "#123456"},
        "destinations": DEST,
    }
    jsonschema.validate(manifest, MANIFEST_SCHEMA)
    manifest["trip"]["access_notes"] = True
    jsonschema.validate(manifest, MANIFEST_SCHEMA)


def test_a_non_boolean_flag_is_refused():
    import jsonschema

    manifest = {
        "trip": {"title": "A drive", "subtitle": "west", "theme_color": "#123456",
                 "access_notes": "yes please"},
        "destinations": DEST,
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(manifest, MANIFEST_SCHEMA)


# ---------------------------------------------------------------- guidance

def test_off_by_default_and_silent_when_off():
    """A guide nobody asked for access notes on renders as it always did."""
    for meta in ({}, None, {"access_notes": False}, {"title": "A drive"}):
        said = _guidance(meta)
        assert "REPORT ACCESS" not in said
        assert "say nothing about accessibility" in said


def test_when_asked_it_wants_the_foot_approach_measured():
    said = _guidance({"access_notes": True})
    assert "REPORT ACCESS" in said
    assert "on foot" in said
    assert "surface" in said and "gradient" in said


def test_when_asked_it_wants_the_documented_facilities():
    said = _guidance({"access_notes": True})
    for facility in ("step-free", "accessible parking", "accessible toilets"):
        assert facility in said


def test_the_honesty_rule_is_the_load_bearing_half():
    """The instruction that stops this feature doing harm.

    Asserted as its own test, and named, because it is the clause most likely
    to be trimmed by somebody shortening a long prompt -- and the one whose
    absence turns a helpful guide into a misleading one.
    """
    said = _guidance({"access_notes": True})
    assert "access not documented" in said
    assert "NEVER infer accessibility" in said
    assert "unless a source says so" in said


def test_it_defers_to_the_venue():
    """Nothing generated here is a substitute for the venue's own information,
    and the guide has to say so rather than leaving the reader to assume."""
    assert "venue's own information" in _guidance({"access_notes": True})


# ------------------------------------------------------------------ wiring

def test_the_destination_prompt_has_somewhere_to_put_it():
    """The template and the call site have to agree.

    A placeholder with no argument raises KeyError at generation time -- during
    a paid run, after the search spend, which is the worst moment to find out.
    """
    from pathlib import Path

    template = (Path(__file__).parent.parent / "prompts" / "destination_content.txt").read_text(
        encoding="utf-8")
    assert "{access_guidance}" in template

    import inspect
    source = inspect.getsource(AIContentGenerator._generate_destination_content_impl) \
        if hasattr(AIContentGenerator, "_generate_destination_content_impl") else ""
    if not source:
        source = inspect.getsource(AIContentGenerator)
    assert "access_guidance=self._build_access_guidance(" in source
