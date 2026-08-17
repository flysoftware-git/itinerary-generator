from unittest.mock import patch

from generator.cultural_events import CulturalEventsDiscoverer


def _discoverer() -> CulturalEventsDiscoverer:
    # Use helper methods without initializing network clients.
    return CulturalEventsDiscoverer.__new__(CulturalEventsDiscoverer)


def test_search_provider_override_forces_single_provider():
    """--search-provider (2026-08-15): forces cultural_events' search
    client to a specific provider regardless of config.yaml, for a clean
    single-provider comparison run."""
    from generator.grok_search import GrokSearch

    mock_llm = type("MockLLM", (), {"usage_tracker": None})()
    with patch.dict("os.environ", {"XAI_API_KEY": "test-key"}):
        discoverer = CulturalEventsDiscoverer(
            config_path="config.yaml", llm_client=mock_llm, search_provider_override="grok"
        )

    assert isinstance(discoverer._search, GrokSearch)


def test_local_tip_removed_when_weekday_outside_itinerary() -> None:
    d = _discoverer()
    result = {
        "has_events": False,
        "honest_assessment": "Quiet scene with visitor center programs.",
        "local_tip": "Saturday artisan market on Main Street.",
    }
    sanitized = d._sanitize_local_tip_by_itinerary_days(result, "October 7-9, 2026")
    assert "local_tip" not in sanitized


def test_local_tip_kept_when_weekday_inside_itinerary() -> None:
    d = _discoverer()
    result = {
        "has_events": False,
        "honest_assessment": "Quiet scene with visitor center programs.",
        "local_tip": "Friday live music at the town hall.",
    }
    sanitized = d._sanitize_local_tip_by_itinerary_days(result, "October 7-9, 2026")
    assert sanitized.get("local_tip") == "Friday live music at the town hall."


def test_local_tip_removed_when_dates_unparseable_and_weekday_specific() -> None:
    d = _discoverer()
    result = {
        "has_events": False,
        "honest_assessment": "Quiet scene with visitor center programs.",
        "local_tip": "Sunday market near the visitor center.",
    }
    sanitized = d._sanitize_local_tip_by_itinerary_days(result, "Early October")
    assert "local_tip" not in sanitized


def test_local_tip_kept_when_not_weekday_specific() -> None:
    d = _discoverer()
    result = {
        "has_events": False,
        "honest_assessment": "Quiet scene with visitor center programs.",
        "local_tip": "Check ranger talks posted at the visitor center desk.",
    }
    sanitized = d._sanitize_local_tip_by_itinerary_days(result, "Early October")
    assert sanitized.get("local_tip") == "Check ranger talks posted at the visitor center desk."


def test_drop_events_before_arrival_removes_event_preceding_stay() -> None:
    """Real dipstick58 St. George bug: destination stays October 17, 2026 only,
    but an "October 12, 2026" event (5 days before arrival) rendered anyway.
    Events dated before the destination's own arrival date must never show."""
    d = _discoverer()
    result = {
        "has_events": True,
        "events": [
            {
                "name": "I-15 Country Rock Music Festival",
                "venue": "Mesquite Regional Sports and Event Complex",
                "dates_in_range": "October 17, 2026",
                "admission": "Varies",
            },
            {
                "name": "St. George Concert in the Park Series 2026",
                "venue": "Vernon Worthen Park",
                "dates_in_range": "October 12, 2026",
                "admission": "Free",
            },
        ],
        "ambient_scene": "St. George has a vibrant local music scene.",
    }

    filtered = d._drop_events_before_arrival(result, "October 17, 2026")

    names = [e["name"] for e in filtered["events"]]
    assert names == ["I-15 Country Rock Music Festival"]
    assert filtered["has_events"] is True


def test_drop_events_before_arrival_falls_back_when_all_events_precede_stay() -> None:
    d = _discoverer()
    result = {
        "has_events": True,
        "events": [
            {"name": "Early Bird Market", "dates_in_range": "October 5, 2026"},
        ],
    }

    filtered = d._drop_events_before_arrival(result, "October 17, 2026")

    assert filtered["events"] == []
    assert filtered["has_events"] is False


def test_drop_events_before_arrival_keeps_unparseable_recurring_dates() -> None:
    d = _discoverer()
    result = {
        "has_events": True,
        "events": [
            {"name": "Weekly Farmers Market", "dates_in_range": "Every Friday evening in October"},
        ],
    }

    filtered = d._drop_events_before_arrival(result, "October 17, 2026")

    assert len(filtered["events"]) == 1
    assert filtered["has_events"] is True


def test_drop_events_before_arrival_keeps_event_on_arrival_day() -> None:
    d = _discoverer()
    result = {
        "has_events": True,
        "events": [
            {"name": "Arrival Day Show", "dates_in_range": "October 17, 2026"},
        ],
    }

    filtered = d._drop_events_before_arrival(result, "October 17, 2026")

    assert len(filtered["events"]) == 1


def test_verify_event_urls_strips_generic_landing_page() -> None:
    """Real dipstick60 Moab bug: "Field of Screams Softball Tournament" and
    "Canyonlands Ultra" rendered with no verified link. Cultural events'
    URL check only confirmed the URL was *reachable* (HTTP status), so a
    generic /things-to-do listing page -- live, but not actually the event's
    own page -- would have sailed through unflagged. Verify the generic-URL
    check (reused from url_discovery.URLDiscoverer, the same detector
    attractions/restaurants rely on) now strips those before they render."""
    d = _discoverer()
    result = {
        "has_events": True,
        "events": [
            {
                "name": "Field of Screams Softball Tournament",
                "venue": "Moab",
                "dates_in_range": "October 23-24, 2026",
                "url": "https://www.moab.org/things-to-do",
            },
            {
                "name": "Canyonlands Ultra",
                "venue": "Moab",
                "dates_in_range": "October 24, 2026",
                "url": "https://www.moabultra.com/404errorpage",
            },
        ],
    }

    verified = d._verify_event_urls(result)

    assert all("url" not in e for e in verified["events"])


def test_verify_event_urls_keeps_live_specific_url() -> None:
    """A specific, reachable event page must survive verification unstripped."""
    d = _discoverer()
    result = {
        "has_events": True,
        "events": [
            {
                "name": "Canyonlands Ultra",
                "venue": "Moab",
                "dates_in_range": "October 24, 2026",
                "url": "https://www.moabultra.com/canyonlands-ultra-race-info",
            },
        ],
    }

    with patch("generator.url_validator.URLValidator.verify_url", return_value=(True, 200)):
        verified = d._verify_event_urls(result)

    assert verified["events"][0]["url"] == "https://www.moabultra.com/canyonlands-ultra-race-info"


def test_verify_event_urls_strips_dead_link() -> None:
    """A URL that isn't generic-looking but fails the live reachability check
    (dead link / real 404) must still be stripped -- the pre-existing behavior
    this refactor must not regress."""
    d = _discoverer()
    result = {
        "has_events": True,
        "events": [
            {
                "name": "Canyon Ultra",
                "venue": "Moab",
                "dates_in_range": "October 24, 2026",
                "url": "https://www.moabultra.com/canyonlands-ultra-race-info",
            },
        ],
    }

    with patch("generator.url_validator.URLValidator.verify_url", return_value=(False, 404)):
        verified = d._verify_event_urls(result)

    assert "url" not in verified["events"][0]


def test_verify_event_urls_noop_when_no_events() -> None:
    d = _discoverer()
    result = {"has_events": False, "honest_assessment": "No events found."}

    assert d._verify_event_urls(result) == result
