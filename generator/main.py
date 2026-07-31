"""
main.py — CLI entry point for the Road Trip Itinerary Generator.

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
  --skip-images     Skip image fetching (useful for fast content iteration)
  --skip-events     Skip cultural events discovery
  --skip-url-discovery  Skip URL discovery (AI content only)
  --destination     Process only this destination id (repeatable)
  --verbose         Enable debug logging
"""

from __future__ import annotations
import atexit
import json
import logging, os, sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
import click
from generator import __version__, __template_version__
from generator.entity_registry import build_entity_registry, reconcile_trip_from_registry

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


def _reconcile_trip_via_registry(trip: dict, *, return_registry: bool = False) -> dict | tuple[dict, dict[str, Any]]:
    registry = build_entity_registry(trip)
    reconciled = reconcile_trip_from_registry(trip, registry)
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

        retry_triggers: list[str] = []
        if quarantined_entity_ids or validation_counts["quarantined"] > 0:
            retry_triggers.append("registry_quarantined_entities")
        if validation_counts["needs_retry"] > 0:
            retry_triggers.append("registry_entities_needing_retry")
        if not skip_images and image_count == 0:
            retry_triggers.append("image_shortfall")
        if not skip_url_discovery and validation_counts["total"] > 0 and validation_counts["accepted"] == 0:
            retry_triggers.append("url_collapse")

        accepted_for_ratio = validation_counts["accepted"]
        evaluated_for_ratio = (
            validation_counts["accepted"]
            + validation_counts["rejected"]
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
                        "needs_retry_count": validation_counts["needs_retry"],
                        "quarantined_count": validation_counts["quarantined"],
                        "acceptance_ratio": round(url_acceptance_ratio, 4),
                        "acceptance_ratio_threshold": min_url_acceptance_ratio,
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
            lines.append(f"- {destination_name} ({destination_id}) — status={status}, terminal={terminal_state}")

    path = output_dir / "destination_status_report.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


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

    retry_trip = {
        "trip": trip.get("trip", {}),
        "destinations": retry_destinations,
    }

    if not skip_events:
        if run_events is None:
            from generator.cultural_events import CulturalEventsDiscoverer

            run_events = lambda subset_trip: CulturalEventsDiscoverer(config_path, llm_client=llm_client).discover(subset_trip)
        run_events(retry_trip)

    if not skip_images:
        if run_images is None:
            from generator.image_fetcher import ImageFetcher

            run_images = lambda subset_trip: ImageFetcher(
                config_path,
                output_dir=output_dir / "images",
                force_refresh=refresh_image_cache,
            ).fetch_all(subset_trip)
        run_images(retry_trip)

    if not skip_url_discovery:
        if run_urls is None:
            from generator.url_discovery import URLDiscoverer

            def _default_run_urls(subset_trip: dict[str, Any]) -> None:
                url_discoverer = URLDiscoverer(config_path, llm_client=llm_client)
                url_discoverer.discover_all(subset_trip)
                url_discoverer.audit_discovered_urls(subset_trip)

            run_urls = _default_run_urls
        run_urls(retry_trip)

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


def _append_run_ledger(ledger_path: Path, record: dict) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


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
    type=click.Choice(["dev", "test", "prod"], case_sensitive=False),
    help="Environment override (dev/test/prod). Optional.",
)
@click.option(
    "--env-file",
    type=click.Path(exists=True),
    help="Optional path to .env file. If provided, loaded before environment resolution.",
)
@click.option("--llm-model", type=str, help="Override LLM model for this run")
@click.option("--dry-run", is_flag=True, help="Parse & validate only; no AI calls")
@click.option("--skip-images", is_flag=True, help="Skip image fetching")
@click.option("--refresh-image-cache", is_flag=True, help="Force refresh image-provider queries, bypassing local image cache")
@click.option("--skip-events", is_flag=True, help="Skip cultural events discovery")
@click.option("--skip-url-discovery", is_flag=True, help="Skip URL discovery")
@click.option("--noschedule", is_flag=True, help="Suppress schedule card rendering in output HTML")
@click.option("--destination", "destinations", multiple=True, help="Limit to specific destination ids")
@click.option("--first-destination", "first_destination_only", is_flag=True, help="Process only the first destination after any destination filtering")
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
    llm_model: str | None,
    dry_run: bool,
    skip_images: bool,
    refresh_image_cache: bool,
    skip_events: bool,
    skip_url_discovery: bool,
    noschedule: bool,
    destinations: tuple[str, ...],
    first_destination_only: bool,
    log_level: str,
    verbose: bool,
) -> None:
    run_started_at = datetime.now(timezone.utc)
    run_started_at_perf = perf_counter()
    run_id = run_started_at.strftime("%Y%m%dT%H%M%S.%fZ")
    ledger_path = Path(output) / "dev" / "run_ledger.jsonl"
    finalized = False
    stage_timings: dict[str, float] = {}
    runtime_metrics: dict[str, Any] = {}

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
            "environment": environment,
            "env_file": env_file,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "dry_run": bool(dry_run),
            "skip_images": bool(skip_images),
            "skip_events": bool(skip_events),
            "skip_url_discovery": bool(skip_url_discovery),
            "first_destination_only": bool(first_destination_only),
            "destinations": list(destinations),
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

    click.echo(f"🗺  Road Trip Itinerary Generator")
    click.echo(f"   Manifest : {manifest}")
    click.echo(f"   Output   : {output_dir.resolve()}")
    click.echo(f"   Config   : {config_path}")
    click.echo(f"   Logging  : {effective_log_level}")

    if llm_provider:
        click.echo(f"   LLM      : provider override = {llm_provider.lower()}")
    if llm_model:
        click.echo(f"   LLM      : model override = {llm_model}")
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
    trip = parser.load(manifest)

    # ── Hybrid environment selection ─────────────────────────────────────────
    env_from_manifest = trip.get("trip", {}).get("environment")
    env_from_cli = environment
    env_from_env = os.environ.get("ENVIRONMENT")

    environment_selected = (
        (env_from_cli or env_from_manifest or env_from_env or "dev").lower()
    )

    click.echo(
        click.style("   Env      : ", fg="cyan") +
        click.style(environment_selected, fg="green")
    )

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
    llm_overrides = dict(trip.get("trip", {}).get("llm", {}))

    # ── Hybrid provider selection ───────────────────────────────────────────
    provider_from_manifest = trip.get("trip", {}).get("llm_provider")
    provider_from_cli = llm_provider
    provider_from_env = os.environ.get("LLM_PROVIDER")
    provider_selected = (provider_from_cli or provider_from_manifest or provider_from_env)

    if trip.get("trip", {}).get("llm_provider"):
        llm_overrides["provider"] = trip["trip"].get("llm_provider")
    if trip.get("trip", {}).get("llm_features"):
        llm_overrides["features"] = trip["trip"].get("llm_features")
    if llm_provider:
        llm_overrides["provider"] = llm_provider.lower()
    elif provider_selected:
        llm_overrides["provider"] = provider_selected.lower()

    click.echo(
        click.style("   LLM      : provider = ", fg="cyan") +
        click.style(llm_overrides.get("provider"), fg="green")
    )

    if llm_model:
        llm_overrides["model"] = llm_model

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

    def _run_events() -> None:
        if not skip_events:
            click.echo("Stage 4/6 — Cultural events discovery…")
            from generator.cultural_events import CulturalEventsDiscoverer
            CulturalEventsDiscoverer(config_path, llm_client=llm_client).discover(trip)
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

    def _run_urls() -> None:
        if not skip_url_discovery:
            click.echo("Stage 5b — URL discovery…")
            from generator.url_discovery import URLDiscoverer
            url_discoverer = URLDiscoverer(config_path, llm_client=llm_client)
            url_discoverer.discover_all(trip)
            url_discoverer.audit_discovered_urls(trip)
            click.echo("  ✓ URLs discovered and verified")
        else:
            click.echo("Stage 5b — URL discovery SKIPPED")

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
    retry_started = perf_counter()
    if max_retries_per_destination > 0:
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
        )
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
        "llm": {
            "provider": llm_client.provider,
            "model": llm_client.model,
            "usage": llm_client.usage_summary(),
        },
    }
    assembler = HTMLAssembler(config_path)
    html = assembler.assemble(trip)

    output_file.write_text(html, encoding="utf-8")
    click.echo(f"  ✓ index.html written ({output_file.stat().st_size:,} bytes)")

    current_output_urls = _read_output_urls(output_file)
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

    predicted_cost, actual_cost = summarize_from_usage(trip.get("_meta", {}).get("llm", {}).get("usage", {}))
    print_cost_summary(
        model=trip.get("_meta", {}).get("llm", {}).get("model", llm_client.model),
        manifest_path=manifest,
        predicted_usd=predicted_cost,
        actual_usd=actual_cost,
        environment=environment_selected,
    )
    usage_models = trip.get("_meta", {}).get("llm", {}).get("usage", {}).get("models", [])
    if usage_models:
        click.echo("  Usage breakdown by provider/model:")
        for row in usage_models:
            click.echo(
                f"    - {row.get('provider')}/{row.get('model')}: "
                f"calls={row.get('calls', 0)} tokens={row.get('total_tokens', 0)} "
                f"est=${row.get('estimated_cost_usd', 0.0):.4f}"
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
