"""A script-free copy of the itinerary, for attaching to mail.

Gmail rejected index.html as a virus. Nothing malicious is in it -- no eval,
no base64 payload, no iframe -- but three remote <script src> loads in an HTML
attachment is the shape generic heuristics score as Trojan:HTML/Phish.
"""
import re

import pytest

from generator.email_safe import make_email_safe

SAMPLE = """<html><head>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css">
</head><body>
<div class="flex items-center bg-terracotta text-white px-4">Header</div>
<a href="https://www.google.com/maps/dir/?api=1&origin=A&destination=B&travelmode=transit">Open in Google Maps</a>
<div data-id="14" id="overview-map" style="height:340px;">map goes here</div>
<img src="https://x/a.jpg" class="hide-on-error" loading="lazy" />
<script src="https://cdn.tailwindcss.com"></script>
<script>tailwind.config = {theme:{}};</script>
<script src="https://unpkg.com/lucide@latest" defer></script>
<script>document.addEventListener('error', function(){}, true);</script>
</body></html>"""


class TestNothingExecutableSurvives:
    def test_no_script_tags_at_all(self):
        out, _ = make_email_safe(SAMPLE)
        assert "<script" not in out.lower()

    def test_no_remote_code_fetches(self):
        out, _ = make_email_safe(SAMPLE)
        assert "cdn.tailwindcss.com" not in out
        assert "unpkg.com" not in out
        assert "leaflet" not in out.lower()

    def test_no_inline_event_handlers(self):
        """The other half of the heuristic, already removed upstream."""
        out, _ = make_email_safe(SAMPLE)
        assert not re.search(r"\son\w+\s*=", out)

    def test_reports_what_it_removed(self):
        _, stats = make_email_safe(SAMPLE)
        # Two script tags carry src (tailwind, lucide); the leaflet reference in
        # the sample is a stylesheet <link>, which is stripped separately.
        assert stats["cdn_scripts_removed"] == 2
        assert stats["scripts_removed"] == 4
        assert stats["map_replaced"] == 1


class TestContentIsPreserved:
    def test_the_itinerary_text_survives(self):
        out, _ = make_email_safe(SAMPLE)
        assert "Header" in out

    def test_layout_classes_get_static_css(self):
        """Tailwind compiled in the browser; without the CDN the classes must
        still resolve or the page loses its layout."""
        out, _ = make_email_safe(SAMPLE)
        for rule in (".flex{display:flex}", ".items-center", ".px-4", ".bg-terracotta", ".text-white"):
            assert rule.split("{")[0] in out

    def test_the_map_becomes_a_link_not_a_hole(self):
        out, _ = make_email_safe(SAMPLE)
        assert "overview-map" not in out
        assert "Route map" in out
        assert "google.com/maps/dir" in out

    def test_images_still_render(self):
        out, _ = make_email_safe(SAMPLE)
        assert 'src="https://x/a.jpg"' in out


class TestDegradesQuietly:
    def test_a_page_with_no_map_is_fine(self):
        out, stats = make_email_safe("<html><body><p>No map here</p></body></html>")
        assert stats["map_replaced"] == 0
        assert "No map here" in out

    def test_a_page_with_no_scripts_is_fine(self):
        out, stats = make_email_safe("<html><head></head><body>hi</body></html>")
        assert stats["scripts_removed"] == 0
        assert "hi" in out

    def test_map_without_a_route_link_still_explains_itself(self):
        html = '<div id="overview-map">x</div>'
        out, stats = make_email_safe(html)
        assert stats["map_replaced"] == 1
        assert "full itinerary file" in out
