"""The icon an installed itinerary shows on a home screen.

The icon was a map emoji on a rectangle of the trip's theme colour, written
out twice -- once into `manifest.webmanifest` by `main._write_pwa_assets`, and
once inline in the template's `<link rel="icon">`. That is fine as a default
and impossible to change: a publisher who puts their own name on a guide had
no way to put their own icon on it, and the two copies could drift apart
without anything noticing.

So the emoji moves here, behind one function, and a manifest may name an icon
of its own under `trip.brand.icon` -- the same block that already carries the
distributor credit, because this is the same kind of fact about who published
the guide.

**SVG only, deliberately.** One file has to answer for both the 192 and the
512 entry in the web manifest and for the favicon; a raster image cannot,
and an icon declared at a size it is not is worse than no icon. The manifest
parser resolves a path into a `data:` URI before anything here sees it, so a
guide stays one self-contained file with no second request to make offline.
"""

from __future__ import annotations

from typing import Any

#: The default face: a map, on the trip's own colour.
DEFAULT_GLYPH = "%F0%9F%97%BA%EF%B8%8F"

DEFAULT_THEME_HEX = "C0623E"


def theme_hex(trip: dict[str, Any]) -> str:
    """The trip's theme colour as bare hex, for a `data:` URI.

    A `#` inside one has to stay percent-encoded as `%23`, so the colour is
    carried without it and written back as `%23{hex}`.
    """
    meta = trip.get("trip", {}) if isinstance(trip, dict) else {}
    colour = str(meta.get("theme_color", "") or "").strip()
    return colour.lstrip("#") or DEFAULT_THEME_HEX


#: Corner radius and glyph size per icon size, as they were written inline
#: before this module existed. Kept as measurements rather than derived from a
#: ratio: 512 used a glyph of 300 where the 192 icon's ratio gives 293, and a
#: refactor that quietly redraws the icon it was meant to move is not a
#: refactor. Any other size falls back to the 192 icon's proportions.
_DRAWN = {192: (36, 110), 512: (96, 300)}


def default_icon(hex_colour: str, size: int) -> str:
    """The map-emoji icon, at one of the manifest's sizes."""
    radius, font = _DRAWN.get(size, (round(size * 36 / 192), round(size * 110 / 192)))
    return (
        f"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {size} {size}'%3E"
        f"%3Crect width='{size}' height='{size}' rx='{radius}' fill='%23{hex_colour}'/%3E"
        f"%3Ctext x='50%25' y='54%25' font-size='{font}' text-anchor='middle' "
        f"dominant-baseline='middle'%3E{DEFAULT_GLYPH}%3C/text%3E%3C/svg%3E"
    )


def icon_for(trip: dict[str, Any], size: int) -> str:
    """The icon this trip should install with, as a `data:` URI.

    `size` is ignored for a manifest's own icon: an SVG is the same file at
    every size, which is why only SVG is accepted (module docstring).
    """
    meta = trip.get("trip", {}) if isinstance(trip, dict) else {}
    brand = meta.get("brand") if isinstance(meta.get("brand"), dict) else {}
    chosen = str((brand or {}).get("icon", "") or "").strip()
    if chosen:
        return chosen
    return default_icon(theme_hex(trip), size)


def touch_icon_for(trip: dict[str, Any]) -> str:
    """The icon for `apple-touch-icon`, which is the one iOS reads.

    `trip.brand.icon_png` when the manifest carries one, because iOS ignores
    an SVG here and shows a screenshot of the page instead. Otherwise the same
    answer as everywhere else: what a guide has always installed with on that
    platform, which is nothing on iOS and the SVG everywhere else.
    """
    meta = trip.get("trip", {}) if isinstance(trip, dict) else {}
    brand = meta.get("brand") if isinstance(meta.get("brand"), dict) else {}
    png = str((brand or {}).get("icon_png", "") or "").strip()
    return png or icon_for(trip, 192)


def is_default(trip: dict[str, Any]) -> bool:
    """Whether this trip installs with the stock icon."""
    meta = trip.get("trip", {}) if isinstance(trip, dict) else {}
    brand = meta.get("brand") if isinstance(meta.get("brand"), dict) else {}
    return not str((brand or {}).get("icon", "") or "").strip()
