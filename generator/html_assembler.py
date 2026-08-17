"""
html_assembler.py — Assemble final index.html from frozen v2.5 template.

Steps:
  1. Verify template SHA-256 checksum (hard fail on mismatch)
  2. Build per-destination section HTML strings
  3. Replace template placeholders with generated content
  4. Inject attribution block at page bottom

IMPORTANT: Uses Python string assembly — no Jinja2, no DOM parsing.
Template placeholders use the pattern <!--PLACEHOLDER_NAME-->.
"""
from __future__ import annotations
import html as html_escape
import hashlib, json, logging
from datetime import datetime
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from generator.multi_site_grouping import (
    DEFAULT_BASE_OWNED_CATEGORIES,
    category_deferred_to_base,
    group_base_id,
    is_grouped,
)

logger = logging.getLogger(__name__)
TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "v2.5_template.html"
CHECKSUM_PATH = Path(__file__).parent.parent / "templates" / "checksums.txt"

def _portable_image_href(path_str: str) -> str:
    """Return a portable image href relative to the generated index.html.

    Using absolute file:// URLs makes output brittle when the folder is moved.
    Images are written under the sibling images/ directory, so we emit
    ./images/<filename> for both local and hosted usage.
    """
    name = Path(path_str).name
    return f"./images/{quote(name)}"

def _verify_checksum(template_text: str) -> None:
    """Hard fail if template SHA-256 doesn't match stored value."""
    if not CHECKSUM_PATH.exists():
        raise FileNotFoundError(f"Checksum file not found: {CHECKSUM_PATH}")
    stored = CHECKSUM_PATH.read_text(encoding="utf-8").strip().split()[0]
    actual = hashlib.sha256(template_text.encode("utf-8")).hexdigest()
    if actual != stored:
        raise RuntimeError(
            f"Template checksum mismatch!\n"
            f"  Expected: {stored}\n"
            f"  Actual:   {actual}\n"
            "The frozen template has been modified. Restore it from git."
        )


class HTMLAssembler:
    def __init__(self, config_path: Path | str = "config.yaml") -> None:
        import yaml
        with Path(config_path).open() as f:
            self._config = yaml.safe_load(f)
        # GH #68 multi-site grouping default (see generator/multi_site_grouping.py
        # and generator/url_discovery.py, which independently loads the same
        # config key for its discovery gate).
        raw_base_owned = (self._config or {}).get("multi_site_grouping", {}).get(
            "base_owned_categories", DEFAULT_BASE_OWNED_CATEGORIES
        )
        if isinstance(raw_base_owned, list):
            self._multi_site_base_owned_categories = frozenset(
                str(c or "").strip().lower() for c in raw_base_owned if str(c or "").strip()
            )
        else:
            self._multi_site_base_owned_categories = frozenset(DEFAULT_BASE_OWNED_CATEGORIES)

    def assemble(self, trip: dict[str, Any]) -> str:
        template_text = TEMPLATE_PATH.read_text(encoding="utf-8")
        _verify_checksum(template_text)
        logger.info("Template checksum verified ✓")

        html = template_text
        meta = trip.get("_meta", {})
        dev_build = meta.get("development_build", {}) if isinstance(meta.get("development_build", {}), dict) else {}
        dev_fingerprint = str(dev_build.get("fingerprint", "") or "")
        stamp = (
            f"<!-- generator_version={meta.get('generator_version', '')}; "
            f"template_version={meta.get('template_version', '')}; "
            f"provider={meta.get('llm', {}).get('provider', '')}; "
            f"model={meta.get('llm', {}).get('model', '')}; "
            f"generated_at={meta.get('generated_at_utc', '')}; "
            f"development_build={dev_fingerprint} -->\n"
        )
        html = stamp + html

        # ── Trip-level substitutions ─────────────────────────────────────────
        meta = trip["trip"]
        html = html.replace("<!--TRIP_TITLE-->", meta["title"])
        html = html.replace("<!--THEME_COLOR-->", meta.get("theme_color", "#C0623E"))

        # ── Google Maps overview link ────────────────────────────────────────
        gmaps_url = self._build_google_maps_url(trip["destinations"], meta)
        html = html.replace("<!--GOOGLE_MAPS_URL-->", gmaps_url)

        # ── Map markers JSON ─────────────────────────────────────────────────
        markers = self._build_map_markers(trip["destinations"], meta)
        html = html.replace("'<!--MAP_MARKERS_JSON-->'", json.dumps(markers))

        # ── Nav tabs ────────────────────────────────────────────────────────
        html = html.replace("<!--NAV_TABS-->", self._build_nav_tabs(trip["destinations"], meta))

        # ── Per-destination sections ─────────────────────────────────────────
        sections_html = ""
        destinations = trip.get("destinations", [])
        # GH #68 multi-site grouping: id -> destination lookup so a grouped
        # entry's section can render "see base" pointers (lodging, deferred
        # categories) that name and link to its group base.
        dest_by_id = {d["id"]: d for d in destinations if isinstance(d, dict) and d.get("id")}

        # GH #68 card-within-card hierarchy: a grouped entry (group_with set)
        # never gets its own top-level <section> -- its content renders
        # nested inside its group base's section instead (see
        # _build_group_child_card). children_by_base maps a base's id -> its
        # grouped children in trip order, regardless of where each child
        # falls in the destinations list (mirrors the same base-id grouping
        # _build_nav_tabs already does for the nav-tab pill clustering).
        children_by_base: dict[str, list[dict[str, Any]]] = {}
        for d in destinations:
            if not isinstance(d, dict) or not d.get("id"):
                continue
            base_id = group_base_id(d)
            if base_id:
                children_by_base.setdefault(base_id, []).append(d)
        grouped_ids = {
            child["id"] for children in children_by_base.values() for child in children if child.get("id")
        }

        # The trailing "Departure Route Options" card belongs wherever the
        # traveler is actually spending the trip's last night. If the last
        # destination in the manifest is itself a grouped (day-trip) entry,
        # that is the group's base, not the day-trip location -- already
        # true for how _build_getting_there labels the leg (§4 open
        # question #3); this generalizes it to *where the card renders*,
        # now that day-trip entries no longer get their own section to
        # render it in.
        last_dest = destinations[-1] if destinations else None
        effective_last_id = ""
        if isinstance(last_dest, dict):
            effective_last_id = group_base_id(last_dest) or str(last_dest.get("id", "") or "")

        departure_name = meta.get("departure", "")

        def _previous_context(index: int) -> tuple[str, str]:
            if index > 0:
                return destinations[index - 1]["name"], self._destination_route_target(destinations[index - 1])
            return departure_name, str(departure_name or "").strip()

        for index, dest in enumerate(destinations):
            if not isinstance(dest, dict):
                continue
            dest_id = dest.get("id")
            if dest_id in grouped_ids:
                continue  # rendered nested inside its group base's section below

            previous_name, previous_route_target = _previous_context(index)
            current_route_target = self._destination_route_target(dest)

            group_children = children_by_base.get(dest_id, [])
            group_children_html = ""
            for child in group_children:
                # GH #68 design doc §4: a grouped child's "Getting Here"
                # must always compute base->child, never
                # previous-in-list->child -- the previous entry in manifest
                # order could itself be another grouped sibling (e.g.
                # Arches rendering just before Canyonlands under the same
                # Moab base), which would wrongly source the route/distance
                # from that sibling instead of from the shared lodging base
                # both day-trip from. Every grouped child's day trip
                # originates from its own base, regardless of manifest
                # position, so the base's own name/route target (already
                # computed above as `dest`/`current_route_target`) is
                # always the right "previous" context here.
                child_current_route_target = self._destination_route_target(child)
                group_children_html += self._build_group_child_card(
                    child,
                    meta,
                    dest["name"],
                    current_route_target,
                    child_current_route_target,
                    dest_by_id,
                )

            sections_html += self._build_single_section(
                dest,
                meta,
                previous_name,
                previous_route_target,
                current_route_target,
                is_last=(dest_id == effective_last_id),
                dest_by_id=dest_by_id,
                group_children=group_children,
                group_children_html=group_children_html,
                # The departure card's actual route content (distance,
                # route_summary, route_options) must still come from
                # whichever destination is truly last in the manifest --
                # its own ai_content.getting_there, exactly as before this
                # restructuring -- even though it now renders inside the
                # group base's section when that last destination is
                # grouped. _build_getting_there itself already knows how
                # to relabel the origin as the group base's name when
                # `departure_source` is a grouped entry (§4 open question
                # #3); passing the base's own ai here instead would be
                # wrong -- the base's ai_content.getting_there describes
                # a different (or absent) leg entirely.
                departure_source=last_dest,
            )
        sections_html += self._build_packing_summary(destinations)
        html = html.replace("<!--DESTINATION_SECTIONS-->", sections_html)

        # ── var DRIVE_DESCRIPTIONS (keyed by raw title, matches template JS) ──
        drive_descriptions = self._build_drive_descriptions(trip["destinations"])
        drive_json = json.dumps(drive_descriptions, indent=2)
        html = html.replace(
            "var DRIVE_DESCRIPTIONS = {};",
            f"var DRIVE_DESCRIPTIONS = {drive_json};",
        )

        # ── Footer credit ───────────────────────────────────────────────────
        html = self._inject_generator_footer(html, trip)

        return html

    # ── Private helpers ──────────────────────────────────────────────────────

    def _build_google_maps_url(self, destinations: list[dict[str, Any]], trip_meta: dict[str, Any] | None = None) -> str:
        if not destinations:
            return ""

        trip_meta = trip_meta or {}
        departure = str(trip_meta.get("departure", "") or "").strip()
        ret = str(trip_meta.get("return", "") or "").strip()

        route_stops = [self._destination_route_target(d) for d in destinations]
        route_stops = [s for s in route_stops if s]
        if not route_stops:
            return ""

        origin = departure or route_stops[0]
        destination = ret or route_stops[-1]
        if not origin or not destination:
            return ""

        stops = route_stops
        if departure:
            waypoints = stops
        else:
            waypoints = stops[1:]
        if not ret and waypoints:
            waypoints = waypoints[:-1]

        params = [
            "api=1",
            f"origin={quote(origin)}",
            f"destination={quote(destination)}",
            "travelmode=driving",
        ]
        if waypoints:
            params.append("waypoints=" + quote("|".join(waypoints), safe="|"))
        return "https://www.google.com/maps/dir/?" + "&".join(params)

    def _build_map_markers(self, destinations: list[dict[str, Any]], trip_meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Build Leaflet stops array in {c:[lat,lng], mo, dy, name} format."""
        import re
        trip_meta = trip_meta or {}
        result = []

        if trip_meta.get("departure") and trip_meta.get("departure_lat") and trip_meta.get("departure_lng"):
            dep_date, dep_time = self._format_trip_datetime_label(str(trip_meta.get("departure_datetime", "") or ""))
            result.append({
                "c": [trip_meta.get("departure_lat"), trip_meta.get("departure_lng")],
                "mo": "DEP",
                "dy": "",
                "name": str(trip_meta.get("departure"))[:24],
                "date_label": dep_date,
                "time_label": dep_time,
            })

        for i, d in enumerate(destinations):
            dates = d.get("dates", "")
            # Extract month abbrev and start day from e.g. "October 7-9, 2026"
            mo_match = re.match(r'([A-Za-z]+)\s+(\d+)', dates)
            mo = mo_match.group(1)[:3] if mo_match else ""
            dy = mo_match.group(2) if mo_match else ""
            short = d["name"].replace(" National Park", "").replace(" State Park", "")
            result.append(
                {
                    "c": [d.get("lat", 0), d.get("lng", 0)],
                    "mo": mo,
                    "dy": dy,
                    "name": short,
                    "idx": i + 1,
                    "stop_index": i + 1,
                }
            )

        if trip_meta.get("return"):
            return_lat = trip_meta.get("return_lat")
            return_lng = trip_meta.get("return_lng")
            if (return_lat is None or return_lng is None) and destinations:
                # Keep return annotation visible even when explicit return geocoding is missing.
                last_dest = destinations[-1] if isinstance(destinations[-1], dict) else {}
                return_lat = last_dest.get("lat")
                return_lng = last_dest.get("lng")
            if return_lat is not None and return_lng is not None:
                ret_date, ret_time = self._format_trip_datetime_label(str(trip_meta.get("return_datetime", "") or ""))
                result.append({
                    "c": [return_lat, return_lng],
                    "mo": "RET",
                    "dy": "",
                    "name": str(trip_meta.get("return"))[:24],
                    "date_label": ret_date,
                    "time_label": ret_time,
                })

        return result

    @staticmethod
    def _format_trip_datetime_label(raw: str) -> tuple[str, str]:
        text = str(raw or "").strip()
        if not text:
            return "", ""

        normalized = text.replace("T", " ")
        dt_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{1,2}):(\d{2})(?:\s*([APap][Mm]))?)?", normalized)
        if dt_match:
            year, month, day, hour, minute, ampm = dt_match.groups()
            try:
                month_i = int(month)
                day_i = int(day)
                date_label = datetime(int(year), month_i, day_i).strftime("%b %d").replace(" 0", " ")
            except ValueError:
                date_label = f"{month}/{day}/{year}"

            if hour is None:
                return date_label, ""

            hour_i = int(hour)
            minute_i = int(minute or 0)
            if ampm:
                time_label = f"{hour_i}:{minute_i:02d} {ampm.upper()}"
            else:
                suffix = "AM" if hour_i < 12 else "PM"
                hour12 = hour_i % 12
                if hour12 == 0:
                    hour12 = 12
                time_label = f"{hour12}:{minute_i:02d} {suffix}"
            return date_label, time_label

        compact_match = re.match(r"^([A-Za-z]{3,9}\.?\s+\d{1,2}(?:,\s*\d{4})?)(?:\s+(.+))?$", text)
        if compact_match:
            return compact_match.group(1).strip(), str(compact_match.group(2) or "").strip()

        return text, ""

    def _build_nav_tabs(self, destinations: list[dict[str, Any]], trip_meta: dict[str, Any] | None = None) -> str:
        """Build tab-btn buttons (data-tab=section-{id}) + Google Maps link.

        GH #68 multi-site grouping (docs/design/
        multi-site-destination-grouping.md §3): a grouped entry no longer
        gets its own nav-tab entry at all -- its content renders as a
        nested card inside its base's own section (see
        _build_group_child_card), reachable by scrolling that section, not
        by a separate top-level click target. §3 originally called for a
        clustered *sub-tab* per grouped child; that was superseded once the
        card-within-card rendering landed (2026-08-16 dipstick60 review):
        a grouped child appearing in the scrolling top-level menu breaks
        its visual relationship to the overview map (each nav entry no
        longer maps 1:1 to a numbered map marker) and isn't needed at the
        menu level once its content already lives inside its base's own
        section.
        """
        gmaps_url = self._build_google_maps_url(destinations, trip_meta)

        def _tab_label(i: int, dest: dict[str, Any]) -> str:
            short = dest["name"].replace(" National Park", "").replace(" State Park", "").split(",")[0].strip()
            return f"{i + 1} · {short}"

        # Map each group base id -> its grouped children, in original
        # destination order, regardless of where they fall in the list.
        children_by_base: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        for i, dest in enumerate(destinations):
            base_id = group_base_id(dest)
            if base_id:
                children_by_base.setdefault(base_id, []).append((i, dest))
        grouped_indices = {i for children in children_by_base.values() for i, _ in children}

        tabs = []
        for i, dest in enumerate(destinations):
            if i in grouped_indices:
                continue  # no nav entry -- content lives nested in its base's section
            active = ' active' if i == 0 else ''
            dest_id = dest["id"]  # use manifest id directly
            label = _tab_label(i, dest)
            tabs.append(f'<button class="tab-btn{active}" data-tab="section-{dest_id}">{label}</button>')
        tabs.append(
            f'<a href="{gmaps_url}" target="_blank" rel="noopener" class="map-tab-btn">'
            f'🗺️ Full Route Map</a>'
        )
        return "\n          ".join(tabs)

    def _build_single_section(
        self,
        dest: dict[str, Any],
        trip_meta: dict[str, Any],
        previous_name: str = "",
        previous_route_target: str = "",
        current_route_target: str = "",
        *,
        is_last: bool = False,
        dest_by_id: dict[str, dict[str, Any]] | None = None,
        group_children: list[dict[str, Any]] | None = None,
        group_children_html: str = "",
        departure_source: dict[str, Any] | None = None,
    ) -> str:
        import logging
        logger = logging.getLogger(__name__)
        ai = dest.get("ai_content", {})
        images = dest.get("images", [])
        events = dest.get("cultural_events", {})
        drives = dest.get("scenic_drives", [])
        logger.debug(f"_build_single_section for {dest['name']}: scenic_drives={len(drives)}")

        section_id = dest["id"]  # use manifest id directly
        section = f'<section id="section-{section_id}" class="dest-section">\n'

        # GH #68 card-within-card hierarchy, problem 2 ("avoid duplication"
        # -- dipstick59): a group base's own AI-generated top_attractions
        # has no awareness that a grouped child now independently covers
        # some of the same landmarks (e.g. Moab's own list suggesting
        # "Delicate Arch" when Arches -- now a nested child card -- already
        # covers it). Filter the base's own attraction list against
        # whatever its grouped children actually cover before rendering,
        # reusing the existing fuzzy name-match helper rather than
        # requiring an exact string match.
        attractions_for_render = ai.get("top_attractions", [])
        if group_children:
            covered_names = self._group_child_covered_names(group_children)
            attractions_for_render = self._dedupe_attractions_against_names(attractions_for_render, covered_names)

        # Header
        section += self._build_header(
            dest,
            images,
            dest.get("planning_links", []),
            dest.get("nps_park_code"),
            attractions_for_render,
        )

        # GH #68 multi-site grouping §2: grouped entry with no own lodging
        # gets a compact "Based from X (see Y)" pointer instead of nothing.
        # (No-op here in practice: manifest validation forbids a base from
        # itself having group_with, so this always returns "" for the
        # destination reaching this method -- kept for parity/safety since
        # a grouped child's own pointer is built by _build_group_child_card.)
        section += self._build_group_lodging_pointer(dest, dest_by_id)

        # Intro note or cultural event summary belongs directly under hero
        section += self._build_intro_note(dest, events)

        # Image gallery
        section += self._build_image_gallery(images, dest["name"])

        # Expected environment
        section += self._build_environment_card(ai, dest)

        # Getting here + en-route stops
        section += self._build_getting_here(
            ai,
            dest,
            previous_name,
            previous_route_target=previous_route_target,
            current_route_target=current_route_target,
            dest_by_id=dest_by_id,
        )

        # Attractions + scenic drives/viewpoints (deduped against group
        # children's own content, see attractions_for_render above)
        attractions_ai = ai
        if group_children:
            attractions_ai = dict(ai)
            attractions_ai["top_attractions"] = attractions_for_render
        section += self._build_attractions(attractions_ai, drives, dest.get("name", ""), dest=dest, dest_by_id=dest_by_id)

        # Daily schedule
        section += self._build_schedule(ai, drives, dest["name"])

        # Cultural events
        section += self._build_events(events, dest["name"])

        # Dinner recommendations
        section += self._build_restaurants(ai, dest["name"], dest=dest, dest_by_id=dest_by_id)

        # GH #68 card-within-card hierarchy: this base's grouped children
        # (day trips) render as nested cards here, after the base's own
        # full content, so the page reads as "Moab, plus its day trips" --
        # not as unrelated equal-weight stops. See _build_group_child_card.
        section += group_children_html

        # Final leg departure guidance for the last destination is rendered as a
        # separate trailing card so it does not displace inbound route context.
        # GH #68: `departure_source` is whichever destination is truly last in
        # the manifest -- itself, when this section's own dest is the last
        # destination and it's ungrouped, or a grouped child whose base is
        # this dest. Its own ai_content.getting_there supplies the actual
        # route content; _build_getting_there separately relabels the origin
        # as the group base's name when departure_source is grouped (§4 open
        # question #3) -- using this dest's own `ai` here instead would be
        # wrong, since a group base's ai_content.getting_there describes a
        # different (or absent) leg.
        if is_last:
            # Default to this dest's own ai/content when the caller doesn't
            # pass an explicit departure_source (e.g. a direct unit-level
            # call, or the ungrouped case where the last destination is its
            # own departure source anyway) -- matches pre-restructuring
            # behavior exactly.
            source = departure_source if departure_source is not None else dest
            departure_ai = source.get("ai_content", {})
            section += self._build_getting_there(departure_ai, source, trip_meta, dest_by_id=dest_by_id)

        # Collapsible debug block (opt-in only)
        if self._config.get("render", {}).get("show_debug_block", False):
            section += self._build_debug_block(dest, trip_meta)

        section += "</section>\n"
        return section

    # ── Section builders ─────────────────────────────────────────────────────

    def _build_header(
        self,
        dest: dict[str, Any],
        images: list[dict[str, Any]],
        planning_links: list[dict[str, Any]],
        nps_code: str | None,
        attractions: list[dict[str, Any]],
    ) -> str:
        hero_img = images[0]["local_path"] if images else ""
        # Use portable relative paths so moved/exported folders still work.
        if hero_img:
            hero_img = _portable_image_href(hero_img)
        credit = self._build_image_caption(images[0]) if images else ""
        header_links = self._build_header_links(planning_links, nps_code, dest, attractions)
        return (
            f'<div class="dest-header" style="background-image:url(\'{hero_img}\')">\n'
            f'  <div class="dest-header-actions">{header_links}</div>\n'
            f'  <h2>{dest["name"]}</h2>\n'
            f'  <p class="dates">{dest["dates"]}</p>\n'
            f'  <p class="img-credit">{credit}</p>\n'
            f'</div>\n'
        )

    def _build_header_links(
        self,
        links: list[dict[str, Any]],
        nps_code: str | None,
        dest: dict[str, Any],
        attractions: list[dict[str, Any]],
    ) -> str:
        pills: list[str] = []
        weather_url = self._build_weather_url(dest)
        if weather_url:
            pills.append(
                f'<a href="{weather_url}" class="notion-header-btn">Current Weather</a>'
            )
        attractions_map_url = self._build_destination_attractions_map_url(
            str(dest.get("name", "") or ""),
            attractions,
        )
        if attractions_map_url:
            pills.append(
                f'<a href="{self._safe_href(attractions_map_url)}" class="notion-header-btn">Attractions Map</a>'
            )
        if nps_code and self._is_us_destination(dest):
            pills.append(
                f'<a href="https://www.nps.gov/{nps_code}/" class="notion-header-btn">NPS</a>'
            )
        for link in links:
            url = self._normalize_external_url(link.get("url", ""))
            if not url:
                continue
            label = html_escape.escape(link.get("label", "Plans"))
            pills.append(
                f'<a href="{self._safe_href(url)}" class="notion-header-btn">{label}</a>'
            )
        return "".join(pills)

    def _build_destination_attractions_map_url(
        self,
        dest_name: str,
        attractions: list[dict[str, Any]],
    ) -> str:
        names: list[str] = []
        seen: set[str] = set()
        for item in attractions or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
            if len(names) >= 8:
                break

        if not names:
            return ""

        dest_label = str(dest_name or "").strip()
        qualified = [self._maps_fallback_query_text(name, dest_label) for name in names]

        # Single item: open a focused destination-scoped search query directly.
        if len(qualified) == 1:
            return f"https://www.google.com/maps/search/?api=1&query={quote(qualified[0])}"

        # Multiple items: emit a simple destination-scoped query that passes
        # single-result URL policy checks (no multi-name compound query).
        if dest_label:
            return f"https://www.google.com/maps/search/?api=1&query={quote(dest_label)}"

        # Fallback without destination context.
        return f"https://www.google.com/maps/search/?api=1&query={quote(', '.join(qualified))}"

    def _build_intro_note(self, dest: dict[str, Any], events: dict[str, Any]) -> str:
        title = html_escape.escape(dest.get("name", ""))
        what = dest.get("what_to_know") or {}
        if not isinstance(what, dict):
            what = {}

        summary = html_escape.escape(str(what.get("summary", "") or "").strip())
        if not summary:
            summary = (
                "Expect conditions and logistics to shift by season; confirm weather, operating hours, "
                "and access details the day before arrival."
            )

        html = '<div class="card intro-note-card">\n'
        html += f'  <h3>What to Know About {title}</h3>\n'
        html += f'  <p class="intro-note-text">{summary}</p>\n'

        detail_items = [
            ("Local customs", what.get("local_customs", "")),
            ("Best times of day", what.get("best_times_of_day", "")),
            ("Transportation quirks", what.get("transportation_quirks", "")),
            ("Safety", what.get("safety_considerations", "")),
            ("Crowd patterns", what.get("crowd_patterns", "")),
            ("Local etiquette", what.get("local_etiquette", "")),
        ]

        details_html = []
        for label, value in detail_items:
            text = html_escape.escape(str(value or "").strip())
            if text:
                details_html.append(f'  <p class="intro-note-text"><strong>{label}:</strong> {text}</p>')

        if details_html:
            html += "\n".join(details_html) + "\n"

        html += '</div>\n'
        return html

    def _build_getting_there(
        self,
        ai: dict,
        dest: dict,
        trip_meta: dict[str, Any],
        *,
        dest_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> str:
        getting_there = ai.get("getting_there", {}) if isinstance(ai, dict) else {}
        if not isinstance(getting_there, dict):
            getting_there = {}

        return_name = str((trip_meta or {}).get("return", "") or "").strip()
        route_summary = str(getting_there.get("route_summary", "") or "").strip()
        distance = str(getting_there.get("distance_miles", "") or "").strip()
        drive_time = str(getting_there.get("drive_time", "") or "").strip()
        route_options = getting_there.get("route_options", []) or []
        return_date_label, return_time_label = self._format_trip_datetime_label(
            str((trip_meta or {}).get("return_datetime", "") or "")
        )
        if not return_name and not route_summary and not route_options:
            return ""

        # GH #68 multi-site grouping §4 (open question #3, applied
        # symmetrically to the arrival-side fix): when the trip's final
        # destination is itself a grouped entry, the traveler's actual
        # physical base for the departure leg is the group's base, not the
        # grouped entry's own location -- they overnight at the base, not
        # inside the day-trip park. Label-only fix; the Google Maps link
        # below has never encoded an explicit origin (left to the device's
        # current location), so no behavior change there.
        departure_origin_name = str(dest.get("name", "") or "").strip()
        base_id = group_base_id(dest)
        if base_id:
            base = (dest_by_id or {}).get(base_id) or {}
            base_name = str(base.get("name", "") or "").strip()
            if base_name:
                departure_origin_name = base_name

        route_label = ""
        if return_name:
            route_label = f'{self._short_place_name(departure_origin_name)} → {self._short_place_name(return_name)}'

        gmaps_url = ""
        if return_name:
            pseudo_dest = {"name": return_name}
            gmaps_url = self._build_route_gmaps_url("", pseudo_dest, route_options)

        html = '<div class="card getting-here-card getting-here-subcard departure-route-card">\n'
        html += '  <div class="getting-here-header">\n'
        html += '    <h3>↩️ Departure Route Options</h3>\n'
        if gmaps_url:
            html += f'    <a href="{gmaps_url}" target="_blank" rel="noopener" class="gmaps-link">Open in Google Maps →</a>\n'
        html += '  </div>\n'

        if distance and drive_time:
            html += '  <div class="route-headline-row">\n'
            if route_label:
                html += f'    <div class="route-headline">{html_escape.escape(route_label)}</div>\n'
            html += '    <div class="route-badges route-badges-inline">\n'
            html += f'      <span class="badge badge-distance">{distance} mi</span>\n'
            html += f'      <span class="badge badge-time">{drive_time}</span>\n'
            html += '    </div>\n'
            html += '  </div>\n'
        elif route_label:
            html += f'  <div class="route-headline">{html_escape.escape(route_label)}</div>\n'

        if route_summary:
            html += f'  <p class="route-summary">{html_escape.escape(route_summary)}</p>\n'

        if return_date_label or return_time_label:
            return_anchor = " ".join(part for part in [return_date_label, return_time_label] if part).strip()
            html += f'  <p class="route-summary"><strong>Return anchor:</strong> {html_escape.escape(return_anchor)}</p>\n'

        renderable_route_options: list[dict[str, Any]] = []
        for opt in route_options:
            title = str(opt.get("title", "") or "").strip()
            if not title:
                continue
            url, _is_map_fallback = self._select_preferred_external_link(opt, section="en_route_stop")
            if not url:
                continue
            renderable_route_options.append({"title": title, "url": url, "opt": opt})

        if renderable_route_options:
            html += '  <div class="can-miss-header">🧭 DEPARTURE ROUTE OPTIONS</div>\n'
            html += '  <div class="en-route-stops">\n'
            for item in renderable_route_options:
                title = item["title"]
                url = item["url"]
                opt = item["opt"]
                source_icon = self._link_source_icon(url)
                name_html = (
                    f'<a href="{self._safe_href(url)}">{html_escape.escape(title)}</a>'
                    f' <span class="attr-external-link" title="link source">{source_icon}</span>'
                )
                maps_corner_html = self._maps_corner_link_html(opt, url)
                dist = str(opt.get("distance_or_duration", "") or "").strip()
                detour_html = f' <span class="stop-detour">({html_escape.escape(dist)})</span>' if dist else ""
                description = html_escape.escape(str(opt.get("description", "") or "").strip())
                html += (
                    '    <div class="stop-card">'
                    '<span class="stop-icon">🚗</span>'
                    f'<div class="stop-body"><strong>{name_html}</strong>{detour_html}'
                    f'<div class="stop-desc">{description}</div></div>'
                    f'{maps_corner_html}'
                    '</div>\n'
                )
            html += '  </div>\n'

        html += '</div>\n'
        return html

    def _build_weather_url(self, dest: dict[str, Any]) -> str:
        lat = dest.get("lat")
        lng = dest.get("lng")
        if lat is None or lng is None:
            return ""
        try:
            lat_f = float(lat)
            lng_f = float(lng)
        except (TypeError, ValueError):
            return ""

        if self._is_us_coordinates(lat_f, lng_f):
            return f"https://forecast.weather.gov/MapClick.php?lat={lat_f:.4f}&lon={lng_f:.4f}"

        # Global fallback provider for non-US destinations.
        return f"https://weather.com/weather/today/l/{lat_f:.4f},{lng_f:.4f}"

    @staticmethod
    def _is_us_coordinates(lat: float, lng: float) -> bool:
        if 24.0 <= lat <= 49.5 and -125.0 <= lng <= -66.5:
            return True
        if 51.0 <= lat <= 72.0 and -170.0 <= lng <= -129.0:
            return True
        if 18.0 <= lat <= 23.0 and -161.0 <= lng <= -154.0:
            return True
        return False

    def _is_us_destination(self, dest: dict[str, Any]) -> bool:
        lat = dest.get("lat")
        lng = dest.get("lng")
        try:
            return self._is_us_coordinates(float(lat), float(lng))
        except (TypeError, ValueError):
            return False

    def _build_environment_card(self, ai: dict, dest: dict[str, Any]) -> str:
        env = ai.get("expected_environment", "")
        if not env:
            return ""
        # Handle dict structure: {"summary": "...", "temperature_high_f": 72, ...}
        if isinstance(env, dict):
            summary = env.get("summary", "")
            html = '<div class="card env-card">\n'
            html += '<div class="env-subcard">\n'
            html += f'<h3>🧥 What to Expect</h3>\n'
            html += f'  <p class="env-summary">{summary}</p>\n'
            weather_url = self._build_weather_url(dest)
            if weather_url:
                html += f'  <a href="{weather_url}" class="weather-link">Current Weather</a>\n'
            html += '</div>\n'
            html += '</div>\n'
            return html
        # Fallback for string
        return f'<div class="card env-card"><p>{env}</p></div>\n'

    def _build_image_gallery(self, images: list, dest_name: str) -> str:
        """Build image gallery from discovered images."""
        if not images or len(images) <= 1:
            return ""

        gallery_images = [img for img in images[1:] if img.get("local_path")]
        if not gallery_images:
            return ""

        gallery_class = "photo-gallery photo-gallery-single" if len(gallery_images) == 1 else "photo-gallery"
        html = f'<div class="{gallery_class}">\n'
        for img in gallery_images:
            local_path = img.get("local_path", "")
            caption = self._build_image_caption(img)

            file_url = _portable_image_href(local_path)
            dest_escaped = html_escape.escape(dest_name)
            
            html += '  <div class="image-tile photo-item">\n'
            html += (
                f'    <img src="{file_url}" alt="{dest_escaped}" '
                f'onerror="this.style.display=\'none\';" loading="lazy" />\n'
            )
            html += f'    <div class="caption photo-caption">{caption}</div>\n'
            html += '  </div>\n'
        
        html += '</div>\n'
        return html

    def _build_image_caption(self, image: dict[str, Any]) -> str:
        credit = self._sanitize_caption_text(image.get("credit", ""))
        source = self._sanitize_caption_text(image.get("source", ""))
        title = self._sanitize_caption_text(image.get("title", ""))
        if credit:
            return html_escape.escape(credit)
        if source and title:
            return html_escape.escape(f"{source.title()} — {title}")
        if source:
            return html_escape.escape(source.title())
        return html_escape.escape(title)

    def _sanitize_caption_text(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""

        text = html_escape.unescape(text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"https?://\S+", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip()

        lower = text.lower()
        noisy_markers = (
            "when reusing",
            "please credit me",
            "contact me at",
            "layouttemplate",
            "external text",
            "mw-file",
            "cc-by-sa",
            "wikimedia.org/wiki/file",
            "info icon",
        )
        if any(marker in lower for marker in noisy_markers):
            return ""

        if len(text) > 160:
            text = text[:157].rstrip() + "..."
        return text

    @staticmethod
    def _route_waypoint_sort_key(stop: Any) -> tuple[int, float]:
        if not isinstance(stop, dict):
            return (1, 0.0)
        if stop.get("route_waypoint_eligible") is False:
            return (1, 0.0)
        value = stop.get("route_progress_ratio")
        try:
            ratio = float(value)
        except (TypeError, ValueError):
            # No confirmed position along the route (e.g. url_discovery's
            # geocoder couldn't resolve this stop's coordinates) must not
            # silently sort as "the very start of the route" -- that put
            # destination-adjacent stops ahead of genuinely-earlier ones.
            # See dipstick58 Bug 1: "Fremont Petroglyphs" and "Gifford
            # Homestead" (both inside Capitol Reef, i.e. at/past the
            # destination) rendered 1st/2nd on the Bryce -> Capitol Reef
            # leg, ahead of real Highway 12 waypoints (Kodachrome Basin,
            # Escalante Petrified Forest, Head of the Rocks Overlook, Lower
            # Calf Creek Falls, Anasazi State Park Museum) that all had a
            # verified, smaller route_progress_ratio. Sorting unknown-ratio
            # stops after every stop with a confirmed ratio keeps the
            # verified geographic order intact instead of corrupting it.
            return (0, float("inf"))
        return (0, ratio)

    def _build_route_gmaps_url(
        self,
        previous_name: str,
        dest: dict,
        stops: list,
        *,
        waypoint_scope_name: str = "",
    ) -> str:
        """Build a Google Maps directions URL with destination and waypoints."""
        destination = dest.get("name", "")
        if not destination:
            return ""

        params = [f"destination={quote(destination)}", "travelmode=driving", "api=1"]
        if previous_name:
            params.append(f"origin={quote(previous_name)}")

        waypoint_names: list[str] = []
        destination_name = str(destination or "").strip()
        waypoint_scope = str(waypoint_scope_name or destination_name).strip()
        ordered_stops = sorted(
            [stop for stop in stops if isinstance(stop, dict) and stop.get("route_waypoint_eligible") is not False],
            key=self._route_waypoint_sort_key,
        )
        for stop in ordered_stops[:8]:
            stop_name = str(stop.get("name", "") or "").strip()
            if not stop_name:
                continue
            # Prefer a real geocoded coordinate over any name-based guess when
            # one exists (url_discovery.py already verified it's plausibly
            # on-route -- see _prune_en_route_stops_by_geometry). A coordinate
            # resolves to exactly one point; a free-text waypoint name does
            # not, and the qualification heuristic below has a real failure
            # mode for en-route stops specifically: an en-route stop isn't
            # "at" the arrival destination the way a same-destination
            # attraction is, so (a) a coincidental shared word can suppress
            # qualification entirely (dipstick60: "Canyon Overlook Trail", a
            # Zion-area trail, shares "canyon" with arrival destination
            # "Bryce Canyon National Park" and rendered fully unqualified,
            # resolving in Google Maps to an unrelated "Stan's Overlook
            # Trail, Snoqualmie, WA"), and (b) even when qualification does
            # fire, blindly appending the *arrival* destination's name is
            # wrong for a stop that isn't actually near it (dipstick60:
            # "Mossy Cave", a real Bryce Canyon trail, rendered as "Mossy
            # Cave Capitol Reef National Park" on the Bryce->Capitol Reef
            # leg). Both are avoided entirely once a real coordinate exists.
            geocoded_lat = stop.get("geocode_lat")
            geocoded_lng = stop.get("geocode_lng")
            if isinstance(geocoded_lat, (int, float)) and isinstance(geocoded_lng, (int, float)):
                waypoint_names.append(f"{geocoded_lat},{geocoded_lng}")
            elif waypoint_scope and self._looks_location_qualified(waypoint_scope):
                waypoint_names.append(self._maps_fallback_query_text(stop_name, waypoint_scope))
            else:
                waypoint_names.append(stop_name)
        if waypoint_names:
            # Our own waypoint ordering (_route_waypoint_sort_key /
            # route_progress_ratio) is a straight-line projection onto the
            # origin->destination line -- it has no notion of which real
            # roads exist. That breaks down for a stop that's a genuine
            # detour off the direct highway (e.g. dipstick63: Kolob Canyons
            # Visitor Center sits ~20 mi north of the St. George->Springdale
            # line, but its projection lands *ahead of* Virgin, UT, which is
            # only ~3 mi off that line and genuinely closer to the
            # destination -- verified with real geocoded coordinates from
            # that run). With 2+ waypoints, let Google's own road-network
            # routing reorder them instead of trusting our approximation.
            if len(waypoint_names) > 1:
                params.append("waypoints=" + quote("optimize:true|" + "|".join(waypoint_names), safe="|"))
            else:
                params.append("waypoints=" + quote("|".join(waypoint_names), safe="|"))
        return "https://www.google.com/maps/dir/?" + "&".join(params)

    def _build_destination_scope_maps_url(self, destination_name: str = "", source_url: str = "") -> str:
        candidate = str(destination_name or "").strip()
        if not candidate and source_url:
            normalized = self._normalize_external_url(source_url)
            if self._is_maps_directions_url(normalized):
                try:
                    parsed = urlparse(normalized)
                    query_values = parse_qs(parsed.query)
                    for key in ("destination", "q", "query"):
                        values = query_values.get(key, [])
                        if values:
                            candidate = str(values[0] or "").strip()
                            break
                except Exception:
                    candidate = ""
        if not candidate:
            return ""
        return f"https://www.google.com/maps/search/?api=1&query={quote(candidate)}"

    @staticmethod
    def _destination_route_target(dest: dict[str, Any] | None) -> str:
        if not isinstance(dest, dict):
            return ""
        lodging = dest.get("lodging", {}) if isinstance(dest.get("lodging", {}), dict) else {}
        lodging_location = str(lodging.get("location", "") or "").strip()
        if lodging_location:
            return lodging_location
        return str(dest.get("name", "") or "").strip()

    # ── GH #68 multi-site grouping: "see base" rendering helpers ────────────
    # See docs/design/multi-site-destination-grouping.md §2 (lodging dedup)
    # and §5 (base-owned category pointers). Both funnel through
    # _group_base_pointer_html so every deferred-category pointer looks and
    # links the same way.

    def _resolved_base_owned_categories_for_render(self, dest: dict[str, Any] | None) -> frozenset[str]:
        default_categories = getattr(
            self, "_multi_site_base_owned_categories", frozenset(DEFAULT_BASE_OWNED_CATEGORIES)
        )
        from generator.multi_site_grouping import resolve_base_owned_categories

        return resolve_base_owned_categories(dest, default_categories)

    def _category_deferred_for_render(self, dest: dict[str, Any] | None, category: str) -> bool:
        return category_deferred_to_base(
            dest,
            category,
            getattr(self, "_multi_site_base_owned_categories", frozenset(DEFAULT_BASE_OWNED_CATEGORIES)),
        )

    def _group_base_pointer_html(
        self,
        dest: dict[str, Any] | None,
        dest_by_id: dict[str, dict[str, Any]] | None,
        label: str,
        *,
        icon: str = "\U0001f4cd",
        css_class: str = "group-base-pointer",
    ) -> str:
        """Compact link back to a grouped entry's base section, e.g.
        'Dining: see Moab'. Returns "" when the base can't be resolved.

        dipstick60 Bug 3: only the destination name itself ("Moab") is the
        clickable anchor -- the icon/label prefix stays plain text -- so the
        rendered line reads as a sentence with a linked place name rather
        than one large button-like link. The href reuses the same
        `#section-<id>` hash target the top nav's tab buttons already jump
        to (see the TAB NAVIGATION hashchange handler in
        templates/v2.5_template.html), so clicking it smoothly scrolls to
        and activates the base's own section like any other in-page nav
        link -- no new navigation mechanism invented here.
        """
        base_id = group_base_id(dest)
        if not base_id:
            return ""
        base = (dest_by_id or {}).get(base_id) or {}
        base_name = str(base.get("name", "") or base_id).strip()
        return (
            f'<p class="{css_class}">'
            f'{icon} {html_escape.escape(label)}: see '
            f'<a href="#section-{base_id}">{html_escape.escape(base_name)}</a></p>\n'
        )

    def _build_group_lodging_pointer(
        self, dest: dict[str, Any], dest_by_id: dict[str, dict[str, Any]] | None = None
    ) -> str:
        """§2: a grouped entry with no own `lodging` block renders a
        compact 'Based from X (see Y)' pointer instead of repeating a full
        lodging block for what is, physically, one stay."""
        base_id = group_base_id(dest)
        if not base_id:
            return ""
        own_lodging = dest.get("lodging")
        if isinstance(own_lodging, dict) and own_lodging:
            return ""  # this entry overrides lodging -- nothing to dedup
        base = (dest_by_id or {}).get(base_id) or {}
        base_name = str(base.get("name", "") or base_id).strip()
        base_lodging = base.get("lodging") if isinstance(base.get("lodging"), dict) else {}
        lodging_name = str(base_lodging.get("name") or base_lodging.get("location") or "").strip()
        text = f"Based from {lodging_name} (see {base_name})" if lodging_name else f"See {base_name} for lodging"
        return (
            '<p class="group-lodging-pointer">'
            f'<a href="#section-{base_id}">\U0001f3e8 {html_escape.escape(text)}</a></p>\n'
        )

    # ── GH #68 card-within-card hierarchy ────────────────────────────────
    # See docs/design/multi-site-destination-grouping.md §8 and the
    # dipstick59 triage notes: a grouped entry's own "Possible Daily
    # Schedule" card falsely implied parity with a genuine multi-day
    # destination (the base's own schedule already places the day trip on
    # a specific day), and the base's own AI-generated top_attractions had
    # no awareness that a grouped child now separately covers some of the
    # same landmarks. _build_group_child_card nests a grouped entry's
    # content inside its base's <section> instead of a sibling <section>,
    # omitting the schedule card entirely; _group_child_covered_names /
    # _dedupe_attractions_against_names let the base's own attractions
    # card filter out anything a grouped child already covers.

    def _group_child_covered_names(self, children: list[dict[str, Any]]) -> list[str]:
        """Names already rendered inside a group base's own nested child
        cards -- the child destination's own name plus every name in its
        top_attractions and scenic_drives. Used so the base's own
        attractions card doesn't re-suggest a landmark a grouped child
        already covers (e.g. Moab independently suggesting "Delicate Arch"
        when Arches -- now a nested child card -- already covers it)."""
        covered: list[str] = []
        for child in children or []:
            if not isinstance(child, dict):
                continue
            child_name = str(child.get("name", "") or "").strip()
            if child_name:
                covered.append(child_name)
            child_ai = child.get("ai_content", {})
            if isinstance(child_ai, dict):
                for attr in child_ai.get("top_attractions", []) or []:
                    if isinstance(attr, dict):
                        name = str(attr.get("name", "") or "").strip()
                        if name:
                            covered.append(name)
            for drive in child.get("scenic_drives", []) or []:
                if isinstance(drive, dict):
                    title = str(drive.get("title", "") or "").strip()
                    if title:
                        covered.append(title)
        return covered

    def _dedupe_attractions_against_names(
        self, attractions: list[dict[str, Any]], covered_names: list[str]
    ) -> list[dict[str, Any]]:
        """Drop any attraction whose name plausibly describes the same
        real place as something in `covered_names`, reusing the same
        fuzzy name-match rule as the existing scenic-drive/attraction
        dedup (_attraction_names_are_duplicates) rather than requiring an
        exact string match."""
        if not covered_names or not attractions:
            return attractions
        kept: list[dict[str, Any]] = []
        for attr in attractions:
            name = str(attr.get("name", "") or "") if isinstance(attr, dict) else ""
            if name and any(
                self._attraction_names_are_duplicates(name, covered) for covered in covered_names
            ):
                continue
            kept.append(attr)
        return kept

    def _build_group_child_card(
        self,
        dest: dict[str, Any],
        trip_meta: dict[str, Any],
        previous_name: str,
        previous_route_target: str,
        current_route_target: str,
        dest_by_id: dict[str, dict[str, Any]] | None,
    ) -> str:
        """Render a grouped (day-trip) entry's own content nested inside
        its group base's <section>, instead of as an independent sibling
        <section> carrying equal visual weight to a genuine multi-day
        destination.

        Reuses the same per-destination card builders _build_single_section
        uses (header, intro note, gallery, environment, getting-here,
        attractions, events, restaurants -- including the existing §2
        lodging pointer and §5 "see base" category pointers, which keep
        working unchanged here). Two things intentionally differ from a
        top-level section:

        1. No _build_schedule call, ever. A grouped entry's day-trip plan
           is already covered by the base's own multi-day schedule (see
           module-level GH #68 comment block above) -- rendering its own
           "Possible Daily Schedule" card here would re-introduce the
           false-parity problem this restructuring exists to fix.
        2. No _build_getting_there call. The trailing departure-route card
           belongs to wherever the traveler actually spends the trip's
           last night -- the group base -- handled by assemble()'s
           effective_last_id computation, not here.
        """
        ai = dest.get("ai_content", {})
        images = dest.get("images", [])
        events = dest.get("cultural_events", {})
        drives = dest.get("scenic_drives", [])

        child_id = dest["id"]
        base_id = group_base_id(dest)
        base = (dest_by_id or {}).get(base_id) or {}
        base_name = str(base.get("name", "") or base_id).strip()

        html = (
            f'<div id="section-{child_id}" class="group-child-card" '
            'style="margin:0 1.5rem 1.25rem;border:2px solid var(--sandstone);'
            'border-radius:14px;overflow:hidden;background:#faf7f2;">\n'
        )
        html += (
            '  <div class="group-child-banner" style="padding:0.5rem 1.25rem;'
            'background:var(--sandstone);font-size:0.8rem;font-weight:700;'
            'letter-spacing:0.03em;color:var(--canyon);text-transform:uppercase;">'
            f'\U0001f9ed Day trip from {html_escape.escape(base_name)}</div>\n'
        )

        html += self._build_header(
            dest,
            images,
            dest.get("planning_links", []),
            dest.get("nps_park_code"),
            ai.get("top_attractions", []),
        )
        html += self._build_group_lodging_pointer(dest, dest_by_id)
        html += self._build_intro_note(dest, events)
        html += self._build_image_gallery(images, dest["name"])
        html += self._build_environment_card(ai, dest)
        html += self._build_getting_here(
            ai,
            dest,
            previous_name,
            previous_route_target=previous_route_target,
            current_route_target=current_route_target,
            dest_by_id=dest_by_id,
        )
        html += self._build_attractions(ai, drives, dest.get("name", ""), dest=dest, dest_by_id=dest_by_id)
        # No _build_schedule call -- see docstring above.
        html += self._build_events(events, dest["name"])
        html += self._build_restaurants(ai, dest["name"], dest=dest, dest_by_id=dest_by_id)

        if self._config.get("render", {}).get("show_debug_block", False):
            html += self._build_debug_block(dest, trip_meta)

        html += '</div>\n'
        return html

    def _build_getting_here(
        self,
        ai: dict,
        dest: dict,
        previous_name: str,
        *,
        previous_route_target: str = "",
        current_route_target: str = "",
        dest_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> str:
        gh = ai.get("getting_here", {})
        if not gh:
            return ""
        route_summary = gh.get("route_summary", "")
        distance = gh.get("distance_miles", "")
        drive_time = gh.get("drive_time", "")
        stops = gh.get("en_route_stops", [])
        route_label = ""
        if previous_name:
            route_label = f'{self._short_place_name(previous_name)} → {self._short_place_name(dest.get("name", ""))}'

        # GH #68 multi-site grouping §4: a group_with transition is a
        # there-and-back day trip from the shared base, not a one-way
        # relocation leg -- label it distinctly so "Moab → Arches" doesn't
        # read as a new place to check into.
        is_group_day_trip = is_grouped(dest)

        route_destination = {"name": current_route_target or dest.get("name", "")}
        gmaps_url = self._build_route_gmaps_url(
            previous_route_target or previous_name,
            route_destination,
            stops,
            waypoint_scope_name=str(dest.get("name", "") or ""),
        )
        
        # Icon map for stop types
        stop_icons = {
            "viewpoint": "🏜️",
            "attraction": "🏛️",
            "town": "🏘️",
            "food": "🍔",
            "scenic": "🌄",
            "natural": "🏞️",
            "historic": "🏛️",
            "hike": "🥾",
            "waterfall": "💧",
            "museum": "🏛️",
            "market": "🛍️",
        }
        
        html = '<div class="card getting-here-card getting-here-subcard">\n'
        html += '  <div class="getting-here-header">\n'
        html += '    <h3>🚗 Getting Here</h3>\n'
        if gmaps_url:
            html += f'    <a href="{gmaps_url}" target="_blank" rel="noopener" class="gmaps-link">Open in Google Maps →</a>\n'
        html += '  </div>\n'

        day_trip_badge = (
            '<span class="badge badge-daytrip" style="background:var(--sage);color:#fff;">Day Trip</span>\n'
            if is_group_day_trip
            else ""
        )

        # Route summary with distance and time badges
        if distance and drive_time:
            html += '  <div class="route-headline-row">\n'
            if route_label:
                html += f'    <div class="route-headline">{html_escape.escape(route_label)}</div>\n'
            html += '    <div class="route-badges route-badges-inline">\n'
            if day_trip_badge:
                html += f'      {day_trip_badge}'
            html += f'      <span class="badge badge-distance">{distance} mi</span>\n'
            html += f'      <span class="badge badge-time">{drive_time}</span>\n'
            html += '    </div>\n'
            html += '  </div>\n'
        elif route_label:
            html += f'  <div class="route-headline">{html_escape.escape(route_label)}'
            if day_trip_badge:
                html += f' {day_trip_badge}'
            html += '</div>\n'

        if route_summary:
            html += f'  <p class="route-summary">{route_summary}</p>\n'

        # GH #68 multi-site grouping §5: en-route stops deferred to the
        # group base render a pointer instead of just silently vanishing.
        if not stops and self._category_deferred_for_render(dest, "en_route_stop"):
            html += self._group_base_pointer_html(dest, dest_by_id, "En-route stops", icon="\U0001f9ed")

        if stops:
            visible_stops: list[tuple[dict[str, Any], str, bool]] = []
            ordered_stops = sorted(
                [stop for stop in stops if not (isinstance(stop, dict) and stop.get("route_waypoint_eligible") is False)],
                key=self._route_waypoint_sort_key,
            )
            for stop in ordered_stops:
                preferred_url, is_map_fallback = self._select_preferred_external_link(stop, section="en_route_stop")
                if preferred_url:
                    visible_stops.append((stop, preferred_url, is_map_fallback))
                elif self._should_render_without_url(stop, section="en_route_stop"):
                    visible_stops.append((stop, "", is_map_fallback))

        if stops and visible_stops:
            html += '  <div class="can-miss-header">🧭 CAN\'T-MISS ENROUTE</div>\n'
            html += '  <div class="en-route-stops">\n'
            for stop, url, is_map_fallback in visible_stops:
                stop_type = self._infer_stop_type(stop).lower()
                icon = stop_icons.get(stop_type, "📍")
                stop_name = html_escape.escape(str(stop.get("name", "") or ""))
                if url:
                    source_icon = self._link_source_icon(url)
                    name_html = (
                        f'<a href="{self._safe_href(url)}" target="_blank" rel="noopener">{stop_name}</a>'
                        f' <span class="attr-external-link" title="link source">{source_icon}</span>'
                    )
                    maps_corner_html = self._maps_corner_link_html(stop, url)
                else:
                    name_html = stop_name
                    maps_corner_html = ""
                # These two values are rendered exactly as extracted, with no
                # normalization of one-way vs. round-trip vs. loop-back
                # semantics -- see the detour-semantics note above
                # _extract_en_route_detour_minutes_from_text in
                # url_discovery.py for why that's a documented open gap
                # rather than a bug fixed here.
                detour_parts: list[str] = []
                detour_miles = stop.get("detour_distance_miles")
                detour_minutes = stop.get("detour_time_minutes")
                if detour_miles not in (None, ""):
                    detour_parts.append(f"{detour_miles} mi detour")
                if detour_minutes not in (None, ""):
                    detour_parts.append(f"{detour_minutes} min")
                detour_html = ""
                if detour_parts:
                    detour_html = f' <span class="stop-detour">({html_escape.escape(" | ".join(detour_parts))})</span>'
                description_raw = str(stop.get("description", "") or "").strip()
                practical_note_raw = str(stop.get("practical_note", "") or "").strip()
                if practical_note_raw and practical_note_raw.casefold() == description_raw.casefold():
                    practical_note_raw = ""

                rating_value = stop.get("rating")
                rating_text = str(stop.get("raw_rating") or "").strip()
                if not rating_text and rating_value is not None:
                    try:
                        rating_text = f"{float(rating_value):.1f}"
                    except (TypeError, ValueError):
                        rating_text = str(rating_value).strip()
                if not rating_text:
                    rating_text, description_raw = self._extract_rating_badge_and_clean_text(description_raw)
                if not rating_text:
                    rating_text, practical_note_raw = self._extract_rating_badge_and_clean_text(practical_note_raw)
                rating_badge_html = (
                    f' <span class="badge badge-rating">★ {html_escape.escape(rating_text)}</span>' if rating_text else ""
                )
                caution_html = (
                    ' <span class="badge badge-caution" title="No verified source link found for this recommendation">⚠ Unverified</span>'
                    if not url else ""
                )

                description = html_escape.escape(description_raw)
                practical_note = html_escape.escape(practical_note_raw)
                note_html = f'<div class="stop-note">{practical_note}</div>' if practical_note else ""
                html += (
                    f'    <div class="stop-card">'
                    f'<span class="stop-icon">{icon}</span>'
                    f'<div class="stop-body"><strong>{name_html}</strong>{detour_html}{rating_badge_html}{caution_html}'
                    f'<div class="stop-desc">{description}</div>{note_html}</div>'
                    f'{maps_corner_html}'
                    f'</div>\n'
                )
            html += '  </div>\n'
        html += '</div>\n'
        return html

    def _short_place_name(self, name: str) -> str:
        short = name.replace("National Park", "NP").replace("State Park", "SP")
        return " ".join(short.split())

    @staticmethod
    def _normalize_attraction_name_for_dedup(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()

    # Filler words stripped before comparing name tokens -- neither
    # side of a genuine duplicate nor a genuine distinction hinges on these.
    _ATTRACTION_DEDUP_STOPWORDS = frozenset({
        "a", "an", "the", "to", "at", "in", "on", "of", "and", "for", "from",
        "via", "near", "into", "with",
    })

    # Qualifiers that name two genuinely different, commonly-paired real
    # places (e.g. Bryce Canyon's actual "Sunrise Point" and "Sunset Point"
    # viewpoints, or a park's "North Overlook" vs "South Overlook"). If these
    # are the words that differ between two names, every other word matching
    # is not enough evidence they're the same place -- never merge on that
    # basis alone.
    _ATTRACTION_DEDUP_DIFFERENTIATORS = frozenset({
        "sunrise", "sunset", "north", "south", "east", "west",
        "upper", "lower", "inner", "outer", "main", "back", "front",
        "old", "new",
    })

    @staticmethod
    def _attraction_dedup_tokens(name: str) -> set[str]:
        normalized = HTMLAssembler._normalize_attraction_name_for_dedup(name)
        return {
            token
            for token in normalized.split()
            if token and token not in HTMLAssembler._ATTRACTION_DEDUP_STOPWORDS
        }

    @staticmethod
    def _attraction_names_are_duplicates(name_a: str, name_b: str) -> bool:
        """True when two differently-worded names plausibly describe the
        same real place.

        See dipstick58 Bug 3: a top_attractions item titled "Telluride
        Mountain Village Gondola" and a scenic_drives item titled "Free
        Gondola to Mountain Village" are the same real, free town<->resort
        gondola, described two different ways by independent AI generation
        passes for the same destination. An exact normalized-string match
        (the prior behavior) never catches this. Token-set overlap does, but
        must guard against merging two real, distinct places that happen to
        share every word but one directional/temporal qualifier (e.g.
        "Sunrise Point" vs "Sunset Point") -- see
        _ATTRACTION_DEDUP_DIFFERENTIATORS.
        """
        norm_a = HTMLAssembler._normalize_attraction_name_for_dedup(name_a)
        norm_b = HTMLAssembler._normalize_attraction_name_for_dedup(name_b)
        if not norm_a or not norm_b:
            return False
        if norm_a == norm_b:
            return True

        tokens_a = HTMLAssembler._attraction_dedup_tokens(name_a)
        tokens_b = HTMLAssembler._attraction_dedup_tokens(name_b)
        if not tokens_a or not tokens_b:
            return False

        shared = tokens_a & tokens_b
        if len(shared) < 2:
            return False

        differing = (tokens_a | tokens_b) - shared
        if differing & HTMLAssembler._ATTRACTION_DEDUP_DIFFERENTIATORS:
            return False

        smaller = min(len(tokens_a), len(tokens_b))
        return (len(shared) / smaller) >= 0.66

    def _build_attractions(
        self,
        ai: dict,
        drives: list[dict[str, Any]],
        dest_name: str = "",
        *,
        dest: dict[str, Any] | None = None,
        dest_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> str:
        attrs = ai.get("top_attractions", [])
        # GH #68 multi-site grouping §5: scenic drives deferred to the
        # group base leave `drives` empty by design (url_discovery.py's
        # gate clears it) -- render a pointer instead of silently omitting
        # the whole subsection when there's nothing else on the card either.
        scenic_drive_deferred = self._category_deferred_for_render(dest, "scenic_drive")
        if not attrs and not drives:
            if scenic_drive_deferred:
                return self._group_base_pointer_html(dest, dest_by_id, "Scenic drives", icon="\U0001f5fa️")
            return ""

        # Icon and badge color map by type
        type_icons = {
            "hike": "🥾",
            "attraction": "🏛️",
            "viewpoint": "📍",
            "activity": "🎯",
            "landmark": "🗻",
            "nature": "🌲",
            "scenic": "🌄",
        }
        
        difficulty_colors = {
            "Easy": "badge-hike-easy",
            "Moderate": "badge-hike-moderate",
            "Strenuous": "badge-hike-strenuous",
        }
        
        scenic_badges = {
            "drive": "Scenic Drive",
            "viewpoint": "Viewpoint",
            "aerial": "Aerial",
            "day_trip": "Day Trip",
            "historic": "Historic Route",
        }

        # "Must-See" is a deterministic badge, not the LLM's opinion: the model's
        # own must_see flag is unverified and identical to a word the system
        # prompt otherwise bans from prose ("must-see"). It qualifies here only
        # when backed by verified rating/review data attached during URL
        # discovery, same as the general >=4.0/10-reviews inclusion bar but
        # stricter -- capped to the top MUST_SEE_MAX_BADGES per destination so
        # the badge stays meaningful (see docs/design/url-discovery-and-audit.md).
        def _qualifies_for_must_see_badge(candidate: dict[str, Any]) -> bool:
            rating = candidate.get("rating")
            votes = candidate.get("votes")
            try:
                rating_f = float(rating) if rating not in (None, "") else None
            except (TypeError, ValueError):
                rating_f = None
            try:
                votes_i = int(votes) if votes not in (None, "") else None
            except (TypeError, ValueError):
                votes_i = None
            return (
                rating_f is not None
                and rating_f >= self._MUST_SEE_MIN_RATING
                and votes_i is not None
                and votes_i >= self._MUST_SEE_MIN_VOTES
            )

        qualifying = [a for a in attrs if isinstance(a, dict) and _qualifies_for_must_see_badge(a)]
        qualifying.sort(key=lambda a: (-(float(a.get("rating") or 0)), -(int(a.get("votes") or 0))))
        must_see_ids = {id(a) for a in qualifying[: self._MUST_SEE_MAX_BADGES]}

        # Render order: everything else up front, hikes/trails in the middle,
        # scenic drives last (drives are already appended after this loop via
        # the separate `drives` param, so they're covered without a bucket
        # here). A stable sort keeps items within each bucket in their
        # existing relative order -- this only changes render position, not
        # which items render, their badges, or their content.
        #
        # dipstick60 Bug 2 (real data): type == "hike" alone under-detects.
        # E.g. Canyonlands' "Mesa Arch" and Telluride's "Bridal Veil Falls"
        # both carry a hike-difficulty badge (Easy/Moderate/Strenuous, from
        # `difficulty`) and a walking duration -- clearly hikes -- yet their
        # `type` is "viewpoint"/"attraction", not "hike", so they stayed in
        # the non-hike bucket and rendered first/interleaved instead of in
        # the middle. `difficulty` is itself a hike-specific field: see
        # url_discovery.py's trail-miles-threshold demotion, which explicitly
        # clears `difficulty`/`elevation_gain_feet` when it reclassifies an
        # item away from being a hike. A recognized hike-difficulty value is
        # therefore just as reliable a hike signal as type == "hike".
        def _attraction_render_bucket(candidate: Any) -> int:
            if not isinstance(candidate, dict):
                return 0
            item_type = str(candidate.get("type", "attraction") or "attraction").lower()
            if item_type == "hike":
                return 1
            if str(candidate.get("difficulty", "") or "") in difficulty_colors:
                return 1
            return 0

        ordered_attrs = sorted(attrs, key=_attraction_render_bucket) if isinstance(attrs, list) else attrs

        attraction_rows: list[str] = []
        rendered_attraction_names: list[str] = []
        for attr in ordered_attrs:
            url, _is_map_fallback = self._select_preferred_external_link(attr, section="attraction")
            if not url and not self._should_render_without_url(attr, section="attraction"):
                continue
            rendered_attraction_names.append(str(attr.get("name", "") or ""))
            attr_type = attr.get("type", "attraction").lower()
            icon = type_icons.get(attr_type, "📍")
            attr_name = html_escape.escape(str(attr.get("name", "") or ""))
            if url:
                source_icon = self._link_source_icon(url)
                name_html = (
                    f'<a href="{self._safe_href(url)}" class="attr-link" target="_blank" rel="noopener">{attr_name}</a>'
                    f'<span class="attr-external-link" title="link source">{source_icon}</span>'
                )
                maps_corner_html = self._maps_corner_link_html(attr, url)
            else:
                name_html = attr_name
                maps_corner_html = ""
            
            diff = attr.get("difficulty", "")
            dur_raw = str(attr.get("duration", "") or "").strip()
            dur = dur_raw if dur_raw and dur_raw.lower() != "n/a" and dur_raw.lower() != "na" else ""
            distance_raw = attr.get("distance_miles")
            distance_value = str(distance_raw).strip() if distance_raw not in (None, "") else ""
            if distance_value and distance_value.lower() not in {"n/a", "na"}:
                try:
                    distance_display = f"{float(float(distance_value)):.1f} mi" if float(distance_value) % 1 else f"{float(distance_value):.0f} mi"
                except (TypeError, ValueError):
                    distance_display = f"{distance_value} mi"
            else:
                distance_display = ""
            elevation_raw = attr.get("elevation_gain_feet")
            elevation_value = str(elevation_raw).strip() if elevation_raw not in (None, "") else ""
            if elevation_value and elevation_value.lower() not in {"n/a", "na"}:
                try:
                    elevation_display = f"{int(float(elevation_value))} ft"
                except (TypeError, ValueError):
                    elevation_display = f"{elevation_value} ft"
            else:
                elevation_display = ""
            must = id(attr) in must_see_ids
            note = attr.get("practical_note", "")

            attr_rating_value = attr.get("rating")
            attr_rating_text = str(attr.get("raw_rating") or "").strip()
            if not attr_rating_text and attr_rating_value is not None:
                try:
                    attr_rating_text = f"{float(attr_rating_value):.1f}"
                except (TypeError, ValueError):
                    attr_rating_text = str(attr_rating_value).strip()

            diff_class = difficulty_colors.get(diff, "")
            diff_html = f'<span class="badge {diff_class}">{diff}</span>' if diff and diff_class else ""
            dur_html = f'<span class="badge badge-duration">{dur}</span>' if dur else ""
            distance_html = f'<span class="badge badge-distance">{html_escape.escape(distance_display)}</span>' if distance_display else ""
            elevation_html = f'<span class="badge badge-elevation">{html_escape.escape(elevation_display)}</span>' if elevation_display else ""
            rating_html = f'<span class="badge badge-rating">★ {html_escape.escape(attr_rating_text)}</span>' if attr_rating_text else ""
            must_html = '<span class="badge badge-mustsee">Must-See</span>' if must else ""
            # Seed attractions are the traveler's own explicit requests
            # (docs/requirements.md §3.4), distinct from Must-See's verified-
            # quality signal -- surfaced first since it's the more personal claim.
            seed_html = '<span class="badge badge-seed">Your Pick</span>' if attr.get("is_seed") else ""
            # Rendered without a verified source link (see _should_render_without_url):
            # promoted anyway because we had enough descriptive metadata to be useful,
            # but flagged so it's visually distinguishable from a linked, source-checked entry.
            caution_html = (
                '<span class="badge badge-caution" title="No verified source link found for this recommendation">⚠ Unverified</span>'
                if not url else ""
            )
            note_html = f'<span class="practical-note">📌 {note}</span>' if note else ""

            attraction_rows.append(
                f'  <div class="attr-item">'
                f'<div class="attr-header attr-header-inline">'
                f'<span class="attr-icon">{icon}</span>'
                f'<span class="attr-name">{name_html}</span>'
                f'<div class="attr-badges attr-badges-inline">'
                f'{seed_html}'
                f'{caution_html}'
                f'{must_html}'
                f'{rating_html}'
                f'{diff_html}'
                f'{dur_html}'
                f'{distance_html}'
                f'{elevation_html}'
                f'</div>'
                f'</div>'
                f'<span class="attr-desc">{html_escape.escape(str(attr.get("description", "") or ""))}</span>'
                f'{note_html}'
                f'{maps_corner_html}'
                f'</div>\n'
            )

        if not attraction_rows and not drives:
            if scenic_drive_deferred:
                return self._group_base_pointer_html(dest, dest_by_id, "Scenic drives", icon="\U0001f5fa️")
            return ""

        html = '<div class="card attractions-card">\n<h3>🏔️ Top Attractions</h3>\n<div class="attraction-list">\n'
        html += "".join(attraction_rows)

        for drive in drives:
            title = drive.get("title", "")
            if not title:
                continue
            # A scenic drive that shares its name -- or plausibly describes
            # the same real place under different wording (see
            # _attraction_names_are_duplicates) -- with an already-rendered
            # attraction (e.g. "Inspiration Point" appearing as both a
            # top_attraction and a scenic-drive item for the same destination)
            # must not render twice -- the attraction card, which carries a
            # real link/description, wins; the redundant drive card is dropped.
            if any(
                self._attraction_names_are_duplicates(title, rendered_name)
                for rendered_name in rendered_attraction_names
            ):
                continue
            safe = title.replace('"', '&quot;').replace("'", "&#39;")
            category = scenic_badges.get(drive.get("category", "drive"), "Scenic Drive")
            duration = drive.get("distance_or_duration", "")
            description = drive.get("description", "")
            # PR-003: card shows first-sentence teaser; popup shows full description via DRIVE_DESCRIPTIONS
            first_sentence = self._first_sentence(description)
            card_desc = (first_sentence + ".") if first_sentence and not first_sentence.endswith((".", "!", "?")) else first_sentence
            duration_html = (
                f'<span class="badge badge-duration">{html_escape.escape(duration)}</span>'
                if duration else ""
            )
            link_html = (
                f'<a href="#" class="attr-link drive-link" data-drive-title="{safe}">'
                f'{html_escape.escape(title)}</a>'
            )

            html += (
                '  <div class="attr-item attr-drive-item">'
                '<div class="attr-header attr-header-inline">'
                '<span class="attr-icon">🚗</span>'
                f'<span class="attr-name">{link_html}</span>'
                '<div class="attr-badges attr-badges-inline">'
                f'<span class="badge badge-scenic">{category}</span>'
                f'{duration_html}'
                '</div>'
                '</div>'
                f'<span class="attr-desc">{html_escape.escape(str(card_desc or ""))}</span>'
                '</div>\n'
            )
        if not drives and scenic_drive_deferred:
            html += self._group_base_pointer_html(dest, dest_by_id, "Scenic drives", icon="\U0001f5fa️")
        html += '</div>\n</div>\n'
        return html

    def _build_schedule(self, ai: dict, drives: list, dest_name: str) -> str:
        schedule = ai.get("possible_daily_schedule", [])
        # Renderer is fail-closed for schedule content. If upstream generation
        # produced no normalized schedule, omit this card rather than inventing
        # day plans that can conflict with travel-window rationalization.
        if not schedule:
            return ""
        
        html = '<div class="card schedule-card">\n<h3>⏰ Possible Daily Schedule</h3>\n'
        period_icons = {"morning": "🌅", "afternoon": "☀️", "evening": "🌙", "plan": "🗺️"}

        if isinstance(schedule, list) and schedule and isinstance(schedule[0], dict):
            day_count = sum(1 for day in schedule if day.get("periods"))
            grid_class = " schedule-days-two-col" if day_count > 1 else ""
            html += f'<div class="schedule-days{grid_class}">\n'
            for day in schedule:
                periods = day.get("periods", [])
                if not periods:
                    continue
                html += f'  <div class="schedule-day">\n'
                html += f'    <div class="schedule-day-title">{html_escape.escape(day.get("day_label", "Day"))}</div>\n'
                for period in periods:
                    label = str(period.get("period", "Plan")).title()
                    content = str(period.get("summary", "")).strip()
                    if not content:
                        continue
                    html += f'    <div class="schedule-period">\n'
                    icon = period_icons.get(label.lower(), "🗺️")
                    html += f'      <div class="schedule-line"><span class="schedule-time"><span class="schedule-icon">{icon}</span> {html_escape.escape(label)}</span><span class="schedule-summary">{html_escape.escape(content)}</span></div>\n'
                    html += f'    </div>\n'
                html += '  </div>\n'
            html += '</div>\n'
        elif isinstance(schedule, dict):
            html += '<div class="schedule-day">\n'
            html += '  <div class="schedule-day-title">Day 1</div>\n'
            for period in ["morning", "afternoon", "evening"]:
                content = str(schedule.get(period, "")).strip()
                if not content:
                    continue
                html += f'  <div class="schedule-period">\n'
                icon = period_icons.get(period, "🗺️")
                html += f'    <div class="schedule-line"><span class="schedule-time"><span class="schedule-icon">{icon}</span> {period.title()}</span><span class="schedule-summary">{html_escape.escape(content)}</span></div>\n'
                html += f'  </div>\n'
            html += '</div>\n'
        else:
            return ""

        html += '</div>\n'
        return html

    def _build_events(self, events: dict, dest_name: str) -> str:
        import logging
        logger = logging.getLogger(__name__)
        
        if not events:
            return ""
        
        html = '<div class="card events-card">\n'
        if events.get("has_events"):
            html += '<h3>🎭 Cultural Events &amp; Entertainment</h3>\n'
            events_intro = events.get("ambient_scene", "") or events.get("intro", "")
            if events_intro:
                html += f'<p class="events-intro">{events_intro}</p>\n'
            html += '<div class="events-list">\n'
            for ev in events.get("events", []):
                url = self._normalize_external_url(ev.get("url", ""))
                if not url:
                    # Omit fallback link when no canonical event URL is available;
                    # generic search queries fail strict single-result validation.
                    pass
                name_html = (
                    f'<a href="{self._safe_href(url)}" class="event-link" target="_blank" rel="noopener">{html_escape.escape(str(ev.get("name", "") or ""))}</a>'
                    if url else ev.get("name", "")
                )
                date_str = ev.get("dates_in_range", "") or ev.get("date", "")
                venue_str = ev.get("venue", "")
                admission_str = ev.get("admission", "")
                
                html += (
                    f'  <div class="event-item">\n'
                    f'    <div class="events-subcard">\n'
                    f'      <strong>{name_html}</strong><br/>\n'
                    f'      <span class="events-date-range">{date_str}</span><br/>\n'
                    f'      📍 {venue_str}<br/>\n'
                    f'      💵 {admission_str}<br/>\n'
                    f'    </div>\n'
                )
                html += '  </div>\n'
            html += '</div>\n'
        else:
            # Fallback: no confirmed ticketed events
            html += '<h3>🎭 Cultural Events</h3>\n'
            honest = events.get("honest_assessment", "")
            if not honest:
                logger.warning("No honest_assessment for '%s' (events=%s)", dest_name, events)
                honest = "No ticketed events were confidently verified for these dates. Check visitor center and local calendars close to travel dates."
            html += f'<p>{honest}</p>\n'
            tip = events.get("local_tip", "")
            if tip:
                html += (
                    f'<p class="local-tip"><strong>Local tip:</strong> {html_escape.escape(str(tip))}</p>\n'
                )
        html += '</div>\n'
        return html

    _MUST_SEE_MIN_RATING = 4.5
    _MUST_SEE_MIN_VOTES = 20
    _MUST_SEE_MAX_BADGES = 2

    _RATING_TEXT_PATTERN = re.compile(
        r"(?:rated\s+|with\s+an?\s+(?:average\s+)?rating\s+of\s+)?"
        r"(\d+(?:\.\d+)?)\s*(?:/\s*5\b|out\s+of\s+5\b|stars?\b)"
        r"(?:\s*\((\d[\d,]*)\s*reviews?\))?",
        re.IGNORECASE,
    )

    @staticmethod
    def _extract_rating_badge_and_clean_text(text: str) -> tuple[str, str]:
        """Pull a "4.5 stars (230 reviews)"-style rating mention out of free text
        into a badge value, and return the text with that mention removed.

        En-route stops never went through the same rating->badge extraction
        attractions/restaurants get, so a rating baked into AI-generated prose
        stayed there verbatim instead of becoming a ★ badge like everywhere else.
        """
        raw = str(text or "").strip()
        if not raw:
            return "", raw
        match = HTMLAssembler._RATING_TEXT_PATTERN.search(raw)
        if not match:
            return "", raw
        rating_value = match.group(1)
        votes = match.group(2)
        badge_text = f"{rating_value} ({votes} reviews)" if votes else rating_value
        cleaned = raw[: match.start()] + raw[match.end():]
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        cleaned = re.sub(r"\(\s*\)", "", cleaned)
        cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
        cleaned = cleaned.strip(" -:|,;.")
        return badge_text, cleaned

    @staticmethod
    def _sanitize_restaurant_display_name(
        name: str, *, rating_text: str = "", price: str = "", cuisine: str = ""
    ) -> str:
        """Strip rating/price/cuisine decoration from a harvested restaurant
        name before it's shown as the card title, since that same information
        is already rendered separately as badges (dipstick55 Theme D: ratings
        were appearing in both the title text AND the rating badge). The
        harvest source glues these on *after* the real name, in the middle of
        the string rather than strictly at the very end (e.g. "Cliffside
        Restaurant 4.4/5 $$$ American" or "Painted Pony Restaurant 4.5/5 $$$
        Contemporary American" -- rating, then price, then a whole trailing
        cuisine phrase that may not match the parsed `cuisine` field
        word-for-word). A restaurant name essentially never organically
        contains something matching a "4.4/5"-style rating pattern, so once
        that pattern is found, everything from there to the end of the string
        is decoration -- truncate there rather than trying to strip each
        piece (rating/price/cuisine-phrase) individually, which would leave
        stray fragments like "Contemporary" behind.
        """
        cleaned = str(name or "").strip()
        if not cleaned:
            return ""

        truncated = False

        rating_text = str(rating_text or "").strip()
        if rating_text:
            rating_num_match = re.match(r"(\d+(?:\.\d+)?)", rating_text)
            rating_num = rating_num_match.group(1) if rating_num_match else None
            if rating_num:
                # Match the rating value in whatever textual form it takes in
                # the name (bare number, "X/5", "X out of 5", "X stars"), not
                # just the exact raw_rating string, so this still catches it
                # even if the two representations differ slightly.
                match = re.search(
                    rf"\b{re.escape(rating_num)}\s*(?:/\s*5\b|out\s+of\s+5\b|stars?\b)?",
                    cleaned,
                    flags=re.IGNORECASE,
                )
                if match and match.start() > 0:
                    cleaned = cleaned[: match.start()]
                    truncated = True

        price = str(price or "").strip()
        if not truncated and price and re.fullmatch(r"[\$#]{1,4}", price) and price in cleaned:
            idx = cleaned.find(price)
            if idx > 0:
                cleaned = cleaned[:idx]
                truncated = True

        cuisine = str(cuisine or "").strip()
        if not truncated and cuisine:
            # Only reached when neither rating nor price truncation already
            # found a decoration boundary -- at that point there's no
            # definitive split point, so this is a heuristic guess, applied
            # only as a last resort. Once rating/price truncation has already
            # produced a clean name, leave it alone: e.g. "Wild Rabbit Cafe"
            # with cuisine="Cafe" must not have "Cafe" stripped just because
            # it happens to match the (already-past) cuisine badge value.
            #
            # That protection only works when rating/price truncation actually
            # ran, though. A name that never had rating/price glued onto it in
            # the first place (already-clean harvested names, e.g. "Book Club
            # Bistro" with cuisine="Bistro") leaves `truncated` False too, so
            # without further guarding this would still wrongly chop the real
            # trailing word off the name (dipstick58: "Book Club Bistro" ->
            # "Book Club"). Require independent evidence that *something*
            # decoration-shaped (a rating pattern or a price-symbol run) is
            # still glued into the name before trusting the cuisine match at
            # the tail is decoration rather than the restaurant's own name.
            has_glued_decoration = bool(
                re.search(r"\d+(?:\.\d+)?\s*(?:/\s*5\b|out\s+of\s+5\b|stars?\b)", cleaned, flags=re.IGNORECASE)
                or re.search(r"[\$#]{1,4}", cleaned)
            )
            if has_glued_decoration:
                cleaned = re.sub(
                    rf"\s*{re.escape(cuisine)}\s*$", "", cleaned, flags=re.IGNORECASE
                )

        cleaned = re.sub(
            r"\s*(?:[-–—]\s*)?(?:\d+(?:\.\d+)?\s*(?:/\s*5|stars?)\s*(?:[\$#]{1,4})?|[\$#]{1,4}|(?:[\$#]{1,4})\s*\d+(?:\.\d+)?\s*(?:/\s*5|stars?)|[-–—]\s*\d+(?:\.\d+)?\s*(?:/\s*5|stars?))\s*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:|,;")
        return cleaned

    @staticmethod
    def _should_render_without_url(item: dict[str, Any], *, section: str) -> bool:
        if not isinstance(item, dict):
            return False
        name = str(item.get("name", "") or "").strip()
        description = str(item.get("description", "") or "").strip()
        practical_note = str(item.get("practical_note", "") or "").strip()
        text_blob = " ".join(part for part in (description, practical_note) if part).strip()
        if not text_blob and not name:
            return False

        if section == "restaurant":
            if not name:
                return False
            if item.get("maps_url") and HTMLAssembler._is_maps_search_url(str(item.get("maps_url", ""))):
                return False
            if description.lower() in {"source", "source maps", "maps", "locally surfaced dinner option", "locally surfaced dinner option."}:
                return True
            if any(marker in description.lower() for marker in ("missing links", "fallback map", "source maps", "google.com/maps/search", "no link")):
                return False
            return True

        if section == "attraction":
            # Seed attractions are user-requested anchors (requirements.md §3.4) and
            # must never silently vanish just because they lack a rich enough
            # description or metadata to clear the generic no-url render bar.
            if item.get("is_seed") and name:
                return True
            metadata_fields = [
                item.get("difficulty", ""),
                item.get("distance_miles", ""),
                item.get("elevation_gain_feet", ""),
                item.get("duration", ""),
                item.get("must_see", ""),
                item.get("practical_note", ""),
            ]
            if any(str(value).strip() for value in metadata_fields if value not in (None, "", "N/A", "n/a", "NA", "na")):
                return True
            if not text_blob:
                return False
            lower = text_blob.lower()
            if any(marker in lower for marker in ("source maps", "fallback map", "missing links", "google.com/maps/search", "maps search", "no link")):
                return False
            return len(re.findall(r"[A-Za-z0-9]+", text_blob)) >= 8

        if section == "en_route_stop":
            if not text_blob:
                return bool(name)
            token_count = len(re.findall(r"[A-Za-z0-9]+", text_blob))
            return token_count >= 6

        return bool(text_blob and len(re.findall(r"[A-Za-z0-9]+", text_blob)) >= 6)

    def _build_restaurants(
        self,
        ai: dict,
        dest_name: str,
        *,
        dest: dict[str, Any] | None = None,
        dest_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> str:
        rests = ai.get("dinner_recommendations", [])
        if not rests:
            # GH #68 multi-site grouping §5: restaurants deferred to the
            # group base leave dinner_recommendations empty by design
            # (url_discovery.py's gate clears it) -- render a pointer
            # instead of just omitting the section.
            if self._category_deferred_for_render(dest, "restaurant"):
                return self._group_base_pointer_html(dest, dest_by_id, "Dinner recommendations", icon="\U0001f37d️")
            return ""
        rows: list[str] = []
        for rest in rests:
            # Only render canonical discovered URLs as primary restaurant links.
            # Ambiguous maps/search fallbacks remain metadata for optional manual use.
            url, _is_map_fallback = self._select_preferred_external_link(rest, section="restaurant")
            if not url:
                maps_only = bool(rest.get("maps_url") and self._is_maps_search_url(str(rest.get("maps_url", ""))))
                if maps_only:
                    continue
                if not self._should_render_without_url(rest, section="restaurant"):
                    continue
            rest_name_raw = str(rest.get("name", "") or "")
            cuisine = rest.get("cuisine", "")
            price = str(rest.get("price_range", "") or rest.get("price", "") or "").strip()
            rating_value = rest.get("rating")
            raw_rating = str(rest.get("raw_rating") or "").strip()
            rating_text = raw_rating
            if not rating_text and rating_value is not None:
                try:
                    rating_text = f"{float(rating_value):.1f}"
                except (TypeError, ValueError):
                    rating_text = str(rating_value).strip()
            display_name = self._sanitize_restaurant_display_name(
                rest_name_raw, rating_text=rating_text, price=price, cuisine=str(cuisine or "")
            )
            rest_name = html_escape.escape(display_name or rest_name_raw)
            if url:
                source_icon = self._link_source_icon(url)
                name_html = (
                    f'<a href="{self._safe_href(url)}" target="_blank" rel="noopener">{rest_name}</a>'
                    f' <span class="attr-external-link" title="link source">{source_icon}</span>'
                )
            else:
                name_html = rest_name
            desc = self._restaurant_description(rest, dest_name, bool(url), _is_map_fallback)
            reserve = rest.get("reserve_recommended", False)

            # Cuisine badge
            cuisine_badge = f'<span class="badge cuisine-badge">{cuisine}</span>' if cuisine else ""

            # Reserve recommendation badge
            reserve_badge = '<span class="badge badge-reserve">Reservations Recommended</span>' if reserve else ""
            price_badge = f'<span class="badge badge-price">{html_escape.escape(price)}</span>' if price else ""
            rating_badge = f'<span class="badge badge-rating">★ {html_escape.escape(rating_text)}</span>' if rating_text else ""
            caution_badge = (
                '<span class="badge badge-caution" title="No verified source link found for this recommendation">⚠ Unverified</span>'
                if not url else ""
            )

            desc_html = f'    <span class="rest-desc">{html_escape.escape(desc)}</span>\n' if desc else ""
            maps_corner_html = self._maps_corner_link_html(rest, url) if url else ""
            rows.append(
                f'  <div class="rest-item">\n'
                f'    <div class="rest-header rest-header-inline">\n'
                f'      <span class="rest-name"><span class="rest-icon">🍽️</span> {name_html}</span>\n'
                f'      <div class="rest-badges">\n'
                f'        {caution_badge}\n'
                f'        {cuisine_badge}\n'
                f'        {rating_badge}\n'
                f'        {price_badge}\n'
                f'        {reserve_badge}\n'
                f'      </div>\n'
                f'    </div>\n'
                f'{desc_html}'
                f'{maps_corner_html}'
                f'  </div>\n'
            )

        html = '<div class="card restaurants-card">\n<h3>🍽️ Dinner Recommendations</h3>\n<div class="restaurant-list">\n'
        html += "".join(rows)
        html += '</div>\n</div>\n'
        return html

    @staticmethod
    def _restaurant_description(rest: dict[str, Any], dest_name: str, has_url: bool, is_map_fallback: bool) -> str:
        desc = str(rest.get("description", "") or "").strip()
        name = str(rest.get("name", "") or "").strip()
        name = HTMLAssembler._sanitize_restaurant_display_name(name) or name
        cuisine = str(rest.get("cuisine", "") or "").strip()
        desc_lower = desc.lower()

        if desc:
            desc = re.sub(r"\bLinks?\s*[:\-].*$", "", desc, flags=re.IGNORECASE).strip(" -:|,;")
            desc = re.sub(r"\b(?:Source|Maps?)\b\s*$", "", desc, flags=re.IGNORECASE).strip(" -:|,;")
            desc = re.sub(r"(?i)\b(?:rating|review|stars?)\b\s*[:\-]?\s*\d+(?:\.\d+)?\s*(?:/\s*5|stars?)", "", desc)
            desc = re.sub(r"(?i)\b(?:price|prices?)\b\s*[:\-]?\s*[\$#]{1,4}", "", desc)
            desc = re.sub(r"\b\d+(?:\.\d+)?\s*(?:/\s*5|stars?)\b", "", desc, flags=re.IGNORECASE)
            desc = re.sub(r"(?:^|[\s,;:])(?:[\$#]{1,4})+(?:[\s,;:]|$)", " ", desc)
            desc = re.sub(r"\s+", " ", desc).strip(" -:|,;")

        synthetic = False
        if not desc:
            synthetic = True
        elif name and desc.lower() == name.lower():
            synthetic = True
        elif desc_lower in {str(cuisine).lower() for cuisine in (cuisine,)} and cuisine:
            synthetic = True
        elif desc.lower() in {"cafe", "american", "pizza", "mexican", "italian", "bbq", "barbecue", "seafood", "sushi", "ramen", "burger", "bistro", "grill", "brewpub", "bakery", "coffee", "steakhouse", "taqueria", "brasserie", "food", "diner"}:
            synthetic = True
        elif re.search(r"\b(source|maps?|links?)\b", desc, re.IGNORECASE) and len(desc.split()) <= 8:
            synthetic = True
        elif re.fullmatch(r"(?:\d+(?:\.\d+)?\s*(?:/\s*5|stars?)\s*(?:,\s*[\$#]{1,4})?(?:,\s*[A-Za-z][A-Za-z\- ]+)?|[\$#]{1,4}\s*(?:,\s*[A-Za-z][A-Za-z\- ]+)?)", desc, flags=re.IGNORECASE):
            synthetic = True
        elif desc_lower in {
            "locally surfaced dinner option.",
            "locally surfaced dinner option",
            "local dinner option.",
            "local dinner option",
        }:
            synthetic = True

        if synthetic:
            # Fail-closed: the renderer should not invent a generic teaser by repeating the
            # restaurant name, destination name, or a fabricated "verify current hours" warning.
            # When the underlying source text is weak or missing, render the header metadata
            # without a teaser instead of manufacturing content.
            return ""

        if desc and desc.lower() not in {"source", "maps", "source maps", "links"}:
            return desc
        return ""

    @staticmethod
    def _first_sentence(text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""

        abbreviations = {
            "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "ft", "vs",
            "etc", "e.g", "i.e", "am", "pm", "us", "uk", "no", "fig",
        }

        sentence_chars = ".!?"
        for index, ch in enumerate(raw):
            if ch not in sentence_chars:
                continue
            prefix = raw[:index].rstrip()
            last_token = prefix.rsplit(" ", 1)[-1].lower().strip(" ").strip(".,;:!?()[]{}\"'") if prefix else ""
            if last_token in abbreviations:
                continue
            sentence = raw[: index + 1].strip()
            return sentence
        return raw

    def _select_preferred_external_link(self, item: dict[str, Any], *, section: str = "generic") -> tuple[str, bool]:
        canonical = self._normalize_external_url(item.get("url", ""))
        if canonical and not self._is_maps_directions_url(canonical):
            if self._is_maps_search_url(canonical):
                if section == "restaurant":
                    return canonical, False
                if section == "en_route_stop":
                    return canonical, True
                return canonical, False
            return canonical, False

        if section == "restaurant":
            maps_fallback = self._normalize_external_url(item.get("maps_url", ""))
            if maps_fallback and not self._is_maps_directions_url(maps_fallback) and not self._is_maps_search_url(maps_fallback):
                return maps_fallback, True
            if canonical and self._is_maps_directions_url(canonical):
                name = str(item.get("name", "") or "").strip()
                if name:
                    return self._build_destination_scope_maps_url(name), True
            return "", False

        if section == "attraction":
            maps_fallback = self._normalize_external_url(item.get("maps_url", ""))
            if maps_fallback and not self._is_maps_directions_url(maps_fallback) and not self._is_maps_search_url(maps_fallback):
                return maps_fallback, True
            if canonical and self._is_maps_directions_url(canonical):
                name = str(item.get("name", "") or "").strip()
                if name:
                    return self._build_destination_scope_maps_url(name), True
            return "", False

        if section == "en_route_stop":
            maps_fallback = self._normalize_external_url(item.get("maps_url", ""))
            if maps_fallback:
                if self._is_maps_directions_url(maps_fallback):
                    name = str(item.get("name", "") or "").strip()
                    if name:
                        return self._build_destination_scope_maps_url(name), True
                    return "", False
                if self._is_maps_search_url(maps_fallback):
                    if self._is_route_context_maps_search_url(maps_fallback):
                        name = str(item.get("name", "") or "").strip()
                        if name:
                            return self._build_destination_scope_maps_url(name), True
                        return "", False
                    return maps_fallback, True
                return maps_fallback, True
            if canonical and self._is_maps_directions_url(canonical):
                name = str(item.get("name", "") or "").strip()
                if name:
                    return self._build_destination_scope_maps_url(name), True
            return "", False

        return "", False

    @staticmethod
    def _link_source_icon(url: str) -> str:
        lower = str(url or "").lower()
        if "alltrails.com/trail/" in lower:
            return "🥾"
        if "google.com/maps" in lower or "maps.google.com" in lower or "maps.app.goo.gl" in lower:
            return "🗺️"
        return "🔗"

    @staticmethod
    def _looks_like_maps_url(url: str) -> bool:
        lower = str(url or "").lower()
        return "google.com/maps" in lower or "maps.google.com" in lower or "maps.app.goo.gl" in lower

    def _maps_corner_link_html(self, item: dict[str, Any], primary_url: str) -> str:
        """Small map-icon link to `item["maps_url"]`, rendered in the card's
        lower-right corner (see `.map-corner-link` in v2.5_template.html).

        Project owner's ask: when an item carries BOTH a real primary source
        URL (e.g. an NPS/official page, chosen by `_select_preferred_external_link`
        as the card's main link) AND a separate `maps_url`, the maps_url was
        previously discarded entirely -- there was no way to reach it from the
        card. This surfaces it, but only when it's a genuinely distinct,
        useful destination: skipped when there's no maps_url, when the primary
        link IS already that maps_url (the `_select_preferred_external_link`
        maps-fallback case), when the primary link is itself already some
        other Google Maps URL, or when maps_url is only a generic text-query
        search link (`_is_maps_search_url`) -- ambiguous and not guaranteed to
        land on the right place, so `_select_preferred_external_link` already
        keeps it out of the primary-link decision for these sections and this
        icon respects the same call (see e.g.
        test_build_restaurants_prefers_discovered_url_over_maps_query, which
        asserts a maps/search fallback stays out of the HTML entirely once a
        real URL is already showing).
        """
        maps_url = self._normalize_external_url(item.get("maps_url", ""))
        if not maps_url:
            return ""
        if self._is_maps_search_url(maps_url):
            return ""
        normalized_primary = self._normalize_external_url(primary_url)
        if not normalized_primary:
            return ""
        if maps_url == normalized_primary:
            return ""
        if self._looks_like_maps_url(normalized_primary):
            return ""
        return (
            f'<a href="{self._safe_href(maps_url)}" class="map-corner-link" target="_blank" '
            f'rel="noopener" title="Open in Google Maps" aria-label="Open in Google Maps">🗺️</a>'
        )

    @staticmethod
    def _is_maps_directions_url(url: str) -> bool:
        lower = str(url or "").lower()
        return "google.com/maps/dir/" in lower or "maps.google.com/maps/dir/" in lower

    @staticmethod
    def _is_maps_search_url(url: str) -> bool:
        lower = str(url or "").lower()
        if "google.com/maps/search" in lower:
            return True
        return "maps.google.com" in lower and "?q=" in lower

    @staticmethod
    def _is_route_context_maps_search_url(url: str) -> bool:
        try:
            parsed = urlparse(str(url or ""))
            query = parse_qs(parsed.query)
        except Exception:
            return False
        for key in ("query", "q"):
            values = query.get(key, [])
            for value in values:
                lowered = str(value or "").lower()
                if "route from" in lowered:
                    return True
        return False

    # Packing-summary items are short, free-text AI phrasings generated
    # independently per destination (e.g. "layered clothing" vs "layers for
    # fluctuating temperatures" vs "warm layers" all mean the exact same
    # actionable advice). Unlike _attraction_names_are_duplicates (built for
    # real-world place names, matched by shared name tokens), these often
    # share zero tokens, so a small curated canonicalization is used
    # instead. Kept conservative: only merges phrasings that are genuinely
    # the same advice, not ones that are merely similar-sounding but
    # usefully distinct -- "comfortable walking shoes" (a plaza-walking
    # city) stays separate from "hiking boots" (a trail-heavy park), since
    # that's different real advice, not different wording of the same
    # advice; "waterproof jacket" stays separate from generic layers/light-
    # jacket advice since it signals expected rain, a materially different
    # packing reason than temperature swings.
    _PACKING_ITEM_CANONICAL_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("Camera", ("camera",)),
        ("Layers / light jacket (temperature swings)", ("layer", "light jacket")),
        ("Hiking boots/shoes", ("hiking boot", "hiking shoe")),
    )

    @classmethod
    def _canonicalize_packing_item(cls, item: str) -> str:
        lowered = item.lower()
        for canonical, triggers in cls._PACKING_ITEM_CANONICAL_GROUPS:
            if any(trigger in lowered for trigger in triggers):
                return canonical
        return item

    def _build_packing_summary(self, destinations: list[dict[str, Any]]) -> str:
        by_item: dict[str, set[str]] = {}
        for dest in destinations:
            env = dest.get("ai_content", {}).get("expected_environment", {})
            if not isinstance(env, dict):
                continue
            for raw_item in env.get("what_to_pack", []) or []:
                item = str(raw_item).strip()
                if not item:
                    continue
                item = self._canonicalize_packing_item(item)
                by_item.setdefault(item, set()).add(dest.get("name", ""))

        if not by_item:
            return ""

        html = '<section class="dest-section pack-summary-section">\n'
        html += '  <div class="card pack-summary-card">\n'
        html += '    <h3>🎒 Packing Summary (Trip-Wide)</h3>\n'
        html += '    <p class="pack-summary-intro">Here\'s what to bring and where it\'s needed:</p>\n'
        html += '    <ul class="pack-summary-list">\n'
        for item in sorted(by_item.keys(), key=str.lower):
            places = ", ".join(sorted(p for p in by_item[item] if p))
            html += f'      <li><strong>{html_escape.escape(item)}</strong>'
            if places:
                html += f' <span class="pack-summary-places">({html_escape.escape(places)})</span>'
            html += '</li>\n'
        html += '    </ul>\n'
        html += '  </div>\n'
        html += '</section>\n'
        return html

    def _build_generator_footer(self, trip: dict[str, Any]) -> str:
        meta = trip.get("_meta", {})
        version = meta.get("generator_version", "")
        broken_link_issue_link = (
            "https://github.com/flysoftware-git/road-trip-generator/issues/new"
            "?template=broken-link-report.yml&labels=bug"
        )
        feedback_issue_link = (
            "https://github.com/flysoftware-git/road-trip-generator/issues/new"
            "?template=itinerary-feedback.yml"
        )
        timestamp = meta.get("generated_at_utc")
        if timestamp:
            try:
                dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                shown_time = dt.strftime("%Y-%m-%d %H:%M UTC")
            except ValueError:
                shown_time = str(timestamp)
        else:
            shown_time = "unknown"
        return (
            '<footer class="generator-footer" '
            'style="margin:1.25rem auto 2.5rem;padding:0 1rem;max-width:72rem;">'
            '<div style="border-top:1px solid #e8dcc8;padding-top:0.85rem;'
            'font-size:0.8rem;color:#8B6347;line-height:1.5;text-align:center;">'
            'Generated by '
            '<a href="https://github.com/flysoftware-git/road-trip-generator" '
            '>Road Trip Itinerary Generator</a>'
            f' v{html_escape.escape(str(version))} · Itinerary output: {html_escape.escape(shown_time)}'
            '<div style="margin-top:0.35rem;">'
            'Issue reporting: '
            f'<a href="{broken_link_issue_link}">'
            'Report broken links</a>'
            ' · '
            f'<a href="{feedback_issue_link}">'
            'Share itinerary feedback</a>'
            '</div>'
            '</div>'
            '</footer>'
        )

    def _inject_generator_footer(self, html: str, trip: dict[str, Any]) -> str:
        footer_html = self._build_generator_footer(trip)
        if "<!--GENERATOR_FOOTER-->" in html:
            return html.replace("<!--GENERATOR_FOOTER-->", footer_html)
        if "<!-- DRIVE INFO MODAL -->" in html:
            return html.replace("<!-- DRIVE INFO MODAL -->", footer_html + "\n\n    <!-- DRIVE INFO MODAL -->", 1)
        if "</body>" in html:
            return html.replace("</body>", footer_html + "\n</body>", 1)
        return html + "\n" + footer_html

    def _build_drive_descriptions(self, destinations: list[dict]) -> dict[str, Any]:
        """Build DRIVE_DESCRIPTIONS keyed by raw title string (matches template JS lookup)."""
        result: dict[str, Any] = {}
        for dest in destinations:
            dest_name = str(dest.get("name", "") or "").strip()
            ai = dest.get("ai_content", {})
            attraction_names = [
                str(attr.get("name", "") or "")
                for attr in ai.get("top_attractions", [])
                if isinstance(attr, dict)
            ]
            for drive in dest.get("scenic_drives", []):
                key = drive.get("title", "")
                # Must mirror _build_attractions' inline drive-link dedup: a
                # drive sharing its name -- or plausibly describing the same
                # real place under different wording, see
                # _attraction_names_are_duplicates -- with an already-rendered
                # attraction never gets a modal-trigger button there, so it
                # can't get a DRIVE_DESCRIPTIONS entry here either -- otherwise
                # this produces an orphan key with no button to open it (found
                # 2026-08-15: validator caught 'Potash Road' doing exactly
                # this for a Moab run).
                if key and any(
                    self._attraction_names_are_duplicates(key, name) for name in attraction_names
                ):
                    continue
                entry = {
                    "title": drive.get("title", ""),
                    "category": drive.get("category", "scenic_drive"),
                    "distance_or_duration": drive.get("distance_or_duration", ""),
                    "best_time": drive.get("best_time", ""),
                    "description": self._clean_drive_description(drive.get("description", "")),
                    "vehicle_requirement": drive.get("vehicle_requirement", ""),
                }
                info_url = self._normalize_external_url(drive.get("url", ""))
                if info_url:
                    entry["url"] = info_url
                route_map_url = self._build_scenic_drive_route_map_url(drive, dest_name)
                if route_map_url:
                    entry["route_map_url"] = route_map_url
                result[key] = entry
        return result

    def _build_scenic_drive_route_map_url(self, drive: dict[str, Any], dest_name: str) -> str:
        explicit_route = self._normalize_external_url(drive.get("route_map_url", ""))
        if self._is_maps_directions_url(explicit_route):
            return explicit_route

        drive_url = self._normalize_external_url(drive.get("url", ""))
        if self._is_maps_directions_url(drive_url):
            return drive_url

        drive_title = str(drive.get("title", "") or "").strip()
        destination = " ".join(part for part in (drive_title, dest_name) if part).strip()
        if not destination:
            return ""

        params = [
            "api=1",
            f"destination={quote(destination)}",
            "travelmode=driving",
        ]
        return "https://www.google.com/maps/dir/?" + "&".join(params)

    def _clean_drive_description(self, description: Any) -> str:
        text = str(description or "").strip()
        if not text:
            return ""
        text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
        text = re.sub(r"\b(?:template_version|generator_version|generated_at|provider|model)\s*=\s*[^;\n]+", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\bGenerated by\b[^.\n]*", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\bVersion\b\s*[:=]?\s*[^.\n]*", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\bUpdated\b\s*[:=]?\s*[^.\n]*", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(?:Attribution|Credit|License)\b\s*[:=]?\s*[^.\n]*", " ", text, flags=re.IGNORECASE)
        lines = [ln.strip() for ln in re.split(r"\n+", text) if ln.strip()]
        keep: list[str] = []
        for line in lines:
            lower = line.lower()
            if any(k in lower for k in ["attribution", "credit:", "photo by", "license:", "version", "updated", "generator"]):
                continue
            keep.append(line)
        cleaned = " ".join(keep).strip()
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return cleaned

    def _safe_href(self, url: str) -> str:
        return html_escape.escape(self._normalize_external_url(url), quote=True)

    def _normalize_external_url(self, url: Any) -> str:
        raw = str(url or "").strip()
        if not raw:
            return ""
        low = raw.lower()
        if low.startswith(("javascript:", "data:")):
            return ""
        if low.startswith("//"):
            return "https:" + raw
        if low.startswith(("http://", "https://", "mailto:")):
            return raw
        if re.match(r"^[a-z][a-z0-9+.-]*:", low):
            return ""
        return "https://" + raw

    @staticmethod
    def _looks_location_qualified(text: str) -> bool:
        lowered = (text or "").lower().strip()
        if not lowered:
            return False
        if "," in lowered:
            return True
        if any(
            term in lowered
            for term in (
                "national park",
                "state park",
                "utah",
                "colorado",
                "arizona",
                "new mexico",
                "nevada",
                "california",
            )
        ):
            return True
        if re.search(r"\bst\.?\s+[a-z]", lowered):
            return True
        return False

    @staticmethod
    def _significant_place_tokens(text: str) -> set[str]:
        tokens = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
        stop = {"the", "and", "for", "park", "national", "state", "road", "trail", "restaurant", "cafe"}
        return {t for t in tokens if len(t) >= 4 and t not in stop}

    @classmethod
    def _maps_fallback_query_text(cls, item_name: str, dest_name: str) -> str:
        item = str(item_name or "").strip()
        dest = str(dest_name or "").strip()
        if not item:
            return dest
        item_tokens = cls._significant_place_tokens(item)
        dest_tokens = cls._significant_place_tokens(dest)
        if item_tokens and dest_tokens and (item_tokens & dest_tokens):
            return item
        if cls._looks_location_qualified(item):
            return item
        return f"{item} {dest}".strip()

    def _infer_stop_type(self, stop: dict[str, Any]) -> str:
        raw = str(stop.get("type", "") or "").strip().lower()
        if raw:
            return raw
        text = f"{stop.get('name', '')} {stop.get('description', '')}".lower()
        if any(k in text for k in ["trail", "hike", "loop", "summit"]):
            return "hike"
        if any(k in text for k in ["overlook", "viewpoint", "vista"]):
            return "viewpoint"
        if any(k in text for k in ["museum", "center", "historic"]):
            return "museum"
        if any(k in text for k in ["market", "shop", "gallery"]):
            return "market"
        if any(k in text for k in ["falls", "waterfall", "river", "lake"]):
            return "waterfall"
        if any(k in text for k in ["food", "cafe", "restaurant", "bakery"]):
            return "food"
        return "attraction"

    def _build_debug_block(self, dest: dict[str, Any], trip_meta: dict[str, Any]) -> str:
        debug_payload = {
            "destination_id": dest.get("id", ""),
            "destination_name": dest.get("name", ""),
            "coordinates": {"lat": dest.get("lat"), "lng": dest.get("lng")},
            "nps_park_code": dest.get("nps_park_code"),
            "counts": {
                "images": len(dest.get("images", [])),
                "attractions": len(dest.get("ai_content", {}).get("top_attractions", [])),
                "restaurants": len(dest.get("ai_content", {}).get("dinner_recommendations", [])),
                "drives": len(dest.get("scenic_drives", [])),
                "events": len(dest.get("cultural_events", {}).get("events", [])),
            },
            "llm": {
                "provider": trip_meta.get("llm", {}).get("provider", ""),
                "model": trip_meta.get("llm", {}).get("model", ""),
            },
        }
        payload = html_escape.escape(json.dumps(debug_payload, indent=2))
        return (
            '<details class="debug-block" style="margin-top:1rem;background:#f8fafc;border:1px solid #d7dee7;padding:0.75rem;border-radius:8px;">\n'
            '  <summary style="cursor:pointer;font-weight:600;">Debug</summary>\n'
            f'  <pre style="margin-top:0.75rem;overflow:auto;white-space:pre-wrap;">{payload}</pre>\n'
            '</details>\n'
        )
