# Search-Provider Capability Probe & Grok Live-Search Fix

Status: Grok fix (§1-2) implemented and test-covered. Cross-provider probe
(§3) run twice against real APIs (initial pass + a fixes-applied rerun).
Claude added as a second working search/harvest provider (§4), implemented
and test-covered; live end-to-end validation blocked by an Anthropic
account credit-balance issue (not a code problem -- see §4.1). Probe script
hardened into a repeatable, parameterized, history-tracking CLI (§5).
OpenAI/Gemini capability follow-up remains open (tracked via the same
probe, not yet separately scoped).

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
