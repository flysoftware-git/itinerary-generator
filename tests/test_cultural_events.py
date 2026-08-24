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


def test_init_loads_cultural_events_into_default_deferred_categories() -> None:
    """config.yaml's multi_site_grouping.base_owned_categories now
    includes cultural_events by default (dipstick67 fix) -- confirm
    CulturalEventsDiscoverer.__init__ actually loads it, not just
    multi_site_grouping.py's own constant."""
    mock_llm = type("MockLLM", (), {"usage_tracker": None})()
    with patch.dict("os.environ", {"XAI_API_KEY": "test-key"}):
        discoverer = CulturalEventsDiscoverer(config_path="config.yaml", llm_client=mock_llm)

    assert "cultural_events" in discoverer._multi_site_base_owned_categories


def test_discover_skips_generation_for_deferred_grouped_child() -> None:
    """dipstick67 fix: cultural_events.py's discover() now skips the
    entire Grok search + LLM synthesis call for a grouped child (e.g.
    Canyonlands) whose cultural_events category is deferred to its group
    base (e.g. Moab) -- the real API cost is avoided entirely, not just
    hidden at render time. This differs from restaurants/scenic-drives,
    which are bundled into ai_content.py's one combined LLM call and can
    only skip the separate discovery/verification step; cultural events
    have their own dedicated per-destination call, so the generation
    itself can be skipped."""
    d = _discoverer()
    d._multi_site_base_owned_categories = frozenset({"restaurant", "cultural_events"})

    def _boom(dest):
        raise AssertionError("_discover_for_dest should not be called for a deferred grouped child")

    d._discover_for_dest = _boom

    trip = {
        "destinations": [
            {
                "id": "canyonlands",
                "name": "Canyonlands National Park",
                "dates": "August 3, 2026",
                "group_with": "moab",
            },
        ]
    }
    d.discover(trip)

    assert trip["destinations"][0]["cultural_events"] == {}


def test_discover_still_runs_for_group_base_itself() -> None:
    """The group base (no group_with) always supplies its own real
    cultural_events regardless of the deferred-category config -- only a
    grouped child ever defers (category_deferred_to_base's is_grouped()
    guard)."""
    d = _discoverer()
    d._multi_site_base_owned_categories = frozenset({"restaurant", "cultural_events"})

    calls: list[str] = []

    def _fake(dest):
        calls.append(dest["name"])
        return {"has_events": False, "honest_assessment": "No ticketed events were confidently verified."}

    d._discover_for_dest = _fake

    trip = {"destinations": [{"id": "moab", "name": "Moab", "dates": "August 1-4, 2026"}]}
    d.discover(trip)

    assert calls == ["Moab"]
    assert trip["destinations"][0]["cultural_events"] == {
        "has_events": False,
        "honest_assessment": "No ticketed events were confidently verified.",
    }


def test_discover_runs_for_grouped_child_when_cultural_events_not_deferred() -> None:
    """A grouped child still gets its own independent cultural-events
    discovery call when cultural_events isn't in the resolved
    base_owned_categories (e.g. an explicit per-entry override, or a
    project config that only defers restaurants)."""
    d = _discoverer()
    d._multi_site_base_owned_categories = frozenset({"restaurant"})  # no cultural_events

    calls: list[str] = []

    def _fake(dest):
        calls.append(dest["name"])
        return {"has_events": False, "honest_assessment": "ok"}

    d._discover_for_dest = _fake

    trip = {
        "destinations": [
            {
                "id": "canyonlands",
                "name": "Canyonlands National Park",
                "dates": "August 3, 2026",
                "group_with": "moab",
            },
        ]
    }
    d.discover(trip)

    assert calls == ["Canyonlands National Park"]


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
    attractions/restaurants rely on) now strips those before they render.

    dipstick62 follow-up: both events must still end up with SOME link --
    a Google Maps search fallback, matching every other content type --
    rather than rendering with no link at all."""
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

    verified = d._verify_event_urls(result, "Moab")

    assert all(
        e["url"].startswith("https://www.google.com/maps/search/?api=1&query=")
        for e in verified["events"]
    )
    assert "moab.org" not in verified["events"][0]["url"]
    assert "moabultra.com" not in verified["events"][1]["url"]


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
    this refactor must not regress. It then gets a maps fallback like any
    other verified-away event URL."""
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
        verified = d._verify_event_urls(result, "Moab")

    assert "moabultra.com" not in verified["events"][0]["url"]
    assert verified["events"][0]["url"].startswith("https://www.google.com/maps/search/?api=1&query=")


def test_verify_event_urls_noop_when_no_events() -> None:
    d = _discoverer()
    result = {"has_events": False, "honest_assessment": "No events found."}

    assert d._verify_event_urls(result) == result


def test_verify_event_urls_assigns_maps_fallback_when_no_url_present() -> None:
    """dipstick62 real gap: Moab's "Canyonlands Ultra" cultural event never
    had a url field at all -- confirmed via live reproduction that the only
    search result available was a single bundled Moab-wide events-calendar
    page covering a dozen unrelated events, not a per-event page, so the
    synthesis prompt correctly omitted the url field rather than fabricating
    one. Every other content type (attractions, restaurants, en-route stops)
    falls back to a Google Maps search link when no real URL survives; events
    previously had no equivalent and rendered with no link at all."""
    d = _discoverer()
    result = {
        "has_events": True,
        "events": [
            {
                "name": "Canyonlands Ultra",
                "venue": "Moab",
                "dates_in_range": "October 24, 2026",
                "admission": "Varies",
            },
        ],
    }

    verified = d._verify_event_urls(result, "Moab")

    assert verified["events"][0]["url"] == (
        "https://www.google.com/maps/search/?api=1&query=Canyonlands%20Ultra%20Moab"
    )


def test_verify_event_urls_fallback_omits_redundant_destination_scope() -> None:
    """When the event name already contains the destination/venue name,
    don't pad the maps query with a redundant repeat."""
    d = _discoverer()
    result = {
        "has_events": True,
        "events": [
            {
                "name": "Moab Music Festival",
                "venue": "Moab",
                "dates_in_range": "September 2-18, 2026",
            },
        ],
    }

    verified = d._verify_event_urls(result, "Moab")

    assert verified["events"][0]["url"] == (
        "https://www.google.com/maps/search/?api=1&query=Moab%20Music%20Festival"
    )


def test_verify_event_urls_fallback_skipped_without_event_name() -> None:
    d = _discoverer()
    result = {
        "has_events": True,
        "events": [
            {"venue": "Moab", "dates_in_range": "October 24, 2026"},
        ],
    }

    verified = d._verify_event_urls(result, "Moab")

    assert "url" not in verified["events"][0]


def test_verify_local_tip_url_assigns_maps_fallback_when_no_url_present() -> None:
    """Project owner's concrete example: "Check out Moab Farmers Market" names
    a real, findable place but local_tip is plain prose with no way to link
    it. When the synthesis prompt names the specific place (local_tip_name)
    but the search results didn't surface a per-place URL, mirror
    _verify_event_urls' exact discipline and fall back to a Google Maps
    search scoped to that place -- same as ticketed events already get."""
    d = _discoverer()
    result = {
        "has_events": False,
        "honest_assessment": "Moab has a reliable weekly market scene.",
        "local_tip": "Check out Moab Farmers Market on Thursday evenings for fresh produce and live music.",
        "local_tip_name": "Moab Farmers Market",
    }

    verified = d._verify_local_tip_url(result, "Moab")

    assert verified["local_tip_url"] == (
        "https://www.google.com/maps/search/?api=1&query=Moab%20Farmers%20Market"
    )


def test_verify_local_tip_url_keeps_live_specific_url() -> None:
    """A specific, reachable, non-generic URL supplied by synthesis for the
    named place must survive verification unstripped."""
    d = _discoverer()
    result = {
        "has_events": False,
        "honest_assessment": "Moab has a reliable weekly market scene.",
        "local_tip": "Check out Moab Farmers Market on Thursday evenings.",
        "local_tip_name": "Moab Farmers Market",
        "local_tip_url": "https://moabfarmersmarket.org/",
    }

    with patch("generator.url_validator.URLValidator.verify_url", return_value=(True, 200)):
        verified = d._verify_local_tip_url(result, "Moab")

    assert verified["local_tip_url"] == "https://moabfarmersmarket.org/"


def test_verify_local_tip_url_strips_dead_link_then_falls_back() -> None:
    d = _discoverer()
    result = {
        "has_events": False,
        "honest_assessment": "Moab has a reliable weekly market scene.",
        "local_tip": "Check out Moab Farmers Market on Thursday evenings.",
        "local_tip_name": "Moab Farmers Market",
        "local_tip_url": "https://moabfarmersmarket.org/dead-page",
    }

    with patch("generator.url_validator.URLValidator.verify_url", return_value=(False, 404)):
        verified = d._verify_local_tip_url(result, "Moab")

    assert "moabfarmersmarket.org" not in verified["local_tip_url"]
    assert verified["local_tip_url"].startswith("https://www.google.com/maps/search/?api=1&query=")


def test_verify_local_tip_url_strips_generic_landing_page() -> None:
    d = _discoverer()
    result = {
        "has_events": False,
        "honest_assessment": "Moab has a reliable weekly market scene.",
        "local_tip": "Check out Moab Farmers Market on Thursday evenings.",
        "local_tip_name": "Moab Farmers Market",
        "local_tip_url": "https://www.moab.org/things-to-do",
    }

    verified = d._verify_local_tip_url(result, "Moab")

    assert "moab.org" not in verified["local_tip_url"]
    assert verified["local_tip_url"].startswith("https://www.google.com/maps/search/?api=1&query=")


def test_verify_local_tip_url_stays_empty_for_generic_tip_without_named_place() -> None:
    """A generic tip with nothing specific to link (no local_tip_name) must
    NOT be forced onto a maps fallback -- there's nothing real to link to."""
    d = _discoverer()
    result = {
        "has_events": False,
        "honest_assessment": "Moab has a reliable weekly market scene.",
        "local_tip": "Check ranger talks posted at the visitor center desk.",
    }

    verified = d._verify_local_tip_url(result, "Moab")

    assert "local_tip_url" not in verified
    assert "local_tip_name" not in verified


def test_verify_local_tip_url_noop_when_has_events_or_no_tip() -> None:
    d = _discoverer()
    result_has_events = {"has_events": True, "events": []}
    assert d._verify_local_tip_url(result_has_events, "Moab") == result_has_events

    result_no_tip = {"has_events": False, "honest_assessment": "Quiet scene."}
    assert d._verify_local_tip_url(result_no_tip, "Moab") == result_no_tip


def test_extract_local_tip_venue_name_finds_at_anchored_venue() -> None:
    """Real example: synthesis omitted local_tip_name for a tip that
    unambiguously names one place ("Alloy Kitchen"), project-owner
    reported. The text-extraction fallback must recover it."""
    tip = (
        "Check out live music at Alloy Kitchen, which hosts shows every "
        "Sunday through October. It's a great way to experience local "
        "talent in a relaxed setting."
    )
    assert CulturalEventsDiscoverer._extract_local_tip_venue_name(tip) == "Alloy Kitchen"


def test_extract_local_tip_venue_name_finds_check_out_anchored_venue() -> None:
    """Second real, previously-flagged example from this same docstring's
    history: "Check out Moab Farmers Market" with no preposition before
    the name at all."""
    tip = "Check out Moab Farmers Market for local produce and crafts."
    assert CulturalEventsDiscoverer._extract_local_tip_venue_name(tip) == "Moab Farmers Market"


def test_extract_local_tip_venue_name_strips_leading_article() -> None:
    tip = "Visit the Santa Fe Farmers Market on Saturday morning."
    assert CulturalEventsDiscoverer._extract_local_tip_venue_name(tip) == "Santa Fe Farmers Market"


def test_extract_local_tip_venue_name_returns_empty_for_generic_advice() -> None:
    """Conservative by design: no anchor + Title Case match means no
    extraction, matching this pipeline's fail-closed default (a miss here
    is a missing link, never a wrong one)."""
    assert CulturalEventsDiscoverer._extract_local_tip_venue_name(
        "Enjoy the quiet mountain evenings and stargazing from your lodging."
    ) == ""
    assert CulturalEventsDiscoverer._extract_local_tip_venue_name(
        "Check ranger talks posted at the visitor center desk."
    ) == ""


def test_verify_local_tip_url_recovers_missing_name_via_text_extraction() -> None:
    """Full-path regression for the real Alloy Kitchen case: local_tip_name
    was omitted by synthesis, but the tip text names one specific,
    findable place -- _verify_local_tip_url must recover it via the
    extraction fallback and still produce a maps-fallback link, rather
    than silently dropping a real, linkable place."""
    d = _discoverer()
    result = {
        "has_events": False,
        "honest_assessment": "Pagosa Springs has a relaxed live-music scene.",
        "local_tip": (
            "Check out live music at Alloy Kitchen, which hosts shows every "
            "Sunday through October."
        ),
        # local_tip_name deliberately absent, matching the real failure.
    }

    verified = d._verify_local_tip_url(result, "Pagosa Springs")

    assert verified["local_tip_name"] == "Alloy Kitchen"
    assert verified["local_tip_url"] == (
        "https://www.google.com/maps/search/?api=1&query=Alloy%20Kitchen%20Pagosa%20Springs"
    )


class TestCulturalEventsModelIsPinned:
    """Cultural events must not resolve its own model independently.

    Issue #65/#64 normalised this away for url_discovery: GrokSearch used to
    fall through to os.environ XAI_MODEL / "grok-latest", disconnected from
    whatever the client actually resolved. The fix was applied to one call
    site and missed here.

    Measured on runs 3 and 4 (2026-08-22): url_discovery ran grok-4.3 while
    cultural_events ran grok-4-fast in the same process.
    """

    def test_search_model_override_is_applied(self, tmp_path, monkeypatch):
        from unittest.mock import patch
        from generator.cultural_events import CulturalEventsDiscoverer
        cfg = tmp_path / "c.yaml"
        cfg.write_text("url_discovery:\n  search_model: grok-4.3\n"
                       "cultural_events:\n  search_provider: grok\n", encoding="utf-8")
        (tmp_path / "x").mkdir(exist_ok=True)
        llm = type("M", (), {"provider": "openai", "model": "gpt-4o-mini", "usage_tracker": None})()
        with patch("generator.search_provider.GrokSearch") as grok, \
             patch("generator.search_provider.ClaudeSearch"), \
             patch.object(CulturalEventsDiscoverer, "_load_multi_site_grouping_config", lambda *a, **k: None):
            CulturalEventsDiscoverer(str(cfg), llm_client=llm)
        assert grok.call_args.kwargs["model"] == "grok-4.3"

    def test_a_minimal_client_does_not_crash_it(self, tmp_path):
        """An ad-hoc llm_client without provider/model must leave the model
        unpinned rather than raise -- an unpinned model is a reporting
        problem, a crash is a broken run."""
        from unittest.mock import patch
        from generator.cultural_events import CulturalEventsDiscoverer
        cfg = tmp_path / "c.yaml"
        cfg.write_text("cultural_events:\n  search_provider: grok\n", encoding="utf-8")
        llm = type("M", (), {"usage_tracker": None})()
        with patch("generator.search_provider.GrokSearch") as grok, \
             patch("generator.search_provider.ClaudeSearch"), \
             patch.object(CulturalEventsDiscoverer, "_load_multi_site_grouping_config", lambda *a, **k: None):
            CulturalEventsDiscoverer(str(cfg), llm_client=llm)
        assert grok.call_args.kwargs["model"] is None
