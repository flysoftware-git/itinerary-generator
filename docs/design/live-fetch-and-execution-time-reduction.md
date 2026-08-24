# Live-Fetch Reduction & Execution-Time Architecture

Status: items 1, 2, 4, 5, 6 (see §3) plus the AI-generation batching merge
(§4) are implemented and test-covered, built in an isolated working copy
(`C:\Dev\Road-trip-generator-perfwork`) so as not to disturb a manifest run
in progress in the main checkout. Item 3 (raising
`grok_max_concurrent_destinations`) remains deliberately un-changed — it's a
genuine empirical tradeoff that needs a real run to validate, not something
to flip while the only available checkout is mid-run.

Purpose: (1) assess the risk of reducing/eliminating live HTTP fetching in URL
discovery, and (2) step back from that narrow question and identify broader
architecture levers that would cut end-to-end execution time for a manifest
run, independent of live-fetch changes.

## 0. Context: how slow is "slow"?

Two very different regimes are visible in run history, and they call for
different fixes:

- **Healthy-provider baseline** (pre-session historical measurement, since
  superseded by this session's cost/runtime work — see
  `docs/reports/dipstick55_bug_triage.md` and later dipstick runs for
  current figures): a controlled single-destination run measured **408s**
  total pipeline (median of 3), and a batching candidate got that to
  **254s** (37.7% reduction). Under normal conditions the architecture is
  not catastrophic.
- **Provider-outage regime** (same historical measurement window): one
  real run recorded `stage_4_5_parallel` = **29714.926s** (~8.25 hours),
  99.72% of a ~8.28-hour total pipeline. This matches the live incident
  from today's session (70+ minutes of near-total Grok timeout rate). Several fixes
  already landed today that directly target this regime: circuit breaker,
  narrowed tenacity predicates, per-key negative-result cooldown + in-flight
  coalescing for harvest calls, AllTrails block-cooldown short-circuit.

**Implication:** most of "very slow" that you're currently experiencing is
very likely still the outage regime (or its residue), not the healthy-path
architecture. The items below are what's left to fix in the healthy-path
architecture itself, plus generalizing today's outage-regime fixes to domains
that don't have them yet (TripAdvisor, generically).

## 1. Live-fetch catalog: what each call site actually does

Full call-site inventory omitted here for brevity (available on request) —
structural summary:

Most fetch/verify logic lives in shared helpers (`_retain_discovered_url`,
`_is_relevant_result`, `_verify_url_cached`, `_alltrails_confidence_level`,
`_passes_alltrails_post_search_filters`, `_looks_like_item_specific_homepage`)
called from **both** `discover_all`'s per-item resolution methods
(`_discover_attractions`, `_discover_restaurants`, `_discover_en_route_stops`,
`_discover_scenic_drives`) **and** again directly from `audit_discovered_urls`
(the "final safety net" pass that runs after `discover_all` completes, in the
same synchronous CLI stage).

Cross-cutting redundancy: `_prewarm_url_validation_cache` (called from
`audit_discovered_urls`) does a fresh 8-worker parallel full-page-text fetch
over **every** discovered non-AllTrails URL at the start of the audit pass,
regardless of what discovery already established about that URL (a
high-confidence NPS.gov/direct-batch-authoritative URL gets the same
full-content re-fetch as a shaky one). Exact-URL cache hits make this free
*within* a single fetch type, but it can still upgrade a URL from
"liveness-checked" to "content-fetched" unconditionally.

### Risk tiers

**Tier 1 — safe to reduce, no fail-closed invariant at stake (data
extraction, not gating):**
- Trail-mileage extraction from AllTrails page text (`audit_discovered_urls`,
  used only for threshold-demotion, has an AI-description fallback already).
- Restaurant metadata enrichment (`_enrich_restaurant_metadata_from_url`) —
  cuisine/price/description backfill, not a publish gate.
- Route distance/time via Google Maps directions HTML scrape
  (`_update_route_distance_and_time` / `_parse_route_info_from_maps_html`) —
  overwrites an AI estimate with a scraped "real" value. This is scraping a
  page Google does not offer a stable API for; it is exactly the kind of
  fetch that could itself start getting blocked like AllTrails did, and a
  haversine-based estimate from already-known coordinates is a legitimate,
  free, always-available substitute.
- NPS scenic-drive page confirmation fetch, attraction closure-marker
  second-pass fetch — both narrow the result but degrade gracefully to the
  pre-fetch state on failure already.

**Recommendation:** make Tier 1 fetches best-effort with a short timeout and
silent fallback to the existing estimate/AI value on any failure (not just
timeout) — several already do this; audit for the ones that don't and align
them.

**Tier 2 — real bug, not just a performance issue:** the **generic
(non-AllTrails) branch of `_is_relevant_result`** has no blocked-vs-dead
distinction: `if not ok: return False` — any fetch failure, including a 403
from TripAdvisor or any other bot-blocking site, unconditionally rejects the
URL as if it were confirmed dead. Compare to the AllTrails/Google-Maps-place
paths, which already gate on `_is_definitively_dead_status` (only 404/410/DNS
failure counts as dead; 403/blocked does not). This means:
- A real, live TripAdvisor (or similarly-blocking) restaurant page is
  currently being **wrongly rejected**, not just slowly processed — this is
  a correctness bug living inside what looked like a performance question.
- There is no cooldown/block-tracking for this path (unlike the AllTrails
  fetch, which now short-circuits while blocked). Every subsequent
  resolution attempt against a known-blocking domain re-pays the full fetch
  timeout for a guaranteed failure.

**Recommendation:** (a) fix `_is_relevant_result`'s generic branch to use the
same `_is_definitively_dead_status` distinction as the AllTrails/Maps-place
paths — blocked ≠ dead; fall through to metadata/slug-based confidence
instead of hard-rejecting. (b) Generalize the AllTrails-specific
block-cooldown mechanism (`_alltrails_blocked_until_ts` /
`_alltrails_block_cooldown_seconds`) into a **per-domain** fetch circuit
breaker reusable for any domain, not just AllTrails — TripAdvisor is the
immediate second beneficiary, but this becomes a general defense against any
future bot-blocking source.

**Tier 3 — keep as-is, already correctly designed:** AllTrails confidence
scoring and Google-Maps-place-page entity-match checks already distinguish
blocked from dead and already degrade to corroboration-based medium
confidence rather than hard failure. These do the actual fail-closed
named-entity work the invariants require (`v2-issue-6-invariants.md` §2:
never publish a fabricated/ambiguous/unverifiable link for a named entity;
§3: a trail-like attraction without a validated trail URL renders with no
link, never a downgraded generic map-search link). **Do not remove these
fetches wholesale** — that is the actual guardrail against the "bad links
retained" defect class from earlier in this project's history.

### What "abandoning live-fetching" would risk if done bluntly

A blanket "stop fetching" change would trade one problem (slowness) for a
worse one (dead/wrong links reaching the rendered itinerary), directly
undoing the fail-closed invariant work already done. The targeted version —
fix the Tier 2 bug, generalize the cooldown, make Tier 1 best-effort — gets
most of the speed win without touching Tier 3's correctness guarantees.

## 2. Broader architecture levers (independent of live-fetch)

Ordered by estimated impact-to-effort ratio.

### 2.1 AI content generation is serialized to 1 destination at a time under Grok (highest suspected impact)

`ai_content.py`: `_llm_stage_max_workers()` returns
`grok_max_concurrent_destinations` (config default **1**) when the active
provider is Grok, vs `max_concurrent_destinations` (default 4) otherwise.
Stage 3 (`ai_gen.generate_all(trip)`) runs **before** the parallel Stage
4/5a/5b block and fully blocks on its own completion. With the default of 1,
every destination's `_generate_destination_bundle` (+ `_generate_drives`) LLM
call runs strictly sequentially — for an 8-destination trip at even
15-30s/call, that's 4-8 minutes of pure serial wall-clock before Stage
4/5b even starts, regardless of how fast those later stages are.

This cap almost certainly exists because concurrent Grok calls used to
amplify timeout/rate-limit risk with no circuit breaker to contain it.

Correction (Dipstick48 follow-up): the circuit breaker that existed as of the
original write-up above only covered `grok_search.py`'s search/harvest calls
-- it did not actually cover this setting. Content-generation calls
(`llm_client.py`, invoked from `_generate_destination_bundle`) had no
circuit breaker, no shared failure tracking, and no fail-fast path at all;
only `ai_content.py`'s per-call tenacity retry (3 attempts, 2-30s backoff,
no cross-call awareness). Raising `grok_max_concurrent_destinations` on that
basis would have removed the one thing actually limiting storming risk on
this path (low concurrency itself) without replacing it with anything.

That gap is now closed: `llm_client.py` has its own circuit breaker
(`MultiLLMClient._circuit_breaker_check`/`_record_circuit_breaker_outcome`),
tuned separately from `grok_search.py`'s (threshold=3, window=180s vs
threshold=4, window=30s) because the per-attempt timeout is longer (60s vs
25s) and concurrency can be as low as 1 (sequential) under the `ai.provider:
grok` default, not just capped at 4. See `llm_client.py`'s module-level
comment for the full derivation. **This is now the strongest candidate for a
controlled experiment**: raise `grok_max_concurrent_destinations` to 2-3 and
measure whether the new resilience holds under real load.

#### First data point (2026-08-24): width 3 held, on a small workload

The experiment above has now been run once, at width 3, and it passed. Recording
it here because a single trial is exactly the kind of result that gets
remembered as more than it was.

**Workload.** Three concurrent `llm_client.generate_json` content-generation
calls against `grok-latest`, each producing one structured object of roughly a
third of the 4096-token cap. Not a destination-content run — a smaller,
three-call shape that happens to exercise the same client, breaker and provider.

| `grok_max_concurrent_destinations` | Wall clock | First result | Cost |
|---|---|---|---|
| 1 (sequential, current default) | 237 s | 91 s | $0.0258 |
| 3 | **89 s** | 70 s | $0.0258 |

2.7x on wall clock, identical cost, and afterwards
`MultiLLMClient.is_circuit_open()` was `False`: no 429s, no timeouts, no
transient failures recorded. The resilience added since the original write-up
did hold.

**What this does not establish.** Three concurrent calls is not eight to ten,
which is what an 8-destination Stage 3 would issue at width 3 — the storming
risk this cap exists to contain scales with the number in flight, and the
measurement says nothing about that. Nor is one trial evidence about
rate-limit behaviour over a day, or under a different key's quota, or when xAI
is busy. Each call here was also smaller than a real destination bundle, so
per-call duration and token pressure both differ.

**Suggested next step**, unchanged in spirit: raise the default to 2 and run a
handful of real generations before considering 3 or 4. The gap worth closing is
between "held once at width 3 on three small calls" and "holds repeatedly at
width 2-3 on a real Stage 3". The default is deliberately left at 1 by this
change.

### 2.2 Generalize the per-domain block-cooldown (see Tier 2 above)

Same idea as 2.1: today's AllTrails-specific fix should become a reusable
primitive so every bot-blocking domain benefits, not just the one that got
manual attention.

### 2.3 Reconsider the "insufficient rows" retry-prompt under provider stress

Status: implemented (per-call level). `_get_direct_batch_html_rows_for_destination`'s
retry-prompt (fired when a harvest returns fewer than `min_required` rows)
doubled Grok exposure per call for a chance at a few more rows. Under a
circuit-breaker-open or recently-degraded state, this retry was the worst
possible moment to fire a second expensive call. It's now gated off while
`GrokSearch.is_circuit_open()` is true, without removing the retry's value
during healthy periods.

**Follow-up (Dipstick48), implemented**: the same problem existed one level
up. `main.py`'s selective-retry pass (re-runs events + images + URL
discovery for every destination flagged `needs_retry`/`quarantined` after the
main pass) used to fire unconditionally whenever any destination needed it —
including immediately after the very outage burst that caused the breaker to
trip in the first place, guaranteeing every retried destination would
independently pay a full timeout+retry cycle only to fail the same way again.
It's now gated on `url_discoverer.is_search_circuit_open()` (the same
already-populated instance from the initial pass, reused rather than
reconstructed): if the breaker is open, the whole selective-retry pass is
skipped and the initial-pass results are kept as-is, logged clearly, and
recorded in `runtime_metrics.retry_skipped_due_to_circuit_open` for the run
ledger. Recovery is left to the breaker's own cooldown on a subsequent run.

### 2.4 Scope `audit_discovered_urls`'s prewarm to what actually needs re-checking

Rather than a blanket full-content fetch over every discovered URL, skip
prewarming URLs that already carry high-confidence provenance (e.g.
`direct_batch_authoritative`, `.gov` domains) and only force-fetch the
borderline ones. Reduces both fetch volume and audit-pass wall-clock without
touching the fail-closed guarantee (high-confidence sources were already
exempted from failure in practice; this just stops re-paying for the check).

### 2.5 Replace Google-Maps-directions HTML scraping with haversine estimates (ties to Tier 1)

Already covered above — listed here because it's also a Stage 4/5b latency
item, not just a "some page might block us" risk item.

## 3. Sequencing and implementation status

1. **Done.** Fixed the `_is_relevant_result` generic-branch blocked-vs-dead
   bug (2.2 half A). `_is_definitively_dead_status` now gates rejection;
   a blocked/transient failure falls back to a secondary `_verify_url_cached`
   probe, then candidate-metadata token matching, instead of hard-rejecting.
   Tests: `test_is_relevant_result_generic_branch_*` in
   `tests/test_url_discovery.py`.
2. **Done.** Generalized the AllTrails cooldown to a per-domain mechanism
   (2.2 half B), in `_fetch_page_text`'s shared entry point
   (`_domain_blocked_until_ts` / `domain_block_cooldown_seconds`). Any
   domain, not just AllTrails, that 401/403s now short-circuits further
   fetches to other URLs on that domain. Tests:
   `test_fetch_page_text_*` in `tests/test_url_discovery.py`.
3. **Not started — needs live validation.** Raising
   `grok_max_concurrent_destinations` (2.1) under controlled measurement.
   Reuse this protocol (originally from a since-removed, now-stale
   acceptance-gate doc — the method still holds even though its specific
   numbers don't): fixed manifest/config/env, 3 full runs, median cost and
   runtime as baseline (target CoV <= 10% cost / <= 15% runtime to call the
   baseline stable); a candidate change passes if it clears at least 3 of 4
   thresholds relative to that baseline (cost reduction >= 20%, total
   runtime reduction >= 25%, stage 4-5 runtime reduction >= 30%, provider
   work-unit reduction >= 25% in at least two expensive stages), with the
   cost threshold mandatory among the three. Highest potential payoff, but
   an empirical tradeoff (today's resilience fixes are unproven at
   real-world scale) that can't be validated without a run against the
   live provider. Left at its current default (`1`).
4. **Done.** Gated the harvest "insufficient rows" retry-prompt off
   `GrokSearch.is_circuit_open()` (2.3) — no second expensive harvest call
   fires while the breaker is open. Tests:
   `test_direct_batch_html_skips_insufficient_rows_retry_prompt_while_circuit_open`,
   `test_is_circuit_open_reflects_current_breaker_state`.
5. **Done** (route distance/time; the rest already qualified). Route
   distance/time live-fetch is now skippable via
   `route_distance_live_fetch_enabled` (default `true`, preserves current
   behavior), falling straight to the existing Haversine estimate when
   disabled. Restaurant enrichment, trail-mileage extraction, and the NPS
   scenic-drive confirmation fetch were already best-effort (fail silently,
   never gate publication) and now also inherit item 2's per-domain cooldown
   automatically, since they all route through `_fetch_page_text`. Tests:
   `test_update_route_distance_*`.
6. **Done.** Scoped `audit_discovered_urls`'s prewarm by provenance
   confidence (2.4) via `_is_high_confidence_provenance_url` — `.gov`
   domains and harvest rows already marked direct-batch-authoritative skip
   the proactive bulk fetch. Tests: `test_prewarm_url_validation_cache_skips_*`.

## 4. Batching: merged scenic-drives generation into the destination bundle call

Separate from the live-fetch work above (raised in a follow-up "reduce API
calls" discussion, not originally scoped in §2): `ai_content.py`'s
`generate_all()` used to run two full sequential passes over every
destination — `generate_destination_content()` (the combined
`destination_content` + `what_to_know` call) then a second, separate
`generate_scenic_drive_descriptions()` pass calling `_generate_drives()` per
destination. Under `grok_max_concurrent_destinations: 1` this meant two
entire serialized N-destination rounds back to back.

`_generate_destination_bundle` now requests `scenic_drives` as a third
top-level JSON key in the same call (region inferred via the extracted
`_infer_region_for_destination` helper, `max_tokens` bumped by +2048 to cover
the added section). `_generate_drives` and
`generate_scenic_drive_descriptions` were removed — `generate_all` now runs
a single pass. Halves Stage 3's LLM call count and removes an entire
serialized pass under the Grok concurrency constraint from item 3 above.
Tests: `test_generate_destination_bundle_retries_transient_errors`,
`test_generate_destination_bundle_does_not_retry_programming_errors`
(migrated from the removed `_generate_drives`, same tenacity-narrowing
coverage), `test_generate_destination_bundle_uses_one_llm_call_and_normalizes_both_payloads`-style
coverage extended to the third key.

Explicitly out of scope (deferred, per user direction): whether a scenic
drive that happens to lie along the route between two destinations should
also be surfaced as an en-route stop candidate. That's a distinct feature —
cross-referencing scenic-drive location against route geometry — not
something this batching change provides.

## 5. Content-generation provider failover

Follow-up to the dipstick49 validation run (§0's "provider-outage regime"):
prompted by watching the llm_client.py/grok_search.py circuit breakers trip
correctly during a real ~40-minute Grok outage, but with no way to actually
keep making progress once tripped besides waiting out the cooldown.

**Scope decision (explicit, not full parity with GH #64/#65):** content
generation only. Search/harvest (`grok_search.py`) has no alternative-
provider implementation at all — Grok's built-in web-search capability
isn't replicated for any other provider in this codebase, and building
that (e.g. wiring up Claude's web-search tool: different API shape,
tool-use response parsing, mapping into the harvest-row format
`url_discovery.py` expects) is a separately-scoped, larger effort. This
section covers `llm_client.py`'s content-generation calls only, which
already had full multi-provider plumbing (openai/anthropic/gemini/
deepseek/grok) from GH #64/#65's groundwork.

**Mechanism:** `MultiLLMClient` accepts optional `ai.fallback_provider` /
`ai.fallback_model` config (see `config.yaml`). When set, a second, fully
independent `MultiLLMClient` instance is constructed eagerly at startup
(not lazily on first failover — a missing fallback API key must fail loudly
at construct time, not deep into a run at the exact moment the primary is
already struggling). It shares the primary's `UsageTracker` instance so
cost accounting stays centralized regardless of which provider actually
served a call (GH #66's explicit ask).

In `generate_json`, after the primary's own cache is checked (a cache hit
is served regardless of breaker state — no need to fail over for something
already known), if the primary's circuit breaker is open and a fallback is
configured, the call is transparently delegated to the fallback instance's
own `generate_json` instead of raising `LLMCircuitOpenError`. The fallback
runs through its own full stack (its own cache, its own breaker, its own
transient-error classification) — this is deliberately a single level of
failover, no chains. Recovery is automatic and not sticky: since
`is_circuit_open()` is checked fresh on every call, as soon as the
primary's cooldown expires, subsequent calls go straight back to it without
needing to explicitly "switch back."

A `fallback_provider` equal to the primary `provider` is ignored (logged,
not acted on) — both because it's nonsensical and because it would recurse
into constructing a fallback-of-itself via the same config.yaml.

Not implemented: manifest-level fallback override (only config.yaml-level
for this first cut), and any failover for the search/harvest layer (the
harder, higher-value half of the original outage — see scope decision
above; tracked separately, not yet scheduled).

Tests: `test_generate_json_fails_over_to_fallback_when_primary_circuit_open`,
`test_generate_json_without_fallback_still_raises_when_circuit_open`,
`test_generate_json_failover_shares_usage_tracker_with_primary`,
`test_construct_builds_fallback_client_from_config_and_fails_fast_on_missing_key`,
`test_construct_ignores_fallback_provider_matching_primary` in
`tests/test_llm_client.py`.
