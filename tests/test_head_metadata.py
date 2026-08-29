"""The <head> must describe THIS trip, not the first one the project built.

Reported 2026-08-28 from the published Europe build: the body was correct
Europe content while the head still read "Southwest Road Trip Itinerary".
Four values were hard-coded in the template -- the <title>, both PWA app
names, and the meta description -- so every itinerary this generator has
produced announced itself as the Southwest trip in the browser tab, the
home-screen icon label, and every link preview.
"""
import re

import pytest

from generator.html_assembler import HTMLAssembler

EUROPE = {
    "trip": {
        "title": "Europe Exploration",
        "subtitle": "Five capitals by rail — Brussels to Frankfurt",
    },
    "destinations": [
        {"name": "Brussels, Belgium"},
        {"name": "Amsterdam, Netherlands"},
        {"name": "Berlin, Germany"},
    ],
}


class TestDescription:
    def test_the_manifest_subtitle_wins(self):
        """It is the author's own one-line summary, better than anything derived."""
        out = HTMLAssembler._build_trip_description(EUROPE)
        assert out.startswith("Europe Exploration —")
        assert "Five capitals by rail" in out

    def test_falls_back_to_the_city_list(self):
        trip = {"trip": {"title": "Southwest Road Trip"},
                "destinations": [{"name": "Zion National Park"}, {"name": "Moab, Utah"}]}
        assert HTMLAssembler._build_trip_description(trip) == (
            "Southwest Road Trip — Zion National Park, Moab"
        )

    def test_the_country_is_dropped_from_each_stop(self):
        """A meta description is truncated near 155 characters; repeating the
        country for every stop spends that budget on nothing."""
        trip = {"trip": {"title": "T"}, "destinations": [{"name": "Brussels, Belgium"}]}
        assert HTMLAssembler._build_trip_description(trip) == "T — Brussels"

    @pytest.mark.parametrize("trip", [{}, {"trip": {}}, {"trip": {"title": ""}}])
    def test_a_manifest_with_no_title_still_produces_something(self, trip):
        assert HTMLAssembler._build_trip_description(trip) == "Trip Itinerary"


class TestNoSouthwestLeftInTheTemplate:
    """The regression this file exists for."""

    def test_the_template_hardcodes_no_trip_identity(self):
        from pathlib import Path

        head = Path("templates/v2.5_template.html").read_text(encoding="utf-8").split("</head>")[0]
        assert "SW Road Trip" not in head
        assert "Southwest Road Trip Itinerary" not in head
        assert "Zion, Bryce, Capitol Reef" not in head

    def test_the_head_placeholders_are_present(self):
        from pathlib import Path

        head = Path("templates/v2.5_template.html").read_text(encoding="utf-8").split("</head>")[0]
        for placeholder in ("<!--DOCUMENT_TITLE-->", "<!--APP_SHORT_NAME-->", "<!--TRIP_DESCRIPTION-->"):
            assert placeholder in head, placeholder

    def test_every_head_placeholder_is_substituted(self):
        """A placeholder the assembler forgets renders as a literal comment --
        which is how the theme colour silently pinned every trip to terracotta."""
        import inspect

        source = inspect.getsource(HTMLAssembler)
        for placeholder in ("<!--DOCUMENT_TITLE-->", "<!--APP_SHORT_NAME-->", "<!--TRIP_DESCRIPTION-->"):
            assert f'"{placeholder}"' in source, placeholder
