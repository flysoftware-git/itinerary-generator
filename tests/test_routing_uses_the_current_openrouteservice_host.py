"""Directions go to openrouteservice's current host, not the one being shut down.

HeiGIT moved the public API from api.openrouteservice.org to api.heigit.org:
the old host was deprecated on 2026-04-28, had its quota reduced from
2026-08-24, and shuts down on 2026-11-02..06. On the old host every leg came
back `403 Quota exceeded` and a page's roads all became straight lines.
"""

from __future__ import annotations

from urllib.parse import urlparse

from generator import routing


def test_directions_go_to_the_current_host():
    """Red with ENDPOINT_BASE on api.openrouteservice.org."""
    parsed = urlparse(routing.ENDPOINT_BASE)
    assert parsed.scheme == "https"
    assert parsed.netloc == "api.heigit.org", routing.ENDPOINT_BASE
    assert parsed.path == "/openrouteservice/v2/directions", routing.ENDPOINT_BASE
