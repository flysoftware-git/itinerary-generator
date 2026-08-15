# Multi-Site Destination Grouping (GH #68)

Status: spec only, not implemented. Follow-up to the three design options
presented for GH #68 ("Multi-site destinations") — this is Option 3
("grouped destinations via manifest"), the recommended starting point:
smallest invasive surface, zero new fabrication risk, reuses every
per-destination subsystem exactly as it works today.

## 0. Why this option, restated

`nps_resolver.py`, `url_discovery.py`, `ai_content.py`, and
`html_assembler.py` all hard-assume one place per destination entry today
(one NPS code, one flat attraction list, one section/heading/weather
link — see the architecture survey this design followed from). Option 3
doesn't touch any of that: Moab, Arches, and Canyonlands become **three
ordinary destination entries**, each independently geocoded, discovered,
content-generated, and rendered exactly as any destination is today —
genuinely correct, genuinely verified content per site, no LLM
self-attribution of "which park is this actually in."

The only new work is **cross-destination**: recognizing that three
sequential destination entries aren't three separate lodging stops, and
adjusting the handful of places that currently assume "next destination =
new place to drive to and check into."

## 1. Manifest schema change

One new optional field on a destination entry:

```yaml
destinations:
  - id: moab
    name: "Moab"
    dates: "August 1-4, 2026"
    lodging:
      name: "Moab Springs Ranch"
      location: "Moab Springs Ranch, Moab, UT"
      checkin_time: "4:00 PM"
    planning_links: [...]

  - id: arches
    name: "Arches National Park"
    dates: "August 2, 2026"
    group_with: moab          # NEW
    planning_links: [...]

  - id: canyonlands
    name: "Canyonlands National Park"
    dates: "August 3, 2026"
    group_with: moab          # NEW
    planning_links: [...]
```

- `group_with` (optional string): the `id` of another destination entry
  this one shares a lodging base with. Omitted = current behavior,
  unchanged.
- **Validation** (`manifest_parser.py`): `group_with` must reference an
  `id` that exists elsewhere in `destinations[]`; a destination cannot
  reference itself; the referenced destination cannot itself have a
  `group_with` (no chains/cycles — one base, N day-trip entries, not a
  tree). A grouped entry's own `dates` should fall within the group base's
  overall date range (a day, not a re-stated multi-day span) — validated
  as a warning, not a hard schema failure, since date-string parsing
  elsewhere in this codebase is already deliberately lenient (free-text
  `dates`, not a structured range).
- `lodging`: optional on a grouped entry. If omitted, the entry inherits
  the group base's `lodging` block wholesale (for rendering/display
  purposes only — see §2). If present, it overrides (rare case: a
  multi-night side trip that still counts as "grouped" for nav/route
  purposes but has genuinely different lodging).

No other manifest fields change. `seeds`, `attractions_per_day`,
`schedule_start_time`, etc. all keep meaning exactly what they mean today,
scoped to that one entry.

## 2. Lodging display dedup

Today `_build_header`/related rendering (`html_assembler.py`) shows a
lodging block (name, location, check-in time) per destination section.
For a grouped entry with no own `lodging`, render a compact variant
instead of repeating the full block: e.g. "Based from Moab Springs Ranch
(see Moab)" with a link/anchor to the group base's section, rather than
duplicating the check-in-time card three times for what is, physically,
one stay.

## 3. Navigation clustering

`_build_nav_tabs` (`html_assembler.py`) currently emits one flat tab per
destination `id`. Grouped entries render as visually clustered
sub-tabs under their base — e.g., the Moab tab gets a subtle expand
affordance or the three tabs (Moab / Arches / Canyonlands) share a
connecting visual band/background and indent, rather than reading as
three unrelated top-level stops in the trip. Exact visual treatment
(indent vs. bracket vs. shared background) is a template/CSS decision,
not a data-model one — deferred to implementation, doesn't affect the
schema or pipeline logic above.

## 4. Route/distance handling for grouped hops

This is the piece most likely to look wrong if unaddressed. Today, the
route-overview and each destination's "getting here" content
(`_update_route_distance_and_time` et al., per
`live-fetch-and-execution-time-reduction.md` §2.5) treats every
destination transition as a one-way relocation: drive N hours from the
previous stop, arrive, check in.

For a `group_with` transition, that's wrong twice: (a) there's no new
check-in — same lodging — and (b) the "drive" is a there-and-back day
trip from the shared base, not a one-way leg advancing the route. Needed
change: when destination B's `group_with` points at the immediately
preceding rendered destination (or any earlier one in the same group),
route/distance calculation for B should compute base→B (not
previous-in-list→B, which could itself be another grouped sibling) and
label it as a day-trip/detour rather than a route leg — and the *next*
ungrouped destination after the group should compute its distance from
the shared base, not from whichever grouped sibling happened to render
last, since the group doesn't move the traveler's actual physical base.

## 5. What does NOT change

- `nps_resolver.py`: no change. Arches and Canyonlands each resolve their
  own real NPS code exactly as any single-park destination does today.
- `url_discovery.py`: no change. Each grouped entry runs the full,
  independently-verified discover_all pass for its own name — real
  AllTrails/restaurant/attraction links, not inherited or re-tagged from
  a combined harvest.
- `ai_content.py`: no change. Each grouped entry gets its own real
  LLM-generated `top_attractions`/`what_to_know`/`scenic_drives`, scoped
  to its own name, exactly like today.
- Day-count/schedule-budget inference (`_infer_destination_day_count`,
  `_infer_day_count`): no change needed. Each grouped entry already
  declares its own `dates` sub-range, and existing per-destination day
  inference already operates on whatever `dates` string that entry has —
  the multi-site case falls out of the existing single-destination logic
  for free, as long as each entry's `dates` is scoped to its own days
  within the stay (already true in the example above).

## 6. Open questions before implementation

1. Exact visual treatment for nav clustering (§3) — needs a quick mockup
   pass, not a data decision.
2. Whether `group_with` validation (date-sub-range-within-base-range)
   should warn or hard-fail on an obviously wrong manifest (e.g. grouped
   entry's dates entirely outside the base's range) — lean warn, matching
   this codebase's existing lenient free-text `dates` handling.
3. Whether the departure/return route leg (last grouped entry back to the
   trip's next real stop, or to the return leg) needs its own
   "distance from base, not from last-rendered-entry" fix, symmetric to §4.

## 7. Rough scope estimate

- `manifest_parser.py`: schema field + validation — small.
- `html_assembler.py`: lodging-dedup rendering, nav-tab clustering — small
  to medium (mostly template/CSS).
- Route/distance calculation (`main.py` and/or `url_discovery.py`,
  wherever `_update_route_distance_and_time` and the route-overview
  builder live) — small to medium, the one piece with real logic
  (base-tracking through a group) rather than just new rendering.
- No changes to `nps_resolver.py`, `url_discovery.py`'s discovery logic,
  or `ai_content.py`.

Overall: a moderate, mostly-additive change concentrated in manifest
validation and rendering, with one real logic change (route/distance
base-tracking, §4) — not a rewrite of any core discovery/content
subsystem.
