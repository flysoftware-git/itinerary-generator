"""
html_validator.py — Post-assembly HTML validation.

Checks:
  1. Div balance per destination section
  2. No orphan <script> tags outside designated blocks
  3. var DRIVE_DESCRIPTIONS present (not const)
  4. Drive modal element IDs match DRIVE_DESCRIPTIONS keys
  5. Image count >= min_per_destination per section
  6. Orphan-content rate (attractions/restaurants/en-route-stops with no URL)
     within configured thresholds
  7. No duplicate URLs within a single destination's top_attractions
  8. Attraction/trail teaser (description) completeness above a configured
     minimum ratio

Checks 6-8 (added 2026-08-15) are content-quality checks, not structural
HTML checks -- promoted from main.py's _run_quality_gate, which only ever
printed to the console and never affected validation_report.json's
pass/fail status (see that check's own docstring/comments for why). All
three are currently warnings, not errors, by design -- see config.yaml's
quality_gate section for the threshold rationale.
"""
from __future__ import annotations
import html
import logging, re
import html as html_lib
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
MIN_PER_DESTINATION_DEFAULT = 2
DEFAULT_MAX_NO_URL_ATTRACTIONS = 3
DEFAULT_MAX_NO_URL_EN_ROUTE_STOPS = 2
DEFAULT_MAX_NO_URL_RESTAURANTS = 0
DEFAULT_MAX_EMPTY_TEASER_RATIO = 0.15



def _format_removal_ratio(removed: int, kept: int) -> str:
    """Render "10 of 13 (77%)" -- the count alone does not say how bad it is."""
    candidates = removed + kept
    if candidates <= 0:
        return str(removed)
    return f"{removed} of {candidates} ({round(removed * 100 / candidates)}%)"


class HTMLValidator:
    def __init__(self, config_path: str | Path = "config.yaml") -> None:
        import yaml
        with Path(config_path).open() as f:
            cfg = yaml.safe_load(f)
        self._min_images = cfg.get("images", {}).get("min_per_destination", MIN_PER_DESTINATION_DEFAULT)
        quality_cfg = cfg.get("quality_gate", {}) or {}
        self._max_no_url_attractions = int(quality_cfg.get("max_no_url_attractions", DEFAULT_MAX_NO_URL_ATTRACTIONS))
        self._max_no_url_en_route_stops = int(quality_cfg.get("max_no_url_en_route_stops", DEFAULT_MAX_NO_URL_EN_ROUTE_STOPS))
        self._max_no_url_restaurants = int(quality_cfg.get("max_no_url_restaurants", DEFAULT_MAX_NO_URL_RESTAURANTS))
        self._max_empty_teaser_ratio = float(quality_cfg.get("max_empty_teaser_ratio", DEFAULT_MAX_EMPTY_TEASER_RATIO))

    def validate(self, html_path: str | Path, trip: dict[str, Any]) -> dict[str, Any]:
        html_path = Path(html_path)
        html = html_path.read_text(encoding="utf-8")
        errors: list[str] = []
        warnings: list[str] = []

        self._check_drive_descriptions_var(html, errors)
        self._check_drive_modal_keys(html, trip, errors)
        self._check_section_div_balance(html, trip, errors)
        self._check_script_isolation(html, warnings)
        self._check_image_counts(html, trip, errors)
        self._check_orphan_content_rate(trip, warnings)
        self._check_duplicate_urls_within_destination(trip, warnings)
        self._check_teaser_completeness(trip, warnings)

        report = {
            "html_path": str(html_path),
            "errors": errors,
            "warnings": warnings,
            "valid": len(errors) == 0,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "meta": trip.get("_meta", {}),
            "llm_usage": trip.get("_meta", {}).get("llm", {}).get("usage", {}),
        }
        if errors:
            logger.error("Validation FAILED: %d error(s)", len(errors))
            for e in errors:
                logger.error("  ✗ %s", e)
        else:
            logger.info("Validation passed ✓ (%d warning(s))", len(warnings))
        return report

    # ── Check 1: var (not const) DRIVE_DESCRIPTIONS ─────────────────────────

    def _check_drive_descriptions_var(self, html: str, errors: list[str]) -> None:
        if "var DRIVE_DESCRIPTIONS" not in html:
            if "const DRIVE_DESCRIPTIONS" in html:
                errors.append(
                    "DRIVE_DESCRIPTIONS declared with 'const' — must use 'var' for compatibility"
                )
            else:
                errors.append("DRIVE_DESCRIPTIONS not found in output HTML")

    # ── Check 2: Drive modal IDs match DRIVE_DESCRIPTIONS keys ───────────────

    def _check_drive_modal_keys(self, html: str, trip: dict[str, Any], errors: list[str]) -> None:
        # Extract keys from var DRIVE_DESCRIPTIONS = { ... }
        drive_json = self._extract_drive_descriptions_json(html)
        if not drive_json:
            return  # Already flagged by check 1
        try:
            import json
            dd = json.loads(drive_json)
        except Exception:
            errors.append("DRIVE_DESCRIPTIONS is not valid JSON — cannot validate drive modal keys")
            return

        # Extract data-drive-title attributes from HTML (template uses drive-link + data-drive-title)
        raw_modal_keys = re.findall(r'data-drive-title="([^"]+)"', html)
        modal_keys = {html_lib.unescape(k) for k in raw_modal_keys}
        dd_keys = set(dd.keys())

        orphan_modals = modal_keys - dd_keys
        missing_modals = dd_keys - modal_keys
        if orphan_modals:
            errors.append(f"Drive modal buttons with no DRIVE_DESCRIPTIONS entry: {sorted(orphan_modals)}")
        if missing_modals:
            errors.append(f"DRIVE_DESCRIPTIONS keys with no modal button: {sorted(missing_modals)}")

    def _extract_drive_descriptions_json(self, html: str) -> str:
        marker = "var DRIVE_DESCRIPTIONS"
        marker_idx = html.find(marker)
        if marker_idx == -1:
            return ""

        start = html.find("{", marker_idx)
        if start == -1:
            return ""

        depth = 0
        in_str = False
        escaped = False
        for i in range(start, len(html)):
            ch = html[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue

            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return html[start:i + 1]
        return ""

    # ── Check 3: Div balance per section ─────────────────────────────────────

    @staticmethod
    def _find_matching_div_close(html: str, search_from: int) -> int | None:
        """Depth-aware match for the </div> that closes the <div> whose opening
        tag ended at search_from. A GH#68 grouped-destination child renders as
        <div id="section-{id}" class="group-child-card">...</div> instead of a
        <section> -- it contains many of its own nested <div>s (attractions,
        restaurants, etc.), so "the first </div> encountered" would truncate
        way before the card's real end. Mirrors the JSON-brace depth counter
        above (_extract_json_object-style), just for div tags instead of {}."""
        depth = 1
        for m in re.finditer(r"<div\b[^>]*>|</div>", html[search_from:], re.IGNORECASE):
            if m.group(0).startswith("</div"):
                depth -= 1
                if depth == 0:
                    return search_from + m.end()
            else:
                depth += 1
        return None

    def _check_section_div_balance(self, html: str, trip: dict[str, Any], errors: list[str]) -> None:
        for dest in trip.get("destinations", []):
            dest_id = dest["id"]
            # Template uses id="section-{dest_id}" on either a <section> (an
            # ordinary or group-base destination) or a <div class="group-
            # child-card"> (a GH#68 grouped child nested inside its base's
            # section -- see html_assembler.py's _build_group_child_card).
            start_pat = re.compile(
                rf'<(section|div)[^>]+id="section-{re.escape(dest_id)}"[^>]*>', re.IGNORECASE
            )
            start_m = start_pat.search(html)
            end_idx: int | None = None
            if start_m:
                if start_m.group(1).lower() == "section":
                    end_m = re.search(r"</section>", html[start_m.end():])
                    end_idx = start_m.end() + end_m.end() if end_m else None
                else:
                    end_idx = self._find_matching_div_close(html, start_m.end())
            if not start_m or end_idx is None:
                errors.append(f"Could not locate section for destination '{dest_id}'")
                continue
            section_html = html[start_m.start():end_idx]
            opens = len(re.findall(r'<div\b', section_html, re.IGNORECASE))
            closes = len(re.findall(r'</div>', section_html, re.IGNORECASE))
            if opens != closes:
                errors.append(
                    f"Div balance mismatch in section '{dest_id}': "
                    f"{opens} <div> vs {closes} </div>"
                )

    # ── Check 4: Script isolation ─────────────────────────────────────────────

    def _check_script_isolation(self, html: str, warnings: list[str]) -> None:
        # Scripts should appear only inside <head> or before </body>
        # Orphan = script inside a destination section
        section_pattern = re.compile(
            r'<section[^>]+class="destination-section".*?</section>', re.DOTALL | re.IGNORECASE
        )
        for section_m in section_pattern.finditer(html):
            section_content = section_m.group()
            if '<script' in section_content.lower():
                # Extract section id for useful message
                id_m = re.search(r'id="([^"]+)"', section_content)
                section_id = id_m.group(1) if id_m else "unknown"
                warnings.append(f"Orphan <script> tag found inside section '{section_id}'")

        # A GH#68 grouped child renders as <div class="group-child-card"> nested
        # inside its base's <section>, not its own <section> -- the pattern
        # above never sees inside it, so it could hide a real orphan script.
        for child_m in re.finditer(
            r'<div[^>]+class="group-child-card"[^>]*>', html, re.IGNORECASE
        ):
            end_idx = self._find_matching_div_close(html, child_m.end())
            if end_idx is None:
                continue
            child_content = html[child_m.start():end_idx]
            if '<script' in child_content.lower():
                id_m = re.search(r'id="([^"]+)"', child_content)
                section_id = id_m.group(1) if id_m else "unknown"
                warnings.append(f"Orphan <script> tag found inside section '{section_id}'")

    # ── Check 5: Image counts ────────────────────────────────────────────────

    def _check_image_counts(self, html: str, trip: dict[str, Any], errors: list[str]) -> None:
        for dest in trip.get("destinations", []):
            count = len(dest.get("images", []))
            if count < self._min_images:
                errors.append(
                    f"Destination '{dest['id']}' has {count} image(s) "
                    f"(minimum: {self._min_images})"
                )

    # ── Check 6: Orphan-content rate (attractions/restaurants/en-route stops
    #    with no URL or maps fallback) ─────────────────────────────────────

    def _check_orphan_content_rate(self, trip: dict[str, Any], warnings: list[str]) -> None:
        """Verified-link-or-seed policy (project owner decision, 2026-08-17):
        url_discovery.py's audit_discovered_urls now REMOVES non-seed
        attractions/en-route stops/restaurants that never got a real,
        verified source URL, rather than leaving them present with an empty
        url. Two consequences for this check:

        - A seed item kept with no url (shown with the "Unverified" badge)
          is expected/acceptable noise, not a signal of a real recall/
          pipeline regression -- it must not count toward the no_url_*
          thresholds below.
        - Non-seed items with no verified url are gone from the trip data
          entirely, so no_url_* can no longer see them at all. The real
          successor signal is `removed_no_verified_url_*`, sourced from the
          `_registry_decisions` audit trail url_discovery.py records for
          every removal (rejection_reason "no_verified_url_removed") --
          checked against the same configured thresholds, since it's the
          same underlying "how much unverified content is here" concern.
        """
        no_url_attractions = 0
        no_url_restaurants = 0
        no_url_stops = 0
        removed_attractions = 0
        removed_restaurants = 0
        removed_stops = 0
        # Denominators. A bare "10 removed" is not interpretable: 10 of 40 is
        # noise, 10 of 13 means the section is gone. Counting what SURVIVED
        # alongside what was dropped makes the warning comparable across
        # destinations that differ wildly in how much they had to begin with --
        # which is how a park-calibrated threshold went unnoticed until the
        # first non-park run. See docs/design/destination-type-coverage.md.
        kept_attractions = 0
        kept_restaurants = 0
        kept_stops = 0
        for dest in trip.get("destinations", []) or []:
            ai = dest.get("ai_content", {}) if isinstance(dest.get("ai_content"), dict) else {}
            for attr in ai.get("top_attractions", []) or []:
                kept_attractions += 1
                if not attr.get("is_seed") and not str(attr.get("url", "") or attr.get("maps_url", "") or "").strip():
                    no_url_attractions += 1
            for rest in ai.get("dinner_recommendations", []) or []:
                kept_restaurants += 1
                if not str(rest.get("url", "") or "").strip():
                    no_url_restaurants += 1
            getting_here = ai.get("getting_here", {}) if isinstance(ai.get("getting_here"), dict) else {}
            for stop in getting_here.get("en_route_stops", []) or []:
                kept_stops += 1
                if not stop.get("is_seed") and not str(stop.get("url", "") or "").strip():
                    no_url_stops += 1
            for decision in dest.get("_registry_decisions", []) or []:
                if not isinstance(decision, dict):
                    continue
                if "no_verified_url_removed" not in (decision.get("rejection_reasons", []) or []):
                    continue
                section_target = str(decision.get("section_target", "") or "")
                entity_class = str(decision.get("entity_class", "") or "")
                if section_target == "dinner_recommendations" or entity_class == "restaurant":
                    removed_restaurants += 1
                elif section_target == "en_route_stops" or entity_class == "en_route_stop":
                    removed_stops += 1
                else:
                    removed_attractions += 1

        if no_url_attractions > self._max_no_url_attractions:
            warnings.append(
                f"Attractions with no URL or maps fallback: {no_url_attractions} "
                f"(threshold: {self._max_no_url_attractions})"
            )
        if no_url_restaurants > self._max_no_url_restaurants:
            warnings.append(
                f"Restaurants with no URL: {no_url_restaurants} "
                f"(threshold: {self._max_no_url_restaurants})"
            )
        if no_url_stops > self._max_no_url_en_route_stops:
            warnings.append(
                f"En-route stops with no URL: {no_url_stops} "
                f"(threshold: {self._max_no_url_en_route_stops})"
            )
        for label, removed, kept, threshold in (
            ("Attractions", removed_attractions, kept_attractions, self._max_no_url_attractions),
            ("Restaurants", removed_restaurants, kept_restaurants, self._max_no_url_restaurants),
            ("En-route stops", removed_stops, kept_stops, self._max_no_url_en_route_stops),
        ):
            if removed <= threshold:
                continue
            warnings.append(
                f"{label} removed for no verified URL: "
                f"{_format_removal_ratio(removed, kept)} (threshold: {threshold})"
            )

    # ── Check 7: Duplicate URLs within a destination's top_attractions ──────

    def _check_duplicate_urls_within_destination(self, trip: dict[str, Any], warnings: list[str]) -> None:
        for dest in trip.get("destinations", []) or []:
            ai = dest.get("ai_content", {}) if isinstance(dest.get("ai_content"), dict) else {}
            seen: dict[str, str] = {}
            for attr in ai.get("top_attractions", []) or []:
                url = str(attr.get("url", "") or "").strip()
                if not url:
                    continue
                name = str(attr.get("name", "") or "").strip()
                if url in seen and seen[url] != name:
                    warnings.append(
                        f"Duplicate attraction URL in '{dest.get('id', dest.get('name', 'unknown'))}': "
                        f"'{seen[url]}' and '{name}' both point to {url}"
                    )
                else:
                    seen[url] = name

    # ── Check 8: Attraction/trail teaser completeness ────────────────────────

    def _check_teaser_completeness(self, trip: dict[str, Any], warnings: list[str]) -> None:
        total = 0
        empty = 0
        for dest in trip.get("destinations", []) or []:
            ai = dest.get("ai_content", {}) if isinstance(dest.get("ai_content"), dict) else {}
            for attr in ai.get("top_attractions", []) or []:
                total += 1
                if not str(attr.get("description", "") or "").strip():
                    empty += 1
        if total == 0:
            return
        ratio = empty / total
        if ratio > self._max_empty_teaser_ratio:
            warnings.append(
                f"Attraction/trail teasers empty: {empty}/{total} "
                f"({ratio:.0%}, threshold: {self._max_empty_teaser_ratio:.0%})"
            )
