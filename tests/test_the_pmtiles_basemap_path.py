"""The `pmtiles` basemap path, and what the shipped default may point at.

WHY THIS FILE EXISTS
--------------------
A guide opened from disk -- which is what an emailed guide is -- has every tile
replaced by OpenStreetMap's "App is not following the tile usage policy" image.
Measured 2026-10-16: a page served over https with a real Referer was fine and
the same page opened as `file://` was refused. The `map.tiles` comment has
warned about exactly this since the section was written, and no amount of
configuration makes a distributed file comply with a policy aimed at it.

So the engine supports `kind: pmtiles`, pointing at a vector archive the
operator hosts. **That code path had never been run by any build** -- the
capability existed and was unexercised -- which is what the second class here
covers.

WHAT THE FIRST CLASS PINS, AND WHY IT IS THE INVERSE OF WHAT IT USED TO
-----------------------------------------------------------------------
This file previously asserted that the shipped `config.yaml` pointed at one
specific hosted archive, and was named for it. That was the wrong invariant for
a public project in two ways, and both are the kind that only show up from
outside:

1. **It made every clone of this repository fetch tiles from a service this
   repository does not operate** -- somebody else's bucket, somebody else's
   bill, and a hard dependency nobody downstream agreed to.
2. **The archive behind it covered three regions.** A default basemap that
   draws nothing outside the regions its host happens to have cut is worse
   than a default that draws everywhere, and the renderer does not say so --
   it simply draws no tiles.

So what is pinned now is the inverse: **the shipped default is a basemap that
works out of the box and names no third-party hosted service.** An operator who
wants their own archive sets `kind: pmtiles` and a `url`, which is what the rest
of this file tests.

WHAT IS DELIBERATELY NOT PINNED
-------------------------------
That any archive is reachable. A test that fetches a URL fails on a flight, on a
CI box without egress, and on the day somebody's DNS is slow -- and it would be
testing a CDN rather than this repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from generator.html_assembler import (
    DEFAULT_TILE_URL,
    PMTILES_FLAVORS,
    PMTILES_LANGS,
    TILE_KINDS,
    _tile_layer_js,
    _tile_renderer_script,
    _tile_settings,
)

CONFIG = yaml.safe_load(
    (Path(__file__).resolve().parent.parent / "config.yaml").read_text(encoding="utf-8")
)
SHIPPED = (CONFIG.get("map") or {}).get("tiles") or {}

#: An operator's archive, standing in for a real one. The hostname is
#: `example.com` on purpose: a test that names a live service makes that service
#: a dependency of this test suite, which is the mistake this file is correcting.
ARCHIVE = {
    "kind": "pmtiles",
    "url": "https://example.com/tiles/north-america.pmtiles",
    "flavor": "light",
    "lang": "en",
    "attribution": "© OpenStreetMap contributors",
    "max_zoom": 12,
}


class TestTheShippedDefault:
    """What a fresh clone gets, and what it must not be."""

    def test_it_names_no_hosted_third_party_service(self):
        """The invariant that replaces the one this file used to assert.

        A URL in the shipped config is a service every clone fetches from. This
        project may ship OSMF's volunteer servers -- they are the commons this
        data comes from, and the attribution says so -- or it may ship nothing.
        It may not ship somebody's private bucket.
        """
        url = str(SHIPPED.get("url") or "")
        assert url, "the shipped config must name a basemap source"
        allowed = ("tile.openstreetmap.org", "tile.osm.org")
        assert any(host in url for host in allowed), (
            f"the shipped basemap url points at {url!r}, which is neither "
            f"OSMF's servers nor absent. A default that fetches from a service "
            f"this repository does not operate makes it a dependency of every "
            f"clone; host your own archive via `kind: pmtiles` in your own "
            f"config instead."
        )

    def test_it_draws_everywhere_rather_than_in_some_regions(self):
        """A raster default is global. A hosted archive is whatever was cut, and
        a default that silently draws no tiles outside three regions is a worse
        failure than a slow tile server, because nothing reports it."""
        assert SHIPPED.get("kind", "raster") == "raster"
        assert str(SHIPPED.get("url", "")).count("{z}") == 1

    def test_the_attribution_credits_openstreetmap(self):
        """ODbL follows the data, not the host. Whichever source serves the
        tiles, the credit is owed."""
        assert "OpenStreetMap" in SHIPPED.get("attribution", "")

    def test_no_credential_rides_in_the_url(self):
        """A guide gets emailed, so anything in this url is given away."""
        url = str(SHIPPED.get("url") or "").lower()
        for secret in ("key=", "token=", "apikey", "api_key", "signature=",
                       "x-amz-", "access_key"):
            assert secret not in url


class TestAnOperatorArchiveProducesTheVectorLayer:
    """The emission path an operator reaches by setting `kind: pmtiles`.

    Unchanged in substance from when this file pinned one specific archive: the
    tests were always about the code path, not about whose bucket it read.
    """

    def test_the_kind_is_one_the_parser_knows(self):
        assert ARCHIVE["kind"] in TILE_KINDS

    def test_the_url_is_an_archive_and_not_a_raster_template(self):
        """A PMTiles url names one file. `{z}/{x}/{y}` here would mean somebody
        pasted a raster template into a vector config."""
        url = ARCHIVE["url"]
        assert url.startswith("https://")
        assert url.endswith(".pmtiles")
        for placeholder in ("{z}", "{x}", "{y}", "{s}"):
            assert placeholder not in url

    def test_flavor_and_lang_are_values_the_pinned_renderer_knows(self):
        """protomaps-leaflet does not reject an unknown language -- it quietly
        draws the labels as Latin script -- which is why these are checked here
        and not left to the browser."""
        assert ARCHIVE["flavor"] in PMTILES_FLAVORS
        assert ARCHIVE["lang"] in PMTILES_LANGS

    def test_the_settings_survive_the_parser(self):
        tiles = _tile_settings({"map": {"tiles": ARCHIVE}})

        assert tiles.kind == "pmtiles"
        assert tiles.url == ARCHIVE["url"]
        assert tiles.max_zoom == 12

    def test_the_emitted_layer_is_protomaps_not_raster(self):
        js = _tile_layer_js(_tile_settings({"map": {"tiles": ARCHIVE}}))

        assert "protomapsL.leafletLayer" in js
        assert "L.tileLayer(" not in js
        assert ARCHIVE["url"] in js

    def test_the_renderer_script_is_included(self):
        """Raster pages get no extra script tag; a vector page must."""
        script = _tile_renderer_script(_tile_settings({"map": {"tiles": ARCHIVE}}))

        assert "protomaps-leaflet" in script
        assert script.strip().startswith("<script")

    def test_it_is_guarded_so_a_failed_renderer_still_draws_the_route(self):
        """If the CDN script fails, the markers and the route must still render
        -- which is what kept the itinerary readable through the blocked OSM
        tiles in the first place."""
        js = _tile_layer_js(_tile_settings({"map": {"tiles": ARCHIVE}}))

        assert "typeof protomapsL" in js

    def test_it_does_not_fall_back_to_openstreetmap(self):
        """Stated in `_tile_layer_js`'s own docstring: a quiet fallback would
        send traffic to donated infrastructure on exactly the day an operator's
        own archive broke."""
        js = _tile_layer_js(_tile_settings({"map": {"tiles": ARCHIVE}}))

        assert "tile.openstreetmap.org" not in js


@pytest.mark.parametrize("bad_url", [
    "https://example.com/tiles/north-america.pmtiles?key=abc123",
    "ftp://example.com/tiles/north-america.pmtiles",
])
def test_a_bad_archive_url_does_not_silently_become_openstreetmap(bad_url):
    """`_tile_settings` reverts to the raster default when a pmtiles url is not
    http(s). That is the right behaviour and it is quiet, so this records which
    inputs take that path rather than leaving it to be discovered on a build."""
    tiles = _tile_settings({"map": {"tiles": {**ARCHIVE, "url": bad_url}}})

    if bad_url.startswith("ftp://"):
        assert tiles.kind == "raster" and tiles.url == DEFAULT_TILE_URL
    else:
        assert tiles.kind == "pmtiles"
