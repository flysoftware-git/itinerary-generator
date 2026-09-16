"""The icon iOS actually reads, which is not the one the manifest names.

`trip.brand.icon` is an SVG because one vector answers for the 192 and 512
manifest entries and the favicon. iOS has never taken an SVG in
`apple-touch-icon`: Safari ignores the link and an added-to-home-screen guide
gets a screenshot of the page. So the platform where a home-screen icon matters
most was the one that key could not reach.
"""

from __future__ import annotations

import base64
import struct
import zlib
from pathlib import Path

import pytest

from generator import app_icon
from generator.manifest_parser import ManifestParser

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

SVG = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><circle cx='32' cy='32' r='30'/></svg>"


def png_bytes(width: int, height: int) -> bytes:
    """A real PNG of a given size, without an image library.

    Written out rather than checked in: the test is about the size in the
    header, and a fixture file would hide the one number it turns on.
    """
    raw = b"".join(b"\x00" + b"\x8b\x2e\x0a" * width for _ in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def parse(tmp_path: Path, brand: str):
    path = tmp_path / "trip_manifest.yaml"
    path.write_text(MANIFEST.format(brand=brand), encoding="utf-8")
    return ManifestParser().parse(path)


def brand_block(**keys: str) -> str:
    lines = "".join(f"    {k}: {v}\n" for k, v in keys.items())
    return "  brand:\n    distributor: Acme Travel\n" + lines


# ----------------------------------------------------------- what iOS gets


def test_without_a_png_nothing_changes_for_anyone(tmp_path):
    (tmp_path / "logo.svg").write_text(SVG, encoding="utf-8")
    trip = parse(tmp_path, brand_block(icon="logo.svg"))
    assert app_icon.touch_icon_for(trip) == app_icon.icon_for(trip, 192)


def test_a_png_beside_the_manifest_is_what_ios_is_given(tmp_path):
    (tmp_path / "logo.svg").write_text(SVG, encoding="utf-8")
    (tmp_path / "touch.png").write_bytes(png_bytes(180, 180))
    trip = parse(tmp_path, brand_block(icon="logo.svg", icon_png="touch.png"))

    touch = app_icon.touch_icon_for(trip)
    assert touch.startswith("data:image/png;base64,")
    assert base64.b64decode(touch.split(",", 1)[1]).startswith(b"\x89PNG")
    # Everyone else still gets the vector: this key is one platform's.
    assert app_icon.icon_for(trip, 192).startswith("data:image/svg+xml,")
    assert app_icon.icon_for(trip, 512) != touch


def test_the_png_can_stand_alone_without_an_svg(tmp_path):
    """A publisher with one raster should not be forced to draw a vector too."""
    (tmp_path / "touch.png").write_bytes(png_bytes(180, 180))
    trip = parse(tmp_path, brand_block(icon_png="touch.png"))
    assert app_icon.touch_icon_for(trip).startswith("data:image/png;base64,")
    assert app_icon.is_default(trip), "the SVG side is untouched"


def test_a_data_uri_is_taken_and_still_checked(tmp_path):
    good = "data:image/png;base64," + base64.b64encode(png_bytes(180, 180)).decode()
    trip = parse(tmp_path, brand_block(icon_png=good))
    assert trip["trip"]["brand"]["icon_png"] == good

    bad = "data:image/png;base64," + base64.b64encode(png_bytes(120, 120)).decode()
    with pytest.raises(ValueError, match="120x120"):
        parse(tmp_path, brand_block(icon_png=bad))


def test_the_head_asks_for_the_two_icons_separately(tmp_path):
    """One placeholder for both would hand iOS the SVG again."""
    head = Path("templates/v2.5_template.html").read_text(encoding="utf-8")
    assert "<!--APP_TOUCH_ICON-->" in head
    touch = [ln for ln in head.splitlines() if "apple-touch-icon" in ln]
    assert len(touch) == 1 and "<!--APP_TOUCH_ICON-->" in touch[0], touch


# ------------------------------------------------------------ what it refuses


@pytest.mark.parametrize("size,expected", [((120, 120), "120x120"), ((180, 200), "180x200")])
def test_a_png_that_is_not_the_size_ios_asks_for_is_refused(tmp_path, size, expected):
    (tmp_path / "touch.png").write_bytes(png_bytes(*size))
    with pytest.raises(ValueError, match=expected):
        parse(tmp_path, brand_block(icon_png="touch.png"))


def test_a_file_that_is_not_a_png_is_refused_by_its_bytes(tmp_path):
    """The extension is a claim; the signature is the fact."""
    (tmp_path / "touch.png").write_bytes(b"GIF89a and the rest of a gif")
    with pytest.raises(ValueError, match="not a PNG"):
        parse(tmp_path, brand_block(icon_png="touch.png"))


def test_an_svg_named_as_the_png_key_is_refused(tmp_path):
    (tmp_path / "logo.svg").write_text(SVG, encoding="utf-8")
    with pytest.raises(ValueError, match="give a .png file"):
        parse(tmp_path, brand_block(icon_png="logo.svg"))


def test_a_missing_png_names_the_directory_it_was_looked_for_in(tmp_path):
    with pytest.raises(ValueError, match="no file at"):
        parse(tmp_path, brand_block(icon_png="absent.png"))


# ------------------------------------------------- what the SVG now refuses


@pytest.mark.parametrize("markup", [
    "<svg onload='fetch(1)'></svg>",
    "<svg><use href='https://example.com/x.svg#a'/></svg>",
    "<svg><style>@import url(https://example.com/x.css);</style></svg>",
    "<svg><image href='https://example.com/x.png'/></svg>",
])
def test_an_svg_that_reaches_outside_itself_is_refused(tmp_path, markup):
    """The reason given was that the artwork is inlined into a published page.

    A browser runs none of these in an icon slot today, so this is the list the
    stated reason implies rather than a live hole -- and it is the list that
    matters the day the same file is rendered in the document body.
    """
    (tmp_path / "logo.svg").write_text(markup, encoding="utf-8")
    with pytest.raises(ValueError, match="refused"):
        parse(tmp_path, brand_block(icon="logo.svg"))
