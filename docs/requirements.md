# Road Trip Itinerary Generator — Requirements Document
**Version 2.1 · August 2, 2026**

### Changelog for v2.1
| # | Section | Change |
|---|---|---|
| 1 | §3, §4 | Added configurable schedule-start anchors: `trip.default_day_start_time` with per-destination override `destination.schedule_start_time` |
| 2 | §3, §4 | Added configurable daily activity-time budget: `trip.default_daily_activity_hours` with per-destination override `destination.daily_activity_hours` |
| 3 | §4 | Added schedule-packing rule: when inbound transit consumes morning, afternoon should suggest multiple activities only if they fit within the configured activity-hour budget |
| 4 | §5 | Clarified publication middle-ground for named entities: canonical URL is primary, explicit `maps_url` fallback may be rendered as a fallback link when canonical URL is unavailable, and items with neither link source remain hidden |
| 5 | §5 | Fixed generic (non-AllTrails) relevance check to distinguish a blocked/transient fetch failure (403/401/timeout/5xx/SSL) from a definitively dead URL (404/410/DNS failure): a live page on a bot-blocking site (e.g. TripAdvisor) must not be treated as confirmed-dead, matching the AllTrails path's already-correct handling |
| 6 | §5, §10 | Added generic per-domain fetch-block cooldown (`url_discovery.domain_block_cooldown_seconds`), generalizing the AllTrails-specific mechanism so any bot-blocking domain avoids repeated timeout-cost re-probing |
| 7 | §5, §10 | Added per-key negative-result cooldown and in-flight coalescing for direct-batch harvest calls (`url_discovery.direct_batch_html_failure_cooldown_seconds`) so concurrent/repeat callers for the same destination/kind/dates share one failure instead of each re-triggering the network call |
| 8 | §5, §10 | Added persistent on-disk caching for successful direct-batch harvest rows (`url_discovery.persistent_harvest_cache_ttl_hours`, default 24h) so a same-day repeat run of an unchanged manifest skips re-harvesting |
| 9 | §5, §10 | Added `url_discovery.route_distance_live_fetch_enabled` (default `true`) to allow skipping the live Google Maps directions scrape in favor of the existing Haversine distance/time estimate |
| 10 | §5 | Scoped the pre-HTML-assembly audit's proactive URL prewarm to skip already-high-confidence provenance (`.gov` domains, direct-batch-authoritative harvest rows) rather than force-fetching every discovered URL regardless of established confidence |
| 11 | §4 | AI content generation now requests scenic-drive descriptions as part of the same per-destination call as destination content and "what to know" (previously a separate sequential pass); output schema and quantity rules (§4.2) are unchanged |
| 12 | §4 | Schedule reconciliation against filtered/rejected entities now runs against the final entity registry state and spans every section (attractions, restaurants, scenic drives, en-route stops, route options, events), not just top_attractions as before; also fixed a gap where within-destination dedup could silently remove an attraction/scenic-drive with no registry trace |
| 13 | §5, §10 | Fixed `_search_cached` permanently caching an empty search result for the run with no distinction between "genuinely no results" and "the request failed" — replaced with a bounded negative-result cooldown (`url_discovery.search_failure_cooldown_seconds`, default 180s), consistent with the direct-batch harvest cooldown |
| 14 | §4 | Extended capacity-aware Afternoon activity packing to Day 2+ of a multi-day stay (previously only Day 1's arrival Afternoon ever got a duration-aware multi-activity plan); fixed the arrival-day activity budget to be discounted by the recorded drive duration rather than using the full undiscounted daily budget |
| 15 | §4 | Fixed schedule day-content dedup to detect duplication per period rather than only when every period in a day was already a duplicate (a day with 2 of 3 periods repeated previously triggered no correction at all); known remaining gap: no cross-destination schedule-text dedup exists |
| 16 | §11 | Fixed a manifest/config resolution gap: flat `trip.llm_model` (a manifest convenience key symmetric with the already-supported flat `trip.llm_provider`) was silently ignored — only nested `trip.llm.model` or the `--llm-model` CLI flag worked, so a manifest using the flat form (as this project's own `sw_manifest.yaml` does) silently fell back to `config.yaml`'s default model instead of the manifest's intended override, with no warning. Resolution logic extracted to `_resolve_llm_overrides` for direct test coverage of the precedence order (nested < flat-manifest < CLI) |
| 17 | §5, §10 | Retuned the xAI search-harvest circuit breaker (`XAI_CIRCUIT_BREAKER_THRESHOLD`/`_WINDOW_SECONDS`: 5/20s → 4/30s) to match the real failure cadence under a sustained provider outage (25s per-attempt timeout, 4-concurrent semaphore cap) — the original values were mathematically nearly unreachable by that cadence, which is why the breaker never engaged during an observed 3.5-hour outage. Added an equivalent, separately-tuned circuit breaker (`LLM_CIRCUIT_BREAKER_THRESHOLD`/`_WINDOW_SECONDS`/`_COOLDOWN_SECONDS`, default 3/180s/45s) to `llm_client.py`'s content-generation calls, which previously had no cross-call failure protection at all. `main.py`'s selective-retry pass now skips entirely when the search circuit breaker is open, rather than firing a full second pass into a known-ongoing outage |
| 18 | §4 | Added deterministic, code-level enforcement of the system prompt's banned marketing-cliché list (`generator/ai_content.py`'s `BANNED_MARKETING_PHRASES`) — the prompt instruction alone was routinely violated with zero downstream checking (28 occurrences observed in one real run, "stunning" ×20 alone). Enforcement is scoped to an explicit allowlist of prose fields (description, practical_note, summary, ...) so structural fields (name, url, type, ...) are never touched. `"must-see"` is removed from the banned list — it's now used as a deterministic UI badge label (see entry above on the Must-See badge policy), which made banning it from prose self-contradictory. See `docs/design/banned-marketing-language-enforcement.md` |
| 19 | §11 | Added optional content-generation provider failover: `ai.fallback_provider`/`ai.fallback_model` (`config.yaml`) construct a second `MultiLLMClient` eagerly at startup, sharing the primary's usage tracker for centralized cost accounting. When the primary's circuit breaker opens, `generate_json` transparently routes to the fallback instead of raising, with automatic (non-sticky) recovery once the primary's cooldown expires. Scoped to content generation only — search/harvest (`grok_search.py`) has no alternative-provider implementation and is a separate, larger effort. See `docs/design/live-fetch-and-execution-time-reduction.md` §5 |
| 20 | §5, §10 | **Fixed real Grok search, which had never actually been invoked.** xAI silently deprecated the `live_search` chat-completions tool (confirmed: returns `410 Gone`, "switch to the Agent Tools API"); every direct-batch harvest call and every `GrokSearch.search()` call was running on the model's training-data memory, not live search, despite the codebase's own `live_search` parameter existing and being read. Migrated both paths to the real `/v1/responses` endpoint with the `web_search` tool. Probe evidence (4 real multi-destination test cases): with search genuinely enabled, 21/21 embedded URLs matched Grok's own search citations (582 total citations) — vs. zero citations and no verifiable provenance at all beforehand. `cultural_events.py` benefits automatically (same `.search()` method). See `docs/design/search-provider-capability-probe.md` |
| 21 | §5, §10 | Added Claude as a second working search/harvest provider (`generator/claude_search.py`'s `ClaudeSearch`, using the `web_search_20260318` tool on the Messages API), matching `GrokSearch`'s `chat_completion()`/`search()`/`is_circuit_open()` surface exactly so it's a config-selectable drop-in, not a parallel code path. `generator/search_provider.py` is the shared factory `url_discovery.py` and `cultural_events.py` both now call, each independently selecting `grok` (default, unchanged) or `claude` via `url_discovery.search_provider` / `cultural_events.search_provider` in `config.yaml`. Probe evidence: 14/15 citation-matched URLs (93%) — close behind Grok's 100%, well ahead of OpenAI (0%) and Gemini (19%) on the same task. See `docs/design/search-provider-capability-probe.md` §4 |
| 22 | §5, §10 | Split `url_discovery.py`'s single search client into two independent ones: `self._search` (direct-batch HTML harvest, `url_discovery.search_provider`, stays `grok` — the primary, highest-fidelity path) and `self._search_fallback` (the per-item `_search_cached` fallback used when batch harvest returns empty, new `url_discovery.nonbatch_search_provider` key, switched to `claude`). `cultural_events.search_provider` (already independent, purely non-batch) switched from `grok` to `claude` on the same citation-fidelity evidence. Live-validated during a genuine xAI outage: the automatic batch-empty → fallback chain transparently returned real AllTrails URLs via Claude while Grok's batch harvest was timing out completely. See `docs/design/search-provider-capability-probe.md` §6 |
| 23 | §5, §10 | Added a real half-open state to `grok_search.py`'s and `claude_search.py`'s circuit breakers. Previously, the moment cooldown elapsed, the entire concurrent backlog rushed back in at once rather than one caller — and since the 30s failure-detection window outlasts the 15s cooldown, a burst could re-trip off as few as 1-2 fresh failures stacked on stale ones, producing a flapping pattern (trip → cooldown → flood → re-trip) that can masquerade as a long outage. Now exactly one caller becomes a lease-bounded recovery probe after cooldown; a successful probe fully resets breaker state, a failed probe reopens immediately without waiting to reaccumulate `threshold` failures. Both classes now track `trip_count`/`total_open_seconds` (`get_circuit_breaker_stats()`), surfaced into `runtime_metrics["circuit_breaker_stats"]` in `main.py`. Also found (unrelated, undocumented until now): `request_delay_seconds` was a dead constructor parameter, never actually applied anywhere. See `docs/design/search-provider-capability-probe.md` §7 |
| 24 | §5, §10 | Fixed a real production gap found by reading a live run's `circuit_breaker_stats`: during a ~12-minute Grok batch outage, the fallback client (Claude) stayed perfectly healthy (0 breaker trips) but every destination still rendered 0% of `top_attractions` with a URL, because the narrower non-batch fallback (one generic `.search()` query per category) structurally can't match every specific named item a batch prompt covers — it predates the Claude/Grok split and was never redesigned around the fallback having its own working batch capability. `_fetch_direct_batch_html_rows` now retries the identical purpose-built batch prompt through the fallback client's own `chat_completion(live_search=True)` before ever dropping to the narrower mode, gated on the fallback's own circuit breaker. Debug captures now record which client's result actually won (`provider` field) for faster diagnosis next time. See `docs/design/search-provider-capability-probe.md` §8 |
| 25 | §5, §10 | Fixed the circuit breaker being blind to HTTP-level failures — a non-2xx response (e.g. Anthropic's `400 "credit balance is too low"`) got a response object back with no `requests`-level exception, so `_post_with_retries` recorded it as a **success**; `circuit_breaker_stats` showed `trip_count: 0` while every call was actually being rejected, discovered live immediately after entry #24's fix produced the same symptom again. `raise_for_status()` now happens inside `_post_with_retries` itself: retryable statuses (429/5xx) get an immediate in-call retry like a network timeout; non-retryable ones (400/401/403/...) don't retry the same call but still count toward the breaker, so repeated failures across a run correctly trip it. Live-verified against the real, still-exhausted Anthropic account: breaker opened after exactly 4 calls (`threshold`), 5th call correctly short-circuited. Also fixed, found investigating the same incident: `main.py`'s cost-attribution only recognized `url_discovery:*`, not the fallback client's separate `url_discovery_fallback:*` operation prefix — every fallback call was silently excluded from `stage_cost_usd`. See `docs/design/search-provider-capability-probe.md` §9 |

### Changelog for v2.0
| # | Section | Change |
|---|---|---|
| 1 | Global | Promoted the validated v0.30 behavioral contract to Version 2 after successful Phase 4 selective-regeneration rollout, Phase 6 invariant validation, and Phase 7 controlled end-to-end gate run |
| 2 | §9, §11 | Added destination-status observability outputs and selective-regeneration orchestration as the supported runtime model for Version 2 |
| 3 | §9, §11 | Added runtime timing and retry-efficiency instrumentation so selective regeneration can be measured against full-rerun fallback cost |

Version 2 preserves the v0.30 rendering, fail-closed URL, ownership, and template-integrity invariants while promoting the orchestration architecture that was validated in the Issue #6 execution track.

### Changelog for v0.30
| # | Section | Change |
|---|---|---|
| 1 | §4, §7 | Added final-leg rendering requirement: the last destination must include a dedicated `Getting There` block for return-route logistics (not just schedule prose) |
| 2 | §4 | Departure-aligned one-way scenic drives are no longer dropped; they must be reclassified into `ai_content.getting_there.route_options` for final-leg presentation |
| 3 | §7 | Route-map marker payload now requires explicit stop-order metadata (`stop_index`) aligned with numbered destination tabs; marker rendering must preserve date context while showing stop numbers |
| 4 | §5 | Added URL domain denylist requirement (`url_discovery.url_domain_denylist`) for config-driven hard rejection of known-untrusted domains prior to relevance scoring |
| 5 | §5 | Scenic-drive links must be route-intent specific; generic place pages that do not indicate route/byway/drive intent are not acceptable as scenic-drive `More Info` links |
| 6 | §5 | Added cross-destination scenic-drive dedup requirement: scenic drives that duplicate another destination's primary attraction concept must be removed from the conflicting destination |
| 7 | §7 | Footer issue/reporting guidance must render on a second line and provide distinct links for broken-link reports vs itinerary-feedback reports |

### Changelog for v0.29
| # | Section | Change |
|---|---|---|
| 1 | §4 | Added restaurant freshness gate: permanently closed venues and not-yet-open venues must be excluded from `dinner_recommendations` before final output |
| 2 | §4 | Added restaurant name denylist (`url_discovery.restaurant_name_denylist`): config-driven list of known-closed or ineligible venue names rejected during URL discovery audit |
| 3 | §4 | Added page-text closure and pre-opening marker detection: when a restaurant's discovered URL is fetched, presence of closure or pre-opening phrases causes the entire restaurant entry to be removed from recommendations |
| 4 | §4 | Added AI-side closure signal rejection: `_normalize_restaurants` skips restaurant entries whose AI-generated description contains explicit closure language |

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
| `default_day_start_time` | ❌ optional | Default local start time anchor for schedule realism (e.g., `10:00 AM`) |
| `default_daily_activity_hours` | ❌ optional | Default per-day activity-time budget in hours used for schedule packing (default `5`) |

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
| `schedule_start_time` | ❌ optional | Destination-specific schedule start-time override (e.g., `8:30 AM`) |
| `daily_activity_hours` | ❌ optional | Destination-specific activity-time budget override (hours) |
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
- Day-level sequencing must honor the effective schedule start-time anchor (`destination.schedule_start_time` > `trip.default_day_start_time` > `10:00 AM`).
- For non-first destinations, when inbound drive-time consumes morning capacity, Morning should be allocated to transit and Afternoon should shift to post-arrival activities.
- Afternoon may include multiple activities only when their estimated durations fit inside the effective activity-time budget (`destination.daily_activity_hours` > `trip.default_daily_activity_hours` > `5h`).
- Activities proposed after arrival to a destination must not duplicate CAN'T-MISS ENROUTE stops from that inbound leg.
- For multi-day stops, each day must render Morning, Afternoon, and Evening periods after normalization (no sparse day cards).
- For multi-day stops, each additional day should contain at least one meaningful planning variation relative to previously listed day summaries.
- For multi-day stops, cosmetic rewording alone does not satisfy variation; each additional day must include at least one substantive differentiator (distinct area, activity focus, or transfer-duty context).
- Renderer-level schedule synthesis must not override normalized structured schedule content when that content is present.

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

For non-AllTrails candidates, page text must match enough of the item name to be credible; a single shared token is not sufficient when the requested attraction/hike name contains multiple meaningful tokens. When the page-text fetch itself fails, a blocked/transient failure (403/401/timeout/5xx/SSL) must not be treated as proof the link is dead — only an explicit not-found status (404/410) or DNS-level failure is definitive; a blocked fetch falls back to a secondary liveness probe and then candidate-metadata matching before rejection.

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
- Named entities (attractions, restaurants, en-route stops, scenic drives, events) must fail closed at the canonical layer: strict discovery/audit determines whether an entity-specific URL is publishable.
- Query-style fallback URLs (for example `google.com/maps/search`) are non-canonical diagnostics/context metadata and must not be published as primary links for named entities.
- Category-level context links may use query-style fallbacks when no single named entity is implied.
- Rendering middle-ground: when canonical URL is unavailable but an explicit `maps_url` fallback is available from normalized data, renderer may publish that fallback as a secondary link treatment; items with neither canonical URL nor explicit fallback should be hidden from the rendered list.

Provenance-controlled publication requirement:
- Link publication is controlled by validated provenance state, not by name recoverability.
- Candidate discovery does not imply publishability.
- Renderers must consume validated decision outputs and must not synthesize new named-entity links from names when URL state is unresolved or rejected.

Fallback curation contract:
- Fallback handling is a multi-stage contract, not a single rule.
- URL discovery owns candidate harvesting (including snippet-extracted source links and explicit fallback metadata).
- Qualification and audit own trust/validity curation (relevance, class policy, entity integrity, and section-specific constraints).
- Renderer owns publication only and must apply section rules to curated fields rather than inventing canonical entity links.
- Publication behavior by section:
  - Attractions and en-route stops: if no publishable curated link exists, render plain text item names (no forced canonical link).
  - Restaurants: if no curated canonical URL exists, renderer may use explicit lookup fallback (`google.com/search?q=<name + destination + restaurant>`).
  - Events: if no normalized event URL exists, renderer may use query-based lookup fallback.
- Authoritative direct-link-batch mode must still pass qualification/curation gates; harvested candidates are not auto-publishable.

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
- Canonical publication remains fail-closed. If no entity-specific URL survives discovery/audit, canonical link output is empty.
- The synthesized `maps_url` search fallback is acceptable as explicit fallback rendering context when no canonical URL is available and when fallback rendering is enabled for that card/section.
- If neither canonical URL nor explicit fallback URL is available, the item must not render as an unlinked entry.

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
  --notrails               Disable trail link discovery and omit trail links
  --alltrails-source TEXT  AllTrails source for trail-like attractions (`direct-link-batch`, `search`, or `apify-single-call`)
  --attraction-source TEXT Source for non-trail attractions (`search` or `direct-link-batch`)
  --restaurant-source TEXT Source for restaurant links (`search` or `direct-link-batch`)
  --en-route-source TEXT   Source for en-route stops (`search` or `direct-link-batch`)
  --alltrails-apify-actor-id TEXT
                           Optional Apify actor id override for `apify-single-call`
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
| `url_discovery.domain_block_cooldown_seconds` | `8.0` | Generic per-domain fetch-block cooldown: after a 401/403, further fetches to other URLs on that domain short-circuit for this many seconds |
| `url_discovery.direct_batch_html_failure_cooldown_seconds` | `180.0` | Negative-result cooldown for a failed direct-batch harvest call, keyed per destination/kind/dates |
| `url_discovery.persistent_harvest_cache_ttl_hours` | `24` | On-disk TTL for successful direct-batch harvest rows |
| `url_discovery.route_distance_live_fetch_enabled` | `true` | When `false`, always use the Haversine distance/time estimate instead of live-scraping Google Maps directions HTML |
| `url_discovery.search_failure_cooldown_seconds` | `180.0` | Negative-result cooldown for a failed generic search query (`_search_cached`), keyed per query string |
| `ai.grok_max_concurrent_destinations` | `1` | Max concurrent per-destination AI-generation calls when Grok is the active provider (kept conservative pending live-load validation of newer resilience protections) |
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

---

## 19. Requirements Testing Linkage

- The authoritative requirements-to-tests linkage matrix is maintained in:
  `docs/reports/requirements-traceability-v0.30-to-v0.20.md`.
- Post-triage quality-hardening linkage and gate sequencing (provenance control,
  fail-closed publication, category stoplist handling, multi-day schedule
  rationalization) are tracked in the same report under the v2.0 addendum.

Validation cadence requirement:
1. Run focused contract gates mapped to changed requirement areas.
2. Run one controlled end-to-end smoke execution only after focused gates pass.
3. Treat smoke output as confirmation, not primary defect discovery.
