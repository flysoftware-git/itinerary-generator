# The Provenance Canary

**Added 2026-08-24, against generator `v2.1.0`, branch `v2`.**

A scheduled, near-free check that live web search is *actually being invoked*
— the one failure this repository has already suffered and still has no
detector for.

> **Companion notes:** `search-provider-capability-probe.md` (the discovery
> and the cross-provider probe) · `cost-accounting-and-reduction.md`
> (the usage plumbing this reuses) · `url-discovery-and-audit.md` (what
> discovery does with what search returns)

---

## 1. The failure being detected

On 2026-08-14 it emerged that xAI had deprecated the `live_search`
chat-completions tool. It answers `410 Gone`. For an unknown period before
that, every direct-batch harvest call and every `GrokSearch.search()` call in
this pipeline had been answered from the model's **training memory**, while
the code dutifully read a `live_search` parameter the API had stopped
honouring.

What makes this the sharpest incident in the project's history is not its
size. It is that **nothing failed.**

| Guard | What it saw |
|---|---|
| Exception handling | no exception |
| Circuit breakers | no failures to count |
| `_post_with_retries` | HTTP 200 |
| URL validation | a plausible fraction of returned URLs resolved fine |
| `validation_report.json` | `valid: true` |
| The cost ledger | spend, as usual |
| Every one of 1,251 tests | green |

Content came back. URLs came back. Some of them were even right — a model
that has read the web during training knows `nps.gov/zion` perfectly well.
The product's central claim — *every published link comes from a real search
and is verified* — was false, and every instrument in the building read
normal.

It was caught by a deliberate capability probe run for an unrelated reason.
That is luck, and luck is not a control.

**The general shape, which will recur:** a provider changes behaviour without
changing its interface, and the degraded response is *plausible*. No
error-driven guard can see this, because there is no error. Only an assertion
about what should have happened can.

---

## 2. What the canary asserts, and why that specific thing

The tempting assertion is about content: check that returned URLs are real,
relevant, fresh. That is exactly the check that passed throughout the
outage, because a well-trained model produces plausible URLs from memory.
**Content quality cannot distinguish "searched" from "remembered."**

The discriminator has to be a signal the model cannot produce by thinking
harder. There is one already in the building, put there for cost accounting:
the provider's own report of server-side tool invocations.

`GrokSearch._record_responses_usage` parses
`usage.server_side_tool_usage_details.web_search_calls` and records it onto
`UsageTracker` as `tool_calls`, because xAI bills $5 per 1,000 invocations
and the estimator was blind to it. That count is the ground truth for
*whether a search physically happened*. During the deprecation it was zero.

So:

| # | Assertion | Detects | Cost |
|---|---|---|---|
| **A1** | `TOOL_INVOKED` — provider reports ≥ 1 billed `web_search` invocation for a `live_search=True` call | **the deprecation shape**: a normal-looking answer with no search behind it | one probe call |
| A2 | `CONTENT` — the call returned non-empty content | provider up but mute | (same call) |
| A3 | `URL_LIVE` — ≥ 1 returned URL resolves over plain HTTP, via the pipeline's own `URLValidator` | fabricated links *despite* a real search | **$0** |
| A4 | `FRESHNESS` — optional; a probe whose answer necessarily postdates any fixed training corpus | A1's own bookkeeping being wrong or unreported | one probe call |

**A1 is the canary. A2–A4 exist to make a failure legible**, not to add
coverage. Three situations demand three different responses and must not be
collapsed:

- A1 fail, A2 pass → the provider is answering without searching. *Stop
  publishing links.*
- A1 fail, A2 fail → the provider is down. *Wait.*
- A1 pass, A3 fail → the provider searched and returned junk. *A discovery
  bug, not a provenance one.*

A4 is the hedge against A1's single point of trust. A1 believes the
provider's self-report; if a provider stopped *reporting* tool use while
still performing it, A1 would fire falsely. A4 answers the same question by a
route that does not depend on that field at all. It is weaker on its own — a
model can decline or format oddly — which is why it is opt-in and why its
failure text says so.

---

## 3. Why not a unit test

A unit test asserting that the `live_search=True` path posts to
`/v1/responses` with a `web_search` tool is worth having, and is cheap, and
would have caught **none of this**. The request was correct throughout. The
*provider* changed. An assertion that never leaves the process cannot observe
that, and a suite of 1,251 such assertions did not.

This is the general principle behind the canary and it is worth stating
plainly, because the instinct to "just add a test" is strong and wrong here:

> A test proves the code does what it was written to do. A canary proves the
> world still behaves the way the code assumes. Those are different claims
> and only one of them was false.

`tests/test_provenance_canary.py` therefore tests *the canary*, not the
provider — including a case that reproduces the exact 2026-08-14 shape
(plausible content, a resolving URL, zero tool calls) and asserts the canary
fails on it. If that test ever goes green with `A1 == PASS`, the detector has
stopped detecting.

---

## 4. Cost

One `--probes 1` run is a single `/v1/responses` call: 1–2 billed
`web_search` invocations plus a few thousand tokens. At xAI list pricing,
comfortably under **$0.05**. A3 is free.

For calibration against `cost-accounting-and-reduction.md` §8.4: a Core-tier
generation run costs **$3.40** and makes ~212 searches. The canary is
roughly **1.5% of one run**, once a day.

The `--dry-run` path constructs nothing and spends nothing, so the canary's
own argument handling and reporting can be smoke-tested on every commit while
the paid assertion runs on a schedule.

---

## 5. Operating it

```bash
python scripts/provenance_canary.py                     # grok, 1 probe
python scripts/provenance_canary.py --provider claude   # check the fallback too
python scripts/provenance_canary.py --probes 2 --freshness --json out/canary.json
python scripts/provenance_canary.py --dry-run           # free
```

### Exit codes, and why 1 and 2 are not the same

```
0  all assertions passed
1  an assertion FAILED — provenance is not established; treat every link
   this pipeline would publish today as unverified
2  the canary could not run (missing key, unreadable config, provider
   unreachable, circuit already open)
```

**`2` means UNKNOWN, not OK.** This split is the direct lesson of the
incident: an absent signal was read as a quiet one for weeks. A canary that
reports success when it could not check would recreate the original failure
in a new place. The script says so on stderr every time it exits 2, and the
open-circuit case is tested to confirm it spends nothing during a known
outage rather than burning probes into a wall.

### Recommended schedule

- **Daily**, on whichever provider `config.yaml` currently routes search to.
  Provider changes are announced by their effects, not in advance.
- **Before any paid generation run**, in the same spirit as
  `cost-accounting-and-reduction.md` §8.2's "probe before you spend" — a
  $0.05 check ahead of a $3.40 run whose entire output depends on the answer.
- **On every provider or model change in `config.yaml`.** The provider matrix
  moved four times in August; each move is a chance for this assumption to
  quietly stop holding.

A failure is not a build break to be muted. It means the claim on which this
product is differentiated is currently unsupported.

---

## 6. Known limits

- **It proves a search happened, not that discovery used it.** A1 verifies
  the provider invoked its tool for the canary's own probe. It does not walk
  the production harvest path. A regression that disabled `live_search` at
  *our* call sites while the provider stayed healthy would pass. That gap is
  covered by unit tests on the request shape — the two are complements, and
  neither is sufficient.
- **A1 trusts one provider-reported field.** Mitigated by A4, not eliminated.
- **`claude_search.py` never reads Anthropic's tool-count field**
  (`cost-accounting-and-reduction.md` §9), so `--provider claude` would report
  zero invocations and fail A1 for a bookkeeping reason rather than a real
  one. Latent today because search routes to grok. **Fix that field before
  relying on the canary against Claude** — until then, `--provider claude
  --freshness` and read A4, not A1.
- **One probe is one sample.** It detects a systemic change, not an
  intermittent one. Raising `--probes` trades money for confidence linearly;
  2 is a reasonable daily setting once the cost of a miss is a customer's
  itinerary rather than a developer's afternoon.
