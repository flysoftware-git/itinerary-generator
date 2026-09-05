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


class TestPwaManifest:
    """The installed app's identity, which is separate from the <head>.

    short_name was title[:24], ignoring the manifest's own short_name -- so the
    field added to fix the HTML meta was honoured in one place and not the
    other. The icons hard-coded the Southwest terracotta, so every installed
    itinerary got a terracotta home-screen icon whatever its manifest said.
    """

    @staticmethod
    def _build(trip):
        import json, pathlib, tempfile

        from generator.main import _write_pwa_assets

        out = pathlib.Path(tempfile.mkdtemp())
        _write_pwa_assets(out, trip)
        return json.loads((out / "manifest.webmanifest").read_text(encoding="utf-8"))

    def test_an_explicit_short_name_is_used(self):
        m = self._build({"trip": {"title": "Southwest Road Trip", "short_name": "SW Road Trip",
                                  "subtitle": "Utah", "theme_color": "#C0623E"}, "destinations": []})
        assert m["name"] == "Southwest Road Trip"
        assert m["short_name"] == "SW Road Trip"

    def test_without_one_it_falls_back_to_the_title(self):
        m = self._build({"trip": {"title": "Europe Exploration", "subtitle": "By rail",
                                  "theme_color": "#3A5F8A"}, "destinations": []})
        assert m["short_name"] == "Europe Exploration"

    def test_icons_take_the_trips_theme_colour(self):
        """Every installed itinerary had a terracotta icon regardless of manifest."""
        m = self._build({"trip": {"title": "Europe Exploration", "subtitle": "By rail",
                                  "theme_color": "#3A5F8A"}, "destinations": []})
        hexes = {i["src"].split("%23")[1][:6] for i in m["icons"] if "%23" in i["src"]}
        assert hexes == {"3A5F8A"}
        assert "C0623E" not in json.dumps(m) if (json := __import__("json")) else True

    def test_a_manifest_with_no_theme_colour_still_builds(self):
        m = self._build({"trip": {"title": "T", "subtitle": "S"}, "destinations": []})
        assert m["short_name"]
        assert m["icons"]
