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


def _decision_with_trail(name, section, entity_class, trail):
    d = _decision(name, section, entity_class)
    d["candidate_trail"] = trail
    d["candidates_considered"] = sum(1 for e in trail if e.get("url"))
    return d


def test_the_report_says_which_url_was_refused_and_by_what():
    """A count of rejections cannot say which item they belonged to.

    Prague's totals showed url_collision_rejected: 6 with no way to tell
    whether Prague Castle was among them, so the cause could only be guessed.
    """
    trail = [
        {"reason": "direct_batch_candidate_rejected", "url": "https://tripadvisor.com/x", "source": "direct_batch"},
        {"reason": "url_collision_rejected", "url": "https://www.hrad.cz/en", "source": "direct_batch"},
    ]
    trip = {"destinations": [{
        "id": "prague", "name": "Prague, Czech Republic",
        "_registry_decisions": [_decision_with_trail("Prague Castle", "top_attractions", "attraction", trail)],
    }]}
    report = _build_destination_status_report(
        trip=trip, registry={"entities": [], "reports": [], "destination_view": {}},
        run_id="r", skip_events=True, skip_images=True, skip_url_discovery=False,
    )
    item = report["destinations"][0]["stage_status"]["url_discovery"]["removed_no_verified_url"][0]
    assert item["candidates_considered"] == 2
    assert [e["reason"] for e in item["candidate_trail"]] == [
        "direct_batch_candidate_rejected", "url_collision_rejected",
    ]


def test_markdown_shows_the_refusing_check_per_url(tmp_path):
    trail = [{"reason": "url_collision_rejected", "url": "https://www.hrad.cz/en", "source": "direct_batch"}]
    trip = {"destinations": [{
        "id": "prague", "name": "Prague, Czech Republic",
        "_registry_decisions": [_decision_with_trail("Prague Castle", "top_attractions", "attraction", trail)],
    }]}
    report = _build_destination_status_report(
        trip=trip, registry={"entities": [], "reports": [], "destination_view": {}},
        run_id="r", skip_events=True, skip_images=True, skip_url_discovery=False,
    )
    text = _write_destination_status_markdown_report(tmp_path, report).read_text(encoding="utf-8")
    assert "Prague Castle — top_attractions (1 candidate(s) considered)" in text
    assert "url_collision_rejected: https://www.hrad.cz/en" in text


def test_an_item_no_url_was_ever_found_for_reads_as_zero_candidates(tmp_path):
    """Distinguishes a discovery gap from a filter refusing a good link."""
    trip = {"destinations": [{
        "id": "brussels", "name": "Brussels, Belgium",
        "_registry_decisions": [_decision_with_trail("Fritland", "dinner_recommendations", "restaurant", [])],
    }]}
    report = _build_destination_status_report(
        trip=trip, registry={"entities": [], "reports": [], "destination_view": {}},
        run_id="r", skip_events=True, skip_images=True, skip_url_discovery=False,
    )
    text = _write_destination_status_markdown_report(tmp_path, report).read_text(encoding="utf-8")
    assert "Fritland — dinner_recommendations (0 candidate(s) considered)" in text


def test_removal_trail_reads_the_real_event_keys():
    """Exercise _removal_trail against a real thread, not a hand-built one.

    The first version of this feature read reason_code/rendered_url -- the
    _log_decision parameter names -- while the thread stores reason/url. Every
    lookup returned empty, so the report said "0 candidate(s) considered" for
    every removed item in a full Europe run. That reads as a finding (nothing
    was ever found for these) rather than as a bug, and it was only caught
    because 238 trail events had been captured while every count was zero.

    The tests above build the trail in its output shape and so never touched
    the extraction. This one starts where the pipeline starts.
    """
    from threading import Lock

    from generator.url_discovery import URLDiscoverer

    d = URLDiscoverer.__new__(URLDiscoverer)
    d._decision_threads_by_destination = {}
    d._request_cache_lock = Lock()
    d._decision_event_sequence = 0

    ctx = dict(kind="attraction", dest_name="Prague, Czech Republic", item_name="Prague Castle")
    d._record_disposition_thread_event(
        trace_id=d._trace_id(**ctx), reason_code="url_collision_rejected",
        source_code="direct_batch", message="already claimed",
        rendered_url="https://www.hrad.cz/en", **ctx,
    )

    trail = d._removal_trail(**ctx)
    assert trail == [{
        "reason": "url_collision_rejected",
        "url": "https://www.hrad.cz/en",
        "source": "direct_batch",
    }]


def test_removal_trail_is_empty_for_an_item_with_no_events():
    from threading import Lock

    from generator.url_discovery import URLDiscoverer

    d = URLDiscoverer.__new__(URLDiscoverer)
    d._decision_threads_by_destination = {}
    d._request_cache_lock = Lock()
    d._decision_event_sequence = 0
    assert d._removal_trail(kind="attraction", dest_name="Nowhere", item_name="Nothing") == []


def test_a_retried_destination_does_not_double_its_candidate_trail():
    """The retry fix cleared _registry_decisions but not the disposition threads.

    Those accumulate across passes, so a retried destination logged each
    candidate once per pass -- correct removal counts, inflated
    candidates_considered. Brussels showed 65 duplicated events of 131 while
    no un-retried destination showed any.
    """
    from threading import Lock

    from generator.url_discovery import URLDiscoverer

    d = URLDiscoverer.__new__(URLDiscoverer)
    d._decision_threads_by_destination = {}
    d._request_cache_lock = Lock()
    d._decision_event_sequence = 0

    ctx = dict(kind="restaurant", dest_name="Brussels, Belgium", item_name="Beijingya")
    for _ in range(3):  # three passes over the same item
        d._record_disposition_thread_event(
            trace_id=d._trace_id(**ctx), reason_code="url_collision_rejected",
            source_code="direct_batch", message="claimed",
            rendered_url="https://www.bruxellestoday.be/x.html", **ctx,
        )
    d._record_disposition_thread_event(
        trace_id=d._trace_id(**ctx), reason_code="direct_batch_candidate_rejected",
        source_code="direct_batch", message="generic",
        rendered_url="https://maps.example/q", **ctx,
    )

    trail = d._removal_trail(**ctx)
    assert len(trail) == 2, f"expected 2 distinct candidates, got {len(trail)}"
    assert {e["url"] for e in trail} == {
        "https://www.bruxellestoday.be/x.html", "https://maps.example/q",
    }


def test_the_same_url_refused_by_two_different_checks_is_kept_twice():
    """Dedupe is on (reason, url) -- two different refusals are two answers."""
    from threading import Lock

    from generator.url_discovery import URLDiscoverer

    d = URLDiscoverer.__new__(URLDiscoverer)
    d._decision_threads_by_destination = {}
    d._request_cache_lock = Lock()
    d._decision_event_sequence = 0

    ctx = dict(kind="attraction", dest_name="Prague, Czech Republic", item_name="Prague Castle")
    for reason in ("direct_batch_candidate_rejected", "url_collision_rejected"):
        d._record_disposition_thread_event(
            trace_id=d._trace_id(**ctx), reason_code=reason, source_code="direct_batch",
            message="m", rendered_url="https://www.hrad.cz/en", **ctx,
        )
    assert len(d._removal_trail(**ctx)) == 2
