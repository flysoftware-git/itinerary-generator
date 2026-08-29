"""Removed items must be recorded by name, not only counted.

The quality gate reported "restaurants removed for no verified URL: 39" and
no artifact said which 39. _registry_decisions carried the names in memory;
main.py counted them and dropped them. Diagnosing a removal spike therefore
required a fresh paid run, and the first hypothesis it was used to test
(an exhausted search-provider balance) turned out to be wrong.
"""

import json

from generator.main import (
    _build_destination_status_report,
    _write_destination_status_markdown_report,
)


def _decision(name, section, entity_class, reason="no_verified_url_removed"):
    return {
        "display_name": name,
        "entity_class": entity_class,
        "section_target": section,
        "validation_status": "rejected",
        "rejection_reasons": [reason],
    }


def _trip():
    return {
        "destinations": [
            {
                "id": "brussels",
                "name": "Brussels, Belgium",
                "_registry_decisions": [
                    _decision("Chez Leon", "dinner_recommendations", "restaurant"),
                    _decision("Aux Armes", "dinner_recommendations", "restaurant"),
                    _decision("Atomium", "top_attractions", "attraction"),
                    # a rejection for an unrelated reason must not be swept in
                    _decision("Kept Cafe", "dinner_recommendations", "restaurant", "duplicate_url"),
                ],
            }
        ]
    }


def _report():
    return _build_destination_status_report(
        trip=_trip(),
        registry={"entities": [], "reports": [], "destination_view": {}},
        run_id="test-run",
        skip_events=True,
        skip_images=True,
        skip_url_discovery=False,
    )


def _url_stage(report):
    return report["destinations"][0]["stage_status"]["url_discovery"]


def test_removed_items_are_listed_by_name():
    stage = _url_stage(_report())
    names = [i["display_name"] for i in stage["removed_no_verified_url"]]
    assert sorted(names) == ["Atomium", "Aux Armes", "Chez Leon"]
    assert stage["removed_no_verified_url_count"] == 3


def test_other_rejection_reasons_are_not_swept_in():
    names = [i["display_name"] for i in _url_stage(_report())["removed_no_verified_url"]]
    assert "Kept Cafe" not in names


def test_each_item_says_where_it_was_removed_from():
    items = {i["display_name"]: i for i in _url_stage(_report())["removed_no_verified_url"]}
    assert items["Chez Leon"]["section_target"] == "dinner_recommendations"
    assert items["Atomium"]["entity_class"] == "attraction"


def test_the_report_is_json_serialisable():
    json.dumps(_report())


def test_markdown_summary_names_them(tmp_path):
    path = _write_destination_status_markdown_report(tmp_path, _report())
    text = path.read_text(encoding="utf-8")
    assert "## Removed for No Verified URL (3)" in text
    assert "Chez Leon" in text and "Atomium" in text
    assert "Kept Cafe" not in text


def test_markdown_says_none_when_nothing_was_removed(tmp_path):
    report = _build_destination_status_report(
        trip={"destinations": [{"id": "d", "name": "D", "_registry_decisions": []}]},
        registry={"entities": [], "reports": [], "destination_view": {}},
        run_id="r", skip_events=True, skip_images=True, skip_url_discovery=False,
    )
    text = _write_destination_status_markdown_report(tmp_path, report).read_text(encoding="utf-8")
    assert "## Removed for No Verified URL (0)" in text


def test_retry_replaces_the_audit_trail_rather_than_appending():
    """A retried destination must not have its removals counted twice.

    url_discovery appends to _registry_decisions and never resets it, so a
    selective retry re-running discovery over the same dest dict stacked a
    second set of removal records on the first. The quality gate counts those
    records, so retried destinations reported roughly double: an Old Hickory
    run recorded 37 removals for 20 distinct items, and the duplicated
    destinations were exactly the two the run reported as retried.
    """
    from generator.main import _selective_retry_destinations

    dest = {
        "id": "oldhickory",
        "name": "Old Hickory, Tennessee",
        "_registry_decisions": [_decision("Salvo's Pizza", "dinner_recommendations", "restaurant")],
    }
    trip = {"trip": {}, "destinations": [dest]}
    status_report = {
        "destinations": [{
            "destination_id": "oldhickory",
            "status": "needs_retry",
            "retry_recommended": True,
            "retry_triggers": ["url_acceptance_ratio_below_threshold"],
        }]
    }

    seen = {}

    def _fake_run_urls(subset_trip):
        # whatever the retry finds, it starts from a clean trail
        seen["at_entry"] = list(subset_trip["destinations"][0]["_registry_decisions"])
        subset_trip["destinations"][0]["_registry_decisions"].append(
            _decision("Salvo's Pizza", "dinner_recommendations", "restaurant")
        )

    _selective_retry_destinations(
        trip=trip, status_report=status_report, config_path="config.yaml",
        llm_client=None, output_dir=None, refresh_image_cache=False,
        skip_events=True, skip_images=True, skip_url_discovery=False,
        no_trails=True, alltrails_source=None, attraction_source=None,
        restaurant_source=None, en_route_source=None,
        run_events=lambda *a, **k: None, run_images=lambda *a, **k: None,
        run_urls=_fake_run_urls,
    )

    assert seen["at_entry"] == [], "retry started from a stale audit trail"
    assert len(dest["_registry_decisions"]) == 1, "removal recorded twice for a retried destination"
