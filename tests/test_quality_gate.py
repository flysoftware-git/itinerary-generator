"""Tests for generator.main._run_quality_gate's no-url/no-maps-fallback checks."""
from __future__ import annotations

from generator.main import _run_quality_gate


def test_quality_gate_restaurant_with_maps_fallback_not_flagged(capsys):
    """A restaurant with no `url` but a real `maps_url` has something
    clickable -- it must not count as 'no URL or maps fallback', matching
    how attractions are already counted."""
    trip = {
        "destinations": [
            {
                "ai_content": {
                    "dinner_recommendations": [
                        {
                            "name": "Painted Pony",
                            "description": "Fine dining.",
                            "maps_url": "https://www.google.com/maps/dir/?api=1&destination=Painted+Pony",
                        }
                    ]
                }
            }
        ]
    }
    _run_quality_gate(trip)
    out = capsys.readouterr().out
    assert "restaurants with no URL" not in out


def test_quality_gate_restaurant_with_no_url_and_no_maps_flagged(capsys):
    trip = {
        "destinations": [
            {
                "ai_content": {
                    "dinner_recommendations": [
                        {"name": "Mystery Diner", "description": "A place to eat."}
                    ]
                }
            }
        ]
    }
    _run_quality_gate(trip)
    out = capsys.readouterr().out
    assert "restaurants with no URL or maps fallback: 1" in out


def test_quality_gate_en_route_stop_with_maps_fallback_not_flagged(capsys):
    trip = {
        "destinations": [
            {
                "ai_content": {
                    "getting_here": {
                        "en_route_stops": [
                            {
                                "name": "Scenic Overlook",
                                "maps_url": "https://www.google.com/maps/dir/?api=1&destination=Scenic+Overlook",
                            }
                        ]
                        * 3
                    }
                }
            }
        ]
    }
    _run_quality_gate(trip)
    out = capsys.readouterr().out
    assert "en-route stops with no URL" not in out


def test_quality_gate_en_route_stop_with_no_url_and_no_maps_flagged(capsys):
    trip = {
        "destinations": [
            {
                "ai_content": {
                    "getting_here": {
                        "en_route_stops": [{"name": "Mystery Stop"}] * 3
                    }
                }
            }
        ]
    }
    _run_quality_gate(trip)
    out = capsys.readouterr().out
    assert "en-route stops with no URL or maps fallback: 3" in out
