"""The phrasing experiment asks one question two ways, and nothing else.

`scripts/experiment_locals_favourite_phrasing.py` changes no query and no
prompt; it measures whether one would be worth changing. Two things about it
are worth holding, because both were wrong in a first draft and neither would
have announced itself in the numbers:

* the two arms must differ **only** in the opening clause. Everything after it
  -- the price clause, the $/cuisine contract, the ratings and links
  instructions -- is the real builder's, so a later change to any of them is
  shared by both arms automatically. An experiment against a restated prompt
  measures the restatement.
* the variant must be readable English. The first draft spliced the new opener
  in front of the builder's own `for <place>` and produced *"restaurants that
  are favourites of the people who live for Oak Harbor, Washington"*, which is
  a different question from the intended one and a worse one than the baseline.
  Nothing downstream could have told.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parent.parent / "scripts"
          / "experiment_locals_favourite_phrasing.py")


def _module():
    spec = importlib.util.spec_from_file_location("locals_favourite_experiment", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXPERIMENT = _module()
STOP = "Oak Harbor, Washington"


def test_the_two_arms_differ_only_in_the_opening_clause():
    pair = EXPERIMENT.queries_for(STOP)

    baseline_rest = pair["baseline"][len(EXPERIMENT.BASELINE_OPENER):]
    variant_rest = pair["locals_favourite"][len(EXPERIMENT.LOCALS_FAVOURITE_OPENER):]
    assert baseline_rest == variant_rest
    assert pair["baseline"] != pair["locals_favourite"]


def test_the_variant_reads_as_the_question_it_means_to_ask():
    """The first draft read *"the people who live for Oak Harbor"*. That is a
    different question, it is a worse one than the baseline, and every number
    this script produces would have been about it without saying so."""
    variant = EXPERIMENT.queries_for(STOP)["locals_favourite"]

    assert f"who live in {STOP} with" in variant
    assert "live for" not in variant


def test_the_baseline_is_the_real_production_query():
    """Not a copy of it. The whole design of this script is that a phrasing
    which wins against a hand-rolled prompt has won nothing."""
    from generator.url_discovery import URLDiscoverer

    real = URLDiscoverer.__new__(URLDiscoverer)._restaurant_direct_batch_query(STOP, "")

    assert EXPERIMENT.queries_for(STOP)["baseline"] == real


def test_it_refuses_to_guess_when_the_production_query_moves(monkeypatch):
    """A prompt edit that changes the opening clause must stop this script, not
    quietly leave it splicing onto a sentence that no longer has that shape."""
    monkeypatch.setattr(EXPERIMENT, "BASELINE_OPENER", "Something else entirely ")

    with pytest.raises(SystemExit) as refused:
        EXPERIMENT.queries_for(STOP)

    assert "_restaurant_direct_batch_query" in str(refused.value)


@pytest.mark.parametrize("name, chain", [
    ("Applebee's Grill + Bar", True),
    ("Subway", True),
    ("Frasers Gourmet Hideaway", False),
    ("", False),
])
def test_the_chain_metric_counts_chains(name, chain):
    assert EXPERIMENT._is_chain(name) is chain


@pytest.mark.parametrize("a, b, expected", [
    ([{"title": "A"}, {"title": "B"}], [{"title": "A"}, {"title": "B"}], 1.0),
    ([{"title": "A"}], [{"title": "B"}], 0.0),
    ([{"title": "The Oyster Bar"}], [{"title": "oyster bar"}], 1.0),
    ([], [], None),
])
def test_overlap_is_what_says_a_phrasing_changed_nothing(a, b, expected):
    """The metric that can invalidate the other three. A phrasing whose chain,
    caterer and link numbers all improve while its overlap is 1.0 returned the
    same restaurants in a different order."""
    assert EXPERIMENT._overlap(a, b) == expected


def test_a_run_with_no_credential_says_so_instead_of_reporting_nothing(monkeypatch):
    """A search experiment with no search is not a result of zero. It is an
    experiment that did not happen, and it has to say which."""
    for name in ("XAI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SERPER_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit) as refused:
        EXPERIMENT._ask("anything")

    assert "No search credential" in str(refused.value)


def test_everything_it_writes_goes_to_docs_reports():
    """Its first docstring line says it changes nothing in `generator/`, and a
    reader has to be able to trust that without reading all of it. The script
    writes exactly twice, and both go to the reports directory this repo already
    keeps experiment write-ups in."""
    reports = (Path(__file__).resolve().parent.parent / "docs" / "reports").resolve()

    written = [EXPERIMENT.REPORT_JSON.resolve(), EXPERIMENT.REPORT_MD.resolve()]
    assert [p.parent for p in written] == [reports, reports]
    assert SCRIPT.read_text(encoding="utf-8").count(".write_text(") == len(written)
