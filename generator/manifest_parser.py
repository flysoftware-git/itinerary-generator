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
        "depart_time": {
            "type": "string",
            "description": "Clock time the leg leaves, LOCAL TO THE PLACE IT "
                           "LEAVES FROM, exactly as the booking states it "
                           "('17:00', '5:00 PM'). Separate from `depart` "
                           "because that field is a display string a reader "
                           "may have put a date in, and because a time is the "
                           "one thing here that is meaningless without knowing "
                           "which clock it is on. NOTHING NORMALIZES THIS TO A "
                           "SINGLE ZONE: a sailing that leaves Venice at 17:00 "
                           "and reaches Kotor at 08:00 is stating two "
                           "different clocks, and rewriting either into the "
                           "other's zone -- or into UTC -- produces a time no "
                           "document anywhere says, which is a wrong answer "
                           "that looks precise. Absent when the booking does "
                           "not state one; never inferred from a date.",
        },
        "arrive_time": {
            "type": "string",
            "description": "Clock time the leg arrives, local to the place it "
                           "arrives at. See `depart_time`.",
        },
        "total_cost": {
            "type": "string",
            "description": "What the booking says it costs, as digits: "
                           "'4310.00'. A string like `dates` and `depart`, "
                           "for the same reason -- it is transcribed from a "
                           "document, not computed -- so no consumer should "
                           "do arithmetic on it without first agreeing with "
                           "`currency`. Absent when the confirmation states no "
                           "total; never summed out of parts and never "
                           "converted. Cleared in privacy-redacted builds "
                           "along with the rest of the leg "
                           "(main._apply_privacy_redaction drops booked legs "
                           "wholesale), which is what a fare wants: it is a "
                           "fact about the traveler's finances, not about the "
                           "trip.",
        },
        "currency": {
            "type": "string",
            "description": "ISO 4217 code the `total_cost` is denominated in "
                           "('EUR', 'GBP', 'USD'). Required reading for anyone "
                           "who adds `total_cost` to anything: a fare booked "
                           "in Euros folded into a dollar total is wrong by "
                           "whatever the rate happens to be, and looks "
                           "complete. Left as whatever the document said when "
                           "it cannot be resolved to a code -- '$' names four "
                           "currencies and resolving it would be a guess -- so "
                           "a consumer must treat anything that is not a "
                           "three-letter code as unknown rather than as its "
                           "own.",
        },
        "website": {
            "type": "string",
            "description": "Carrier/rental manage-booking or info URL.",
        },
        "stops": {
            "type": "array",
            "description": "Where a multi-stop booked leg calls on the way, in "
                           "the order the traveler reaches them. A cruise or a "
                           "multi-city rail fare is not one place with two "
                           "dates: it is an itinerary, and every call is "
                           "somewhere the traveler will actually be. Without "
                           "this the booking says only where it starts and "
                           "ends, so the places in between cannot be written "
                           "about, linked, or planned around -- which is most "
                           "of the trip on a sailing. Empty or absent for the "
                           "ordinary single-hop leg, which is unchanged.",
            "items": {
                "type": "object",
                "required": ["place"],
                "properties": {
                    "place": {
                        "type": "string",
                        "minLength": 1,
                        "description": "The port, station or city called at, "
                                       "named the way a destination is so it "
                                       "can be matched to one.",
                    },
                    "date": {
                        "type": "string",
                        "description": "The day of the call, ISO 8601 where "
                                       "the booking states it. Free-text like "
                                       "`depart` and `dates`: nothing in the "
                                       "pipeline schedules from it.",
                    },
                    "arrive_time": {
                        "type": "string",
                        "description": "Clock time the leg reaches this call, "
                                       "LOCAL TO THIS PLACE. A cruise "
                                       "itinerary states one per port and they "
                                       "are on as many clocks as there are "
                                       "countries; storing them as if they "
                                       "shared a zone would be wrong in a way "
                                       "nothing downstream could detect, so "
                                       "each is kept exactly as its own "
                                       "document states it. Absent where the "
                                       "booking does not say -- a schedule "
                                       "naming times at some calls and not "
                                       "others is ordinary, and filling the "
                                       "rest in would be inventing a schedule.",
                    },
                    "depart_time": {
                        "type": "string",
                        "description": "Clock time the leg leaves this call, "
                                       "local to this place. With `arrive_time` "
                                       "it is how long the traveler has ashore, "
                                       "which is the question a port call "
                                       "exists to answer. See `arrive_time`.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


#: How the traveler covers the legs BETWEEN destinations. `auto` is today's
#: behaviour, unchanged: every leg is a drive. Kept as an explicit enum member
#: rather than only "omitted", so a manifest can state the assumption it is
#: relying on. See docs/design/multimodal-routing.md 3.1.
TRANSPORT_MODES: tuple[str, ...] = ("auto", "transit", "mixed", "bike", "hike")

#: A point an author states rather than one the build looks up. Defined once so
#: `coordinates` means the same thing wherever it appears: on a destination, and
#: on a side trip's two ends.
COORDINATES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lat", "lng"],
    "properties": {
        "lat": {"type": "number", "minimum": -90, "maximum": 90},
        "lng": {"type": "number", "minimum": -180, "maximum": 180},
    },
}

#: One end of a side trip: somewhere the outing starts or finishes that is not
#: the lodging base it is grouped with.
SIDE_TRIP_END_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "coordinates": COORDINATES_SCHEMA,
    },
}

#: One authored leg between two adjacent destinations. `from`/`to` are
#: destination `id`s, never display names -- the issue's free-text matching
#: fails silently, and a silently-ignored leg ships a traveler an itinerary
#: telling them to drive a leg they have no car for (multimodal-routing.md 3.2).
LEG_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["from", "to", "mode"],
    "properties": {
        "from": {
            "type": "string",
            "pattern": "^[a-z0-9_]+$",
            "description": "Destination `id` this leg departs from. An id, not a "
                           "name: 'Zion NP' against a manifest saying 'Zion "
                           "National Park' would match nothing and the leg would "
                           "quietly stay `auto`.",
        },
        "to": {
            "type": "string",
            "pattern": "^[a-z0-9_]+$",
            "description": "Destination `id` this leg arrives at. Must be the "
                           "next destination after `from` in itinerary order.",
        },
        "mode": {
            "type": "string",
            "enum": list(TRANSPORT_MODES),
            "description": "Travel mode for this leg. Same values as "
                           "`transport_mode`; naming the same leg in both places "
                           "is allowed only when they agree.",
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
                        # The icon an installed guide shows on a home screen.
                        # An `.svg` beside the manifest, or a
                        # `data:image/svg+xml` URI. Absent -- the ordinary
                        # case -- the guide installs with the map icon in the
                        # trip's theme colour, exactly as before. Checked
                        # properly in `_resolve_brand_icon`, which can say
                        # which file and why; a regex here would only be able
                        # to say the manifest is invalid.
                        "icon": {"type": "string", "minLength": 1},
                        # iOS has never supported SVG in `apple-touch-icon`, so
                        # `icon` above reaches every platform except the one
                        # where a home-screen icon matters most. This is the
                        # same artwork as a 180x180 PNG, used there and nowhere
                        # else. Optional: without it an iPhone shows what it has
                        # always shown, a screenshot of the page.
                        "icon_png": {"type": "string", "minLength": 1},
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
                "access_notes": {
                    "description": (
                        "Optional. When true, every destination's content is asked to say "
                        "what is known about REACHING each place named -- how far it is on "
                        "foot from parking or the nearest transit stop when that is the only "
                        "way in, the surface and gradient of that approach, and whether step-"
                        "free entry, accessible parking or accessible facilities are "
                        "documented. Absent (the default) nothing changes and no access "
                        "sentence is added. "
                        "The point of the flag is the honesty rule it carries: the model is "
                        "told to say plainly when access is NOT documented rather than to "
                        "infer it. A guide that guesses 'wheelchair accessible' from a place "
                        "name is worse than one that says nothing, because a traveller can "
                        "plan around 'unknown' and cannot plan around a wrong yes. Nothing "
                        "here is a substitute for the venue's own information, and the "
                        "generated text says so."
                    ),
                    # A string says WHICH access question to answer, and is the
                    # reason this is not only a flag. `true` asks the general
                    # question above, which is the right thing when all that is
                    # known is that access matters. But *step-free entry* and *a
                    # bench every two hundred metres* are different needs, and a
                    # guide that answers the general question answers neither of
                    # them: it reports what a venue documents rather than what
                    # this reader has to know before setting out.
                    #
                    # The honesty rule is unchanged and applies to a string
                    # exactly as it does to the flag -- a specific question makes
                    # a confident wrong yes MORE likely, not less, because the
                    # model has been handed the answer somebody wants to hear.
                    #
                    # A string is a *requirement*, never a person's medical
                    # circumstances. It reaches a content-generation prompt and,
                    # through it, a published page; "step-free entry to every
                    # indoor stop" belongs there and why the reader needs it does
                    # not. The generator cannot enforce that -- it is the
                    # caller's to respect -- so it is said here, where whoever
                    # writes the manifest is reading.
                    "oneOf": [
                        {"type": "boolean"},
                        {"type": "string", "minLength": 3, "maxLength": 300},
                    ],
                },
                "transport_mode": {
                    "type": "string",
                    "enum": list(TRANSPORT_MODES),
                    "description": "Optional trip-wide travel assumption for "
                                   "inter-destination legs. 'auto' (the default when "
                                   "omitted) is current behaviour, unchanged: every leg "
                                   "is a drive. 'transit' asks for scheduled public "
                                   "transport instead, including the arrival-day "
                                   "schedule. 'mixed' renders transit options alongside "
                                   "the drive rather than in place of it. 'bike' and "
                                   "'hike' are SELF-POWERED: nobody operates them, so "
                                   "there is no timetable to suggest and no options card "
                                   "-- they change the leg's duration, its map link and "
                                   "how it is described. Overridable per destination.",
                },
                "trail_name": {
                    "type": "string",
                    "description": "Optional named trail this trip follows on foot or "
                                   "by bike, e.g. 'Pacific Crest Trail'. Used to look up "
                                   "the AllTrails page for each leg's section, so it "
                                   "only has an effect on legs whose transport_mode is "
                                   "bike or hike. The generator never invents this: "
                                   "without it, a leg gets no trail link rather than a "
                                   "guessed one.",
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
                "vehicle_range_miles": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "Optional. How far the vehicle this trip is driven in "
                                   "goes on a tank (or a charge), in miles. The en-route "
                                   "stop then fires on whichever limit a leg reaches "
                                   "first -- continuous driving time or the tank -- and "
                                   "on a leg longer than the tank the arrival note says "
                                   "to stop for fuel, at a verified en-route stop within "
                                   "reach. The range is converted at each leg's own "
                                   "speed, so it bites sooner on an interstate than on a "
                                   "mountain road.\n\n"
                                   "Trip-level because range is a fact about the vehicle, "
                                   "and one vehicle normally drives the whole trip; it "
                                   "sits beside has_high_clearance_vehicle for the same "
                                   "reason. When set it takes precedence over "
                                   "config.yaml's en_route_stops.vehicle_range_miles, "
                                   "which applies to every trip run from that config. "
                                   "Omitted = current behaviour, unchanged: with neither "
                                   "set, no fuel guidance is given, because a range "
                                   "nobody stated would put a fuel stop on a leg that "
                                   "never needed one. Zero or less is refused rather than "
                                   "read as 'unknown' -- a tank of no miles is a mistake, "
                                   "not a vehicle. Like every en-route suggestion it names "
                                   "only stops the pipeline has verified, so it is silent "
                                   "when en_route_stops is disabled.",
                },
                "llm_provider": {
                    "type": "string",
                    "enum": ["openai", "anthropic", "deepseek", "gemini", "grok", "azure_openai"],
                },
                "llm_model": {
                    "type": "string",
                    "minLength": 2,
                    "description": "Optional flat form of `llm.model`, symmetric with "
                                   "`llm_provider`. Honoured by main._resolve_llm_overrides "
                                   "but absent from this schema until now, so a "
                                   "misspelling of the field the trip's whole content is "
                                   "written by validated clean. Beaten by --llm-model.",
                },
                "short_name": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Optional home-screen icon label for the installed PWA. "
                                   "iOS truncates around 12 characters, so a long `title` "
                                   "renders clipped; omitted, the manifest and HTML fall back "
                                   "to `title` (trimmed to 24 characters).",
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
        "categories": {
            "type": "object",
            "description": "Optional per-trip answers for the four priced discovery "
                           "categories, so a trip that wants trails does not buy them "
                           "for every other trip by flipping a config.yaml switch. "
                           "Read by main._manifest_category_override, which also accepts "
                           "this block nested under `trip`, and beaten by the paired CLI "
                           "flags (--trails/--no-trails and friends). Each entry is a "
                           "boolean, or an object carrying `enabled`.",
            "properties": {
                key: {
                    "anyOf": [
                        {"type": "boolean"},
                        {
                            "type": "object",
                            "properties": {"enabled": {"type": "boolean"}},
                        },
                    ],
                }
                for key in ("trails", "cultural_events", "en_route_stops", "restaurants")
            },
        },
        "legs": {
            "type": "array",
            "description": "Optional per-leg travel modes, addressed by destination "
                           "id. An alternative to per-destination `transport_mode` for "
                           "authors who think in journeys rather than in stops; both "
                           "may be used, but not to disagree about one leg. See "
                           "docs/design/multimodal-routing.md 3.2.",
            "items": LEG_ITEM_SCHEMA,
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
                            "locality": {
                                "type": "object",
                                "description": "Optional: the town, region and country the "
                                               "stay is in, each as its own part -- the place, "
                                               "as distinct from `location`, which reservation "
                                               "ingestion fills with the street address exactly "
                                               "as the confirmation prints it. Written by "
                                               "ingestion so a consumer asking 'which town' "
                                               "never has to parse an address. NOT redacted in "
                                               "privacy-redacted builds: a town is what "
                                               "destination.name already publishes, and it is "
                                               "the property name that identifies a stay.",
                                "properties": {
                                    "city": {"type": "string"},
                                    "region": {"type": "string"},
                                    "country": {"type": "string"},
                                },
                                "additionalProperties": False,
                            },
                            "dates": {
                                "type": "string",
                                "description": "Optional free text for when the stay itself "
                                               "is booked (\"October 17-19, 2026\"), in the same "
                                               "shape as destination.dates. Distinct from the "
                                               "destination's dates, which are the whole stop: a "
                                               "property held for two nights of a four-night stop "
                                               "states something the stop does not. Written by "
                                               "reservation ingestion, which reads it off the "
                                               "confirmation; a manifest may state it directly. "
                                               "NOT redacted in privacy-redacted builds, for the "
                                               "same reason as lodging.location and checkin_time "
                                               "(main._apply_privacy_redaction) -- the days a "
                                               "traveler is at a stop are already published as "
                                               "destination.dates, so this discloses nothing the "
                                               "guide does not, and it is the property NAME that "
                                               "turns a date into where-they-sleep.",
                            },
                            "checkin_time": {"type": "string"},
                            "total_cost": {
                                "type": "string",
                                "description": "Optional: what the confirmation says the stay "
                                               "costs, as digits ('412.00'). The same field and "
                                               "the same rules as a transportation leg's "
                                               "total_cost -- transcribed, never summed out of "
                                               "per-night amounts, never converted, and "
                                               "meaningless to add to anything without "
                                               "`currency`. Absent when the confirmation states "
                                               "no total. Cleared in privacy-redacted builds "
                                               "(main._apply_privacy_redaction).",
                            },
                            "currency": {
                                "type": "string",
                                "description": "ISO 4217 code `total_cost` is in ('USD', 'EUR'), "
                                               "or whatever the document said when that cannot "
                                               "be resolved to a code. Present only beside a "
                                               "total_cost.",
                            },
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
                        "items": {
                            "oneOf": [
                                {"type": "string", "minLength": 2},
                                {
                                    "type": "object",
                                    "required": ["name"],
                                    "additionalProperties": False,
                                    "properties": {
                                        "name": {"type": "string", "minLength": 2},
                                        "url": {"type": "string", "minLength": 4},
                                    },
                                },
                            ],
                        },
                        "description": "Attraction/hike/experience hints. Either a bare "
                                       "name, or an object naming the page that hint "
                                       "means: {name, url}. A bare name is still a name "
                                       "only and may not be a URL — the object form is "
                                       "how an author says WHICH page, when discovery "
                                       "cannot be expected to guess it.",
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
                    "transport_mode": {
                        "type": "string",
                        "enum": list(TRANSPORT_MODES),
                        "description": "Optional override for the leg ARRIVING at this "
                                       "destination -- the journey from the previous stop "
                                       "to this one. Attaches to the arriving destination "
                                       "for the same reason en_route_seeds and "
                                       "transportation do: the inbound leg belongs to the "
                                       "place it delivers you to. Meaningless on a "
                                       "group_with entry, which is a there-and-back day "
                                       "trip rather than a relocation; set there it is "
                                       "warned about and ignored.",
                    },
                    "trail_section": {
                        "type": "string",
                        "description": "Optional name of the trail section covering the leg "
                                       "ARRIVING at this destination, written as the trail "
                                       "catalogue names it -- e.g. 'Pacific Crest Trail "
                                       "(PCT): Section B - Callahan's-Ashland to Fish Lake'. "
                                       "Only used on bike/hike legs. Without it the lookup "
                                       "has to compose a section name from the two stop "
                                       "names, which no catalogue page is called, so it "
                                       "matches nothing. Several legs may share one section: "
                                       "guidebook sections are where the road crosses, "
                                       "resupply stops are where you can buy food.",
                    },
                    "trail_url": {
                        "type": "string",
                        "description": "Optional URL for this leg's trail section, supplied "
                                       "by the author. Used verbatim and skips discovery "
                                       "entirely. design.md 1.4 bars the MODEL from "
                                       "producing a URL; a human may, which is the same "
                                       "footing planning_links has always stood on. Worth "
                                       "supplying here because a trail catalogue's own "
                                       "titles and slugs disagree -- AllTrails calls one "
                                       "page 'Section B - Callahan's-Ashland to Fish Lake' "
                                       "and slugs it 'pct-or-section-b-highway-5-to-"
                                       "highway-140-fish-lake' -- so no search phrase "
                                       "reliably resolves it.",
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
                    "coordinates": {
                        **COORDINATES_SCHEMA,
                        "description": "Optional author-supplied position for this "
                                       "destination. Given, it is used instead of "
                                       "geocoding `name` -- and it is the author's "
                                       "statement about which place this is, which a "
                                       "gazetteer cannot always be asked for. A name "
                                       "several places share ('Hollywood Beach' is a "
                                       "park in Port Angeles and a hamlet 190 km away) "
                                       "resolves by importance, confidently, and there "
                                       "is no spelling that makes the question "
                                       "unambiguous. An author who already knows where "
                                       "they mean can now say so. Omitted = geocoded "
                                       "from the name, unchanged.",
                    },
                    "start": {
                        **SIDE_TRIP_END_SCHEMA,
                        "description": "Optional: where this outing BEGINS, when that is "
                                       "not the base it is grouped with. Only meaningful "
                                       "with `group_with`; set elsewhere it is warned "
                                       "about and ignored. A grouped entry has always "
                                       "meant a there-and-back day trip from the base, "
                                       "and that is one shape of outing rather than the "
                                       "only one: a ride along a trail is dropped off at "
                                       "one end and finishes at the other. Omitted = "
                                       "there-and-back from the base, unchanged.",
                    },
                    "end": {
                        **SIDE_TRIP_END_SCHEMA,
                        "description": "Optional: where this outing FINISHES, when that "
                                       "is not the base it is grouped with. Only "
                                       "meaningful with `group_with`; set elsewhere it is "
                                       "warned about and ignored. It is also where the "
                                       "NEXT stop's journey starts from -- a traveller "
                                       "who finished the ride here does not go back to "
                                       "the base first to leave from it. Omitted = the "
                                       "next stop leaves from the base, unchanged.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": list(TRANSPORT_MODES),
                        "description": "Optional: how this OUTING is travelled -- the "
                                       "ride, walk or paddle itself. Only meaningful "
                                       "with `group_with`; set elsewhere it is warned "
                                       "about and ignored. Distinct from "
                                       "`transport_mode`, which describes the "
                                       "relocation leg ARRIVING at a stop and is "
                                       "meaningless on a grouped entry for exactly that "
                                       "reason: a day out has no arriving journey. This "
                                       "is the other half of that sentence -- the day "
                                       "out has a mode of its own, and 'we drove there' "
                                       "is not what a bike ride is. Omitted = the trip's "
                                       "own mode, unchanged.",
                    },
                    "stretch_note": {
                        "type": "string",
                        "pattern": "\\S",
                        "maxLength": 500,
                        "description": "Optional author's note on the leg ARRIVING at this "
                                       "destination, rendered verbatim (escaped) on its "
                                       "getting-here card -- e.g. 'Half traffic-free rail-trail, "
                                       "half on-road connector; the connectors are where the "
                                       "day gets long.' Free text rather than a scale: effort "
                                       "and surface do not reduce to one number (a flat on-road "
                                       "connector is easy and long), and a judgement like that "
                                       "loses most of its content as a level. Any mode. Attaches "
                                       "to the arriving destination for the same reason "
                                       "trail_section and transport_mode do. Omitted = no note, "
                                       "and the card renders exactly as before.",
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
        self._resolve_brand_icon(data, manifest_path)
        self._resolve_brand_touch_icon(data, manifest_path)
        self._validate_seeds(data)
        self._normalize_seeds(data)
        self._validate_en_route_seeds(data)
        self._validate_en_route_exclude(data)
        self._validate_ids_unique(data)
        self._validate_group_with(data)
        self._warn_side_trip_fields_without_group(data)
        self._reject_legacy_transport_mode_key(data)
        self._validate_legs(data)
        self._warn_transport_mode_on_grouped(data)
        logger.info(
            "Manifest valid — %d destination(s): %s",
            len(data["destinations"]),
            ", ".join(d["name"] for d in data["destinations"]),
        )
        return data

    def load(self, manifest_path: Path | str) -> dict[str, Any]:
        """Backward-compatible alias used by CLI/tests."""
        return self.parse(manifest_path)

    #: An icon is inlined into every page of the guide's head, so it is
    #: carried in full by each build. 256 KB is generous for a logo and small
    #: enough that a file picked by mistake -- a photograph, an export nobody
    #: optimised -- is refused while somebody can still see why.
    MAX_ICON_BYTES = 256 * 1024

    def _resolve_brand_icon(self, data: dict[str, Any], manifest_path: Path) -> None:
        """Turn `trip.brand.icon` into a `data:` URI, or refuse it here.

        Here rather than at render time, for the reason the schema is checked
        before anything is generated: an icon that does not exist should cost
        a sentence at parse, not a finished guide with a missing face. And
        here rather than in the schema, because the useful message names the
        file that is missing and the directory it was looked for in.

        **SVG only.** One file answers for the 192 and 512 manifest entries
        and the favicon; a raster image cannot, and an icon declared at a size
        it is not is worse than none (`generator/app_icon.py`).
        """
        from urllib.parse import quote

        trip = data.get("trip") if isinstance(data, dict) else None
        brand = trip.get("brand") if isinstance(trip, dict) and isinstance(trip.get("brand"), dict) else None
        if not brand:
            return
        raw = str(brand.get("icon", "") or "").strip()
        if not raw:
            return

        if raw.startswith("data:"):
            if not raw.startswith("data:image/svg+xml"):
                raise ValueError(
                    "trip.brand.icon: a data: URI must be data:image/svg+xml -- one file has to "
                    "serve the 192 and 512 icons and the favicon, which only a vector can do."
                )
            if len(raw.encode("utf-8")) > self.MAX_ICON_BYTES:
                raise ValueError(
                    f"trip.brand.icon is {len(raw.encode('utf-8')) // 1024} KB; the limit is "
                    f"{self.MAX_ICON_BYTES // 1024} KB. It is inlined into the guide's head."
                )
            self._refuse_scripted_icon(raw)
            return

        if not raw.lower().endswith(".svg"):
            raise ValueError(
                f"trip.brand.icon: {raw!r} -- give an .svg file beside the manifest, or a "
                "data:image/svg+xml URI. One file serves the 192 and 512 icons and the "
                "favicon, so it has to be a vector."
            )

        path = Path(raw)
        if not path.is_absolute():
            path = manifest_path.parent / path
        if not path.is_file():
            raise ValueError(
                f"trip.brand.icon: no file at {path}. A relative path is read from the "
                f"manifest's own directory ({manifest_path.parent})."
            )
        size = path.stat().st_size
        if size > self.MAX_ICON_BYTES:
            raise ValueError(
                f"trip.brand.icon: {path} is {size // 1024} KB; the limit is "
                f"{self.MAX_ICON_BYTES // 1024} KB. It is inlined into the guide's head."
            )
        svg = path.read_text(encoding="utf-8")
        if "<svg" not in svg.lower():
            raise ValueError(f"trip.brand.icon: {path} does not contain an <svg> element.")
        self._refuse_scripted_icon(svg)
        # Percent-encoded rather than base64: it stays readable in the built
        # page, and `#` inside a fill has to be escaped in a data: URI anyway.
        brand["icon"] = "data:image/svg+xml," + quote(svg.strip(), safe="")
        logger.info("Brand icon: %s (%d bytes)", path, size)

    #: What Apple asks for, and what this refuses anything else for. A single
    #: size rather than a set: an iPhone downscales one icon cleanly, and a
    #: manifest that may name any size is a manifest that will name a 1024px
    #: export nobody looks at again.
    TOUCH_ICON_PX = 180

    def _resolve_brand_touch_icon(self, data: dict[str, Any], manifest_path: Path) -> None:
        """Turn `trip.brand.icon_png` into a `data:` URI, or refuse it here.

        **Why a second key at all.** `icon` is an SVG because one vector
        answers for the 192 and 512 manifest entries and the favicon. iOS does
        not take an SVG in `apple-touch-icon` and never has: Safari ignores the
        link and an added-to-home-screen guide gets a screenshot of the page.
        So the one platform where a home-screen icon matters most is the one
        `icon` cannot reach, and the fix is a raster of a known size rather
        than a guess made at render time.

        Nothing is rasterised here. The author exports the PNG; this checks it
        is a PNG, that it is square at `TOUCH_ICON_PX`, and inlines it.
        """
        import base64

        trip = data.get("trip") if isinstance(data, dict) else None
        brand = trip.get("brand") if isinstance(trip, dict) and isinstance(trip.get("brand"), dict) else None
        if not brand:
            return
        raw = str(brand.get("icon_png", "") or "").strip()
        if not raw:
            return

        if raw.startswith("data:"):
            if not raw.startswith("data:image/png;base64,"):
                raise ValueError(
                    "trip.brand.icon_png: a data: URI must be data:image/png;base64 -- iOS takes "
                    "a raster here, which is the whole reason this key is separate from `icon`."
                )
            try:
                blob = base64.b64decode(raw.split(",", 1)[1], validate=True)
            except Exception as exc:  # noqa: BLE001 -- the message has to name the key
                raise ValueError(f"trip.brand.icon_png: the base64 payload could not be read ({exc}).") from exc
            self._check_touch_icon(blob, "trip.brand.icon_png")
            return

        if not raw.lower().endswith(".png"):
            raise ValueError(
                f"trip.brand.icon_png: {raw!r} -- give a .png file beside the manifest, or a "
                "data:image/png;base64 URI. It exists because iOS will not take the SVG."
            )
        path = Path(raw)
        if not path.is_absolute():
            path = manifest_path.parent / path
        if not path.is_file():
            raise ValueError(
                f"trip.brand.icon_png: no file at {path}. A relative path is read from the "
                f"manifest's own directory ({manifest_path.parent})."
            )
        blob = path.read_bytes()
        self._check_touch_icon(blob, f"trip.brand.icon_png ({path})")
        brand["icon_png"] = "data:image/png;base64," + base64.b64encode(blob).decode("ascii")
        logger.info("Brand touch icon: %s (%d bytes)", path, len(blob))

    def _check_touch_icon(self, blob: bytes, what: str) -> None:
        """A PNG, square, at the size iOS is given. Refused here or never.

        The dimensions are in the IHDR chunk, the first thing after the
        signature, so reading them needs no image library -- and a wrong size
        has to fail at parse like every other icon fault, because on the far
        side of this is a phone nobody is holding.
        """
        import struct

        if len(blob) > self.MAX_ICON_BYTES:
            raise ValueError(
                f"{what} is {len(blob) // 1024} KB; the limit is "
                f"{self.MAX_ICON_BYTES // 1024} KB. It is inlined into the guide's head."
            )
        if not blob.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"{what} is not a PNG -- its first bytes are not a PNG signature.")
        if len(blob) < 24 or blob[12:16] != b"IHDR":
            raise ValueError(f"{what} is a PNG with no readable header, so its size cannot be checked.")
        width, height = struct.unpack(">II", blob[16:24])
        if (width, height) != (self.TOUCH_ICON_PX, self.TOUCH_ICON_PX):
            raise ValueError(
                f"{what} is {width}x{height}; it has to be exactly "
                f"{self.TOUCH_ICON_PX}x{self.TOUCH_ICON_PX}. That is what iOS asks for, and a "
                "size declared and not met is the defect this key exists to avoid."
            )

    @staticmethod
    def _refuse_scripted_icon(svg: str) -> None:
        """An icon is artwork, and artwork does not need a script in it.

        A manifest is a file somebody may have been sent. SVG can carry script
        and external references, and this one is inlined into the head of a
        page that is then published; refusing the two obvious carriers costs an
        author nothing and removes the interesting case entirely.
        """
        lowered = svg.lower()
        # A browser runs none of these in an icon slot today. The list is what
        # the reasoning above implies rather than what is exploitable now: the
        # reason given is that the artwork is inlined into a published page,
        # and that reason carries to the day somebody renders the same file in
        # the document body, where every one of these does run.
        for marker in ("<script", "javascript:", "<foreignobject", "onload=", "onerror=",
                       "onclick=", "<use", "@import", "<image"):
            if marker in lowered:
                raise ValueError(
                    f"trip.brand.icon contains {marker!r}. An icon is artwork: script, embedded "
                    "HTML and references to anything outside the file are refused, because it is "
                    "inlined into the published page."
                )

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
        """A bare seed is still a name; an object seed may carry the page it means.

        The bare-string rule is unchanged, including its message: a name is a
        hint for discovery and a URL pasted into that slot was always a mistake.
        What is new is a way to say the other thing -- *this hint means THIS
        page* -- which the schema had no room for, so an author who already knew
        the page had to discard that and hope discovery agreed.
        """
        for dest in data.get("destinations", []):
            for seed in dest.get("seeds", []):
                if isinstance(seed, dict):
                    name = str(seed.get("name", "") or "")
                    url = str(seed.get("url", "") or "").strip()
                    if name.startswith(("http://", "https://")):
                        raise ValueError(
                            f"Destination '{dest['id']}': seed name '{name}' must be a "
                            "name — put the address in this seed's 'url' instead."
                        )
                    if url and not url.startswith(("http://", "https://")):
                        raise ValueError(
                            f"Destination '{dest['id']}': seed '{name}' has url "
                            f"'{url}', which is not an http(s) address."
                        )
                    continue
                if seed.startswith(("http://", "https://")):
                    raise ValueError(
                        f"Destination '{dest['id']}': seed '{seed}' must be a "
                        "name only — not a URL. The generator discovers all URLs "
                        "automatically. To say which page a hint means, give the "
                        "seed as {name: ..., url: ...} instead."
                    )

    def _normalize_seeds(self, data: dict[str, Any]) -> None:
        """Hand every consumer the shape it already understands.

        `seeds` goes back to being a list of plain names, exactly as before this
        change, and any page a seed named is collected into `seed_links` beside
        it. Nothing downstream has to learn the object form, and nothing that
        reads `seeds` today has to change -- which is the whole reason the
        widening is safe to make in one commit.

        `seed_links` maps name -> url and is only present when at least one seed
        carried a page, so its absence means *no seed named one* rather than
        *this manifest predates the field*.

        **Nothing yet PREFERS these links.** URL discovery still chooses an
        attraction's link the way it always has; carrying the author's own
        answer to that question is a separate change, deliberately not made
        here. A field the parser validates and exposes and no caller reads is a
        real cost, and it is paid on purpose: the alternative is one commit that
        widens a schema in shared code AND rewires link selection, which is two
        reviews wearing one hat.
        """
        for dest in data.get("destinations", []):
            seeds = dest.get("seeds")
            if not isinstance(seeds, list) or not seeds:
                continue
            names: list[str] = []
            links: dict[str, str] = {}
            for seed in seeds:
                if isinstance(seed, dict):
                    name = str(seed.get("name", "") or "").strip()
                    url = str(seed.get("url", "") or "").strip()
                    if not name:
                        continue
                    names.append(name)
                    if url:
                        # Two seeds of one name pointing at different pages is a
                        # question the parser cannot answer, and `links[name] =
                        # url` answered it by keeping whichever came last.
                        # Silent, and the losing page is the one the author will
                        # go looking for.
                        existing = links.get(name)
                        if existing and existing != url:
                            raise ValueError(
                                f"Destination '{dest['id']}': seed '{name}' names two "
                                f"different pages ('{existing}' and '{url}'). Give the "
                                "hint once, or name the two places distinctly."
                            )
                        links[name] = url
                    continue
                name = str(seed or "").strip()
                if name:
                    names.append(name)
            dest["seeds"] = names
            if links:
                dest["seed_links"] = links

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

    #: The name GH #2's issue proposed for the per-destination mode, rejected
    #: in favour of plain `transport_mode` (multimodal-routing.md 3.1). It has
    #: to RAISE rather than be quietly accepted as an alias: destination items
    #: allow unknown keys, so a manifest written to the issue's spelling
    #: validates clean, resolves every leg to the trip-wide default, and ships
    #: an itinerary telling the traveler to drive a leg they meant as a train.
    #: That is the same silent-fallback failure the `legs:` id contract exists
    #: to prevent, arriving through a different door. Found 2026-09-02 by the
    #: acceptance manifest itself, which was written to the issue's names.
    _REJECTED_TRANSPORT_MODE_KEYS = ("transport_mode_from_previous", "transport_mode_from")

    def _reject_legacy_transport_mode_key(self, data: dict[str, Any]) -> None:
        for dest in data.get("destinations", []) or []:
            if not isinstance(dest, dict):
                continue
            for key in self._REJECTED_TRANSPORT_MODE_KEYS:
                if key in dest:
                    raise ValueError(
                        f"Destination '{dest.get('id')}': '{key}' is not a manifest key. "
                        f"Use 'transport_mode' -- it already means the leg ARRIVING at "
                        f"this destination, matching en_route_seeds and transportation. "
                        f"Renaming it here is required rather than assumed, because an "
                        f"unrecognised key would otherwise be ignored and the leg would "
                        f"silently stay '"
                        + str((data.get("trip") or {}).get("transport_mode", "auto") or "auto")
                        + "'."
                    )

    def _validate_legs(self, data: dict[str, Any]) -> None:
        """Validate the optional `legs:` list (multimodal-routing.md 3.2).

        Every failure here RAISES. The issue's original design matched
        `from`/`to` as free text against destination names, which meant a
        typo produced no leg, no warning and a build that succeeded --
        discoverable only by remembering you had wanted a train there. A
        build failure costs thirty seconds; a silent fallback ships a
        traveler an itinerary telling them to drive a leg they have no car
        for.

        Naming the same leg in both `legs:` and the arriving destination's
        `transport_mode` is allowed when the two AGREE, and is an error when
        they disagree -- not last-wins, not most-specific-wins. This is the
        drift bug class this project has hit repeatedly: one value restated
        in two places, free to diverge. Silent precedence would mean an
        author edits `transport_mode`, sees no change, and has no way to
        find out why.
        """
        legs = data.get("legs") or []
        if not legs:
            return
        destinations = [d for d in data.get("destinations", []) or [] if isinstance(d, dict)]
        by_id = {d["id"]: d for d in destinations if "id" in d}
        order = [d.get("id") for d in destinations]

        seen: dict[tuple[str, str], int] = {}
        for index, leg in enumerate(legs):
            if not isinstance(leg, dict):
                continue
            origin = str(leg.get("from", "") or "").strip()
            arrival = str(leg.get("to", "") or "").strip()
            where = f"legs[{index}] ('{origin}' -> '{arrival}')"

            for role, value in (("from", origin), ("to", arrival)):
                if value not in by_id:
                    raise ValueError(
                        f"{where}: {role} '{value}' does not match any destination id. "
                        "legs reference destination ids, not names — known ids: "
                        + ", ".join(f"'{i}'" for i in order if i)
                        + "."
                    )
            if origin == arrival:
                raise ValueError(f"{where}: from and to are the same destination — not a leg.")

            if order.index(arrival) - order.index(origin) != 1:
                raise ValueError(
                    f"{where}: '{origin}' and '{arrival}' are not adjacent in itinerary "
                    "order, so the leg between them has no defined meaning. A leg joins a "
                    "destination to the one immediately after it."
                )

            key = (origin, arrival)
            if key in seen:
                raise ValueError(
                    f"{where}: duplicates legs[{seen[key]}] — the same leg is given two "
                    "modes and there is no rule for choosing between them."
                )
            seen[key] = index

            mode = str(leg.get("mode", "") or "").strip()
            dest_mode = str(by_id[arrival].get("transport_mode", "") or "").strip()
            if dest_mode and dest_mode != mode:
                raise ValueError(
                    f"{where}: mode '{mode}' disagrees with destination "
                    f"'{arrival}'s own transport_mode '{dest_mode}'. Both describe the "
                    f"same leg — the one arriving at '{arrival}' — so one of them is a "
                    "mistake. Remove one, or make them agree."
                )

    def _warn_transport_mode_on_grouped(self, data: dict[str, Any]) -> None:
        """`transport_mode` on a group_with entry is a category error.

        A grouped entry is a there-and-back day trip from a shared base, not
        an inbound relocation leg, so there is no arriving journey for a mode
        to describe. Warn and ignore rather than raise, matching
        `_warn_if_group_dates_outside_base_range` (multimodal-routing.md 4.4).
        """
        for dest in data.get("destinations", []) or []:
            if not isinstance(dest, dict):
                continue
            if str(dest.get("group_with", "") or "").strip() and dest.get("transport_mode"):
                logger.warning(
                    "Destination '%s': transport_mode '%s' ignored — a group_with entry is "
                    "a day trip from its base, not an arriving leg.",
                    dest.get("id"), dest.get("transport_mode"),
                )

    #: Fields that describe a day out, and so say nothing on an entry that is
    #: not one. Warned about and ignored, like `transport_mode` above: a
    #: manifest is often hand-edited, and refusing a build over a field in the
    #: wrong place costs more than saying what was ignored.
    _SIDE_TRIP_ONLY_FIELDS = ("start", "end", "mode")

    def _warn_side_trip_fields_without_group(self, data: dict[str, Any]) -> None:
        """`start`, `end` and `mode` describe an outing from a base.

        Without `group_with` there is no base and no outing -- the entry is a
        stop the trip relocates to, whose arriving leg is described by
        `transport_mode` and whose own position is `coordinates`. So these are
        not quietly repurposed; they are named and dropped.
        """
        for dest in data.get("destinations", []) or []:
            if not isinstance(dest, dict):
                continue
            if str(dest.get("group_with", "") or "").strip():
                continue
            for field in self._SIDE_TRIP_ONLY_FIELDS:
                if dest.get(field):
                    logger.warning(
                        "Destination '%s': %s ignored -- it describes a day out from a "
                        "base, and this entry has no group_with.",
                        dest.get("id"), field,
                    )

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
