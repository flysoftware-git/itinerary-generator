# Search-Provider Capability Probe & Grok Live-Search Fix

Status: Grok fix (§1-2) implemented and test-covered. Cross-provider probe
(§3) run twice against real APIs (initial pass + a fixes-applied rerun).
Claude added as a second working search/harvest provider (§4), implemented
and test-covered; live end-to-end validation blocked by an Anthropic
account credit-balance issue (not a code problem -- see §4.1). Probe script
hardened into a repeatable, parameterized, history-tracking CLI (§5).
Grok/Claude batch-vs-fallback provider split, live-validated during a
genuine xAI outage (§6). Cross-provider batch retry added after a real
production run showed the narrower single-query fallback couldn't match
item-specific coverage even while fully healthy (§8). Fixed the circuit
breaker being blind to HTTP-level failures (§9), found immediately after
via the same live-run-then-fix-then-verify loop -- a persistent
account-level failure (exhausted Anthropic credit) registered as 100%
healthy in circuit_breaker_stats while genuinely failing 100% of calls.
Confirmed real Sonnet 5 pricing, fixed a silent $0.00 cost-estimator bug,
capped search rounds per call, and added a `--search-provider` CLI flag
for clean single-provider comparison runs (§10). Half-open circuit-breaker recovery implemented
and test-covered (§7). OpenAI/Gemini capability follow-up remains open
(tracked via the same probe, not yet separately scoped).

## 0. Why this investigation started

Prior sessions built LLM-provider normalization for **content generation**
(`llm_client.py`, GH #64/#65) and a content-generation-only failover
(`live-fetch-and-execution-time-reduction.md` §5), explicitly scoped away
from search/harvest because "Grok's built-in web-search capability isn't
replicated for any other provider in this codebase."

Before building that failover for search/harvest too, the actual question
had to be answered first: **is Grok's own search even working today?**, and
**how do the other four providers (OpenAI, Claude, Gemini, plus Grok itself)
compare on search/harvest-shaped queries**, given that the original choice
of Grok for this role was made based on an OpenAI JSON-payload problem that
could as easily have been an interface bug as a capability gap, and given
that every provider in this space is actively evolving and differentiating
over time (so any comparison is a snapshot, not a permanent verdict).

## 1. Root cause: Grok's `live_search` tool was silently deprecated

`grok_search.py`'s `chat_completion(..., live_search=True)` and
`_grok_search()` (used by `.search()`, and transitively by
`cultural_events.py`) both built requests using xAI's chat-completions
`live_search` tool. A direct API call against that tool now returns:

```
410 Gone — "Live search is deprecated. Please switch to the Agent Tools API"
```

This means **every direct-batch HTML harvest call and every `.search()`
call had been running on the model's training-data memory, not live
search**, for an unknown period — despite `live_search` existing as a
parameter and being read correctly. The two call sites in
`_get_direct_batch_html_rows_for_destination` (`url_discovery.py`) were
also hardcoded to `live_search=False`, compounding the problem: even a
working `live_search` tool would not have been reached for the primary
harvest path.

Confirmed via `docs.x.ai/docs/guides/tools/overview`: xAI moved search to a
separate `/v1/responses` endpoint with an `Agent Tools API` shape —
different request fields (`input` string instead of `messages` array),
different response shape (`output` array of interleaved `reasoning` /
`web_search_call` / `message` items instead of `choices`), different usage
field names (`input_tokens`/`output_tokens` instead of
`prompt_tokens`/`completion_tokens`).

## 2. Fix: migrate both search paths to `/v1/responses`

`generator/grok_search.py`:
- Added `GROK_RESPONSES_ENDPOINT`. `_post_with_retries` generalized to take
  an `endpoint` parameter, preserving existing circuit-breaker/retry
  behavior for both endpoints.
- Added `_extract_responses_text` (walks `output`, keeps only
  `message`/`output_text` blocks) and `_record_responses_usage` (reads
  `input_tokens`/`output_tokens`).
- `chat_completion(..., live_search=True)` now posts to
  `GROK_RESPONSES_ENDPOINT` with `{"input": ..., "tools": [{"type":
  "web_search"}]}`. The `live_search=False` path is unchanged (old
  endpoint, old shape) — this fix only touches the code path that was
  supposed to be doing real search.
- `_grok_search` (backing `.search()`) rewritten to always use
  `/v1/responses` with `tools: [{"type": "web_search"}]` and
  `text.format: {"type": "json_object"}`, keeping the same
  `{"results": [{"title","url","snippet"}]}` contract and the same
  malformed-JSON stricter-retry behavior downstream code already depends
  on.

`generator/url_discovery.py`: both `_get_direct_batch_html_rows_for_destination`
call sites flipped from `live_search=False` to `live_search=True`, now that
the flag actually does something.

`cultural_events.py` required no changes — it only calls
`self._search.search(...)`, so it inherits the fix automatically.

Tests: 6 new cases in `tests/test_grok_search.py` (endpoint/payload shape,
reasoning/search-call items correctly skipped, usage tracked from the new
field names, the `live_search=False` path proven unchanged, `.search()`
hitting the new endpoint, malformed-JSON retry still working) plus
`test_url_discovery.py`'s
`test_direct_batch_html_uses_real_live_search` (renamed/flipped from a test
that had been asserting the bug as correct behavior). Full suite: 713
passing.

Validated against the real production `GrokSearch` class (not mocked): both
`chat_completion(live_search=True)` and `.search()` return real, verifiable
URLs (e.g. Hickman Bridge, Cassidy Arch trail pages) and correctly surface a
generic TripAdvisor listing page for downstream Tier 1 filtering to catch —
i.e., real search results with real noise, not hallucinated content.

## 3. Cross-provider capability probe

### 3.1 Method

`scripts/probe_multi_provider_search_2026.py` imports `URLDiscoverer`
directly from `generator.url_discovery` so probe prompts are the **real,
current** production prompt (`_direct_batch_html_prompt`) rather than a
hand-copied duplicate — a prior throwaway experiment script
(`experiment_gemini_vs_xai_html_candidates.py`, found stale during this
investigation) had drifted from production and also ran its Gemini call
with no grounding/search tool enabled at all, making it a pure
hallucination-risk test rather than a real search-quality comparison.

Four real, multi-destination test cases, chosen to include a
previously-problematic case shape: `(Zion National Park, trail)`,
`(Zion National Park, attraction)`, `(St. George UT, restaurant)`,
`(St. George UT, attraction)`.

Each provider config's raw HTML response is parsed with the actual
production parser (`_direct_batch_rows_from_html`), each embedded URL is
independently verified live (HEAD, GET fallback on 403/405), and — the key
metric — cross-checked against the provider's own search citations.

**Citation-fidelity metric** (`html_urls_not_in_citations`): the set of
embedded `<a href>` URLs in a provider's response that do *not* appear
anywhere in that same response's own search-citation metadata. This proved
to be the cleanest available signal because it's independent of
AllTrails/TripAdvisor bot-blocking (which pollutes simple alive/dead HTTP
checks with false negatives) — it directly answers "did the provider's
search actually produce this URL, or did the model just write a
plausible-looking one?"

### 3.2 Provider-specific API shapes discovered (all confirmed against
current, August-2026 provider docs — not assumed from training knowledge,
since two of the four next-worked providers turned out to have changed
their search APIs within the training window)

- **Grok**: see §1-2 above.
- **OpenAI**: `gpt-5-search-api` (the older `gpt-4o-search-preview`/
  `gpt-4o-mini-search-preview` models are deprecated 2026-07-23). Rejects
  the `temperature` parameter entirely — sending it is a hard 400.
  Citations live at `message.annotations[].url_citation`
  (`url`/`title`/`start_index`/`end_index`).
- **Claude**: `web_search_20260318` tool on the Messages API (GA since
  April 2026). Returns explicit `web_search_tool_result` content blocks
  (`url`/`title`/`page_age`) plus per-text-block `citations` arrays
  (`web_search_result_location`: `url`/`title`/`cited_text`).
- **Gemini**: `google_search` grounding tool. Citations live at
  `groundingMetadata.groundingChunks[].web.uri/title`, with
  `groundingSupports` mapping specific text spans back to sources.

### 3.3 Results (rerun, after fixing the Grok endpoint and the OpenAI
`temperature` 400)

| Config | Citation fidelity | Notes |
|---|---|---|
| Grok (`/v1/responses`, `web_search`) | **21/21 matched (100%)** | 582 total citations across the 4 cases |
| Claude (`web_search_20260318`) | **14/15 matched (93%)** | 3/4 initial calls timed out at 90s — fixed by raising to 150s in the rerun; one case returned fewer rows than requested (not investigated further) |
| Gemini (`google_search` grounding) | **4/21 matched (19%)** | Grounding tool present, but most embedded URLs are not present in `groundingChunks` — the model is still writing plausible URLs largely independent of what it actually retrieved |
| OpenAI (`gpt-5-search-api`) | **0/21 matched (0%)** | Complete fabrication despite the search tool being genuinely invoked — every embedded URL failed to match any `url_citation` |

**Reading these results**: Grok, once actually pointed at its real search
endpoint, is currently the most trustworthy of the four for this
harvest-shaped task. Claude is close behind and workable. Gemini and OpenAI
both invoke a real search tool but the model's written-out URLs frequently
don't correspond to what was actually retrieved — i.e., the failure mode
for those two is *provenance*, not "no search happened." This matches (and
gives concrete evidence for) the original reason Grok was chosen over
OpenAI for this role — it was not merely an interface artifact.

(Historical note: the fixed-config rerun originally lived in a separate
`scripts/probe_rerun_fixed.py`, written as a fast patch-and-recheck pass
rather than a redesign. §5 folded it into the main probe script as its
default per-provider config — that split no longer exists.)

Raw output: `output/dev/multi_provider_search_probe/20260814T233928Z/`
(first pass) and `output/dev/multi_provider_search_probe/20260814T235502Z-rerun/`
(fixes-applied rerun).

## 4. Forward plan (per explicit direction: "fix Grok, then get Grok
working for the non-batch stuff, then Claude for both")

1. **Done.** Grok fixed for both the direct-batch HTML harvest path and the
   non-batch `.search()` path (§1-2).
2. **Done.** Added Claude as a second working search/harvest provider for
   both paths. `generator/claude_search.py`'s `ClaudeSearch` matches
   `GrokSearch`'s `chat_completion()`/`search()`/`is_circuit_open()` surface
   exactly (own thread-local sessions, own circuit breaker in its own
   `ANTHROPIC_SEARCH_*` env-var namespace, same malformed-JSON retry
   contract), built on the exact request/response shape the §3 probe
   already validated against the real API (`web_search_20260318` tool,
   Messages API). `generator/search_provider.py` is a small shared factory
   (`build_search_client`) that both `url_discovery.py` and
   `cultural_events.py` now call instead of constructing `GrokSearch`
   directly; each picks its provider independently via
   `url_discovery.search_provider` / `cultural_events.search_provider` in
   `config.yaml` (`"grok"` default — unchanged behavior unless explicitly
   switched to `"claude"`). Tests: `tests/test_claude_search.py`,
   `tests/test_search_provider.py`, plus updated patch targets in
   `tests/test_url_discovery.py` (construction now happens inside
   `search_provider.py`, not `url_discovery.py`, so the two model-inheritance
   regression tests patch `generator.search_provider.GrokSearch`).
3. **Needs work, not blocking.** OpenAI and Gemini both have a real
   `web_search`/`google_search` tool wired up in the probe, but both show
   citation-fidelity failures serious enough (0% and 19%) to disqualify
   them for this role today. Whether that's fixable with response-format
   constraints, stricter prompting, or is a genuine current capability gap
   is unresolved — tracked as follow-up investigation, not scheduled yet.

### 4.1 Live validation: an account block, then a real bug it caught

A real (non-mocked) end-to-end call against the production `ClaudeSearch`
class — the same validation method used to confirm the Grok fix — first
returned `400 invalid_request_error: "Your credit balance is too low to
access the Anthropic API."` This was an account billing state, not a code
defect (resolved once credit was added).

With billing resolved, the same live call then surfaced a real bug:
`400 "temperature is deprecated for this model"`. `ClaudeSearch` had added
a `temperature` field to every request (modeled on `GrokSearch.chat_completion`'s
signature), but the §3 probe's `call_claude` — the function whose exact
payload shape `ClaudeSearch` was supposed to match — never sent `temperature`
at all. This was a real divergence between the implementation and the
shape that had actually been validated, caught only because live
validation was retried rather than accepted as "blocked, unit tests pass."
Fixed by dropping `temperature` from both request payloads entirely
(`chat_completion` and `_claude_search`); the parameter stays in
`chat_completion`'s signature for interface parity with `GrokSearch` but is
now documented as accepted-and-ignored, same treatment as `response_format`.

Re-validated live after the fix: both `search()` and
`chat_completion(live_search=True)` returned real, correctly-formed
AllTrails URLs (Angels Landing Trail, Zion Narrows) with no further errors.

## 5. Repeatable monitoring

Per explicit direction, this comparison should not be a one-off: "a
repeatable test that can be run again to track blocking issue relief over
time, and can be scheduled to run periodically on all to monitor shifts in
an evolving landscape." `scripts/probe_multi_provider_search_2026.py` was
hardened into a standalone, idempotent CLI:

- **Parameterized config selection**: `--providers grok,openai,claude,gemini`
  (default: all four). A provider whose API key isn't set in the running
  environment is skipped with a clear `[skip] <provider>: <ENV_VAR> not set`
  line rather than crashing the whole run — a scheduled/unattended run
  shouldn't hard-fail because one provider's key is absent from that
  environment.
- **One canonical config per provider by default**: the earlier "v1
  broken / v2 fixed" split (`grok_live_search` vs the old separate
  `probe_rerun_fixed.py` script) is gone — each provider now has one
  current-best call shape as its default (`grok_live_search`,
  `openai_search`, `claude_web_search`, `gemini_google_search`).
  `--include-baselines` opts back into the older no-search/comparison
  configs (`grok_no_search`, `openai_plain_html`, `openai_plain_json`) for
  before/after-style investigation, keeping the default scheduled run cheap
  (4 configs × 4 cases = 16 calls, not the original 7 × 4 = 28).
- **Stable, comparable report format across runs**: every run now also
  computes a `summary` block (`summarize_provider_runs`) per config —
  `citation_fidelity_pct`, `rows_with_url`, `rows_verified_alive/dead`,
  `cases_errored` — rolled up across all test cases, in addition to the
  full per-case detail. That summary line (plus the run timestamp) is
  appended to `output/dev/multi_provider_search_probe/history.jsonl`
  (append-only, one compact JSON object per run) — the mechanism that
  actually makes "track blocking issue relief over time" usable, since
  diffing two `report.json` files by hand doesn't scale but comparing two
  `history.jsonl` lines does.
- **Model pins are env-overridable** (`PROBE_GROK_MODEL`,
  `PROBE_OPENAI_SEARCH_MODEL`, `PROBE_CLAUDE_MODEL`, `PROBE_GEMINI_MODEL`,
  etc.) rather than hardcoded, anticipating that every provider will keep
  renaming/retiring search-capable models over time (already observed
  twice in this investigation alone: OpenAI's `gpt-4o-*-search-preview` →
  `gpt-5-search-api`, xAI's chat-completions `live_search` → `/v1/responses`).

**Scheduling mechanism deliberately left to the operator, not decided
here.** The script is now a clean CLI entry point
(`python scripts/probe_multi_provider_search_2026.py [--providers ...]`);
running it periodically is an infrastructure choice (Windows Task
Scheduler, cron, CI, or an agent-side scheduled wakeup) with different
cost/notification tradeoffs each way, so it wasn't picked unilaterally.

## 6. Production split: Grok for batch, Claude for non-batch/cultural-events

Per-activity vs. wholesale provider selection was an explicit design
question (not just theory — Grok's own outage below made the answer
concrete): should one config knob govern every search call, or should
different call shapes be pinned independently? Decoupled, because the two
task shapes have different capability profiles and different failure
domains (§0, §3.3) and there's no cost to letting each pick its own
provider when they're already both wired up.

**The two `url_discovery.py` call shapes weren't actually one client.**
`_get_direct_batch_html_rows_for_destination` (the HTML-mode harvest,
`chat_completion(live_search=True)`) is the primary, highest-volume path.
But every one of the four discovery categories (trail/attraction/
restaurant/en-route) *also* falls back to `_get_direct_batch_rows_for_destination`
(JSON-mode, via `_search_cached` → `.search()`) whenever the HTML harvest
comes back empty — this fallback existed before this change, just always
using the same client as the batch call. `url_discovery.py`'s `__init__`
now builds two independent clients:

- `self._search` — batch/harvest, `url_discovery.search_provider`
  (`config.yaml`), stays **grok** (strongest evidence: 100% citation
  fidelity, and it's the primary path).
- `self._search_fallback` — the per-item `_search_cached` fallback
  (`_search_first`/`_search_first_strict` also route through it),
  `url_discovery.nonbatch_search_provider`, switched to **claude** (93%
  citation fidelity on the same non-batch shape).

`_search_cached` prefers `self._search_fallback` when present, falling
back to `self._search` when it isn't (partially-constructed test doubles)
— production always sets both, so the fallback-to-`self._search` branch
never fires there. `cultural_events.search_provider` (already independent,
100% non-batch module) switched from grok to claude on the same evidence.

`generator/search_provider.py`'s `build_search_client` gained a
`provider_key` parameter (default `"search_provider"`) so the same factory
serves both `url_discovery.search_provider` and
`url_discovery.nonbatch_search_provider` from one config section.

**Manifest-level override**: not added. Search-provider selection remains
`config.yaml`-only (no `trip.search_provider` in `manifest_parser.py`'s
schema) — neither `trip_manifest.yaml` nor the Sandbox validation
manifest (`sw_manifest.yaml`) set anything that conflicts with it (the
latter does set `trip.llm_provider: openai` for *content generation*,
which is the independent axis this section doesn't touch). Adding a
manifest override is a reasonable next step if a specific trip ever needs
to diverge from the config.yaml default, but wasn't required to make this
change effective.

**Live validation, 2026-08-15 — proved the design's actual point, not
just its wiring.** While smoke-testing this change, xAI's API was
observed genuinely down (`ReadTimeoutError`, consistent across content
generation *and* search calls — a real outage, not a test artifact).
Client-construction wiring was confirmed correct
(`type(self._search).__name__ == "GrokSearch"`,
`type(self._search_fallback).__name__ == "ClaudeSearch"`), and calling the
production wrapper `_get_alltrails_direct_batch_rows_for_destination`
against real Zion National Park data returned 2 valid AllTrails trail URLs
(Canyon Overlook Trail, Kayenta Trail to the Emerald Pools) *despite*
Grok's batch harvest timing out completely — the automatic HTML-empty →
JSON/Claude-fallback chain absorbed the outage transparently. A direct
`self._search_fallback.search(...)` call and a `CulturalEventsDiscoverer`
call both independently confirmed real, correctly-provenanced Claude
results (AllTrails URLs; Zion Canyon Music Festival / Greater Zion Events
Calendar). This is the resilience benefit argued for in the "why not
harmonize to one provider" discussion, demonstrated live rather than
theoretically.

Tests: `test_url_discoverer_builds_grok_batch_client_and_claude_fallback_client_from_real_config`,
`test_search_cached_prefers_fallback_client_over_batch_client_when_both_set`,
`test_search_cached_falls_back_to_batch_client_when_fallback_client_unset`
in `tests/test_url_discovery.py`.

## 7. Half-open circuit-breaker recovery (2026-08-15)

Follow-up investigation, prompted by a direct question: given the
observed timeouts, "is there a throttle, and can concurrency defeat the
pause strategy?"

**Diagnosis.** `grok_search.py`'s own pre-existing module comment
(`_GROK_SEMAPHORE`) already documents the mechanism: *"16 parallel
threads → xAI rate-limits → all time out. Keep it at 4."* xAI's
throttling doesn't return a clean `429` — it hangs the connection until
the client's own timeout fires, so "rate-limited" and "genuinely
degraded" are indistinguishable from the client side. A live,
single-request, unconcurrent diagnostic call during the same period
(`GrokSearch().chat_completion(...)`, no other traffic in flight)
returned cleanly in under 5 seconds, and a real search in 18.5s — strong
evidence a concurrent smoke-test run's timeouts were substantially
self-inflicted (our own request volume), not a blanket provider outage.

**The specific flaw**: the breaker was strictly binary (open/closed), no
half-open state. The moment cooldown elapsed, the *entire* concurrent
backlog (up to `_GROK_SEMAPHORE`'s cap of 4, fed continuously by
`url_discovery.py`'s per-destination × per-category parallelism, which
can queue well more than 4 logical callers behind that one semaphore)
rushed back in at once — not "one lucky ticket gets tested," but a full
flood. Worse: the failure-detection window (30s) is longer than the
cooldown (15s), so some pre-trip failures were still "in window" the
instant cooldown ended — a burst could re-trip off as few as 1-2 fresh
failures stacked on stale ones, even when the provider had recovered
enough to serve a single gentle request cleanly. This produces exactly
the "flapping" pattern that can masquerade as a long outage: trip → 15s
cooldown → flood → re-trip → 15s cooldown → flood → re-trip...

Also found in the course of this investigation: `request_delay_seconds`
(`self._delay` in both `grok_search.py` and `claude_search.py`) is set
from a constructor parameter but **never read anywhere else** — a dead
parameter. There has never been any proactive inter-request pacing;
only the hard concurrency cap and the reactive breaker.

**Fix**: a real half-open state. `_circuit_breaker_check()` now returns
`bool` — `True` for exactly one caller (the recovery probe) once
cooldown elapses, `False` when the breaker was never open. Every other
caller keeps failing fast (`GrokCircuitOpenError`/`ClaudeCircuitOpenError`,
"probe in flight") until that probe resolves. A successful probe fully
resets breaker state (clean signal, full recovery); a failed probe
reopens immediately — a single failed probe is itself sufficient evidence
the provider hasn't recovered, no need to wait for `threshold` fresh
failures to reaccumulate. The probe "claim" is a lease bounded by
`timeout × (network_retries + 1) + 5s`, not a plain boolean flag, so a
probe that exits through an unanticipated path (e.g. a non-transient
error, which never reaches the outcome-recording call) can't wedge the
breaker in "probe permanently in flight" — it just expires and the next
caller gets a fresh chance.

**Instrumentation**: both classes now track `trip_count` and
`total_open_seconds` (a flapping episode — trip → failed probe → failed
probe → ... → eventual success — counts as one trip and one continuous
open duration, not N), with `OPENED`/`RECOVERED` log lines and a
`get_circuit_breaker_stats()` snapshot method. `main.py` surfaces this
into `runtime_metrics["circuit_breaker_stats"]` for `url_discoverer`'s
batch and fallback clients, so a run's actual wall-clock cost of breaker-open
time is a real number in the run's metrics, not something inferred from
scattered log lines after the fact.

**Expected impact, calibrated honestly**: this does not shorten a
genuine sustained provider-side outage — the floor is set by however
long the provider is actually degraded, and no client-side breaker logic
changes that. What it targets is the *self-inflicted* portion: each
avoided flap cycle saves roughly one cooldown-plus-detection round
(~15s cooldown + ~25-30s for a fresh multi-caller round to reach its own
timeout, i.e. ~40-55s), and avoids the "stale failure carryover" risk
where a recovered-but-fragile provider gets re-tripped by our own burst
rather than genuinely still being down. What fraction of any given
incident was self-inflicted-flap versus genuine unavailability isn't
known precisely without real production telemetry from a comparable
incident — `runtime_metrics["circuit_breaker_stats"]` is the mechanism to
get that data on the next one, rather than continuing to guess.

Tests: `test_circuit_breaker_check_returns_probe_flag_after_cooldown_elapses`,
`test_circuit_breaker_check_returns_false_when_never_tripped`,
`test_circuit_breaker_check_rejects_non_probe_callers_while_probe_in_flight`,
`test_successful_probe_fully_resets_breaker_state`,
`test_failed_probe_reopens_breaker_immediately_without_needing_threshold_failures`,
`test_non_probe_callers_do_not_touch_network_while_probe_pending`,
`test_circuit_breaker_stats_track_trip_count_and_total_open_seconds`,
`test_repeated_probe_failures_within_same_episode_do_not_inflate_trip_count`
in both `tests/test_grok_search.py` and `tests/test_claude_search.py`.

## 8. Cross-provider batch retry, before dropping to the narrower fallback shape (2026-08-15)

Found by reading a real, completed production run (`SW2026-dipstick51`,
run during a live ~12-minute Grok batch outage — `circuit_breaker_stats`
showed `url_discovery_batch: {trip_count: 1, total_open_seconds: 705.7,
currently_open: true}` against an 820.9s total run, `url_discovery_fallback:
{trip_count: 0, total_open_seconds: 0.0}`). Despite the fallback client
(Claude) staying perfectly healthy the entire run — zero errors, zero
breaker trips — every one of 8 destinations still finished with **100% of
`top_attractions` rendering with no URL**, and restaurants and en-route
stops showed the identical pattern. Only scenic drives were spared (they
have a deterministic NPS-page-pattern shortcut independent of live
search).

**Root cause, traced through the actual disposition log** (`Canyon
Overlook Trail`, Zion, representative of every affected item): Grok's
batch harvest came back empty twice (initial call + the existing
insufficient-rows retry-prompt) → fell through to the non-batch fallback
(`_search_cached` → `self._search_fallback.search(...)`) → that call
*succeeded* (no error, consistent with 0 breaker trips) but returned
`direct_batch_no_match` — its single generic query for the category
didn't happen to surface a result matching this specific named item →
`direct_batch_authoritative: true` blocks any further per-item search →
item renders with no URL.

The narrower fallback (`_get_direct_batch_rows_for_destination` →
`_search_cached`, one generic `.search()` query per category per
destination) predates the Claude/Grok split entirely — it was originally
built as Grok's *own* same-provider fallback, a lighter-weight query to
try in case the batch prompt's specific format was the problem, not the
whole provider. It was never redesigned around the fact that
`self._search_fallback` is now a fully independent second provider with
its own working batch-harvest capability (`chat_completion(live_search=True)`,
the same purpose-built per-item list prompt the primary uses) — so the
fallback chain never reached for the stronger option that was already
sitting there, proven, and wired up.

**Fix**: `_fetch_direct_batch_html_rows` now tries the fallback client's
*own* `chat_completion(live_search=True)` with the identical
system/user prompt the primary got, before ever dropping to the narrower
single-query mode — gated on `self._search_fallback.is_circuit_open()`,
mirroring the primary's own retry-prompt gate. One attempt only (no
retry-prompt escalation on the fallback's attempt — this is already a
second full harvest call; a third would be excessive under conditions
that are already degraded). `_persist_direct_batch_html_capture` now
records a `provider` field (the winning client's class name) in its
debug captures, so a future incident like this one is diagnosable
directly from the capture files instead of requiring a manual trace
through the disposition log the way this one did.

Tests: `test_direct_batch_html_falls_back_to_cross_provider_when_primary_empty`,
`test_direct_batch_html_skips_cross_provider_fallback_while_its_circuit_open`,
`test_direct_batch_html_does_not_call_fallback_when_primary_already_sufficient`,
`test_direct_batch_html_keeps_primary_result_when_fallback_does_not_improve`,
`test_direct_batch_html_capture_records_which_provider_supplied_the_result`
in `tests/test_url_discovery.py`.

## 9. Circuit breaker was blind to HTTP-level failures (2026-08-15)

Immediate follow-up to §8: rerunning after that fix (`SW2026-dipstick52`)
hit a wall the same day, live-reproduced and traced to its actual cause.
`circuit_breaker_stats.url_discovery_fallback` showed `trip_count: 0,
total_open_seconds: 0.0` — apparently perfectly healthy — while every
single captured batch-harvest attempt still showed `row_count: 0,
provider: ""` (the new §8 provenance field showed neither client ever won).
Reproducing the fallback's exact batch call live surfaced the real cause:
Anthropic's account had run out of credit again, returning `400
"Your credit balance is too low..."` on every call.

**The breaker literally could not see this failure class.** `_post_with_retries`
only ever recorded a circuit-breaker failure inside its `except
requests.RequestException` branch — but getting a response back with a
non-2xx status doesn't raise anything at the `session.post()` level; only
a *separate* `resp.raise_for_status()` call (which callers like
`chat_completion` made *after* `_post_with_retries` had already returned
and already recorded a **success**) would raise. So a persistent
account-level failure — wrong/expired key, exhausted credit, a real 429
rate limit, a provider-side 5xx — registered as 100% healthy in
`circuit_breaker_stats` while genuinely failing 100% of the time. This is
a materially worse failure mode than a plain outage: an outage at least
degrades visibly (`trip_count` climbs, `is_circuit_open()` returns true);
this looked identical to full health right up until someone manually
reproduced a single call and read the response body.

**Fix**: moved `resp.raise_for_status()` inside `_post_with_retries`
itself, immediately after the request returns, so the same
circuit-breaker bookkeeping that already existed for network-level
exceptions now also sees HTTP-level ones. Two sub-cases, both now
correctly handled:
- **Retryable status** (429, 500, 502, 503, 504): treated like a network
  timeout — worth an immediate retry within the same call, same backoff
  as before.
- **Non-retryable status** (400, 401, 403, 404, ...): not worth retrying
  the identical payload against the identical broken account/key within
  this call, but **still recorded as a circuit-breaker failure** — so
  repeated calls of this kind across a run correctly accumulate toward
  `threshold` and trip the breaker, instead of each one independently
  discovering the same dead end. Live-verified against the real,
  still-exhausted Anthropic account: 4 real calls (matching the default
  `threshold=4`), each correctly counted, breaker opens on the 4th, and a
  5th call correctly short-circuits without touching the network at all
  — `get_circuit_breaker_stats()` showing `trip_count: 1,
  currently_open: True` throughout, where it previously would have shown
  `trip_count: 0` indefinitely.

Applied identically to `GrokSearch` and `ClaudeSearch`.

**Separately, a real cost-attribution gap found investigating the same
incident**: `main.py`'s `_build_gate_a_metrics` only recognized the
primary batch client's `"url_discovery:*"` operation prefix, not the
fallback client's `"url_discovery_fallback:*"` (a distinct prefix since
§6's client split) — so every fallback call, batch or non-batch, was
silently excluded from `stage_cost_usd` and `url_discovery_search_calls`.
A run that leaned heavily on the fallback (exactly the case during a
primary-provider outage, when the fallback fires the most) looked far
cheaper and less active than it actually was. Note this only affected the
*stage-level breakdown* — `UsageTracker.summary()`'s overall
`total_estimated_cost_usd` was already a true sum over every record
regardless of operation name, so it wasn't itself wrong, just not broken
down by stage correctly. Fixed by recognizing both prefixes.

Tests: `test_post_with_retries_counts_non_retryable_http_error_toward_breaker`,
`test_post_with_retries_retries_retryable_http_status_within_same_call`,
`test_post_with_retries_does_not_retry_non_retryable_http_status_within_same_call`
in both `tests/test_grok_search.py` and `tests/test_claude_search.py`;
`test_build_gate_a_metrics_attributes_fallback_client_operation_prefix` in
`tests/test_main_requirements.py`.

## 10. Real console data, corrected pricing, and a single-provider comparison mode (2026-08-15)

Direct continuation of §9: the user checked the actual Anthropic usage
console (ground truth I have no access to) and found **9,155,523 input
tokens, 327,009 output tokens, 361 web searches, all in one day**. That
settled two open questions decisively.

**Confirmed real, official Sonnet 5 pricing** (fetched live from
`platform.claude.com/docs/en/about-claude/pricing`, not assumed): **$2/MTok
input, $10/MTok output** — the introductory rate is now permanent; the
previously scheduled Sept 1 2026 increase to $3/$15 was cancelled. Web
search itself is billed *separately*: **$10 per 1,000 searches**, on top
of token costs — a cost component this codebase's `UsageTracker` doesn't
track at all (no field for `server_tool_use` search counts). Reconstructed
total: 9.15M × $2 + 0.327M × $10 + 361 × $0.01 ≈ **$25**, comfortably
consistent with a $20 balance running dry mid-run — not a display glitch,
not (primarily) the call-count doubling from §8's fix, but a materially
under-priced/under-capped tool doing exactly what it was configured to do.

**A second, more severe internal bug found while confirming this**:
`DEFAULT_PRICING_USD_PER_1M` (`llm_client.py`) had no entry for
`"claude-sonnet-5"` at all — only stale `claude-3-5-sonnet-latest`/
`claude-3-7-sonnet-latest` rows that don't prefix-match it.
`UsageTracker._estimate_cost`'s no-match fallback is `return 0.0` — every
real Sonnet 5 call this entire session had been silently costed at
**exactly $0.00**. This is *why* "runs are pennies" looked true internally:
not because usage was low, but because the estimator was broken for the
model actually in use. Fixed by adding the confirmed real pricing entry.

**Root mechanism for the token volume**: `CLAUDE_SEARCH_TOOL`'s
`max_uses: 5` permitted up to 5 agentic search rounds per call, and each
round resends the growing context (including retrieved page content) as
new input tokens on the next step. `9,155,523 ÷ 361 ≈ 25,364 tokens per
search` is consistent with multi-round compounding, not a single query.
Lowered to `max_uses: 1` — caps every call to one search round while this
is characterized further with real (now-accurate) cost data. Raise
deliberately later if there's evidence 1 round is insufficient coverage,
not by drifting back up.

**New: `--search-provider {grok,claude}` CLI flag**, for exactly the
question this incident couldn't answer cleanly — "what does a run
actually cost/behave like on one provider alone?" When set, it forces a
single provider for both `url_discovery`'s batch client and
`cultural_events`, and **does not construct a fallback client at all**
(`self._search_fallback = None`) — every call site that reads it already
treats `None` as "no fallback available" (§6/§8's design), so this needed
no new gating logic, just not building the second client. This gives a
clean, uncontaminated per-provider run for comparing real console numbers
between Grok and Claude, without either §6's batch-fallback split or
§8's cross-provider retry mixing both providers into one run's data.
`build_search_client` gained a `provider_override` parameter that bypasses
the `config.yaml` lookup entirely (same unknown-value-falls-back-to-grok
validation as the config-driven path); threaded through
`URLDiscoverer.__init__`, `CulturalEventsDiscoverer.__init__`, and every
one of `main.py`'s four discoverer-construction call sites (including the
selective-retry pass, which reuses the initial pass's already-constructed
client so only needed the override threaded to its *own* default-lambda
construction path). Recorded in the run ledger
(`search_provider_override`) and echoed to the console at startup for
visibility, matching the existing `--llm-provider` pattern.

Tests: `test_build_search_client_provider_override_bypasses_config_entirely`,
`test_build_search_client_provider_override_falls_back_to_grok_on_unknown_value`
in `tests/test_search_provider.py`;
`test_url_discoverer_search_provider_override_forces_single_provider_no_fallback`
in `tests/test_url_discovery.py`;
`test_search_provider_override_forces_single_provider` in
`tests/test_cultural_events.py`;
`test_estimate_cost_has_a_real_entry_for_claude_sonnet_5` in
`tests/test_llm_client.py`.
