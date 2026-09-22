"""
multi_site_grouping.py — shared helpers for GH #68 "Multi-Site Destinations"
(Option 3: grouped destinations via manifest).

See docs/design/multi-site-destination-grouping.md for the full design.
This module centralizes the `base_owned_categories` resolution rule so
manifest_parser.py (schema/validation), url_discovery.py (the discovery
skip-gate), ai_content.py (scenic-drive content gate), and
html_assembler.py (rendering) all agree on the same resolution without
duplicating it in four places.
"""
from __future__ import annotations
from typing import Any

# Valid category names for multi_site_grouping.base_owned_categories
# (config.yaml default) and a grouped destination entry's own
# base_owned_categories override. Matches the categories url_discovery.py
# independently discovers per destination, plus cultural_events (discovered
# by cultural_events.py, not url_discovery.py).
VALID_BASE_OWNED_CATEGORIES: frozenset[str] = frozenset(
    {"trail", "attraction", "restaurant", "en_route_stop", "scenic_drive", "cultural_events"}
)

# config.yaml multi_site_grouping.base_owned_categories default, matching
# docs/design/multi-site-destination-grouping.md §5's recommendation:
# physical proximity means restaurants are the category most likely to be
# a duplicate if independently discovered per grouped entry. cultural_events
# joined this default after a real validation run (dipstick67) showed a
# grouped child (Canyonlands) independently generating its own Cultural
# Events section that was actually about the group base's town (Moab) --
# project owner: "Cultural events should likely be kept in the primary
# destination not sub-destinations."
DEFAULT_BASE_OWNED_CATEGORIES: tuple[str, ...] = ("restaurant", "cultural_events")

# ...but that reasoning is about PARKS, not about grouping as such. The
# dipstick67 finding was a national park deferring to its gateway town, and
# it generalizes because a park genuinely has no dining scene or events
# calendar of its own -- both belong to the town you sleep in.
#
# A city day trip is the opposite case. Nashville reached from a base in Old
# Hickory has its own restaurants and its own music calendar, and deferring
# them to the smaller base inverts the relationship: the grouped entry is
# the bigger place. So park-likeness, not groupedness, decides whether the
# default deferral applies. An explicit per-entry base_owned_categories
# still overrides either way.
#
# Detection is deliberately not "has an nps_park_code". That field is
# US-only (NPS has no non-US coverage) and empty for state and provincial
# parks, so keying off it alone would classify Bledsoe Creek State Park and
# every park outside the US as a city and hand each one its own restaurant
# discovery. The name check catches those; the park code catches US
# national parks whose names do not contain an obvious keyword.
_PARK_NAME_KEYWORDS: tuple[str, ...] = (
    "national park", "state park", "provincial park", "national monument",
    "national forest", "national lakeshore", "national seashore",
    "national recreation area", "national historic", "wildlife refuge",
    "nature reserve", "regional park", "conservation area",
)


def is_park_like(dest: dict[str, Any] | None) -> bool:
    """True when a destination entry is a park rather than a populated place.

    Used to decide whether a grouped entry inherits the default deferral of
    restaurants/cultural events to its group base. False for anything it
    cannot positively identify as a park -- an unrecognized place is much
    more likely to be a town than a park, and the cost of being wrong that
    way (a redundant restaurant list) is smaller than the cost of the other
    way (a city day trip with no dining and no events at all).
    """
    if not isinstance(dest, dict):
        return False
    if str(dest.get("nps_park_code", "") or "").strip():
        return True
    name = str(dest.get("name", "") or "").lower()
    return any(keyword in name for keyword in _PARK_NAME_KEYWORDS)


def group_base_id(dest: dict[str, Any] | None) -> str:
    """Return a destination entry's `group_with` target id, or "" if unset."""
    if not isinstance(dest, dict):
        return ""
    return str(dest.get("group_with", "") or "").strip()


def is_grouped(dest: dict[str, Any] | None) -> bool:
    """True when this destination entry is a grouped (child) entry."""
    return bool(group_base_id(dest))


def resolve_base_owned_categories(
    dest: dict[str, Any] | None,
    default_categories: Any = DEFAULT_BASE_OWNED_CATEGORIES,
) -> frozenset[str]:
    """Resolve which categories a grouped entry defers to its group base.

    A grouped entry that is NOT park-like (a city day trip) defers nothing
    by default, since its dining and events are its own rather than the
    base's. Per-entry `base_owned_categories` overrides all of this
    entirely when present -- including an explicit empty list, which opts
    an entry OUT of any deferral even though the project-wide default is
    non-empty (the "sites are far enough apart" case from the design doc).
    Omitting the field entirely inherits `default_categories` (the
    resolved config.yaml value).
    """
    if isinstance(dest, dict) and "base_owned_categories" in dest:
        raw = dest.get("base_owned_categories")
        if isinstance(raw, list):
            return frozenset(str(c or "").strip().lower() for c in raw if str(c or "").strip())
        return frozenset()
    # No explicit override: the config default applies to park-like entries
    # only. See _PARK_NAME_KEYWORDS above for why a city day trip keeps its
    # own restaurants and events.
    if is_grouped(dest) and not is_park_like(dest):
        return frozenset()
    return frozenset(str(c or "").strip().lower() for c in (default_categories or ()) if str(c or "").strip())


def category_deferred_to_base(
    dest: dict[str, Any] | None,
    category: str,
    default_categories: Any = DEFAULT_BASE_OWNED_CATEGORIES,
) -> bool:
    """True when `category` discovery/content-generation should be skipped
    for this entry because its group base supplies it instead.

    Only ever true for a grouped entry (one with `group_with` set) -- a
    base entry (no `group_with`) always supplies its own categories
    regardless of the config default, since it has nothing to defer to.
    """
    if not is_grouped(dest):
        return False
    return str(category or "").strip().lower() in resolve_base_owned_categories(dest, default_categories)


# ── A day out that goes from one place to another ──────────────────────────
#
# A grouped entry has meant a there-and-back day trip from the base since GH
# #68, and that is one shape of outing rather than the only one. A ride along
# a trail is dropped off at one end and finishes at the other; so is a paddle
# down a river, a one-way walk, and a lift to the top of a hill. The three
# fields below are how a manifest says so, and they are read HERE rather than
# in the four modules that need them, for the reason this module already
# exists: four readings of one rule become four rules.


def stated_coordinates(holder: Any) -> tuple[float, float] | None:
    """The point an author put in the manifest, or None.

    None is the ordinary answer and means *look it up*, which is what every
    manifest written before this field existed says. A malformed block is also
    None rather than an error: the schema refuses anything that is not two
    numbers in range, so reaching here with something else means a caller built
    the dict itself, and no build is worth failing over that.
    """
    if not isinstance(holder, dict):
        return None
    block = holder.get("coordinates")
    if not isinstance(block, dict):
        return None
    try:
        return float(block["lat"]), float(block["lng"])
    except (KeyError, TypeError, ValueError):
        return None


def _named_end(dest: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    """One end of an outing, when the entry is an outing and the end is named.

    Both halves matter. `start` on an entry that is not grouped describes
    nothing -- there is no base for the outing to leave from -- and the parser
    warns about exactly that, so reading it here would be the second, silent
    interpretation of a field the first one rejected.
    """
    if not is_grouped(dest):
        return None
    end = (dest or {}).get(key)
    if not isinstance(end, dict) or not str(end.get("name", "") or "").strip():
        return None
    return end


def side_trip_start(dest: dict[str, Any] | None) -> dict[str, Any] | None:
    """Where this outing begins, when that is not the base it is grouped with."""
    return _named_end(dest, "start")


def side_trip_end(dest: dict[str, Any] | None) -> dict[str, Any] | None:
    """Where this outing finishes, when that is not the base it is grouped with.

    Also where the NEXT stop's journey starts from: a traveller who ended the
    day at the far end of a trail does not return to the base in order to
    leave from it.
    """
    return _named_end(dest, "end")


def is_point_to_point(dest: dict[str, Any] | None) -> bool:
    """True when this outing does not come back to where it started.

    Either end being named is enough. A ride that starts at a trailhead and
    finishes back at the base is still a one-way journey out, and calling it
    there-and-back would describe a return leg nobody rides.
    """
    return side_trip_start(dest) is not None or side_trip_end(dest) is not None


def side_trip_mode(dest: dict[str, Any] | None) -> str:
    """How this outing is travelled, or "" for the trip's own mode.

    Distinct from `transport_mode`, which describes the relocation leg
    arriving at a stop and is meaningless on a grouped entry: a day out has no
    arriving journey. This is the other half of that -- the day out has a mode
    of its own, and *we drove there* is not what a bike ride is.
    """
    if not is_grouped(dest):
        return ""
    return str((dest or {}).get("mode", "") or "").strip()
