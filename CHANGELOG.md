# Changelog

Notable user-facing changes to the itinerary generator.

This file starts at 2.2.0. Earlier releases were tagged only by their bump
commits (`2.1.0` 2026-08-20, `2.0.1` 2026-08-19, `2.0.0` 2026-08-18) and are
recoverable from git history; they are not reconstructed here rather than
risk describing them inaccurately.

Versions follow semver as applied to an application: **minor** for new
capability, new configuration surface, or a change in the shape of the
published artifact; **patch** for fixes that leave behaviour unchanged.
`__template_version__` tracks the frozen HTML template separately and does
not move with this number.

## 2.2.0 — 2026-08-25

59 commits since 2.1.0. The theme is per-run cost: a like-for-like run fell
from **$6.32 to $2.82 (55%)**, with a Core-only run at **$1.18**.

### Added

- **Serper as a search provider** for per-item URL discovery, replacing an
  LLM used as a search engine. New `SERPER_API_KEY`, new `nonbatch_search_provider`
  config. The fallback cost **$1.65 → $0.036** and content quality improved.
  The Grok batches remain — they supply ratings and descriptions Serper does
  not carry, on every row.
- **Independent category switches** for `trails`, `en_route_stops`,
  `cultural_events` and `restaurants`, each with an `enabled` flag. This is
  the basis of the Core/premium split; development runs default the optional
  ones off. Switches are enforced at the discovery chokepoint rather than per
  call site, after four earlier per-site attempts each left a leak.
- **`place_resolver`** module, so secondary map links can name a place
  instead of describing it.
- **Content metrics and per-call-site cost attribution**, so a cost change
  can be checked against what the run actually produced rather than against
  the total alone.
- **Change-outcome tracking** (`docs/reports/change-outcomes.jsonl`), which
  measured that 67% of defects in this work escaped 1400+ passing tests.

### Changed

- **Images are published at their source URLs**, not copied from the local
  build cache. `images/` is a download-avoidance cache and was never the
  delivery mechanism; every build had been shipping its own copy of NPS,
  Wikimedia and Unsplash assets — 37 files, one of them 33 MB. The service
  worker now precaches those URLs individually at install, so installed
  itineraries still work offline.
- `grok_max_concurrent_destinations` **1 → 2**, after a measured 2.7×
  wall-clock improvement at width 3 with no circuit-breaker activity.
  Deliberately 2, not 3: the evidence was three small calls, not real Stage 3
  load.
- Direct-batch item count **20 → 12** (measured −3.6%, not the −20%
  predicted — item count drives output tokens, which are 8.6% of the total).
- Attractions **4 → 5/day**, scenic drives **2 → 1/day**, favouring trails.
- Cultural events default to **off**.
- Product renamed to **itinerary-generator**.

### Fixed

- **Grok pricing was 9× wrong** (`grok-4-fast` listed at $0.20/$0.50 against
  a real $2.00/$6.00), which is why reported spend never matched the bill.
  Unpriced models now warn instead of silently reporting $0.00.
- The cultural-events search model fell through to `XAI_MODEL`/`grok-latest`
  instead of the pinned catalog model (#65/#64).
- Hike names were being sent to the attraction batch, whose prompt excludes
  hikes; they now route to the trail batch.
- Drive time leaned short, and a test was holding the lean in place.
- Grok content-generation read timeout is now sized to the call.
- Reservation ingestion: PDF attachments are read, rasterized PDFs are
  reported rather than returning silently empty, IMAP is addressed by UID
  rather than sequence number, and Gmail app passwords are accepted with the
  spaces Google displays.

### Known

- Google Maps Platform remains unusable for both directions and imagery —
  not on terms, but because it assumes a live authenticated application and
  this product publishes a static artifact. See
  `docs/design/per-item-imagery.md`.
- Images are still fetched per destination, not per item, so several
  attractions in one stop share a photo. Scoped in the same note; free
  sources cover 91% of items.
