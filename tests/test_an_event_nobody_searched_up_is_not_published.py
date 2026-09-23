"""A cultural event has to come from the search, and its map has to be a place.

Both cases here were published. The Southwest guide built on 2026-09-22 from
v3 carried three cultural events, every one of them linking to a Google Maps
search, two of those for somewhere that does not exist:

    Drew Baldridge              -> maps?query=St. George area St. George, Utah
    Zion: We Own the Night Tour -> maps?query=Zion area Zion National Park
    Rocky Horror Picture Show   -> maps?query=Telluride

The link was the symptom. "Zion: We Own the Night Tour" appears in none of
that destination's eight search results -- the model knew Under Canvas runs
that tour and wrote it down. "Rocky Horror Picture Show" is Telluride's real,
sourced "Telluride Horror Show" crossed with a St. George result for "The
Rocky Horror Picture Show Movie Screening", carried over with the St. George
screening's October 24 date. The sourced Telluride event was dropped for it.

An event with no source has no URL and no real venue, which is how it reaches
the page as a map of an area rather than a place.
"""

from __future__ import annotations

import pytest

from generator.cultural_events import CulturalEventsDiscoverer as CED

# Verbatim from the 2026-09-22 build log, Zion National Park.
ZION_RESULTS = [
    {"name": "St. George Concert in the Park Series 2026",
     "url": "https://events.greaterzion.com/event/st-george-concert-in-the-park-series-2026-5"},
    {"name": "Trail Hero Music Fest",
     "url": "https://zionutahjellystonepark.com/fall-events-near-jellystone-zion-trail-hero-m"},
    {"name": "Live Music at AutoCamp Zion",
     "url": "https://autocamp.com/event/live-music-zion/2026-10-17/"},
    {"name": "Browncoat Ball 2026",
     "url": "https://www.facebook.com/BrowncoatBall/posts/browncoat-ball-2026-will-be-in-zion"},
]

# Verbatim from the same log, Telluride.
TELLURIDE_RESULTS = [
    {"name": "Oktoberfest | Visit Telluride", "url": "https://www.telluride.com/event/oktoberfest/"},
    {"name": "Telluride Horror Show | Visit Telluride",
     "url": "https://www.telluride.com/event/telluride-horror-show/"},
    {"name": "Festivals | Visit Telluride", "url": "https://www.telluride.com/festivals-events/festivals/"},
    {"name": "Telluride Festivals & Events in Colorado",
     "url": "https://www.colorado.com/co/telluride/festivals-events"},
]


class TestTheEventCameFromTheSearch:
    def test_the_invented_zion_tour_is_dropped(self) -> None:
        assert not CED._event_is_in_the_search_results(
            {"name": "Zion: We Own the Night Tour"}, ZION_RESULTS
        )

    def test_the_transplanted_telluride_screening_is_dropped(self) -> None:
        assert not CED._event_is_in_the_search_results(
            {"name": "Rocky Horror Picture Show"}, TELLURIDE_RESULTS
        )

    def test_the_real_telluride_event_is_kept(self) -> None:
        assert CED._event_is_in_the_search_results(
            {"name": "Telluride Horror Show"}, TELLURIDE_RESULTS
        )

    def test_an_event_named_exactly_as_a_result_is_kept(self) -> None:
        assert CED._event_is_in_the_search_results(
            {"name": "Live Music at AutoCamp Zion"}, ZION_RESULTS
        )

    def test_a_renamed_event_is_kept(self) -> None:
        """The model rewording a result's title is not an invention."""
        results = [{"name": "A City Different Dia de los Muertos - Burn Zozobra",
                    "url": "https://www.santafe.org/events/dia-de-los-muertos/"}]
        assert CED._event_is_in_the_search_results(
            {"name": "Dia de los Muertos Celebration"}, results
        )

    def test_an_accented_name_matches_its_unaccented_source(self) -> None:
        results = [{"name": "A City Different Dia de los Muertos", "url": "https://x.test/"}]
        assert CED._event_is_in_the_search_results(
            {"name": "Día de los Muertos"}, results
        )

    def test_an_event_named_only_in_a_calendar_snippet_is_kept(self) -> None:
        """The bundled-calendar case the maps fallback exists to serve.

        Matching on titles alone would have dropped it and undone dipstick62.
        """
        results = [{
            "name": "Events - Bryce Canyon Country",
            "snippet": "Ticaboo ATV Rally, Canyonlands Ultra, and Heritage StarFest this month.",
            "url": "https://www.brycecanyoncountry.com/events-calendar/",
        }]
        assert CED._event_is_in_the_search_results({"name": "Canyonlands Ultra"}, results)

    def test_a_failed_search_does_not_condemn_the_event(self) -> None:
        assert CED._event_is_in_the_search_results({"name": "Telluride Horror Show"}, [])

    def test_a_name_of_pure_filler_words_is_not_judged(self) -> None:
        assert CED._event_is_in_the_search_results({"name": "Annual Festival 2026"}, ZION_RESULTS)

    def test_the_drop_clears_has_events_when_nothing_survives(self) -> None:
        discoverer = CED.__new__(CED)
        result = discoverer._drop_events_with_no_source(
            {"has_events": True, "events": [{"name": "Zion: We Own the Night Tour"}]},
            ZION_RESULTS,
            "Zion National Park",
        )
        assert result["events"] == []
        assert result["has_events"] is False

    def test_the_drop_keeps_the_sourced_event_beside_the_invented_one(self) -> None:
        discoverer = CED.__new__(CED)
        result = discoverer._drop_events_with_no_source(
            {"has_events": True, "events": [
                {"name": "Rocky Horror Picture Show"},
                {"name": "Telluride Horror Show"},
            ]},
            TELLURIDE_RESULTS,
            "Telluride",
        )
        assert [e["name"] for e in result["events"]] == ["Telluride Horror Show"]
        assert result["has_events"] is True


class TestTheVenueNamesAPlace:
    @pytest.mark.parametrize("venue,dest", [
        ("Zion area", "Zion National Park"),
        ("St. George area", "St. George, Utah"),
        ("Telluride", "Telluride"),
        ("Greater Zion area", "Zion National Park"),
        ("various venues", "Santa Fe"),
        ("downtown Telluride", "Telluride"),
        ("TBD", "Moab"),
        ("", "Moab"),
    ])
    def test_a_vague_venue_is_not_mappable(self, venue: str, dest: str) -> None:
        assert not CED._venue_names_a_place(venue, dest)

    @pytest.mark.parametrize("venue,dest", [
        ("Under Canvas Zion", "Zion National Park"),
        ("The Palm Theatre", "Telluride"),
        ("Tuacahn Amphitheatre", "St. George, Utah"),
        ("Sheridan Opera House", "Telluride"),
        ("Bryce Canyon Visitor Center", "Bryce Canyon National Park"),
    ])
    def test_a_real_venue_is_mappable(self, venue: str, dest: str) -> None:
        assert CED._venue_names_a_place(venue, dest)


class TestNoMapOfNowhereReachesTheCard:
    def _verify(self, event: dict, dest: str) -> dict:
        discoverer = CED.__new__(CED)
        result = discoverer._verify_event_urls(
            {"has_events": True, "events": [event]}, dest
        )
        return result["events"][0]

    def test_the_zion_area_event_gets_no_link(self) -> None:
        event = self._verify(
            {"name": "Zion: We Own the Night Tour", "venue": "Zion area"},
            "Zion National Park",
        )
        assert not event.get("url"), (
            "a maps search for 'Zion area Zion National Park' is not a lookup"
        )

    def test_the_event_keeps_its_other_fields(self) -> None:
        event = self._verify(
            {"name": "Drew Baldridge", "venue": "St. George area",
             "date": "October 17", "admission": "Varies"},
            "St. George, Utah",
        )
        assert event["name"] == "Drew Baldridge"
        assert event["date"] == "October 17"
        assert event["admission"] == "Varies"

    def test_a_real_venue_still_gets_its_map(self) -> None:
        event = self._verify(
            {"name": "Rocky Horror Picture Show", "venue": "The Palm Theatre"},
            "Telluride",
        )
        assert event["url"].startswith("https://www.google.com/maps/search/")
        assert "Palm" in event["url"]
