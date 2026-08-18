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

    Per-entry `base_owned_categories` overrides the config.yaml default
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
