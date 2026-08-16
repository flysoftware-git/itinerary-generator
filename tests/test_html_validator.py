"""Tests for generator.html_validator"""
import json
import pytest
from pathlib import Path
from generator.html_validator import HTMLValidator


SAMPLE_TRIP = {
    "destinations": [
        {
            "id": "zion",
            "name": "Zion National Park",
            "images": [
                {"local_path": "output/images/abc.jpg"},
                {"local_path": "output/images/def.jpg"},
            ],
            "scenic_drives": [
                {"title": "Zion Canyon Scenic Drive"}
            ],
        }
    ]
}

DRIVE_KEY = "Zion Canyon Scenic Drive"

VALID_HTML = f"""<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body>
<section id="section-zion" class="destination-section">
  <div class="dest-header">
    <div class="inner"></div>
  </div>
</section>
<script>
var DRIVE_DESCRIPTIONS = {json.dumps({DRIVE_KEY: {"title": "Zion Canyon Scenic Drive"}})};
</script>
<button class="drive-link" data-drive-title="{DRIVE_KEY}"></button>
</body>
</html>"""


def _write_html(tmp_path, html):
    p = tmp_path / "index.html"
    p.write_text(html, encoding="utf-8")
    return p


def _make_validator(tmp_path):
    # Build a minimal config.yaml for the validator
    cfg = tmp_path / "config.yaml"
    cfg.write_text("images:\n  min_per_destination: 2\n  max_per_destination: 4\n")
    return HTMLValidator(config_path=str(cfg))


def test_valid_html_passes(tmp_path):
    p = _write_html(tmp_path, VALID_HTML)
    v = _make_validator(tmp_path)
    report = v.validate(p, SAMPLE_TRIP)
    assert report["valid"] is True
    assert report["error_count"] == 0


def test_const_drive_descriptions_flagged(tmp_path):
    bad_html = VALID_HTML.replace("var DRIVE_DESCRIPTIONS", "const DRIVE_DESCRIPTIONS")
    p = _write_html(tmp_path, bad_html)
    v = _make_validator(tmp_path)
    report = v.validate(p, SAMPLE_TRIP)
    assert any("const" in e for e in report["errors"])


def test_missing_drive_descriptions_flagged(tmp_path):
    bad_html = VALID_HTML.replace("var DRIVE_DESCRIPTIONS", "// removed")
    p = _write_html(tmp_path, bad_html)
    v = _make_validator(tmp_path)
    report = v.validate(p, SAMPLE_TRIP)
    assert any("DRIVE_DESCRIPTIONS" in e for e in report["errors"])


def test_image_count_below_min_flagged(tmp_path):
    trip_with_one_image = {
        "destinations": [
            {
                "id": "zion",
                "name": "Zion National Park",
                "images": [{"local_path": "output/images/abc.jpg"}],
                "scenic_drives": [{"title": "Zion Canyon Scenic Drive"}],
            }
        ]
    }
    p = _write_html(tmp_path, VALID_HTML)
    v = _make_validator(tmp_path)
    report = v.validate(p, trip_with_one_image)
    assert any("image" in e.lower() or "minimum" in e.lower() for e in report["errors"])


def _trip_with_attractions(attractions, dinner=None, en_route_stops=None):
    return {
        "destinations": [
            {
                "id": "zion",
                "name": "Zion National Park",
                "images": [
                    {"local_path": "output/images/abc.jpg"},
                    {"local_path": "output/images/def.jpg"},
                ],
                "scenic_drives": [{"title": "Zion Canyon Scenic Drive"}],
                "ai_content": {
                    "top_attractions": attractions,
                    "dinner_recommendations": dinner or [],
                    "getting_here": {"en_route_stops": en_route_stops or []},
                },
            }
        ]
    }


def test_orphan_attractions_above_threshold_warns(tmp_path):
    """Regression (2026-08-15, dipstick56+): this exact check used to only
    print to the console via main.py's _run_quality_gate -- a run with 12
    orphan attraction cards still reported validation_report.json's
    valid:true with no persisted trace of the problem."""
    attractions = [{"name": f"Attraction {i}", "url": ""} for i in range(4)]
    trip = _trip_with_attractions(attractions)
    p = _write_html(tmp_path, VALID_HTML)
    v = _make_validator(tmp_path)
    report = v.validate(p, trip)
    assert report["valid"] is True  # warning, not error -- known unresolved gap, not a hard fail
    assert any("attractions with no url" in w.lower() for w in report["warnings"])


def test_orphan_attractions_within_threshold_silent(tmp_path):
    attractions = [{"name": f"Attraction {i}", "url": "https://example.com"} for i in range(3)] + [
        {"name": "Orphan", "url": ""}
    ]
    trip = _trip_with_attractions(attractions)
    p = _write_html(tmp_path, VALID_HTML)
    v = _make_validator(tmp_path)
    report = v.validate(p, trip)
    assert not any("attractions with no url" in w.lower() for w in report["warnings"])


def test_any_orphan_restaurant_warns(tmp_path):
    """max_no_url_restaurants defaults to 0 -- even a single orphan
    restaurant warns, matching the old quality gate's stricter bar for
    restaurants specifically."""
    trip = _trip_with_attractions([], dinner=[{"name": "Cafe", "url": ""}])
    p = _write_html(tmp_path, VALID_HTML)
    v = _make_validator(tmp_path)
    report = v.validate(p, trip)
    assert any("restaurants with no url" in w.lower() for w in report["warnings"])


def test_duplicate_attraction_url_within_destination_warns(tmp_path):
    """New check (2026-08-15) -- not in the old quality gate. Regression
    for the Theme F bug (dipstick55): two entries independently resolving
    to the same URL both survived as separate cards until a merge pass was
    added; this check gives durable, independent protection against that
    class of bug regressing."""
    attractions = [
        {"name": "Inspiration Point", "url": "https://www.alltrails.com/trail/x"},
        {"name": "Sunset and Inspiration Points via Rim Trail", "url": "https://www.alltrails.com/trail/x"},
    ]
    trip = _trip_with_attractions(attractions)
    p = _write_html(tmp_path, VALID_HTML)
    v = _make_validator(tmp_path)
    report = v.validate(p, trip)
    assert any("duplicate attraction url" in w.lower() for w in report["warnings"])


def test_distinct_urls_do_not_warn_as_duplicates(tmp_path):
    attractions = [
        {"name": "Mesa Arch", "url": "https://www.alltrails.com/trail/mesa-arch"},
        {"name": "Double Arch Trail", "url": "https://www.alltrails.com/trail/double-arch-trail"},
    ]
    trip = _trip_with_attractions(attractions)
    p = _write_html(tmp_path, VALID_HTML)
    v = _make_validator(tmp_path)
    report = v.validate(p, trip)
    assert not any("duplicate attraction url" in w.lower() for w in report["warnings"])


def test_empty_teaser_ratio_above_threshold_warns(tmp_path):
    """Regression (2026-08-15, dipstick56+): 19% of attraction/trail
    teasers rendered empty in a real run with no persisted signal anywhere
    that this happened."""
    attractions = [{"name": f"Attraction {i}", "url": "https://example.com", "description": ""} for i in range(3)] + [
        {"name": "Has Teaser", "url": "https://example.com", "description": "A real description."}
    ]
    trip = _trip_with_attractions(attractions)
    p = _write_html(tmp_path, VALID_HTML)
    v = _make_validator(tmp_path)
    report = v.validate(p, trip)
    assert any("teasers empty" in w.lower() for w in report["warnings"])


def test_empty_teaser_ratio_within_threshold_silent(tmp_path):
    attractions = [{"name": "A", "url": "https://example.com", "description": "Real."}] * 9 + [
        {"name": "B", "url": "https://example.com", "description": ""}
    ]
    trip = _trip_with_attractions(attractions)
    p = _write_html(tmp_path, VALID_HTML)
    v = _make_validator(tmp_path)
    report = v.validate(p, trip)
    assert not any("teasers empty" in w.lower() for w in report["warnings"])


def test_div_imbalance_flagged(tmp_path):
    bad_html = VALID_HTML.replace(
        '<section id="section-zion" class="destination-section">\n  <div class="dest-header">\n    <div class="inner"></div>\n  </div>\n</section>',
        '<section id="section-zion" class="destination-section">\n  <div class="dest-header">\n    <div class="inner">\n  </div>\n</section>'
    )
    p = _write_html(tmp_path, bad_html)
    v = _make_validator(tmp_path)
    report = v.validate(p, SAMPLE_TRIP)
    assert any("zion" in e.lower() or "div" in e.lower() for e in report["errors"])


def test_orphan_script_in_section_warns(tmp_path):
    bad_html = VALID_HTML.replace(
        '<section id="section-zion" class="destination-section">',
        '<section id="section-zion" class="destination-section"><script>alert(1)</script>'
    )
    p = _write_html(tmp_path, bad_html)
    v = _make_validator(tmp_path)
    report = v.validate(p, SAMPLE_TRIP)
    assert any("script" in w.lower() for w in report["warnings"])


def test_nested_drive_descriptions_json_parses(tmp_path):
    nested = {
        DRIVE_KEY: {
            "title": "Zion Canyon Scenic Drive",
            "description": "Contains nested JSON-like blocks",
            "meta": {"season": "fall", "difficulty": {"level": "easy"}},
        }
    }
    html = VALID_HTML.replace(
        json.dumps({DRIVE_KEY: {"title": "Zion Canyon Scenic Drive"}}),
        json.dumps(nested),
    )
    p = _write_html(tmp_path, html)
    v = _make_validator(tmp_path)
    report = v.validate(p, SAMPLE_TRIP)
    assert report["valid"] is True


def test_drive_title_html_entities_do_not_break_key_matching(tmp_path):
        drive_key = "Viewpoint at Angel's Landing"
        html = f"""<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body>
<section id="section-zion" class="destination-section">
    <div class="dest-header"><div class="inner"></div></div>
</section>
<script>
var DRIVE_DESCRIPTIONS = {json.dumps({drive_key: {"title": drive_key}})};
</script>
<button class="drive-link" data-drive-title="Viewpoint at Angel&#39;s Landing"></button>
</body>
</html>"""

        p = _write_html(tmp_path, html)
        v = _make_validator(tmp_path)
        report = v.validate(p, SAMPLE_TRIP)
        assert report["valid"] is True
