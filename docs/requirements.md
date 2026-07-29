# Road Trip Itinerary Generator — Requirements Document
**Version 0.28 · July 29, 2026**

### Changelog for v0.28
| # | Section | Change |
|---|---|---|
| 1 | §4, §5 | Added within-destination deduplication requirement: when a named entity appears in both `top_attractions` and `scenic_drives` for the same destination, the scenic-drives entry must be removed (one-card-one-entity rule) |
| 2 | §4 | Added cross-section deduplication requirement: text present verbatim in `what_to_know` fields must not also appear in `cultural_events.local_tip` or event descriptions for the same destination |
| 3 | §4 | Added cross-destination deduplication requirement: `what_to_know` field values that are verbatim-identical across 2+ destinations must be replaced with the field-level fallback default |
| 4 | §5 | Added compound entity URL rejection: attraction names containing ` & ` (joining two distinct POIs) must not receive a discovered URL; the name may render as plain text but must not be hyperlinked to a single generic target |

### Changelog for v0.27
| # | Section | Change |
|---|---|---|
| 1 | §5 | Added entity-path integrity requirement for encyclopedic URLs: when the entity name is structurally encoded in the URL path (for example Wikipedia `/wiki/` slugs), item tokens must appear in that path; mismatches are rejected before any page fetch |
| 2 | §5 | Added redirect entity-match requirement: when an AllTrails URL follows a redirect, the final URL slug must still match the requested item name; silent redirects to different trail entities must be rejected |
| 3 | §5 | Added configurable AllTrails slug denylist (`url_discovery.alltrails_slug_denylist`): known-invalid or dead AllTrails slugs (detectable only via browser, not automation due to bot-blocking) may be explicitly excluded from discovery and audit |

### Changelog for v0.26
| # | Section | Change |
|---|---|---|
| 1 | §5 | Added URL class blocklist requirement: specific structural URL patterns (Google Maps search queries, Google Maps directions, bare Google search queries, social-media content pages) are categorically prohibited from final output regardless of discovery score or fallback path |
| 2 | §5 | Added fail-closed definition for named-entity links: a published link must be a deterministic, entity-specific target; query-style URLs that open a list of results are not acceptable for named entities; when no entity-specific URL survives audit, the item must be rendered without a link |
| 3 | §5 | Added URL policy rollout mode requirement: URL policy enforcement must be supportable in monitor-only mode (logging without rejection) to enable safe gradual rollout without breaking previously validated runs |
| 4 | §5 | Clarified maps-search fallback acceptable-use boundary: the synthesized maps-search fallback is never acceptable as a published link for a named entity (attraction, restaurant, waypoint); it may only be used for category-level or type-level items where no single entity is implied |

### Changelog for v0.25
| # | Section | Change |
|---|---|---|
| 1 | §5, §10 | Added optional filtered AllTrails selection mode using snippet metadata constraints (distance, elevation gain, difficulty, min reviews) before accepting/ranking trail candidates |
| 2 | §5, §10 | Filtered AllTrails mode ranks by rating/review volume among candidates that pass constraints, and allows fewer-than-target trail links rather than padding with weak matches |

### Changelog for v0.24
| # | Section | Change |
|---|---|---|
| 1 | §5, §10 | Trail-like attractions now apply a configurable AllTrails publish-confidence gate; links below threshold must fall back to non-AllTrails URLs (typically Google Maps query fallback) |
| 2 | §5, §10 | Added `url_discovery.alltrails_min_confidence_for_publish` (`low|medium|high`) to control strictness for blocked/sparse AllTrails pages |

### Changelog for v0.23
| # | Section | Change |
|---|---|---|
| 1 | §5, §10 | URL scoring now applies high-rating prioritization for AllTrails and restaurant candidates only when review-count thresholds are met (vote-gated rating boost) |
| 2 | §5, §10 | Added configuration controls for rating/vote thresholds and boost weights for AllTrails and restaurant discovery |

### Changelog for v0.22
| # | Section | Change |
|---|---|---|
| 1 | §3, §4 | Seed handling is now explicitly guaranteed at normalization time: missing seed attractions are injected and seed attractions are protected from en-route overlap pruning |
| 2 | §4 | Schedule boundary policy hardened: first destination Day 1 Morning is reserved for inbound travel from origin, and final destination last-day Afternoon/Evening are reserved for return travel |
| 3 | §5 | AllTrails hike selection now prefers canonical trail slugs over `-via-` variants when available, and place-level entities (including plain `park`) are guarded against false trail classification |
| 4 | §5, §7 | Restaurant cards must prefer a verified discovered URL over query fallback links during rendering |
| 5 | §9 | Added `--first-destination` CLI switch to process only the first destination after any destination-id filtering |

### Changelog for v0.21
| # | Section | Change |
|---|---|---|
| 1 | §4 | Multi-day schedule normalization now requires complete daily period coverage (Morning/Afternoon/Evening), with arrival context on Day 1, departure context on final day, and at least one unique planning signal per additional day |
| 2 | §5 | Trail-like attraction detection expanded beyond explicit hike types (for example `walk`, `loop`, `narrows`, `riverside walk`) so AllTrails-first policy applies consistently |
| 3 | §5 | Generic landing-page rejection expanded (including NPS `things2do` paths), and trusted-host SSL fallback is allowed for verification/readability checks when certificate-chain issues occur |
| 4 | §6 | Capitol Reef image disambiguation hardened: marine/underwater reef imagery is hard-rejected for inland/desert contexts and Capitol-Reef-specific relevance cues are required when available |
| 5 | §6, §10 | Added destination-agnostic image content blacklist (configurable) for categories that should never appear (for example underwater/scuba/snorkeling imagery) |

### Changelog for v0.20
| # | Section | Change |
|---|---|---|
| 1 | §5, §10 | Added configurable attraction-interest filtering in URL discovery: hard blacklist keywords (for example golf-course style attractions) and seasonal ski-attraction suppression outside configured ski months |
| 2 | §4 | What-to-Know output schema trimmed to rendered fields only; weather-pattern and photography-tip fields are no longer required in prompt or normalization |
| 3 | §5, §7 | Scenic-drive/day-trip popup content may include one optional "More Info" link when a verified URL is available; no generic fallback link is required |

### Changelog for v0.19
| # | Section | Change |
|---|---|---|
| 1 | §2, §4 | Added mandatory per-destination LLM-generated "What to Know" briefing with global context fields (customs, weather patterns, transportation quirks, safety, photography, crowds, etiquette) |
| 2 | §2, §3, §5 | NPS enrichment is now US-coordinate gated; non-US destinations skip NPS resolution and must still receive full itinerary content |
| 3 | §2, §7 | Weather link generation now uses weather.gov only for US coordinates and a global Weather.com fallback elsewhere |
| 4 | §5 | URL selection now uses semantic candidate scoring (keyword alignment, domain hints, path relevance, title/context signals, and destination-country TLD boosts) instead of first-valid selection |
| 5 | §6, §7 | Image gallery markup is standardized to one image-tile wrapper containing both image and caption; image-load failures hide only the <img> element |

### Changelog from v0.18
| # | Section | Change |
|---|---|---|
| 1 | §4, §5 | Arrival-destination attractions and schedules must not repeat CAN'T-MISS ENROUTE stops; restaurant normalization now excludes obvious chain / fast-food picks |
| 2 | §4.3, §5 | Cultural-event rendering now allows at most one outbound link per item, and AllTrails hike acceptance now rejects localized soft-404 pages instead of trusting URL shape alone |
| 3 | §5 | Non-hike attractions must not resolve to AllTrails; hike links may use AllTrails only when page-body validation confirms the trail page is real and relevant |
| 4 | §6, §12 | Local image references must be emitted as portable relative `./images/...` paths for both gallery images and hero backgrounds |
| 5 | §6 | Image ranking must penalize off-theme marine / coral / underwater imagery for inland and desert destinations |
| 6 | §9, §12 | Output environment subdirectories are opt-in via `--environment`; default output writes directly to the requested output directory |
| 7 | §9, §11, §13 | Requirements updated for current multi-provider LLM setup, `--env-file` / `--refresh-image-cache` / `--log-level` CLI options, standalone-safe PWA degradation under `file://`, and removal of the attribution footer block |
| 8 | §5 | URL validation now requires stronger page-text overlap for candidate pages, so hallucinated trail names cannot pass on a single broad token match |

### Changelog from v0.17
| # | Section | Change |
|---|---|---|
| 1 | §4, §12 | Added local iterative image cache index (`.cache/images/cache_index.json`) with destination-keyed reuse and TTL control to reduce repeated provider discovery calls |
| 2 | §5, §11 | Added CLI switch `--refresh-image-cache` to bypass local image cache on demand |

### Changelog from v0.16
| # | Section | Change |
|---|---|---|
| 1 | §13 | Consolidated PWA flow to one canonical implementation: static `manifest.webmanifest`, single `sw.js` registration path, and one install-prompt UX path |
| 2 | §12, §13 | Generator now writes PWA companion assets beside `index.html` in the active output environment folder |

### Changelog from v0.15
| # | Section | Change |
|---|---|---|
| 1 | §4, §7 | Daily schedule rendering updated to hanging-indent format so wrapped lines align under time-of-day content while icon+label stay on one line |
| 2 | §4.3 | Local-tip fallback content now includes a "More info" link using query-based lookup when no direct source URL is available |
| 3 | §6, §7 | Single supplemental image layout now centers/fills gallery space rather than left-column pinning |
| 4 | §5 | External link rendering hardened with URL normalization/escaping to reduce broken links in attractions, restaurants, events, and en-route cards |

### Changelog from v0.14
| # | Section | Change |
|---|---|---|
| 1 | §4, §7 | Schedule time-of-day labels now require non-wrapping icon+label alignment, with content text wrapping under the schedule content column |
| 2 | §4, §5 | CAN'T-MISS ENROUTE stop links hardened with escaped href rendering and actionable fallback links |
| 3 | §4.3 | Cultural events now require a resolvable "More info" link per identified event (source URL or generated search fallback) |

### Changelog from v0.13
| # | Section | Change |
|---|---|---|
| 1 | §4 | Schedule normalization now injects arrival/departure travel context for first/last itinerary days when multi-day stays are present |
| 2 | §5 | Attraction and en-route links now fall back to Google Maps query links when strict URL discovery yields no verified page |
| 3 | §7 | Scenic-drive popup now includes a "More Info" external link and suppresses attribution-style text in popup body |
| 4 | §4, §6 | Attraction deduplication and image-localization relevance scoring added to reduce redundant entries and off-location photos |
| 5 | §7 | Cultural events card styling normalized to match core card visual language |

### Changelog from v0.12
| # | Section | Change |
|---|---|---|
| 1 | §5 | Hike URL reliability hardened: specific AllTrails trail URLs are accepted without brittle liveness checks that often fail on bot-protected pages |
| 2 | §4, §7 | En-route stop schema/rendering now includes detour metadata (`detour_distance_miles`, `detour_time_minutes`) for CAN'T-MISS ENROUTE cards |
| 3 | §7, §8 | Footer credit now renders generator name + version + generation timestamp with repository link; static "Made by Copilot" removed |
| 4 | §4 | Added budget-aware dinner price filtering rules in post-normalization |
| 5 | §13-§18 | Added requirements coverage for PWA support, print formatting, per-destination maps, dinner price logic, planning-link formatting, and month-specific weather grounding |

### Changelog from v0.11
| # | Section | Change |
|---|---|---|
| 1 | §4 | Added deterministic weather grounding: `expected_environment.temperature_high_f` / `temperature_low_f` are normalized from historical monthly climate normals by destination coordinates and travel month |
| 2 | §4 | Environment summary temperature claims are rewritten to reflect grounded normals, reducing hallucinated weather ranges |

### Changelog from v0.8
| # | Section | Change |
|---|---|---|
| 1 | §7 | `v2.5_template.html` converted from hardcoded reference document to true generator template: trip title, nav tabs, Google Maps URL, Leaflet map markers, and all destination sections now use injection placeholders |
| 2 | §7 | Assembler updated to produce output matching template's CSS/JS conventions: section IDs use `section-{id}` format, CSS class `dest-section`, drive buttons use `class="drive-link"` + `data-drive-title`, `DRIVE_DESCRIPTIONS` keyed by raw title string |
| 3 | §7 | Validator updated to check `data-drive-title` attribute (not `data-drive-key`) and `id="section-{id}"` format (not bare `id="{id}"`) |
| 4 | §11 | Added `XAI_MODEL` env var to select Grok model; `XAI_API_KEY` already present since v0.8 |

### Changelog from v0.10
| # | Section | Change |
|---|---|---|
| 1 | §3 | Added optional `trip.departure` and `trip.return` manifest fields; geocoded and used in route links/map context |
| 2 | §5, §9 | Added `--noschedule` CLI flag to suppress schedule rendering |
| 3 | §5 | URL policy tightened: hike links resolve via AllTrails; non-hike attractions may use NPS/official sources |
| 4 | §5 | URL selection requires relevance checks (not only liveness), reducing generic search/landing-page links |
| 5 | §7 | Full Route Map now uses Google Maps Directions API parameters (origin/destination/waypoints by place name) rather than bare coordinate chains |
| 6 | §8 | Debug block rendering is opt-in (`config.render.show_debug_block`) and off by default |
| 7 | §11 | LLM cost tracking now includes Grok/xAI usage from URL discovery and cultural-event search calls |

### Changelog from v0.7
| # | Section | Change |
|---|---|---|
| 1 | §2, §5, §11 | Search client migrated from Google Programmable Search Engine (v1.4, rate-limited) to xAI Grok semantic search (v1.5); env var changed to `XAI_API_KEY` (single key, simpler setup) |

### Changelog from v0.6 *(superseded)*
Migrated from Bing Web Search (v1.3) → Google Programmable Search (v1.4). Fully superseded by v0.8 Grok migration.

### Changelog from v0.5 *(superseded)*
Migrated from Brave Search (v1.2) → Bing Web Search (v1.3). Fully superseded by v0.8 Grok migration. Added parallel `ThreadPoolExecutor` execution model.

---

## 1. Purpose & Scope

A Python command-line program that accepts a minimal user-defined trip manifest and produces a portable `index.html` itinerary that is visually, structurally, and functionally aligned with the Southwest Road Trip Itinerary v2.5 — but with AI-generated content tailored to any set of destinations worldwide.

The output file must remain usable when opened directly from disk (`file://`) and must also be deployable to GitHub Pages or any static host with zero server-side dependencies.

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────┐
│  trip_manifest.yaml          (user-authored, minimal)   │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  STAGE 1: Input Validation & Auto-Enrichment            │
│  • Parse and validate manifest schema                   │
│  • Geocode each destination → lat/lng (Nominatim API)   │
│  • Detect NPS park code for US destinations only        │
│  • Construct weather URL: weather.gov (US) or global    │
│    Weather.com fallback (non-US)                        │
│  • Verify all user-provided planning link URLs          │
│  • Auto-generate Google Maps overview URL               │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  STAGE 2: AI Content Generation (Configured LLM)        │
│  • Per-destination content: environment, attractions,   │
│    en-route stops, schedule, restaurants (NO URLS)      │
│  • Per-destination "What to Know" briefing via LLM      │
│    with global/local logistics context                   │
│  • Post-normalization grounds monthly temperatures       │
│    from climate normals and rewrites weather narrative   │
│  • Post-normalization removes chain / fast-food dining   │
│    and avoids duplicating en-route stops as destination  │
│    arrival attractions or schedule items                │
│  • Scenic drives + viewpoints (fully AI-discovered)     │
│  • Cultural events via Grok semantic search + AI synthesis│
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  STAGE 3: URL Discovery (xAI Grok Semantic Search)      │
│  • Per-item URL discovery for every named entity        │
│  • NPS domain filter for park attractions               │
│  • Two-pass restaurant strategy:                        │
│    Pass 1: Google Maps (top-rated, hours)               │
│    Pass 2: TripAdvisor (diversity, local favorites)     │
│  • 4-variant fallback query sequence per item            │
│  • HTTP verification + page-body relevance checks        │
│  • AllTrails soft-404 rejection for hike links          │
│  • Semantic scoring selects best candidate URL (not      │
│    first valid URL)                                      │
│  • High-rating candidate boosts are vote-gated           │
│    (ratings only influence rank when review volume is    │
│    above configured thresholds)                          │
│  • Trail-like AllTrails links are confidence-gated       │
│    before publish; low-confidence results fall back to   │
│    non-AllTrails URLs                                     │
│  • Optional filtered AllTrails mode applies hard         │
│    trail constraints and snippet-based ranking before     │
│    canonicalization/publish confidence checks             │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  STAGE 4: Image Fetching                                │
│  • NPS API for national parks (park code required)      │
│  • Unsplash, then Wikimedia fallback for all destinations│
│  • Local iterative image cache with TTL + refresh flag  │
│  • THUMB_WIDTH = 960px always                           │
│  • 4-attempt automatic fallback on verification fail    │
│  • Hard fail if < min_per_destination verified images   │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  STAGE 5: HTML Assembly                                 │
│  • SHA-256 checksum verification of frozen template     │
│  • Python string assembly (no Jinja2)                   │
│  • var DRIVE_DESCRIPTIONS JS object (not const)         │
│  • Google Maps overview URL auto-injected               │
│  • No attribution <details> block is appended            │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  STAGE 6: Validation & Reporting                        │
│  • Div balance per destination section                  │
│  • Script tag isolation check                           │
│  • var DRIVE_DESCRIPTIONS presence (not const)          │
│  • Drive modal key/button alignment                     │
│  • Image count >= min_per_destination per section       │
│  • JSON validation report output                        │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Trip Manifest Schema (v0.5)

The manifest is intentionally minimal. All geocoding, NPS detection, URL discovery, and content generation happen automatically.

### 3.1 Trip-Level Fields

| Field | Required | Description |
|---|---|---|
| `title` | ✅ | Trip title (e.g., "Southwest Road Trip") |
| `subtitle` | ✅ | Trip subtitle (e.g., "October 2026 — Utah & Colorado") |
| `theme_color` | ✅ | Hex color for nav, headers, map markers (e.g., `#C0623E`) |
| `llm` | ❌ optional | Override model routing for this trip: `{provider, model, temperature, max_tokens}` |
| `budget` | ❌ optional | Budget guidance (string/number/object) passed into AI prompts |
| `departure` | ❌ optional | Route origin location name for first leg and full-route map |
| `return` | ❌ optional | Route final endpoint location name for full-route map |

`trip.llm.provider` supports: `openai`, `anthropic`, `deepseek`, `gemini`.

### 3.2 Auto-Resolved Fields (NOT in manifest)

| Field | Resolution Method |
|---|---|
| `lat` / `lng` | Nominatim geocoding from `name` |
| `nps_park_code` | Keyword detection + NPS API text search (US coordinates only) |
| `weather_url` | Constructed from lat/lng: weather.gov in US, global fallback elsewhere |
| `google_maps_link` | Auto-generated from origin/destination/waypoints (names); includes `departure`/`return` when provided |

### 3.3 Destination Fields

| Field | Required | Description |
|---|---|---|
| `id` | ✅ | Unique slug (e.g., `zion`, `moab`) |
| `name` | ✅ | Full destination name for geocoding and AI prompts |
| `dates` | ✅ | Human-readable date range (e.g., `"October 7–9, 2026"`) |
| `planning_links[]` | ✅ | Array of `{label, url}` — Notion, TripIt, reservation links |
| `seeds[]` | ❌ optional | Attraction/hike/experience **name hints only** — things the user specifically intends to include. No URLs. No scenic drive titles (AI discovers those). |

### 3.4 Seeds Rules

Seeds are lightweight hints that anchor AI content generation to specific user intentions. They are:
- ✅ Attraction names: `"Angels Landing"`, `"The Narrows"`
- ✅ Hike names: `"Navajo Loop Trail"`, `"Hickman Bridge"`
- ✅ Experience anchors: `"Dark Sky Stargazing"`, `"Jeep rental"`
- ❌ NOT URLs — any seed containing `://` is rejected with a validation error
- ❌ NOT scenic drive titles — AI discovers those independently
- ❌ NOT restaurant names — AI discovers those via TripAdvisor/Google Maps sourcing

Runtime guarantees:
- If AI omits a requested seed attraction, normalization must inject it as a structured attraction item.
- Seed attractions must be protected from en-route overlap pruning so user-requested anchors are retained.

---

## 4. AI Content Generation

### 4.1 Per-Destination Content Schema

```json
{
  "expected_environment": {
    "summary": "string — sensory lead + operational note; temp claims are grounded post-generation",
    "temperature_high_f": "integer — grounded monthly daytime normal in °F",
    "temperature_low_f": "integer — grounded monthly overnight normal in °F",
    "what_to_pack": ["string", "..."]
  },
  "getting_here": {
    "from_previous": "string — driving directions from previous destination",
    "en_route_stops": [
      {
        "name": "string",
        "highway_reference": "string — highway and exit/milepost",
        "description": "string — what makes this worth stopping for",
        "time_required": "string — e.g., '30 minutes'",
        "detour_distance_miles": "number — extra miles off the direct route (0 if on-route)",
        "detour_time_minutes": "number — extra drive minutes for detour (0 if on-route)"
      }
    ]
  },
  "top_attractions": [
    {
      "name": "string",
      "description": "string",
      "difficulty": "Easy | Moderate | Strenuous",
      "duration": "string — e.g., '4–5 hours'",
      "must_see": true,
      "practical_note": "string — permit info, seasonal closures, gear requirements"
    }
  ],
  "possible_daily_schedule": ["string array — timed itinerary items"],
  "dinner_recommendations": [
    {
      "name": "string",
      "cuisine": "string — specific cuisine type",
      "price": "$ | $$ | $$$ | $$$$",
      "description": "string"
    }
  ]
}
```

**Restaurant requirements:** 5–6 per destination. Must include 3+ distinct cuisine types and 2+ price tiers. Coverage must include both top-rated and local/casual options.

Restaurants that are clearly chain or fast-food picks must be removed during normalization even if they appear in model output.

**Dinner price filtering logic:**
- If trip budget indicates budget/economy/value, recommendations should be centered on `$`/`$$` with at most one splurge (`$$$`/`$$$$`).
- If trip budget indicates premium/luxury/upscale, recommendations should be centered on `$$$`/`$$$$` with at most one casual option (`$`/`$$`).
- If no clear budget signal exists, include a mixed tier spread.

**Schedule realism rules:**
- For the first destination, Day 1 Morning is reserved for transportation from trip origin.
- For the final destination, last-day Afternoon and Evening are reserved for return travel.
- For intermediate destinations, final evening should account for onward departure preparation to the next destination.
- Day-level sequencing should remain feasible given same-day drive and activity load.
- Activities proposed after arrival to a destination must not duplicate CAN'T-MISS ENROUTE stops from that inbound leg.
- For multi-day stops, each day must render Morning, Afternoon, and Evening periods after normalization (no sparse day cards).
- For multi-day stops, each additional day should contain at least one meaningful planning variation relative to previously listed day summaries.

### 4.2 Scenic Drives & Viewpoints Schema

```json
[
  {
    "title": "string — AI-discovered, not seeded",
    "category": "scenic_drive | viewpoint | aerial | day_trip | historic",
    "distance_or_duration": "string",
    "best_time": "string",
    "description": "string",
    "vehicle_requirement": "any | high_clearance | 4wd"
  }
]
```

2–4 entries per destination. Titles are fully AI-discovered — never seeded by the user.

### 4.3 Cultural Events Schema (has_events decision tree)

**Format A — Events found:**
```json
{
  "has_events": true,
  "intro": "string",
  "events": [
    {
      "name": "string",
      "date": "string",
      "venue": "string — physical address",
      "admission": "string",
      "ambient_scene": "string — what it feels like to be there",
      "url": "string — source event URL when available"
    }
  ]
}
```

Each identified event may expose at most one outbound link. If the event name is already linked, the renderer must not add a redundant separate "More info" link. If a source event URL is unavailable after verification, the event name itself may fall back to a query-based search link using event name + venue + destination.

**Format B — Honest fallback (no invented events):**
```json
{
  "has_events": false,
  "honest_assessment": "string — what the park/town IS good for in this season",
  "local_tip": "string — one practical insight"
}
```

If `local_tip` references a specific weekday (for example, Friday or Saturday), that weekday must be within the destination itinerary date window; otherwise `local_tip` is omitted. Honest-fallback local tips may include one query-based outbound link when that link adds actionable context.

The AI must NEVER invent events. Remote national parks almost always return Format B.

---

## 5. URL Discovery

AI content generation and URL discovery are strictly separate pipeline stages. **AI never generates URLs.**

After AI content is generated, the URL Discoverer uses xAI Grok semantic search for every named entity:

1. **Hike attractions:** Resolve via AllTrails domain filter (primary policy)
2. **Non-hike attractions in NPS parks:** Prefer `site:nps.gov` domain filter
3. **Non-hike attractions:** Fall back to official/specific pages from broader search, but must not resolve to AllTrails
4. **Restaurants:** Two-pass — Google Maps domain filter, then TripAdvisor
5. **All items:** 4-variant fallback query sequence (most specific → broadest)
6. **Final fallback:** Empty string stored (no fabricated URLs)

Attraction interest filtering policy:
- URL discovery may skip attraction linking for user-uninterested categories using configurable keyword blacklists.
- Seasonal attraction filters are supported; for ski/snow attractions, linking may be skipped when destination-trip months fall outside configured in-season months.
- Skipped attractions must not force generic map-link fallback; they remain intentionally unlinked.

Every discovered URL is verified before storage, and strict candidates must also pass relevance checks against item/destination tokens. Live-but-generic search pages are rejected.

For non-AllTrails candidates, page text must match enough of the item name to be credible; a single shared token is not sufficient when the requested attraction/hike name contains multiple meaningful tokens.

The acceptance gate is stricter than HTTP liveness alone. Generic 404 pages, asset-detail pages, and other obviously non-target landing pages must be rejected even when they return 200.

AllTrails-specific acceptance rules:
- Non-hike attractions must reject AllTrails results even if those pages appear live.
- Hike links may use `alltrails.com/trail/...`, but acceptance must require a strong trail-name match against the AllTrails slug plus page-body validation.
- Known AllTrails soft-404 content, including localized "We've reached the end of the trail" / replacement-link pages, must be rejected even when the HTTP status is 200.
- For trail-like attractions, the discoverer must exhaust the configured AllTrails variant sequence (specific to broad variants) before generic map-link fallback is permitted.
- Trail-like detection must include common trail phrasing in names/descriptions (for example `trail`, `hike`, `loop`, `walk`, `narrows`, `riverside walk`) even when the item type is a generic attraction.
- Trail-like detection must guard place-level entities (for example names ending in `park`, `state park`, or `national park`) unless the attraction name itself carries explicit trail cues.
- When multiple valid AllTrails candidates exist, canonical trail slugs should be preferred over broader `-via-` route variants when a canonical slug match is available.

Generic-page rejection rules:
- Discovery must reject broad landing pages such as `/plan-your-visit`, `/things-to-do`, `/things2do`, `/explore`, `/about`, and equivalent non-specific listings.

SSL verification handling:
- URL verification/readability checks should remain strict by default.
- For approved trusted public hosts with known certificate-chain instability (for example `*.blm.gov`), an SSL-verify fallback may be used to avoid discarding otherwise valid destination links.

Fallback policy for unresolved links:
- If strict discovery does not produce a verified attraction/en-route/scenic URL, render a Google Maps query link so cards still resolve to actionable context.
- Exception: trail-like attractions may use the generic map-link fallback only after the AllTrails variant sequence has been fully attempted and rejected.
- Restaurant rendering should prefer verified discovered URLs first, then `maps_url`, then synthesized maps-search query fallback.

Final audit pass:
- Before HTML assembly, a cleanup pass strips any remaining weak discovered URLs so hallucinated or low-confidence links do not reach the final itinerary.
- Scenic-drive/day-trip URLs are retained only when verified and relevant; popups may render a single optional "More Info" link for those entries.

URL class blocklist (structural prohibition):
- The following URL structural patterns must never appear in published itinerary output, regardless of discovery method, relevance score, or fallback path:
  - `google.com/maps/search/` or equivalent Maps search query (non-deterministic place list)
  - `google.com/maps/dir/` or equivalent Maps directions URL
  - `google.com/search` or equivalent bare Google web-search query
  - Any URL on a social-media domain (`facebook.com`, `instagram.com`, `tiktok.com`, `twitter.com`, `x.com`)
- These URL classes must be rejected in the audit retention pass regardless of HTTP liveness or token overlap scores.
- Implementation must support configuration-driven class blocking so the blocklist can be extended without code changes.

Entity-path integrity for encyclopedic URLs:
- For URLs where the subject entity is structurally encoded in the URL path by convention (Wikipedia `/wiki/` pages, similar encyclopedic sources), item name tokens must appear in the URL path segment.
- A Wikipedia URL whose path encodes a different entity from the item being linked must be rejected at audit time, before any page fetch.
- This applies to the audit pass (`audit_discovered_urls`) as a pre-fetch gate.

Redirect entity-match requirement:
- When a URL fetch follows a redirect (HTTP 3xx or server-side canonical redirect), the final resolved URL slug must still match the item name tokens.
- If the final URL identifies a materially different entity from the requested item (for example an AllTrails URL for Trail A redirects to Trail B), the link must be rejected.
- This applies to AllTrails trail URLs where redirect-to-different-slug mismatches are detectable when the page is accessible.

AllTrails slug denylist:
- A configurable list of known-invalid AllTrails URL slugs must be supported (`url_discovery.alltrails_slug_denylist`).
- Any AllTrails URL whose final path segment matches a denylist entry must be rejected regardless of HTTP status, page text, or slug-token match.
- Rationale: AllTrails bot-blocking (HTTP 403) prevents automated detection of 404/dead trails; the denylist provides a manual escape hatch for cases observed in browser but not detectable in automation.

Fail-closed policy for named-entity links:
- A link published for a named entity (attraction, restaurant, en-route stop, event) must resolve to a deterministic, entity-specific target — one that refers to that single entity and not a list, a search query, or an area-level reference.
- A Google Maps search query of the form `maps/search/<name>+near+<destination>` is an area-reference query, not an entity-specific target, and must not be used as the published link for a named subject.
- When no entity-specific URL survives discovery and audit, the correct behavior is to render the item with no link, not to publish the best-available query URL.
- The synthesized `maps_url` search fallback is acceptable for rendering context only when the item is a category or type (not a specific named entity), or when the maps fallback is the configured last resort for a destination card and no other link is present.

URL policy rollout mode:
- URL class enforcement must be configurable via a policy mode setting: `off` (no enforcement), `monitor` (log violations but do not reject), or `enforce` (reject blocked URL classes).
- Default mode for new installs must be `monitor` to prevent silent regressions on first use; `enforce` is the production target after validation.
- Previously validated output links may be grandfathered via an allowlist mechanism; the allowlist must support automatic seeding from the prior generated HTML output to eliminate manual curation burden.

---

## 6. Image Pipeline

| Priority | Source | Condition |
|---|---|---|
| 1st | NPS API | `nps_park_code` present |
| 2nd | Unsplash search | Preferred broad source for non-NPS destinations |
| 3rd | Wikimedia Commons MediaSearch | Fallback / supplemental source |
| Fallback | Broader Wikimedia queries (4 attempts) | If < min_per_destination verified |
| Hard fail | RuntimeError raised | If still < min_per_destination |

- `THUMB_WIDTH = 960` always
- Images saved to a sibling `images/` directory beside the generated `index.html`, using MD5-hashed filenames
- Generated HTML must reference local images with relative `./images/{filename}` paths for both `<img>` tags and hero `background-image` styles
- Image selection should strongly prefer location-relevant landscape / landmark photography and penalize obvious theme mismatches such as marine or coral imagery for inland desert parks
- For Capitol Reef destinations, marine/underwater reef imagery must be hard-rejected and ranking should prefer Utah/canyon/sandstone context cues when present
- A destination-agnostic image blacklist must be applied first; blacklisted content classes (for example underwater/scuba/snorkeling) are always rejected regardless of destination
- A local image cache index at `.cache/images/cache_index.json` may be reused until TTL expiry; `--refresh-image-cache` must bypass that cache on demand
- Image metadata (source, license, author) stored with the image records for validation and reporting

---

## 7. Template Integrity

The v2.5 HTML template is committed to the repository as `templates/v2.5_template.html`. A SHA-256 checksum is stored in `templates/checksums.txt`.

On every run, the generator verifies the template checksum before processing. A mismatch causes an immediate hard failure with a clear error message.

The template is never fetched at runtime.

### 7.1 Template Injection Placeholders

The template is a true generator template — all trip-specific content is injected at runtime. Hardcoded content from the reference document has been replaced with the following placeholders:

| Placeholder | Replaced With |
|---|---|
| `<!--TRIP_TITLE-->` | `trip.title` from manifest |
| `<!--NAV_TABS-->` | Generated `<button class="tab-btn" data-tab="section-{id}">` elements + Google Maps link |
| `<!--DESTINATION_SECTIONS-->` | Full per-destination section HTML built by `HTMLAssembler` |
| `'<!--MAP_MARKERS_JSON-->'` | JSON array of `{c:[lat,lng], mo, dy, name}` objects for Leaflet map |
| `var DRIVE_DESCRIPTIONS = {};` | Populated with AI-generated drive descriptions keyed by raw title string |

### 7.2 Template CSS/JS Conventions

The assembler must produce output conforming to the template's JavaScript expectations:

| Element | Required Format |
|---|---|
| Destination sections | `<section id="section-{id}" class="dest-section">` |
| Nav tab buttons | `<button class="tab-btn" data-tab="section-{id}">` |
| Scenic drive buttons | `<button class="drive-link" data-drive-title="{title}">` |
| `DRIVE_DESCRIPTIONS` keys | Raw title string (e.g. `"Zion Canyon Scenic Drive"`) |

The template JavaScript queries `.dest-section` for scroll-spy, `.tab-btn[data-tab]` for navigation, and `.drive-link[data-drive-title]` for scenic drive modals.

---

## 8. Footer Policy

No attribution footer block is appended to generated itineraries.

Scenic-drive popups and other in-page modal content must not leak generator-version strings or attribution-table boilerplate into their visible body copy.

---

## 9. CLI Interface

```
python -m generator.main [OPTIONS]

Options:
  --manifest PATH          Trip manifest YAML (required)
  --output PATH            Output directory [default: output/]
  --config PATH            Config YAML [default: config.yaml]
  --llm-provider TEXT      Override LLM provider for this run
  --environment TEXT       Optional environment folder override (dev/test/prod)
  --env-file PATH          Optional .env file loaded before env resolution
  --llm-model TEXT         Override LLM model for this run
  --log-level TEXT         Console logging threshold (`debug|info|warning|error|critical`)
  --dry-run                Parse & validate manifest only; no AI calls
  --skip-images            Skip image fetching
  --refresh-image-cache    Force refresh image-provider queries, bypassing local cache
  --skip-events            Skip cultural events discovery
  --skip-url-discovery     Skip URL discovery (AI content only)
  --noschedule             Suppress schedule rendering in output HTML
  --destination TEXT       Limit to specific destination id (repeatable)
  --first-destination      Process only first destination after any --destination filtering
  --verbose                Enable debug logging
```

Output path policy:
- By default, generated files write directly under the requested `--output` directory.
- An environment subdirectory is created only when `--environment` is provided explicitly on the CLI.

Logging policy:
- Default console logging threshold is `INFO`.
- `--log-level` must allow `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL` thresholds.
- `--verbose` remains supported as a convenience alias for `DEBUG` and takes precedence over `--log-level` when both are provided.

---

## 10. Configuration (config.yaml)

Key configurable values:

| Key | Default | Description |
|---|---|---|
| `ai.temperature` | `0.7` | LLM temperature for content generation |
| `ai.max_tokens` | `3000` | Max tokens per AI response |
| `images.min_per_destination` | `2` | Hard fail threshold |
| `images.max_per_destination` | `4` | Maximum images fetched per destination |
| `images.never_content_terms` | `['underwater','scuba','snorkel','snorkeling']` | Destination-agnostic image blacklist terms that are always filtered out |
| `url_discovery.max_fallback_attempts` | `4` | Fallback query attempts per item |
| `url_discovery.uninterested_attraction_keywords` | `['golf course','country club']` | Attraction keyword blacklist for categories that should not receive discovered links |
| `url_discovery.seasonal_uninterested.ski` | Keywords + months | Ski/snow attraction suppression outside configured in-season month numbers |
| `validation.min_images_per_section` | `2` | HTML validator image count threshold |

---

## 11. Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Cond. | Required when `openai` is the selected content-generation provider |
| `OPENAI_MODEL` | ❌ | Optional default OpenAI model override |
| `OPENAI_BASE_URL` | ❌ | Optional OpenAI-compatible base URL override |
| `DEEPSEEK_API_KEY` | Cond. | Required when `deepseek` is the selected content-generation provider |
| `DEEPSEEK_BASE_URL` | ❌ | Optional DeepSeek-compatible base URL override |
| `ANTHROPIC_API_KEY` | Cond. | Required when `anthropic` is the selected content-generation provider |
| `GEMINI_API_KEY` | Cond. | Required when `gemini` is the selected content-generation provider |
| `AZURE_OPENAI_ENDPOINT` | Cond. | Required when `azure_openai` is the selected content-generation provider |
| `AZURE_OPENAI_API_KEY` | Cond. | Required when `azure_openai` is the selected content-generation provider |
| `AZURE_OPENAI_DEPLOYMENT` | Cond. | Required deployment name when `azure_openai` is selected |
| `AZURE_OPENAI_API_VERSION` | ❌ | Azure API version (default: `2024-02-01`) |
| `XAI_API_KEY` | ✅ | Required for Grok semantic search URL discovery and Grok provider usage |
| `XAI_MODEL` | ❌ | Optional Grok model override |
| `NPS_API_KEY` | ❌ | NPS API key (default: `DEMO_KEY`, rate-limited) |

API requirements summary:
- Content generation requires one configured LLM provider with its matching credentials.
- URL discovery currently requires `XAI_API_KEY` because Grok semantic search is the active discovery engine.
- Image enrichment may use NPS and third-party image sources; `NPS_API_KEY` is optional but improves park coverage.

Cost accounting note:
- LLM usage/cost summary includes OpenAI (content/drives), plus xAI Grok usage from URL discovery and cultural-event search requests.
- URL liveness/relevance HTTP checks do not consume LLM tokens and are not part of LLM-cost.

---

## 12. Output Structure

```
output/
├── index.html              ← Portable itinerary entry point
├── images/
│   ├── {md5hash}.jpg       ← Downloaded destination images
│   └── ...
├── manifest.webmanifest    ← PWA companion asset
├── sw.js                   ← Service worker asset
└── validation_report.json  ← Post-assembly validation results
```

When `--environment` is provided explicitly, the above structure is created under `output/{environment}/` instead.

---

## 13. PWA Support Requirements

- Output HTML must include installable web app metadata (manifest + app icons).
- A service worker must be registered best-effort for offline shell behavior and static asset caching.
- Install prompt UX should be exposed when browser eligibility allows.
- Direct `file://` usage must remain functional even when service worker registration is unavailable or blocked.
- PWA enhancements must degrade gracefully on insecure contexts and must not break the standalone HTML experience.

---

## 14. Print-Friendly Requirements

- Print stylesheet must hide non-essential interactive UI (map nav chrome, install buttons, galleries where needed).
- Printed output must preserve section readability, headings, and link traceability (show URL targets in print).
- Page-break behavior should keep each destination section coherent and avoid orphaned headers.

---

## 15. Per-Destination Map Requirements

- Each destination section should support an embedded local map panel showing the destination coordinate context.
- Embedded map content must not break static-host deployment and should degrade gracefully when map scripts fail.

Related popup requirement:
- Scenic-drive and day-trip modal content should not include attribution-list boilerplate.

---

## 16. Planning Link Formatting Rules

- Planning links render as compact pill-style buttons in destination headers.
- Labels should be short, action-oriented, and consistently capitalized.
- Invalid or missing URLs must be omitted rather than rendered as dead controls.

---

## 17. Month-Specific Weather Grounding Rules

- Temperature fields in `expected_environment` must be post-normalized from historical monthly climate normals using destination coordinates and trip month.
- Grounding source currently uses Open-Meteo historical daily temperatures, aggregated to monthly daytime high and overnight low normals.
- Narrative summary temperature claims must be rewritten to match grounded values.

---

## 18. En-Route Detour Display Rules

- CAN'T-MISS ENROUTE entries should display detour overhead (`detour_distance_miles`, `detour_time_minutes`) when available.
- Zero-detour stops should remain valid and may display as on-route.
- Stop cards should use content-appropriate iconography (trail, viewpoint, food, market, etc.) and should avoid forced em-dash-only sentence formatting.
