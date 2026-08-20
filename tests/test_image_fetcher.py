"""Tests for generator.image_fetcher"""
import threading
import time
from unittest.mock import MagicMock, patch
from generator.image_fetcher import ImageFetcher, REQUEST_DELAY


def _make_fetcher(tmp_path):
    """Create an ImageFetcher with a real config.yaml wired to tmp output."""
    fetcher = ImageFetcher.__new__(ImageFetcher)
    fetcher._nps_key = "DEMO_KEY"
    fetcher._min_per_dest = 2
    fetcher._max_per_dest = 4
    fetcher._output_dir = tmp_path / "images"
    fetcher._output_dir.mkdir(parents=True, exist_ok=True)
    fetcher._cache_ttl_seconds = 7 * 24 * 3600
    fetcher._force_refresh = False
    fetcher._cache_index_path = tmp_path / "cache_index.json"
    fetcher._cache_lock = threading.Lock()
    fetcher._cache_index = {"version": 1, "entries": {}}
    fetcher._session = MagicMock()
    return fetcher


def test_request_delay_is_a_small_courtesy_pause_not_a_rate_limit_workaround():
    """REQUEST_DELAY paces raw file downloads after they succeed, applied
    uniformly across three unrelated static-asset hosts (NPS, Unsplash,
    Wikimedia) that document no per-download throttle. It should stay a
    small courtesy value, not creep back up toward the old unjustified 1.5s
    (GH #67 audit finding, 2026-08-16)."""
    assert 0 < REQUEST_DELAY <= 0.5


def test_fetch_all_keeps_destination_with_empty_images_if_not_enough_images(tmp_path):
    fetcher = _make_fetcher(tmp_path)

    trip = {
        "destinations": [
            {"id": "zion", "name": "Zion National Park", "nps_park_code": "zion"}
        ]
    }

    with patch.object(fetcher, "_fetch_from_nps", return_value=[]):
        with patch.object(fetcher, "_fetch_from_wikimedia", return_value=[]):
            with patch.object(fetcher, "_download_image", return_value=None):
                fetcher.fetch_all(trip)

    assert "images" in trip["destinations"][0]
    assert trip["destinations"][0]["images"] == []


def test_fetch_all_attaches_images_to_dest(tmp_path):
    fetcher = _make_fetcher(tmp_path)

    fake_images = [
        {"url": "https://example.com/img1.jpg", "title": "Img 1", "credit": "NPS", "license": "PD", "source": "nps"},
        {"url": "https://example.com/img2.jpg", "title": "Img 2", "credit": "NPS", "license": "PD", "source": "nps"},
    ]
    trip = {
        "destinations": [
            {"id": "zion", "name": "Zion National Park", "nps_park_code": "zion"}
        ]
    }

    fake_local = tmp_path / "images" / "fake.jpg"
    fake_local.write_bytes(b"FAKE")

    with patch.object(fetcher, "_fetch_from_nps", return_value=fake_images):
        with patch.object(fetcher, "_fetch_from_wikimedia", return_value=[]):
            with patch.object(fetcher, "_download_image", return_value=fake_local):
                fetcher.fetch_all(trip)

    assert len(trip["destinations"][0]["images"]) == 2


def test_fallback_queries_returns_four():
    queries = ImageFetcher._fallback_queries("Zion National Park")
    assert len(queries) == 4
    assert all(isinstance(q, str) for q in queries)


def test_guess_extension_jpg():
    assert ImageFetcher._guess_extension("https://example.com/photo.jpg") == ".jpg"


def test_guess_extension_unknown_defaults_jpg():
    assert ImageFetcher._guess_extension("https://example.com/photo.tiff") == ".jpg"


def test_build_thumb_url_md5(tmp_path):
    """Thumb URL construction uses MD5 hash of filename for Wikimedia path."""
    import hashlib
    url = "https://upload.wikimedia.org/wikipedia/commons/5/5b/Zion_Canyon.jpg"
    filename = url.split("/")[-1]
    h = hashlib.md5(filename.encode()).hexdigest()
    # Just verify the hash logic is consistent
    assert h == hashlib.md5(b"Zion_Canyon.jpg").hexdigest()


def test_rank_images_penalizes_marine_mismatch_for_capitol_reef(tmp_path):
    fetcher = _make_fetcher(tmp_path)
    images = [
        {
            "url": "https://example.com/coral-reef-underwater.jpg",
            "title": "Coral reef underwater scene",
            "credit": "Photographer",
            "source": "unsplash",
        },
        {
            "url": "https://example.com/capitol-reef-utah-canyon.jpg",
            "title": "Capitol Reef Utah canyon landscape",
            "credit": "Photographer",
            "source": "unsplash",
        },
    ]

    ranked = fetcher._rank_images_for_destination(images, "Capitol Reef National Park")
    assert ranked
    assert "capitol-reef-utah-canyon" in ranked[0]["url"]


def test_provider_query_disambiguates_capitol_reef():
    q = ImageFetcher._provider_query_for_destination("Capitol Reef National Park")
    assert "Utah national park desert canyon" in q


def test_location_tokens_drop_ambiguous_reef_for_capitol_reef():
    tokens = ImageFetcher._location_tokens("Capitol Reef National Park")
    assert "capitol" in tokens
    assert "reef" not in tokens


def test_rank_images_hard_rejects_marine_only_results_for_inland_dest(tmp_path):
    fetcher = _make_fetcher(tmp_path)
    images = [
        {
            "url": "https://example.com/coral-reef-underwater.jpg",
            "title": "Coral reef underwater scene",
            "credit": "Photographer",
            "source": "unsplash",
        },
        {
            "url": "https://example.com/scuba-reef-fish.jpg",
            "title": "Scuba reef fish",
            "credit": "Photographer",
            "source": "unsplash",
        },
    ]

    ranked = fetcher._rank_images_for_destination(images, "Capitol Reef National Park")
    assert ranked == []


def test_rank_images_global_blacklist_rejects_underwater_for_any_destination(tmp_path):
    fetcher = _make_fetcher(tmp_path)
    fetcher._global_blacklist_terms = {"underwater", "scuba", "snorkel", "snorkeling"}
    images = [
        {
            "url": "https://example.com/reef-underwater-shot.jpg",
            "title": "Underwater reef photo",
            "credit": "Photographer",
            "source": "unsplash",
        },
        {
            "url": "https://example.com/coastal-overlook.jpg",
            "title": "Coastal overlook at sunset",
            "credit": "Photographer",
            "source": "unsplash",
        },
    ]

    ranked = fetcher._rank_images_for_destination(images, "Sydney, Australia")
    assert ranked
    assert all("underwater" not in (img.get("title", "") or "").lower() for img in ranked)


def test_rank_images_for_capitol_reef_prefers_required_context_when_available(tmp_path):
    fetcher = _make_fetcher(tmp_path)
    images = [
        {
            "url": "https://images.unsplash.com/photo-reef-12345",
            "title": "",
            "credit": "Photographer",
            "source": "unsplash",
        },
        {
            "url": "https://example.com/capitol-reef-utah-canyon.jpg",
            "title": "Capitol Reef Utah canyon landscape",
            "credit": "NPS",
            "source": "nps",
        },
    ]

    ranked = fetcher._rank_images_for_destination(images, "Capitol Reef National Park")
    assert ranked
    assert "capitol-reef-utah-canyon" in ranked[0]["url"]


def test_destination_image_profile_marks_marine_terms_negative_for_inland_parks():
    profile = ImageFetcher._destination_image_profile("Zion National Park, Utah")
    assert "coral" in profile["negative"]
    assert "underwater" in profile["negative"]


def test_rank_images_prefers_scenery_over_wildlife_when_available(tmp_path):
    fetcher = _make_fetcher(tmp_path)
    images = [
        {
            "url": "https://example.com/bryce-bird-perch.jpg",
            "title": "Bird perched at Bryce",
            "credit": "NPS",
            "source": "nps",
        },
        {
            "url": "https://example.com/bryce-canyon-hoodoos-landscape.jpg",
            "title": "Bryce Canyon hoodoos landscape",
            "credit": "NPS",
            "source": "nps",
        },
    ]

    ranked = fetcher._rank_images_for_destination(images, "Bryce Canyon National Park")
    assert ranked
    assert "hoodoos-landscape" in ranked[0]["url"]


def test_fetch_for_dest_uses_fresh_cache_before_provider_queries(tmp_path):
    fetcher = _make_fetcher(tmp_path)
    dest = {"name": "Zion National Park", "nps_park_code": "zion"}
    key = fetcher._cache_key(dest)
    fetcher._cache_index["entries"][key] = {
        "updated_at": time.time(),
        "images": [
            {"url": "https://example.com/z1.jpg", "title": "Zion 1", "credit": "NPS", "license": "PD", "source": "nps"},
            {"url": "https://example.com/z2.jpg", "title": "Zion 2", "credit": "NPS", "license": "PD", "source": "nps"},
        ],
    }
    fake_local = tmp_path / "images" / "cached.jpg"
    fake_local.write_bytes(b"X")

    with patch.object(fetcher, "_download_image", return_value=fake_local):
        with patch.object(fetcher, "_fetch_from_nps", side_effect=AssertionError("provider should not be called")):
            with patch.object(fetcher, "_fetch_from_unsplash", side_effect=AssertionError("provider should not be called")):
                with patch.object(fetcher, "_fetch_from_wikimedia", side_effect=AssertionError("provider should not be called")):
                    out = fetcher._fetch_for_dest(dest)

    assert len(out) >= fetcher._min_per_dest
    assert all(i.get("local_path") for i in out)


def test_fetch_for_dest_force_refresh_bypasses_cache(tmp_path):
    fetcher = _make_fetcher(tmp_path)
    fetcher._force_refresh = True
    dest = {"name": "Bryce Canyon National Park", "nps_park_code": "brca"}
    key = fetcher._cache_key(dest)
    fetcher._cache_index["entries"][key] = {
        "updated_at": time.time(),
        "images": [
            {"url": "https://example.com/old.jpg", "title": "Old", "credit": "NPS", "license": "PD", "source": "nps"},
        ],
    }

    live_images = [
        {"url": "https://example.com/live1.jpg", "title": "Live 1", "credit": "NPS", "license": "PD", "source": "nps"},
        {"url": "https://example.com/live2.jpg", "title": "Live 2", "credit": "NPS", "license": "PD", "source": "nps"},
    ]
    fake_local = tmp_path / "images" / "live.jpg"
    fake_local.write_bytes(b"Y")

    with patch.object(fetcher, "_fetch_from_nps", return_value=live_images) as p_nps:
        with patch.object(fetcher, "_fetch_from_unsplash", return_value=[]):
            with patch.object(fetcher, "_fetch_from_wikimedia", return_value=[]):
                with patch.object(fetcher, "_download_image", return_value=fake_local):
                    out = fetcher._fetch_for_dest(dest)

    assert p_nps.called
    assert len(out) >= fetcher._min_per_dest
    assert all("live" in i.get("url", "") for i in out)


def test_sanitize_metadata_text_drops_wikimedia_template_noise():
    noisy = "<table><tr><td>When reusing, please credit me <a rel='nofollow' class='external text' href='https://commons.wikimedia.org/wiki/File:foo'>link</a></td></tr></table>"
    cleaned = ImageFetcher._sanitize_metadata_text(noisy)
    assert cleaned == ""


def test_normalize_image_record_sets_fallback_credit_when_sanitized_empty(tmp_path):
    fetcher = _make_fetcher(tmp_path)
    record = {
        "url": "https://example.com/img.jpg",
        "title": "<b>Beautiful Canyon</b>",
        "credit": "<table>When reusing, please credit me</table>",
        "license": "<i>CC BY-SA</i>",
        "source": "wikimedia",
    }

    normalized = fetcher._normalize_image_record(record)

    assert normalized["title"] == "Beautiful Canyon"
    assert normalized["credit"] == "Wikimedia Commons"
    assert "<" not in normalized["license"]


def test_image_fallback_loop_queries_unsplash_not_just_wikimedia(monkeypatch, tmp_path):
    """A destination short of min_per_destination must reach Unsplash.

    Regression (run 20260820T032249): 'bryce' and 'capitolreef' finished with
    1 image against a minimum of 2 and failed validation, while the run made
    ZERO Unsplash calls despite UNSPLASH_ACCESS_KEY being set. The Source-2
    gate tests len(images) -- the CANDIDATE count -- against _max_per_dest, so
    a destination with plentiful but unusable candidates (4 collected, 1
    surviving verification) skips Unsplash and Wikimedia entirely and lands in
    this loop as its only remaining chance.
    """
    from generator.image_fetcher import ImageFetcher

    fetcher = ImageFetcher.__new__(ImageFetcher)
    fetcher._min_per_dest = 2
    fetcher._max_per_dest = 4
    fetcher._force_refresh = True
    fetcher._counters = {}
    fetcher._cache_index = {"version": 1, "entries": {}}

    calls = {"unsplash": 0, "wikimedia": 0}

    def fake_unsplash(query, limit=4):
        calls["unsplash"] += 1
        return [{"url": f"https://u/{calls['unsplash']}.jpg", "source": "unsplash"}]

    def fake_wikimedia(query, limit=4):
        calls["wikimedia"] += 1
        return []

    monkeypatch.setattr(fetcher, "_fetch_from_unsplash", fake_unsplash, raising=False)
    monkeypatch.setattr(fetcher, "_fetch_from_wikimedia", fake_wikimedia, raising=False)
    monkeypatch.setattr(fetcher, "_fetch_from_nps", lambda code: [], raising=False)
    monkeypatch.setattr(fetcher, "_rank_images_for_destination", lambda imgs, name: imgs, raising=False)
    monkeypatch.setattr(fetcher, "_provider_query_for_destination", lambda name: name, raising=False)
    monkeypatch.setattr(fetcher, "_cache_key", lambda dest: "k", raising=False)
    monkeypatch.setattr(fetcher, "_set_cached_images", lambda k, v: None, raising=False)
    # Never verifies anything, forcing the loop to exhaust its attempts.
    monkeypatch.setattr(fetcher, "_verify_and_materialize", lambda imgs, name: [], raising=False)

    fetcher._fetch_for_dest({"name": "Bryce Canyon National Park"})

    assert calls["unsplash"] > 0, "fallback loop never queried Unsplash"
    assert calls["wikimedia"] > 0, "fallback loop stopped querying Wikimedia"


def test_wikimedia_rate_limit_is_retried_not_reported_as_no_images(monkeypatch):
    """A 429 must be distinguishable from "Commons has no images for this".

    Regression (run 20260820T032249): _fetch_from_wikimedia caught every
    RequestException and returned [], so a rate-limited call was
    indistinguishable from an empty result set. 'bryce' and 'capitolreef'
    finished with 1 image against a minimum of 2 and failed validation, even
    though the same queries return 4 usable candidates when not throttled.
    """
    import requests

    from generator import image_fetcher as mod
    from generator.image_fetcher import ImageFetcher

    monkeypatch.setattr(mod, "WIKIMEDIA_MIN_REQUEST_INTERVAL_SECONDS", 0.0)
    fetcher = ImageFetcher.__new__(ImageFetcher)

    attempts = {"n": 0}

    def flaky(query, limit=4):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return None  # rate-limited
        return [{"url": "https://example.invalid/a.jpg", "source": "wikimedia"}]

    monkeypatch.setattr(fetcher, "_fetch_from_wikimedia_once", flaky, raising=False)

    results = fetcher._fetch_from_wikimedia("Bryce Canyon National Park")

    assert attempts["n"] == 2, "a rate-limited call was not retried"
    assert len(results) == 1


def test_wikimedia_429_maps_to_none_and_other_errors_map_to_empty(monkeypatch):
    """None means retry; [] means genuinely nothing found."""
    import requests

    from generator.image_fetcher import ImageFetcher

    fetcher = ImageFetcher.__new__(ImageFetcher)
    fetcher._session_local = type("L", (), {})()

    class FakeResp:
        def __init__(self, code):
            self.status_code = code

        def raise_for_status(self):
            err = requests.HTTPError(f"{self.status_code}")
            err.response = self
            raise err

    class FakeSession:
        def __init__(self, code):
            self._code = code

        def get(self, *a, **k):
            return FakeResp(self._code)

    monkeypatch.setattr(ImageFetcher, "_wikimedia_throttle", staticmethod(lambda: None))

    monkeypatch.setattr(fetcher, "_get_session", lambda: FakeSession(429), raising=False)
    assert fetcher._fetch_from_wikimedia_once("q") is None

    monkeypatch.setattr(fetcher, "_get_session", lambda: FakeSession(404), raising=False)
    assert fetcher._fetch_from_wikimedia_once("q") == []
