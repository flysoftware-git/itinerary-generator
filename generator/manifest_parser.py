"""
manifest_parser.py — YAML manifest parsing and schema validation.

Seeds must be plain name strings only — no URLs. The generator resolves
all URLs independently via live web search (see generator/url_discovery.py
and generator/search_provider.py) after content generation.
"""
from __future__ import annotations
from datetime import datetime
import logging
import re
from pathlib import Path
from typing import Any
import yaml
import jsonschema

from generator.multi_site_grouping import VALID_BASE_OWNED_CATEGORIES

logger = logging.getLogger(__name__)

MANIFEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["trip", "destinations"],
    "properties": {
        "trip": {
            "type": "object",
            "required": ["title", "subtitle", "theme_color"],
            "properties": {
                "title": {"type": "string"},
                "subtitle": {"type": "string"},
                "theme_color": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                "budget": {
                    "description": "Optional budget guidance consumed by content generation.",
                    "oneOf": [
                        {"type": "string"},
                        {"type": "number"},
                        {
                            "type": "object",
                            "additionalProperties": {
                                "oneOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}]
                            },
                        },
                    ],
                },
                "departure": {
                    "type": "string",
                    "description": "Optional trip starting point used for full-route directions and first destination getting-here context.",
                },
                "departure_datetime": {
                    "type": "string",
                    "description": "Optional departure date/time anchor used for route overview labels and schedule feasibility guidance.",
                },
                "return": {
                    "type": "string",
                    "description": "Optional trip endpoint after the final destination for full-route directions.",
                },
                "return_datetime": {
                    "type": "string",
                    "description": "Optional return date/time anchor used for route overview labels and schedule feasibility guidance.",
                },
                "default_day_start_time": {
                    "type": "string",
                    "description": "Optional default local day start time (e.g., '10:00 AM') used by schedule realism when allocating transit and activities.",
                },
                "default_daily_activity_hours": {
                    "type": "number",
                    "description": "Optional default maximum activity hours per day used for schedule packing (default: 5).",
                },
                "attractions_per_day": {
                    "type": "number",
                    "description": "Optional default target number of attractions to keep per destination-day when ranking candidates.",
                },
                "restaurants_per_day": {
                    "type": "number",
                    "description": "Optional default target number of dinner_recommendations to keep per destination-day when ranking candidates. Mirrors attractions_per_day.",
                },
                "en_route_stops_per_day": {
                    "type": "number",
                    "description": "Optional default target number of en-route stops to keep per destination-day (scaled by the ARRIVING destination's day count, not the drive itself) when trimming candidates. Mirrors attractions_per_day.",
                },
                "has_high_clearance_vehicle": {
                    "type": "boolean",
                    "description": "Optional traveler vehicle declaration. When explicitly set to "
                                    "false, scenic drives whose vehicle_requirement is "
                                    "'High-clearance recommended' or '4WD required' (see "
                                    "prompts/scenic_drives.txt) are excluded from the generated "
                                    "output -- no point recommending a drive the traveler can't "
                                    "make. Omitted (or true) = current behavior, unchanged; this "
                                    "is an opt-in filter, never a new default restriction.",
                },
                "llm_provider": {
                    "type": "string",
                    "enum": ["openai", "anthropic", "deepseek", "gemini", "grok", "azure_openai"],
                },
                "environment": {
                    "type": "string",
                    "enum": ["dev", "test", "prod"],
                    "description": "Optional environment tag for hybrid selection. "
                                   "Priority: CLI > manifest > ENVIRONMENT env var. "
                                   "Does not affect config.yaml unless user chooses "
                                   "to implement environment-specific configs later."
                },
                "llm_features": {
                    "type": "object",
                    "properties": {
                        "code_execution": {"const": True},
                    },
                    "additionalProperties": False,
                },
                "llm": {
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "enum": ["openai", "anthropic", "deepseek", "gemini", "grok", "azure_openai"],
                        },
                        "features": {
                            "type": "object",
                            "properties": {
                                "code_execution": {"const": True},
                            },
                            "additionalProperties": False,
                        },
                        "model": {"type": "string", "minLength": 2},
                        "temperature": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "max_tokens": {"type": "integer", "minimum": 256, "maximum": 16384},
                    },
                    "additionalProperties": False,
                },
            },
        },
        "destinations": {
            "type": "array",
            "minItems": 1,
            "maxItems": 15,
            "items": {
                "type": "object",
                "required": ["id", "name", "dates", "planning_links"],
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-z0-9_]+$"},
                    "name": {"type": "string", "minLength": 2},
                    "dates": {"type": "string"},
                    "schedule_start_time": {
                        "type": "string",
                        "description": "Optional destination-specific day start time override (e.g., '9:30 AM').",
                    },
                    "daily_activity_hours": {
                        "type": "number",
                        "description": "Optional destination-specific override for daily activity-hour budget.",
                    },
                    "attractions_per_day": {
                        "type": "number",
                        "description": "Optional destination-specific target number of attractions to keep per destination-day when ranking candidates.",
                    },
                    "restaurants_per_day": {
                        "type": "number",
                        "description": "Optional destination-specific target number of dinner_recommendations to keep per destination-day when ranking candidates. Mirrors attractions_per_day.",
                    },
                    "en_route_stops_per_day": {
                        "type": "number",
                        "description": "Optional destination-specific target number of en-route stops to keep per destination-day when trimming candidates. Mirrors attractions_per_day.",
                    },
                    "lodging": {
                        "type": "object",
                        "description": "Optional per-destination lodging anchor used for routing and schedule realism.",
                        "required": ["location"],
                        "properties": {
                            "name": {"type": "string"},
                            "location": {"type": "string", "minLength": 2},
                            "checkin_time": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                    "planning_links": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["label", "url"],
                            "properties": {
                                "label": {"type": "string"},
                                "url": {"type": "string"},
                            },
                        },
                    },
                    "seeds": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 2},
                        "description": "Attraction/hike/experience name hints only — no URLs.",
                    },
                    "en_route_seeds": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 2},
                        "description": "Name hints for en-route-stop discovery on the leg "
                                       "arriving at this destination (i.e. the drive from the "
                                       "previous destination to this one) — not attractions "
                                       "within the destination itself. Structurally identical "
                                       "to `seeds` (plain names only, no URLs); these are "
                                       "threaded into en-route-stop candidate discovery as "
                                       "strong hints, still subject to the same route-proximity "
                                       "and detour-threshold verification as any other "
                                       "en-route-stop candidate.",
                    },
                    "group_with": {
                        "type": "string",
                        "pattern": "^[a-z0-9_]+$",
                        "description": "GH #68 multi-site grouping: id of another destination entry "
                                       "this one shares a lodging base with. Omitted = current "
                                       "behavior, unchanged. See docs/design/"
                                       "multi-site-destination-grouping.md.",
                    },
                    "base_owned_categories": {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(VALID_BASE_OWNED_CATEGORIES)},
                        "description": "GH #68 multi-site grouping: per-entry override of which "
                                       "discovery categories defer to the group base instead of "
                                       "being independently discovered for this entry. Omitted = "
                                       "inherit config.yaml's multi_site_grouping.base_owned_categories "
                                       "default. An explicit empty list opts this entry out of any "
                                       "deferral. Only meaningful when group_with is also set.",
                    },
                },
            },
        },
    },
}


class ManifestParser:
    def __init__(self, config_path: Path | str = "config.yaml") -> None:
        pass

    def parse(self, manifest_path: Path | str) -> dict[str, Any]:
        manifest_path = Path(manifest_path)
        logger.info("Parsing manifest: %s", manifest_path)
        data: dict[str, Any] = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        self._validate_schema(data)
        self._validate_seeds(data)
        self._validate_en_route_seeds(data)
        self._validate_ids_unique(data)
        self._validate_group_with(data)
        logger.info(
            "Manifest valid — %d destination(s): %s",
            len(data["destinations"]),
            ", ".join(d["name"] for d in data["destinations"]),
        )
        return data

    def load(self, manifest_path: Path | str) -> dict[str, Any]:
        """Backward-compatible alias used by CLI/tests."""
        return self.parse(manifest_path)

    def _validate_schema(self, data: dict[str, Any]) -> None:
        jsonschema.validate(instance=data, schema=MANIFEST_SCHEMA)

    def _validate_seeds(self, data: dict[str, Any]) -> None:
        for dest in data.get("destinations", []):
            for seed in dest.get("seeds", []):
                if seed.startswith(("http://", "https://")):
                    raise ValueError(
                        f"Destination '{dest['id']}': seed '{seed}' must be a "
                        "name only — not a URL. The generator discovers all URLs automatically."
                    )

    def _validate_en_route_seeds(self, data: dict[str, Any]) -> None:
        for dest in data.get("destinations", []):
            for seed in dest.get("en_route_seeds", []):
                if seed.startswith(("http://", "https://")):
                    raise ValueError(
                        f"Destination '{dest['id']}': en_route_seed '{seed}' must be a "
                        "name only — not a URL. The generator discovers all URLs automatically."
                    )

    def _validate_ids_unique(self, data: dict[str, Any]) -> None:
        ids = [d["id"] for d in data.get("destinations", [])]
        seen = set()
        for did in ids:
            if did in seen:
                raise ValueError(f"duplicate destination id: '{did}'")
            seen.add(did)

    def _validate_group_with(self, data: dict[str, Any]) -> None:
        """GH #68 multi-site grouping: validate `group_with` references.

        - Must reference an `id` that exists elsewhere in destinations[].
        - A destination cannot reference itself.
        - The referenced destination cannot itself have a `group_with`
          (no chains/cycles — one base, N day-trip entries).
        A grouped entry whose `dates` fall outside its base's date range
        only warns (logged), matching this codebase's existing lenient
        free-text `dates` handling elsewhere — see cultural_events.py's
        own best-effort date-range parsing for precedent.
        """
        destinations = data.get("destinations", [])
        by_id = {d["id"]: d for d in destinations if isinstance(d, dict) and "id" in d}
        for dest in destinations:
            if not isinstance(dest, dict):
                continue
            group_with = str(dest.get("group_with", "") or "").strip()
            if not group_with:
                continue
            dest_id = dest.get("id")
            if group_with == dest_id:
                raise ValueError(
                    f"Destination '{dest_id}': group_with cannot reference itself."
                )
            base = by_id.get(group_with)
            if base is None:
                raise ValueError(
                    f"Destination '{dest_id}': group_with '{group_with}' does not match "
                    "any destination id."
                )
            base_group_with = str(base.get("group_with", "") or "").strip()
            if base_group_with:
                raise ValueError(
                    f"Destination '{dest_id}': group_with target '{group_with}' is itself "
                    f"grouped (group_with: '{base_group_with}') — chained/nested grouping "
                    "is not supported. Point every grouped entry directly at one ungrouped "
                    "base destination."
                )
            self._warn_if_group_dates_outside_base_range(dest, base)

    @staticmethod
    def _parse_lenient_date_range(dates: str) -> tuple[datetime, datetime] | None:
        """Best-effort free-text date-range parse. Returns None (not an
        error) on anything it can't confidently parse — this is only used
        for an advisory warning, never a hard validation failure."""
        if not dates:
            return None
        normalized = str(dates).replace("–", "-").replace("—", "-")

        m = re.search(
            r"([A-Za-z]+)\s+(\d{1,2})(?:\s*-\s*(?:[A-Za-z]+\s+)?(\d{1,2}))?,?\s*(\d{4})",
            normalized,
        )
        if m:
            month_name, day_start, day_end, year = m.group(1), m.group(2), m.group(3), m.group(4)
            try:
                start = datetime.strptime(f"{month_name} {int(day_start)} {year}", "%B %d %Y")
                end = datetime.strptime(f"{month_name} {int(day_end or day_start)} {year}", "%B %d %Y")
            except ValueError:
                return None
            if end < start:
                return None
            return start, end

        iso = re.findall(r"(\d{4}-\d{2}-\d{2})", normalized)
        if len(iso) >= 2:
            try:
                start = datetime.strptime(iso[0], "%Y-%m-%d")
                end = datetime.strptime(iso[1], "%Y-%m-%d")
            except ValueError:
                return None
            if end < start:
                start, end = end, start
            return start, end
        if len(iso) == 1:
            try:
                start = datetime.strptime(iso[0], "%Y-%m-%d")
            except ValueError:
                return None
            return start, start

        return None

    def _warn_if_group_dates_outside_base_range(self, dest: dict[str, Any], base: dict[str, Any]) -> None:
        dest_range = self._parse_lenient_date_range(str(dest.get("dates", "") or ""))
        base_range = self._parse_lenient_date_range(str(base.get("dates", "") or ""))
        if not dest_range or not base_range:
            return
        dest_start, dest_end = dest_range
        base_start, base_end = base_range
        if dest_start < base_start or dest_end > base_end:
            logger.warning(
                "Destination '%s' dates ('%s') fall outside group base '%s' dates ('%s') — "
                "group_with entries are expected to be day trips within the base's stay.",
                dest.get("id"), dest.get("dates"), base.get("id"), base.get("dates"),
            )
