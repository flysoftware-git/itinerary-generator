# Canonical Provider/Model Matrix

Status: documentation only (GH #65's option "A" — see
`docs/design/search-provider-capability-probe.md` for the underlying
evidence this table summarizes). No code changes in this doc's own
commit; one stale default was discovered while compiling it and is
flagged in §3, not fixed here.

Purpose: GH #65 asked for "a canonical model matrix" and noted the
codebase's real gap was "policy, not plumbing." This is that matrix —
one place that says, per provider, what role it's actually approved for
today, what model id backs it, and what evidence justifies that.

## 1. The matrix

| Provider | Content generation | Search / harvest (batch) | Search / harvest (non-batch) |
|---|---|---|---|
| **Grok (xAI)** | ✅ Default (`ai.provider: grok`). Model: `grok-latest` (env `XAI_MODEL`, `llm_client.py` `_provider_default_model`). | ✅ **Primary role.** Model: `grok-latest` (env `XAI_MODEL`, `grok_search.py`). Evidence: 21/21 (100%) citation-matched URLs, real API validation. `url_discovery.search_provider: grok` (default). | ✅ Works (same client), but not the production default for this shape — Claude is (see below). |
| **Claude (Anthropic)** | ✅ Available, opt-in via `ai.provider`/`ai.fallback_provider: anthropic`. Model default: `claude-3-5-sonnet-latest` — **⚠ stale, see §3**. | ✅ Available (`ClaudeSearch`), not selected as the batch default. | ✅ **Primary role for this shape.** Model: `claude-sonnet-5` (env `ANTHROPIC_MODEL`, `claude_search.py`). Evidence: 14/15 (93%) citation-matched. `url_discovery.nonbatch_search_provider: claude` and `cultural_events.search_provider: claude` (both default). |
| **OpenAI** | ✅ Available, opt-in via `ai.provider`/`ai.fallback_provider: openai`. Model default: `gpt-4o-mini`. | ❌ **Disqualified.** Probed with `gpt-5-search-api` (the current search-capable model — `gpt-4o-*-search-preview` deprecated 2026-07-23): 0/21 (0%) citation-matched — genuine search tool invoked, but returned URLs don't correspond to what was actually retrieved. Not wired into `search_provider.py` at all. | ❌ Same disqualification — no non-batch OpenAI search path exists in production. |
| **Gemini** | ✅ Available, opt-in via `ai.provider`/`ai.fallback_provider: gemini`. Model default: `gemini-1.5-flash` — note this is a different, older alias family than the `gemini-flash-latest` used in search probing; not verified live this session. | ❌ **Disqualified.** Probed with `google_search` grounding + `gemini-flash-latest`: 4/21 (19%) citation-matched. Same failure mode as OpenAI — real grounding tool, unreliable provenance. Not wired into `search_provider.py`. | ❌ Same disqualification. |
| **DeepSeek** | ✅ Available, opt-in via `ai.provider`/`ai.fallback_provider: deepseek`. Model default: `deepseek-chat`. OpenAI-compatible API. | Not evaluated. No search/grounding capability integrated or probed — out of scope for the 2026-08-14/15 investigation, which only covered the four providers with grounding tools this codebase could exercise. | Not evaluated. |
| **Azure OpenAI** | ✅ Legacy-compatibility path only (`ai.provider: azure_openai`), env-driven deployment name (`AZURE_OPENAI_DEPLOYMENT`), no independent default model constant. | Not applicable — never had a search role. | Not applicable. |

## 2. Where compatibility is enforced (GH #65 ask #2)

**Content generation**: one place, as asked — `llm_client.py`'s
`_normalize_model_for_provider` + `_PROVIDER_MODEL_PREFIXES` catches a
model string that doesn't match its provider's expected prefix (e.g.
`provider: anthropic` with a leftover `model: gpt-4o-mini`) before the
first API call, not deep into a run.

**Search/harvest**: no equivalent normalization exists, and it's a
different, weaker mechanism — `search_provider.py`'s valid-provider set
(`{"grok", "claude"}`) enforces compatibility *by omission*: OpenAI and
Gemini simply aren't wireable as search providers, but there's no explicit
"this is disqualified, here's why" signal if someone tries (an unknown
`search_provider` value just falls back to `grok` with a warning — see
`_read_search_provider`). This is two separate mechanisms, not truly "one
place." Formalizing that into a single enforcement layer with an evidence-
citing rejection message is deferred (GH #65 discussion's "Option C") —
not worth building until OpenAI or Gemini search is actually fixed and
there's a real re-enablement decision to gate.

## 3. Known issue discovered while compiling this matrix (not fixed here)

`llm_client.py`'s `_provider_default_model("anthropic")` returns
`"claude-3-5-sonnet-latest"`. This model id returned `404 Not Found` from
the real Anthropic API during a 2026-08-15 smoke-test attempt (see
`search-provider-capability-probe.md` §6) — it appears to be retired.
This is the **content-generation** path (`llm_client.py`), a completely
separate code path from `claude_search.py` (which already defaults to the
current `claude-sonnet-5` and works). Content generation on
`ai.provider: anthropic` (or a fallback to it) is broken today until this
default is updated or an explicit `ai.model`/`--llm-model` override is
supplied. Neither `config.yaml`'s default (`grok`) nor the Sandbox
validation manifest's override (`openai`) currently exercises this path,
so it hasn't caused a production failure — but it will the moment anyone
configures `ai.provider: anthropic` or `ai.fallback_provider: anthropic`
without an explicit model override. Tracked here, not fixed in this
commit (out of scope for a documentation-only pass); low-risk one-line
fix when picked up.

## 4. Explicit policy gates (GH #65 ask #3)

These are now real defaults in code, not just documented intent:

1. **Batch search/harvest → Grok.** Highest citation fidelity (100%),
   lowest latency of the working options, primary path for all four
   direct-batch categories (trail/attraction/restaurant/en-route).
2. **Non-batch search fallback + cultural events → Claude.** Used when
   (a) the batch harvest returns empty (automatic fallback within
   `url_discovery.py`) or (b) the call is inherently single-query
   (`cultural_events.py`). 93% citation fidelity — second-best, and this
   role's lower volume tolerates its higher latency budget (150s vs
   Grok's ~25-90s).
3. **OpenAI and Gemini are not eligible for search in production**, on
   direct evidence, regardless of how well either performs at content
   generation — the two capabilities don't track together (see
   `search-provider-capability-probe.md` §0 for why this is treated as an
   orthogonal axis, not a single "pick one provider" decision).
4. **Content generation defaults to Grok, with reactive (not proactive)
   failover.** `ai.fallback_provider` only engages when the primary's
   circuit breaker is open — it does not proactively route by latency or
   prompt-length heuristics (GH #65's suggested policy, "only use Gemini/
   Claude where latency or prompt constraints allow," is not implemented
   as a routing rule; the only per-run provider choice is explicit,
   via `ai.provider`, `trip.llm_provider`, or `--llm-provider`).
5. **No mid-run provider switching except explicit override or reactive
   failover** — satisfies GH #65's "avoid switching providers mid-run
   unless explicit override is requested": the only two ways a run's
   provider changes are (a) an explicit config/CLI/manifest override set
   before the run starts, or (b) an already-opted-into fallback path
   reacting to a circuit breaker, itself only enabled by explicit config.

## 5. Maintenance note

This table is a snapshot, not a contract — the underlying probe evidence
(`docs/design/search-provider-capability-probe.md` §3, §5) is explicitly
expected to shift as every provider "evolves their capabilities and seeks
differentiation over time." Re-run `scripts/probe_multi_provider_search_2026.py`
periodically (see that doc's §5) and update this table's search columns
when citation-fidelity numbers move enough to change a role assignment.
