"""Published pages link images at their source, and still work offline.

The local `images/<hash>.jpg` directory beside the HTML is the generator's
download-avoidance cache; it was never the delivery mechanism. Emitting it
meant every published build shipped its own copy of NPS/Unsplash/Wikimedia
assets -- 37 files under sw/prod/images/, one of them 33 MB -- and rehosted
third-party images rather than linking them.

Owner decision 2026-08-24, option B: point at the source AND have the
service worker precache them, so offline use survives the change.
"""
import json
from pathlib import Path
from unittest.mock import patch

from generator.html_assembler import _image_href


class TestImageHref:
    def test_prefers_the_source_url(self):
        img = {"url": "https://www.nps.gov/common/uploads/x.jpg", "local_path": "/out/images/abc.jpg"}
        assert _image_href(img) == "https://www.nps.gov/common/uploads/x.jpg"

    def test_falls_back_to_the_cache_path_when_no_source(self):
        """A record missing its url degrades to the old behaviour rather than
        to no image at all."""
        assert _image_href({"local_path": "/out/images/abc.jpg"}) == "./images/abc.jpg"

    def test_a_non_http_url_is_not_treated_as_a_source(self):
        img = {"url": "images/abc.jpg", "local_path": "/out/images/abc.jpg"}
        assert _image_href(img) == "./images/abc.jpg"

    def test_empty_record_yields_no_href(self):
        assert _image_href({}) == ""
        assert _image_href(None) == ""

    def test_cache_filename_is_url_quoted(self):
        assert _image_href({"local_path": "/out/images/a b.jpg"}) == "./images/a%20b.jpg"


class TestServiceWorkerCachesRemoteImages:
    """Pointing at source URLs without teaching sw.js about them would look
    fine online and show nothing offline -- worse than what it replaced."""

    @staticmethod
    def _sw(tmp_path, trip):
        from generator.main import _write_pwa_assets
        _write_pwa_assets(Path(tmp_path), trip)
        return (Path(tmp_path) / "sw.js").read_text(encoding="utf-8")

    def _trip(self):
        return {"trip": {"title": "T"}, "destinations": [
            {"images": [{"url": "https://www.nps.gov/a.jpg"}, {"url": "https://images.unsplash.com/b.jpg"}]},
            {"images": [{"url": "https://www.nps.gov/a.jpg"}, {"local_path": "/x/c.jpg"}]},
        ]}

    def test_image_urls_are_precached(self, tmp_path):
        sw = self._sw(tmp_path, self._trip())
        assert "https://www.nps.gov/a.jpg" in sw
        assert "https://images.unsplash.com/b.jpg" in sw

    def test_precache_list_is_deduplicated(self, tmp_path):
        sw = self._sw(tmp_path, self._trip())
        line = next(l for l in sw.splitlines() if l.startswith("const IMAGES"))
        urls = json.loads(line.split("=", 1)[1].strip().rstrip(";"))
        assert urls.count("https://www.nps.gov/a.jpg") == 1

    def test_records_without_a_source_url_are_not_precached(self, tmp_path):
        sw = self._sw(tmp_path, self._trip())
        assert "/x/c.jpg" not in sw

    def test_install_tolerates_a_failed_image(self, tmp_path):
        """addAll is atomic; one 404 from a third-party host would otherwise
        abort the whole install and leave the app uncached."""
        sw = self._sw(tmp_path, self._trip())
        assert "cache.add(u).catch(" in sw

    def test_runtime_caches_cross_origin_images(self, tmp_path):
        """The old handler returned early for anything cross-origin that was
        not one of three CDNs -- which would now skip every image."""
        sw = self._sw(tmp_path, self._trip())
        assert "event.request.destination === 'image'" in sw
        assert "!sameOrigin && !cacheableCdn && !isImage" in sw

    def test_cache_name_was_bumped(self, tmp_path):
        """An installed client holding the v1 shell must not keep serving it."""
        sw = self._sw(tmp_path, self._trip())
        assert "roadtrip-shell-v2" in sw

    def test_a_trip_with_no_images_still_writes_valid_sw(self, tmp_path):
        sw = self._sw(tmp_path, {"trip": {"title": "T"}, "destinations": []})
        assert "const IMAGES = [];" in sw
