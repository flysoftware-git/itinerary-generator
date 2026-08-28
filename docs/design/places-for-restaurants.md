# Places API for Restaurant Discovery

**Probed 2026-08-28 against generator `v2.2.0`. Option 1 (filter-only) built the same day; see §5.**

Asked whether Google Maps Platform could help with the restaurant problems that
have taken most of a working session to chase. It can, and the fit is closer
than anything else considered — which is why the terms question needs answering
before any code is written rather than after.

> **Companion notes:** `destination-type-coverage.md` (the defects this would
> address) · `cost-accounting-and-reduction.md` §6.4 (why Maps Platform was
> ruled out for Directions and imagery) · `per-item-imagery.md` §1 (the same
> reasoning, applied to photos)

---

## 1. What one call returns

`places:searchText`, one request, Amsterdam, `priceLevels` filtered to
`INEXPENSIVE` and `MODERATE`:

| | Current pipeline | Places Text Search |
|---|---|---|
| Candidates | **1** (2026-08-28 build) | **20** |
| Carrying a price level | ~85%, model-asserted | **20/20**, authoritative |
| Cuisine as a food style | leaked `Pflugstrasse 11`, `Photos & …` | **20/20** structured types |
| Official website | food blogs, aggregators | **19/20** `websiteUri` |
| Budget filtering | after the fact, by discarding | **server-side, in the query** |
| Existence | the entire verified-link-or-seed policy | inherent — a Place is a real place |

It returned **Vlaams Friteshuis Vleminckx**, the canonical Amsterdam friterie,
which is exactly what "low-cost, no fine dining" should surface and what this
pipeline has never once produced.

### Which defects it closes

Every restaurant defect from the 2026-08-27/28 sessions, at the source rather
than by filtering:

- **Thin sections.** 20 candidates against Amsterdam's 1.
- **Unpriced entries.** Frankfurt shipped five with no price; an item with no
  price bypasses the budget cap entirely, because the cap cannot judge what it
  cannot see.
- **Cuisine badges showing addresses.** `primaryTypeDisplayName` is an enum,
  not scraped text. No blocklist can be outrun by it.
- **Blogs standing in for official sites.** `websiteUri` is the restaurant's
  own domain — `thepantry.nl`, `moeders.com` — which is what
  `_domain_matches_item_name` currently reverse-engineers from search results.
- **Fine dining on a budget brief.** The price filter is a query parameter, so
  Michelin restaurants are never returned rather than returned and removed.
- **URL collisions.** Distinct places, distinct `place_id`s.

## 2. Cost is not the obstacle

| | Per 1,000 | Free monthly |
|---|---|---|
| Text Search Pro | $32.00 | 5,000 |
| Text Search Enterprise | $35.00 | 1,000 |

The field mask needed here (`priceLevel`, `websiteUri`, `rating`,
`primaryTypeDisplayName`) is likely Enterprise. Five destinations is **five
calls per run**: roughly **200 runs a month inside the free tier**, then about
$0.18 a run.

Against current run costs of $0.16–$0.54, that is small — and it may be
**negative**, because it could replace the Grok restaurant direct-batch rather
than sit alongside it. That batch is one of the larger remaining token costs.

## 3. The terms are the obstacle

Places content may not be cached or stored. Only `place_id` is exempt, and it
is exempt indefinitely.

This project publishes a **static HTML file** that is emailed, attached and
kept. Putting `priceLevel`, cuisine type and `websiteUri` into it is storing
Places content, plainly. §6.4 ruled Maps Platform out for Directions on exactly
this basis, and `per-item-imagery.md` §1 ruled out Places Photos the same way.

**This is a larger ask than the transit estimate already accepted.** That was
one derived number per leg. This is core content for every restaurant on the
page, refreshed never.

### What is defensible, and what is not

| Use | Assessment |
|---|---|
| Store `place_id` | **Explicitly permitted**, indefinitely |
| Use `priceLevel` at build time to *choose* which restaurants to include, and publish only the name | **Arguably fine** — the selection is ours; Google's data is an input we do not redistribute |
| Publish `websiteUri` as the restaurant's link | **Grey.** The destination is a third-party site, but the mapping came from Places |
| Publish `priceLevel` as a `$$` badge, or the cuisine type as a badge | **Not defensible.** That is redistributing Places content verbatim |

The middle row is the interesting one, and it is genuinely useful on its own:
using Places purely as a *filter* would fix the thin sections, the fine-dining
leakage and the invented restaurants, without publishing a single Places field.
The badges would keep coming from where they come from today.

## 4. Recommendation

**Ask before building.** Three options, in the order I would take them:

1. **Filter-only.** Use Places to choose which restaurants appear, publish no
   Places field. Fixes the biggest problems, keeps the terms question narrow,
   and needs no decision from anyone but us.
2. **Full adoption**, publishing price, cuisine and website. Fixes everything
   listed in §1, and requires a deliberate decision to store Places content in
   a published artifact — the same decision already made for transit
   estimates, but wider.
3. **Do neither**, and keep tuning prompts. The record of this session argues
   against it: the ask has been rewritten four times and the model still
   returned three Michelin restaurants for Berlin on a "No fine dining" brief.

Option 1 is the one I would build. It is most of the value, and it does not
require deciding the hard question first — though it should be recorded that
choosing it is a *narrowing*, not an avoidance.

---

## 5. Built: filter-only

Chosen and implemented 2026-08-28 as `generator/places_filter.py`. Places
decides which of our own candidates survive; no field it returns is published.

The boundary is enforced by the **field mask**, not by intent:
`places.id,places.displayName,places.priceLevel`. `websiteUri`, `rating`,
`photos` and `editorialSummary` are not requested at all, which is a stronger
guarantee than deciding not to render them. A test asserts the mask.

### Three things the probe corrected

**Server-side price filtering hides the expensive.** Filtering the query to
inexpensive levels made costly restaurants *absent*, which is
indistinguishable from a place Google has never heard of — Ciel Bleu and a
corner café both returned `unknown`, so neither could be rejected. The
destination sweep runs unfiltered as well, and that pass supplies the levels
that make "too expensive" sayable.

**A city-wide sweep does not reach our candidates.** Forty places is nothing
against a city: Horváth, Comme Chez Soi and Madami were all `unknown` against
one. A *targeted per-name* lookup answered 11 of 12 correctly. Those calls are
spent only on items whose own price is missing or already looks wrong — an
item marked `$` or `$$` needs no second opinion.

**Decoration in the query changes the answer.** Names arrive as
`**Comme Chez Soi**`, sometimes with the rating attached. Querying that returns
*two* places and ranks a different `MODERATE` one first; the clean name returns
the single `VERY_EXPENSIVE` match. The asterisks flipped the verdict and put a
two-star restaurant back on a "No fine dining" itinerary.

That one nearly escaped. A first test showed decorated and clean names giving
identical verdicts — because `lookup_one` memoises, so the second call never
queried and returned the first one's answer. **A memo that makes two different
inputs look equivalent is precisely the shape that hides this class of bug.**

### Result, and the problem it revealed

Every Michelin entry is now rejected: Ciel Bleu, Rutz, Tim Raue, Facil,
La Dégustation, V Zátiší, Lafleur, Seven Swans, Comme Chez Soi, Field. Zero
`$$$` or `$$$$` reach the page.

**And the page then had 13 restaurants across five cities**, one each for
Amsterdam and Frankfurt — Frankfurt had 7 of 8 candidates rejected.

The filter did not cause that; it revealed it. Every gate downstream had grown
stricter — verified-link-or-seed, the budget cap, now the price check — while
`restaurant_direct_batch_item_count` stayed at 8. **The ask has to exceed the
survivors, not match them.** Raised to 20.

Worth keeping as the general lesson: each gate was individually justified, and
nothing was watching their product. A correctness fix that empties the page is
not finished.
