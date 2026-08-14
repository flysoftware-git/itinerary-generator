from __future__ import annotations

from copy import deepcopy
from typing import Any
import re


_TRAIL_TYPE_TOKENS = {"hike", "hiking", "trail", "trek", "walk"}
_TRAIL_NAME_PATTERN = re.compile(r"\b(trail|hike|hiking|loop|walk|trek|path|summit|narrows)\b", re.IGNORECASE)
_ACCEPTED_STATUSES = {"accepted", "pending"}
_SECTION_TARGETS = (
    "top_attractions",
    "scenic_drives",
    "getting_here.en_route_stops",
    "getting_there.route_options",
    "dinner_recommendations",
    "cultural_events",
)


def _normalized_name(text: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", str(text or "").lower())
    return "-".join(tokens)


def _classify_attraction(item: dict[str, Any]) -> str:
    item_type = str(item.get("type", "") or "").strip().lower()
    item_name = str(item.get("name", "") or "")
    if item_type in _TRAIL_TYPE_TOKENS or _TRAIL_NAME_PATTERN.search(item_name):
        return "trail"
    return "attraction"


def _description_for(section_target: str, item: dict[str, Any]) -> str:
    if section_target in {"scenic_drives", "getting_there.route_options"}:
        return str(item.get("description", "") or "")
    if section_target == "cultural_events":
        return str(item.get("description", "") or item.get("date", "") or "")
    return str(item.get("description", "") or item.get("summary", "") or "")


def _display_name_for(section_target: str, item: dict[str, Any]) -> str:
    if section_target in {"scenic_drives", "getting_there.route_options"}:
        return str(item.get("title", "") or "")
    return str(item.get("name", "") or "")


def _entity_class_for(section_target: str, item: dict[str, Any]) -> str:
    if section_target == "top_attractions":
        return _classify_attraction(item)
    if section_target == "scenic_drives":
        return "scenic_drive"
    if section_target == "getting_here.en_route_stops":
        return "en_route_stop"
    if section_target == "getting_there.route_options":
        return "route_option"
    if section_target == "dinner_recommendations":
        return "restaurant"
    return "event"


def _registry_directives(item: dict[str, Any]) -> dict[str, Any]:
    directives = item.get("_registry", {}) if isinstance(item.get("_registry", {}), dict) else {}
    return directives


def _report_for(reports: list[dict[str, Any]], destination_id: str) -> dict[str, Any]:
    for report in reports:
        if str(report.get("destination_id", "") or "") == destination_id:
            return report
    report = {
        "destination_id": destination_id,
        "accepted": [],
        "rejected": [],
        "reassigned": [],
        "quarantined": [],
    }
    reports.append(report)
    return report


def _clean_payload(payload: dict[str, Any], rendered_url: str) -> dict[str, Any]:
    cleaned = deepcopy(payload)
    cleaned.pop("_registry", None)
    if "url" in cleaned or rendered_url:
        cleaned["url"] = rendered_url
    return cleaned


def _append_registry_decision_entities(
    *,
    dest: dict[str, Any],
    destination_id: str,
    entities: list[dict[str, Any]],
    reports: list[dict[str, Any]],
) -> None:
    decisions = dest.get("_registry_decisions", []) if isinstance(dest.get("_registry_decisions", []), list) else []
    if not decisions:
        return

    report = _report_for(reports, destination_id)
    for ordering_hint, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            continue
        display_name = str(decision.get("display_name", "") or "")
        entity_class = str(decision.get("entity_class", "generic") or "generic")
        normalized_name = _normalized_name(display_name)
        entity_id = str(decision.get("entity_id", "") or f"{destination_id}:{entity_class}:{normalized_name or ordering_hint}:decision")
        validation_status = str(decision.get("validation_status", "rejected") or "rejected")
        rejection_reasons = [
            str(reason or "")
            for reason in (decision.get("rejection_reasons", []) or [])
            if str(reason or "")
        ]
        record = {
            "entity_id": entity_id,
            "destination_id": destination_id,
            "entity_class": entity_class,
            "ownership_type": str(decision.get("ownership_type", "destination") or "destination"),
            "source_stage": str(decision.get("source_stage", "reconciliation") or "reconciliation"),
            "display_name": display_name,
            "normalized_name": normalized_name,
            "description": str(decision.get("description", "") or ""),
            "raw_payload": deepcopy(decision.get("raw_payload", {})) if isinstance(decision.get("raw_payload", {}), dict) else {},
            "confidence": str(decision.get("confidence", "high") or "high"),
            "validation_status": validation_status,
            "rejection_reasons": rejection_reasons,
            "rendered_url": str(decision.get("rendered_url", "") or ""),
            "candidate_urls": list(decision.get("candidate_urls", []) or []) if isinstance(decision.get("candidate_urls", []), list) else [],
            "section_target": str(decision.get("section_target", "") or ""),
            "ordering_hint": int(decision.get("ordering_hint", ordering_hint) or ordering_hint),
            "shared_group_id": decision.get("shared_group_id"),
            "metadata": deepcopy(decision.get("metadata", {})) if isinstance(decision.get("metadata", {}), dict) else {"removed": True},
        }
        entities.append(record)

        if validation_status == "quarantined":
            report["quarantined"].append(entity_id)
        elif validation_status in _ACCEPTED_STATUSES:
            report["accepted"].append(entity_id)
        else:
            report["rejected"].append({
                "entity_id": entity_id,
                "reasons": rejection_reasons,
            })


def _section_items(dest: dict[str, Any], section_target: str) -> list[dict[str, Any]]:
    ai = dest.get("ai_content", {}) if isinstance(dest.get("ai_content", {}), dict) else {}
    if section_target == "top_attractions":
        return list(ai.get("top_attractions", []) or [])
    if section_target == "getting_here.en_route_stops":
        getting_here = ai.get("getting_here", {}) if isinstance(ai.get("getting_here", {}), dict) else {}
        return list(getting_here.get("en_route_stops", []) or [])
    if section_target == "getting_there.route_options":
        getting_there = ai.get("getting_there", {}) if isinstance(ai.get("getting_there", {}), dict) else {}
        return list(getting_there.get("route_options", []) or [])
    if section_target == "dinner_recommendations":
        return list(ai.get("dinner_recommendations", []) or [])
    if section_target == "scenic_drives":
        return list(dest.get("scenic_drives", []) or [])
    events = dest.get("cultural_events", {}) if isinstance(dest.get("cultural_events", {}), dict) else {}
    return list(events.get("events", []) or [])


def build_entity_registry(trip: dict[str, Any]) -> dict[str, Any]:
    entities: list[dict[str, Any]] = []
    destination_view: dict[str, dict[str, list[str]]] = {}
    reports: list[dict[str, Any]] = []

    for dest in trip.get("destinations", []) or []:
        destination_id = str(dest.get("id", "") or "")
        destination_view[destination_id] = {section: [] for section in _SECTION_TARGETS}
        report = _report_for(reports, destination_id)

        for section_target in _SECTION_TARGETS:
            for ordering_hint, item in enumerate(_section_items(dest, section_target)):
                if not isinstance(item, dict):
                    continue
                directives = _registry_directives(item)
                display_name = _display_name_for(section_target, item)
                entity_class = _entity_class_for(section_target, item)
                normalized_name = _normalized_name(display_name)
                entity_id = f"{destination_id}:{entity_class}:{normalized_name or ordering_hint}"
                ownership_type = str(
                    directives.get(
                        "ownership_type",
                        "transfer_leg" if section_target == "getting_there.route_options" else "destination",
                    )
                    or "destination"
                )
                target_section = str(directives.get("section_target", section_target) or section_target)
                if target_section not in _SECTION_TARGETS:
                    target_section = section_target
                validation_status = str(directives.get("validation_status", "accepted") or "accepted")
                rejection_reasons = [
                    str(reason or "")
                    for reason in (directives.get("rejection_reasons", []) or [])
                    if str(reason or "")
                ]
                rendered_url = str(directives.get("rendered_url", item.get("url", "")) or "")
                record = {
                    "entity_id": entity_id,
                    "destination_id": destination_id,
                    "entity_class": entity_class,
                    "ownership_type": ownership_type,
                    "source_stage": "reconciliation",
                    "display_name": display_name,
                    "normalized_name": normalized_name,
                    "description": _description_for(section_target, item),
                    "raw_payload": _clean_payload(item, rendered_url),
                    "confidence": "high",
                    "validation_status": validation_status,
                    "rejection_reasons": rejection_reasons,
                    "rendered_url": rendered_url,
                    "candidate_urls": list(item.get("url_candidates", []) or []) if isinstance(item.get("url_candidates", []), list) else [],
                    "section_target": target_section,
                    "ordering_hint": ordering_hint,
                    "shared_group_id": None,
                    "metadata": {},
                }
                entities.append(record)
                destination_view[destination_id][target_section].append(entity_id)
                if target_section != section_target:
                    report["reassigned"].append({
                        "entity_id": entity_id,
                        "from": section_target,
                        "to": target_section,
                    })
                if validation_status == "quarantined":
                    report["quarantined"].append(entity_id)
                elif validation_status in _ACCEPTED_STATUSES:
                    report["accepted"].append(entity_id)
                else:
                    report["rejected"].append({
                        "entity_id": entity_id,
                        "reasons": rejection_reasons,
                    })

        _append_registry_decision_entities(
            dest=dest,
            destination_id=destination_id,
            entities=entities,
            reports=reports,
        )

    return {
        "entities": entities,
        "destination_view": destination_view,
        "reports": reports,
    }


def reconcile_trip_from_registry(trip: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    reconciled = deepcopy(trip)
    entities = registry.get("entities", []) if isinstance(registry.get("entities", []), list) else []
    by_id = {str(entity.get("entity_id", "") or ""): entity for entity in entities if isinstance(entity, dict)}
    destination_view = registry.get("destination_view", {}) if isinstance(registry.get("destination_view", {}), dict) else {}

    def _payloads(destination_id: str, section_target: str) -> list[dict[str, Any]]:
        refs = destination_view.get(destination_id, {}).get(section_target, []) if isinstance(destination_view.get(destination_id, {}), dict) else []
        ordered: list[tuple[int, str, dict[str, Any]]] = []
        for ref in refs:
            entity = by_id.get(str(ref or ""))
            if not entity:
                continue
            if str(entity.get("validation_status", "accepted") or "accepted") not in _ACCEPTED_STATUSES:
                continue
            ordered.append((
                int(entity.get("ordering_hint", 0) or 0),
                str(entity.get("entity_id", "") or ""),
                deepcopy(entity.get("raw_payload", {})),
            ))
        ordered.sort(key=lambda item: (item[0], item[1]))
        return [payload for _, _, payload in ordered]

    for dest in reconciled.get("destinations", []) or []:
        destination_id = str(dest.get("id", "") or "")
        ai = dest.get("ai_content", {}) if isinstance(dest.get("ai_content", {}), dict) else {}
        ai = deepcopy(ai)
        getting_here = ai.get("getting_here", {}) if isinstance(ai.get("getting_here", {}), dict) else {}
        getting_there = ai.get("getting_there", {}) if isinstance(ai.get("getting_there", {}), dict) else {}
        cultural_events = dest.get("cultural_events", {}) if isinstance(dest.get("cultural_events", {}), dict) else {}

        ai["top_attractions"] = _payloads(destination_id, "top_attractions")
        ai["dinner_recommendations"] = _payloads(destination_id, "dinner_recommendations")
        getting_here["en_route_stops"] = _payloads(destination_id, "getting_here.en_route_stops")
        getting_there["route_options"] = _payloads(destination_id, "getting_there.route_options")
        ai["getting_here"] = getting_here
        ai["getting_there"] = getting_there
        dest["ai_content"] = ai

        dest["scenic_drives"] = _payloads(destination_id, "scenic_drives")
        cultural_events["events"] = _payloads(destination_id, "cultural_events")
        dest["cultural_events"] = cultural_events

    return reconciled


_SCHEDULE_FALLBACK_BY_PERIOD = {
    "morning": "Start with a currently eligible priority stop and keep parking buffers before midday crowds.",
    "afternoon": "Focus on currently eligible nearby highlights and realistic transition time between stops.",
    "evening": "Wrap with an eligible low-friction stop and dinner near your base.",
}
_SCHEDULE_FALLBACK_GENERIC = "Use currently eligible activities for this block and avoid filtered-out stops."
# An entity can stay "accepted" in the registry while carrying one of these
# reason codes -- e.g. a trail demoted to a plain attraction for exceeding
# the configured mileage threshold is still present, just no longer the
# thing a schedule mention originally implied ("go hike this trail"). Such
# mentions must still be scrubbed even though the entity itself survives.
_SOFT_DEMOTION_REASONS = {"threshold_demoted_to_attraction"}


def _schedule_summary_mentions_entity(summary: str, entity_name: str) -> bool:
    text = str(summary or "")
    name = str(entity_name or "").strip()
    if not text or not name:
        return False
    pattern = r"\b" + re.escape(name).replace(r"\ ", r"\s+") + r"\b"
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def reconcile_schedule_from_registry(reconciled_trip: dict[str, Any], registry: dict[str, Any]) -> None:
    """Strip stale schedule-text references to entities the registry rejected
    or quarantined, across every section (not just top_attractions) -- mutates
    reconciled_trip in place.

    This must run after reconcile_trip_from_registry, against the final
    entity state: an entity dropped anywhere in the pipeline (URL audit,
    within-destination dedup, cross-destination reassignment, ...) is only
    reliably knowable once the registry itself is final. Replaces the
    affected period's summary with a generic-but-truthful fallback rather
    than re-calling the LLM -- deterministic and consistent with the rest of
    the pipeline's cost profile, at the cost of losing some specificity in
    that one period.
    """
    entities = registry.get("entities", []) if isinstance(registry.get("entities", []), list) else []
    blocked_by_destination: dict[str, set[str]] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        display_name = str(entity.get("display_name", "") or "").strip()
        if not display_name:
            continue
        validation_status = str(entity.get("validation_status", "accepted") or "accepted")
        rejection_reasons = {
            str(reason or "") for reason in (entity.get("rejection_reasons", []) or []) if str(reason or "")
        }
        is_blocked = validation_status not in _ACCEPTED_STATUSES or bool(rejection_reasons & _SOFT_DEMOTION_REASONS)
        if not is_blocked:
            continue
        destination_id = str(entity.get("destination_id", "") or "")
        blocked_by_destination.setdefault(destination_id, set()).add(display_name)

    if not blocked_by_destination:
        return

    for dest in reconciled_trip.get("destinations", []) or []:
        destination_id = str(dest.get("id", "") or "")
        blocked_names = blocked_by_destination.get(destination_id)
        if not blocked_names:
            continue
        ai = dest.get("ai_content", {}) if isinstance(dest.get("ai_content", {}), dict) else {}
        schedule = ai.get("possible_daily_schedule", []) if isinstance(ai.get("possible_daily_schedule", []), list) else []
        for day in schedule:
            if not isinstance(day, dict):
                continue
            periods = day.get("periods", []) if isinstance(day.get("periods", []), list) else []
            for period in periods:
                if not isinstance(period, dict):
                    continue
                summary = str(period.get("summary", "") or "").strip()
                if not summary:
                    continue
                if not any(_schedule_summary_mentions_entity(summary, name) for name in blocked_names):
                    continue
                label = str(period.get("period", "") or "").strip().lower()
                period["summary"] = _SCHEDULE_FALLBACK_BY_PERIOD.get(label, _SCHEDULE_FALLBACK_GENERIC)