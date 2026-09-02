"""The per-day restaurant target must govern the published list.

ai_content applies `restaurants_per_day * day_count` to the AI-generated
list. With `restaurant_source: direct_link_batch` the published list is the
batch's instead -- a flat `restaurant_direct_batch_item_count` (20) per
destination, written to dinner_recommendations after that cap had already run
on a different list. The cap was correct and did not govern.

It went unnoticed because verified-link-or-seed was discarding 60-77% of
candidates, so the surviving count landed near the target by accident. Once
link discovery improved, the sw run published 18 dinner recommendations for a
two-night stop at Capitol Reef and 20 each at Bryce and Santa Fe.

Selection keeps range, not just the top-rated: eight variations on the same
expensive bistro is a worse answer than eight that differ.
"""

import pytest

from generator.ai_content import AIContentGenerator
from generator.main import _enforce_restaurant_per_day_cap

CUISINES = ["Italian", "Italian", "Thai", "Diner", "Italian", "BBQ", "Thai", "Sushi"]
PRICES = ["$$", "$$", "$", "$$$", "$$", "$", "$$", "$$$"]


def _restaurants(n=18):
    return [
        {"name": f"R{i}", "rating": 4.9 - i * 0.05,
         "cuisine": CUISINES[i % 8], "price_range": PRICES[i % 8]}
        for i in range(n)
    ]


def _trip(dates="October 21-22, 2026", n=18, per_day=None):
    dest = {"name": "Capitol Reef National Park", "dates": dates,
            "ai_content": {"dinner_recommendations": _restaurants(n)}}
    if per_day is not None:
        dest["restaurants_per_day"] = per_day
    return {"trip": {}, "destinations": [dest]}


def _kept(trip):
    return trip["destinations"][0]["ai_content"]["dinner_recommendations"]


def test_a_two_night_stop_gets_eight_not_eighteen():
    trip = _trip()
    counts = _enforce_restaurant_per_day_cap(trip, AIContentGenerator.__new__(AIContentGenerator))
    assert len(_kept(trip)) == 8
    assert counts["removed"] == 10


def test_a_longer_stay_keeps_more():
    trip = _trip(dates="October 21-24, 2026", n=20)
    _enforce_restaurant_per_day_cap(trip, AIContentGenerator.__new__(AIContentGenerator))
    assert len(_kept(trip)) == 16


def test_a_manifest_override_is_respected():
    trip = _trip(per_day=2)
    _enforce_restaurant_per_day_cap(trip, AIContentGenerator.__new__(AIContentGenerator))
    assert len(_kept(trip)) == 4


def test_a_list_already_under_target_is_untouched():
    trip = _trip(n=5)
    counts = _enforce_restaurant_per_day_cap(trip, AIContentGenerator.__new__(AIContentGenerator))
    assert len(_kept(trip)) == 5 and counts["removed"] == 0


def test_selection_spreads_cuisine_and_price():
    """Top-8-by-rating alone would be five Italians."""
    kept = AIContentGenerator.select_diverse_restaurants(_restaurants(), target=8)
    assert len({r["cuisine"] for r in kept}) >= 4
    assert len({r["price_range"] for r in kept}) == 3


def test_page_order_is_preserved():
    """Trimming must not silently re-sort the section by rating."""
    src = _restaurants()
    kept = AIContentGenerator.select_diverse_restaurants(src, target=8)
    order = [r["name"] for r in kept]
    assert order == [r["name"] for r in src if r["name"] in set(order)]


def test_blank_cuisine_does_not_claim_a_diversity_slot():
    items = [{"name": "A", "rating": 4.9, "cuisine": "", "price_range": ""},
             {"name": "B", "rating": 4.8, "cuisine": "", "price_range": ""},
             {"name": "C", "rating": 4.1, "cuisine": "Thai", "price_range": "$"},
             {"name": "D", "rating": 4.0, "cuisine": "BBQ", "price_range": "$$"}]
    kept = AIContentGenerator.select_diverse_restaurants(items, target=2)
    assert {r["name"] for r in kept} == {"C", "D"}


@pytest.mark.parametrize("bad", [None, "not a list", []])
def test_malformed_input_is_safe(bad):
    trip = {"trip": {}, "destinations": [{"name": "X", "dates": "October 1, 2026",
                                          "ai_content": {"dinner_recommendations": bad}}]}
    _enforce_restaurant_per_day_cap(trip, AIContentGenerator.__new__(AIContentGenerator))
