"""The lunch stop must be visible as one, and link to food.

_inject_lunch_stop_suggestions only appended a sentence to the schedule
("Break for lunch around Oak Ridge, Tennessee, roughly the midpoint"). The
picked stop rendered as an ordinary en-route card, and its map link pointed at
the place itself -- accurate, but not the thing being suggested. On the Old
Hickory trip the Oak Ridge lunch suggestion was present and invisible.
"""

from urllib.parse import unquote

from generator.ai_content import AIContentGenerator


def _trip(drive_minutes=240):
    stop = {"name": "Oak Ridge, Tennessee", "route_progress_ratio": 0.5}
    return {
        "destinations": [{
            "name": "Asheville, North Carolina",
            "ai_content": {
                "getting_here": {"travel_time": f"{drive_minutes} min", "en_route_stops": [stop]},
                "possible_daily_schedule": [{"periods": [{"summary": "Arrive and settle in."}]}],
            },
        }]
    }, stop


def _run(trip):
    gen = AIContentGenerator.__new__(AIContentGenerator)
    gen._lunch_stop_min_drive_minutes = 120
    gen._inject_lunch_stop_suggestions(trip)


def test_the_picked_stop_is_marked():
    trip, stop = _trip()
    _run(trip)
    assert stop.get("is_lunch_stop") is True


def test_the_link_searches_for_food_not_the_place():
    trip, stop = _trip()
    _run(trip)
    url = stop["lunch_maps_url"]
    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "restaurants near Oak Ridge, Tennessee" in unquote(url)


def test_the_schedule_sentence_is_still_added():
    trip, _ = _trip()
    _run(trip)
    summary = trip["destinations"][0]["ai_content"]["possible_daily_schedule"][0]["periods"][0]["summary"]
    assert "Break for lunch around Oak Ridge, Tennessee" in summary


def test_a_short_leg_marks_nothing():
    trip, stop = _trip(drive_minutes=30)
    gen = AIContentGenerator.__new__(AIContentGenerator)
    gen._lunch_stop_min_drive_minutes = 240
    gen._inject_lunch_stop_suggestions(trip)
    assert "is_lunch_stop" not in stop


def test_the_card_shows_a_badge_and_a_food_link():
    from generator.html_assembler import HTMLAssembler

    a = HTMLAssembler.__new__(HTMLAssembler)
    stop = {
        "name": "Oak Ridge, Tennessee",
        "is_lunch_stop": True,
        "lunch_maps_url": "https://www.google.com/maps/search/?api=1&query=restaurants%20near%20Oak%20Ridge",
        "url": "https://www.oakridgetn.gov/",
        "detour_miles": 24.0,
        "detour_minutes": 30,
    }
    ai = {"getting_here": {"en_route_stops": [stop], "travel_time": "4 hr", "distance_miles": 220}}
    html = a._build_getting_here(ai, {"name": "Asheville, North Carolina"}, "Old Hickory, Tennessee")
    assert "badge-lunch" in html and "Lunch stop" in html
    assert 'class="lunch-link"' in html and "Find lunch here" in html
