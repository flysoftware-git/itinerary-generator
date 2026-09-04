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

from generator.environments import ENVIRONMENTS
from generator.multi_site_grouping import VALID_BASE_OWNED_CATEGORIES

logger = logging.getLogger(__name__)

#: One booked travel leg. Shared verbatim by the per-destination
#: `transportation` list and the trip-level one, so the two can never
#: drift into accepting different fields.
TRANSPORTATION_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["type"],
    "properties": {
        "type": {
            "type": "string",
            "enum": ["plane", "train", "car", "ship", "ferry", "bus", "shuttle", "other"],
            "description": "Drives the category title and icon on the "
                           "rendered card. `ship` covers a cruise or "
                           "repositioning sailing that carries the traveler "
                           "between stops; `ferry` a shorter crossing. Both "
                           "are BOOKED legs like any other -- the traveler "
                           "holds a confirmation -- and are unrelated to the "
                           "transit-routing design in "
                           "docs/design/multimodal-routing.md, which concerns "
                           "services nobody has bought yet. `other` remains "
                           "the fallback so an unrecognized booking still "
                           "renders with its details intact rather than being "
                           "dropped.",
        },
        "provider": {
            "type": "string",
            "description": "Airline, rail operator or rental company.",
        },
        "label": {
            "type": "string",
            "description": "Short human-readable identifier for the leg "
                           "(e.g. 'UA 1234 SFO→LAS', 'Midsize SUV'). "
                           "Falls back to provider, then to the type's "
                           "own title, when omitted.",
        },
        "confirmation_number": {"type": "string"},
        "depart": {
            "type": "string",
            "description": "Free-text departure point and/or time, kept "
                           "as a string for the same reason `dates` is: "
                           "these are display strings, not scheduling "
                           "inputs. Nothing in the pipeline parses them.",
        },
        "arrive": {
            "type": "string",
            "description": "Free-text arrival point and/or time. See "
                           "`depart`.",
        },
        "website": {
            "type": "string",
            "description": "Carrier/rental manage-booking or info URL.",
        },
    },
    "additionalProperties": False,
}


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
                "brand": {
                    "description": (
                        "Optional distributor credit and support routing for the generated "
                        "page footer (§8.3). Absent -- the ordinary case -- the footer renders "
                        "exactly as it always has, crediting this project and pointing at its "
                        "issue tracker. Provenance (§8.2) is not configurable and is not here."
                    ),
                    "type": "object",
                    "properties": {
                        "distributor": {"type": "string", "minLength": 1, "maxLength": 120},
                        "distributor_url": {"type": "string", "pattern": "^https://"},
                        # https or mailto only. A generated guide is a published
                        # artifact that outlives the run: `http://` is a downgrade
                        # someone else can read, and `javascript:` is script
                        # injection through a YAML file.
                        "support_url": {"type": "string", "pattern": "^(https://|mailto:)"},
                        "support_label": {"type": "string", "minLength": 1, "maxLength": 80},
                    },
                    "additionalProperties": False,
                },
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
                "transportation": {
                    "type": "array",
                    "description": "Optional TRIP-WIDE booked travel legs -- the flight in, "
                                   "the flight home, a rental car held for the whole trip. "
                                   "These bracket the itinerary rather than belonging to any "
                                   "one stop: an inbound flight lands at trip.departure and a "
                                   "rental collected there spans every destination, so filing "
                                   "them under the first or last stop misrepresents them. "
                                   "Rendered under the route overview map. A leg tied to a "
                                   "specific locale mid-trip belongs in that destination's own "
                                   "`transportation` list instead. Same item shape, and cleared "
                                   "in privacy-redacted builds exactly the same way.",
                    "items": TRANSPORTATION_ITEM_SCHEMA,
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
                    "description": "Optional default target number of en-route stops for the arrival leg. NOT scaled by day count, unlike attractions/restaurants/scenic drives: an en-route stop belongs to the single drive INTO a destination, which happens once however long the stay. This description previously said the opposite; the code has been a flat cap since _resolve_enroute_target was written, and day-scaling here let a 3-day stay carry 12 candidates for one drive -- past Google's 8-waypoint URL cap, so stops beyond the 8th rendered as cards with no map pin.",
                },
                "scenic_drives_per_day": {
                    "type": "number",
                    "description": "Optional default target number of scenic drives to keep per destination-day when trimming candidates. Mirrors attractions_per_day, but defaults to 2/day (half the others' 4/day) -- scenic drives are typically fewer/bigger commitments, and every one costs an individual live search call (no direct-batch harvest fallback exists for this category).",
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
                "max_hike_miles": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "Optional traveler walking limit, in miles on foot for a "
                                   "single outing. When set, attractions reporting a longer "
                                   "`distance_miles` are excluded from the generated output. "
                                   "Omitted = current behavior, unchanged: this is an opt-in "
                                   "filter, never a new default restriction, and it mirrors "
                                   "has_high_clearance_vehicle in that respect.\n\n"
                                   "An attraction that reports NO distance is never excluded. "
                                   "The figure is produced by the model rather than measured, "
                                   "so a missing one means 'not known' and filtering on it "
                                   "would drop real options for lack of an estimate. Absence "
                                   "and zero are different things and only one of them is a "
                                   "number.",
                },
                "max_hike_elevation_gain_ft": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "Optional traveler climbing limit, in feet of ascent for a "
                                   "single outing. Works exactly as max_hike_miles: opt-in, "
                                   "and an attraction reporting no elevation gain is never "
                                   "excluded.",
                },
                "llm_provider": {
                    "type": "string",
                    "enum": ["openai", "anthropic", "deepseek", "gemini", "grok", "azure_openai"],
                },
                "environment": {
                    "type": "string",
                    # One list, named in generator/environments.py. The same
                    # setting is reachable from a manifest, a flag and an env
                    # var, and a value one of them rejects is not one another
                    # should accept. `test` was the previous name for `eval`.
                    "enum": list(ENVIRONMENTS),
                    "description": "Optional environment tag for hybrid selection. "
                                   "Priority: CLI > manifest > ENVIRONMENT env var. "
                                   "Does not affect config.yaml unless user chooses "
                                   "to implement environment-specific configs later. "
                                   "Renamed from `test` to `eval`; a manifest still "
                                   "carrying `test` needs the one-word change."
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
                    "scenic_drives_per_day": {
                        "type": "number",
                        "description": "Optional destination-specific target number of scenic drives to keep per destination-day when trimming candidates. Mirrors attractions_per_day, but defaults to 2/day. See trip-level scenic_drives_per_day.",
                    },
                    "lodging": {
                        "type": "object",
                        "description": "Optional per-destination lodging anchor used for routing and schedule realism.",
                        "required": ["location"],
                        "properties": {
                            "name": {"type": "string"},
                            "location": {"type": "string", "minLength": 2},
                            "checkin_time": {"type": "string"},
                            "confirmation_number": {
                                "type": "string",
                                "description": "Optional booking/confirmation code for the stay. "
                                               "Redacted in privacy-redacted builds "
                                               "(main._apply_privacy_redaction) -- on most "
                                               "booking sites a confirmation code plus a surname "
                                               "is enough to view, change or cancel the "
                                               "reservation, so this is the most sensitive field "
                                               "in the lodging block, not merely an identifying "
                                               "one.",
                            },
                            "website": {
                                "type": "string",
                                "description": "Optional public URL for the lodging property "
                                               "(the hotel's own site, not a reservation or "
                                               "account link). Rendered as a header pill so the "
                                               "traveler can reach property details -- address, "
                                               "amenities, check-in policy -- without logging in "
                                               "anywhere. Redacted in privacy-redacted builds "
                                               "alongside lodging.name (main._apply_privacy_redaction): "
                                               "the URL identifies the property just as precisely "
                                               "as the name, so exempting it would leak the same "
                                               "where-the-traveler-sleeps-on-which-dates fact that "
                                               "redacting the name exists to protect.",
                            },
                        },
                        "additionalProperties": False,
                    },
                    "transportation": {
                        "type": "array",
                        "description": "Optional booked travel legs ARRIVING at this "
                                       "destination (the flight/train/rental that gets the "
                                       "traveler here), mirroring how en_route_seeds attaches "
                                       "the inbound drive to the arriving destination rather "
                                       "than the departing one. A rental car held across "
                                       "several stops belongs on the destination where it is "
                                       "picked up. Every field is cleared in privacy-redacted "
                                       "builds (main._apply_privacy_redaction) -- a carrier plus "
                                       "a record locator is typically enough to view or change "
                                       "someone else's booking.",
                        "items": TRANSPORTATION_ITEM_SCHEMA,
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
                    "en_route_exclude": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 2},
                        "description": "Name hints for en-route stops to NEVER include on the "
                                       "leg arriving at this destination, even if the AI/"
                                       "discovery pipeline proposes and would otherwise verify "
                                       "one (e.g. a real, live-verified geocode that turns out "
                                       "to be the wrong same-named place — no automated check "
                                       "can reliably catch this class of mismatch, see "
                                       "docs/design/url-discovery-and-audit.md's En-Route Stop "
                                       "Geocode sections). Matched the same way as `seeds`/"
                                       "`en_route_seeds` (case-insensitive, punctuation-"
                                       "normalized) and applied before URL discovery runs, so "
                                       "an excluded name also never costs a search call. "
                                       "Structurally identical to `en_route_seeds` (plain names "
                                       "only, no URLs) but with the opposite effect.",
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
        try:
            data: dict[str, Any] = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(self._format_yaml_error(manifest_path, exc)) from exc
        self._validate_schema(data)
        self._merge_reservations_sidecar(data, manifest_path)
        self._validate_seeds(data)
        self._validate_en_route_seeds(data)
        self._validate_en_route_exclude(data)
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

    @staticmethod
    def reservations_sidecar_path(manifest_path: Path | str) -> Path:
        """Sidecar location for a manifest: `<stem>.reservations.yaml` beside it.

        Named off the manifest stem rather than a fixed `reservations.yaml` so
        several manifests can share a directory (sw_manifest.yaml and
        Japan_manifest.yaml both live in Sandbox/) without one trip's bookings
        bleeding into another's.
        """
        manifest_path = Path(manifest_path)
        return manifest_path.with_name(f"{manifest_path.stem}.reservations.yaml")

    def _merge_reservations_sidecar(self, data: dict[str, Any], manifest_path: Path) -> None:
        """Fold ingested reservations into the parsed manifest, if any exist.

        Runs after schema validation of the hand-authored manifest and
        re-validates afterwards, so LLM-extracted email content cannot enter
        the pipeline in a shape the schema forbids -- it fails the build
        loudly instead. Ingested values fill empty fields only; anything the
        manifest states wins, because a human wrote that and a model guessed
        this. Absence of a sidecar is the normal case and is silent.
        """
        sidecar_path = self.reservations_sidecar_path(manifest_path)
        if not sidecar_path.exists():
            return

        from generator.reservation_ingest import load_sidecar, merge_sidecar_into_trip

        sidecar = load_sidecar(sidecar_path)
        if not sidecar:
            return

        counts = merge_sidecar_into_trip(data, sidecar)
        self._validate_schema(data)

        pending = len(sidecar.get("pending", []) or [])
        # Surfaced on the console by main, not only in the log: the runner is
        # expected to run at WARNING, where these counts would otherwise be
        # invisible -- and "no bookings appeared" is exactly the outcome that
        # looks identical to "there were none to apply".
        data.setdefault("_meta", {})["reservations_merged"] = {**counts, "pending": pending}
        logger.info(
            "Merged reservations from %s -- %d lodging field(s), "
            "%d destination transportation leg(s), %d trip-wide leg(s)%s",
            sidecar_path.name,
            counts["lodging_fields"],
            counts["transportation_legs"],
            counts["trip_legs"],
            f"; {pending} awaiting review" if pending else "",
        )
        if pending:
            logger.warning(
                "%d ingested reservation(s) in %s matched no destination confidently "
                "and were NOT applied. Resolve them under 'pending:' in that file.",
                pending,
                sidecar_path.name,
            )

    def _validate_schema(self, data: dict[str, Any]) -> None:
        try:
            jsonschema.validate(instance=data, schema=MANIFEST_SCHEMA)
        except jsonschema.exceptions.ValidationError as exc:
            raise ValueError(self._format_schema_error(data, exc)) from exc

    @staticmethod
    def _format_yaml_error(manifest_path: Path, exc: "yaml.YAMLError") -> str:
        # PyYAML's MarkedYAMLError (the common case -- syntax errors) carries
        # .problem_mark with a real line/column; a bare YAMLError (rarer --
        # e.g. a duplicate-key or scanner-level failure without a mark) does
        # not, so fall back to str(exc) rather than assume the attribute
        # exists. Deliberately drops PyYAML's own multi-paragraph message
        # (which repeats the same line/column info twice, once for the error
        # itself and once for its "context") down to one clean line -- the
        # full traceback a user previously saw here bottomed out at internal
        # PyYAML frames (composer.py, parser.py, ...) with no indication
        # which manifest line to look at without reading the exception text
        # closely.
        mark = getattr(exc, "problem_mark", None)
        problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        if mark is not None:
            return (
                f"YAML syntax error in {manifest_path}, line {mark.line + 1}, "
                f"column {mark.column + 1}: {problem}"
            )
        return f"YAML syntax error in {manifest_path}: {problem}"

    @staticmethod
    def _format_schema_error(data: dict[str, Any], exc: jsonschema.exceptions.ValidationError) -> str:
        # jsonschema.ValidationError's default str() embeds the entire
        # sub-schema it was validating against (sometimes hundreds of lines,
        # every property's description text included) as context for
        # library authors -- exc.message is the same short, human-readable
        # sentence ("'planning_links' is a required property") without that
        # dump. exc.absolute_path is a deque of keys/indices from the
        # document root to the failing node; walking it against the actual
        # manifest data (not just printing raw indices) lets the message
        # name the destination by its own id/name when the failure is
        # inside a destinations[] entry, which is the overwhelmingly common
        # case and the one a bare "destinations[6]" index doesn't help a
        # human locate quickly in a 50-destination manifest.
        path = list(exc.absolute_path)
        location = "manifest"
        if len(path) >= 2 and path[0] == "destinations" and isinstance(path[1], int):
            destinations = data.get("destinations", []) if isinstance(data, dict) else []
            idx = path[1]
            dest = destinations[idx] if isinstance(destinations, list) and idx < len(destinations) else {}
            dest_id = dest.get("id") if isinstance(dest, dict) else None
            dest_name = dest.get("name") if isinstance(dest, dict) else None
            label = dest_id or dest_name or f"index {idx}"
            location = f"destinations[{idx}] ('{label}')"
            remaining = path[2:]
            if remaining:
                location += " → " + ".".join(str(p) for p in remaining)
        elif path:
            location = ".".join(str(p) for p in path)
        return f"Manifest validation failed at {location}: {exc.message}"

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

    def _validate_en_route_exclude(self, data: dict[str, Any]) -> None:
        for dest in data.get("destinations", []):
            for name in dest.get("en_route_exclude", []):
                if name.startswith(("http://", "https://")):
                    raise ValueError(
                        f"Destination '{dest['id']}': en_route_exclude entry '{name}' must be a "
                        "name only — not a URL."
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
