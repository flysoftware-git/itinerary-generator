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


# ── Verified-link-or-seed policy (2026-08-17) ───────────────────────────────


def test_quality_gate_unverified_seed_attractions_excluded_from_warning_count(capsys):
    """Policy (2026-08-17): an unverified seed attraction is expected/
    acceptable noise (the traveler's own request the pipeline couldn't
    verify), not a signal of a real recall/pipeline regression -- it must not
    count toward (or trip) the no_url_attractions warning threshold, even
    though it has no url."""
    trip = {
        "destinations": [
            {
                "ai_content": {
                    "top_attractions": [
                        {"name": "Obscure Trailhead", "is_seed": True},
                        {"name": "Another Seed", "is_seed": True},
                        {"name": "Third Seed", "is_seed": True},
                        {"name": "Fourth Seed", "is_seed": True},
                    ]
                }
            }
        ]
    }
    _run_quality_gate(trip)
    out = capsys.readouterr().out
    assert "attractions with no URL" not in out
    assert "unverified seed items kept" in out
    assert "attractions: 4" in out


def test_quality_gate_unverified_seed_en_route_stops_excluded_from_warning_count(capsys):
    """Same policy as above, applied to en-route stops: an unverified seed
    stop (from the manifest's en_route_seeds) must not count toward the
    no_url_stops warning threshold."""
    trip = {
        "destinations": [
            {
                "ai_content": {
                    "getting_here": {
                        "en_route_stops": [{"name": "Obscure Detour", "is_seed": True}] * 3
                    }
                }
            }
        ]
    }
    _run_quality_gate(trip)
    out = capsys.readouterr().out
    assert "en-route stops with no URL" not in out
    assert "unverified seed items kept" in out
    assert "en-route stops: 3" in out


def _registry_decision(entity_class: str, section_target: str, name: str, reason: str = "no_verified_url_removed") -> dict:
    return {
        "entity_class": entity_class,
        "display_name": name,
        "section_target": section_target,
        "rejection_reasons": [reason],
    }


def test_quality_gate_below_threshold_removals_reported_only_for_restaurants(capsys):
    """New visibility signal (2026-08-17): since non-seed items with no
    verified URL are now REMOVED from the trip data entirely (not left
    present with an empty url), the old no_url_* counters can no longer see
    them. url_discovery.py's audit_discovered_urls records each removal in
    the destination's _registry_decisions with rejection_reason
    "no_verified_url_removed" -- the quality gate surfaces that as its own
    counter, mirroring the old no_url_* thresholds: restaurants warn on any
    removal (>=1), attractions need >3, en-route stops need >2. Below those
    thresholds, attractions/stops stay quiet -- exactly like the old
    no_url_attractions/no_url_stops counters did."""
    trip = {
        "destinations": [
            {
                "ai_content": {"top_attractions": [], "dinner_recommendations": []},
                "_registry_decisions": [
                    _registry_decision("attraction", "top_attractions", "Sunrise Point"),
                    _registry_decision("attraction", "top_attractions", "Inspiration Point"),
                    _registry_decision("restaurant", "dinner_recommendations", "Mystery Diner"),
                    _registry_decision("en_route_stop", "en_route_stops", "Random Pulloff"),
                    # Not a no-verified-url removal -- must not be counted here.
                    _registry_decision("attraction", "top_attractions", "Golf Course", reason="interest_filter_removed"),
                ],
            }
        ]
    }
    _run_quality_gate(trip)
    out = capsys.readouterr().out

    assert "restaurants removed for no verified URL (verified-link-or-seed policy): 1" in out
    assert "attractions removed for no verified URL" not in out
    assert "en-route stops removed for no verified URL" not in out


def test_quality_gate_above_threshold_removals_flagged_for_attractions_and_stops(capsys):
    """Above the same thresholds the old no_url_attractions (>3) and
    no_url_stops (>2) counters used, the new removed_no_verified_url_*
    counters must warn -- this is the real successor signal for a genuine
    harvest/recall regression now that the affected items are gone from the
    trip data rather than present-with-no-url."""
    trip = {
        "destinations": [
            {
                "ai_content": {"top_attractions": [], "dinner_recommendations": []},
                "_registry_decisions": (
                    [
                        _registry_decision("attraction", "top_attractions", f"Attraction {i}")
                        for i in range(4)
                    ]
                    + [
                        _registry_decision("en_route_stop", "en_route_stops", f"Stop {i}")
                        for i in range(3)
                    ]
                ),
            }
        ]
    }
    _run_quality_gate(trip)
    out = capsys.readouterr().out

    assert "attractions removed for no verified URL (verified-link-or-seed policy): 4" in out
    assert "en-route stops removed for no verified URL (verified-link-or-seed policy): 3" in out
