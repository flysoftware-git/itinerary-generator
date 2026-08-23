# Cost Accounting and Reduction

**Investigation and changes of 2026-08-20/21, against generator `v2.1.0`, branch `v2`.**

This note records how this project's spend was measured, what the measurements
overturned, and what was changed as a result. It is written to be **re-runnable**:
§7 is a benchmarking procedure, and every change lands with named tests so a later
reader can verify the claims rather than trust them.

It also deliberately records **beliefs that proved wrong** (§4). Several of them were
stated confidently and acted on. Keeping only the conclusions would make the reasoning
look cleaner than it was and would hide the specific mistakes that are easy to repeat.

> **Companion notes:** `per-day-item-caps.md` (item ceilings, the other cost lever) ·
> `search-provider-capability-probe.md` (provider selection) ·
> `url-discovery-and-audit.md` (what discovery actually does)

---

## 1. Why this investigation happened

The owner was receiving a stream of $5 charge emails from xAI and asked, in effect,
whether the cache and the cost reporting were working at all.

Both halves of that question turned out to be worth asking, and the answers differed:

- **The cache was working.** Daily spend fell $45.46 → $6.11 → $3.12 across
  2026-08-18/19/20 while the number of runs *rose*.
- **The cost reporting was not.** It under-reported token cost by roughly **9×**,
  and had earlier reported near-zero against ~$24/day of real billing.

**The $5 emails were a red herring, and it is worth saying why.** They are credit
refills against a $5 limit, approved at unpredictable times. A single charge therefore
carries no information about what ran or when — it cannot be used as an alarm for
unexpected API activity, and a quiet period is not evidence of no activity. What *was*
informative was their volume: roughly 32 refills across 2026-08-15/18 corresponded to
the $159 actually consumed in that window. The instrument was real; it was measuring
cumulative burn, not per-run cost.

---

## 2. What the historical record actually shows

xAI's usage export is the authoritative consumption record. Daily, for the period in
question, against what this project's own run ledger claimed:

| Date | Console (tokens) | Ledger total | Ledger coverage |
|---|---|---|---|
| 2026-08-15 | $65.40 | $37.14 | 57% |
| 2026-08-16 | $23.84 | $1.08 | **5%** |
| 2026-08-17 | $24.31 | $3.68 | **15%** |
| 2026-08-18 | $45.46 | $16.54 | 36% |
| 2026-08-19 | $6.11 | $10.37 | — |
| 2026-08-20 | $3.12 | $5.10 | — |

Two separate failures are visible here and they must not be conflated:

1. **Aug 16–17: the ledger recorded ~$0.00 of token cost** against ~$24/day. The
   configured model had no pricing entry and `_estimate_cost` returned `0.0` silently.
   Backing search fees out of those days' ledger totals leaves a *negative* remainder,
   which is the signature of this bug.
2. **Aug 15 and 18: runs existed that the ledger never saw.** Coverage improved once
   dipstick ledgers under `C:\Temp\RoadTripRuns` were included (Aug 18 went from 1
   logged run to 12), so part of this was an incomplete scan, not a missing write.

**Total for 2026-08-15 through 08-21: $171.99 in tokens alone**, across 75.1M tokens,
before any web_search fees.

---

## 3. The measurement that settled it

Daily granularity could not isolate a single run. The **hourly** export could.

A cold-start run was executed deliberately with the persistent cache cleared, then the
hourly export was matched against its ledger row:

| | Tokens |
|---|---|
| Console, hour `2026-08-20 19:00` | 1,760,982 |
| Our ledger, same run | 1,760,607 |
| **Delta** | **+375 (0.021%)** |

That 0.02% agreement is what makes everything below trustworthy: **the run is
identified beyond doubt, and our token accounting was never the problem.**

That hour billed **$3.75296**, i.e. **$2.131 per million tokens**.

### 3.1 The console bills tokens only

Established by rate stability rather than by documentation. Across 45 non-zero hours the
rate holds between **$2.109 and $3.062/M** while hourly request counts range from 9 to
1,735. A figure that included per-invocation search fees would swing with request
density. It does not, so `usd` is token spend and the $5/1,000 `web_search` fees are
billed separately.

**Consequence:** true cost of that run was **$3.75 tokens + $1.88 fees = $5.63**,
against the **$2.32** the ledger reported. Under-reported by ~2.4× overall, ~9× on
tokens.

### 3.2 The pricing entry was a guess that was flagged as a guess

`grok-4-fast` was priced at `$0.20/$0.50`. Its own comment, written 2026-08-15, said the
figure came from a third-party aggregator, that xAI's docs did not list the model at
all, and to *"treat this entry as provisional until checked against real billing."*

It was then trusted for six days without that check. **The comment was correct and the
process around it failed.** This is the strongest argument for §5.1's warnings: a
provisional value that looks like a number is indistinguishable from a verified one at
the call site.

`grok-4-fast` is not in xAI's model catalog — it is what the API reports back for an
aliased request. `config.yaml` asked for `grok-latest`, an alias, which is why the
served model could change underneath the project without any config change. The
effective rate had in fact moved: $4.20–4.82/M in early August, $2.00–3.05/M mid-month,
$2.11–2.38/M since 08-15, all on an unchanged `grok-latest`.

---

## 4. Beliefs held during this investigation that proved wrong

Recorded because each was acted on, and each is easy to repeat.

| Belief | Reality | How it was caught |
|---|---|---|
| "~80% of spend is search tool fees" | Tokens are **67%**, fees 33% | Fell out once token pricing was corrected |
| "We *over*-estimate by 11–14%" | We **under**-report by ~2.4× | The over-estimate was an artifact of subtracting fees using `url_discovery_search_calls` (158) instead of the billed `web_search_calls` (375) — a 2.4× undercount of fees |
| "The console buckets by UTC" | It buckets by **local time** | The run started 02:16 UTC and appeared in the `19:00` bucket |
| "Console `usd` may include search fees" | Tokens only | Rate stability across 45 hours (§3.1) |
| "NPS-first is the highest-value cost work, attacking the dominant term" | It is ~15% of spend; tokens are dominant | Measured after the claim was made |
| "NPS API covers 86% of park items" | **74%**, and Capitol Reef only 30% | Token *intersection* matching produced false positives — it matched *Zion Lodge* to *Stargazing in Zion*. Subset matching is the correct test |
| "`allowed_domains` attacks the 87% input term at its source" | Only one of four batch kinds declares a single-domain source | Found while implementing |
| "Stage cost components don't sum to the total" | For Aug 19–20 they do | Generalised from one older sampled record |
| "The cost changes will reduce spend" | Run 2 came out **+19%** | Measured; three defects in the changes, plus item-count growth shipped alongside |
| "The alignment fix will cut `no_match`" | It rose 82 → 108 | Hike names were sent to the batch whose prompt excludes hikes |

The recurring shape: **a plausible ratio computed from an unverified input**. In three
of these the arithmetic was fine and the input was wrong.

---

## 5. Where the money actually goes

Measured from the cold-start run's own usage records (`validation_report.json` →
`llm.records`).

| Operation | Calls | Tokens | Share |
|---|---|---|---|
| `url_discovery:chat_completion_search` (direct batches) | 38 | 914,634 | **49.3%** |
| `url_discovery_fallback:search` (per-item retries) | 120 | 774,675 | **41.8%** |
| `cultural_events:search` | 8 | 71,298 | 3.8% |
| `destination_bundle:*` (all ten) | 10 | ~68,000 | 3.7% |

**URL discovery is 91% of all tokens. The destination content bundles — the product the
traveller actually reads — are 0.4% each.**

And it is **input**: 1,529,200 input against 231,407 output. A batch call carries a
~300-token prompt and ~24,000 tokens of retrieved page content billed back into
context. We were paying premium rates to re-read the web.

### 5.1 The silent-zero class of bug

Three code paths returned `0.0` and said nothing:

- `_estimate_cost`, when no pricing entry matched
- `_estimate_tool_call_cost`, when a provider had no tool-fee entry
- a search provider whose tool-invocation count is never read (live in
  `claude_search.py`, latent only because search currently routes to grok)

All three now warn once per run. The third is checked at summary time, not per call:
a single search legitimately reports zero invocations when the model decides it needs
none, so a per-call warning would fire constantly against grok, which reports correctly.
A provider reporting zero across an entire run of search operations is not being frugal.

The ledger also recorded `llm_model: null` on every run — the field held the *CLI
override*, not the effective model. Answering "which model did that run use?" required
archaeology through `config.yaml`'s git history.

### 5.2 The retry pattern — two distinct problems

From the 303 per-item disposition threads in `destination_status_report.json`:

| Failure | Count | Concentrated in |
|---|---|---|
| `direct_batch_candidate_rejected` | 301 | **253 are en-route stops** |
| `direct_batch_no_match` | 82 | **81 are attractions** |

**En-route stops** — ~4 rejected candidates each. Rejected domains: `blm.gov` (55),
`nps.gov` (48), `roadtripryan.com` (31), `fs.usda.gov` (24). The *right* domains
offering the *wrong* page: a land-agency landing page for a specific roadside pullout.
The page granularity does not exist, so no better searching produces it.

**Attractions** — the batch has no row for the item at all, because Stage 3 invents the
attraction list and the Stage 5b batch independently invents its own. Every
non-overlapping name costs a fallback search.

---

## 6. Changes made

| Commit | Change | Targets |
|---|---|---|
| `7d3dde2` | Warn instead of silently reporting $0.00 | §5.1 |
| `2fbed09` | Record the effective model in the ledger | §5.1 |
| `a1fadfe` | Attractions 4→5/day, scenic drives 2→1/day | `per-day-item-caps.md` |
| `a313f20` | Cross-category AllTrails URL preference | wasted purchases |
| `3131194` | Backfill emptied attraction slots from the trail batch | 71% of harvested trails were discarded |
| `2f88dc2` | Pricing correction, discovery model split, `allowed_domains` | §3.2, §5 |
| `8ea33be` | Pass the itinerary's item names to the attraction batch | §5.2 attractions |
| `286a133` | En-route stops resolve to a Maps link | §5.2 en-route |

Three deserve explanation.

**Discovery model split** (`url_discovery.search_model`). Discovery is extraction from
retrieved pages, not the reasoning the top tier is worth paying for, and it is 91% of
tokens. Content generation stays on the better model because at 0.4% it costs almost
nothing to do so. Unset leaves both on the content model.

**`allowed_domains`.** `link_types.hike.discovery_site_filter` had declared
`"alltrails.com"` since the taxonomy was written and **was read by no module** — the
filter was applied only to results, so the project paid to search the open web and then
discarded whatever was off-domain. Now passed to the tool. Scoped to the trail batch
only: attractions, restaurants and en-route stops legitimately span many domains, and
`link_types.scenic_drive` sets the filter to `null` explicitly.

**En-route → Maps.** This removes a failure mode rather than tuning it. En-route stops
are waypoints; the traveller needs to find the pullout, not read about it. The mode
skips the en-route batch entirely (10 of 38 harvest calls) and builds the link locally
at zero cost, preferring a route-verified coordinate over a name query. An AllTrails URL
already harvested by the trail batch still wins over a pin.

---

## 7. Benchmarking procedure — how to repeat this

The measurement is only meaningful if it is reproducible. It requires no special tools.

### 7.1 Produce a comparable run

```bash
# 1. Park the persistent cache (do not delete -- a bad run should be recoverable)
mv .cache/url_discovery/persistent_cache.json /some/scratch/persistent_cache.backup.json
```

```bash
# 2. Run cold, to its own output directory so nothing is clobbered
cmd.exe /c "C:\Dev\Sandbox\run-trip.BAT sw dev C:\Temp\RoadTripRuns\<label> < nul"
```

`< nul` matters: the BAT ends in `pause`, which blocks a headless invocation forever.

**Note the local wall-clock hour.** The console buckets by local time (§4), so a run at
19:16 local lands in the `19:00` row regardless of UTC.

### 7.2 Reconcile against the bill

Download the xAI usage export at **hourly** granularity (daily cannot isolate a run).
Then:

1. Match the hour's `tokens` against the ledger's grok token total. **Expect agreement
   within ~0.02%.** If it does not match, the hour is contaminated by other activity and
   nothing below is valid.
2. Compare the hour's `usd` against the run's estimated token cost.
3. Add web_search fees separately — they are **not** in the export. Use the billed
   invocation count (`web_search_calls` from the provider's usage block), **not**
   `url_discovery_search_calls`, which undercounts by ~2.4× because one call can fire
   the tool several times.

### 7.2a Measure CONTENT, not only cost

This procedure originally measured spend and nothing else, and that gap hid two real
regressions behind acceptable-looking cost numbers. Removal counters are not
sufficient: run 4 reported 17 attractions removed (an improvement on 29) while
**silently deleting every AllTrails link in the output, 21 to 0**.

Count the artifact itself, every run, alongside the cost:

```bash
python - <<'EOF'
import io, re
h = io.open(r"<output>/dev/index.html", encoding="utf-8").read()
for label, pat in [
    ("attractions",  r'class="attr-item"'),
    ("restaurants",  r'class="rest-item"'),
    ("en-route",     r'class="stop-card"'),
    ("alltrails",    r'alltrails\.com/trail/[^"' ]*'),
    ("nps.gov",      r'nps\.gov/[^"' ]*'),
    ("ext links",    r'href="(https?://[^"]+)"'),
]:
    print(f"{label:14} {len(set(re.findall(pat, h)))}")
EOF
```

Measured history, for comparison:

| | run1 | run2 | run3 | run4 |
|---|---|---|---|---|
| attractions | 41 | 62 | 56 | 47 |
| restaurants | 55 | 55 | 61 | **38** |
| en-route stops | 50 | **13** | **12** | 40 |
| AllTrails links | 21 | 27 | 27 | **0** |
| external links | 326 | 285 | 291 | 275 |

**A cost improvement with a content column moving down is not an improvement.** Both
regressions above were invisible in the cost figures and in the removal counters, and
were found only when someone asked whether the output had changed.

### 7.3 Where the numbers live

| Figure | Source |
|---|---|
| Per-operation token breakdown | `<output>/validation_report.json` → `llm.records` |
| Per-stage cost, search counts | `<output>/run_ledger.jsonl` → `runtime_metrics.gate_a` |
| Effective model used | `run_ledger.jsonl` → `llm_effective` |
| Per-item disposition threads | `<output>/destination_status_report.json` |
| Raw harvest rows and prompts | `<output>/dev/url_discovery_direct_batch_html/*.meta.json` |

The `.meta.json` captures are the most useful and least obvious: they hold the exact
system and user prompts, the parsed rows, and the cache key, so a harvest can be
audited without re-running it.

### 7.4 The 2026-08-21 baseline

| | |
|---|---|
| Grok tokens | 1,760,607 (1,529,200 in / 231,407 out) |
| Grok calls | 166 |
| `web_search` invocations | 375 |
| Console token cost | $3.75296 |
| Search fees | $1.875 |
| **True total** | **$5.63** |
| Ledger reported at the time | $2.32 |

---

## 7.5 The second run (2026-08-22), and what it caught

The changes in §6 were measured by a second cold-start run against the same
manifest. **It came out 19% more expensive**, and the reason is worth recording in
full: the run did not measure the cost work, it measured three defects in it.

| | run 1 | run 2 | |
|---|---|---|---|
| Grok tokens | 1,760,607 | 2,098,883 | **+19.2%** |
| Token cost (list $2/$6) | $4.45 | $5.34 | |
| Search fees | $1.88 (375 calls) | $2.17 (434 calls) | |
| **Total** | **$6.32** | **$7.51** | **+19%** |

### What worked

**En-route → Maps did what it was meant to.** `direct_batch_candidate_rejected` fell
**301 → 50 (−83%)**, en-route item threads 64 → 18, and batch tokens dropped
914,634 → 599,170 (**−34%**). The failure mode is gone.

### Three defects, all in the changes themselves

1. **The model split never applied.** It was gated on
   `self._llm.provider == "grok"` — the *content* provider. This project generates
   content with openai and searches with grok, so the gate was always false and
   `url_discovery.search_model` was silently ignored. The whole run billed the
   expensive tier while config said `grok-4.3`. **A test had enshrined the bug**,
   asserting `GrokSearch` received `None` — exactly the broken behaviour.

2. **Coordinate Maps links were silently rewritten.** `_retain_discovered_url`
   rebuilds `google_maps_search` queries to sanitize AI-authored query text. It was
   also rebuilding the coordinate links `en_route_source: "maps"` constructs from a
   route-verified geocode, turning an exact pin into a name search. The mode still
   emitted *a* Maps link, so it looked correct while discarding the precision it
   exists for.

3. **Hike names were fed to the batch that excludes hikes.** The alignment fix's
   plumbing worked — the Zion capture shows Stage 3 names reaching the prompt. The
   *content* was wrong: the attraction prompt says "excluding hikes" in its own
   text, and four of the five names sent were hikes. The model correctly refused
   them and returned one, while the hint list displaced slots that would have held
   real attractions. `direct_batch_no_match` went **up**, 82 → 108.

### Why cost rose: two opposing changes shipped together

Cost *per item* fell. **Item count rose more.** Attraction threads went 81 → 100,
driven by `promoted_from_trail_batch: 10` and the 4→5/day ceiling raise. Fallback
calls went 120 → 184.

**Lesson for the next round: do not ship cost reductions and content expansions in
the same measurement.** Neither effect can be attributed afterwards, and here they
had opposite signs.

### What the fixes are worth, without another run

A rate change on known token counts is deterministic:

```
run 2 as billed             $7.51
run 2 with the split applied $5.15   (−18% vs run 1)
```

Caveat: this assumes `grok-4.3` produces the same token volume. A more or less
verbose model shifts the count, so $5.15 is a projection, not a measurement.

---

## 8. Tests

Every claim above that could regress has a test. Named here so a later reader can run
them rather than re-derive the reasoning.

| Area | Test |
|---|---|
| Silent-zero warnings | `tests/test_llm_client.py::TestCostReportingBlindSpotWarnings` |
| — unpriced model warns once per run, not per call | `…::test_unpriced_model_warns_once` |
| — a provider that never reports tool counts | `…::test_search_provider_reporting_no_tool_counts_warns_at_summary` |
| — an occasional zero-tool search must NOT warn | `…::test_occasional_zero_tool_call_search_does_not_warn` |
| Discovery model split | `tests/test_url_discovery.py::test_url_discoverer_search_model_override_splits_discovery_from_content` |
| — override ignored for a non-grok content provider | `…::test_search_model_override_ignored_for_non_grok_content_provider` |
| Search domain filtering | `tests/test_url_discovery.py::TestAllowedDomainsForBatchKind` |
| — heterogeneous kinds stay unconstrained | `…::test_heterogeneous_kinds_are_left_unconstrained` |
| Batch alignment | `tests/test_url_discovery.py::TestAttractionBatchPrewarmWithItineraryItems` |
| — seeds first, so the cap cannot crowd them out | `…::test_seeds_come_first_so_the_cap_cannot_crowd_them_out` |
| En-route → Maps | `tests/test_url_discovery.py::TestEnRouteStopsResolveToMaps` |
| — coordinates preferred, strings not trusted | `…::test_stringified_coordinates_are_not_trusted_and_fall_back_to_name` |
| Trail-batch backfill | `tests/test_url_discovery.py::TestBackfillAttractionsFromTrailBatch` |
| — never fetches, so it cannot add a paid call | `…::test_never_fetches_when_no_trail_batch_ran` |
| Cross-category AllTrails preference | `tests/test_url_discovery.py::TestCachedAllTrailsBatchUrlForItem` |
| Per-day ceilings | `tests/test_ai_content_normalization.py::test_resolve_attraction_target_default_is_five_per_day` |
| | `…::test_resolve_scenic_drive_target_default_is_one_per_day` |

---

## 8.1 Tracking iteration convergence

Three of the changes in §6 did not do what they claimed on the first pass, which
raises a planning question the individual post-mortems cannot answer: **how many
passes should a change on these paths be budgeted?**

`docs/reports/change-outcomes.jsonl` records one line per behaviour-changing
operation — intent, outcome, whether it injected a separate defect, and critically
**how it was detected and what that detection cost**. Docs-only and test-only
commits are excluded; they are not the denominator.

```bash
python scripts/change_outcome_stats.py
```

Seeded from this work (13 settled operations):

| | |
|---|---|
| Clean first pass | **62%** |
| `failed_to_fix` | 23% |
| Injected a separate defect | 23% |
| **Problems caught only by a paid run or the user** | **80%** |

**The headline is the last row, not the first.** A 62% first-pass rate implies
~1.6 passes per change, which is useful for estimating. But the failure *rate* only
sets how many iterations are needed; the detection *channel* sets what each one
costs. Four of five problems here were invisible until a paid run or the owner
surfaced them — and each of those three defects had the same property: **it produced
a plausible-looking result while doing the wrong thing.** A Maps link was still
emitted; the batch prompt still contained the hints; the config still said
`grok-4.3`.

Halving a failure rate is hard. Moving detection from `paid_run` to `tests` is
usually just writing the assertion first — and the one case where a test *was*
written against the new behaviour, it asserted the bug (`pricing-and-model-split`),
which is why the ledger tracks that as an injected defect in its own right.

Practical use: before a change on the discovery/cost paths, ask what observable
would distinguish "worked" from "looked like it worked", and whether a test can see
it. If only a paid run can, that is a known 1.6x-and-metered cost going in.

---

## 8.2 Probe the real code path before paying for a run

The cheapest verification tier in this project was being skipped. There are three,
not two:

| Tier | Cost | Catches |
|---|---|---|
| Unit tests | free | what you predicted |
| **Offline probe against real data** | **free** | **whether the real code works on real inputs** |
| Paid run | $2–6 | interactions, end-to-end effects, actual spend |

The middle tier is possible because every run leaves its own inputs behind:
`destination_status_report.json` holds the per-item disposition threads, and the
`*.meta.json` batch captures hold the exact prompts and parsed rows. A later change
can be exercised against exactly the items that failed last time, without a
generator run and without spending anything.

**Worked example, 2026-08-22.** Before running the geocode-fallback change, the
26 attraction names that run 3 had deleted were pulled from its status report and
put through a geocoder:

- First probe: **12% resolved.** Alarming — it implied the change would cut cost by
  deleting content, the exact failure this project had already made twice.
- That probe was wrong. It used the query form `"{name}, {destination}"`, which the
  implementation does not use. Isolating the query form scored it **0/6** while
  name-only scored **6/6**.
- Re-probed through the actual method, with real destination coordinates:
  **26/26 resolved, all within 60 miles of their destination.**

Both directions matter. The probe raised a false alarm and then cleared it, for
nothing, in minutes — and either outcome was worth more than another passing test,
because it exercised the real code against real inputs rather than the author's
model of them.

**The practice:** before any paid run, take the failing items from the previous
run's artifacts and put them through the changed code path directly. Predict the
run's numbers in writing first. If a metric then moves for a reason the prediction
did not anticipate, that is the signal to stop and re-diagnose rather than iterate
— every defect in §8.1's ledger announced itself that way.

---

## 8.3 The failure mode behind most of §4, and the rule that prevents it

Seventeen operations in, the misses in §4 and §8.1 are not independent. Almost all of
them are one error: **reasoning from a name instead of from behaviour.**

Four were an aggregation label mistaken for a mechanism:

| Label trusted | What it actually was |
|---|---|
| `url_discovery_fallback` | an operation **prefix** shared by four call paths into `_search_cached`; only `_search_first_strict` is the per-item website hunt |
| `url_discovery_search_calls` | a different counter from the billed `web_search_calls` — off by 2.4× |
| "token overlap = match" | matched *Zion Lodge* to *Stargazing in Zion* |
| `grok-4-fast` @ `$0.20/$0.50` | a guess its own comment flagged as provisional |

Three were a mechanism changed from its interface without reading its body: the model
split gated on `self._llm.provider` without checking which provider runs (openai, not
grok); `en_route_source: maps` set without noticing the batch **supplies the stops**,
not merely their URLs; hints fed to a prompt whose own text says *"excluding hikes"*.

In a module this size, names are abundant and free while behaviour costs a read or a
measurement. The cheap option was taken repeatedly.

**The discriminator is measurement, not care.** Sorting the ledger by whether the
specific mechanism was measured *before* the change:

| Pre-measured | Outcome |
|---|---|
| AllTrails cross-category — 11 pairs checked for false conflations | clean |
| Trail backfill — 84 rows harvested vs 24 used | clean |
| Geocode links for attractions — 26/26 offline probe | worked exactly as probed |
| Model-split gate — no | failed |
| en-route maps mode — no | failed, 76% content loss |
| Hint routing — no | failed twice |
| Fallback gate scope — no | failed, −6% against −66% predicted |

The last one is the sharpest: attributing those 218 calls to their call sites was
**free**, from artifacts that already existed. That query was run *after* the paid run
rather than before it.

### The rule

**No cost prediction without first attributing the number to its call sites.**

Not *"this label is 66% of spend"* but *"these N call sites produce it, and this change
removes M of them."* The precondition is cheap, checkable, and would have caught the
2026-08-22 miss before the run instead of after.

Corollary, from §8.2: a prediction stated in writing before the run is what makes a
miss legible. Without it, a metric that moves for the wrong reason reads as success —
which is how the en-route change was first reported here as a win.

---

## 9. Open items

- **The residual 18%.** The corrected `$2.00/$6.00` rate computes $4.45 against $3.75
  billed. The gap is consistent with xAI's cached-input discount, which this table
  cannot model. It therefore **overstates** cost slightly — the safe direction, but it
  means the ledger is not expected to match the bill exactly.
- **Model pinning.** `config.yaml` still requests the alias `grok-latest` for content
  generation. Pinning it to a catalog model would end the silent-drift risk, but changes
  which model runs and so is a quality decision, not a cost one.
- **NPS-first discovery.** Scoped and measured (74% coverage of park items, 30% at
  Capitol Reef) but not built. Modest as a cost lever (~15%); its stronger case is
  content quality, since 8 of 13 logged URL rejections were nps.gov pages the model
  guessed at.
- **`claude_search.py` tool counts.** It records token usage but never reads Anthropic's
  tool-count field, so search fees would report $0.00 for an entire run. Latent only
  because search routes to grok. The §5.1 warning will fire if that changes.
