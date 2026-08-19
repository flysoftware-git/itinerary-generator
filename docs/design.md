# Road Trip Itinerary Generator — Design Document

**Version 2.1 · August 17, 2026**
Describes generator `v2.0.0`, template `v2.5`, branch `issue-6-v2`.
Aligned with [`docs/requirements.md` v2.1](requirements.md).

> **Start here.** This is the top-level introduction to the codebase. It explains
> *why the system is shaped the way it is* — the load-bearing ideas, the contracts
> between parts, and where the design is still moving.
>
> - [`README.md`](../README.md) — **how to run it**.
> - [`docs/requirements.md`](requirements.md) — **what it must do**.
> - [`docs/design/`](design/README.md) — **21 behaviour-level notes**, one per component or
>   investigation. Those are the detail; this document is the map that connects them.
> - This file — **how it is built, and why**.
>
> Two link conventions run throughout. A **“Requires:”** line points at the requirement
> sections a piece of design satisfies. A **“Design notes:”** line points at the
> `docs/design/` notes that go deeper. [Appendix A](#appendix-a--design-note-index) is
> the reverse index: every note, and where in this document it belongs.

---

## 0. How this document is organized

The design is described through the **PIANOS framework** — six keys for extracting order
from chaos, described in
[*The PIANOS framework: Six keys to extract order from chaos*](https://blog.swiftsure.pro/p/pianos).

PIANOS is a meta-framework for systems operating on the boundary between order and
chaos, and that is a fair description of this one. The generator takes a *deliberately
underspecified* input — a page of YAML naming places a human wants to visit — and must
produce a rigid, verified, publishable artifact. Between those ends sits genuine
uncertainty: what is actually at a destination, whether a page still exists at that URL
today, whether a restaurant has closed, whether a festival is real. Nearly every
subsystem here is an answer to *"how do we take a controlled walk through that
uncertainty and come back with something we can stand behind?"*

| Key | The article's framing | What it covers here |
|---|---|---|
| **P — Production** | "bringing something forward into existence"; the core engine, three interlocking gears: **Exploration**, **Evaluation**, **Transformation** | §1 — the pipeline, and the gear cycle that recurs inside every stage |
| **I — Innovation** | "researching, experimenting, and engineering novel capabilities and solutions into existence" | §2 — the genuinely novel bets: fail-closed publication, provenance as control, the entity registry, evidence-driven provider selection |
| **A — Adaptation** | "enhancing the suitability, resiliency, and efficacy of production capabilities within dynamically evolving environments" | §3 — circuit breakers, fallback chains, cooldowns, caches, degradation policy |
| **N — Navigation** | "analyzing, elaborating, and exploiting effective pathways to progress over a capability's lifecycle" | §4 — the frozen template, selective regeneration, cost and telemetry, the debt map |
| **O — Orchestration** | "collaborating across viewpoints to assure performance, exchange data and control information, and respond to feedback" | §5 — the `trip` dict, the registry as arbiter, concurrency, feedback channels |
| **S — Synthesis** | "iteratively configuring, aligning, and tuning existing capabilities to enhance their fitness for use" | §6 — normalization, the tuning surface, config-vs-code drift |

Read §1 and §5 first if you have twenty minutes. Read §3 and §4 before changing
anything. §7 is the orientation map.

**Scale, for calibration.** The `generator/` package is ~26,000 lines across 23 modules.
`url_discovery.py` alone is 12,753 lines — roughly half the codebase. That single fact
explains a great deal about where the design's attention has gone, and §1.4 explains
why.

---

## 1. P — Production

> *Production is "the act of bringing something forward into existence" — the core
> engine, depicted as three interlocking gears: **exploration** ("traversing an
> unfamiliar region of a landscape to gain knowledge about that environment"),
> **evaluation** ("assessing attributes … of a situation within a particular context"),
> and **transformation** ("following a course of action, order, or plan to translate
> goals and inputs into desired outcomes").*

### 1.1 What is produced

One file: `index.html` — a portable, deployable trip itinerary that works from `file://`
and from any static host. Companion output: `images/`, `manifest.webmanifest`, `sw.js`,
`validation_report.json`, and a family of diagnostic reports (§5.4).

The input is `trip_manifest.yaml`: a title, a theme colour, and up to 15 destinations
with dates, planning links, and optional *seeds* (name hints). Everything else —
coordinates, park codes, attractions, trails, restaurants, scenic drives, en-route
stops, cultural events, images, every URL, weather normals, and the day-by-day schedule
— is produced by the system.

That gap is the whole design problem. Almost every subsystem exists either to
**discover** something the user did not provide, or to **check** something the system
invented.

> **Requires:** [§1 Purpose & Scope](requirements.md#1-purpose--scope) ·
> [§3 Trip Manifest Schema](requirements.md#3-trip-manifest-schema-v05) ·
> [§12 Output Structure](requirements.md#12-output-structure)

### 1.2 The pipeline

`generator/main.py` (2,302 lines) is the spine — a Click CLI that runs six stages plus a
reconciliation and selective-retry block that did not exist in earlier versions.

```
trip_manifest.yaml
    │
    ▼  STAGE 1 — Parse & validate                    manifest_parser.py
       • jsonschema MANIFEST_SCHEMA
       • seed / en_route_seed URL rejection
       • destination id uniqueness
       • group_with referential integrity (no self-ref, no chains)
    │
    ▼  STAGE 2 — Geocode & enrich                    geocoder.py, nps_resolver.py
       • Nominatim: destinations and lodging  (sequential — 1 req/s ToS)
       • departure / return coordinates
       • NPS park codes, US-bounded            (parallel, 4 workers)
    │
    ▼  STAGE 3 — AI content                          ai_content.py → llm_client.py
       • ONE merged call per destination produces
         ai_content + what_to_know + scenic_drives          ← NO URLS
         (+ an optional second url-candidate call, off by default)
       • concurrency capped per provider (Grok: 1)
    │
    ▼  STAGES 4 / 5 / 5b — concurrent                (3 workers)
       ├── cultural_events.py   search → synthesis → verification
       ├── image_fetcher.py     NPS → Unsplash → Wikimedia, ranked, cached
       └── url_discovery.py     discover_all() then audit_discovered_urls()
    │
    ▼  RECONCILE                                     ai_content.normalize_trip_content
       • trip-wide normalization, dedup, banned-phrase scrubbing
       • departure-aligned drives migrate to getting_there.route_options
       • entity_registry.py: build → reconcile trip → reconcile schedule
       • destination_status_report.json
    │
    ▼  SELECTIVE RETRY                               (gated on the search circuit breaker)
       • only destinations that failed their thresholds
       • only the stages their failures implicate
       • then re-normalize and re-reconcile
    │
    ▼  STAGE 6 — Assemble & validate                 html_assembler.py, html_validator.py
       • SHA-256 checksum gate on the frozen template
       • marker substitution (str.replace — no template engine)
       • 8 validation checks; 4 fatal, 4 advisory
       • quality gate (advisory), cost summary, gate-A metrics
    │
    ▼
output[/{env}]/index.html      exit 0 · 1 input error · 2 validation failed
```

The reconciliation and selective-retry block is the architectural centre of gravity in
v2 and has no counterpart in earlier versions. §5.1 explains why it exists.

> **Requires:** [§2 System Architecture](requirements.md#2-system-architecture) ·
> [§9 CLI Interface](requirements.md#9-cli-interface)
> Note the two documents number stages differently — requirements numbers by logical
> requirement, this document by execution order.
>
> **Design notes:** [`v2-issue-6-execution-plan.md`](design/v2-issue-6-execution-plan.md) ·
> [`v2-phase-4-6-7-checklist.md`](design/v2-phase-4-6-7-checklist.md)

### 1.3 The three gears

The stage list is the surface structure. The deeper structure — and the one worth
carrying in your head — is that the same three-gear cycle recurs inside almost every
stage. Recognising it makes unfamiliar modules legible immediately.

```
                    ┌──────────────────┐
              ┌────▶│   EXPLORATION    │─────┐
              │     │  reach outward   │     │
              │     └──────────────────┘     ▼
    ┌──────────────────┐            ┌──────────────────┐
    │  TRANSFORMATION  │◀───────────│    EVALUATION    │
    │  commit to form  │            │  judge, or bin   │
    └──────────────────┘            └──────────────────┘
              ▲                                │
              └────────────────────────────────┘
                     (re-explore on failure)
```

**Exploration.** Every outward reach: Nominatim, the NPS parks and multimedia APIs, the
three image providers, Open-Meteo climate normals, the LLM content call, and — the big
one — web search through Grok, Claude, or OpenAI, used both for batch link harvesting
and per-item lookup.

**Evaluation.** Unusually heavy here, and the system's defining characteristic. Nothing
discovered is trusted:

| What is evaluated | How | Where |
|---|---|---|
| Discovered URLs | HEAD/GET liveness, then specificity, then relevance (shallow, then deep page-text) | `url_discovery._retain_discovered_url`, `_is_relevant_result` |
| Blocked vs dead | 404/410/DNS = dead; 401/403/timeout/5xx/SSL = blocked, fails *open* | `_is_definitively_dead_status` |
| AllTrails links | publish-confidence tiers (`low`/`medium`/`high`) plus slug corroboration | `_meets_alltrails_publish_confidence` |
| Restaurants | name denylist, page-text closure and pre-opening markers | `_is_restaurant_ineligible` |
| Attractions | closure markers evaluated per *sentence*, so a closed wing ≠ a closed park | `_has_attraction_closure_marker` |
| Places generally | rating and vote floors before a link is admitted | `_meets_place_interest_threshold` |
| Candidate images | token/profile scoring, marine and blacklist rejects | `image_fetcher._rank_images_for_destination` |
| Temperature claims | overwritten from Open-Meteo monthly normals | `ai_content` weather grounding |
| Marketing language | deterministic `BANNED_MARKETING_PHRASES` scrub over prose fields only | `ai_content` |
| Every entity's fate | registry validation status and rejection reasons | `entity_registry.py` |
| The template | SHA-256 against `templates/checksums.txt` | `html_assembler._verify_checksum` |
| The finished document | 8 structural and quality checks | `html_validator.py` |
| Spend | per-call token accounting, per-operation attribution | `llm_client.UsageTracker` |

**Transformation.** The normalization pipeline in `ai_content.py` (§6.3), registry
reconciliation, and `html_assembler.py`.

The gears are interdependent, not sequential — evaluation failure feeds back into
exploration. The fallback ladders of §3.2 *are* that return path, made concrete.

> **Requires:** [§5 URL Discovery](requirements.md#5-url-discovery) ·
> [§6 Image Pipeline](requirements.md#6-image-pipeline) ·
> [§7 Template Integrity](requirements.md#7-template-integrity) ·
> [§17 Weather Grounding](requirements.md#17-month-specific-weather-grounding-rules)
>
> **Design notes:** [`url-discovery-and-audit.md`](design/url-discovery-and-audit.md) ·
> [`image-selection-and-filtering.md`](design/image-selection-and-filtering.md) ·
> [`banned-marketing-language-enforcement.md`](design/banned-marketing-language-enforcement.md)

### 1.4 Why the gears are kept apart — and why URL discovery is half the codebase

The most important structural rule in the system:

> **The language model describes. It never produces a URL.**

*(One config-flippable exception exists: `ai.enable_url_candidate_experiment`, default
`false`, adds a second stage-3 call that does ask the model for `url_candidates`. Those
candidates are still subject to every gate below, but be aware the rule has a switch.)*

Every content prompt states it, `manifest_parser` enforces the mirror rule on user input
(seeds beginning `http://`/`https://` are rejected), and `url_discovery.py` exists solely
to supply what the model was forbidden to invent.

The reasoning: a language model is a good explorer of *description* and a bad explorer of
*addresses*. It will produce a plausible URL as readily as a real one, and a plausible
URL is indistinguishable from a real one until you fetch it. So references are pushed
into a stage where every candidate passes evaluation gates before it can reach the page.

The corollary is **fail-closed publication**: an item with no defensible link is either
rendered without one (marked `⚠ Unverified`) or dropped — never given a fabricated or
generic search link. That single policy is what turned URL discovery into 12,753 lines.
Getting a link is easy; *proving a link is the right one* requires the harvest path, the
per-item path, the confidence tiers, the closure detectors, the denylists, the dedup
passes, and the audit. Read
[`url-discovery-and-audit.md`](design/url-discovery-and-audit.md) and
[`fallback-curation-contract.md`](design/fallback-curation-contract.md) before touching
any of it.

> **Requires:** [§5 URL Discovery](requirements.md#5-url-discovery) ·
> [§3.4 Seeds Rules](requirements.md#34-seeds-rules)
>
> **Design notes:** [`url-discovery-and-audit.md`](design/url-discovery-and-audit.md) ·
> [`fallback-curation-contract.md`](design/fallback-curation-contract.md) ·
> [`url-quality-pr-backlog.md`](design/url-quality-pr-backlog.md)

---

## 2. I — Innovation

> *Innovation is "researching, experimenting, and engineering novel capabilities and
> solutions into existence" — advancement within unfamiliar environments.*

Six bets in this system are genuinely novel rather than assembled from convention. They
are where its character lives, and the parts most worth understanding before extending
it.

### 2.1 Separation of description from reference, and fail-closed publication

Covered in §1.4. The novel move is not noticing that models hallucinate — it is the
structural response: **remove the category of claim the model cannot verify from its job
entirely**, rebuild it from a verifiable source, and then refuse to publish anything that
fails verification. Reach for this pattern first when adding a new content type.

> **Design notes:** [`url-discovery-and-audit.md`](design/url-discovery-and-audit.md) ·
> [`fallback-curation-contract.md`](design/fallback-curation-contract.md)

### 2.2 Provenance as the controlling publish mechanism

Rather than a single boolean "is this URL good", the system tracks *where a URL came
from* and lets provenance drive downstream behaviour. A URL remembered as
**direct-batch-authoritative for this specific item** short-circuits the retention gate,
skips the audit prewarm fetch, exempts a restaurant from the freshness gate, and can be
re-admitted after a strip. A `.gov` host is treated as high-confidence provenance.
AllTrails links carry an explicit confidence tier.

This is the idea to internalise: *confidence is a property of the path a fact travelled,
not just of the fact*. It is what lets the system be strict by default without
re-verifying things it already proved.

> **Design notes:** [`provenance-control-and-scheduling-rationalization.md`](design/provenance-control-and-scheduling-rationalization.md) ·
> [`instrumentation-curation-and-provenance.md`](design/instrumentation-curation-and-provenance.md)

### 2.3 The entity registry as single arbiter of what renders

The newest and most consequential architecture. After the parallel stages, every
renderable item across six section targets — `top_attractions`, `scenic_drives`,
`getting_here.en_route_stops`, `getting_there.route_options`, `dinner_recommendations`,
`cultural_events` — is projected into a flat registry of **entities**, each with an
`entity_id`, `entity_class`, `ownership_type`, `validation_status`, `rejection_reasons`,
`rendered_url`, `section_target` and `ordering_hint`.

Three things then happen:

1. **Trip reconciliation** rebuilds every section from accepted entities only, in
   deterministic `(ordering_hint, entity_id)` order. The registry — not the discovery
   code, not the assembler — decides section membership.
2. **Tombstones.** Items removed from the trip entirely still enter the registry via
   `dest["_registry_decisions"]`, so a deletion leaves a trace instead of vanishing.
3. **Schedule reconciliation** scans the prose day-schedule for mentions of blocked or
   soft-demoted entities and substitutes surviving ones — deterministically, with no
   second LLM call. This closes a real failure mode: the model wrote "hike Angels
   Landing" in the schedule, and Angels Landing was later dropped.

The registry is **derived, not stored** — rebuilt from the trip on every call and
discarded. That keeps it honest, at the cost of rebuilding after every retry.

> **Design notes:** [`v2-issue-6-registry-schema.md`](design/v2-issue-6-registry-schema.md) ·
> [`v2-issue-6-invariants.md`](design/v2-issue-6-invariants.md) ·
> [`provenance-control-and-scheduling-rationalization.md`](design/provenance-control-and-scheduling-rationalization.md)

### 2.4 Choosing providers on measured evidence, not vendor claims

The most instructive episode in the project's history. Grok's live-search path had been
**silently dead** — xAI deprecated the chat-completions `live_search` tool (410 Gone,
"switch to the Agent Tools API"), so every harvest call had been answering from training
memory rather than the live web, with no error and no signal. The fix migrated to
`/v1/responses` with the `web_search` tool.

That discovery prompted a four-provider capability probe run against the *real*
production prompt, scored on citation fidelity — do the URLs the model embeds actually
appear in its own search citations? Grok 21/21, Claude 14/15 (93%), Gemini 19%,
OpenAI 0%. The provider×role matrix that came out of it is a *consequence of measurement*, and
[`provider-model-matrix.md`](design/provider-model-matrix.md) is its canonical record.

**It has since been overridden on cost, and that matters when reading this document.**
The note assigns Claude to the non-batch search and cultural-events roles; `config.yaml`
currently pins **all three search roles to Grok**, because a same-shape comparison run
found Claude costing ~4.6× Grok for equivalent output and the account is no longer
funded. So the matrix records the evidence, not the running configuration — check
`config.yaml` before assuming which provider is live.

The transferable practice: when a dependency's behaviour is unobservable, build the probe
before building on it.

> **Design notes:** [`search-provider-capability-probe.md`](design/search-provider-capability-probe.md) ·
> [`provider-model-matrix.md`](design/provider-model-matrix.md)

### 2.5 Deterministic enforcement of things the prompt only asks for

A recurring pattern: state the rule in the prompt, then enforce it in code, because
prompt instructions alone are routinely violated with no downstream checking.

- **Banned marketing language.** The system prompt has long banned "stunning", "iconic",
  "nestled", "hidden gem" and friends. One real run contained 28 violations —
  "stunning" ×20. `BANNED_MARKETING_PHRASES` now scrubs deterministically, scoped to an
  explicit allowlist of *prose* fields so structural fields are never touched, and
  violations are counted into the run ledger. Note the deliberate carve-out: `"must-see"`
  was removed from the banned list once it became a UI badge label.
- **Weather grounding.** Temperatures are overwritten from Open-Meteo monthly normals and
  temperature-bearing sentences rewritten.
- **Must-See badges.** The model's own `must_see` flag is not trusted for badging; the
  badge requires `rating ≥ 4.5` **and** `votes ≥ 20`, capped at 2 per destination.

> **Requires:** [§4.1 Per-Destination Content Schema](requirements.md#41-per-destination-content-schema) ·
> [§17 Weather Grounding](requirements.md#17-month-specific-weather-grounding-rules)
>
> **Design notes:** [`banned-marketing-language-enforcement.md`](design/banned-marketing-language-enforcement.md) ·
> [`building-attractions.md`](design/building-attractions.md)

### 2.6 Honest fallback as a product surface

Cultural events use a discriminated union: **Format A** (`has_events: true`) with real,
dated, source-linked events, or **Format B** (`has_events: false`) with an
`honest_assessment` of what the place is genuinely good for that season plus a
`local_tip`. The prompt carries an explicit decision tree and an explicit cost framing —
a traveller arriving to find a fabricated festival is a far worse outcome than one told
there is nothing on.

Format B is not an error path. It renders as designed content. **Designing the graceful
answer as a product surface rather than a degraded one** is the transferable idea.

> **Requires:** [§4.3 Cultural Events Schema](requirements.md#43-cultural-events-schema-has_events-decision-tree)

---

## 3. A — Adaptation

> *Adaptation is "enhancing the suitability, resiliency, and efficacy of production
> capabilities within dynamically evolving environments" — accommodating change while
> still delivering adequate fitness.*

The environment is hostile: providers deprecate tools without notice, sites bot-block
automated requests, APIs rate-limit and run out of credit, models return malformed JSON,
restaurants close, URLs rot. Adaptation is the most developed key here.

### 3.1 Circuit breakers

Four independent breakers — one per search client, one for content generation.

| Breaker | Threshold | Window | Cooldown | Half-open probe |
|---|---|---|---|---|
| Grok search | 4 | 70 s | 15 s | yes, lease-bounded |
| Claude search | 4 | 70 s | 15 s | yes |
| OpenAI search | 4 | 50 s | 15 s | yes |
| Content generation (`llm_client`) | 3 | 180 s | 45 s | **no** — no probe, no stats |

All are env-tunable (`XAI_CIRCUIT_BREAKER_*`, `ANTHROPIC_SEARCH_CIRCUIT_BREAKER_*`,
`OPENAI_SEARCH_CIRCUIT_BREAKER_*`, `LLM_CIRCUIT_BREAKER_*`).

Two hard-won details worth preserving:

- **Window sizing is not arbitrary.** The window must exceed one full round of
  concurrent-slot timeouts, or a real outage's failures never co-reside in the window and
  the breaker never trips. The original 5-failures-in-20s configuration was
  mathematically nearly unreachable — it sat closed through an observed 3.5-hour outage.
- **A half-open state, not a flood gate.** When cooldown elapses, exactly one caller
  claims a time-bounded lease and becomes the recovery probe. A successful probe fully
  resets; a failed probe reopens immediately without waiting to re-accumulate failures.
  Before this, the whole backlog rushed back at once and re-tripped, producing a flapping
  pattern that reads like a much longer outage.

Failure accounting was also a live bug: a non-2xx response returns a response object with
no exception, so HTTP-level failures were being recorded as *successes* — a 100%-failing
client reported zero trips. `raise_for_status()` now happens inside the retry helper;
retryable statuses (429, 5xx) retry in-call, non-retryable ones (400/401/403) do not
retry but still count toward the breaker.

> **Design notes:** [`search-provider-capability-probe.md`](design/search-provider-capability-probe.md) ·
> [`live-fetch-and-execution-time-reduction.md`](design/live-fetch-and-execution-time-reduction.md)

### 3.2 Fallback chains

The recurring shape: ordered most-specific → broadest, each rung a re-exploration
triggered by an evaluation failure.

**Link harvesting**, per destination × kind:

1. Primary client (`url_discovery.search_provider`) runs a purpose-built batch prompt
   asking for an HTML `<ul>` of named items with links, ratings and distances.
2. Below `min_required` rows and breaker closed → **same-provider retry** with an
   escalated prompt; kept only if it produced strictly more rows.
3. Still short → **cross-provider batch retry**: the identical batch prompt through the
   fallback client (`nonbatch_search_provider`), gated on that client's own breaker.
   This exists because of a real incident — during a 12-minute Grok outage the fallback
   client was perfectly healthy but every destination still rendered 0% linked
   attractions, because the narrower per-item fallback structurally cannot cover what a
   batch prompt covers.
   **Currently inert as cross-provider resilience:** with `nonbatch_search_provider` also
   set to `grok` (§2.4), this rung retries through a second Grok client, so that same
   incident would repeat today. The mechanism is right; the configuration has been
   reverted on cost.
4. Still empty → the legacy single-prose-query path.
5. Per item, `_search_first_strict` runs 4 query variants × 2 acceptance tiers, then deep
   relevance-checks the top 3 ranked candidates.
6. Nothing defensible → **fail closed** (§1.4).

**Images**: NPS (parks only) → Unsplash (only if `UNSPLASH_ACCESS_KEY` is set) →
Wikimedia, then up to 4 broader Wikimedia queries.

> **Requires:** [§5 URL Discovery](requirements.md#5-url-discovery) ·
> [§6 Image Pipeline](requirements.md#6-image-pipeline)
>
> **Design notes:** [`fallback-curation-contract.md`](design/fallback-curation-contract.md) ·
> [`restaurant-discovery-ranking-linkage.md`](design/restaurant-discovery-ranking-linkage.md) ·
> [`image-selection-and-filtering.md`](design/image-selection-and-filtering.md)

### 3.3 Blocked is not dead

A distinction worth stating plainly, because getting it wrong silently destroys link
coverage. `_is_definitively_dead_status` treats as dead **only** 404, 410, and connection-level
failures meaning the host does not exist or refuses all connections (DNS resolution
failure, connection refused). Timeouts, 401, 403, 5xx and SSL errors are *blocked* —
inconclusive — and fail **open**.

This is why a live page on a bot-blocking host (TripAdvisor, AllTrails) is not discarded,
and why AllTrails gets a shape-based acceptance path for `/trail/` URLs.

A related but separate carve-out lives in `url_validator.py`: an allowlisted TLS fallback
for `blm.gov`, whose certificate is misconfigured — a broken cert on a trusted host is not
a dead link. Note this applies only to **user-supplied `planning_links`**, not to
discovered content URLs, which get no `verify=False` retry.

### 3.4 Cooldowns, caches, and pacing

The performance work is largely about *not re-paying for known failures*:

- **Per-domain block cooldown** — a 401/403 host is skipped for `domain_block_cooldown_seconds`
  and returns a synthetic uncached 403, so the next attempt after expiry is real. A
  separate, stricter version exists for AllTrails with its own request pacing.
- **Negative-result cooldowns** — bounded, not permanent: `search_failure_cooldown_seconds`
  and `direct_batch_html_failure_cooldown_seconds` (both 180 s). The predecessor cached an
  empty result for the whole run with no distinction between "genuinely nothing" and "the
  request failed".
- **In-flight coalescing** — a per-`(destination, kind, dates)` lock means concurrent
  callers share one harvest instead of each triggering a network call.
- **Persistent on-disk caches** — harvest rows (24 h), search results (72 h), page text
  (24 h), URL verification (12 h), geocodes, AllTrails fetches (12 h). Empty harvests are
  deliberately never persisted.
- **Nominatim** — `url_discovery`'s own geocoding enforces a 1.1 s minimum interval under
  a lock, because the check-sleep-write sequence only *looks* enforced from a single
  thread's view. Stage 2's `geocoder.py` has no interval enforcement at all; it depends on
  being called sequentially and backs off only reactively after a 429.

> **Requires:** [§10 Configuration](requirements.md#10-configuration-configyaml)
>
> **Design notes:** [`live-fetch-and-execution-time-reduction.md`](design/live-fetch-and-execution-time-reduction.md)

### 3.5 Degradation policy

| Subsystem | On failure |
|---|---|
| Geocoding | **hard fail** — a destination that cannot be placed cannot be routed |
| AI content | **hard fail** after 3 attempts (2 retries), with non-retryable exception types excluded |
| Images | **warn** in the fetcher — but the validator turns a shortfall into a fatal error (§4.5) |
| Cultural events | **soft degrade** to Format B |
| Search clients | return `[]`; the breaker is the real signal |
| URL discovery | **fail closed** — no link rather than a wrong link |
| Quality gate | **advisory only** — never changes the exit code |

The organising principle: *hard-fail what makes the artifact structurally impossible;
fail closed on anything the reader would have to trust; degrade only where a true negative
exists and is presentable.* Decide explicitly which of these you want when adding a stage,
and record it in the module docstring.

---

## 4. N — Navigation

> *Navigation is "analyzing, elaborating, and exploiting effective pathways to progress
> over a capability's lifecycle" — balancing current operational needs against the
> positioning of future capability.*

### 4.1 The frozen template as a lifecycle boundary

`templates/v2.5_template.html` is **frozen and checksum-verified on every run**; a
mismatch is an immediate hard failure before any work begins. The template is not a
skeleton — it carries all CSS, all JavaScript (Leaflet map, drive modal, tab navigation
and scroll-spy, print export, PWA registration), the page chrome, and the CDN
dependencies.

The assembler fills four comment markers plus one source-string replacement:
`<!--TRIP_TITLE-->`, `<!--NAV_TABS-->`, `<!--DESTINATION_SECTIONS-->`,
`'<!--MAP_MARKERS_JSON-->'` (the surrounding quotes are consumed, turning a JS string
literal into an array literal), and `var DRIVE_DESCRIPTIONS = {};`. Substitution is plain
`str.replace` — no template engine, by decision.

Because the template owns the CSS and JS, assembler output must conform to conventions the
template does not declare: `class="dest-section"`, `data-tab="section-{id}"`,
`class="drive-link"` with `data-drive-title`, `DRIVE_DESCRIPTIONS` keyed by the raw title
and declared with `var`. `html_validator.py` exists largely to enforce that untyped
contract after the fact.

**One subtlety with teeth:** the checksum hashes newline-*normalized* text, not bytes. The
in-Python hash matches; an external `sha256sum -c templates/checksums.txt` on a CRLF
checkout does not. Do not wire that into CI expecting it to pass.

> **Requires:** [§7 Template Integrity](requirements.md#7-template-integrity) ·
> [§7.1 Injection Placeholders](requirements.md#71-template-injection-placeholders) ·
> [§7.2 CSS/JS Conventions](requirements.md#72-template-cssjs-conventions) ·
> [§13 PWA](requirements.md#13-pwa-support-requirements) ·
> [§14 Print](requirements.md#14-print-friendly-requirements) ·
> [§15 Per-Destination Maps](requirements.md#15-per-destination-map-requirements) ·
> [§16 Planning Links](requirements.md#16-planning-link-formatting-rules) ·
> [§8 Footer Policy](requirements.md#8-footer-policy)
>
> **Design notes:** [`html-assembly-pipeline.md`](design/html-assembly-pipeline.md)

### 4.2 Selective regeneration — the v2 orchestration model

The navigational centrepiece. A full run costs real money and minutes; re-running
everything because two destinations came out thin is waste. So the system measures each
destination and retries only what needs it.

`_build_destination_status_report` computes per destination: registry validation counts,
rejection reasons, image and event counts, en-route resolution rate, rendered-link
shortfalls. From those it derives **retry triggers** — quarantined entities, image
entities marked `needs_retry`, image shortfall, URL collapse, rendered items missing
links, URL acceptance ratio below threshold, per-section minimums not met — and
classifies the destination
`healthy | degraded | needs_retry | quarantined`.

Crucially, triggers are **attributed to stages**, so a cultural-events problem retries
only events, not images and URL discovery too. The retry pass reuses the existing
`URLDiscoverer` instance so in-memory caches survive, and is **gated on the search circuit
breaker** both before the pass and again immediately before URL retry — a real run spent
231 s retrying into a reopened breaker for zero improvement.

Retries are capped (`max_retries_per_destination_per_run`, default 1) and every
destination ends in a terminal state: `resolved_after_retry`,
`retry_cap_reached_unresolved`, `not_retried_due_to_cap`, or `stable_without_retry`.

**When debugging, note the reports are written twice.** Reconciliation and the status
report run before the retry pass and again after it, and the second run overwrites the
first — so the triggers you read on disk are the *post-retry* state, not what caused the
retry.

> **Design notes:** [`v2-issue-6-execution-plan.md`](design/v2-issue-6-execution-plan.md) ·
> [`v2-issue-6-invariants.md`](design/v2-issue-6-invariants.md) ·
> [`v2-issue-6-kickoff-checklist.md`](design/v2-issue-6-kickoff-checklist.md) ·
> [`v2-phase-4-6-7-checklist.md`](design/v2-phase-4-6-7-checklist.md)

### 4.3 Fast paths, source selection, and environments

The CLI is an instrument panel, not a convenience layer. Beyond `--dry-run`,
`--destination`, `--first-destination` and the `--skip-*` flags, four **source-selection**
flags (`--alltrails-source`, `--attraction-source`, `--restaurant-source`,
`--en-route-source`) switch a category between `search`, `direct-link-batch` and (for
trails) `apify-single-call`, and `--search-provider` forces one provider across both
url_discovery and cultural_events *and disables cross-provider fallback* — specifically so
a per-provider cost/behaviour comparison is uncontaminated.

Environments (`dev`/`test`/`prod`) resolve CLI > manifest > `ENVIRONMENT` > `dev`, and
nest the output directory **only** when the CLI form was used.

> **Requires:** [§9 CLI Interface](requirements.md#9-cli-interface) ·
> [§12 Output Structure](requirements.md#12-output-structure)

### 4.4 Cost and telemetry as steering signals

One `UsageTracker` is shared by every LLM and search client, so a run's figure covers
content generation, harvest, per-item search and event synthesis together. Every call is
tagged with an operation prefix — `destination_bundle:`, `url_candidates:`,
`cultural_events:search`, `url_discovery:*`, `url_discovery_fallback:*` — and those
prefixes are load-bearing: two separate incidents involved spend being silently excluded
from stage cost because a prefix was not recognised.

**Gate-A metrics** (`v2.1-gate-a`) roll this into provider calls by stage, stage cost,
throughput per minute, and a `batch_work_ratio` comparing actual calls against a naive
per-destination baseline — the quantitative case for batch harvesting.

Everything lands in `output/<environment>/run_ledger.jsonl` — the ledger path resolves to
the same `dev`/`eval`/`prod` value as the rest of the run (it defaults to `dev` only for the
narrow window before the manifest is parsed and the environment is known), alongside stage
timings, CLI flags, circuit-breaker stats, banned-phrase violations and retry efficiency. An
`atexit` guard writes a `terminated_without_finalize` row if the process dies, so a crashed
run is still visible.

Two caveats when reading costs: usage is recorded only on *success*, so a run that dies
after burning retries reports near-zero; and an unpriced model silently costs `$0.00` —
which is exactly how every Claude Sonnet 5 call was costed at zero until the pricing table
was corrected. Web-search server-tool fees (`tool_call_cost_usd`) are tracked and folded
into `estimated_cost_usd` — see `generator/costs.py` — but are the majority of spend on a
Grok-heavy run (§4.7), so a report that only surfaces `estimated_cost_usd` without the
token/tool split reads as far more expensive in "tokens" than it is.

> **Design notes:** [`instrumentation-curation-and-provenance.md`](design/instrumentation-curation-and-provenance.md) ·
> [`provider-model-matrix.md`](design/provider-model-matrix.md)

### 4.5 The debt map

Stated plainly, because a new contributor will otherwise find these the hard way.

1. **`url_discovery.py` is 12,753 lines, ~12,200 of them a single `URLDiscoverer`
   class.** It is the harvest client, the per-item searcher, the audit, the curator, the
   dedup engine, the geometry engine and the telemetry emitter. It has 559 tests, which is
   the only reason it is tractable.
2. **`_load_interest_filters` is a single ~630-line `try:` block.** One malformed config
   key aborts the rest of the load and silently leaves every later key at its default.
3. **`_url_cache` is a module-level global** keyed without category, surviving across
   `URLDiscoverer` instances. Latent today, because main reuses one discoverer for the
   retry pass — but it bites any caller that constructs a fresh one, which then silently
   inherits the previous instance's negative lookups while every other cache starts empty.
4. **The half-open probe lease is never cleared on success** in any of the three search
   clients. After a successful probe the stale lease persists (up to 505 s for Grok); if
   the breaker trips again inside that window, recovery is blocked far longer than the
   15 s cooldown implies.
5. **Streaming `response.failed` / `response.incomplete` raise a bare `RuntimeError`**,
   which is not a `RequestException` and therefore never reaches the breaker.
6. **Two orphan-URL counters disagree** — `html_validator._check_orphan_content_rate`
   counts `maps_url` for attractions only, `main._run_quality_gate` counts it for all
   three sections.
7. **The image-shortfall policy is contradictory** — `image_fetcher` warns, the validator
   fails the run.
8. **Half a validation check never fires**: `_check_script_isolation`'s `<section>` branch
   matches `class="destination-section"` while the assembler emits `dest-section`, so
   top-level sections are never scanned. Its `group-child-card` branch does work. The
   fixtures use the old name, so it is green in tests and half-inert in production.
9. **Three replacements target markers that no longer exist.** `<!--THEME_COLOR-->` is
   absent, so the manifest's `theme_color` is entirely inert. `<!--GOOGLE_MAPS_URL-->` is
   absent, so the trip-level Maps overview link is computed and thrown away every run (the
   nav-tab link is built separately). `<!--GENERATOR_FOOTER-->` is absent, so the footer
   falls back to injecting before the drive modal — and the `attribution-block` element
   the template's JS hides is never emitted at all.
10. **`config.yaml` declares far more than the code reads** — `template:`,
    `azure_openai:` (retry keys), `grok_search:`, `nps:`, `wikimedia:`,
    `url_verification:`, `link_types:`, `geocoding:` and the whole `validation:` block are
    wholly inert, as are several keys inside `images:` and `quality_gate:`. `validation:`
    is the actively misleading one — it looks like the validator's switchboard, and
    `min_images_per_destination` there is a decoy for the `images.min_per_destination` the
    validator actually reads. `render:` is the mirror image: the code reads it and no
    config defines it, so the debug block cannot be enabled.
11. **HTML escaping is inconsistent.** URL handling is centralized and safe
    (`javascript:`/`data:` dropped, hrefs escaped), but roughly a dozen prose fields are
    interpolated raw — including `route_summary` in `_build_getting_here` while the
    identical line in `_build_getting_there` *is* escaped. Drive descriptions and map
    marker names reach the DOM through `innerHTML`.
12. **Grouped destinations number inconsistently** — nav tabs skip grouped children while
    map markers still number them, so tab numbers have gaps and pins have no tabs.
13. **`circuit_breaker_stats` covers only two of the four breakers** — the two
    `url_discovery` search clients. The cultural-events search client and the
    content-generation breaker never reach the ledger, so a content-generation outage is
    invisible there.
14. **The probe script the design notes reference is not in the repository.**
    `search-provider-capability-probe.md` and `provider-model-matrix.md` both cite
    `scripts/probe_multi_provider_search_2026.py` as re-runnable; it is absent.
15. **Dead-but-plausible code** worth knowing about: `replay_html_capture_directory`
    (tests only), the grouped multi-destination harvest path (disabled via
    `direct_batch_group_size: 1` and never root-caused), `GrokSearch.chat_completion(live_search=False)`,
    `_build_group_lodging_pointer`, and `route_options` waypoints — which can never be
    produced because options key on `title` while the URL builder reads `name`, and the
    code relies on that accident.

> **Design notes:** [`url-quality-pr-backlog.md`](design/url-quality-pr-backlog.md) ·
> [`side-trip-exploration.md`](design/side-trip-exploration.md)

### 4.6 Release rollback

There is no runtime kill switch for v2 behaviour, and none is planned. The tool is a
batch CLI, not a served process: each invocation is a discrete, on-demand run, so nothing
is "live" between a bad run and the next `git revert` the way it would be for a system
under continuous traffic. A flag that toggles v1-vs-v2 behaviour at runtime would mean
carrying two maintained code paths indefinitely to hedge against a scenario — flip a
switch mid-incident without being able to rerun — that this architecture doesn't produce.

The source-selection flags in §4.3 (`--notrails`, `--alltrails-source`,
`--attraction-source`, `--restaurant-source`, `--en-route-source`, `--search-provider`)
already cover the fragile, externally-dependent subsystems — AllTrails' DataDome bot
gate chief among them — without any additional plumbing.

**The actual rollback plan:**

- **Trigger:** an entry-into-service run produces materially worse
  `resolved_exact` / `resolved_fallback_query` / `unresolved` / `rejected` counts than the
  `v1.4.6` baseline, or an unexplained cost spike.
- **Mechanism:** redeploy/rerun from the last known-good tag (`v1.4.6`, commit `003419f`,
  ahead of that the pre-v2 baseline `ff71a13`) until the regression is root-caused and
  fixed forward on `issue-6-v2`.
- **Not provided:** an in-place behavioural toggle. If a specific subsystem needs
  disabling mid-run without a full revert, reach for the matching §4.3 source-selection
  flag first — that covers the highest-risk surface already.

### 4.7 V2.0 entry-into-service baseline

Recorded from the run reviewed and approved for entry into service, pulled from its
ledger entry and validation report rather than re-run — **recorded only, not enforced**:
no automated check fails a future run for drifting from these numbers yet.

- **Run:** `run_id 20260819T011501.442336Z`, `environment=prod`, commit `e15f289`
  (`issue-6-v2`), manifest `C:/Dev/Sandbox/sw_manifest.yaml`, 10 destinations, wall time
  1094.3 s (~18m 14s).
- **Cost:** **$2.6549** total across 202 calls — 184 × `grok-4-fast` (1.65M tokens,
  445 web-search tool calls costing $2.225) + 18 × `gpt-4o-mini` ($0.0245). Web-search
  tool fees are ~84% of spend on this run, not token usage.
- **Quality:** `validation_report.json` — `valid: true`, 0 errors, 3 warnings
  ("Attractions removed for no verified URL: 17 (threshold 3)", "Restaurants removed: 1
  (threshold 0)", "En-route stops removed: 3 (threshold 2)"). All 10 destinations landed
  `status=degraded` / `terminal=stable_without_retry`; en-route resolution ranged 3/4 to
  8/8 per destination.
- **Caveat:** no `v1.4.6` baseline run was ever captured for a direct before/after
  comparison — the kickoff checklist's §4.2 baseline-snapshot section was left blank.
  These are absolute v2.0 numbers, not a delta against v1.4.

> **Source:** `run_ledger.jsonl`, `validation_report.json`, `destination_status_report.md`
> alongside the reviewed `index.html`.

---

## 5. O — Orchestration

> *Orchestration is "collaborating across viewpoints to assure performance, exchange data
> and control information, and respond to feedback so that intended outcomes can be
> achieved within environmental constraints."*

### 5.1 Two contracts: the `trip` dict, then the registry

There is no domain model. Every subsystem receives the same mutable `trip` dictionary,
reads what it needs, writes what it produces, and returns `None`. **Understanding this
graph is the prerequisite for understanding anything else.**

```
trip
├── trip                                   ← manifest
│   ├── title, subtitle, theme_color(inert), budget?, environment?
│   ├── departure?, departure_datetime?, return?, return_datetime?
│   ├── default_day_start_time?, default_daily_activity_hours?, attractions_per_day?
│   ├── has_high_clearance_vehicle?
│   ├── llm { provider, model, temperature, max_tokens, features? }
│   ├── llm_provider?, llm_model?, llm_features?      ← flat legacy twins
│   └── departure_lat/lng, return_lat/lng             ← stage 2
├── destinations[]
│   ├── id, name, dates, planning_links[], seeds[], en_route_seeds[]      ← manifest
│   ├── lodging { name?, location, checkin_time? }, group_with?, base_owned_categories?
│   ├── schedule_start_time?, daily_activity_hours?, attractions_per_day?
│   ├── lat, lng, lodging.lat/lng, nps_park_code       ← stage 2
│   ├── ai_content                                     ← stage 3 (one merged call)
│   │   ├── expected_environment { summary, temperature_high_f, temperature_low_f, what_to_pack[] }
│   │   ├── getting_here { route_summary, drive_time, distance_miles, en_route_stops[] }
│   │   ├── getting_there { route_summary, route_options[] }   ← created in RECONCILE,
│   │   │        last destination only; NOT produced by stage 3
│   │   ├── top_attractions[]         { name, type, difficulty, duration, must_see,
│   │   │                               description, practical_note, rating, votes,
│   │   │                               is_seed, url*, maps_url* }
│   │   ├── possible_daily_schedule[] { day, periods[ { period, summary } ] }
│   │   └── dinner_recommendations[]  { name, cuisine, price_range, description, url*, maps_url* }
│   ├── what_to_know                                   ← stage 3
│   ├── scenic_drives[]  { title, category, distance_or_duration, best_time,
│   │                      description, vehicle_requirement, url* }
│   ├── cultural_events  has_events ? { events[] } : { honest_assessment, local_tip? }
│   ├── images[]         { url, local_path, title, credit, license, source }
│   ├── _url_discovery   { reason_counts, source_counts, disposition_threads{…}, …}
│   ├── _registry_decisions[]   ← tombstones for items removed entirely
│   └── item._registry   { validation_status, rendered_url, rejection_reasons[],
│                          ownership_type?, section_target? }
└── _meta                                              ← stage 6, written last
    ├── generator_version, template_version, generated_at_utc, environment, development_build
    └── llm { provider, model, usage { models[], records[], total_calls, total_estimated_cost_usd } }

  * url, maps_url, rating, votes, is_seed, cuisine, price_range and the en-route detour
    fields are written in stage 5b into structures stage 3 created — the model never
    supplies them. This is why the Must-See badge rule (rating ≥ 4.5, votes ≥ 20) silently
    no-ops under --skip-url-discovery.
```

**The registry is the second contract, and it supersedes the first for anything
renderable.** Reconciliation deep-copies the trip and rebuilds the six section targets
from accepted entities only — so after reconciliation, `trip` is a *different object*, and
any reference held across that boundary is stale. Section membership and ordering are the
registry's decision, not the discovery code's and not the assembler's.

Consequences to internalise:

- Contracts are enforced by convention and `.get()` chains, not validation. A renamed key
  fails at render time, in one card, silently.
- Naming is inconsistent by section: most collections use `name`, but `scenic_drives` and
  `route_options` use `title` — and that difference is load-bearing in at least two places.
- `item["_registry"]` is the hand-off channel from discovery to the registry. Writing it is
  how a module votes on an item's fate.
- `_registry_decisions` survives the reconciliation deepcopy, so a post-retry rebuild
  re-emits the first pass's tombstones — rejected counts can double-count across passes.

> **Requires:** [§3 Trip Manifest Schema](requirements.md#3-trip-manifest-schema-v05) ·
> [§3.1 Trip-Level Fields](requirements.md#31-trip-level-fields) ·
> [§3.3 Destination Fields](requirements.md#33-destination-fields) ·
> [§4 AI Content Generation](requirements.md#4-ai-content-generation) ·
> [§18 En-Route Detour Display](requirements.md#18-en-route-detour-display-rules)
>
> **Design notes:** [`v2-issue-6-registry-schema.md`](design/v2-issue-6-registry-schema.md) ·
> [`building-attractions.md`](design/building-attractions.md)

### 5.2 Multi-site destination grouping

The one structural feature that changes the shape of the output rather than its content.
A destination may declare `group_with: <base_id>`, making it a day trip rendered *inside*
the base's section rather than as its own stop — Moab as a base for Arches and
Canyonlands.

`multi_site_grouping.py` is deliberately tiny and dependency-free so that the manifest
schema, URL discovery, content generation and the assembler all resolve
`base_owned_categories` identically instead of drifting into four copies. Categories
deferred to the base (default: `restaurant`) are skipped for the child and replaced with a
pointer — `📍 Dining: see Moab`. Grouped children get no nav pill, no schedule card and no
departure card; the trailing "Departure Route Options" is routed to the base when the last
manifest entry is grouped; and the base's own attractions are de-duplicated against
whatever the children cover.

Manifest validation forbids self-reference, dangling references and chains — one base,
N children, no nesting.

> **Design notes:** [`multi-site-destination-grouping.md`](design/multi-site-destination-grouping.md) ·
> [`side-trip-exploration.md`](design/side-trip-exploration.md)

### 5.3 Concurrency

```
main                       ThreadPoolExecutor(3)      events ‖ images ‖ urls
  cultural_events            ≤4                       per destination
  image_fetcher              ≤4                       per destination
  url_discovery              ≤3 destinations
    └── per destination      4                        attractions ‖ restaurants ‖ stops ‖ drives
  audit URL prewarm          ≤8                       per destination
  grouped prefetch           ≤8                       (currently disabled)
stage 3 (before the above)   provider-capped          Grok: 1; others: 4
NPS resolution               4
```

Peak during the parallel block is roughly 20 network-bound workers, and higher again while
the audit prewarm is running. Backpressure comes from per-client semaphores (Grok 8,
Claude 4, OpenAI 8), the AllTrails global fetch lock, the Nominatim interval lock, and the
circuit breakers. Note that stage 3 is *provider-capped*: Grok content generation runs
strictly sequentially, which is why the **content-generation** breaker's 180 s window is
sized against a single destination's ~186 s tenacity retry cycle rather than against a
concurrent burst — the search breakers' 70 s windows are sized against a round of
concurrent slot timeouts instead.

### 5.4 Feedback channels

| Channel | What it carries |
|---|---|
| `validation_report.json` | validity, errors, warnings, per-model cost and calls |
| `destination_status_report.json` (+ `.md`) | per-destination status, triggers, stage scope, retry outcomes |
| `run_ledger.jsonl` | stage timings, gate-A metrics, breaker stats, banned-phrase counts, retry efficiency, CLI flags |
| `entity_registry_debug.json` | full registry dump (`--verbose` only) |
| `direct_batch_parity_report.json` | harvest capture vs rendered links |
| `url_diff_report.json` (+ `.md`) | this run's URLs vs the previous run's |
| `output/dev/url_discovery_direct_batch_html/` | raw harvest captures with prompt, rows and winning provider |
| Exit codes | 0 success · 1 input error · 2 validation failed (artifacts still written) |
| `verify_links_until_clean.py` | generate → parse anchors → verify → repeat until clean |

The last one is worth knowing about: it is the outer loop the link-quality work is
actually driven by, and it encodes the single-result URL policy the assembler is written
against.

> **Design notes:** [`instrumentation-curation-and-provenance.md`](design/instrumentation-curation-and-provenance.md)

---

## 6. S — Synthesis

> *Synthesis is "iteratively configuring, aligning, and tuning existing capabilities to
> enhance their fitness for use."*

### 6.1 The tuning surface

| Surface | Where | What it tunes |
|---|---|---|
| Runtime config | `config.yaml` (~375 lines) | providers, concurrency caps, all `url_discovery` thresholds, cooldowns, cache TTLs, image counts, quality-gate limits — **and a great deal that is inert; see §4.5 item 10** |
| Policy files | `docs/policy/` | [`url_policy_allowlist.txt`](policy/url_policy_allowlist.txt), [`schedule_generation_policy.md`](policy/schedule_generation_policy.md) |
| Prompts | `prompts/*.txt` (5 files) | voice, schemas, quantity rules, banned language, decision trees |
| Normalizers | `ai_content.py` | what the model produced vs what actually renders |
| Ranking | `url_discovery`, `image_fetcher` | which candidate wins |
| Assembly | `html_assembler.py` | icons, badges, card ordering, render/hide rules |

The `url_discovery:` config block is the real control panel — rating and vote floors,
trail mileage ceilings, AllTrails confidence minimums, denylists, batch item counts per
day, all cooldowns and TTLs. It is also the block most worth reading before changing
behaviour, because much of what looks like code policy is config policy.

> **Requires:** [§10 Configuration](requirements.md#10-configuration-configyaml)

### 6.2 Prompts as design artifacts

The five prompt files are load-bearing and should be read as source. They carry the output
schemas, quantity constraints, the banned-adjective list, the anti-invention rules, and
the cultural-events decision tree. Stage 3 now issues **one merged call per destination**
producing destination content, "what to know" and scenic drives together — previously
three sequential passes.

> **Requires:** [§4 AI Content Generation](requirements.md#4-ai-content-generation) ·
> [§4.1](requirements.md#41-per-destination-content-schema) ·
> [§4.2](requirements.md#42-scenic-drives--viewpoints-schema) ·
> [§4.3](requirements.md#43-cultural-events-schema-has_events-decision-tree)

### 6.3 Normalization — where model output becomes renderable

`ai_content.py` is 2,599 lines and almost all of it is synthesis. Raw model output is never
rendered. The pipeline grounds weather from Open-Meteo, dedups attractions within and
across sections and destinations, filters chains and fast food, applies the restaurant
freshness gate and budget-driven price filtering, migrates departure-aligned scenic drives
into `getting_there.route_options`, scrubs banned marketing phrases from prose fields, and
reshapes the daily schedule.

**Schedule normalization is the deepest part** — its design note is 45 KB, the largest in
the repository, and it is the right place to start if you touch schedules. The current
model is configurable rather than hardcoded: a start-time anchor chain
(`destination.schedule_start_time` > `trip.default_day_start_time` > `10:00 AM`) and an
activity-budget chain (`destination.daily_activity_hours` > `trip.default_daily_activity_hours`
> 5 h), with capacity-aware packing that discounts the arrival day by the recorded drive
duration, extends to Day 2+, and dedups repeated content **per period** rather than only
when an entire day repeats.

The honest read of this layer: much of it corrects things the prompt already asks for and
does not reliably get. That is a reasonable division — the prompt states intent, the
normalizer enforces it. When you add a prompt rule, ask whether it also needs a normalizer.

> **Requires:** [§4.1 Per-Destination Content Schema](requirements.md#41-per-destination-content-schema) ·
> [§17 Weather Grounding](requirements.md#17-month-specific-weather-grounding-rules) ·
> [§18 En-Route Detour Display](requirements.md#18-en-route-detour-display-rules)
>
> **Design notes:** [`schedule-normalization.md`](design/schedule-normalization.md) ·
> [`policy/schedule_generation_policy.md`](policy/schedule_generation_policy.md) ·
> [`building-attractions.md`](design/building-attractions.md) ·
> [`restaurant-discovery-ranking-linkage.md`](design/restaurant-discovery-ranking-linkage.md) ·
> [`banned-marketing-language-enforcement.md`](design/banned-marketing-language-enforcement.md)

### 6.4 The synthesis loop in practice

Edit → run against a real manifest → read `destination_status_report.json` and
`run_ledger.jsonl` → open the HTML → adjust a threshold, a prompt, or a gate. The loop is
now substantially instrumented — which is exactly what made the last several rounds of
work possible, and is the clearest illustration of the framework's point that feedback is
the backbone of the work rather than a report at the end of it.

What still runs on human judgement: whether the itinerary is any *good*. There is no
golden-output test.

---

## 7. Orientation map

### 7.1 Repository layout

```
generator/                            ~26,000 lines
├── main.py               (2,302)   CLI, stages, selective retry, status reports, telemetry
├── manifest_parser.py      (362)   MANIFEST_SCHEMA + seed/id/group_with validation
├── parser.py                 (9)   re-export shim (what main imports)
├── geocoder.py              (52)   Nominatim
├── nps_resolver.py          (86)   name → NPS park code
├── multi_site_grouping.py    (77)   group_with / base_owned_categories — shared vocabulary
├── llm_client.py           (713)   MultiLLMClient, UsageTracker, pricing, content breaker, failover
├── llm/router.py            (14)   provider router (live: routes grok)
├── providers/grok.py        (80)   Grok content-generation provider
├── ai_content.py         (2,599)   merged generation call + the whole normalization layer
├── cultural_events.py      (441)   search → synthesis → verification, Format A/B
├── search_provider.py      (107)   factory; the duck-typed search interface
├── grok_search.py          (792)   xAI /v1/responses + web_search, breaker, streaming
├── claude_search.py        (638)   Anthropic Messages + web_search tool
├── openai_search.py        (574)   OpenAI /v1/responses + web_search
├── url_discovery.py     (12,753)   harvest, per-item search, audit, curation, dedup, telemetry
├── url_validator.py        (137)   planning-link liveness; blocked-vs-dead helpers
├── image_fetcher.py        (669)   NPS → Unsplash → Wikimedia, ranking, cache
├── entity_registry.py      (425)   registry build, trip reconcile, schedule reconcile
├── html_assembler.py     (2,769)   template injection + every builder + publication rules
├── html_validator.py       (320)   8 checks, 4 fatal
├── report_writer.py         (49)   validation_report.json
└── costs.py                 (40)   cost summary printing

prompts/           5 templates      docs/design/     21 behaviour notes + README
templates/         frozen v2.5      docs/policy/     URL allowlist, schedule policy
scripts/           bootstrap, run-trip, make_template, verify_links_until_clean
tests/             1,044 tests      docs/requirements.md   v2.1
                   in 24 files
```

### 7.2 Reading order for a new contributor

1. `README.md` — run it once, even with `--dry-run`.
2. `trip_manifest.yaml` alongside [`requirements.md` §3](requirements.md#3-trip-manifest-schema-v05).
3. **§5.1 above** — the `trip` dict and the registry. Nothing else makes sense first.
4. [`docs/design/README.md`](design/README.md) — the note index.
5. `generator/main.py` — the only place the whole shape is visible.
6. `prompts/destination_content.txt` — the real content specification.
7. [`url-discovery-and-audit.md`](design/url-discovery-and-audit.md) and
   [`fallback-curation-contract.md`](design/fallback-curation-contract.md) — **before**
   opening `url_discovery.py`.
8. `entity_registry.py` — small, and it explains the v2 model.
9. `html_assembler.py` — last, with §4.1 and
   [`html-assembly-pipeline.md`](design/html-assembly-pipeline.md) open beside it.

### 7.3 Testing posture

**1,044 tests across 24 test files.** The distribution tells you where the risk was felt:
`test_url_discovery.py` 559, `test_html_assembler.py` 155, `test_ai_content_normalization.py` 60,
`test_main_requirements.py` 45, `test_grok_search.py` 29, `test_claude_search.py` 25.
`docs/requirements.md` [§19](requirements.md#19-requirements-testing-linkage) establishes
the requirements-to-tests linkage, and `test-coverage.md` tracks it.

Thin spots: `test_entity_registry.py` (8) and `test_pipeline_integration.py` (2) are light
relative to how much now depends on them, and `nps_resolver`, `report_writer`,
`providers/grok` and `verify_links_until_clean` have one test each. There is still no CI —
the suite runs from `bootstrap.ps1` on a developer machine, and `-SkipTests` exists.

> **Requires:** [§19 Requirements Testing Linkage](requirements.md#19-requirements-testing-linkage)

### 7.4 Secrets and environment

Provider keys are read with `os.environ[...]` — a missing key is a hard `KeyError`, not a
graceful message — while tuning variables use `.get()` with defaults. There is **no
startup preflight**, so a missing key surfaces mid-pipeline after spend has occurred.
`UNSPLASH_ACCESS_KEY` is the quiet exception: absent, the Unsplash provider returns nothing
silently. `.env` loads only via `--env-file`.

> **Requires:** [§11 Environment Variables](requirements.md#11-environment-variables)

---

## 8. Design principles, summarized

1. **The model describes; it never references.** URLs come from discovery and
   verification. (§1.4 · [req §5](requirements.md#5-url-discovery))
2. **Fail closed.** No link beats a wrong link; an unverifiable item renders unlinked or
   not at all. (§1.4 · [`fallback-curation-contract.md`](design/fallback-curation-contract.md))
3. **Confidence is a property of provenance,** not just of the value. (§2.2 ·
   [`provenance-control-and-scheduling-rationalization.md`](design/provenance-control-and-scheduling-rationalization.md))
4. **One arbiter decides what renders.** The registry, not the discoverer and not the
   assembler. (§2.3 · [`v2-issue-6-registry-schema.md`](design/v2-issue-6-registry-schema.md))
5. **Blocked is not dead.** 404/410/DNS only. Everything else fails open. (§3.3)
6. **Measure the dependency before building on it.** (§2.4 ·
   [`search-provider-capability-probe.md`](design/search-provider-capability-probe.md))
7. **If the prompt asks for it, enforce it in code.** (§2.5 ·
   [`banned-marketing-language-enforcement.md`](design/banned-marketing-language-enforcement.md))
8. **Retry what failed, not everything.** (§4.2 ·
   [`v2-issue-6-execution-plan.md`](design/v2-issue-6-execution-plan.md))
9. **Never cache a failure permanently.** Bounded cooldowns, never run-lifetime negatives.
   (§3.4 · [`live-fetch-and-execution-time-reduction.md`](design/live-fetch-and-execution-time-reduction.md))
10. **Design the graceful answer as a product surface.** (§2.6 ·
    [req §4.3](requirements.md#43-cultural-events-schema-has_events-decision-tree))

---

## Appendix A — Design note index

Every note in [`docs/design/`](design/README.md), and where it connects to this document.

| Note | Concern | Referenced from |
|---|---|---|
| [`README.md`](design/README.md) | index + note conventions | §7.2 |
| [`building-attractions.md`](design/building-attractions.md) | generation, normalization, ordering | §2.5, §5.1, §6.3 |
| [`url-discovery-and-audit.md`](design/url-discovery-and-audit.md) | discovery, scoring, filtering | §1.3, §1.4, §2.1, §7.2 |
| [`fallback-curation-contract.md`](design/fallback-curation-contract.md) | harvest → qualify → curate → publish ownership | §1.4, §2.1, §3.2, §7.2, principle 2 |
| [`restaurant-discovery-ranking-linkage.md`](design/restaurant-discovery-ranking-linkage.md) | restaurant links, ranking, freshness | §3.2, §6.3 |
| [`image-selection-and-filtering.md`](design/image-selection-and-filtering.md) | image discovery, ranking, filtering | §1.3, §3.2 |
| [`schedule-normalization.md`](design/schedule-normalization.md) | daily schedule shaping and quality guards | §6.3 |
| [`html-assembly-pipeline.md`](design/html-assembly-pipeline.md) | structured data → final HTML | §4.1, §7.2 |
| [`banned-marketing-language-enforcement.md`](design/banned-marketing-language-enforcement.md) | deterministic prompt-rule enforcement | §1.3, §2.5, §6.3, principle 7 |
| [`provenance-control-and-scheduling-rationalization.md`](design/provenance-control-and-scheduling-rationalization.md) | provenance as publish control | §2.2, §2.3, principle 3 |
| [`instrumentation-curation-and-provenance.md`](design/instrumentation-curation-and-provenance.md) | measurable signals, reporting contract | §2.2, §4.4, §5.4 |
| [`search-provider-capability-probe.md`](design/search-provider-capability-probe.md) | the dead-search discovery and the probe that followed | §2.4, §3.1, principle 6 |
| [`provider-model-matrix.md`](design/provider-model-matrix.md) | canonical provider × role assignments | §2.4, §4.4 |
| [`live-fetch-and-execution-time-reduction.md`](design/live-fetch-and-execution-time-reduction.md) | fetch reduction, concurrency, cooldowns, failover | §3.1, §3.4, principle 9 |
| [`multi-site-destination-grouping.md`](design/multi-site-destination-grouping.md) | GH #68 grouped day trips | §5.2 |
| [`side-trip-exploration.md`](design/side-trip-exploration.md) | side-trip modelling | §4.5, §5.2 |
| [`url-quality-pr-backlog.md`](design/url-quality-pr-backlog.md) | staged plan for URL-state semantics and reporting | §1.4, §4.5 |
| [`v2-issue-6-kickoff-checklist.md`](design/v2-issue-6-kickoff-checklist.md) | v2 branch/setup | §4.2 |
| [`v2-issue-6-execution-plan.md`](design/v2-issue-6-execution-plan.md) | phased v2 execution | §1.2, §4.2, principle 8 |
| [`v2-issue-6-invariants.md`](design/v2-issue-6-invariants.md) | Phase 0 non-regression contract | §2.3, §4.2 |
| [`v2-issue-6-registry-schema.md`](design/v2-issue-6-registry-schema.md) | registry / reconciliation schema | §2.3, §5.1, principle 4 |
| [`v2-phase-4-6-7-checklist.md`](design/v2-phase-4-6-7-checklist.md) | phase 4/6/7 validation | §1.2, §4.2 |

Policy files: [`policy/url_policy_allowlist.txt`](policy/url_policy_allowlist.txt) (§6.1),
[`policy/schedule_generation_policy.md`](policy/schedule_generation_policy.md) (§6.1, §6.3).

## Appendix B — Known drift

Recorded rather than silently corrected, since the repository is under active development
on `issue-6-v2`.

- Several module docstrings predate the code they head: `image_fetcher.py` describes a
  two-provider chain and a base64 data-URI mode that does not exist; `html_assembler.py`
  says it injects an "attribution block" (it injects a generator footer, above the drive
  modal); `main.py`'s docstring omits eight of its 26 CLI flags.
- `requirements.md` §10's config table lists `ai.max_tokens: 3000`; `config.yaml` has
  `4096`.
- `config.yaml`'s `grok_search.endpoint` still points at `/v1/chat/completions`, which the
  Grok migration moved off — and nothing reads the block anyway.
- Several design notes quote provider and circuit-breaker values that have since been
  retuned in code; where a note and `config.yaml` disagree, the config and the module
  defaults are current.
- `README.md` describes the output as "self-contained"; it references Tailwind, Lucide and
  Leaflet from CDNs.
- The two documents number pipeline stages differently (§1.2).
- `requirements.md` §8 requires that no attribution footer block be appended; the assembler
  injects a generator footer unconditionally. The requirement and the code disagree —
  §4.1 links §8 for completeness, not because it is satisfied.
- `provider-model-matrix.md` and `search-provider-capability-probe.md` assign Claude to two
  search roles; `config.yaml` currently runs all three on Grok (§2.4).
- §4.3 above says environments are `dev`/`test`/`prod`; the CLI's actual `--environment`
  choices are `dev`/`eval`/`prod` — `test` was renamed to `eval` in code without the prose
  catching up.

---

*Framework: [PIANOS — six keys to extract order from chaos](https://blog.swiftsure.pro/p/pianos).*
