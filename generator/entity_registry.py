from __future__ import annotations

from copy import deepcopy
from typing import Any
import re


_TRAIL_TYPE_TOKENS = {"hike", "hiking", "trail", "trek", "walk"}
_TRAIL_NAME_PATTERN = re.compile(r"\b(trail|hike|hiking|loop|walk|trek|path|summit|narrows)\b", re.IGNORECASE)
_ACCEPTED_STATUSES = {"accepted", "pending"}


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
    section_targets = (
        "top_attractions",
        "scenic_drives",
        "getting_here.en_route_stops",
        "getting_there.route_options",
        "dinner_recommendations",
        "cultural_events",
    )

    for dest in trip.get("destinations", []) or []:
        destination_id = str(dest.get("id", "") or "")
        destination_view[destination_id] = {section: [] for section in section_targets}
        reports.append({
            "destination_id": destination_id,
            "accepted": [],
            "rejected": [],
            "reassigned": [],
            "quarantined": [],
        })
        report = reports[-1]

        for section_target in section_targets:
            for ordering_hint, item in enumerate(_section_items(dest, section_target)):
                if not isinstance(item, dict):
                    continue
                display_name = _display_name_for(section_target, item)
                entity_class = _entity_class_for(section_target, item)
                normalized_name = _normalized_name(display_name)
                entity_id = f"{destination_id}:{entity_class}:{normalized_name or ordering_hint}"
                record = {
                    "entity_id": entity_id,
                    "destination_id": destination_id,
                    "entity_class": entity_class,
                    "ownership_type": "transfer_leg" if section_target == "getting_there.route_options" else "destination",
                    "source_stage": "reconciliation",
                    "display_name": display_name,
                    "normalized_name": normalized_name,
                    "description": _description_for(section_target, item),
                    "raw_payload": deepcopy(item),
                    "confidence": "high",
                    "validation_status": "accepted",
                    "rejection_reasons": [],
                    "rendered_url": str(item.get("url", "") or ""),
                    "candidate_urls": list(item.get("url_candidates", []) or []) if isinstance(item.get("url_candidates", []), list) else [],
                    "section_target": section_target,
                    "ordering_hint": ordering_hint,
                    "shared_group_id": None,
                    "metadata": {},
                }
                entities.append(record)
                destination_view[destination_id][section_target].append(entity_id)
                report["accepted"].append(entity_id)

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