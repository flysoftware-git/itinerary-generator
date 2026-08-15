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
