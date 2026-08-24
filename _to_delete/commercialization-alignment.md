# Commercialization Alignment

**Written 2026-08-24, against generator `v2.1.0`, requirements `v2.3`, branch `v2` @ `cc914b3`.**

This note connects the engineering record in this repository to the
commercialization plan kept outside it (`TripTips/road-trip-generator-commercialization-plan.md`),
and re-ranks the open work by **commercial risk** rather than by engineering
tidiness.

It exists because those two rankings have diverged. The debt map (§4.5 of
`design.md`), the nine open defect reports, and the ranked cost pivots
(`cost-accounting-and-reduction.md` §8.5) are each internally well-ordered
and each ordered by a *different* question. None of them asks "what stops
this being sold." That question has a different answer, and a much shorter
list.

> **Scope.** This note is about sequencing and shaping. It sets no prices,
> commits to no launch date, and does not duplicate the market comparison —
> those live in the external plan. Branding and trademark are out of scope
> entirely (`TripTips/Branding.md`).

> **Companion notes:** `cost-accounting-and-reduction.md` (the measurements
> everything here rests on) · `per-day-item-caps.md` · `provenance-canary.md` ·
> `multimodal-routing.md` §6.4 (the licensing question in §4.1 below)

---

## 1. What the cost decomposition did to the product definition

The measurement in `cost-accounting-and-reduction.md` §8.4 is the most
consequential thing in this repository for commercial purposes, and it is not
primarily a cost finding. It is a **product-shape** finding.

Core tier, 10 destinations, cold:

| Element | Calls | $ | Share |
|---|---|---|---|
| URL discovery — direct batches | 28 | $1.73 | 51% |
| URL discovery — per-item fallbacks | 88 | $1.65 | 48% |
| **All itinerary content, 10 destinations** | 10 | **$0.005** | **~0%** |

Three consequences follow, and they reorder the entire plan.

### 1.1 The links are the product, economically as well as editorially

Generating a destination's entire prose, schedule and narrative costs ~6,900
tokens. Finding *one URL* costs ~14,700. The thing the marketing story is
built on — *every link fetched, checked, and dropped if it doesn't hold up* —
is also 99% of the bill.

This is a coherent product, not an accident to be optimized away. But it
means every cost-reduction proposal is a **product** decision. "Cut the
fallback" is not a spend cut; it is a decision to publish fewer verified
links, which is a decision to be less differentiated. §8.5's ranking is
right to lead with mechanism changes (warm cache, search API) over content
cuts, and that ordering should be treated as a commercial commitment, not
just an engineering preference.

### 1.2 Cost per *customer* is not cost per *run* — and the gap is the business model

The strongest measured result in the cost investigation:

> Warm runs used **8–27 searches against a cold 212**.

A cold run is the cost of the **first customer to a destination set**. Every
later customer on an overlapping corridor runs warm. This turns a
variable-cost product into a largely fixed-cost one *for the head of the
demand curve*, and it means the two things the plan treats as separate — the
SEO play (publish route pages for popular corridors) and the cost strategy —
are the same initiative:

- Publishing "Zion → Moab, 5 days" as a free public example is top-of-funnel
  marketing **and** it warms the cache for every paying customer who wants
  that corridor.
- Destination *popularity* therefore compounds instead of repeating. A
  hundred customers on ten popular corridors cost far less than a hundred on
  a hundred obscure ones.
- The pricing question changes shape: flat per-trip pricing against a
  variable cold cost is exposure; against a warming cache it is a margin that
  improves with volume on exactly the routes marketing is pushing.

**This is pivot #1 in `cost-accounting-and-reduction.md` §8.5 and it should
also be item #1 in any commercial roadmap.** It is the only item on either
list that improves cost, marketing and quality at once.

Two honest caveats, both already recorded in that note and both worth keeping
in front of a buyer conversation: the warm cache **does not reduce the cold
number**, and must not be sold as if it does; and the measured 8–27 figure
is a same-manifest warm run, not a *different customer on an overlapping
corridor*, which is the case the business depends on. **Measuring
cross-manifest overlap is the single highest-value unspent experiment in the
project** — it can be done offline against existing artifacts, and until it
is done the cross-customer cache is a strong hypothesis rather than a
measured result.

### 1.3 The Core/enrichment split is a tier boundary that already exists in config

As of 2026-08-22, `trails`, `en_route_stops` and `cultural_events` default
**off**; `restaurants` defaults on. That was a cost decision. It is also,
unintentionally, a **packaging** decision, and a defensible one:

| Category | Default | Commercial reading |
|---|---|---|
| Destination content, schedule, weather, maps, routing | on | Core. Costs ~$0.005/run. Never a tier boundary. |
| Restaurants | on | Core. "Dining is arguably core itinerary content" is the right call. |
| Trails | **off** | Premium / niche. Also the beachhead's most-wanted feature (national parks, vanlife) — see §5. |
| En-route stops | **off** | Premium. The detour-math differentiator lives here. |
| Cultural events | **off** | Premium, and the *weakest* candidate: 24K–113K tokens per delivered item, and the content most likely to be stale by departure. |

The switches were built for spend control and they work as a tier mechanism
for free. But note the tension, because it is real and unresolved: **two of
the three things switched off are named differentiators in the sales pitch.**
Detour-aware en-route stops are a capability no competitor has. AllTrails-linked
hikes are the beachhead's reason to care. Shipping Core-only means shipping
the cheap version of the argument.

The resolution is not to switch them back on by default. It is to decide,
before the concierge test, **which tier the test measures** — and the answer
should be the expensive one, because the question being asked is "would you
pay," and the version worth paying for is the one with the differentiators
in it. Cost discipline is for scale; the validation run is not scale.

---

## 2. Commercial risk register, cross-referenced to open work

Risks are ordered by *what they cost if ignored*, not by likelihood or by
effort. Each row names the repo artifacts that bear on it.

### R1 — Google Maps Platform terms may be incompatible with the product's delivery model · **LAUNCH-BLOCKING · LEGAL**

`multimodal-routing.md` §6.4 / open question Q6 records the finding: Maps
Platform terms appear incompatible with a publish-a-static-page model,
**independently of cost**. The note frames it as "what source instead?"

This is the only item in the repository that can invalidate the product
rather than degrade it, and it is currently filed as one of two remaining
design questions on a feature note about transit routing. It is not a
routing question. Deep-linking to Google Maps Directions is in `requirements.md`
§7 and §15, it is a row in the competitive comparison, and it is in the
elevator pitch.

**Actions, in order:** (1) establish precisely which uses are affected —
deep-linking to `google.com/maps/dir/` with named waypoints is a materially
different act from embedding tiles or caching Directions responses, and the
answer may be that the pipeline's actual usage is fine; (2) if it is not
fine, price the alternatives (OSM/Leaflet is already a dependency; the
elevator pitch does not name a vendor); (3) resolve this **before** the
storefront, not before scale. A product that cannot lawfully ship its
navigation hand-off has no pricing question to answer.

### R2 — Single-provider dependency on xAI is now structural · **HIGH**

The plan's previous revision recorded that the dual-provider architecture had
retired this risk. **That is no longer true.** `cultural_events.search_provider`
and `url_discovery.nonbatch_search_provider` were both reverted from `claude`
to `grok` on 2026-08-15 (Claude cost ~4.6× for equivalent output, and the
account is no longer funded), and `ai.fallback_provider` is commented out.
`config.yaml` says it plainly: with no working fallback provider,
`self._search_fallback` is a second grok client and no longer adds
cross-provider resilience.

The *capability* survives — `search_provider.py` still selects three
providers and `claude_search.py` still matches the surface. What is gone is
the *funded, exercised* second path. That distinction matters commercially:
"we can switch providers" is true; "we will keep serving during an xAI
outage" is not.

Compounding it: `claude_search.py` never reads Anthropic's tool-count field,
so a switch to Claude would silently report $0.00 search fees — the exact
class of bug that produced the 9× under-reporting. Switching providers under
pressure would therefore also blind the cost ledger.

**Actions:** treat re-funding a second provider as a launch cost, not an
optimization; fix the Claude tool-count field *before* it is needed, not
during the outage that needs it; and note in any customer-facing SLA language
that recovery is provider-dependent.

### R3 — Silent provider degradation with no detector · **HIGH · now mitigated**

The 2026-08-14 `live_search` deprecation: for an unknown period the pipeline
answered from training memory while every guard read normal. Caught by luck.

**Closed by this change set**: `scripts/provenance_canary.py` and
`docs/design/provenance-canary.md`. See §3 below for the operating
requirement. The residual is real and documented in that note's §6 — the
canary proves a search happened for its own probe, not that production
discovery used one.

### R4 — Fail-closed produces thin guides, and the customer sees subtraction, not discipline · **HIGH · unresolved**

The v2.0 entry-into-service run was `valid: true` with 17 attractions removed
for lacking a verified URL. That is the system working exactly as designed.
It is also a customer receiving a thinner guide than they expected, with no
explanation.

Related and worse: `quality_gate.*` thresholds are warnings, not errors, and
`validation.fail_on_broken_links` is `false` — both **deliberately**, because
orphan-card and teaser-gap coverage is a known-unresolved data issue and
hard-failing would block every run. That is the correct engineering call
today and an untenable one the moment a stranger has paid.

**Actions:** (1) surface the subtraction in the output — "3 attractions
omitted: no verifiable source found" turns an invisible loss into visible
evidence of the differentiator, and costs nothing; (2) define the
`degraded`-threshold at which a paid run is auto-retried or auto-refunded,
before the first sale; (3) treat promoting `quality_gate` from warning to
error as a **storefront prerequisite** with its own tracked work, not as
config tuning.

### R5 — Reservation ingestion is the first real privacy surface · **HIGH**

`reservation_ingest.py` handles IMAP credentials, LLM extraction from
untrusted email bodies, and confirmation numbers. The design is careful
(schema re-validation after merge, HTML-escaping at render, fill-never-overwrite,
pending-on-ambiguity, gitignored sidecar) and privacy mode blanks the right
fields in `prod`.

Two things keep it on this list. First, debt item **#11**: HTML escaping is
inconsistent — roughly a dozen prose fields interpolate raw, and drive
descriptions and map marker names reach the DOM via `innerHTML`. That was a
cosmetic-injection risk when all content came from our own prompts. With
attacker-controllable email in the pipeline it is a different item, and it
should be **re-graded out of the debt map into the launch blockers**. Second,
the incident that produced privacy mode — a `prod` build rendering personal
Notion links into a published header — demonstrates that the failure mode
here is real and has already happened once with a friendly input.

**Action:** close #11 for every field reachable from ingested email before
the ingestion path handles anyone's mailbox but the owner's.

### R6 — Cost is measured on success only, so real COGS is unknown · **MEDIUM-HIGH**

Usage is recorded only on successful completion; a run that dies after
burning retries reports near-zero. An unpriced model silently costs $0.00
(this has already happened twice, at 9× and at "near-zero against $24/day").
Cost still does not reach `run_ledger.jsonl` on disk.

Every margin number in the external plan therefore has an unmeasured lower
bound on the cost side. **Action:** record cost on failure paths; fail loudly
on an unpriced model; land cost in the ledger. These are the three items that
turn pricing from an estimate into a measurement, and they are small.

### R7 — Published links that are wrong or fabricated · **MEDIUM-HIGH · concentrated in two open defects**

PR-020 (Lizard Head Pass, hallucinated target link) and PR-025 (Pagosa
Brewing & Grill, hallucinated restaurant link) are both `status:open`, both
`severity:high`, and both tagged `hallucination-risk`. They are the only two
open defects that attack the central claim directly. See §4.

### R8 — Wrong navigation output · **MEDIUM · owner-deferred**

Debt item **#16**: `_build_route_gmaps_url`'s no-geocode fallback has twice
resolved Southwest-US stops to Washington-state places; a 2026-08-19
Moab→Telluride leg showed a San Juan Island waypoint. Deferred by owner call,
correctly — a prior speculative fix made a similar case worse.

The commercial weight is higher than the engineering weight: a wrong waypoint
in a navigation hand-off is the single most damaging defect class for a
product whose pitch is turn-by-turn-ready. It stays deferred, but it should be
**a named acceptance check in the concierge test** (§5) rather than waiting
for another paid run to surface it.

### R9 — Public-repo exposure · **LOW, and lower than previously assessed**

`origin/main` is hundreds of commits behind; the pushed snapshot is the
July 23 v0.17 state. None of the differentiating work has ever been
published. This is a fortunate accident of workflow, currently protected only
by the habit of not pushing. Formalizing the split is cheap. Full treatment in
the external plan §8.

### R10 — Documentation drift as a credibility risk · **LOW**

`design.md` Appendix B tracks nine divergences; several are now stale in the
opposite direction (the probe script it says is absent is present). Low
operational risk, but the engineering-rigor story is part of the pitch, and a
design doc that misdescribes its own repo undercuts it. Addressed in part by
this change set.

---

## 3. Requirement introduced by this note

**The provenance canary is an operating requirement, not a tool that exists.**
`requirements.md` v2.3 records it. Restated here because it is the one place
where commercial claim and engineering practice are the same sentence:

> Before any paid generation run destined for a recipient other than the
> owner, and at least daily while search is routed to a single provider,
> `scripts/provenance_canary.py` must exit 0. An exit of 2 is *unknown*, not
> pass.

At roughly $0.05 against a $3.40 Core run, the check costs ~1.5% of the run
whose entire output depends on its answer.

---

## 4. Re-ranking the open work by commercial risk

The nine open defect reports, the sixteen debt items and the six ranked cost
pivots, merged and re-sorted by what blocks selling. **This ordering
deliberately disagrees with all three source lists**, and the disagreements
are the useful part.

### Tier 1 — Blocks taking money from a stranger

| Item | Source | Why here |
|---|---|---|
| **Maps Platform licensing (Q6)** | `multimodal-routing.md` §6.4 | R1. Can invalidate the delivery model. Filed as a design question; it is a legal precondition. |
| **PR-020, PR-025** — hallucinated/untrusted links | open defects, `severity:high` | R7. These are the differentiator failing in the field. Everything else in the pitch is downstream of "links are trustworthy." |
| **Debt #11** — inconsistent HTML escaping | debt map | R5. Re-graded: cosmetic when content was ours, a security item now that email enters the pipeline. |
| **Cost on failure + fail-loud on unpriced model + cost in ledger** | R6 | Pricing cannot be committed on a cost figure with an unmeasured lower bound. Three small changes. |
| **`quality_gate` promotion path + degraded-run refund threshold** | config posture, R4 | Not the promotion itself — the *decision* about what a paid customer is owed when fail-closed thins their guide. |

### Tier 2 — Blocks the validation test being worth running

| Item | Source | Why here |
|---|---|---|
| **Decide which tier the concierge test ships** | §1.3 | Testing willingness-to-pay on the cheap tier answers the wrong question. |
| **Cross-manifest warm-cache measurement** | §1.2, pivot #1 | Offline, free, against existing artifacts. Determines whether the business has increasing or flat returns to scale. |
| **PR-005, PR-024, PR-029** — schedule realism, day-trip time budget, departure-leg `Getting There` | open defects, `severity:high` | Customer-visible quality in the exact thing being evaluated. PR-005 is also the head of `index.md`'s own remediation queue. |
| **Surface omissions in the output** | R4 | Free. Converts the product's most confusing behaviour into its clearest proof. |
| **Debt #16 as a concierge acceptance check** | R8 | Stays deferred as code; becomes an explicit thing to look for in a real trip. |

### Tier 3 — Real, but after evidence of demand

| Item | Source | Note |
|---|---|---|
| Pivots #2 (search API), #3 (fallback cap), #4 (grouping), #5 (NPS-first) | `cost-accounting-and-reduction.md` §8.5 | Correct ordering. Scale economics. Pivot #2 is blocked on a free-tier key and is the largest available mechanism change. |
| Debt #13 — breaker stats cover 2 of 4 | debt map | A content-generation outage is invisible in the ledger. Matters when runs are unattended, not before. |
| `url-quality-pr-backlog.md` PR-0…PR-5 | backlog | PR-1's explicit URL state model and PR-5's quality accounting are what would let §1.1's "links are the product" claim be *reported* rather than asserted. Strong, not urgent. |
| PR-028 — trail threshold filtering | open defect | High severity, but trails are off in Core. Re-rank up if trails become a paid tier. |
| Debt #8, #9, #10 — inert validation branch, dead template markers, inert config | debt map | Correctness hygiene. #10's `validation:` decoy will bite silently whenever the two values diverge. |
| Side trips (GH #3), multi-day carried legs (Q10) | design-only | Q10 has a live trigger — a real cruise confirmation was ingested 2026-08-21, and the scheduler is wrong on every cruise itinerary it sees. Scope it when bookings are a customer-facing feature. |

### Explicitly *not* before validation

- Any storefront, payment or account work.
- Manifest-authoring UX. The concierge test is Wizard-of-Oz by design; the
  owner authors the YAML.
- Post-generation editing. Selective regeneration plus manifest denylists is
  the substitute, and whether it suffices is a **question for the concierge
  test**, not an assumption to build against.
- `url_discovery.py` decomposition (debt #1). It is the largest debt item and
  the least commercially urgent; 559 tests are holding it. Revisit when
  someone other than the author needs to work in it.
- The eight stale `claude/*` branches. Cosmetic.

---

## 5. What the concierge test must check, that a passing run does not

The external plan's Phase 0 measures willingness to pay. This repository can
name what else the same trips should be watched for — cheaply, because the
testers are looking at the output anyway. Each of these is a known-open item
whose real severity is unmeasurable from artifacts:

| Watch for | Because |
|---|---|
| Any dead, wrong-target or off-topic link reported by a tester | The single highest-priority defect class in the project. Wire it to the existing `broken-link-report.yml` issue template. |
| A waypoint in the Maps hand-off that is in the wrong state | Debt #16, R8. Only reproduces on real routes. |
| Whether the guide feels thin, and whether testers notice omissions | R4. Distinguishes "fail-closed is invisible" from "fail-closed is a defect." |
| Whether the schedule survives contact with an actual day | PR-005, PR-024; `dipstick55_bug_triage.md` Theme H is explicitly flagged as needing a product decision, not a mechanical fix. |
| Whether the PWA install prompt lands with a non-technical person | Named differentiator, never observed in the wild. |
| Whether regenerate-with-a-tweaked-manifest is an acceptable substitute for editing | Decides whether an editor is ever built. |
| Whether Core-tier absence of trails/en-route/events is noticed | Decides §1.3's tier boundary on evidence instead of on cost. |

Run every concierge build with `--privacy-mode on`. The redaction path exists
precisely for output leaving the owner's machine and has never been exercised
at volume.

---

## 6. The one principle this note is arguing for

The cost investigation arrived at a rule
(`cost-accounting-and-reduction.md` §8.3): *no cost prediction without first
attributing the number to its call sites* — reason from measured behaviour,
not from a plausible name.

The commercial equivalent is the same rule pointed outward:

> **No roadmap item justified by a plausible story about customers.** The
> engineering side of this project earned its reliability by refusing to
> trust labels it had not measured. The business side has, so far, measured
> nothing — there is not yet one data point from a person who is not the
> author.

That asymmetry is the actual state of the project: a system with 1,251 tests,
16 requirements generations and a documented ledger of its own wrong beliefs,
attached to a set of commercial assumptions with no probe run against any of
them. Tier 1 above exists to make the first real test *safe and legal*. Tier 2
exists to make it *informative*. Everything in Tier 3 is an answer to a
question nobody has asked yet.
