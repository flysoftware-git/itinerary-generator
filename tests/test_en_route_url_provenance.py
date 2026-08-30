"""Record which code path put a URL on an en-route stop.

The removal audit records why a link was REJECTED. Nothing recorded why one
was KEPT, so a wrong link that survives every gate is invisible.

Five Asheville-leg stops on the Old Hickory trip shared
https://www.knoxvilletn.gov -- a city homepage standing in for a waterfall
(Fall Creek Falls) and a national park (Great Smoky Mountains). Meanwhile
_retain_discovered_url rejects that URL in isolation (exit 17, generic section
landing page) for every one of those item names, and the audit's en-route loop
demonstrably ran, logging rejections for other stops in the same trip. Those
facts could not be reconciled by reading the code.
"""

from generator.main import _build_destination_status_report


def _report(stops):
    return _build_destination_status_report(
        trip={"destinations": [{
            "id": "asheville", "name": "Asheville, North Carolina",
            "_registry_decisions": [],
            "ai_content": {"getting_here": {"en_route_stops": stops}},
        }]},
        registry={"entities": [], "reports": [], "destination_view": {}},
        run_id="r", skip_events=True, skip_images=True, skip_url_discovery=False,
    )


def _sources(report):
    return report["destinations"][0]["stage_status"]["url_discovery"]["en_route_url_sources"]


def test_the_assigning_path_is_recorded():
    rows = _sources(_report([
        {"name": "Oak Ridge, Tennessee", "url": "https://www.knoxvilletn.gov",
         "_url_assigned_by": "direct_batch_existing_preserved"},
    ]))
    assert rows[0]["assigned_by"] == "direct_batch_existing_preserved"
    assert rows[0]["url"] == "https://www.knoxvilletn.gov"


def test_an_unstamped_assignment_is_visible_as_such():
    """A path nobody instrumented must not look like a path that was."""
    rows = _sources(_report([{"name": "Sunsphere", "url": "https://www.knoxvilletn.gov"}]))
    assert rows[0]["assigned_by"] == "(unstamped)"


def test_stops_without_a_url_are_not_listed():
    assert _sources(_report([{"name": "Nowhere"}])) == []


def test_seed_status_is_carried():
    rows = _sources(_report([
        {"name": "Knoxville, Tennessee", "url": "https://www.knoxvilletn.gov",
         "is_seed": True, "_url_assigned_by": "discovery_selected"},
    ]))
    assert rows[0]["is_seed"] is True


def test_shared_urls_are_all_listed_so_collisions_are_countable():
    rows = _sources(_report([
        {"name": "Fall Creek Falls", "url": "https://www.knoxvilletn.gov", "_url_assigned_by": "a"},
        {"name": "Oak Ridge", "url": "https://www.knoxvilletn.gov", "_url_assigned_by": "b"},
        {"name": "Sunsphere", "url": "https://www.knoxvilletn.gov", "_url_assigned_by": "b"},
    ]))
    assert len(rows) == 3
    assert len({r["url"] for r in rows}) == 1, "the collision must be visible in the data"
