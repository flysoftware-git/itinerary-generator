"""The icon an installed guide shows, and the manifest key that sets it.

The icon was a map emoji drawn twice -- once into `manifest.webmanifest`, once
inline in the template's `<link rel="icon">`. A publisher who puts their own
name on a guide (`trip.brand`) had no way to put their own icon on it, and the
two copies could drift without anything noticing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote

import pytest

from generator import app_icon
from generator.main import _write_pwa_assets
from generator.manifest_parser import ManifestParser

SVG = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><circle cx='32' cy='32' r='30'/></svg>"

MANIFEST = """
trip:
  title: Southwest Road Trip
  subtitle: Utah and Arizona
  theme_color: "#C0623E"
{brand}
destinations:
  - id: moab
    name: Moab
    dates: "May 1-2"
    planning_links:
      - label: Arches
        url: https://www.nps.gov/arch/
"""


def _manifest(tmp_path: Path, brand: str = "") -> Path:
    path = tmp_path / "trip_manifest.yaml"
    path.write_text(MANIFEST.format(brand=brand), encoding="utf-8")
    return path


def _icon_block(icon: str) -> str:
    return f"  brand:\n    distributor: Acme Travel\n    icon: {icon}\n"


def _manifest_json(tmp_path: Path, trip: dict) -> dict:
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    _write_pwa_assets(out, trip)
    return json.loads((out / "manifest.webmanifest").read_text(encoding="utf-8"))


# ------------------------------------------------------- the default stands


def test_a_manifest_that_names_no_icon_installs_exactly_what_it_used_to(tmp_path):
    """The emoji moved behind a function; it must not have been redrawn."""
    trip = {"trip": {"title": "T", "subtitle": "S", "theme_color": "#3A5F8A"}, "destinations": []}
    icons = {i["sizes"]: i["src"] for i in _manifest_json(tmp_path, trip)["icons"]}
    assert icons["192x192"] == (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 192 192'%3E"
        "%3Crect width='192' height='192' rx='36' fill='%233A5F8A'/%3E"
        "%3Ctext x='50%25' y='54%25' font-size='110' text-anchor='middle' dominant-baseline='middle'%3E"
        "%F0%9F%97%BA%EF%B8%8F%3C/text%3E%3C/svg%3E"
    )
    assert "rx='96'" in icons["512x512"] and "font-size='300'" in icons["512x512"]


def test_the_default_still_takes_the_trips_colour(tmp_path):
    trip = {"trip": {"title": "T", "subtitle": "S", "theme_color": "#1B7F5A"}, "destinations": []}
    srcs = json.dumps(_manifest_json(tmp_path, trip)["icons"])
    assert "%231B7F5A" in srcs and "C0623E" not in srcs


def test_a_trip_with_no_colour_at_all_still_gets_an_icon(tmp_path):
    trip = {"trip": {"title": "T", "subtitle": "S"}, "destinations": []}
    assert all(i["src"].startswith("data:image/svg+xml,") for i in _manifest_json(tmp_path, trip)["icons"])


# --------------------------------------------------------- a manifest's own


def test_an_svg_beside_the_manifest_becomes_the_icon(tmp_path):
    (tmp_path / "logo.svg").write_text(SVG, encoding="utf-8")
    trip = ManifestParser().parse(_manifest(tmp_path, _icon_block("logo.svg")))

    icon = trip["trip"]["brand"]["icon"]
    assert icon.startswith("data:image/svg+xml,")
    assert "<circle" in unquote(icon)
    # Both manifest entries, and the head, are that one file.
    assert {i["src"] for i in _manifest_json(tmp_path, trip)["icons"]} == {icon}
    assert app_icon.icon_for(trip, 192) == icon
    assert not app_icon.is_default(trip)


def test_a_data_uri_is_taken_as_it_stands(tmp_path):
    uri = "data:image/svg+xml,%3Csvg%3E%3C/svg%3E"
    trip = ManifestParser().parse(_manifest(tmp_path, _icon_block(uri)))
    assert trip["trip"]["brand"]["icon"] == uri


def test_the_head_and_the_web_manifest_get_the_same_icon(tmp_path):
    """They were the same emoji written out twice, in two files."""
    (tmp_path / "logo.svg").write_text(SVG, encoding="utf-8")
    trip = ManifestParser().parse(_manifest(tmp_path, _icon_block("logo.svg")))
    head = Path("templates/v2.5_template.html").read_text(encoding="utf-8")

    # Two placeholders rather than one: `apple-touch-icon` is iOS's, and takes
    # the PNG when a manifest carries one (`tests/test_touch_icon.py`).
    assert head.count("<!--APP_ICON-->") == 1, "the head no longer draws its own icon"
    links = [line for line in head.splitlines() if "rel=\"icon\"" in line or "apple-touch-icon" in line]
    assert len(links) == 2, links
    assert all("<!--APP_ICON-->" in line or "<!--APP_TOUCH_ICON-->" in line for line in links), links
    assert not any("svg" in line and "%23" in line for line in links), "an icon is still drawn inline"
    assert app_icon.icon_for(trip, 192) == app_icon.icon_for(trip, 512)


# ------------------------------------------------------------ what it refuses


@pytest.mark.parametrize("icon,expected", [
    ("logo.png", "has to be a vector"),
    ("data:image/png;base64,iVBORw0KGgo=", "data:image/svg+xml"),
    ("missing.svg", "no file at"),
])
def test_an_icon_that_cannot_serve_every_size_is_refused_at_parse(tmp_path, icon, expected):
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(ValueError, match=re.escape(expected)):
        ManifestParser().parse(_manifest(tmp_path, _icon_block(icon)))


def test_an_svg_carrying_a_script_is_refused(tmp_path):
    """It is inlined into the head of a page that gets published."""
    (tmp_path / "bad.svg").write_text("<svg><script>alert(1)</script></svg>", encoding="utf-8")
    with pytest.raises(ValueError, match="script"):
        ManifestParser().parse(_manifest(tmp_path, _icon_block("bad.svg")))


def test_a_file_too_large_to_inline_is_refused_with_its_size(tmp_path):
    (tmp_path / "huge.svg").write_text("<svg>" + "x" * (256 * 1024) + "</svg>", encoding="utf-8")
    with pytest.raises(ValueError, match="the limit is 256 KB"):
        ManifestParser().parse(_manifest(tmp_path, _icon_block("huge.svg")))


def test_a_file_that_is_not_an_svg_at_all_is_refused(tmp_path):
    (tmp_path / "notes.svg").write_text("just some text", encoding="utf-8")
    with pytest.raises(ValueError, match="does not contain an <svg> element"):
        ManifestParser().parse(_manifest(tmp_path, _icon_block("notes.svg")))


def test_an_unknown_brand_key_is_still_refused(tmp_path):
    """`additionalProperties: False` held before the icon key was added."""
    with pytest.raises(ValueError):
        ManifestParser().parse(_manifest(tmp_path, "  brand:\n    emblem: logo.svg\n"))
