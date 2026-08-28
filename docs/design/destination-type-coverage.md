# Destination-Type Coverage

**Measured 2026-08-25 against generator `v2.2.0`, run `output/oldhickory`, cost $0.3582.
Two findings; neither is caused by the v2.2.0 changes.**

> **Resolved 2026-08-28 — read §5 first.** The question this note is built around,
> *which kind of destination needs which threshold*, turned out to be the wrong
> question. The 77% was six pipeline defects, none of them related to the
> destination. The measurements below are accurate and the framing is not.

The first real run of a **non-park** destination. Every prior benchmark used
`sw_manifest.yaml` — ten destinations, five with an NPS `parkCode`. Old Hickory,
Tennessee has none, and the difference is larger than expected.

> **Companion notes:** `fallback-curation-contract.md` (the verified-link-or-seed
> policy) · `per-item-imagery.md` (the imagery half of the same run) ·
> `cost-accounting-and-reduction.md` §8.3 (predict-then-measure)

---

## 1. The quality gate is calibrated on one fixture

`config.yaml` sets `quality_gate.max_no_url_restaurants: 0` — *no* restaurant may be
dropped for lacking a verified URL before the gate warns. On this run:

| | Candidates | Removed | Rendered | Threshold |
|---|---|---|---|---|
| Restaurants | 13 | **10 (77%)** | 3 | 0 |
| En-route stops | 7 | **4 (57%)** | — | 2 |
| Attractions | — | — | 2 | 3 |

**Reproduced 2026-08-25 on a third run** (`output/oldhickory-v3`, $0.0645) at exactly
10 of 13. This is a stable property of the destination, not a one-run fluctuation.

The removed names are not hallucinations. Granddaddy's Original Hot Chicken Shack,
BODHI Asian Street Eats, Simply Thai, Gondola House, Sam's Sports Grill — these are
real businesses in and around Old Hickory. They were discarded because **no
verifiable source URL survived discovery**, which is the verified-link-or-seed
policy working exactly as designed.

**The policy is right. The threshold is calibrated on the wrong sample.**

A national park's surroundings are densely indexed: NPS pages, AllTrails, established
review sites. A Nashville suburb's dining is Facebook pages, aggregator stubs, and
listings with no stable canonical URL. The same policy applied to both yields
near-complete dining coverage in one case and **77% loss** in the other.

### Why this was invisible until now

Every threshold in `quality_gate` was tuned during the 14–18 August dipstick window,
and **every one of those runs was `sw_manifest`**. A threshold of 0 removals was
achievable there. It is not a general standard; it is a park-shaped one, and nothing
recorded that distinction at the time.

This is the same failure as §8.3's name-versus-behaviour cases: a number that looks
like a universal quality bar is really an artifact of the single fixture it was
measured against.

### The axis is not park-vs-everything-else

An earlier draft framed this as parks against the rest, because Old Hickory was the
only counter-example available. **That framing is wrong**, and the correction came
from the product owner: a threshold loosened for "non-park" would also loosen it for
Paris, where nothing about the coverage is thin. Old Hickory is not thin because it
lacks a `parkCode`; it is thin because a Nashville suburb of 12,000 people has almost
no indexed commercial web presence.

The differentiator under test is **how densely the destination is indexed**, with
cities as the proposed third class:

| Class | Fixture | Expected coverage |
|---|---|---|
| Park | `sw_manifest.yaml` | good — NPS, AllTrails, established review sites |
| **City** | `europe_cities.yaml` | **good, possibly best** — dense commercial presence |
| Town / suburb | `old_hickory.yaml` | thin — Facebook pages, aggregator stubs |

If cities come out rich, "park vs town" was a proxy and the real variable is
population or indexing density. If cities come out thin too, the hypothesis is wrong
and something else explains Old Hickory.

### Options, not yet chosen

1. **Destination-type-aware thresholds** — each class gets a realistic budget. Adds a
   config axis and, harder, needs a classification rule. `parkCode` presence is not
   it, per the correction above.
2. **Report coverage as a ratio, not a count.** "10 removed" means nothing without
   "of 13". A ratio is comparable across destination types and would have surfaced
   this on the first non-park run rather than the tenth park one.
3. **Accept lower dining coverage for non-park stops** and say so in the output,
   rather than silently shipping three restaurants where a reader expects a dozen.

**Recommendation: (2) first, unconditionally.** It is a reporting change with no
behavioural risk, and it is the one that makes the other two decidable with data.

**Done 2026-08-25**, and it immediately earned itself. Three runs of the same
manifest reported 10, then 5, then 10 removals. The middle run looked like the
name-sanitisation fix had halved the loss. It had not: the denominator had moved.

| Run | Reported | What it meant |
|---|---|---|
| `oldhickory` | `10` | no denominator — uninterpretable |
| `oldhickory-v2` | `5 of 8 (62%)` | fewer *candidates* (cache state), not better coverage |
| `oldhickory-v3` | `10 of 13 (77%)` | like-for-like with the first run |

Under the old format the middle run reads as a 50% improvement worth shipping.
**The ratio is what showed the denominator had changed** — which is the entire
argument for the change, demonstrated within a day of making it.

### What this does not justify

Loosening the policy itself. Publishing a restaurant with no verifiable URL is how
this project previously shipped *"Stan's Overlook Trail, Snoqualmie, WA"* onto a Utah
itinerary. **Fewer, verified items is the correct trade** — the open question is only
what the gate should *report*, not what it should *admit*.

---

## 2. Markdown emphasis reaches the published page

Separate defect, found in the same run, **and it is not new**.

Item names arrive from the content model carrying markdown bold, and it is never
stripped. In `output/oldhickory/index.html`:

```html
<a href="https://flattirediner.com" ...>**Flat Tire Diner**</a>
```

All three surviving restaurants render this way, as do two trails. Ten distinct names
carry `**` in the run's own status report.

It predates v2.2.0 by at least a week:

| Run | Date | Literal `**` pairs in `index.html` |
|---|---|---|
| `SW2026-dipstick75` | 2026-08-18 | **24** |
| `coldstart-full-options` | 2026-08-24 | 4 |
| `output/oldhickory` | 2026-08-25 | 4 |

**Twenty-four visible on a page that passed a dipstick review.** That is the more
interesting fact: this is not subtle, it is asterisks around the name of every other
card, and it survived both the review pass and the validator.

**Confirmed with the reviewer**, rather than inferred: they were not looking for that
class of defect. The dipstick passes were hunting geography errors, relevance and
tone — the failures that had burned the project before — and a formatting defect in
plain sight was simply not what the eye was tuned for. Worth stating because the
lesson is not "the review was careless"; it is that a review shaped by past failures
is blind to defect classes that have not bitten yet, which is an argument for the
validator to carry the mechanical checks rather than the reader.

`html_assembler._sanitize_restaurant_display_name` already exists and already strips
decoration — rating, price, cuisine — added for dipstick55 Theme D. It does not strip
emphasis, and it only covers restaurants. The fix belongs one level up, at the
boundary where model-authored text becomes a display name, and applies to every
category.

**Do not fix it by rendering the markdown.** These are names, not prose; the model
should not be emphasising them at all, and turning `**` into `<strong>` would make a
formatting defect into a permanent styling decision.

### Fixed 2026-08-25 — on the second attempt

The first fix stripped `ai_content`'s payload and was reported as done. The paid run
meant to confirm it **still shipped four asterisk pairs**, because names arrive from
**two** sources: `url_discovery`'s direct-link batch harvests its own names *after*
`ai_content` has run, and every asterisk-bearing log line in that run came from
`url_discovery`.

The same error the trails switch took five attempts to close: asking *"is this path
covered?"* rather than *"is this the only source?"*. The word "chokepoint" was in the
commit message while the code was doing the opposite.

`normalize_trip_content` runs downstream of every stage and is the only point that can
guarantee the invariant. Confirmed at **zero pairs** on `oldhickory-v3`.

---

## 3. The city arm: `europe_cities.yaml`

**Written 2026-08-27, not yet run.** Brussels (2 nights) → Amsterdam (3) → Berlin (3)
→ Prague (3) → Frankfurt (2), 31 August–12 September 2026, rail between every stop,
low-cost dining, no hikes. Arrival and departure to Europe are deliberately out of
scope.

Five major capitals is the strongest available test of the density hypothesis: if
dense commercial indexing is what drives coverage, this should be the *best* result
the project has produced, not merely better than a suburb.

### Predictions, recorded before the run

| | Prediction | Outcome |
|---|---|---|
| Restaurant removals | well under 77%, plausibly **under 20%** | ❌ **77%** — identical to the town |
| Cost per destination | **above** `sw_manifest` | ✅ $0.2043 for one dense city |
| En-route stops | weak or absent: the harvest assumes road travel, these legs are rail | — first destination has no inbound leg; untested |
| Budget hint | low-cost brief reaches restaurant selection | ❌ **inert** — control produced identical output |

The middle two are not tests of the hypothesis, but they are worth stating in advance
so the run cannot be read selectively afterwards.

### Result: the hypothesis is falsified

**Ran 2026-08-27**, Brussels only, $0.2043.

| | Restaurants removed |
|---|---|
| Old Hickory (town, ~12,000) | **10 of 13 (77%)** |
| Brussels (capital, ~1.2M) | **10 of 13 (77%)** |

Identical, to the item. A major European capital loses exactly as much dining as a
Nashville suburb, so **indexing density is not the differentiator**. The prediction was
"plausibly under 20%"; the result is 77%.

Parks remain the outlier in the right direction — the ten-destination `sw_manifest`
runs show 0.5–2.4 removals *per destination* against 10 here — but whatever makes them
different, it is not that they are more densely indexed than Brussels.

### A second hypothesis, also falsified

The removed names (Rotisse, Thaiburi, Yummy Bowl, Pasta Divina) read as cheap
eateries while the survivors (The Lobster House, 9 et Voisins, Brasserie Signature)
read as upmarket, suggesting the gate was inverting the low-cost brief — asking for
cheap food and getting expensive food, because cheap places do not maintain websites.

**Two checks killed it.** A control run with the budget block removed and nothing else
changed produced the *identical* restaurant set, and the survivors' published price
badges are **$$**, not the $$$ the story required. The reading came from how the names
sound, which is not evidence.

### What the experiment did find

**The `budget` field is inert.** Treatment and control proposed the same restaurants,
name for name. The manifest schema documents budget as "consumed by content
generation" and `_build_budget_guidance` does reach the prompt, so this is not missing
wiring — the guidance simply does not change the output.

`prompts/destination_content.txt` suggests why, though this is not yet proven:

```
- Span at least 2 price tiers: at least one at $ or $$ AND at least one at $$$ or $$$$
- Respect the budget guidance above when selecting the center-of-gravity of options
- Include at least one recognisably casual / local option ($ or $$)
- Include at least one higher-end option ($$$ or $$$$)
```

A hard *span* requirement sits directly above a soft *centre-of-gravity* preference.
If the span dominates, no budget value can move the result, which is what was
measured. **Testable**: relax the span for an explicit low-cost budget and re-run.

**A cathedral in the dinner list.** "Mechelen St. Rumbold's Cathedral" was proposed as
a Brussels restaurant — wrong category, and Mechelen is a different city 25 km away.
Same defect class as the Snoqualmie trail. The gate removed it, so it never reached
the page, but it is a reminder that the gate is carrying real weight, not just
trimming.

### Where that leaves the threshold question

**Nowhere yet, and that is the useful outcome.** Both proposed explanations for the
77% are dead, so a destination-type-aware threshold would be tuning against a variable
nobody has identified. The next question is not "what budget should a city get" but
**"why do parks differ at all"** — the only surviving signal.

> **Superseded 2026-08-28.** Parks do not differ. Nothing about the destination
> explained the loss; see §5.

### What else this exercises for the first time

- **Non-US destinations end to end** — geocoding, address formats, and whether
  generated content handles currency and language sensibly.
- **Rail as the connecting mode** rather than driving, across four international
  borders.
- **Five destinations**, so grouping and cross-destination deduplication get a real
  workout; `old_hickory.yaml` has one and exercises neither.

Any of those three could produce a defect that has nothing to do with coverage. That
is a feature of the experiment, not a confound — the project has never generated a
non-US itinerary.

---

## 4. What the Old Hickory run confirmed

Recorded because the predictions were made before the run, per §8.3:

- **Images publish at source.** Every href is a `upload.wikimedia.org` URL; zero
  `./images/` references; `sw.js` precached 4. The v2.2.0 change works end to end.
- **Image sizes were fine here** — 72–161 KB. The 33 MB case in `per-item-imagery.md`
  is latent, not triggered, so that defect remains unproven outside the one cache.
- **Cost scaled as expected.** $0.3582 for one destination against $2.82 for ten;
  $0.0375 and $0.0645 for the two cached reruns.

And one prediction that was **wrong in severity**: `per-item-imagery.md` §4 expected a
non-park destination to yield "a thin gallery". It yielded a thin gallery *and* a
hero image of a **church cornerstone** on a trip about a lake, a dam and the
Hermitage. Per-destination imagery does not merely under-illustrate a non-park stop;
it can actively mislead about what the stop is.

---

## 5. Resolved: it was never the destination

**2026-08-28.** The question this note kept asking — which *kind* of destination gets
which threshold — had no answer because it was the wrong question. Not one of the
causes eventually found had anything to do with parks, cities or towns.

### What the 77% actually was

Six independent defects, each of which discarded or degraded restaurants regardless of
where the destination was:

| Cause | Effect |
|---|---|
| `"low-cost"` matched none of the budget keywords | The budget filter never ran at all |
| The budget cap ran *before* the batch replaced the list | It capped a list that was then thrown away |
| The fine-dining instruction reached **one of four** prompts | A rating floor with no price guidance selected for Michelin |
| The batch cache keyed on destination, not on the ask | Every prompt fix was invisible until the cache cleared |
| The cap ran *before* price enrichment | Unpriced items bypassed it entirely |
| `restaurant_direct_batch_item_count` stayed at 8 | Every gate grew stricter while the ask did not |

Old Hickory and Brussels both lost 77% because **both were run through the same broken
pipeline**. The suburb and the capital were never the variable.

### How the wrong question survived so long

Every measurement was real. Old Hickory's 77% was reproduced three times; Brussels
matched it to the item. What was wrong was the *frame*: a difference was observed
between fixtures, so a property of the fixtures was assumed to explain it.

The correction did not come from more measurement of destinations. It came from asking
whether the removed restaurants were real — they were, with official websites a single
search away — which reframed the whole thing from a **coverage** problem to a
**discovery** problem in one step.

**The lesson is the sequence, not the answer.** "Which destination type is different"
was measurable, and answering it precisely produced eleven pages of correct,
irrelevant analysis. "Are these restaurants real" was one query and settled it.

### What the thresholds should be

Unchanged, and now for a reason rather than for want of data. `max_no_url_restaurants: 0`
is a sound bar: the current build removes 36% of candidates and publishes 70
restaurants across five cities, because the *ask* is now 20 per destination rather
than 8. **A threshold expresses what is acceptable; it cannot manufacture candidates.**
The fix was always upstream.

Destination-type-aware thresholds are **not recommended**. There is no evidence any
destination type needs a different bar, and the classification rule that would be
required — `parkCode` presence was rejected earlier in this note — has no basis left.

### What remains true from the original finding

Two things survive the reframing:

- **Reporting removals as a ratio** was right, and immediately proved itself by
  exposing a moving denominator that a bare count had hidden (§1).
- **The verified-link-or-seed policy is correct.** It was never the cause; it was the
  gate that made the upstream defects visible. Publishing unverified items is how a
  Snoqualmie trail once reached a Utah itinerary.

### Still open

- **`europe_cities.yaml` is not a coverage experiment any more.** It earned its place
  as the fixture that exposed a pipeline built throughout for US road trips: en-route
  stops invented for rail legs, driving directions written for booked trains, and a
  day-count regex that read "August 31 - September 1" as one day. Keep it for that.
- **The batch cache fingerprint covers only the restaurant query.** An edit to the
  attraction prompt is still invisible until the cache clears — the same defect that
  hid three restaurant fixes, still live one module over.
