"""A key is a credential; config carries the decision.

Three modules reach metered Maps Platform products, and each used to decide for
itself whether it was switched on by looking for a key in the environment. A key
arrives for all sorts of reasons that are not "please start billing this
project" -- another checkout on the same machine, a CI secret shared across
jobs, an experiment nobody unset -- and any of them silently enabled three
billable products for every run afterwards, with nothing in any committed file
recording that a decision had been made.

These tests hold the two halves apart, and hold the gate shut on every kind of
doubt.
"""

from __future__ import annotations

import pytest

from generator import maps_platform
from generator.place_resolver import PlaceResolver
from generator.places_filter import PlacesBudgetFilter
from generator.transit_estimate import TransitEstimator


def _config(tmp_path, body: str):
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def funded(monkeypatch):
    """A key in the environment, as on a machine that has one for anything."""
    monkeypatch.setenv("GOOGLE_MAPS_PLATFORM_KEY", "funded-key")
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)


@pytest.fixture
def unfunded(monkeypatch):
    for var in maps_platform.API_KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ------------------------------------------------------- the gate itself


def test_a_key_alone_enables_nothing(tmp_path, funded):
    """The defect this exists for. With a funded key reachable and no decision
    recorded anywhere, the answer is no."""
    off = _config(tmp_path, "maps_platform:\n  enabled: false\n")
    assert maps_platform.enabled(off) is False
    assert maps_platform.api_key(off) == ""


def test_the_gate_alone_enables_nothing(tmp_path, unfunded):
    """Both halves are required, and the missing half is named."""
    on = _config(tmp_path, "maps_platform:\n  enabled: true\n")
    assert maps_platform.enabled(on) is True
    assert maps_platform.api_key(on) == ""
    assert "no key is set" in maps_platform.explain(on)


def test_both_together_enable_it(tmp_path, funded):
    on = _config(tmp_path, "maps_platform:\n  enabled: true\n")
    assert maps_platform.api_key(on) == "funded-key"
    assert maps_platform.explain(on) == ""


@pytest.mark.parametrize("body", [
    "",                                   # empty file
    "other_section:\n  enabled: true\n",  # no section
    "maps_platform: {}\n",                # section, no key
    "maps_platform:\n  enabled: yes-please\n",   # not a boolean
    "maps_platform:\n  enabled: 1\n",     # truthy, still not true
    "maps_platform:\n  enabled: [1, 2\n", # unparseable
])
def test_every_kind_of_doubt_answers_no(tmp_path, funded, body):
    """`read_transit_provider` fails open to its default and is right to: an
    unreadable config should not stop the pipeline running. That argument does
    not transfer to a spend gate. The cost of failing closed is a missing
    place_id; the cost of failing open is a bill nobody authorised."""
    assert maps_platform.enabled(_config(tmp_path, body)) is False


def test_a_missing_config_answers_no(tmp_path, funded):
    assert maps_platform.enabled(tmp_path / "not-here.yaml") is False


# ----------------------------------------- and every caller goes through it


def test_no_metered_client_switches_itself_on(funded, monkeypatch, tmp_path):
    """The three constructors, with a funded key and the shipped default.

    Each of these read the environment directly before this change, so each was
    live the moment a key existed. They now ask one question in one place.
    """
    monkeypatch.chdir(tmp_path)
    _config(tmp_path, "maps_platform:\n  enabled: false\n")

    assert PlaceResolver().enabled is False
    assert PlacesBudgetFilter().available is False
    assert TransitEstimator().available is False


def test_an_explicit_key_still_wins(funded, monkeypatch, tmp_path):
    """Passing a key in is a caller saying so in code, which is a decision of
    the same kind as the config flag. The tests for these modules construct
    them that way, and so does anyone embedding the engine."""
    monkeypatch.chdir(tmp_path)
    _config(tmp_path, "maps_platform:\n  enabled: false\n")

    assert PlaceResolver(api_key="explicit").enabled is True
    assert PlacesBudgetFilter(api_key="explicit").available is True
    assert TransitEstimator(api_key="explicit").available is True


def test_the_shipped_config_has_the_gate_shut():
    """The default anybody gets on a fresh clone. If this ever flips, it should
    take a deliberate edit and a review, which is the whole point of the
    decision living in a committed file."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    assert maps_platform.enabled(root / "config.yaml") is False
