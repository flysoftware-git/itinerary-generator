# Places API for Restaurant Discovery

**Probed 2026-08-28 against generator `v2.2.0`. Decision note; nothing built.**

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
