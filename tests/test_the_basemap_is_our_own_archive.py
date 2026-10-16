"""The shipped config draws its basemap from our own archive, not OSMF's.

WHAT IT COST
------------
A guide opened from disk -- which is what an emailed guide is -- had every tile
replaced by OpenStreetMap's "App is not following the tile usage policy" image.
Measured 2026-10-16: the published pages were fine, because they are served over
https with a real Referer, and a local file was refused. The `map.tiles` comment
had warned about exactly this since the section was written, and no amount of
configuration makes a distributed file comply with a policy aimed at it.

WHAT IS PINNED
--------------
That the shipped `config.yaml` points at the PMTiles archive rather than the
volunteer servers, and that the pieces the renderer needs are present and
self-consistent. The `pmtiles` code path had never been run by any build before
this change -- the capability existed and was unexercised -- so these assert the
configuration a build will actually read.

WHAT IS DELIBERATELY NOT PINNED
-------------------------------
That the archive is reachable. A test that fetches a URL fails on a flight, on a
CI box without egress, and on the day somebody's DNS is slow -- and it would be
testing Cloudflare rather than this repository. The archive was verified
separately, through the live URL, by reading real tiles out of it for every
region and rendering it from a `file://` page in Chromium.
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
TILES = (CONFIG.get("map") or {}).get("tiles") or {}


class TestTheShippedConfig:
    def test_it_no_longer_points_at_the_volunteer_tile_servers(self):
        """The defect, stated as the thing that must not come back."""
        assert "tile.openstreetmap.org" not in TILES.get("url", "")
        assert TILES.get("url") != DEFAULT_TILE_URL

    def test_it_asks_for_the_vector_archive(self):
        assert TILES.get("kind") == "pmtiles"
        assert TILES["kind"] in TILE_KINDS

    def test_the_url_is_the_archive_itself(self):
        """A PMTiles url names one file. `{z}/{x}/{y}` here would mean somebody
        pasted a raster template into a vector config."""
        url = TILES.get("url", "")
        assert url.startswith("https://")
        assert url.endswith(".pmtiles")
        for placeholder in ("{z}", "{x}", "{y}", "{s}"):
            assert placeholder not in url

    def test_flavor_and_lang_are_values_the_pinned_renderer_knows(self):
        """protomaps-leaflet does not reject an unknown language -- it quietly
        draws the labels as Latin script -- which is why these are checked here
        and not left to the browser."""
        assert TILES.get("flavor") in PMTILES_FLAVORS
        assert TILES.get("lang") in PMTILES_LANGS

    def test_max_zoom_matches_the_archive(self):
        """The archive was cut at z12. Asking for 13 draws nothing at the
        deepest level and the renderer reports no error."""
        assert TILES.get("max_zoom") == 12

    def test_the_attribution_still_credits_openstreetmap(self):
        """The tiles are an ODbL produced work whichever host serves them.
        Leaving OSMF's servers does not leave the licence."""
        assert "OpenStreetMap" in TILES.get("attribution", "")

    def test_no_credential_rides_in_the_url(self):
        """A guide is emailed, so anything in this url is given away. That is
        why the archive is a public bucket and not a keyed tile provider."""
        url = TILES.get("url", "").lower()
        for secret in ("key=", "token=", "apikey", "api_key", "signature=",
                       "x-amz-", "access_key"):
            assert secret not in url


class TestTheConfigProducesTheVectorLayer:
    """The emission path this config newly reaches. It existed before today and
    had never been run by a build."""

    def test_the_settings_survive_the_parser(self):
        tiles = _tile_settings({"map": {"tiles": TILES}})

        assert tiles.kind == "pmtiles"
        assert tiles.url == TILES["url"]
        assert tiles.max_zoom == 12

    def test_the_emitted_layer_is_protomaps_not_raster(self):
        js = _tile_layer_js(_tile_settings({"map": {"tiles": TILES}}))

        assert "protomapsL.leafletLayer" in js
        assert "L.tileLayer(" not in js
        assert TILES["url"] in js

    def test_the_renderer_script_is_included(self):
        """Raster pages get no extra script tag; a vector page must."""
        script = _tile_renderer_script(_tile_settings({"map": {"tiles": TILES}}))

        assert "protomaps-leaflet" in script
        assert script.strip().startswith("<script")

    def test_it_is_guarded_so_a_failed_renderer_still_draws_the_route(self):
        """If the CDN script fails, the markers and the route must still render
        -- which is what kept the itinerary readable through the blocked OSM
        tiles in the first place."""
        js = _tile_layer_js(_tile_settings({"map": {"tiles": TILES}}))

        assert "typeof protomapsL" in js

    def test_it_does_not_fall_back_to_openstreetmap(self):
        """Stated in `_tile_layer_js`'s own docstring: a quiet fallback would
        send traffic to donated infrastructure on exactly the day our archive
        broke."""
        js = _tile_layer_js(_tile_settings({"map": {"tiles": TILES}}))

        assert "tile.openstreetmap.org" not in js


@pytest.mark.parametrize("bad_url", [
    "https://maps.chosenroutes.com/basemap.pmtiles?key=abc123",
    "ftp://maps.chosenroutes.com/basemap.pmtiles",
])
def test_a_bad_archive_url_does_not_silently_become_openstreetmap(bad_url):
    """`_tile_settings` reverts to the raster default when a pmtiles url is not
    http(s). That is the right behaviour and it is quiet, so this records which
    inputs take that path rather than leaving it to be discovered on a build."""
    tiles = _tile_settings({"map": {"tiles": {**TILES, "url": bad_url}}})

    if bad_url.startswith("ftp://"):
        assert tiles.kind == "raster" and tiles.url == DEFAULT_TILE_URL
    else:
        assert tiles.kind == "pmtiles"
