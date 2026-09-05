"""Two en-route stops must not publish the same link.

Each assignment path validated its own choice and none consulted what the
others had claimed, so one plausible page could stand in for several places at
once. On the Old Hickory trip: five Asheville-leg stops shared
https://www.knoxvilletn.gov -- a city homepage for Fall Creek Falls and Great
Smoky Mountains National Park -- and Lebanon and Franklin each had a pair
sharing a URL, one via direct_batch_existing_preserved and one via
discovery_selected.

Nothing was wrong with either claim alone. Only the pair was, which is why
every gate passed it and static reading of any single path found nothing.
Restaurants have had this guard (claimed_restaurant_urls) all along.
"""

from generator.url_discovery import URLDiscoverer


def test_a_second_claim_on_the_same_url_is_refused():
    claimed = set()
    url = "https://www.knoxvilletn.gov"
    assert URLDiscoverer._url_already_claimed(url, claimed) is False
    claimed.add(URLDiscoverer._collision_key(url))
    assert URLDiscoverer._url_already_claimed(url, claimed) is True


def test_a_different_url_is_unaffected():
    claimed = {URLDiscoverer._collision_key("https://www.knoxvilletn.gov")}
    assert URLDiscoverer._url_already_claimed("https://www.nps.gov/grsm/", claimed) is False


def test_trivial_url_variants_collide():
    """The pair that shipped differed only in trailing slash across paths."""
    claimed = {URLDiscoverer._collision_key("https://thehermitage.com/")}
    assert URLDiscoverer._url_already_claimed("https://thehermitage.com", claimed) is True


def test_both_colliding_paths_consult_the_claim_set():
    """Guarding one path only would have moved the collision, not removed it.

    The shipped pairs had one stop from each path, so a guard on either alone
    still publishes a shared link.
    """
    import inspect

    src = inspect.getsource(URLDiscoverer._discover_en_route_stops)
    assert src.count("claimed_en_route_urls") >= 4, "both assignment paths must claim and check"
    for marker in ('"direct_batch_existing_preserved"', '"discovery_selected"'):
        head = src.index(marker)
        assert "_url_already_claimed" in src[max(0, head - 2000):head], f"{marker} unguarded"


def test_the_refusal_is_recorded_not_silent():
    import inspect

    src = inspect.getsource(URLDiscoverer._discover_en_route_stops)
    assert src.count("en_route_url_collision_rejected") >= 2
