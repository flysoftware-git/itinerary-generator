"""Markdown emphasis must not reach the page, and removals must be interpretable.

Both defects were found on the first non-park run (2026-08-25, Old Hickory).
See docs/design/destination-type-coverage.md.
"""
import pytest

from generator.ai_content import strip_markdown_emphasis
from generator.html_validator import _format_removal_ratio


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("**Flat Tire Diner**", "Flat Tire Diner"),
        ("**Red Ninja Sushi & Korean Cuisine**", "Red Ninja Sushi & Korean Cuisine"),
        ("*Simply Thai*", "Simply Thai"),
        ("__Gondola House__", "Gondola House"),
        ("**_Crooked Creek Greenway_**", "Crooked Creek Greenway"),
        ("A **bold** word", "A bold word"),
        ("plain name", "plain name"),
        ("", ""),
    ],
)
def test_paired_emphasis_is_removed(raw, expected):
    assert strip_markdown_emphasis(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "M*A*S*H",            # pairs, but joining would fuse word characters
        "Rock_n_Roll",
        "unmatched **start",  # no partner
        "****",               # degenerate: emptying a name is worse than leaving it
    ],
)
def test_names_that_must_survive_untouched(raw):
    """Conservative by design: a name is never silently rewritten."""
    assert strip_markdown_emphasis(raw) == raw


def test_applies_through_nested_payloads():
    """One call at the payload boundary must cover every category."""
    payload = {
        "dinner_recommendations": [{"name": "**A**"}, {"name": "B"}],
        "top_attractions": [{"name": "*C*", "description": "no **change** here?"}],
        "count": 3,
        "flag": True,
    }
    out = strip_markdown_emphasis(payload)
    assert out["dinner_recommendations"][0]["name"] == "A"
    assert out["top_attractions"][0]["name"] == "C"
    assert out["top_attractions"][0]["description"] == "no change here?"
    assert out["count"] == 3 and out["flag"] is True


@pytest.mark.parametrize(
    "removed, kept, expected",
    [
        (10, 3, "10 of 13 (77%)"),   # the Old Hickory case: the section is gone
        (2, 40, "2 of 42 (5%)"),     # same count, entirely different meaning
        (4, 0, "4 of 4 (100%)"),
        (0, 0, "0"),                 # no denominator to report
    ],
)
def test_removal_ratio_gives_the_denominator(removed, kept, expected):
    assert _format_removal_ratio(removed, kept) == expected


def test_names_from_url_discovery_are_stripped_too():
    """Regression: names arrive from TWO sources, not one.

    The first fix stripped only ai_content's payload. url_discovery's
    direct-link batch harvests its own names AFTERWARDS, and those shipped
    "**Flat Tire Diner**" to the page in the 2026-08-25 rerun that was supposed
    to prove the fix. normalize_trip_content is the only point downstream of
    every source.
    """
    from generator.ai_content import AIContentGenerator

    trip = {
        "destinations": [
            {
                "name": "Old Hickory, Tennessee",
                "ai_content": {
                    "dinner_recommendations": [{"name": "**Flat Tire Diner**", "url": "https://x"}],
                    "top_attractions": [{"name": "**Crooked Creek Greenway**", "url": "https://y"}],
                    "getting_here": {"en_route_stops": [{"name": "__Old Hickory Dam__"}]},
                },
            }
        ]
    }
    gen = AIContentGenerator.__new__(AIContentGenerator)
    gen._config = {}          # normalize_trip_content reads config for later steps
    gen.normalize_trip_content(trip)

    ai = trip["destinations"][0]["ai_content"]
    assert ai["dinner_recommendations"][0]["name"] == "Flat Tire Diner"
    assert ai["top_attractions"][0]["name"] == "Crooked Creek Greenway"
    assert ai["getting_here"]["en_route_stops"][0]["name"] == "Old Hickory Dam"
