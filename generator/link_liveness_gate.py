"""The last question a link is asked before it reaches a card: was it found dead?

Publication is fail-closed (requirements 8.4): a link a check found
definitively dead -- 404/410, or a host that does not resolve -- is not
published. Until this module that rule was enforced only inside the URL
audit's retention gate, one call at a time, and the liveness report was
computed separately, as a description. Three measured gaps followed from that.

1. **A retention shortcut that never asks.** `_retain_discovered_url` returns
   a restaurant URL whose host names the restaurant
   (`_looks_like_item_specific_homepage`) -- and a remembered authoritative
   direct-batch URL -- before any fetch. The audit's prewarm had already
   fetched those same URLs and recorded DNS failures in the liveness ledger,
   so the ledger said `dead` while the gate kept the link. On one 11-stop
   guide two restaurant links whose hosts no longer resolve were rendered on
   cards, with the same link icon as a working one, and three more stayed in
   the trip data.

2. **Links the report never saw.** The report's "published" set was the
   audit's own collection of item `url` fields, taken at the end of the
   audit. A leg's trail link (`getting_here.trail_url`) and a route option's
   link are rendered with the same link-source icon and were in neither --
   and a selective retry re-audits a *subset* trip, so the report it computes
   is written to a dict that is thrown away, leaving the retried
   destinations' replacement links outside the report too. On that guide 14
   rendered card links were in no report.

3. **Later edits.** The restaurant per-day cap and registry reconciliation
   change the trip after the audit, so the report described a trip that was
   not the one assembled.

So the rule is enforced once more, at the one point where links are final:
immediately before assembly, over every link a card renders, whatever path
attached it. The existing classification decides what dead means
(`URLDiscoverer.classify_link_liveness`), so blocked hosts (401/403/429),
timeouts and the bot-block carve-out stay `unchecked` and stay published --
this adds no new notion of failure, it only stops a recorded one being
ignored. The existing fail-closed rule decides what happens to the item: a
non-seed attraction, restaurant or en-route stop left with no verified link
is removed (`_keep_item_if_verified_or_seed`); a traveller's own seed stays
and renders unverified; a drive, event, route option, leg trail or local tip
loses only the link, as the audit already does for those.

The report is then recomputed over the final trip, so the footer's "No link
that failed a check was published" describes the page it is printed on, and
what was withheld is recorded rather than silently subtracted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterator

from generator.fanout_metrics import pool as instrumented_pool

logger = logging.getLogger(__name__)

# Item kinds whose removal is governed by the verified-link-or-seed policy.
# Everything else loses only its link, exactly as the audit treats it.
_FAIL_CLOSED_SECTIONS = {
    "attraction": ("top_attractions", "attraction"),
    "restaurant": ("dinner_recommendations", "restaurant"),
    "en_route_stop": ("en_route_stops", "en_route_stop"),
}

# Removal reason recorded for the registry and the decision log.
WITHHELD_REASON = "dead_link_withheld_at_assembly"


@dataclass
class CardLink:
    """One URL a card renders, and where in the trip it lives."""

    dest: dict[str, Any]
    kind: str
    holder: dict[str, Any]
    field: str
    url: str
    # The list the holder sits in, for kinds an item can be removed from.
    container: list[Any] | None = None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def card_links(trip: dict[str, Any]) -> Iterator[CardLink]:
    """Every link the assembler renders on a card with a link-source icon.

    Deliberately a superset of `URLDiscoverer._collect_discovered_urls`, which
    stays as it is because the audit's prewarm uses it. The engine-built Maps
    badges (`maps_url`, `lunch_maps_url`) are not listed: they are navigation
    the engine constructs, not sources anybody could find dead.
    """
    for dest in _list(trip.get("destinations")):
        if not isinstance(dest, dict):
            continue
        ai = _dict(dest.get("ai_content"))
        getting_here = _dict(ai.get("getting_here"))
        events = _dict(dest.get("cultural_events"))
        item_lists = (
            ("attraction", ai.get("top_attractions")),
            ("en_route_stop", getting_here.get("en_route_stops")),
            ("restaurant", ai.get("dinner_recommendations")),
            ("scenic_drive", dest.get("scenic_drives")),
            ("event", events.get("events")),
            ("route_option", _dict(ai.get("getting_there")).get("route_options")),
        )
        for kind, items in item_lists:
            container = _list(items)
            for item in container:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url", "") or "").strip()
                if url:
                    yield CardLink(dest, kind, item, "url", url, container)
        trail_url = str(getting_here.get("trail_url", "") or "").strip()
        if trail_url:
            yield CardLink(dest, "leg_trail", getting_here, "trail_url", trail_url)
        tip_url = str(events.get("local_tip_url", "") or "").strip()
        if tip_url:
            yield CardLink(dest, "local_tip", events, "local_tip_url", tip_url)


def _is_engine_built(url: str) -> bool:
    from generator.url_discovery import SAFE_FALLBACK_URL_PREFIXES

    lower = url.lower()
    return any(lower.startswith(prefix) for prefix in SAFE_FALLBACK_URL_PREFIXES)


def _check_unobserved(discoverer: Any, links: list[CardLink]) -> int:
    """Fetch each card link this run never observed, so it is checked, not assumed.

    A link attached outside the audit (a manifest-authored leg trail link is
    the measured case) has no ledger entry. Reporting it as `never_fetched`
    would be honest, but the rule is that a rendered link is checked, and the
    fetch is one request through the same path -- and the same cooldowns and
    caches -- as every other check. Anything it cannot conclude stays
    `unchecked` and publishes.
    """
    recorded = getattr(discoverer, "_link_liveness", None) or {}
    unobserved = sorted(
        {link.url for link in links if link.url not in recorded and not _is_engine_built(link.url)}
    )
    if not unobserved:
        return 0

    def _fetch(url: str) -> None:
        try:
            discoverer._fetch_page_text(url, 8)
        except Exception as exc:  # a failed check must not fail the run
            logger.info("Assembly link check could not fetch %s: %s", url, exc)

    with instrumented_pool("assembly_link_check", min(8, len(unobserved))) as check_pool:
        for future in [check_pool.submit(_fetch, url) for url in unobserved]:
            future.result()
    return len(unobserved)


def withhold_dead_card_links(trip: dict[str, Any], discoverer: Any) -> dict[str, Any]:
    """Remove every card link the run found dead, then re-report over what is left.

    Mutates `trip` in place and replaces `trip["_link_liveness"]`. Returns a
    summary: how many links were checked here for the first time, how many
    were withheld, and how many items the fail-closed rule then removed.
    """
    from generator.url_discovery import LINK_LIVENESS_DEAD

    links = list(card_links(trip))
    checked_here = _check_unobserved(discoverer, links)

    recorded = getattr(discoverer, "_link_liveness", None) or {}
    withheld: dict[str, str] = {}
    to_review: list[CardLink] = []
    for link in links:
        if discoverer.link_liveness_state(link.url) != LINK_LIVENESS_DEAD:
            continue
        entry = recorded.get(link.url)
        withheld[link.url] = str(entry[1]) if entry else ""
        link.holder.pop(link.field, None)
        if link.kind == "leg_trail":
            link.holder.pop("trail_label", None)
        dest_name = str(link.dest.get("name", "") or "")
        item_name = str(
            link.holder.get("name", "") or link.holder.get("title", "") or link.holder.get("trail_label", "") or link.kind
        )
        logger.info(
            "Dead link withheld from %s '%s' (%s): %s (%s)",
            link.kind, item_name, dest_name, link.url, withheld[link.url],
        )
        if hasattr(discoverer, "_log_decision"):
            discoverer._log_decision(
                kind=link.kind,
                dest_name=dest_name,
                item_name=item_name,
                reason=WITHHELD_REASON,
                message="link a check found dead; not published",
                url=link.url,
            )
        if link.kind in _FAIL_CLOSED_SECTIONS and link.container is not None:
            to_review.append(link)

    removed = 0
    for link in to_review:
        section_target, entity_class = _FAIL_CLOSED_SECTIONS[link.kind]
        item = link.holder
        if not any(existing is item for existing in link.container):
            continue
        dest_name = str(link.dest.get("name", "") or "")
        item_name = str(item.get("name", "") or "")
        is_seed = bool(item.get("is_seed")) if link.kind != "restaurant" else False
        extra_verified = (
            bool(discoverer._item_has_verified_route_geocode(item))
            if link.kind == "en_route_stop"
            else False
        )
        keep = discoverer._keep_item_if_verified_or_seed(
            link.dest,
            item,
            item_name,
            is_seed=is_seed,
            section_target=section_target,
            entity_class=entity_class,
            kind=link.kind,
            dest_name=dest_name,
            extra_verified=extra_verified,
            extra_verified_reason="en_route_geocode_verified_kept" if extra_verified else "",
        )
        if not keep:
            link.container[:] = [existing for existing in link.container if existing is not item]
            removed += 1

    if removed:
        # A removed item may still be named in the day's schedule text. The
        # registry already knows how to strip that; it reads the removal
        # records `_keep_item_if_verified_or_seed` just wrote.
        from generator.entity_registry import build_entity_registry, reconcile_schedule_from_registry

        reconcile_schedule_from_registry(trip, build_entity_registry(trip))

    # Opt-in and off by default: one site-restricted search per refused link
    # this run's searches never returned. Evidence only -- it cannot change a
    # state, so it runs after every removal decision has been made.
    from generator.link_corroboration import search_for_uncorroborated

    corroboration_searches = search_for_uncorroborated(
        discoverer, [link for link in links if link.holder.get(link.field) == link.url]
    )

    report = discoverer.link_liveness_report(trip)
    report["withheld_dead"] = dict(sorted(withheld.items()))
    report["withheld_dead_count"] = len(withheld)
    trip["_link_liveness"] = report
    return {
        "checked_at_assembly": checked_here,
        "withheld": len(withheld),
        "items_removed": removed,
        "corroboration_searches": corroboration_searches,
    }
