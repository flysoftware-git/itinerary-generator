"""End-to-end smoke test for the core content pipeline.

Gap this closes: the test suite has 698+ tests, but the overwhelming majority
construct classes via __new__ (bypassing __init__) and exercise one method in
isolation. Zero tests previously exercised the real chain of
generate_all -> discover_all -> audit_discovered_urls -> normalize_trip_content
-> HTMLAssembler.assemble with real __init__-constructed instances. Several
real bugs found in this project's history were "seam" bugs -- each piece
correct in isolation, the wiring between them wrong -- which unit tests
structurally cannot catch. This test mocks only the actual network boundary
(LLM calls, Grok search/harvest, and a blanket requests.Session.request
safety net) and lets everything else run for real: config loading, prompt
templates, normalization, entity-name matching, badge computation, template
checksum verification, HTML rendering.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from generator.ai_content import AIContentGenerator
from generator.html_assembler import HTMLAssembler
from generator.llm_client import UsageTracker
from generator.parser import ManifestParser
from generator.url_discovery import URLDiscoverer

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeLLMClient:
    """Stands in for MultiLLMClient: same public surface AIContentGenerator
    and URLDiscoverer actually touch (.provider, .model, .usage_tracker,
    .generate_json), but returns a fixed, schema-accurate destination bundle
    instead of making a real API call."""

    def __init__(self) -> None:
        self.provider = "openai"
        self.model = "gpt-4o-mini"
        self.usage_tracker = UsageTracker()

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        operation: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        return {
            "destination_content": {
                "expected_environment": {
                    "summary": "Clear fall days, cool nights, moderate crowds.",
                    "temperature_high_f": 72,
                    "temperature_low_f": 42,
                    "what_to_pack": ["layers", "sun hat", "water bottle"],
                },
                "getting_here": {
                    "drive_time": "2 hrs 15 min",
                    "distance_miles": 95,
                    "route_summary": "Take I-15 north through the canyon corridor.",
                    "en_route_stops": [
                        {
                            "name": "Overlook Point",
                            "description": "A quick pull-off with wide desert views.",
                            "practical_note": "Small gravel lot, room for 4-5 cars.",
                        }
                    ],
                },
                "top_attractions": [
                    {
                        "name": "Angels Landing",
                        "type": "hike",
                        "difficulty": "Strenuous",
                        "duration": "4-5 hrs round-trip",
                        "description": "A steep hike ending at a narrow ridge with canyon views.",
                        "practical_note": "Permit required via NPS lottery.",
                    },
                    {
                        "name": "Sunset Point",
                        "type": "viewpoint",
                        "difficulty": "Easy",
                        "duration": "20 min",
                        "description": "A short walk to a rim overlook facing west.",
                    },
                ],
                "possible_daily_schedule": [
                    "Morning: Arrive and settle in at lodging.",
                    "Afternoon: Angels Landing hike.",
                    "Evening: Dinner in town.",
                ],
                "dinner_recommendations": [
                    {
                        "name": "Spotted Dog Cafe",
                        "cuisine": "American",
                        "price_range": "$$",
                        "description": "A local spot with hearty plates and a full bar.",
                    }
                ],
            },
            "what_to_know": {
                "summary": "A compact canyon-floor destination best explored on foot or via shuttle.",
                "local_customs": "Shuttle etiquette: board in numbered-line order at each stop.",
                "best_times_of_day": "Early morning beats the midday heat and the shuttle lines.",
                "transportation_quirks": "Private vehicles are restricted on the main canyon road in season.",
            },
            "scenic_drives": [
                {
                    "title": "Canyon Scenic Drive",
                    "category": "drive",
                    "distance_or_duration": "20 miles",
                    "best_time": "Morning",
                    "description": "A paved road through canyon walls with several named pull-outs.",
                    "vehicle_requirement": "Any vehicle",
                }
            ],
        }


def _minimal_trip() -> dict[str, Any]:
    return {
        "trip": {
            "title": "Southwest Test Loop",
            "departure": "Las Vegas, NV",
            "return": "Las Vegas, NV",
        },
        "destinations": [
            {
                "id": "zion",
                "name": "Zion National Park",
                "dates": "October 10-11, 2026",
                # Matches a top_attractions name the fake LLM returns exactly,
                # so is_seed marking (url_discovery.py) can be verified as a
                # real cross-module contract, not a hand-set fixture value.
                "seeds": ["Angels Landing"],
                "lat": 37.2982,
                "lng": -113.0263,
            }
        ],
    }


@pytest.fixture
def _no_network(monkeypatch: pytest.MonkeyPatch):
    """Blanket safety net: any code path that reaches the real requests
    library (not just the mocked GrokSearch/_FakeLLMClient surfaces) fails
    loudly instead of making a real HTTP call. Every well-behaved path in
    this codebase treats a fetch failure as "couldn't verify" and degrades
    gracefully -- proven by the existing 600+ unit tests -- so this should
    never make the pipeline crash, only skip live verification."""
    monkeypatch.setenv("XAI_API_KEY", "test-key-not-real")
    # URLDiscoverer now also builds a Claude fallback client (config.yaml's
    # url_discovery.nonbatch_search_provider) -- needs a key present at
    # construct time even though the network is fully disabled below.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    with patch.object(
        requests.sessions.Session,
        "request",
        side_effect=requests.exceptions.ConnectionError("network disabled in test"),
    ):
        yield


def test_core_pipeline_generates_valid_checksummed_html_with_no_network(_no_network) -> None:
    trip = _minimal_trip()
    fake_llm = _FakeLLMClient()

    ai_gen = AIContentGenerator("config.yaml", llm_client=fake_llm)
    ai_gen.generate_all(trip)

    dest = trip["destinations"][0]
    assert dest["ai_content"]["top_attractions"], "content generation produced no attractions"
    assert dest["scenic_drives"], "content generation produced no scenic drives"

    url_discoverer = URLDiscoverer("config.yaml", llm_client=fake_llm, output_dir="output")
    url_discoverer._search = MagicMock()
    url_discoverer._search.is_circuit_open.return_value = False
    url_discoverer._search.chat_completion.return_value = ""

    url_discoverer.discover_all(trip)
    url_discoverer.audit_discovered_urls(trip)

    ai_gen.normalize_trip_content(trip)

    # Cross-module contract: a seed name from the manifest, echoed verbatim
    # by the (fake) LLM, must be marked is_seed by url_discovery.py -- this
    # is exactly the kind of seam that a mocked-in-isolation unit test can't
    # observe, since url_discovery.py's own tests never see real
    # AIContentGenerator output and ai_content.py's tests never see
    # url_discovery.py run.
    attractions_by_name = {a["name"]: a for a in dest["ai_content"]["top_attractions"]}
    assert "Angels Landing" in attractions_by_name
    assert attractions_by_name["Angels Landing"].get("is_seed") is True
    assert attractions_by_name["Sunset Point"].get("is_seed") is not True

    assembler = HTMLAssembler("config.yaml")
    html = assembler.assemble(trip)  # raises RuntimeError if template checksum mismatches

    assert "<html" in html.lower()
    assert "Angels Landing" in html
    assert "Sunset Point" in html
    assert "Spotted Dog Cafe" in html
    assert "Canyon Scenic Drive" in html
    # The seed badge for the manifest-requested attraction.
    assert '<span class="badge badge-seed">Your Pick</span>' in html
    # Banned-marketing-language enforcement ran as part of normalize_trip_content --
    # none of the fixture's clean prose should trip it, but this proves the call
    # didn't silently except-and-skip.
    assert ai_gen.last_banned_phrase_violations == {}


# ── GH #68 multi-site grouping: real Moab/Arches/Canyonlands manifest ───────


class _FakeMultiDestLLMClient:
    """Like _FakeLLMClient, but returns genuinely distinct content per
    destination id (keyed off the `destination_bundle:{id}` operation
    ai_content.py always passes) -- needed to prove Arches and Canyonlands
    keep their own real, distinct attractions/scenic-drives per docs/design/
    multi-site-destination-grouping.md §0/§6, not a duplicated fixture."""

    _BUNDLES: dict[str, dict[str, Any]] = {
        "moab": {
            "destination_content": {
                "expected_environment": {"summary": "Hot, dry desert days.", "temperature_high_f": 95, "temperature_low_f": 68, "what_to_pack": ["sun hat"]},
                "getting_here": {"drive_time": "1 hr 50 min", "distance_miles": 108, "route_summary": "Take I-70 to US-191 south.", "en_route_stops": []},
                "top_attractions": [],
                "possible_daily_schedule": ["Morning: Settle into lodging.", "Evening: Dinner in town."],
                "dinner_recommendations": [
                    {"name": "Moab Diner", "cuisine": "American", "price_range": "$$", "description": "Classic diner breakfast and burgers."},
                ],
            },
            "what_to_know": {"summary": "Basecamp town for Arches and Canyonlands.", "local_customs": "", "best_times_of_day": "", "transportation_quirks": ""},
            "scenic_drives": [],
        },
        "arches": {
            "destination_content": {
                "expected_environment": {"summary": "Hot, dry desert days.", "temperature_high_f": 96, "temperature_low_f": 66, "what_to_pack": ["sun hat"]},
                "getting_here": {"drive_time": "10 min", "distance_miles": 5, "route_summary": "Short hop north on US-191.", "en_route_stops": []},
                "top_attractions": [
                    {"name": "Delicate Arch", "type": "hike", "difficulty": "Moderate", "duration": "2-3 hrs round-trip", "description": "Iconic freestanding arch reached via a slickrock trail."},
                ],
                "possible_daily_schedule": ["Morning: Delicate Arch hike.", "Afternoon: Windows Section."],
                "dinner_recommendations": [
                    {"name": "Arches Snack Bar", "cuisine": "American", "price_range": "$", "description": "In-park quick bites."},
                ],
            },
            "what_to_know": {"summary": "Timed-entry reservation required in season.", "local_customs": "", "best_times_of_day": "", "transportation_quirks": ""},
            "scenic_drives": [
                {"title": "Arches Scenic Drive", "category": "drive", "distance_or_duration": "18 miles", "best_time": "Morning", "description": "Paved road past the park's major formations."},
            ],
        },
        "canyonlands": {
            "destination_content": {
                "expected_environment": {"summary": "Hot, dry desert days.", "temperature_high_f": 94, "temperature_low_f": 64, "what_to_pack": ["sun hat"]},
                "getting_here": {"drive_time": "40 min", "distance_miles": 32, "route_summary": "Head west on UT-313.", "en_route_stops": []},
                "top_attractions": [
                    {"name": "Grand View Point", "type": "viewpoint", "difficulty": "Easy", "duration": "30 min", "description": "Sweeping overlook of the canyon confluence below."},
                ],
                "possible_daily_schedule": ["Morning: Grand View Point.", "Afternoon: Mesa Arch."],
                "dinner_recommendations": [
                    {"name": "Canyonlands Cafe", "cuisine": "American", "price_range": "$", "description": "Casual stop near the visitor center."},
                ],
            },
            "what_to_know": {"summary": "No entrance timed-entry system, unlike Arches.", "local_customs": "", "best_times_of_day": "", "transportation_quirks": ""},
            "scenic_drives": [
                {"title": "Shafer Trail Road", "category": "drive", "distance_or_duration": "24 miles", "best_time": "Sunset", "description": "Switchback dirt road descending the canyon rim."},
            ],
        },
    }

    def __init__(self) -> None:
        self.provider = "openai"
        self.model = "gpt-4o-mini"
        self.usage_tracker = UsageTracker()

    def generate_json(self, *, system_prompt: str, user_prompt: str, operation: str, temperature: float | None = None, max_tokens: int | None = None) -> dict[str, Any]:
        dest_id = operation.split(":", 1)[1] if ":" in operation else ""
        if dest_id in self._BUNDLES:
            return self._BUNDLES[dest_id]
        # what_to_know / url_candidates / other non-bundle operations: return
        # a harmless empty-ish shape so callers that don't expect this
        # fixture's bundle keys don't blow up.
        return {"summary": "", "local_customs": "", "best_times_of_day": "", "transportation_quirks": ""}


def _geocode_moab_group(trip: dict[str, Any]) -> None:
    coords = {
        "moab": (38.5733, -109.5498),
        "arches": (38.7331, -109.5925),
        "canyonlands": (38.2, -109.93),
    }
    for dest in trip["destinations"]:
        lat, lng = coords[dest["id"]]
        dest["lat"] = lat
        dest["lng"] = lng


def test_moab_group_manifest_parses_discovers_and_renders_with_correct_grouping(_no_network) -> None:
    """Real Moab/Arches/Canyonlands-style manifest (docs/design/
    multi-site-destination-grouping.md's own example), run through the
    actual parse -> generate -> discover -> audit -> render chain with only
    the network boundary mocked. Confirms GH #68 end to end: schema
    validation accepts group_with, restaurants defer to the base by
    default, park-specific attractions/scenic-drives stay genuinely
    distinct per entry, nav tabs cluster, and the base's lodging is
    referenced (not duplicated) on both grouped children.
    """
    trip = ManifestParser().load(str(FIXTURES / "moab_group_manifest.yaml"))
    _geocode_moab_group(trip)

    fake_llm = _FakeMultiDestLLMClient()
    ai_gen = AIContentGenerator("config.yaml", llm_client=fake_llm)
    ai_gen.generate_all(trip)

    dest_by_id = {d["id"]: d for d in trip["destinations"]}
    assert dest_by_id["arches"]["ai_content"]["top_attractions"][0]["name"] == "Delicate Arch"
    assert dest_by_id["canyonlands"]["ai_content"]["top_attractions"][0]["name"] == "Grand View Point"

    url_discoverer = URLDiscoverer("config.yaml", llm_client=fake_llm, output_dir="output")
    url_discoverer._search = MagicMock()
    url_discoverer._search.is_circuit_open.return_value = False
    url_discoverer._search.chat_completion.return_value = ""

    url_discoverer.discover_all(trip)

    # §5 default: restaurant is the only base-owned category out of the box --
    # both grouped children defer dining to Moab, the base keeps its own.
    assert dest_by_id["moab"]["ai_content"]["dinner_recommendations"], "base entry should keep its own restaurants"
    assert dest_by_id["arches"]["ai_content"]["dinner_recommendations"] == []
    assert dest_by_id["canyonlands"]["ai_content"]["dinner_recommendations"] == []

    # §0/§6: attractions and scenic drives stay genuinely distinct per park --
    # discovery never touched/cleared them (only restaurant is base-owned by default).
    assert dest_by_id["arches"]["scenic_drives"]
    assert dest_by_id["canyonlands"]["scenic_drives"]
    assert dest_by_id["arches"]["scenic_drives"][0]["title"] != dest_by_id["canyonlands"]["scenic_drives"][0]["title"]

    url_discoverer.audit_discovered_urls(trip)
    ai_gen.normalize_trip_content(trip)

    assembler = HTMLAssembler("config.yaml")
    html = assembler.assemble(trip)  # raises RuntimeError if template checksum mismatches

    assert "<html" in html.lower()
    # §3 nav clustering: grouped children get no nav-tab entry at all (dipstick60
    # review) -- their content lives nested inside Moab's own section instead.
    assert 'class="tab-group"' not in html
    assert 'data-tab="section-arches"' not in html
    assert 'data-tab="section-canyonlands"' not in html
    assert 'data-tab="section-moab"' in html
    # §2 lodging dedup pointer on both grouped children, base's own lodging
    # never duplicated as a separate full block
    assert html.count("Based from Moab Springs Ranch") == 2
    # §5 rendering: "see base" pointer for the deferred restaurant category
    assert html.count("Dinner recommendations: see Moab") == 2
    assert "Moab Diner" in html
    # §0: each grouped entry's own genuinely distinct content survives to render
    assert "Delicate Arch" in html
    assert "Grand View Point" in html
    assert "Arches Scenic Drive" in html
    assert "Shafer Trail Road" in html
    # §4: grouped entries render as day trips, not relocation legs
    assert html.count("Day Trip") == 2
