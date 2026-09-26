"""A seed may name the page it means, and nothing downstream learns a new shape.

WHY THIS EXISTS
---------------
`seeds` is how a manifest author names an attraction they want covered, and the
schema accepted a bare name only: *"Attraction/hike/experience name hints only —
no URLs."* The validator enforced it with a sentence that is the assumption
worth examining — *"The generator discovers all URLs automatically."*

It does, and sometimes it discovers a different thing. A trail with a common
name, a creek that shares its name with a hamlet two hundred kilometres away, a
park page that validates while promising a specific place: discovery can only
guess from a name, and the author frequently knows exactly which page they
meant. The schema had no room to say so, so that knowledge was discarded at the
door and the run went looking for something it had already been told.

So a seed may now be either a bare name (unchanged, and still not a URL) or
`{name, url}` — the author saying WHICH page this hint means.

WHAT THIS CHANGE DOES NOT DO
----------------------------
Nothing here makes URL discovery PREFER the supplied link. `seed_links` is
parsed, validated and exposed; choosing an attraction's link is a separate
change against a different module, and bundling the two would put a schema
widening and a link-selection rewrite under one review.

The tests below assert what the parser promises, and nothing about what
discovery does with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from generator.manifest_parser import ManifestParser

HEAD = """
trip:
  title: "Test"
  subtitle: "Test"
  theme_color: "#123456"
destinations:
  - id: test
    name: "Test Destination"
    dates: "Jan 1-3, 2026"
    planning_links:
      - label: "Notes"
        url: "https://example.com"
"""

PLAIN = """    seeds:
      - "Olympic Discovery Trail"
      - "Fort Worden"
"""

WITH_PAGE = """    seeds:
      - name: "Olympic Discovery Trail"
        url: "https://www.alltrails.com/trail/us/washington/olympic-discovery-trail"
      - "Fort Worden"
"""


TWO_PAGES_ONE_NAME = """    seeds:
      - name: "Riverside Park"
        url: "https://example.org/one"
      - name: "Riverside Park"
        url: "https://example.org/two"
"""

ONE_PAGE_NAMED_TWICE = """    seeds:
      - name: "Riverside Park"
        url: "https://example.org/one"
      - name: "Riverside Park"
        url: "https://example.org/one"
"""

ONE_SEED_WITH_A_PAGE = """    seeds:
      - name: "Riverside Park"
        url: "https://example.org/one"
"""


def _parse(tmp_path: Path, seeds_block: str) -> dict:
    path = tmp_path / "manifest.yaml"
    path.write_text(HEAD + seeds_block, encoding="utf-8")
    return ManifestParser().parse(path)


def test_two_seeds_of_one_name_may_not_name_different_pages(tmp_path):
    """The parser cannot choose between them, so it refuses to choose silently.

    `links[name] = url` kept whichever came last, and the losing page is the one
    the author would go looking for.
    """
    with pytest.raises(ValueError) as caught:
        _parse(tmp_path, TWO_PAGES_ONE_NAME)

    said = str(caught.value)
    assert "two different pages" in said
    assert "example.org/one" in said and "example.org/two" in said


def test_the_same_page_named_twice_is_not_a_conflict(tmp_path):
    """Repetition is not ambiguity: the author said the same thing twice."""
    dest = _parse(tmp_path, ONE_PAGE_NAMED_TWICE)["destinations"][0]

    assert dest["seeds"] == ["Riverside Park", "Riverside Park"]
    assert dest["seed_links"] == {"Riverside Park": "https://example.org/one"}


def test_noseed_drops_the_pages_the_seeds_named(tmp_path):
    """`--noseed` means ignore the manifest's seeds, the links included.

    Left behind, `seed_links` would let the first consumer of it honour an
    author's chosen page during a run told to ignore the hint that carried it.
    """
    from generator.main import _strip_destination_seeds

    parsed = _parse(tmp_path, ONE_SEED_WITH_A_PAGE)
    assert parsed["destinations"][0]["seed_links"]

    _strip_destination_seeds(parsed)

    assert parsed["destinations"][0]["seeds"] == []
    assert "seed_links" not in parsed["destinations"][0], (
        "the pages the seeds named outlived the seeds they came from"
    )


# -- the bare form is untouched ----------------------------------------------


def test_a_bare_name_still_parses_exactly_as_before(tmp_path):
    data = _parse(tmp_path, PLAIN)

    assert data["destinations"][0]["seeds"] == [
        "Olympic Discovery Trail",
        "Fort Worden",
    ]


def test_a_bare_seed_may_still_not_be_a_url(tmp_path):
    """The original rule and its reason are unchanged: a name is a hint for
    discovery, and an address pasted into that slot was always a mistake.
    `test_manifest_parser.py::test_seed_urls_rejected` still guards it; this
    asserts the message now also says what to do instead."""
    block = '    seeds:\n      - "https://alltrails.com/trail/test"\n'

    with pytest.raises(ValueError) as caught:
        _parse(tmp_path, block)

    said = str(caught.value)
    assert "must be a" in said and "URL" in said
    assert "name: ..., url: ..." in said


def test_no_seed_links_key_when_no_seed_named_a_page(tmp_path):
    """Absence means *no seed named one*, not *this manifest predates the
    field*. A key that is always present and usually empty cannot carry that
    distinction."""
    data = _parse(tmp_path, PLAIN)

    assert "seed_links" not in data["destinations"][0]


# -- the object form ---------------------------------------------------------


def test_a_seed_may_name_the_page_it_means(tmp_path):
    data = _parse(tmp_path, WITH_PAGE)

    assert data["destinations"][0]["seed_links"] == {
        "Olympic Discovery Trail":
            "https://www.alltrails.com/trail/us/washington/olympic-discovery-trail",
    }


def test_the_names_come_back_as_plain_strings_either_way(tmp_path):
    """THE POINT OF THE WHOLE CHANGE, and the reason it is safe in one commit.

    Every existing consumer reads `seeds` as a list of names -- `str(seed)
    .strip()`, `seed.startswith(...)`, a key set built by lowercasing it. A dict
    arriving there would read as "{'name': ...}" and match nothing, so widening
    the schema without normalising would be worse than not widening it.
    """
    data = _parse(tmp_path, WITH_PAGE)
    seeds = data["destinations"][0]["seeds"]

    assert seeds == ["Olympic Discovery Trail", "Fort Worden"]
    assert all(isinstance(s, str) for s in seeds)


def test_a_seed_object_may_omit_the_url(tmp_path):
    """`url` is optional: `{name}` alone is the bare form written longhand, and
    an author part-way through filling them in should not be refused."""
    block = '    seeds:\n      - name: "Fort Worden"\n'

    data = _parse(tmp_path, block)

    assert data["destinations"][0]["seeds"] == ["Fort Worden"]
    assert "seed_links" not in data["destinations"][0]


def test_the_name_in_an_object_seed_may_not_be_a_url(tmp_path):
    """The same rule at the other door -- otherwise the object form becomes the
    way to smuggle in exactly what the bare form forbids."""
    block = (
        '    seeds:\n'
        '      - name: "https://example.com/trail"\n'
        '        url: "https://example.com/trail"\n'
    )

    with pytest.raises(ValueError) as caught:
        _parse(tmp_path, block)

    assert "must be a name" in str(caught.value)


def test_a_url_that_is_not_an_address_is_refused(tmp_path):
    """`minLength` in the schema cannot tell an address from a sentence."""
    block = (
        '    seeds:\n'
        '      - name: "Olympic Discovery Trail"\n'
        '        url: "ask the ranger"\n'
    )

    with pytest.raises(ValueError) as caught:
        _parse(tmp_path, block)

    assert "not an http(s) address" in str(caught.value)


def test_an_unknown_key_on_a_seed_is_refused(tmp_path):
    """`additionalProperties: false`. A misspelt `link:` would otherwise be
    accepted and silently dropped -- which is the failure this whole change is
    about: knowledge discarded at the door."""
    block = (
        '    seeds:\n'
        '      - name: "Olympic Discovery Trail"\n'
        '        link: "https://example.com/trail"\n'
    )

    with pytest.raises(ValueError):
        _parse(tmp_path, block)


def test_the_two_forms_mix_in_one_list(tmp_path):
    data = _parse(tmp_path, WITH_PAGE)
    dest = data["destinations"][0]

    assert len(dest["seeds"]) == 2
    assert "Fort Worden" not in dest["seed_links"]
