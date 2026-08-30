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

| "The image counters are broken" | They are **correct** | `nps_api_calls: 0` alongside 34 downloads looked impossible, but provider lookups were served from a SECOND cache, `.cache/images/`, that the benchmark never cleared. Asserted as a defect before checking |

| "validation_report.json has no pass/fail field" | It has `summary.valid` | Checked `d.get("valid")` at the top level, got `None`, and reported a missing field without looking one level down. Third instance this day of asserting a defect before checking |

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
# 1. Park BOTH persistent caches (do not delete -- a bad run should be recoverable).
#    Naming only the first is why no run in this project has been fully cold:
#    .cache/images holds provider lookups, which is why nps_api_calls and
#    wikimedia_api_calls read 0 on a supposedly cold run. No cost impact --
#    image lookups are free -- but the runs were not what they claimed.
mv .cache/url_discovery/persistent_cache.json /some/scratch/persistent_cache.backup.json
mv .cache/images/cache_index.json            /some/scratch/image_cache.backup.json
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

## 8.4 Where the money actually is, after pruning (2026-08-23)

The Core tier — trails, en-route stops and cultural events switched off — costs
**$3.40** cold. Decomposed by flow element:

| Element | Calls | Tokens | $ | Share |
|---|---|---|---|---|
| URL discovery — direct batches | 28 | 664,897 | $1.73 | **51%** |
| URL discovery — per-item fallbacks | 88 | 721,652 | $1.65 | **48%** |
| **All itinerary content, 10 destinations** | 10 | 68,510 | **$0.005** | **~0%** |

Per delivered item: **$0.034, 14,698 tokens, 2.9 searches.**

**99% of Core spend is URL discovery.** Generating a destination's entire prose,
schedule and narrative costs ~6,900 tokens; finding one URL costs ~14,700. The
product is nearly free and the links are the entire bill.

### What the expensive half buys

Of the 52 URLs the paid fallback resolved on that run:

| Domain class | Count |
|---|---|
| `alltrails.com` — **in a run with trails disabled** | 29 |
| Third-party travel content (travelandleisure, lonelyplanet, tripadvisor, …) | 9 |
| `facebook.com` | 1 |
| `.gov` / `.org` official | 3 |

So $1.65 delivered roughly three authoritative links, nine magazine pages, and
twenty-nine links to a category that was switched off. The AllTrails leak is fixed
(§8.5); the remainder is a genuine question about whether the fallback earns its
place at all.

### The mechanism, not the volume, is the cost

A batch call carries a ~300-token prompt and ~23,400 tokens of **retrieved page
content billed back as input**. The pipeline uses an LLM as a search-and-retrieve
engine, paying ~$2.13/M to read web pages into a context window, then discards the
pages and keeps a URL — while the same URLs are already validated for free over
plain HTTP (696 requests per run, $0).

Pruning categories cut volume. It never touched this, and this is where 99% of the
money is.

---

## 8.5 Ranked pivots

Ordered by measured evidence times expected effect, not by appeal.

| # | Pivot | Evidence | Attacks | Confidence |
|---|---|---|---|---|
| 1 | **Warm / cross-customer entity cache** | **Measured**: warm runs used 8–27 searches against a cold 212 | cost per *customer* | **High** |
| 2 | **Replace LLM candidate-finding with a search API** | Token composition measured; hit rate **untested** | 99% of spend | Medium |
| 3 | **Drop or cap the per-item fallback** | 88 calls, $1.65, output shown above | 48% | Medium-high |
| 4 | **Group destinations per batch call** | `direct_batch_group_size: 1` today; config-only | 51% | Medium |
| 5 | **NPS-first for park items** | Probed: 74% coverage, 30% at Capitol Reef | ~15% | Medium |
| 6 | **Extend `allowed_domains` beyond trails** | Only 1 of 4 batch kinds declares a single-domain source | retrieval volume | Low |

**1 — Warm cache.** The strongest measured result in this whole investigation, and the
one that reframes the question. A cold run is the cost of the *first* customer to a
destination set; every later one runs warm. An entity cache keyed by place rather
than by query makes destination popularity compound instead of repeat. This does not
reduce the cold number and should not be sold as doing so.

**2 — Search API.** The largest mechanism change available and the only one that
removes no content. Eliminates token injection outright and cuts per-search fees
roughly 5–15×. **Blocked on evidence**: the keyless probe attempted 2026-08-23 was
rate-limited after two queries and proved nothing. A free-tier key (Serper offers
2,500 queries; Brave has a free tier) allows the 88 real fallback items to be
measured offline before any spend.

**3 — Fallback.** Its own output argues against it. Worth re-measuring after the
AllTrails chokepoint fix, since 29 of 52 results were that leak; the honest question
afterwards is whether ~12 links, mostly magazine pages, justify 48% of a run.

**4 — Grouping.** Cheap and config-only, so it can ride along with any other run.
Saving is well under 50% of batch cost because grouped prompts are larger.

**5 — NPS-first.** Modest as a cost lever, but the strongest *quality* case: 8 of 13
rejections on one run were nps.gov pages the model guessed at, against an API that
returns them authoritatively for free.

**6 — Domain constraints.** Attractions, restaurants and en-route stops legitimately
span many domains; `link_types.scenic_drive` sets its filter to `null` deliberately.
Constraining them buys cost with coverage.

### The trails switch: four leaks, one lesson

Recorded because the shape recurs. The switch was guarded at
`_search_alltrails_for_trail`, then `_search_alltrails_for_seed_relaxed`, then
`_search_alltrails_for_trail_filtered` (which had been buying 98 calls per run while
its output was hidden), then the trail direct batch — and the Core run *still*
resolved 29 AllTrails URLs through the general per-item hunt.

Each fix asked *"is the path I am looking at guarded"* rather than *"is there a point
every candidate must pass through"*. There was: `_retain_discovered_url`. Enforcing
the switch at that chokepoint closed all four at once.

**A switch that hides output while still buying it is worse than no switch**, because
the spend continues and the missing output is read as the switch working.

---

## 8.6 Serper probe (2026-08-23): candidate-finding is better and cheaper elsewhere

Pivot 2 in §8.5 was ranked second and marked *blocked on evidence*. It is no longer
blocked. Probed offline against the **55 real items run 7's paid fallback handled**,
taken from that run's own `destination_status_report.json` — no generator run, 110
queries of a 2,500 free tier.

### Coverage and source quality

| | Serper | LLM paid fallback |
|---|---|---|
| Returned a result | **55/55 (100%)** | 52/55 |
| official `.gov` | **28** | 2 |
| official `.org` | **6** | 1 |
| travel content farms | **2** | 11 |
| social / video | 0 | 1 |
| Top hit same domain as the LLM's | 5/55 (9%) | — |

They find genuinely different things. Serper returns official government or
organisation sources for **62%** of items; the paid path returned them for **5%**.

### The check that actually mattered

A domain histogram proves nothing on its own — the failure that killed the LLM's
`nps.gov` results was *generic pages failing promise-to-target* (Angels Landing
resolving to a permits page). So every Serper hit was run through
**`_retain_discovered_url`**, the same relevance, redirect and promise-to-target
gate the pipeline applies today:

> **53 of 55 — 96% — passed.**

Same items, same validator, same run's data:

| Item | Serper | LLM (paid) |
|---|---|---|
| Inspiration Point | `nps.gov/brca/planyourvisit/inspiration.htm` | lonelyplanet.com |
| Bryce Point Trail | `nps.gov/brca/planyourvisit/brycepoint.htm` | alltrails |
| Navajo Loop & Queen's Garden | `nps.gov/brca/planyourvisit/qgnavajocombo.htm` | alltrails |
| Pioneer Park | `sgcityutah.gov` | tripadvisor |
| Cappeletti's Restaurant | the restaurant's own site | yelp |

**This is the first lever in this investigation that improves content and cuts cost.**
Every other one traded something away.

### What it does NOT establish

- **The batch half is untested.** These 55 were fallback items — *"find the URL for
  this known item"*, a pure lookup. The batch does a different job: it **invents the
  item list** ("what is worth seeing in Zion") as well as resolving URLs, and a search
  API cannot do the first part.
- **No cost figure is claimed.** Serper's paid rate at volume is unconfirmed, and per
  §8.3 no cost prediction is made without attributing it to call sites in a real run.

The batch distinction does suggest a shape worth testing later: if the batch call only
*names* items and stops searching, its ~23,700 tokens collapse to a small prompt and a
list — no retrieved pages injected — with Serper resolving URLs afterwards. That would
attack both halves. It is speculation until measured.

---

## 8.7 Scope: Serper for the per-item fallback only

Deliberately narrow. The fallback is the proven case, worth **$1.65 of a $3.40 Core
run**, and small enough that a single run can attribute the result — which is the
discipline §8.3 exists to enforce.

**Module.** `generator/serper_search.py`, mirroring `grok_search.py`'s shape so
`build_search_client` can return it: same constructor signature, same
`usage_tracker`/`usage_operation_prefix` contract, `is_circuit_open()` for parity.

**Selection.** `url_discovery.nonbatch_search_provider: serper`. That key already
exists and already selects the fallback client independently of the batch client, so
the batch path is untouched by construction — no flag threading, no new call site.

**Cost accounting.** Serper bills per query, not per token, so it needs its own entry
in `DEFAULT_TOOL_CALL_PRICING_USD_PER_1000` and must report a tool-call count through
`UsageTracker.add`. Without that count the §5.1 warning fires — correctly — because a
search provider reporting zero invocations across a run of search operations is the
exact blind spot that guard was written for.

**Unchanged by design:** `_retain_discovered_url` and every validation gate. The probe
ran Serper's results through the current validator unmodified and got 96%; loosening
anything would discard the evidence this scope rests on.

**Verification.** Predict before running: fallback token cost near zero, fallback
web_search invocations near zero, `per_item_website_hunt` still ~88 calls but billed
to Serper. Then the §7.2a content table, with attention to external-link count and
source mix — the expected change is *more* official sources, and a drop there means
something is wrong.

**Rollback.** One config value back to `grok`.

---

## 8.8 Run 8 (2026-08-24): the Serper fallback, measured

Predicted before running, per §8.3: fallback tokens ~0, fallback invocations ~0,
`per_item_website_hunt` ~88 calls billed to Serper, run total ~$1.75, external links
**at or above** 218 -- the last one because this was the first lever expected to
*improve* content, so a drop would mean failure rather than success.

| | run 7 (grok fallback) | run 8 (Serper) |
|---|---|---|
| **Run total** | $3.40 | **$1.8325** |
| Fallback cost | $1.65 | **$0.036** |
| Fallback tokens | 721,652 | **0** |
| Fallback calls | 88 | 36 |
| external links | 218 | **225** |
| official `.gov`/`.org` | 37 | **39** |
| restaurants | 46 | **53** |
| attractions removed for no URL | 62 | **22** |

**$1.83 against $1.75 predicted -- within 5%, the first accurate cost prediction in
this investigation.** Calls fell as well as unit cost: Serper resolves on the first
query where the LLM path burned several variants per item.

Cumulative: **$6.32 baseline → $3.40 Core → $1.83**, a 71% reduction, and only the
middle step cost content.

### What remains

97% of the remaining $1.83 is the direct batch: 27 calls, 686,728 tokens, $1.78.

The batch does **two** jobs — it invents the item list *and* resolves each URL — and
the URLs are already in `rows[].url`. So the question is not whether a SERP API can
add something, but whether the batch's expensive half is buying anything the cheap
path cannot.

Underneath it sits a redundancy already measured elsewhere in this note: **Stage 3
already produces the item names**, for ~6,800 tokens per destination with zero
searches. The batch then independently invents its own list, and the disagreement
between the two is exactly the `direct_batch_no_match` series (82 → 108 → 125). Two
lists of the same thing, disagreeing, one of them costing ~25,000 tokens a call.

**The risk in removing it is content, not cost.** The batch searches the live web, so
it can surface items a model's training data would not. Stage 3 cannot. That is the
question a URL-resolution probe cannot answer, and it should be settled before the
batch is touched.

---

## 8.9 The result, measured like-for-like (2026-08-24)

### The comparison that counts

Earlier summaries in this note quoted reductions against the $6.32 baseline while the
measured run had categories switched **off**. That compares two different products and
overstates the engineering result — the same conflation of scope with cost this note
warns about elsewhere. The owner caught it.

Like-for-like, same feature set, both cold start on `sw_manifest`:

| | cost | attractions | restaurants | en-route | trails | links |
|---|---|---|---|---|---|---|
| **Baseline (run 1)** | **$6.32** | 50 | 55 | 50 | 21 | 326 |
| **Current (run 11)** | **$2.82** | 64 | 46 | 26 | 24 | 288 |

**$6.32 → $2.82, a 55% reduction with no category removed** — and more attractions and
more trails than the baseline had.

### Tiers, measured

| | cost |
|---|---|
| **Core** — trails, en-route and cultural events off (run 10) | **$1.18** |
| **Core + all three options** (run 11) | **$2.82** |
| **The three options together** | **$1.64** |

Batch composition explains the delta: Core runs 18 batch calls (attraction 10,
restaurant 8); all-options runs 46, adding 10 trail and 10 en-route plus events' own.
Serper queries rise 40 → 143 as more items need URLs.

**Core is a product decision, not a saving.** It is a smaller deliverable at a lower
price, and should be presented that way.

### What got it there

In rough order of contribution, all measured:

1. **Serper for the per-item fallback** (§8.6–8.8) — $1.65 → $0.036, and content
   *improved*. The only change in the investigation that did both.
2. **Correct pricing plus the grok-4.3 discovery split** — the ledger stopped
   under-reporting 9×, and discovery moved to a cheaper tier.
3. **En-route stops resolving to Maps links** — removed the 253-of-301 rejection storm.
4. **`direct_link_batch_count` 20 → 12** — real but small, −3.6%, for the reason in §8.10.

### Two figures still unexplained

- **En-route stops 50 → 26** with the category enabled in both runs. Most likely the
  `maps` resolution mode, but nobody decided it, and it should be explained rather than
  accepted.
- **Removing the trail batch raised attraction counts** (55 → 61 in run 10). A
  favourable result from an unmodelled interaction between trail hints, the trail batch
  and the attraction backfill. Favourable misses are still misses.

---

## 8.10 Why the batch is near its floor

`direct_link_batch_count: 20 → 12` was predicted at −20% and delivered **−3.6%**. The
reason is worth recording because the data to avoid the error was already in this note.

A batch call's tokens are **91% input**: 627,904 in against 58,824 out on run 8. The
item count controls the **output** — the list the model returns — and output is 8.6% of
the call. Asking for 12 instead of 20 can only touch that 8.6%. The input is retrieved
page content, and the model searches the web the same amount regardless.

**The prediction was made against the wrong term, having already measured the right
one.** §8.3's rule was followed to the point of attribution and then not used.

What this establishes: the batch's cost is not reachable through prompting. Its
remaining $1.18 is ~500K input tokens of retrieved pages plus 89 billed searches. The
only levers on the dominant term are **fewer searches** (which means less content) or
**not injecting retrieved pages** (which means no agentic batch at all — and §8.6 shows
that costs the ratings and descriptions every row carries, 100% of them).

**Treat $1.18 Core / $2.82 full as close to the floor for this architecture.** Further
movement comes from the warm-cache path (§8.5 pivot 1), not from tuning the batch.

---

## 8.11 Trails, re-enabled and re-measured (2026-08-29)

`config.yaml` carried `trails.enabled: false` as a cost measure, on the figure
recorded next to it: `alltrails_trail_filtered` was **124 of 246 paid fallback
calls**, half that path's spend. The reasonable reading of that is "trails
roughly double the fallback cost", and it was cited that way when the switch was
turned back on.

Measured on `sw` immediately before and after: **$0.0947 -> $0.1089**, about
**15% more per run**, not a doubling. The 124/246 figure describes a share of one
path on one 2026-08-22 run, not a multiplier on run cost. Quoting it as a
prediction was wrong.

The switch is also no longer global. `_resolve_category` consults CLI, then the
manifest, then config, so a trip answers for itself -- enabling trails globally
would have bought them for Europe, whose manifest asks for no hikes.

Non-cost consequence, recorded here because the cost case is what will be
revisited: re-enabling trails also re-enables the audit's AllTrails-only gate,
which strips correct non-AllTrails pages from anything classified `trail_like`.
`sw` attraction removals went 7 -> 17 in the next run. See
`url-discovery-and-audit.md`.

## 8.12 An exhausted search balance is billed as HTTP 400

Serper reports "Not enough credits" as **HTTP 400** -- the same status it uses for
a malformed query -- and the client logged the status while discarding the body
that said why. Runs completed, validation passed, and every item that was never
searched for was reported as "removed for no verified URL", which reads as *the
web has nothing for these*.

Three trips published in that state on 2026-08-29 before it was noticed. The
usage counter is unaffected and its figures remain sound: `_record_usage` fires
on the request, so a 4xx that consumed a credit is still counted. What failed
was diagnosis, not accounting.

`serper_search.py` now logs the response body and treats quota exhaustion as its
own condition -- one error naming the consequence for output quality, rather than
a warning per call. The circuit breaker stays for transient trouble; an empty
balance does not recover on a cooldown.

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
