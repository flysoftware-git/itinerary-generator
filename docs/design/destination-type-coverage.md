# Destination-Type Coverage

**Measured 2026-08-25 against generator `v2.2.0`, run `output/oldhickory`, cost $0.3582.
Two findings; neither is caused by the v2.2.0 changes.**

The first real run of a **non-park** destination. Every prior benchmark used
`sw_manifest.yaml` — ten destinations, five with an NPS `parkCode`. Old Hickory,
Tennessee has none, and the difference is larger than expected.

> **Companion notes:** `fallback-curation-contract.md` (the verified-link-or-seed
> policy) · `per-item-imagery.md` (the imagery half of the same run) ·
> `cost-accounting-and-reduction.md` §8.3 (predict-then-measure)

---

## 1. The quality gate is calibrated on parks

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

### Options, not yet chosen

1. **Destination-type-aware thresholds** — a `parkCode`-bearing destination keeps 0;
   others get a realistic budget. Honest, but adds a config axis and needs a rule for
   classifying destinations that is not itself park-centric.
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

## 3. What the run confirmed

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
