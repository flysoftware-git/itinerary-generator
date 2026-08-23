"""
main.py — CLI entry point for the Itinerary Generator.

Usage:
  python -m generator.main --manifest trip_manifest.yaml --output output/

Flags:
  --manifest        Path to trip manifest YAML (required)
  --output          Output directory (default: output/)
  --config          Path to config.yaml (default: config.yaml)
    --llm-provider    Override LLM provider for this run
    --llm-model       Override LLM model for this run
    --log-level       Console logging threshold (DEBUG, INFO, WARNING, ERROR, CRITICAL)
  --dry-run         Parse + validate manifest only; no AI calls, no output
    --noseed          Ignore destination seeds from the manifest for this run
  --skip-images     Skip image fetching (useful for fast content iteration)
  --skip-events     Skip cultural events discovery
  --skip-url-discovery  Skip URL discovery (AI content only)
    --notrails        Disable trail link discovery and omit trail links
    --alltrails-source  Choose trail-link source: direct-link-batch or search
    --attraction-source Choose non-trail attraction source: search or direct-link-batch
    --restaurant-source Choose restaurant source: search or direct-link-batch
    --en-route-source   Choose en-route stop source: search or direct-link-batch
  --destination     Process only this destination id (repeatable)
  --verbose         Enable debug logging
"""

from __future__ import annotations
import atexit
import json
import logging, os, sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
import click
from generator import __version__, __template_version__
from generator.entity_registry import build_entity_registry, reconcile_schedule_from_registry, reconcile_trip_from_registry

logger = logging.getLogger(__name__)
LOG_LEVEL_CHOICES = ["debug", "info", "warning", "error", "critical"]
_SECTION_TARGETS = {
    "top_attractions",
    "scenic_drives",
    "getting_here.en_route_stops",
    "getting_there.route_options",
    "dinner_recommendations",
    "cultural_events",
}


def _load_destination_retry_policy(config_path: str | Path) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "min_url_acceptance_ratio": 0.0,
        "min_accepted_by_section": {},
        "max_retries_per_destination_per_run": 1,
    }
    try:
        import yaml

        with Path(config_path).open(encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        raw_policy = cfg.get("destination_retry", {}) if isinstance(cfg.get("destination_retry", {}), dict) else {}

        min_ratio = raw_policy.get("min_url_acceptance_ratio", 0.0)
        try:
            parsed_ratio = float(min_ratio)
            policy["min_url_acceptance_ratio"] = min(1.0, max(0.0, parsed_ratio))
        except (TypeError, ValueError):
            policy["min_url_acceptance_ratio"] = 0.0

        raw_min_by_section = raw_policy.get("min_accepted_by_section", {})
        min_by_section: dict[str, int] = {}
        if isinstance(raw_min_by_section, dict):
            for section, value in raw_min_by_section.items():
                section_key = str(section or "").strip()
                if section_key not in _SECTION_TARGETS:
                    continue
                try:
                    count = int(value)
                except (TypeError, ValueError):
                    continue
                if count > 0:
                    min_by_section[section_key] = count
        policy["min_accepted_by_section"] = min_by_section

        max_retries = raw_policy.get("max_retries_per_destination_per_run", 1)
        try:
            parsed_max_retries = int(max_retries)
            policy["max_retries_per_destination_per_run"] = max(0, parsed_max_retries)
        except (TypeError, ValueError):
            policy["max_retries_per_destination_per_run"] = 1
    except Exception:
        return policy
    return policy


def _annotate_retry_outcomes(
    *,
    status_report: dict[str, Any],
    attempted_destination_ids: list[str],
    max_retries_per_destination_per_run: int,
) -> dict[str, Any]:
    destinations = status_report.get("destinations", []) if isinstance(status_report.get("destinations", []), list) else []
    attempted_set = set(attempted_destination_ids)
    max_attempts = max(0, int(max_retries_per_destination_per_run or 0))

    summary = {
        "max_retries_per_destination_per_run": max_attempts,
        "attempted_destination_count": 0,
        "resolved_after_retry_count": 0,
        "unresolved_after_retry_count": 0,
        "not_retried_due_to_cap_count": 0,
        "stable_without_retry_count": 0,
    }

    for row in destinations:
        if not isinstance(row, dict):
            continue
        destination_id = str(row.get("destination_id", "") or "").strip()
        retry_recommended = bool(row.get("retry_recommended", False))
        retry_triggers = row.get("retry_triggers", []) if isinstance(row.get("retry_triggers", []), list) else []
        attempted = bool(destination_id and destination_id in attempted_set)

        terminal_state = "stable_without_retry"
        if attempted and retry_recommended:
            terminal_state = "retry_cap_reached_unresolved"
            if "retry_cap_reached" not in retry_triggers:
                retry_triggers.append("retry_cap_reached")
            summary["attempted_destination_count"] += 1
            summary["unresolved_after_retry_count"] += 1
        elif attempted and not retry_recommended:
            terminal_state = "resolved_after_retry"
            summary["attempted_destination_count"] += 1
            summary["resolved_after_retry_count"] += 1
        elif (not attempted) and retry_recommended:
            terminal_state = "not_retried_due_to_cap"
            if max_attempts == 0 and "retry_cap_reached" not in retry_triggers:
                retry_triggers.append("retry_cap_reached")
            summary["not_retried_due_to_cap_count"] += 1
        else:
            summary["stable_without_retry_count"] += 1

        row["retry_triggers"] = retry_triggers
        row["retry_outcome"] = {
            "attempted": attempted,
            "attempts_used": 1 if attempted else 0,
            "attempt_cap": max_attempts,
            "terminal_state": terminal_state,
        }

    status_summary = status_report.get("summary", {}) if isinstance(status_report.get("summary", {}), dict) else {}
    status_summary["retry_outcomes"] = summary
    status_report["summary"] = status_summary
    return status_report


def _destination_ids_needing_attention(status_report: dict[str, Any]) -> list[str]:
    destinations = status_report.get("destinations", []) if isinstance(status_report.get("destinations", []), list) else []
    unresolved: list[str] = []
    for row in destinations:
        if not isinstance(row, dict):
            continue
        destination_id = str(row.get("destination_id", "") or "").strip()
        outcome = row.get("retry_outcome", {}) if isinstance(row.get("retry_outcome", {}), dict) else {}
        terminal_state = str(outcome.get("terminal_state", "") or "")
        if terminal_state in {"retry_cap_reached_unresolved", "not_retried_due_to_cap"} and destination_id:
            unresolved.append(destination_id)
    return unresolved


def _elapsed_seconds(started_at_perf: float) -> float:
    return round(max(0.0, perf_counter() - started_at_perf), 3)


def _build_retry_efficiency_metrics(
    *,
    destination_count: int,
    retry_candidate_ids: list[str],
    retried_destination_ids: list[str],
    unresolved_destination_ids: list[str],
    max_retries_per_destination_per_run: int,
) -> dict[str, Any]:
    total = max(0, int(destination_count or 0))
    candidate_count = len(list(dict.fromkeys(retry_candidate_ids)))
    retried_count = len(list(dict.fromkeys(retried_destination_ids)))
    unresolved_count = len(list(dict.fromkeys(unresolved_destination_ids)))

    retry_scope_ratio = (retried_count / total) if total > 0 else 0.0
    avoided_ratio = 1.0 - retry_scope_ratio if total > 0 else 0.0

    return {
        "destination_count": total,
        "retry_candidate_count": candidate_count,
        "retried_destination_count": retried_count,
        "unresolved_destination_count": unresolved_count,
        "retry_scope_ratio": round(retry_scope_ratio, 4),
        "retry_scope_reduction_percent": round(avoided_ratio * 100.0, 1),
        "max_retries_per_destination_per_run": max(0, int(max_retries_per_destination_per_run or 0)),
    }


def _counter_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    keys = set(before.keys()) | set(after.keys())
    delta: dict[str, int] = {}
    for key in keys:
        before_value = int(before.get(key, 0) or 0)
        after_value = int(after.get(key, 0) or 0)
        delta[key] = max(0, after_value - before_value)
    return delta


def _per_minute(count: int, seconds: float) -> float:
    if seconds <= 0:
        return 0.0
    return round((int(count or 0) * 60.0) / float(seconds), 3)


def _count_url_candidate_entities(trip: dict[str, Any]) -> int:
    total = 0
    for dest in trip.get("destinations", []) or []:
        if not isinstance(dest, dict):
            continue
        ai = dest.get("ai_content", {}) if isinstance(dest.get("ai_content", {}), dict) else {}

        top_attractions = ai.get("top_attractions", []) if isinstance(ai.get("top_attractions", []), list) else []
        restaurants = ai.get("dinner_recommendations", []) if isinstance(ai.get("dinner_recommendations", []), list) else []
        getting_here = ai.get("getting_here", {}) if isinstance(ai.get("getting_here", {}), dict) else {}
        en_route_stops = getting_here.get("en_route_stops", []) if isinstance(getting_here.get("en_route_stops", []), list) else []
        route_options = (
            ai.get("getting_there", {}).get("route_options", [])
            if isinstance(ai.get("getting_there", {}), dict)
            and isinstance(ai.get("getting_there", {}).get("route_options", []), list)
            else []
        )
        scenic_drives = dest.get("scenic_drives", []) if isinstance(dest.get("scenic_drives", []), list) else []

        total += len(top_attractions)
        total += len(restaurants)
        total += len(en_route_stops)
        total += len(route_options)
        total += len(scenic_drives)
    return total


def _strip_destination_seeds(trip: dict[str, Any]) -> int:
    stripped_count = 0
    for dest in trip.get("destinations", []) or []:
        if not isinstance(dest, dict):
            continue
        seeds = dest.get("seeds", [])
        if isinstance(seeds, list) and seeds:
            stripped_count += len(seeds)
        dest["seeds"] = []
    return stripped_count


def _resolve_privacy_redaction(mode: str | None, environment_selected: str) -> bool:
    """`planning_links` (Notion/reservation links) and lodging property
    names/websites carry personal data that must never reach a build meant
    for wider eyes. `auto` (the default) redacts only in `prod`, since that's
    the only environment whose output is ever committed/published; `on`/`off`
    override the environment-based default explicitly either direction.

    Deliberately NOT redacted: `lodging.location` and `lodging.checkin_time`.
    Both are load-bearing well beyond display -- `location` drives
    geocoding/routing (main.py) and is the search anchor for
    "restaurants near lodging" (url_discovery.py); `checkin_time` drives
    arrival-day schedule/prompt construction (ai_content.py). Redacting
    either would ripple into itinerary content quality, not just privacy.
    """
    normalized = (mode or "auto").lower()
    if normalized == "on":
        return True
    if normalized == "off":
        return False
    return environment_selected == "prod"


def _apply_privacy_redaction(trip: dict[str, Any]) -> dict[str, int]:
    """Redact planning_links and lodging.name in place. planning_links are
    replaced with a single placeholder entry (rather than emptied outright)
    so the renderer can show an explanatory pill instead of the button
    silently vanishing -- see html_assembler._build_header_links. lodging.name
    is blanked, not replaced, since its only consumers already fall back to
    lodging.location when name is empty (html_assembler._build_group_lodging_pointer
    and the grouped-destination banner), so no placeholder is needed there.
    """
    counts = {
        "planning_links": 0,
        "lodging_names": 0,
        "lodging_websites": 0,
        "lodging_confirmations": 0,
        "transportation": 0,
    }
    # Trip-wide legs (the flight in, the flight home, a whole-trip rental) live
    # on trip["trip"], not on any destination, and are exactly as sensitive.
    # Missing this would leave record locators on a published page while every
    # per-destination leg was correctly cleared.
    trip_meta = trip.get("trip")
    if isinstance(trip_meta, dict):
        trip_legs = trip_meta.get("transportation")
        if isinstance(trip_legs, list) and trip_legs:
            counts["transportation"] += len(trip_legs)
            trip_meta["transportation"] = []
    for dest in trip.get("destinations", []) or []:
        if not isinstance(dest, dict):
            continue
        links = dest.get("planning_links", [])
        if isinstance(links, list) and links:
            counts["planning_links"] += len(links)
            dest["planning_links"] = [{"label": "Trip Plans", "url": "", "redacted": True}]
        lodging = dest.get("lodging")
        if isinstance(lodging, dict) and str(lodging.get("name", "") or "").strip():
            counts["lodging_names"] += 1
            lodging["name"] = ""
        # Blanked for the same reason as name, not as a separate policy: a
        # link to the property's own site names the property. Redacting the
        # name while leaving "https://www.zionlodge.com/" one pill away would
        # protect nothing.
        if isinstance(lodging, dict) and str(lodging.get("website", "") or "").strip():
            counts["lodging_websites"] += 1
            lodging["website"] = ""
        # Highest-sensitivity field in the block: on most booking sites this
        # code plus a surname is enough to view, modify or cancel the stay.
        if isinstance(lodging, dict) and str(lodging.get("confirmation_number", "") or "").strip():
            counts["lodging_confirmations"] += 1
            lodging["confirmation_number"] = ""
        # Dropped wholesale rather than field-by-field like lodging above.
        # There is no routing or scheduling consumer downstream to keep alive
        # (unlike lodging.location/checkin_time), and no useful redacted
        # remainder: "a flight, to somewhere, at some time" is not worth
        # rendering, while carrier + record locator is enough to view or
        # change someone else's booking.
        legs = dest.get("transportation")
        if isinstance(legs, list) and legs:
            counts["transportation"] += len(legs)
            dest["transportation"] = []
    return counts


def _run_quality_gate(trip: dict[str, Any], html_path: "Path | None" = None) -> None:
    """Emit warnings for known quality regressions so they're visible on every run.

    Verified-link-or-seed policy (project owner decision, 2026-08-17):
    url_discovery.py's audit_discovered_urls now REMOVES non-seed
    attractions/en-route stops/restaurants that never got a real, verified
    source URL, rather than leaving them present with an empty url. That
    changes what this gate can still observe by walking the final trip data:

    - `no_url_*` below (present-in-the-list-but-no-url) should now normally
      be 0 for non-seed items, since those items are gone from the list
      entirely rather than present-with-no-url. It still walks the data
      (rather than being deleted) as a defensive check for skip-url-discovery
      runs or any other path where the audit/prune pass never ran.
    - `unverified_seed_*` counts seed items kept and shown with the
      "Unverified" badge -- expected, acceptable noise (the traveler's own
      request the pipeline couldn't verify), so it does NOT feed into
      warnings/go-no-go signal, only visibility.
    - `removed_no_verified_url_*` is the real successor to the old
      `no_url_*` signal: how many items were silently dropped this run for
      lacking a verified link. This is where a genuine harvest/recall
      regression would now show up, since the items themselves no longer
      appear anywhere else in the trip data for a human skimming the output
      to notice. Sourced from the `_registry_decisions` audit trail
      url_discovery.py records for every removal (rejection_reason
      "no_verified_url_removed").
    """
    import re as _re
    from pathlib import Path as _Path
    warnings: list[str] = []
    info_lines: list[str] = []

    synthetic_phrases = ("locally surfaced dinner option", "local dinner option")
    synthetic_count = 0
    no_url_attractions = 0
    no_url_stops = 0
    no_url_restaurants = 0
    unverified_seed_attractions = 0
    unverified_seed_stops = 0
    removed_no_verified_url_attractions = 0
    removed_no_verified_url_stops = 0
    removed_no_verified_url_restaurants = 0

    for dest in trip.get("destinations", []) or []:
        ai = dest.get("ai_content", {}) if isinstance(dest.get("ai_content"), dict) else {}

        for rest in ai.get("dinner_recommendations", []) or []:
            desc = str(rest.get("description", "") or "").strip().lower()
            if not desc or any(p in desc for p in synthetic_phrases):
                synthetic_count += 1
            if not str(rest.get("url", "") or rest.get("maps_url", "") or "").strip():
                # Restaurants have no seed concept anywhere in this codebase
                # (see url_discovery.py's audit_discovered_urls) -- one
                # reaching this point with no url means the audit/prune pass
                # didn't run (e.g. --skip-url-discovery), not an expected
                # "kept unverified" case.
                no_url_restaurants += 1

        for attr in ai.get("top_attractions", []) or []:
            if not str(attr.get("url", "") or attr.get("maps_url", "") or "").strip():
                if attr.get("is_seed"):
                    unverified_seed_attractions += 1
                else:
                    no_url_attractions += 1

        getting_here = ai.get("getting_here", {}) if isinstance(ai.get("getting_here"), dict) else {}
        for stop in getting_here.get("en_route_stops", []) or []:
            if not str(stop.get("url", "") or stop.get("maps_url", "") or "").strip():
                if stop.get("is_seed"):
                    unverified_seed_stops += 1
                else:
                    no_url_stops += 1

        for decision in dest.get("_registry_decisions", []) or []:
            if not isinstance(decision, dict):
                continue
            if "no_verified_url_removed" not in (decision.get("rejection_reasons", []) or []):
                continue
            section_target = str(decision.get("section_target", "") or "")
            entity_class = str(decision.get("entity_class", "") or "")
            if section_target == "dinner_recommendations" or entity_class == "restaurant":
                removed_no_verified_url_restaurants += 1
            elif section_target == "en_route_stops" or entity_class == "en_route_stop":
                removed_no_verified_url_stops += 1
            else:
                removed_no_verified_url_attractions += 1

    if synthetic_count:
        warnings.append(f"restaurants with synthetic description: {synthetic_count}")
    if no_url_restaurants:
        warnings.append(f"restaurants with no URL or maps fallback: {no_url_restaurants}")
    if no_url_attractions > 3:
        warnings.append(f"attractions with no URL or maps fallback: {no_url_attractions}")
    if no_url_stops > 2:
        warnings.append(f"en-route stops with no URL or maps fallback: {no_url_stops}")

    # Real successor to the old no_url_* signal: these items no longer
    # appear anywhere in the trip data, so this is the only place a genuine
    # harvest/recall regression is still visible. Same thresholds as the
    # no_url_* checks above, since it's the same underlying concern.
    if removed_no_verified_url_restaurants:
        warnings.append(
            "restaurants removed for no verified URL (verified-link-or-seed policy): "
            f"{removed_no_verified_url_restaurants}"
        )
    if removed_no_verified_url_attractions > 3:
        warnings.append(
            "attractions removed for no verified URL (verified-link-or-seed policy): "
            f"{removed_no_verified_url_attractions}"
        )
    if removed_no_verified_url_stops > 2:
        warnings.append(
            "en-route stops removed for no verified URL (verified-link-or-seed policy): "
            f"{removed_no_verified_url_stops}"
        )

    # Visibility only -- an unverified seed is expected/acceptable noise
    # (see docstring), not a pipeline-health signal, so it's reported
    # separately from `warnings` rather than gating quality-gate pass/fail.
    if unverified_seed_attractions or unverified_seed_stops:
        info_lines.append(
            "unverified seed items kept (shown with the Unverified badge, not counted "
            f"above): attractions: {unverified_seed_attractions}, "
            f"en-route stops: {unverified_seed_stops}"
        )

    if html_path:
        try:
            html = _Path(html_path).read_text(encoding="utf-8", errors="ignore")
            origin_hits = len(_re.findall(r'class="gmaps-link"[^>]*href="[^"]*[?&]origin=', html))
            if origin_hits:
                warnings.append(f"Getting Here route links with hardcoded origin=: {origin_hits}")
            synthetic_in_html = sum(html.lower().count(p) for p in synthetic_phrases)
            if synthetic_in_html:
                warnings.append(f"synthetic dinner phrases in rendered HTML: {synthetic_in_html}")
        except Exception:
            pass

    import click as _click
    if warnings:
        _click.echo("  ⚠ Quality gate — issues detected:")
        for w in warnings:
            _click.echo(f"      · {w}")
    else:
        _click.echo("  ✓ Quality gate passed")
    for line in info_lines:
        _click.echo(f"  ℹ {line}")


def _build_gate_a_metrics(
    *,
    trip: dict[str, Any],
    usage_summary: dict[str, Any],
    stage_timings: dict[str, float],
    skip_events: bool,
    skip_images: bool,
    skip_url_discovery: bool,
    image_counter_delta: dict[str, int],
    url_validator_counter_delta: dict[str, int],
) -> dict[str, Any]:
    records = usage_summary.get("records", []) if isinstance(usage_summary.get("records", []), list) else []

    ai_calls = 0
    ai_cost = 0.0
    cultural_search_calls = 0
    cultural_search_cost = 0.0
    cultural_synthesis_calls = 0
    cultural_synthesis_cost = 0.0
    url_search_calls = 0
    url_search_cost = 0.0

    for row in records:
        if not isinstance(row, dict):
            continue
        operation = str(row.get("operation", "") or "")
        cost = float(row.get("estimated_cost_usd", 0.0) or 0.0)
        if operation.startswith((
            "destination_content:",  # pre-merge name, kept for backward compatibility
            "destination_bundle:",   # current name -- dest_content + what_to_know merged
            "what_to_know:",
            "scenic_drives:",
            "url_candidates:",
        )):
            ai_calls += 1
            ai_cost = round(ai_cost + cost, 6)
        elif operation.startswith("cultural_events:search"):
            cultural_search_calls += 1
            cultural_search_cost = round(cultural_search_cost + cost, 6)
        elif operation.startswith("cultural_events:"):
            cultural_synthesis_calls += 1
            cultural_synthesis_cost = round(cultural_synthesis_cost + cost, 6)
        elif operation.startswith(("url_discovery:search", "url_discovery:chat_completion")):
            # Direct-batch HTML harvest calls (attraction/restaurant/en-route/trail
            # candidate lists) -- previously matched no branch at all, so this
            # (often the single largest cost in stage 4/5) was silently excluded
            # from stage_cost_usd and the url_discovery batch-ratio metrics.
            url_search_calls += 1
            url_search_cost = round(url_search_cost + cost, 6)
        elif operation.startswith(("url_discovery_fallback:search", "url_discovery_fallback:chat_completion")):
            # The fallback client (self._search_fallback, usage_operation_prefix
            # "url_discovery_fallback" -- see generator/search_provider.py)
            # gets its own operation prefix, distinct from the primary
            # batch client's "url_discovery:*". Found 2026-08-15 while
            # investigating unexplained cost during a Grok outage: every
            # fallback call (both the non-batch .search() path and the
            # cross-provider batch chat_completion() retry) was silently
            # excluded from this stage's cost/call attribution, making a
            # run that leaned heavily on the fallback look far cheaper and
            # less active than it actually was.
            url_search_calls += 1
            url_search_cost = round(url_search_cost + cost, 6)

    destination_count = len(trip.get("destinations", []) or [])
    nps_destination_count = sum(
        1
        for dest in (trip.get("destinations", []) or [])
        if isinstance(dest, dict) and str(dest.get("nps_park_code", "") or "").strip()
    )
    url_target_count = _count_url_candidate_entities(trip)

    stage_3_seconds = float(stage_timings.get("stage_3_ai_generation", 0.0) or 0.0)
    stage_4_5_seconds = float(stage_timings.get("stage_4_5_parallel", 0.0) or 0.0)

    ai_naive_calls = destination_count * 3
    events_naive_search_calls = 0 if skip_events else destination_count * 3
    url_naive_search_calls = 0 if skip_url_discovery else url_target_count
    image_naive_provider_calls = 0 if skip_images else (destination_count * 2 + nps_destination_count)

    image_actual_provider_calls = (
        int(image_counter_delta.get("nps_api_calls", 0) or 0)
        + int(image_counter_delta.get("unsplash_api_calls", 0) or 0)
        + int(image_counter_delta.get("wikimedia_api_calls", 0) or 0)
    )

    stage_4_5_cost = round(cultural_search_cost + cultural_synthesis_cost + url_search_cost, 6)
    total_estimated_cost = float(usage_summary.get("total_estimated_cost_usd", 0.0) or 0.0)

    return {
        "version": "v2.1-gate-a",
        "measurement_coverage": {
            "llm_usage_records": bool(records),
            "stage_cost_attribution": True,
            "stage_call_counters": True,
            "stage_throughput": True,
            "batch_ratio_metrics": True,
        },
        "provider_calls_by_stage": {
            "stage_3_ai_generation": {
                "llm_generate_json_calls": ai_calls,
            },
            "stage_4_5_parallel": {
                "cultural_events_search_calls": cultural_search_calls,
                "cultural_events_synthesis_calls": cultural_synthesis_calls,
                "url_discovery_search_calls": url_search_calls,
                "image_provider_calls": {
                    "nps_api_calls": int(image_counter_delta.get("nps_api_calls", 0) or 0),
                    "unsplash_api_calls": int(image_counter_delta.get("unsplash_api_calls", 0) or 0),
                    "wikimedia_api_calls": int(image_counter_delta.get("wikimedia_api_calls", 0) or 0),
                    "image_download_requests": int(image_counter_delta.get("image_download_requests", 0) or 0),
                    "cache_hits": int(image_counter_delta.get("cache_hits", 0) or 0),
                },
                "url_validation_http_calls": {
                    "head_requests": int(url_validator_counter_delta.get("head_requests", 0) or 0),
                    "get_requests": int(url_validator_counter_delta.get("get_requests", 0) or 0),
                    "get_text_requests": int(url_validator_counter_delta.get("get_text_requests", 0) or 0),
                },
            },
        },
        "stage_cost_usd": {
            "stage_3_ai_generation": round(ai_cost, 6),
            "stage_4_5_parallel": stage_4_5_cost,
            "stage_6_assemble_validate": 0.0,
            "total_estimated_cost_usd": round(total_estimated_cost, 6),
        },
        "throughput_entities_per_minute": {
            "stage_3_destinations_per_minute": _per_minute(destination_count, stage_3_seconds),
            "stage_4_5_url_targets_per_minute": _per_minute(url_target_count, stage_4_5_seconds),
            "stage_4_5_event_destinations_per_minute": 0.0 if skip_events else _per_minute(destination_count, stage_4_5_seconds),
            "stage_4_5_image_destinations_per_minute": 0.0 if skip_images else _per_minute(destination_count, stage_4_5_seconds),
        },
        "batch_work_ratio": {
            "ai_generation": {
                "naive_calls": ai_naive_calls,
                "actual_calls": ai_calls,
                "requests_avoided_vs_naive": max(0, ai_naive_calls - ai_calls),
                "destinations_per_provider_request": round((destination_count / ai_calls), 4) if ai_calls > 0 else 0.0,
            },
            "cultural_events_search": {
                "naive_calls": events_naive_search_calls,
                "actual_calls": cultural_search_calls,
                "requests_avoided_vs_naive": max(0, events_naive_search_calls - cultural_search_calls),
                "destinations_per_provider_request": round((destination_count / cultural_search_calls), 4) if cultural_search_calls > 0 else 0.0,
            },
            "url_discovery_search": {
                "naive_calls": url_naive_search_calls,
                "actual_calls": url_search_calls,
                "requests_avoided_vs_naive": max(0, url_naive_search_calls - url_search_calls),
                "url_targets_per_provider_request": round((url_target_count / url_search_calls), 4) if url_search_calls > 0 else 0.0,
            },
            "image_acquisition": {
                "naive_calls": image_naive_provider_calls,
                "actual_calls": image_actual_provider_calls,
                "requests_avoided_vs_naive": max(0, image_naive_provider_calls - image_actual_provider_calls),
                "destinations_per_provider_request": round((destination_count / image_actual_provider_calls), 4) if image_actual_provider_calls > 0 else 0.0,
            },
        },
        "assumptions": {
            "naive_ai_calls_per_destination": 3,
            "naive_cultural_search_calls_per_destination": 3,
            "naive_url_search_calls_per_target": 1,
            "naive_image_provider_calls_per_destination": "unsplash + wikimedia (+nps when park code exists)",
        },
    }


def _reconcile_trip_via_registry(trip: dict, *, return_registry: bool = False) -> dict | tuple[dict, dict[str, Any]]:
    registry = build_entity_registry(trip)
    reconciled = reconcile_trip_from_registry(trip, registry)
    # Runs against the final entity state (every section, not just
    # top_attractions) rather than the earlier, narrower audit-time pass --
    # see generator/entity_registry.py:reconcile_schedule_from_registry.
    reconcile_schedule_from_registry(reconciled, registry)
    if return_registry:
        return reconciled, registry
    return reconciled


def _write_entity_registry_debug_report(output_dir: Path, registry: dict[str, Any]) -> Path:
    entities = registry.get("entities", []) if isinstance(registry.get("entities", []), list) else []
    reports = registry.get("reports", []) if isinstance(registry.get("reports", []), list) else []
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "entity_count": len(entities),
            "destination_count": len(registry.get("destination_view", {})),
            "accepted_count": sum(len(report.get("accepted", [])) for report in reports if isinstance(report, dict)),
            "rejected_count": sum(len(report.get("rejected", [])) for report in reports if isinstance(report, dict)),
            "reassigned_count": sum(len(report.get("reassigned", [])) for report in reports if isinstance(report, dict)),
            "quarantined_count": sum(len(report.get("quarantined", [])) for report in reports if isinstance(report, dict)),
        },
        "registry": registry,
    }
    path = output_dir / "entity_registry_debug.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _build_destination_status_report(
    *,
    trip: dict[str, Any],
    registry: dict[str, Any],
    run_id: str,
    skip_events: bool,
    skip_images: bool,
    skip_url_discovery: bool,
    retry_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    def _compute_en_route_reliability(url_discovery_meta: dict[str, Any]) -> dict[str, Any]:
        threads = (
            url_discovery_meta.get("disposition_threads", {})
            if isinstance(url_discovery_meta.get("disposition_threads", {}), dict)
            else {}
        )

        terminal_events: list[dict[str, Any]] = []
        for _trace_id, events in threads.items():
            if not isinstance(events, list):
                continue
            en_route_events = [
                event
                for event in events
                if isinstance(event, dict) and str(event.get("kind", "") or "") == "en_route_stop"
            ]
            if not en_route_events:
                continue
            en_route_events.sort(key=lambda row: int(row.get("seq", 0) or 0))
            terminal_events.append(en_route_events[-1])

        total = len(terminal_events)
        if total <= 0:
            return {
                "total_en_route_stops": 0,
                "resolved_with_url": 0,
                "exhaustion_or_no_match": 0,
                "resolution_rate": 1.0,
            }

        resolved = 0
        exhausted = 0
        source_locked = 0
        no_canonical = 0
        for event in terminal_events:
            reason = str(event.get("reason", "") or "")
            url = str(event.get("url", "") or "").strip()

            if reason == "discovery_completed" and url:
                resolved += 1
                continue

            if reason == "direct_batch_source_locked_no_match":
                source_locked += 1
                exhausted += 1
                continue

            if reason == "no_canonical_url":
                no_canonical += 1
                exhausted += 1
                continue

            if reason == "discovery_completed" and not url:
                exhausted += 1

        return {
            "total_en_route_stops": total,
            "resolved_with_url": resolved,
            "exhaustion_or_no_match": exhausted,
            "source_locked_no_match": source_locked,
            "no_canonical_url": no_canonical,
            "resolution_rate": round((resolved / total), 4),
        }

    entities = registry.get("entities", []) if isinstance(registry.get("entities", []), list) else []
    reports = registry.get("reports", []) if isinstance(registry.get("reports", []), list) else []
    destination_view = registry.get("destination_view", {}) if isinstance(registry.get("destination_view", {}), dict) else {}

    effective_retry_policy = retry_policy if isinstance(retry_policy, dict) else {}
    min_url_acceptance_ratio = float(effective_retry_policy.get("min_url_acceptance_ratio", 0.0) or 0.0)
    min_url_acceptance_ratio = min(1.0, max(0.0, min_url_acceptance_ratio))
    min_accepted_by_section = (
        effective_retry_policy.get("min_accepted_by_section", {})
        if isinstance(effective_retry_policy.get("min_accepted_by_section", {}), dict)
        else {}
    )

    by_destination_entities: dict[str, list[dict[str, Any]]] = {}
    entity_by_id: dict[str, dict[str, Any]] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_id = str(entity.get("entity_id", "") or "")
        destination_id = str(entity.get("destination_id", "") or "")
        by_destination_entities.setdefault(destination_id, []).append(entity)
        if entity_id:
            entity_by_id[entity_id] = entity

    report_by_destination: dict[str, dict[str, Any]] = {}
    for report in reports:
        if not isinstance(report, dict):
            continue
        destination_id = str(report.get("destination_id", "") or "")
        if destination_id:
            report_by_destination[destination_id] = report

    destination_statuses: list[dict[str, Any]] = []
    status_counts = {
        "healthy": 0,
        "degraded": 0,
        "needs_retry": 0,
        "quarantined": 0,
    }

    for dest in trip.get("destinations", []) or []:
        if not isinstance(dest, dict):
            continue
        destination_id = str(dest.get("id", "") or "")
        destination_name = str(dest.get("name", "") or destination_id)
        destination_entities = by_destination_entities.get(destination_id, [])
        reconciliation_report = report_by_destination.get(destination_id, {})

        validation_counts = {
            "total": len(destination_entities),
            "accepted": 0,
            "pending": 0,
            "rejected": 0,
            "excluded": 0,
            "needs_retry": 0,
            "quarantined": 0,
        }
        for entity in destination_entities:
            status = str(entity.get("validation_status", "pending") or "pending").strip().lower()
            if status in validation_counts:
                validation_counts[status] += 1
            else:
                validation_counts["pending"] += 1

        rejected = reconciliation_report.get("rejected", []) if isinstance(reconciliation_report.get("rejected", []), list) else []
        rejected_reasons = sorted(
            {
                str(reason or "")
                for row in rejected
                if isinstance(row, dict)
                for reason in (row.get("reasons", []) or [])
                if str(reason or "")
            }
        )
        quarantined_entity_ids = (
            reconciliation_report.get("quarantined", [])
            if isinstance(reconciliation_report.get("quarantined", []), list)
            else []
        )

        image_count = len(dest.get("images", []) or []) if isinstance(dest.get("images", []), list) else 0
        cultural_events = dest.get("cultural_events", {}) if isinstance(dest.get("cultural_events", {}), dict) else {}
        event_count = len(cultural_events.get("events", []) or []) if isinstance(cultural_events.get("events", []), list) else 0
        url_discovery_meta = dest.get("_url_discovery", {}) if isinstance(dest.get("_url_discovery", {}), dict) else {}
        url_reason_counts = (
            url_discovery_meta.get("reason_counts", {})
            if isinstance(url_discovery_meta.get("reason_counts", {}), dict)
            else {}
        )
        url_source_counts = (
            url_discovery_meta.get("source_counts", {})
            if isinstance(url_discovery_meta.get("source_counts", {}), dict)
            else {}
        )
        url_thread_count = int(url_discovery_meta.get("thread_count", 0) or 0)
        url_event_count = int(url_discovery_meta.get("event_count", 0) or 0)
        en_route_reliability = _compute_en_route_reliability(url_discovery_meta)
        ai = dest.get("ai_content", {}) if isinstance(dest.get("ai_content", {}), dict) else {}
        rendered_no_url_attractions = sum(
            1
            for item in (ai.get("top_attractions", []) or [])
            if isinstance(item, dict) and not str(item.get("url", "") or item.get("maps_url", "") or "").strip()
        )
        rendered_no_url_restaurants = sum(
            1
            for item in (ai.get("dinner_recommendations", []) or [])
            if isinstance(item, dict) and not str(item.get("url", "") or "").strip()
        )
        getting_here = ai.get("getting_here", {}) if isinstance(ai.get("getting_here", {}), dict) else {}
        rendered_no_url_stops = sum(
            1
            for item in (getting_here.get("en_route_stops", []) or [])
            if isinstance(item, dict) and not str(item.get("url", "") or "").strip()
        )

        retry_triggers: list[str] = []
        if quarantined_entity_ids or validation_counts["quarantined"] > 0:
            retry_triggers.append("registry_quarantined_entities")
        if validation_counts["needs_retry"] > 0:
            retry_triggers.append("registry_entities_needing_retry")
        if not skip_images and image_count == 0:
            retry_triggers.append("image_shortfall")
        if not skip_url_discovery and validation_counts["total"] > 0 and validation_counts["accepted"] == 0:
            retry_triggers.append("url_collapse")
        if not skip_url_discovery and (rendered_no_url_attractions or rendered_no_url_restaurants or rendered_no_url_stops):
            retry_triggers.append("rendered_items_missing_links")

        accepted_for_ratio = validation_counts["accepted"]
        evaluated_for_ratio = (
            validation_counts["accepted"]
            + validation_counts["rejected"]
            + validation_counts["excluded"]
            + validation_counts["needs_retry"]
            + validation_counts["quarantined"]
        )
        url_acceptance_ratio = (accepted_for_ratio / evaluated_for_ratio) if evaluated_for_ratio > 0 else 1.0
        if (
            not skip_url_discovery
            and min_url_acceptance_ratio > 0.0
            and evaluated_for_ratio > 0
            and url_acceptance_ratio < min_url_acceptance_ratio
        ):
            retry_triggers.append("url_acceptance_ratio_below_threshold")

        section_counts: dict[str, dict[str, int]] = {}
        destination_sections = destination_view.get(destination_id, {}) if isinstance(destination_view.get(destination_id, {}), dict) else {}
        for section_name, refs in destination_sections.items():
            if section_name not in _SECTION_TARGETS or not isinstance(refs, list):
                continue
            section_total = 0
            section_accepted = 0
            for ref in refs:
                entity = entity_by_id.get(str(ref or ""))
                if not isinstance(entity, dict):
                    continue
                section_total += 1
                if str(entity.get("validation_status", "") or "").strip().lower() in {"accepted", "pending"}:
                    section_accepted += 1
            section_counts[section_name] = {
                "total": section_total,
                "accepted": section_accepted,
            }

        for section_name, minimum_required in min_accepted_by_section.items():
            if section_name not in _SECTION_TARGETS:
                continue
            try:
                min_required = int(minimum_required)
            except (TypeError, ValueError):
                continue
            if min_required <= 0:
                continue
            current = section_counts.get(section_name, {"total": 0, "accepted": 0})
            if current.get("total", 0) <= 0:
                continue
            if current.get("accepted", 0) < min_required:
                retry_triggers.append(f"section_minimum_not_met:{section_name}")

        # Scope which stages a retry actually needs to touch, instead of the
        # selective-retry pass blanket-rerunning events+images+urls for every
        # flagged destination regardless of which section actually failed.
        # cultural_events is the only registry section events discovery owns;
        # every other _SECTION_TARGETS section is url-discovery-owned.
        needs_retry_entities = [
            entity
            for entity in destination_entities
            if str(entity.get("validation_status", "") or "").strip().lower() in {"needs_retry", "quarantined"}
        ]
        section_minimum_triggers = {
            trigger.split(":", 1)[1]
            for trigger in retry_triggers
            if trigger.startswith("section_minimum_not_met:")
        }
        events_section_counts = section_counts.get("cultural_events", {"total": 0, "accepted": 0})
        # url_collapse/url_acceptance_ratio_below_threshold are computed across
        # *all* entities regardless of section, so they can fire purely from a
        # cultural_events shortfall -- section_counts gives the per-section
        # breakdown needed to attribute the shortfall to the right stage
        # instead of trusting those trigger names directly.
        non_events_section_incomplete = any(
            section_name != "cultural_events" and counts.get("total", 0) > counts.get("accepted", 0)
            for section_name, counts in section_counts.items()
        )
        needs_events_retry = (
            any(str(entity.get("section_target", "") or "") == "cultural_events" for entity in needs_retry_entities)
            or events_section_counts.get("total", 0) > events_section_counts.get("accepted", 0)
            or "cultural_events" in section_minimum_triggers
        )
        needs_urls_retry = (
            any(str(entity.get("section_target", "") or "") != "cultural_events" for entity in needs_retry_entities)
            or non_events_section_incomplete
            or bool(rendered_no_url_attractions or rendered_no_url_restaurants or rendered_no_url_stops)
            or bool(section_minimum_triggers - {"cultural_events"})
        )
        needs_images_retry = "image_shortfall" in retry_triggers
        if retry_triggers and not (needs_events_retry or needs_images_retry or needs_urls_retry):
            # A trigger fired that isn't mapped to a specific stage above (e.g.
            # a registry-level quarantine not traceable to a per-entity
            # section_target) -- fail safe by retrying every stage rather than
            # silently skipping one.
            needs_events_retry = needs_images_retry = needs_urls_retry = True
        retry_stage_scope = {
            "events": needs_events_retry,
            "images": needs_images_retry,
            "urls": needs_urls_retry,
        }

        destination_status = "healthy"
        if quarantined_entity_ids or validation_counts["quarantined"] > 0:
            destination_status = "quarantined"
        elif retry_triggers:
            destination_status = "needs_retry"
        elif validation_counts["rejected"] > 0:
            destination_status = "degraded"

        status_counts[destination_status] += 1

        destination_statuses.append(
            {
                "destination_id": destination_id,
                "destination_name": destination_name,
                "status": destination_status,
                "retry_recommended": destination_status in {"needs_retry", "quarantined"},
                "retry_triggers": retry_triggers,
                "retry_stage_scope": retry_stage_scope,
                "validation_counts": validation_counts,
                "rejected_reasons": rejected_reasons,
                "stage_status": {
                    "ai_generation": {"status": "completed"},
                    "cultural_events": {
                        "status": "skipped" if skip_events else "completed",
                        "event_count": event_count,
                    },
                    "images": {
                        "status": "skipped" if skip_images else ("completed" if image_count > 0 else "shortfall"),
                        "image_count": image_count,
                    },
                    "url_discovery": {
                        "status": "skipped" if skip_url_discovery else "completed",
                        "accepted_count": validation_counts["accepted"],
                        "rejected_count": validation_counts["rejected"],
                        "excluded_count": validation_counts["excluded"],
                        "needs_retry_count": validation_counts["needs_retry"],
                        "quarantined_count": validation_counts["quarantined"],
                        "acceptance_ratio": round(url_acceptance_ratio, 4),
                        "acceptance_ratio_threshold": min_url_acceptance_ratio,
                        "rendered_no_url_attractions": rendered_no_url_attractions,
                        "rendered_no_url_restaurants": rendered_no_url_restaurants,
                        "rendered_no_url_stops": rendered_no_url_stops,
                        "source_counts": url_source_counts,
                        "reason_counts": url_reason_counts,
                        "disposition_thread_count": url_thread_count,
                        "disposition_event_count": url_event_count,
                        "en_route_reliability": en_route_reliability,
                    },
                    "reconciliation": {
                        "status": "completed",
                        "accepted_count": len(reconciliation_report.get("accepted", []) or [])
                        if isinstance(reconciliation_report.get("accepted", []), list)
                        else 0,
                        "rejected_count": len(rejected),
                        "reassigned_count": len(reconciliation_report.get("reassigned", []) or [])
                        if isinstance(reconciliation_report.get("reassigned", []), list)
                        else 0,
                        "quarantined_count": len(quarantined_entity_ids),
                    },
                },
                "section_counts": section_counts,
                "url_discovery_disposition_threads": (
                    url_discovery_meta.get("disposition_threads", {})
                    if isinstance(url_discovery_meta.get("disposition_threads", {}), dict)
                    else {}
                ),
            }
        )

    return {
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "destination_count": len(destination_statuses),
            "status_counts": status_counts,
            "retry_recommended_count": sum(1 for item in destination_statuses if item.get("retry_recommended")),
        },
        "destinations": destination_statuses,
    }


def _write_destination_status_report(output_dir: Path, status_report: dict[str, Any]) -> Path:
    path = output_dir / "destination_status_report.json"
    path.write_text(json.dumps(status_report, indent=2), encoding="utf-8")
    return path


def _write_destination_status_markdown_report(output_dir: Path, status_report: dict[str, Any]) -> Path:
    destinations = status_report.get("destinations", []) if isinstance(status_report.get("destinations", []), list) else []
    summary = status_report.get("summary", {}) if isinstance(status_report.get("summary", {}), dict) else {}
    retry_outcomes = summary.get("retry_outcomes", {}) if isinstance(summary.get("retry_outcomes", {}), dict) else {}

    lines: list[str] = []
    lines.append("# Destination Status Summary")
    lines.append("")
    lines.append(f"- Run ID: {status_report.get('run_id', '')}")
    lines.append(f"- Generated at (UTC): {status_report.get('generated_at_utc', '')}")
    lines.append(f"- Destination count: {summary.get('destination_count', 0)}")
    lines.append(f"- Retry recommended: {summary.get('retry_recommended_count', 0)}")
    if retry_outcomes:
        lines.append(f"- Retry attempted: {retry_outcomes.get('attempted_destination_count', 0)}")
        lines.append(f"- Resolved after retry: {retry_outcomes.get('resolved_after_retry_count', 0)}")
        lines.append(f"- Unresolved after retry: {retry_outcomes.get('unresolved_after_retry_count', 0)}")
        lines.append(f"- Not retried due to cap: {retry_outcomes.get('not_retried_due_to_cap_count', 0)}")
    lines.append("")

    attention_rows = []
    for row in destinations:
        if not isinstance(row, dict):
            continue
        outcome = row.get("retry_outcome", {}) if isinstance(row.get("retry_outcome", {}), dict) else {}
        terminal_state = str(outcome.get("terminal_state", "") or "")
        if terminal_state in {"retry_cap_reached_unresolved", "not_retried_due_to_cap"}:
            attention_rows.append(row)

    lines.append(f"## Needs Attention ({len(attention_rows)})")
    if not attention_rows:
        lines.append("- None")
    else:
        for row in attention_rows:
            destination_name = str(row.get("destination_name", "") or row.get("destination_id", ""))
            destination_id = str(row.get("destination_id", "") or "")
            status = str(row.get("status", "") or "")
            outcome = row.get("retry_outcome", {}) if isinstance(row.get("retry_outcome", {}), dict) else {}
            terminal_state = str(outcome.get("terminal_state", "") or "")
            retry_triggers = row.get("retry_triggers", []) if isinstance(row.get("retry_triggers", []), list) else []
            trigger_text = ", ".join(str(t) for t in retry_triggers) if retry_triggers else "none"
            lines.append(f"- {destination_name} ({destination_id}) — status={status}, terminal={terminal_state}, triggers={trigger_text}")
    lines.append("")

    lines.append(f"## All Destinations ({len(destinations)})")
    if not destinations:
        lines.append("- None")
    else:
        for row in destinations:
            if not isinstance(row, dict):
                continue
            destination_name = str(row.get("destination_name", "") or row.get("destination_id", ""))
            destination_id = str(row.get("destination_id", "") or "")
            status = str(row.get("status", "") or "")
            outcome = row.get("retry_outcome", {}) if isinstance(row.get("retry_outcome", {}), dict) else {}
            terminal_state = str(outcome.get("terminal_state", "pending_retry_pass") or "pending_retry_pass")
            url_stage = row.get("stage_status", {}).get("url_discovery", {}) if isinstance(row.get("stage_status", {}), dict) else {}
            en_route_reliability = (
                url_stage.get("en_route_reliability", {})
                if isinstance(url_stage.get("en_route_reliability", {}), dict)
                else {}
            )
            if en_route_reliability and int(en_route_reliability.get("total_en_route_stops", 0) or 0) > 0:
                lines.append(
                    f"- {destination_name} ({destination_id}) — status={status}, terminal={terminal_state}, "
                    f"en_route_resolved={en_route_reliability.get('resolved_with_url', 0)}/{en_route_reliability.get('total_en_route_stops', 0)}, "
                    f"en_route_exhaustion_or_no_match={en_route_reliability.get('exhaustion_or_no_match', 0)}"
                )
            else:
                lines.append(f"- {destination_name} ({destination_id}) — status={status}, terminal={terminal_state}")

    path = output_dir / "destination_status_report.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _retry_stage_scope_by_destination(status_report: dict[str, Any]) -> dict[str, dict[str, bool]]:
    destinations = status_report.get("destinations", []) if isinstance(status_report.get("destinations", []), list) else []
    scope_by_id: dict[str, dict[str, bool]] = {}
    for row in destinations:
        if not isinstance(row, dict):
            continue
        destination_id = str(row.get("destination_id", "") or "").strip()
        if not destination_id:
            continue
        scope = row.get("retry_stage_scope", {}) if isinstance(row.get("retry_stage_scope", {}), dict) else {}
        # Default True (retry every stage) when scope is absent -- e.g. a
        # status_report built before this field existed -- so missing scope
        # data never silently narrows a retry.
        scope_by_id[destination_id] = {
            "events": bool(scope.get("events", True)),
            "images": bool(scope.get("images", True)),
            "urls": bool(scope.get("urls", True)),
        }
    return scope_by_id


def _resolve_llm_overrides(
    trip: dict[str, Any],
    *,
    cli_provider: str | None,
    cli_model: str | None,
) -> dict[str, Any]:
    """Resolve the effective LLM provider/model/features overrides passed to
    MultiLLMClient, in precedence order (lowest to highest):
      1. nested trip.trip.llm.{provider,model,features,...} (base dict)
      2. flat trip.trip.llm_provider / trip.trip.llm_features / trip.trip.llm_model
      3. --llm-provider / --llm-model CLI flags

    trip.llm_model (flat) previously had no handling at all -- only the nested
    trip.llm.model form or the CLI flag worked, so a manifest using the flat
    form (this project's own sw_manifest.yaml does) had its model override
    silently dropped in favor of config.yaml's default, with no warning.
    Added here symmetric with the pre-existing trip.llm_provider handling.
    """
    trip_meta = trip.get("trip", {}) if isinstance(trip.get("trip", {}), dict) else {}
    overrides = dict(trip_meta.get("llm", {}) or {})

    if trip_meta.get("llm_provider"):
        overrides["provider"] = trip_meta.get("llm_provider")
    if trip_meta.get("llm_features"):
        overrides["features"] = trip_meta.get("llm_features")
    if trip_meta.get("llm_model"):
        overrides["model"] = trip_meta.get("llm_model")

    provider_selected = cli_provider or trip_meta.get("llm_provider")
    if cli_provider:
        overrides["provider"] = cli_provider.lower()
    elif provider_selected:
        overrides["provider"] = str(provider_selected).lower()

    if cli_model:
        overrides["model"] = cli_model

    return overrides


def _destination_ids_for_selective_retry(status_report: dict[str, Any]) -> list[str]:
    destinations = status_report.get("destinations", []) if isinstance(status_report.get("destinations", []), list) else []
    retry_ids: list[str] = []
    for row in destinations:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status", "") or "").strip().lower()
        destination_id = str(row.get("destination_id", "") or "").strip()
        retry_recommended = bool(row.get("retry_recommended", False))
        if destination_id and (retry_recommended or status in {"needs_retry", "quarantined"}):
            retry_ids.append(destination_id)
    # Preserve order while de-duplicating.
    return list(dict.fromkeys(retry_ids))


def _selective_retry_destinations(
    *,
    trip: dict[str, Any],
    status_report: dict[str, Any],
    config_path: str,
    llm_client: Any,
    output_dir: Path,
    refresh_image_cache: bool,
    skip_events: bool,
    skip_images: bool,
    skip_url_discovery: bool,
    no_trails: bool,
    alltrails_source: str | None,
    attraction_source: str | None,
    restaurant_source: str | None,
    en_route_source: str | None,
    search_provider_override: str | None = None,
    is_search_circuit_open: Any | None = None,
    run_events: Any | None = None,
    run_images: Any | None = None,
    run_urls: Any | None = None,
) -> list[str]:
    retry_ids = _destination_ids_for_selective_retry(status_report)
    if not retry_ids:
        return []

    retry_set = set(retry_ids)
    retry_destinations = [
        dest
        for dest in (trip.get("destinations", []) or [])
        if isinstance(dest, dict) and str(dest.get("id", "") or "") in retry_set
    ]
    if not retry_destinations:
        return []

    stage_scope = _retry_stage_scope_by_destination(status_report)
    default_scope = {"events": True, "images": True, "urls": True}

    def _subset_trip_for_stage(stage: str) -> dict[str, Any] | None:
        stage_destinations = [
            dest
            for dest in retry_destinations
            if stage_scope.get(str(dest.get("id", "") or ""), default_scope).get(stage, True)
        ]
        if not stage_destinations:
            return None
        return {"trip": trip.get("trip", {}), "destinations": stage_destinations}

    events_trip = _subset_trip_for_stage("events")
    images_trip = _subset_trip_for_stage("images")
    urls_trip = _subset_trip_for_stage("urls")

    if not skip_events and events_trip is not None:
        if run_events is None:
            from generator.cultural_events import CulturalEventsDiscoverer

            run_events = lambda subset_trip: CulturalEventsDiscoverer(
                config_path, llm_client=llm_client, search_provider_override=search_provider_override
            ).discover(subset_trip)
        run_events(events_trip)

    if not skip_images and images_trip is not None:
        if run_images is None:
            from generator.image_fetcher import ImageFetcher

            run_images = lambda subset_trip: ImageFetcher(
                config_path,
                output_dir=output_dir / "images",
                force_refresh=refresh_image_cache,
            ).fetch_all(subset_trip)
        run_images(images_trip)

    if not skip_url_discovery and urls_trip is not None and is_search_circuit_open is not None and is_search_circuit_open():
        # Regression for a real, observed run (2026-08-15, all-Grok
        # --search-provider comparison): the caller's own pre-retry gate
        # checks the breaker exactly once, before this whole function is
        # entered. Events and images retry (above) can each take real
        # wall-clock time, and the breaker can reopen in that gap -- firing
        # url retry into a since-reopened breaker anyway burned 231s across
        # 8 destinations for a 0% improvement in that run. Re-checking here,
        # immediately before the expensive part fires, catches exactly that
        # gap; it cannot catch the breaker tripping *during* url retry
        # itself (each destination's own network calls already fail fast
        # via their own per-call breaker check once that happens -- this
        # only avoids paying for a retry we already know is doomed before
        # we even start it).
        click.echo(
            "  ⚠ Selective URL retry SKIPPED — search circuit breaker reopened since the "
            "outer retry gate check (events/images retry ran first); retrying now would "
            "repeat the same failures."
        )
    elif not skip_url_discovery and urls_trip is not None:
        if run_urls is None:
            from generator.url_discovery import URLDiscoverer

            def _default_run_urls(subset_trip: dict[str, Any]) -> None:
                url_discoverer = URLDiscoverer(
                    config_path,
                    llm_client=llm_client,
                    disable_trails=bool(no_trails),
                    alltrails_source=alltrails_source,
                    attraction_source=attraction_source,
                    restaurant_source=restaurant_source,
                    en_route_source=en_route_source,
                    output_dir=output_dir,
                    search_provider_override=search_provider_override,
                )
                url_discoverer.discover_all(subset_trip)
                url_discoverer.audit_discovered_urls(subset_trip)

            run_urls = _default_run_urls
        run_urls(urls_trip)

    return retry_ids


def _extract_http_urls_from_html_text(html_text: str) -> set[str]:
    import re

    urls: set[str] = set()
    for match in re.finditer(r'href\s*=\s*["\']([^"\']+)["\']', html_text, flags=re.IGNORECASE):
        candidate = str(match.group(1) or "").strip()
        if candidate.lower().startswith(("http://", "https://")):
            urls.add(candidate)
    return urls


def _read_output_urls(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        return _extract_http_urls_from_html_text(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return set()


def _latest_direct_batch_parity_summary(*, output_dir: Path) -> dict[str, Any]:
    capture_dir = output_dir / "dev" / "url_discovery_direct_batch_html"
    html_files = sorted(capture_dir.glob("*.html")) if capture_dir.exists() else []

    captured_urls: set[str] = set()
    destination_urls: dict[str, set[str]] = {}
    for html_path in html_files:
        urls = _extract_http_urls_from_html_text(html_path.read_text(encoding="utf-8", errors="ignore"))
        captured_urls |= urls

        meta_path = html_path.with_name(f"{html_path.stem}.meta.json")
        destination_name = "unknown"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8", errors="ignore"))
                destination_name = str(meta.get("destination") or "unknown").strip() or "unknown"
            except Exception:
                destination_name = "unknown"
        destination_urls.setdefault(destination_name, set()).update(urls)

    final_output_path = output_dir / "index.html"
    final_urls = _read_output_urls(final_output_path)

    destinations_missing_names = sorted(
        name for name, urls in destination_urls.items() if urls and not urls.issubset(final_urls)
    )

    return {
        "captured_html_input_count": len(html_files),
        "unique_captured_urls": len(captured_urls),
        "unique_final_html_urls": len(final_urls),
        "destinations_missing_at_least_one_captured_url": len(destinations_missing_names),
        "destinations_missing_captured_url_names": destinations_missing_names,
    }


def _write_direct_batch_parity_report(
    *,
    output_dir: Path,
    parity_summary: dict[str, Any],
    run_id: str,
) -> Path:
    report = {
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        **parity_summary,
    }
    path = output_dir / "direct_batch_parity_report.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def _domain_counts(urls: set[str]) -> dict[str, int]:
    from urllib.parse import urlparse

    counts: dict[str, int] = {}
    for url in urls:
        domain = (urlparse(url).netloc or "").lower()
        if not domain:
            continue
        counts[domain] = counts.get(domain, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _write_url_diff_report(
    *,
    output_dir: Path,
    baseline_urls: set[str],
    current_urls: set[str],
    run_id: str,
) -> Path:
    kept = sorted(baseline_urls & current_urls)
    added = sorted(current_urls - baseline_urls)
    removed = sorted(baseline_urls - current_urls)
    report = {
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "baseline_url_count": len(baseline_urls),
            "current_url_count": len(current_urls),
            "kept_count": len(kept),
            "added_count": len(added),
            "removed_count": len(removed),
            "changed_total": len(added) + len(removed),
        },
        "baseline_domains": _domain_counts(baseline_urls),
        "current_domains": _domain_counts(current_urls),
        "kept": kept,
        "added": added,
        "removed": removed,
    }
    path = output_dir / "url_diff_report.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def _write_url_diff_markdown_report(
    *,
    output_dir: Path,
    baseline_urls: set[str],
    current_urls: set[str],
    run_id: str,
) -> Path:
    kept = sorted(baseline_urls & current_urls)
    added = sorted(current_urls - baseline_urls)
    removed = sorted(baseline_urls - current_urls)

    lines: list[str] = []
    lines.append("# URL Diff Report")
    lines.append("")
    lines.append(f"- Run ID: {run_id}")
    lines.append(f"- Generated at (UTC): {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"- Baseline URLs: {len(baseline_urls)}")
    lines.append(f"- Current URLs: {len(current_urls)}")
    lines.append(f"- Kept: {len(kept)}")
    lines.append(f"- Added: {len(added)}")
    lines.append(f"- Removed: {len(removed)}")
    lines.append("")

    def _append_section(title: str, values: list[str]) -> None:
        lines.append(f"## {title} ({len(values)})")
        if not values:
            lines.append("- None")
            lines.append("")
            return
        for v in values:
            lines.append(f"- {v}")
        lines.append("")

    _append_section("Added URLs", added)
    _append_section("Removed URLs", removed)
    _append_section("Kept URLs", kept)

    path = output_dir / "url_diff_report.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _cultural_events_enabled(config_path: str) -> bool:
    """True only when config explicitly enables cultural events.

    Defaults to False -- absent, malformed or unreadable config all mean off.
    That direction is deliberate: this is the worst value-per-token category
    in the pipeline, so an unreadable config should not silently buy it.
    """
    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        return bool((cfg.get("cultural_events") or {}).get("enabled", False))
    except Exception:
        return False


def _append_run_ledger(ledger_path: Path, record: dict) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _record_observed_models(llm_client, llm_effective: dict) -> dict:
    """Return the usage summary, recording the provider-reported model names.

    Pricing is keyed on the name the provider reports back, not the name we
    configured, so this is the field that explains a cost estimate. Returns
    the summary unchanged so it can be used inline where usage_summary() was.
    """
    usage = llm_client.usage_summary()
    llm_effective["observed_models"] = sorted({
        f"{m.get('provider')}:{m.get('model')}"
        for m in (usage.get("models") or [])
        if m.get("model")
    })
    return usage


def _filter_destinations(
    trip: dict,
    destination_ids: tuple[str, ...],
    *,
    first_destination_only: bool,
) -> None:
    destinations = list(trip.get("destinations", []))
    if destination_ids:
        destinations = [d for d in destinations if d["id"] in destination_ids]
        if not destinations:
            raise click.ClickException(f"None of {destination_ids} matched any destination id.")
    if first_destination_only and destinations:
        destinations = destinations[:1]
    trip["destinations"] = destinations


def _is_us_coordinates(lat: object, lng: object) -> bool:
    """Return True when coordinates are in US regions where NPS codes are relevant."""
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError):
        return False

    # Continental US
    if 24.0 <= lat_f <= 49.5 and -125.0 <= lng_f <= -66.5:
        return True
    # Alaska
    if 51.0 <= lat_f <= 72.0 and -170.0 <= lng_f <= -129.0:
        return True
    # Hawaii
    if 18.0 <= lat_f <= 23.0 and -161.0 <= lng_f <= -154.0:
        return True
    return False


def _write_pwa_assets(output_dir: Path, trip: dict) -> None:
        """Write manifest.webmanifest and sw.js next to generated index.html."""
        trip_meta = trip.get("trip", {}) if isinstance(trip, dict) else {}
        title = str(trip_meta.get("title", "Road Trip Itinerary") or "Road Trip Itinerary").strip()
        subtitle = str(trip_meta.get("subtitle", "Interactive road trip itinerary") or "Interactive road trip itinerary").strip()
        theme_color = str(trip_meta.get("theme_color", "#C0623E") or "#C0623E").strip()

        icon_192 = (
                "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 192 192'%3E"
                "%3Crect width='192' height='192' rx='36' fill='%23C0623E'/%3E"
                "%3Ctext x='50%25' y='54%25' font-size='110' text-anchor='middle' dominant-baseline='middle'%3E"
                "%F0%9F%97%BA%EF%B8%8F%3C/text%3E%3C/svg%3E"
        )
        icon_512 = (
                "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'%3E"
                "%3Crect width='512' height='512' rx='96' fill='%23C0623E'/%3E"
                "%3Ctext x='50%25' y='54%25' font-size='300' text-anchor='middle' dominant-baseline='middle'%3E"
                "%F0%9F%97%BA%EF%B8%8F%3C/text%3E%3C/svg%3E"
        )

        manifest = {
                "name": title,
                "short_name": title[:24] or "Road Trip",
                "description": subtitle,
                "start_url": "./index.html",
                "scope": "./",
                "display": "standalone",
                "background_color": "#FAFAF7",
                "theme_color": theme_color,
                "orientation": "portrait-primary",
                "icons": [
                        {
                                "src": icon_192,
                                "sizes": "192x192",
                                "type": "image/svg+xml",
                                "purpose": "any maskable",
                        },
                        {
                                "src": icon_512,
                                "sizes": "512x512",
                                "type": "image/svg+xml",
                                "purpose": "any maskable",
                        },
                ],
        }
        (output_dir / "manifest.webmanifest").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
        )

        sw_js = """const CACHE = 'roadtrip-shell-v1';
const SHELL = ['./', './index.html', './manifest.webmanifest'];

self.addEventListener('install', (event) => {
    event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
        )
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    if (event.request.method !== 'GET') {
        return;
    }

    const reqUrl = new URL(event.request.url);
    const sameOrigin = reqUrl.origin === self.location.origin;
    const cacheableCdn =
        reqUrl.href.startsWith('https://cdn.tailwindcss.com') ||
        reqUrl.href.startsWith('https://unpkg.com/lucide@latest') ||
        reqUrl.href.startsWith('https://cdn.jsdelivr.net/npm/leaflet@1.9.4/');

    if (!sameOrigin && !cacheableCdn) {
        return;
    }

    event.respondWith(
        caches.match(event.request).then((cached) => {
            if (cached) {
                return cached;
            }
            return fetch(event.request)
                .then((response) => {
                    if (response && response.ok) {
                        const clone = response.clone();
                        caches.open(CACHE).then((cache) => cache.put(event.request, clone));
                    }
                    return response;
                })
                .catch(() => caches.match('./index.html'));
        })
    );
});
"""
        (output_dir / "sw.js").write_text(sw_js, encoding="utf-8")


def _setup_logging(verbose: bool, log_level: str) -> str:
    selected = "debug" if verbose else (log_level or "info").lower()
    level = getattr(logging, selected.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    return selected.upper()


def _run_git_command(repo_root: Path, args: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
        return (completed.stdout or "").strip()
    except Exception:
        return ""


def _build_development_build_info(*, repo_root: Path, run_id: str, build_tag: str | None = None) -> dict[str, Any]:
    commit = _run_git_command(repo_root, ["rev-parse", "HEAD"])
    short_commit = _run_git_command(repo_root, ["rev-parse", "--short", "HEAD"])
    branch = _run_git_command(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    status = _run_git_command(repo_root, ["status", "--porcelain"])
    dirty = bool(status)

    fingerprint_parts = [
        f"v{__version__}",
        short_commit or "nogit",
        "dirty" if dirty else "clean",
        run_id,
    ]
    if build_tag:
        fingerprint_parts.append(str(build_tag).strip())

    return {
        "fingerprint": "+".join(part for part in fingerprint_parts if part),
        "run_id": run_id,
        "generator_version": __version__,
        "template_version": __template_version__,
        "build_tag": str(build_tag or "").strip(),
        "git": {
            "branch": branch,
            "commit": commit,
            "short_commit": short_commit,
            "dirty": dirty,
        },
    }


def _write_development_build_info(output_dir: Path, build_info: dict[str, Any]) -> tuple[Path, Path]:
    # Previously nested these under output_dir / "dev" -- a leftover generic
    # "development artifacts" label unrelated to the --environment flag, but
    # colliding with it: an `--environment dev` run already nests output_dir
    # under its own "dev" segment (see the CLI's output_dir construction),
    # so build_info ended up at a confusing, doubled ".../dev/dev/..." path,
    # and the same unrelated "dev" segment appeared even under --environment
    # prod/eval (".../prod/dev/build_info.json"). Every other output file
    # (index.html, validation_report.json, ...) already lives directly in
    # output_dir with no such nesting -- match that.
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(build_info.get("run_id", "") or "")

    per_run_path = output_dir / f"build_info.{run_id or 'unknown'}.json"
    latest_path = output_dir / "build_info.latest.json"

    payload = json.dumps(build_info, indent=2, ensure_ascii=False)
    per_run_path.write_text(payload, encoding="utf-8")
    latest_path.write_text(payload, encoding="utf-8")
    return per_run_path, latest_path


@click.command()
@click.option("--manifest", required=True, type=click.Path(exists=True), help="Trip manifest YAML")
@click.option("--output", default="output", show_default=True, help="Output directory")
@click.option("--config", "config_path", default="config.yaml", show_default=True, help="Config YAML")
@click.option(
    "--llm-provider",
    type=click.Choice(["openai", "anthropic", "deepseek", "gemini", "grok", "azure_openai"], case_sensitive=False),
    help="Override LLM provider for this run",
)
@click.option(
    "--environment",
    type=click.Choice(["dev", "eval", "prod"], case_sensitive=False),
    help="Environment override (dev/eval/prod). Optional.",
)
@click.option(
    "--env-file",
    type=click.Path(exists=True),
    help="Optional path to .env file. If provided, loaded before environment resolution.",
)
@click.option(
    "--privacy-mode",
    type=click.Choice(["auto", "on", "off"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Redact personal trip details (planning_links, lodging property names -- NOT "
         "lodging location/check-in time, which drive routing and schedule content) from "
         "rendered output. 'auto' redacts only in --environment prod; 'on'/'off' force the "
         "behavior regardless of environment.",
)
@click.option("--llm-model", type=str, help="Override LLM model for this run")
@click.option("--dry-run", is_flag=True, help="Parse & validate only; no AI calls")
@click.option("--skip-images", is_flag=True, help="Skip image fetching")
@click.option("--refresh-image-cache", is_flag=True, help="Force refresh image-provider queries, bypassing local image cache")
@click.option("--skip-events", is_flag=True, help="Skip cultural events discovery")
@click.option("--skip-url-discovery", is_flag=True, help="Skip URL discovery")
@click.option("--notrails", "no_trails", is_flag=True, help="Disable trail link discovery and omit trail links")
@click.option(
    "--alltrails-source",
    type=click.Choice(["direct-link-batch", "search"], case_sensitive=False),
    default=None,
    help="AllTrails source for trail-like attractions",
)
@click.option(
    "--attraction-source",
    type=click.Choice(["search", "direct-link-batch"], case_sensitive=False),
    default=None,
    help="Source for non-trail attractions",
)
@click.option(
    "--restaurant-source",
    type=click.Choice(["search", "direct-link-batch"], case_sensitive=False),
    default=None,
    help="Source for restaurant links",
)
@click.option(
    "--en-route-source",
    type=click.Choice(["search", "direct-link-batch"], case_sensitive=False),
    default=None,
    help="Source for en-route stop links",
)
@click.option(
    "--search-provider",
    type=click.Choice(["grok", "claude", "openai"], case_sensitive=False),
    default=None,
    help="Force a single search/harvest provider for this run (url_discovery batch + non-batch, cultural_events), disabling cross-provider fallback entirely -- for a clean, uncontaminated per-provider cost/behavior comparison run.",
)
@click.option("--noschedule", is_flag=True, help="Suppress schedule card rendering in output HTML")
@click.option("--noseed", is_flag=True, help="Ignore destination seeds from the manifest for this run")
@click.option("--destination", "destinations", multiple=True, help="Limit to specific destination ids")
@click.option("--first-destination", "first_destination_only", is_flag=True, help="Process only the first destination after any destination filtering")
@click.option("--build-tag", type=str, help="Optional development build tag recorded in output metadata and dev build-info artifacts")
@click.option(
    "--log-level",
    type=click.Choice(LOG_LEVEL_CHOICES, case_sensitive=False),
    default="info",
    show_default=True,
    help="Console logging threshold. Ignored when --verbose is set.",
)
@click.option("--verbose", is_flag=True, help="Enable debug logging")
def main(
    manifest: str,
    output: str,
    config_path: str,
    llm_provider: str | None,
    environment: str | None,
    env_file: str | None,
    privacy_mode: str,
    llm_model: str | None,
    dry_run: bool,
    skip_images: bool,
    refresh_image_cache: bool,
    skip_events: bool,
    skip_url_discovery: bool,
    no_trails: bool,
    alltrails_source: str | None,
    attraction_source: str | None,
    restaurant_source: str | None,
    en_route_source: str | None,
    search_provider: str | None,
    noschedule: bool,
    noseed: bool,
    destinations: tuple[str, ...],
    first_destination_only: bool,
    build_tag: str | None,
    log_level: str,
    verbose: bool,
) -> None:
    # Windows falls back to the cp1252 codepage (can't encode emoji like the
    # banner's map icon below) whenever stdout/stderr are redirected to a file
    # or pipe instead of a real console -- crashing with UnicodeEncodeError
    # before a single API call is made. Force UTF-8 explicitly so redirected/
    # non-interactive runs (CI, background/batch invocations) behave the same
    # as an interactive terminal.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    run_started_at = datetime.now(timezone.utc)
    run_started_at_perf = perf_counter()
    run_id = run_started_at.strftime("%Y%m%dT%H%M%S.%fZ")
    # Environment isn't resolved until manifest parsing succeeds (it can come
    # from the manifest itself, see "Hybrid environment selection" below), so
    # default to "dev" here purely so a failure before that point still
    # lands its ledger entry somewhere sane. Both names are corrected in
    # place once the real environment is known -- _finalize_run reads them
    # from this enclosing scope at call time, so the later reassignment is
    # visible to it without extra plumbing.
    environment_selected = "dev"
    ledger_path = Path(output) / environment_selected / "run_ledger.jsonl"
    # Resolved once the real environment is known (see _resolve_privacy_redaction
    # below); a safe pre-resolution default of "redact" avoids any window where an
    # early-failure ledger record could misreport this as false.
    redact_privacy_details = True
    finalized = False
    stage_timings: dict[str, float] = {}
    runtime_metrics: dict[str, Any] = {}
    # What the run ACTUALLY used, as opposed to the llm_provider/llm_model
    # fields in the ledger record, which are the CLI overrides and are
    # therefore null on every run that does not pass --llm-model. That gap
    # cost real time on 2026-08-21: reconciling the ledger against xAI's
    # billing needed to know which model each historical run had used, and
    # the ledger had never recorded it.
    #
    # `configured_model` is what we asked for; `observed_models` is what the
    # provider says it served. These differ whenever the configured name is
    # an ALIAS -- asking for "grok-latest" was answered as "grok-4-fast" --
    # and that difference is exactly what makes cost attribution wrong,
    # because pricing is looked up on the observed name.
    llm_effective: dict[str, Any] = {}
    image_counter_delta: dict[str, int] = {}
    url_validator_counter_delta: dict[str, int] = {}
    repo_root = Path(__file__).resolve().parent.parent
    development_build = _build_development_build_info(
        repo_root=repo_root,
        run_id=run_id,
        build_tag=build_tag,
    )

    def _finalize_run(status: str, exit_code: int, error: str | None = None) -> None:
        nonlocal finalized
        if finalized:
            return
        ended_at = datetime.now(timezone.utc)
        duration_s = max(0.0, (ended_at - run_started_at).total_seconds())
        record = {
            "run_id": run_id,
            "status": status,
            "exit_code": int(exit_code),
            "error": str(error or "").strip() or None,
            "started_at_utc": run_started_at.isoformat(),
            "ended_at_utc": ended_at.isoformat(),
            "duration_seconds": round(duration_s, 3),
            "manifest": manifest,
            "output": output,
            "config": config_path,
            "environment": environment_selected,
            "environment_cli_override": environment,
            "env_file": env_file,
            "privacy_mode": privacy_mode,
            "privacy_redacted": bool(redact_privacy_details),
            # CLI overrides only -- null unless --llm-provider/--llm-model was
            # passed. See llm_effective for what the run actually used.
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "llm_effective": llm_effective,
            "search_provider_override": search_provider,
            "build_tag": build_tag,
            "dry_run": bool(dry_run),
            "skip_images": bool(skip_images),
            "skip_events": bool(skip_events),
            "skip_url_discovery": bool(skip_url_discovery),
            "no_trails": bool(no_trails),
            "alltrails_source": str(alltrails_source or ""),
            "attraction_source": str(attraction_source or ""),
            "restaurant_source": str(restaurant_source or ""),
            "en_route_source": str(en_route_source or ""),
            "noseed": bool(noseed),
            "first_destination_only": bool(first_destination_only),
            "destinations": list(destinations),
            "development_build": development_build,
            "stage_timings_seconds": stage_timings,
            "runtime_metrics": runtime_metrics,
        }
        try:
            _append_run_ledger(ledger_path, record)
        except Exception as exc:  # pragma: no cover - defensive only
            logger.warning("Failed to append run ledger entry: %s", exc)
        finalized = True

    def _finalize_if_unfinished() -> None:
        if not finalized:
            _finalize_run("terminated_without_finalize", 1, "Process exited before normal completion")

    atexit.register(_finalize_if_unfinished)

    effective_log_level = _setup_logging(verbose, log_level)
    output_dir = Path(output)
    retry_policy = _load_destination_retry_policy(config_path)

    # Cultural events are OFF unless config turns them on. Measured 2026-08-22
    # across three cold-start runs: 16 calls and 97K-113K tokens to deliver
    # between 1 and 4 event listings -- 24,000 to 113,000 tokens per delivered
    # item, and more tokens than generating the entire itinerary for all ten
    # destinations (10 calls, 68K). It is also the content most likely to be
    # stale by departure, since a trip is typically built months ahead.
    #
    # --skip-events still forces off; this only changes what happens when the
    # flag is absent. Set cultural_events.enabled: true to restore.
    if not skip_events and not _cultural_events_enabled(config_path):
        skip_events = True
        logger.info("Cultural events disabled by config (cultural_events.enabled is not true)")

    click.echo(f"🗺  Itinerary Generator")
    click.echo(f"   Manifest : {manifest}")
    click.echo(f"   Output   : {output_dir.resolve()}")
    click.echo(f"   Config   : {config_path}")
    click.echo(f"   Logging  : {effective_log_level}")
    click.echo(f"   Build    : {development_build.get('fingerprint', '')}")

    if llm_provider:
        click.echo(f"   LLM      : provider override = {llm_provider.lower()}")
    if llm_model:
        click.echo(f"   LLM      : model override = {llm_model}")
    if search_provider:
        click.echo(
            f"   Search   : provider override = {search_provider.lower()} "
            "(single provider, no cross-provider fallback)"
        )
    if dry_run:
        click.echo("   Mode     : DRY RUN (no AI calls)")
    click.echo()

    # ── Optional .env loading ───────────────────────────────────────────────
    if env_file:
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
            click.echo(f"   EnvFile  : loaded from {env_file}")
        except Exception as exc:
            click.echo(f"   EnvFile  : failed to load ({exc})", err=True)

    # ── Stage 1: Parse & validate manifest ──────────────────────────────────
    stage_1_started = perf_counter()
    click.echo("Stage 1/6 — Parsing manifest…")
    from generator.parser import ManifestParser
    parser = ManifestParser()
    try:
        trip = parser.load(manifest)
    except ValueError as exc:
        click.echo(f"  ERROR: {exc}", err=True)
        _finalize_run("input_error", 1, str(exc))
        sys.exit(1)
    stripped_seed_count = _strip_destination_seeds(trip) if noseed else 0
    if noseed:
        click.echo(f"   Seeds    : disabled for this run ({stripped_seed_count} seed(s) ignored)")
    runtime_metrics["manifest_seed_count_ignored"] = stripped_seed_count

    # ── Hybrid environment selection ─────────────────────────────────────────
    env_from_manifest = trip.get("trip", {}).get("environment")
    env_from_cli = environment
    env_from_env = os.environ.get("ENVIRONMENT")

    environment_selected = (
        (env_from_cli or env_from_manifest or env_from_env or "dev").lower()
    )
    # Correct the pre-resolution "dev" placeholder now that the real
    # environment is known, so the run ledger for this (and every later)
    # environment lands in its own file instead of always under dev/.
    ledger_path = Path(output) / environment_selected / "run_ledger.jsonl"

    click.echo(
        click.style("   Env      : ", fg="cyan") +
        click.style(environment_selected, fg="green")
    )

    redact_privacy_details = _resolve_privacy_redaction(privacy_mode, environment_selected)
    if redact_privacy_details:
        redaction_counts = _apply_privacy_redaction(trip)
        click.echo(
            click.style("   Privacy  : ", fg="cyan") +
            click.style(
                f"redacted ({redaction_counts['planning_links']} planning_link(s), "
                f"{redaction_counts['lodging_names']} lodging name(s), "
                f"{redaction_counts['lodging_websites']} lodging website(s), "
                f"{redaction_counts['lodging_confirmations']} confirmation number(s), "
                f"{redaction_counts['transportation']} transportation leg(s))",
                fg="yellow",
            )
        )
    else:
        click.echo(
            click.style("   Privacy  : ", fg="cyan") +
            click.style(
                "off (planning_links, lodging names and websites rendered as-is)",
                fg="green",
            )
        )
    runtime_metrics["privacy_redacted"] = redact_privacy_details

    # Add environment tag to logger name
    logger.name = f"{logger.name}[{environment_selected}]"

    # Output directory behavior:
    # - default: write directly to --output path
    # - only nest by environment when explicitly requested via CLI
    if env_from_cli:
        output_dir = Path(output) / environment_selected
    else:
        output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)
    per_run_build_info_path, latest_build_info_path = _write_development_build_info(output_dir, development_build)
    click.echo(f"  ✓ Build info: {per_run_build_info_path}")
    click.echo(f"  ✓ Build info (latest): {latest_build_info_path}")
    output_file = output_dir / "index.html"
    baseline_output_urls = _read_output_urls(output_file)

    click.echo()

    try:
        _filter_destinations(
            trip,
            destinations,
            first_destination_only=first_destination_only,
        )
    except click.ClickException as exc:
        click.echo(f"  ERROR: {exc}", err=True)
        _finalize_run("input_error", 1, str(exc))
        sys.exit(1)

    click.echo(f"  ✓ {len(trip['destinations'])} destination(s) loaded")

    # Booking counts reach the console rather than only the log, because the
    # runner is expected to run at WARNING (a real run emits ~1700 INFO lines,
    # 95% of them per-item URL-discovery chatter, which buries everything else).
    # "How many bookings landed" is a run-summary fact, not debug detail: with
    # no sidecar the build is still valid and simply renders no booking cards,
    # which is otherwise invisible in the output.
    booking_counts = trip.get("_meta", {}).get("reservations_merged")
    if booking_counts:
        click.echo(
            click.style("   Bookings : ", fg="cyan")
            + click.style(
                f"{booking_counts.get('lodging_fields', 0)} lodging field(s), "
                f"{booking_counts.get('transportation_legs', 0)} destination leg(s), "
                f"{booking_counts.get('trip_legs', 0)} trip-wide leg(s)",
                fg="green",
            )
        )
        pending = booking_counts.get("pending", 0)
        if pending:
            click.echo(
                click.style("   Bookings : ", fg="cyan")
                + click.style(
                    f"{pending} awaiting review -- resolve under 'pending:' in the "
                    "sidecar; they are NOT in this build",
                    fg="yellow",
                )
            )
    stage_timings["stage_1_parse_validate"] = _elapsed_seconds(stage_1_started)
    runtime_metrics["destination_count"] = len(trip.get("destinations", []) or [])

    if dry_run:
        click.echo("\n✅ Dry run complete — manifest valid.")
        stage_timings["total_pipeline"] = _elapsed_seconds(run_started_at_perf)
        _finalize_run("dry_run_completed", 0)
        return

    # ── Stage 2: Geocode + auto-enrich ──────────────────────────────────────
    stage_2_started = perf_counter()
    click.echo("Stage 2/6 — Geocoding & enrichment…")
    from generator.geocoder import Geocoder
    from generator.nps_resolver import NPSResolver
    from concurrent.futures import ThreadPoolExecutor, as_completed
    geo = Geocoder()
    nps = NPSResolver()
    # Geocoding is sequential (Nominatim ToS: 1 req/sec)
    for dest in trip["destinations"]:
        lat, lng = geo._geocode(dest["name"])
        dest["lat"] = lat
        dest["lng"] = lng
        lodging = dest.get("lodging", {}) if isinstance(dest.get("lodging", {}), dict) else {}
        lodging_location = str(lodging.get("location", "") or "").strip()
        if lodging_location:
            try:
                llat, llng = geo._geocode(lodging_location)
                lodging["lat"] = llat
                lodging["lng"] = llng
                dest["lodging"] = lodging
            except Exception as exc:
                logger.warning(
                    "Lodging geocode skipped for %s (%s): %s",
                    dest.get("name", "unknown destination"),
                    lodging_location,
                    exc,
                )

    # Optional departure/return geocoding for full-route maps and first-card routing context.
    departure_name = trip.get("trip", {}).get("departure")
    return_name = trip.get("trip", {}).get("return")
    if departure_name:
        dlat, dlng = geo._geocode(departure_name)
        trip["trip"]["departure_lat"] = dlat
        trip["trip"]["departure_lng"] = dlng
    if return_name:
        rlat, rlng = geo._geocode(return_name)
        trip["trip"]["return_lat"] = rlat
        trip["trip"]["return_lng"] = rlng
    # NPS resolution is independent — run in parallel
    def _resolve_nps(dest: dict) -> None:
        if not _is_us_coordinates(dest.get("lat"), dest.get("lng")):
            dest["nps_park_code"] = None
            return
        dest["nps_park_code"] = nps.resolve(dest["name"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_resolve_nps, d): d for d in trip["destinations"]}
        for f in as_completed(futures):
            f.result()
    for dest in trip["destinations"]:
        click.echo(f"  \u2713 {dest['name']}: lat={dest['lat']:.4f} lng={dest['lng']:.4f} nps={dest['nps_park_code']}")
    stage_timings["stage_2_geocode_enrich"] = _elapsed_seconds(stage_2_started)

    # ── Stage 3: AI content generation ──────────────────────────────────────
    stage_3_started = perf_counter()
    click.echo("Stage 3/6 — AI content generation…")
    from generator.llm_client import MultiLLMClient
    from generator.ai_content import AIContentGenerator
    from generator.costs import print_cost_summary, summarize_from_usage
    normalized_alltrails_source: str | None = None
    if alltrails_source:
        normalized_alltrails_source = str(alltrails_source).strip().lower().replace("-", "_")
        if normalized_alltrails_source not in {"direct_link_batch", "search"}:
            normalized_alltrails_source = None
    normalized_attraction_source: str | None = None
    if attraction_source:
        normalized_attraction_source = str(attraction_source).strip().lower().replace("-", "_")
        if normalized_attraction_source not in {"search", "direct_link_batch"}:
            normalized_attraction_source = None
    normalized_restaurant_source: str | None = None
    if restaurant_source:
        normalized_restaurant_source = str(restaurant_source).strip().lower().replace("-", "_")
        if normalized_restaurant_source not in {"search", "direct_link_batch"}:
            normalized_restaurant_source = None
    normalized_en_route_source: str | None = None
    if en_route_source:
        normalized_en_route_source = str(en_route_source).strip().lower().replace("-", "_")
        if normalized_en_route_source not in {"search", "direct_link_batch"}:
            normalized_en_route_source = None

    # ── Hybrid provider/model selection ─────────────────────────────────────
    llm_overrides = _resolve_llm_overrides(trip, cli_provider=llm_provider, cli_model=llm_model)

    # ── Optional environment-aware config merging ───────────────────────────
    try:
        import yaml
        with Path(config_path).open(encoding="utf-8") as f:
            cfg_full = yaml.safe_load(f) or {}
        if environment_selected in cfg_full:
            env_cfg = cfg_full[environment_selected]
            ai_env_cfg = env_cfg.get("ai", {})
            for key, val in ai_env_cfg.items():
                llm_overrides.setdefault(key, val)
    except Exception:
        pass
    llm_client = MultiLLMClient(
        config_path=config_path,
        llm_overrides=llm_overrides,
    )
    # Printed after construction (not from the raw pre-construction request)
    # so this reflects the actually-effective provider/model -- e.g. after
    # _normalize_model_for_provider has resolved any mismatch/fallback.
    click.echo(
        click.style("   LLM      : provider = ", fg="cyan") +
        click.style(llm_client.provider, fg="green") +
        click.style(", model = ", fg="cyan") +
        click.style(llm_client.model, fg="green")
    )
    # Seeded here rather than at the end so a run that dies mid-pipeline
    # still records what it was configured to use.
    llm_effective["provider"] = llm_client.provider
    llm_effective["configured_model"] = llm_client.model

    ai_gen = AIContentGenerator(config_path, llm_client=llm_client)
    ai_gen.generate_all(trip)

    if noschedule:
        for dest in trip.get("destinations", []):
            if "ai_content" in dest and isinstance(dest["ai_content"], dict):
                dest["ai_content"]["possible_daily_schedule"] = []

    click.echo(f"  ✓ AI content generated for {len(trip['destinations'])} destination(s)")
    stage_timings["stage_3_ai_generation"] = _elapsed_seconds(stage_3_started)

    # ── Stages 4 + 5a + 5b: run concurrently (all independent of each other) ─
    stage_4_5_started = perf_counter()
    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed
    from generator.image_fetcher import ImageFetcher
    from generator.url_validator import URLValidator

    image_counters_before = ImageFetcher.snapshot_counters()
    url_validator_counters_before = URLValidator.snapshot_counters()

    def _run_events() -> None:
        if not skip_events:
            click.echo("Stage 4/6 — Cultural events discovery…")
            from generator.cultural_events import CulturalEventsDiscoverer
            CulturalEventsDiscoverer(
                config_path, llm_client=llm_client, search_provider_override=search_provider
            ).discover(trip)
            click.echo("  ✓ Cultural events resolved")
        else:
            click.echo("Stage 4/6 — Cultural events discovery SKIPPED")

    def _run_images() -> None:
        if not skip_images:
            click.echo("Stage 5/6 — Fetching images…")
            from generator.image_fetcher import ImageFetcher
            ImageFetcher(
                config_path,
                output_dir=output_dir / "images",
                force_refresh=refresh_image_cache,
            ).fetch_all(trip)
            total = sum(len(d.get("images", [])) for d in trip["destinations"])
            click.echo(f"  ✓ {total} images fetched")
        else:
            click.echo("Stage 5/6 — Image fetching SKIPPED")
            for dest in trip["destinations"]:
                dest.setdefault("images", [])

    # Hoisted so the selective-retry pass below can reuse this same instance
    # instead of constructing a fresh one -- a fresh instance would discard
    # this pass's in-memory session state (request caches, corroboration
    # source counts, direct-batch harvest rows) that isn't flushed to the
    # persistent on-disk cache mid-run, forcing needless re-fetches for
    # destinations that are only being retried for an unrelated reason.
    url_discoverer: Any | None = None

    def _run_urls() -> None:
        nonlocal url_discoverer
        if not skip_url_discovery:
            click.echo("Stage 5b — URL discovery…")
            from generator.url_discovery import URLDiscoverer
            url_discoverer = URLDiscoverer(
                config_path,
                llm_client=llm_client,
                disable_trails=bool(no_trails),
                alltrails_source=normalized_alltrails_source,
                attraction_source=normalized_attraction_source,
                restaurant_source=normalized_restaurant_source,
                en_route_source=normalized_en_route_source,
                output_dir=output_dir,
                search_provider_override=search_provider,
            )
            url_discoverer.discover_all(trip)
            url_discoverer.audit_discovered_urls(trip)
            click.echo("  ✓ URLs discovered and verified")
        else:
            click.echo("Stage 5b — URL discovery SKIPPED")

    def _run_urls_for_retry(subset_trip: dict[str, Any]) -> None:
        # skip_url_discovery is the same flag passed to both the initial pass
        # and the retry pass below, and this only runs when it's False -- so
        # _run_urls() above has always already populated url_discoverer by
        # the time this fires.
        url_discoverer.discover_all(subset_trip)
        url_discoverer.audit_discovered_urls(subset_trip)

    click.echo("Stages 4–5b — Cultural events, images, and URL discovery (parallel)…")
    with ThreadPoolExecutor(max_workers=3) as _stage_pool:
        _stage_futures = [_stage_pool.submit(fn) for fn in (_run_events, _run_images, _run_urls)]
        for _f in _as_completed(_stage_futures):
            _f.result()
    stage_timings["stage_4_5_parallel"] = _elapsed_seconds(stage_4_5_started)

    # Post-parallel content normalization: cross-section and cross-destination dedup.
    stage_reconcile_started = perf_counter()
    ai_gen.normalize_trip_content(trip)
    click.echo("  ✓ Content normalized")
    trip, registry = _reconcile_trip_via_registry(trip, return_registry=True)
    click.echo("  ✓ Entity registry reconciled")
    destination_status_report = _build_destination_status_report(
        trip=trip,
        registry=registry,
        run_id=run_id,
        skip_events=skip_events,
        skip_images=skip_images,
        skip_url_discovery=skip_url_discovery,
        retry_policy=retry_policy,
    )
    max_retries_per_destination = max(
        0,
        int(retry_policy.get("max_retries_per_destination_per_run", 1) or 1),
    )
    destination_status_report_path = _write_destination_status_report(output_dir, destination_status_report)
    destination_status_markdown_path = _write_destination_status_markdown_report(output_dir, destination_status_report)
    click.echo(f"  ✓ Destination status report: {destination_status_report_path}")
    click.echo(f"  ✓ Destination status summary: {destination_status_markdown_path}")

    retry_candidate_ids = _destination_ids_for_selective_retry(destination_status_report)
    runtime_metrics["retry_candidate_ids"] = retry_candidate_ids
    runtime_metrics["retry_candidate_count"] = len(retry_candidate_ids)
    stage_timings["status_build_initial"] = _elapsed_seconds(stage_reconcile_started)

    retried_destination_ids: list[str] = []
    retry_skipped_due_to_circuit_open = False
    retry_started = perf_counter()
    if max_retries_per_destination > 0 and retry_candidate_ids:
        if url_discoverer is not None and url_discoverer.is_search_circuit_open():
            # The circuit breaker just tripped during the pass that produced
            # these retry candidates -- firing a full second pass (events +
            # images + URL discovery, across every flagged destination) into a
            # known-ongoing outage would just repeat the same failures at full
            # timeout+retry cost per destination instead of failing fast.
            # Skip the whole pass and keep the initial results as-is; the
            # circuit breaker's own cooldown handles recovery on the next run.
            retry_skipped_due_to_circuit_open = True
            click.echo(
                "  ⚠ Selective retry SKIPPED — Grok circuit breaker is currently open "
                "after a recent burst of transient errors; retrying now would repeat "
                "the same failures. Keeping initial-pass results."
            )
        else:
            retried_destination_ids = _selective_retry_destinations(
                trip=trip,
                status_report=destination_status_report,
                config_path=config_path,
                llm_client=llm_client,
                output_dir=output_dir,
                refresh_image_cache=refresh_image_cache,
                skip_events=skip_events,
                skip_images=skip_images,
                skip_url_discovery=skip_url_discovery,
                no_trails=bool(no_trails),
                alltrails_source=normalized_alltrails_source,
                attraction_source=normalized_attraction_source,
                restaurant_source=normalized_restaurant_source,
                en_route_source=normalized_en_route_source,
                search_provider_override=search_provider,
                is_search_circuit_open=(
                    url_discoverer.is_search_circuit_open if url_discoverer is not None else None
                ),
                run_urls=_run_urls_for_retry,
            )
    runtime_metrics["retry_skipped_due_to_circuit_open"] = retry_skipped_due_to_circuit_open
    if retried_destination_ids:
        click.echo(
            "  ↻ Selective retry completed for: "
            + ", ".join(retried_destination_ids)
        )
        ai_gen.normalize_trip_content(trip)
        trip, registry = _reconcile_trip_via_registry(trip, return_registry=True)
        destination_status_report = _build_destination_status_report(
            trip=trip,
            registry=registry,
            run_id=run_id,
            skip_events=skip_events,
            skip_images=skip_images,
            skip_url_discovery=skip_url_discovery,
            retry_policy=retry_policy,
        )
    stage_timings["selective_retry"] = _elapsed_seconds(retry_started)
    # Read after BOTH possible normalize_trip_content() calls (the
    # unconditional one earlier, and the conditional one above that only
    # runs when retried_destination_ids is non-empty) -- last_banned_phrase_violations
    # accumulates across both, so this always reflects the real final total
    # regardless of whether a retry pass ran. Previously read right after
    # only the first call, so a run that retried anything reported a stale,
    # incomplete count that matched neither pass's real findings.
    runtime_metrics["banned_phrase_violations"] = dict(ai_gen.last_banned_phrase_violations)
    destination_status_report = _annotate_retry_outcomes(
        status_report=destination_status_report,
        attempted_destination_ids=retried_destination_ids,
        max_retries_per_destination_per_run=max_retries_per_destination,
    )
    destination_status_report_path = _write_destination_status_report(output_dir, destination_status_report)
    destination_status_markdown_path = _write_destination_status_markdown_report(output_dir, destination_status_report)
    click.echo(f"  ✓ Destination status report refreshed: {destination_status_report_path}")
    click.echo(f"  ✓ Destination status summary refreshed: {destination_status_markdown_path}")
    unresolved_destination_ids = _destination_ids_needing_attention(destination_status_report)
    runtime_metrics["retry_efficiency"] = _build_retry_efficiency_metrics(
        destination_count=len(trip.get("destinations", []) or []),
        retry_candidate_ids=retry_candidate_ids,
        retried_destination_ids=retried_destination_ids,
        unresolved_destination_ids=unresolved_destination_ids,
        max_retries_per_destination_per_run=max_retries_per_destination,
    )
    retry_efficiency = runtime_metrics.get("retry_efficiency", {}) if isinstance(runtime_metrics.get("retry_efficiency", {}), dict) else {}
    click.echo(
        "  ⏱ Retry scope: "
        f"{retry_efficiency.get('retried_destination_count', 0)}/"
        f"{retry_efficiency.get('destination_count', 0)} destination(s) retried "
        f"({retry_efficiency.get('retry_scope_reduction_percent', 0.0):.1f}% work avoided vs full rerun)"
    )
    if unresolved_destination_ids:
        click.echo("  ! Unresolved destinations after retry: " + ", ".join(unresolved_destination_ids))
    else:
        click.echo("  ✓ No unresolved destinations after retry pass")
    stage_timings["stage_4_5_postprocessing"] = _elapsed_seconds(stage_reconcile_started)
    image_counter_delta = _counter_delta(image_counters_before, ImageFetcher.snapshot_counters())
    url_validator_counter_delta = _counter_delta(url_validator_counters_before, URLValidator.snapshot_counters())

    # Circuit-breaker trip count / total-open-seconds per search client,
    # for measuring how much of this run's wall-clock time was actually
    # spent blocked (2026-08-15 half-open-recovery follow-up) instead of
    # inferring it after the fact from scattered log lines. url_discoverer
    # is None when skip_url_discovery was set -- no clients to report on.
    if url_discoverer is not None:
        circuit_breaker_stats: dict[str, Any] = {}
        for label, client in (
            ("url_discovery_batch", getattr(url_discoverer, "_search", None)),
            ("url_discovery_fallback", getattr(url_discoverer, "_search_fallback", None)),
        ):
            if client is not None and hasattr(client, "get_circuit_breaker_stats"):
                circuit_breaker_stats[label] = client.get_circuit_breaker_stats()
        if circuit_breaker_stats:
            runtime_metrics["circuit_breaker_stats"] = circuit_breaker_stats

    if verbose:
        registry_report_path = _write_entity_registry_debug_report(output_dir, registry)
        click.echo(f"  ✓ Entity registry debug report: {registry_report_path}")

    # ── Stage 6: Assemble HTML ───────────────────────────────────────────────
    stage_6_started = perf_counter()
    click.echo("Stage 6/6 — Assembling HTML…")
    from generator.html_assembler import HTMLAssembler
    trip["_meta"] = {
        "generator_version": __version__,
        "template_version": __template_version__,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": environment_selected,
        "privacy_redacted": redact_privacy_details,
        "manifest_name": Path(manifest).name,
        "development_build": development_build,
        "llm": {
            "provider": llm_client.provider,
            "model": llm_client.model,
            "usage": _record_observed_models(llm_client, llm_effective),
        },
    }
    assembler = HTMLAssembler(config_path)
    html = assembler.assemble(trip)

    output_file.write_text(html, encoding="utf-8")
    click.echo(f"  ✓ index.html written ({output_file.stat().st_size:,} bytes)")

    current_output_urls = _read_output_urls(output_file)
    parity_summary = _latest_direct_batch_parity_summary(output_dir=output_dir)
    click.echo(
        "  ✓ Parity check: "
        f"{parity_summary['captured_html_input_count']} captured HTML inputs | "
        f"{parity_summary['unique_captured_urls']} unique captured URLs | "
        f"{parity_summary['unique_final_html_urls']} unique URLs in the assembled final HTML | "
        f"{parity_summary['destinations_missing_at_least_one_captured_url']} destinations missing at least one captured URL"
    )
    if parity_summary["destinations_missing_captured_url_names"]:
        click.echo(
            "  ! Destinations missing at least one captured URL: "
            + ", ".join(parity_summary["destinations_missing_captured_url_names"])
        )
    parity_report_path = _write_direct_batch_parity_report(
        output_dir=output_dir,
        parity_summary=parity_summary,
        run_id=run_id,
    )
    click.echo(f"  ✓ Direct-batch parity report: {parity_report_path}")
    url_diff_report_path = _write_url_diff_report(
        output_dir=output_dir,
        baseline_urls=baseline_output_urls,
        current_urls=current_output_urls,
        run_id=run_id,
    )
    url_diff_markdown_path = _write_url_diff_markdown_report(
        output_dir=output_dir,
        baseline_urls=baseline_output_urls,
        current_urls=current_output_urls,
        run_id=run_id,
    )
    click.echo(f"  ✓ URL diff report: {url_diff_report_path}")
    click.echo(f"  ✓ URL diff summary: {url_diff_markdown_path}")

    _write_pwa_assets(output_dir, trip)
    click.echo("  ✓ PWA assets written (manifest.webmanifest, sw.js)")

    # ── Validate ─────────────────────────────────────────────────────────────
    from generator.html_validator import HTMLValidator
    from generator.report_writer import ReportWriter
    validator = HTMLValidator(config_path)
    report = validator.validate(output_file, trip)
    report_path = ReportWriter(output_dir).write(report)
    click.echo(f"  ✓ Validation report: {report_path}")

    _run_quality_gate(trip, output_file)

    llm_usage = trip.get("_meta", {}).get("llm", {}).get("usage", {})
    estimated_cost = summarize_from_usage(llm_usage)
    tool_call_cost = float(llm_usage.get("total_tool_call_cost_usd", 0.0) or 0.0)
    usage_models = llm_usage.get("models", [])
    if usage_models:
        cost_summary_model = "+".join(
            f"{row.get('provider')}/{row.get('model')}" for row in usage_models
        )
    else:
        cost_summary_model = trip.get("_meta", {}).get("llm", {}).get("model", llm_client.model)
    print_cost_summary(
        model=cost_summary_model,
        manifest_path=manifest,
        estimated_usd=estimated_cost,
        environment=environment_selected,
        tool_call_cost_usd=tool_call_cost,
    )
    if usage_models:
        click.echo("  Usage breakdown by provider/model:")
        for row in usage_models:
            tool_calls = row.get("tool_calls", 0)
            tool_suffix = (
                f" web_search_calls={tool_calls} tool_fee=${row.get('tool_call_cost_usd', 0.0):.4f}"
                if tool_calls
                else ""
            )
            click.echo(
                f"    - {row.get('provider')}/{row.get('model')}: "
                f"calls={row.get('calls', 0)} tokens={row.get('total_tokens', 0)} "
                f"est=${row.get('estimated_cost_usd', 0.0):.4f}{tool_suffix}"
            )
    runtime_metrics["gate_a"] = _build_gate_a_metrics(
        trip=trip,
        usage_summary=trip.get("_meta", {}).get("llm", {}).get("usage", {}),
        stage_timings=stage_timings,
        skip_events=skip_events,
        skip_images=skip_images,
        skip_url_discovery=skip_url_discovery,
        image_counter_delta=image_counter_delta,
        url_validator_counter_delta=url_validator_counter_delta,
    )
    click.echo(
        "  ✓ Gate A metrics captured: "
        f"ai_calls={runtime_metrics['gate_a']['provider_calls_by_stage']['stage_3_ai_generation']['llm_generate_json_calls']} "
        f"url_search_calls={runtime_metrics['gate_a']['provider_calls_by_stage']['stage_4_5_parallel']['url_discovery_search_calls']}"
    )
    stage_timings["stage_6_assemble_validate"] = _elapsed_seconds(stage_6_started)
    stage_timings["total_pipeline"] = _elapsed_seconds(run_started_at_perf)

    if not report["valid"]:
        click.echo(f"\n⚠️  {report['error_count']} validation error(s) found:", err=True)
        for e in report["errors"]:
            click.echo(f"   ✗ {e}", err=True)
        _finalize_run("validation_failed", 2, f"{report['error_count']} validation errors")
        sys.exit(2)

    _finalize_run("completed", 0)
    click.echo(f"\n✅ Done! Open {output_file.resolve()} in your browser.")


if __name__ == "__main__":
    main()
