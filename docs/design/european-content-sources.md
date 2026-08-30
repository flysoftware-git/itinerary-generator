# European Content Sources

**Probed 2026-08-27 against generator `v2.2.0`. Design note; nothing built.**

Asked whether Rick Steves' writing, books and site would improve discovery for
European destinations. Short answer: **strong for attractions, useless for the
problem we actually have, and the part worth taking is the part least defensible to
take.** A better-licensed source with the right data turned up while checking.

> **Companion notes:** `destination-type-coverage.md` (the 77% dining loss this is
> trying to solve) · `per-item-imagery.md` (the same licence reasoning applied to
> images)

---

## 1. What ricksteves.com actually offers

`robots.txt` permits the destination pages; only interviews, travel news and `/search`
are disallowed. So access is not the obstacle.

The Brussels page carries roughly **20 named sights**, and — the interesting part — an
**"At a Glance" section ranking them ▲▲▲ / ▲▲ / ▲**. That is a curated importance
ordering by a trusted editorial voice, which is precisely what a generated itinerary
lacks. Grand-Place and the Royal Museums carry three triangles; Autoworld does not.

**But there are no restaurants.** Not thin — *absent*. The free pages name chocolate
shops as a category and stop there. Restaurant listings are the paid guidebooks'
content, and they are the reason the books sell.

That matters because **dining is the failure we are trying to fix.** Old Hickory and
Brussels both lose 77% of restaurants; attraction coverage is comparatively fine. Rick
Steves is strong exactly where we do not need help.

## 2. The copyright line, which is not where it first appears

Place *names* are facts. "Grand-Place is in Brussels" is not protectable, and
harvesting names to drive our own verification would not infringe.

**The star ratings are the problem.** ▲▲▲ versus ▲ is editorial judgement — the
distilled product of decades of work, and the thing customers pay for. Copying that
ranking is not taking a fact; it is taking the assessment, and doing so commercially
while adding nothing. The prose is more clearly off-limits still.

So the value decomposes badly:

| | Legally clean | Useful to us |
|---|---|---|
| Place names | yes | marginally — we already find attractions |
| **Star rankings** | **no** | **yes — this is the actual value** |
| Prose descriptions | no | yes |
| Restaurants | — | **not present** |

No published terms-of-use page was found (`/about-us/legal` returns 404); the
syndication FAQ states Rick reserves all rights to his content. Absence of a stated
licence is not permission.

**Recommendation: do not integrate.** Not primarily a legal call — it simply does not
address the dining gap, and the piece that would help is the piece we should not take.

## 3. Wikivoyage does have what we need

Found while checking the above. Same Wikimedia family already used for images,
licensed **CC BY-SA** — explicit permission to reuse with attribution, which the
project already renders for image credits.

Large cities push dining into district subpages, which is why a city-level probe looks
empty and nearly caused this to be dismissed:

| Page | Named eateries | With URLs | Price tiers |
|---|---|---|---|
| `Brussels/Pentagon` | 18 | 8 | Budget · Mid-range · Splurge |
| `Brussels/Centre` | 7 | 2 | Budget · Mid-range |
| `Brussels/European Quarter` | 0 | 0 | — |
| **Brussels total** | **25** | **10** | |

Real names — *La Friterie de la Place de la Chapelle*, *Friterie Tabora*, *Café De
Markten* — with **explicit budget tiers**. That is the exact shape the low-cost brief
needs and could not get: our pipeline proposed cheap places and then discarded them for
lacking verifiable URLs.

### Why this may not rescue the 77%

Stated up front, because two hypotheses have already died here by being assumed rather
than measured:

- **10 of 25 carry URLs.** Better than nothing, but the verified-link-or-seed policy
  would still drop over half.
- A Wikivoyage listing is arguably itself a **verification source** — a curated,
  attributed mention. Whether that satisfies the policy is a design decision nobody has
  made, and it is the more interesting question than the integration.
- Coverage is uneven and volunteer-driven. The European Quarter has nothing.
- **Europe-only in practice.** It would not help Old Hickory, which is half the problem.

### Cost

Zero. Wikivoyage is a MediaWiki API, same as the Commons calls already made, with no
key and no per-request charge.

## 4. What to do

1. **Drop Rick Steves.** Wrong data, and the useful part is not ours to take.
2. **Probe Wikivoyage against the removal set** — do the 10 Brussels restaurants we
   discarded appear there, with URLs? That is a free offline check and it decides
   whether integration is worth scoping.
3. **Answer the policy question first.** If a CC BY-SA listing counts as verification,
   coverage improves without any new source at all, and that reasoning applies to US
   destinations too.

Sequenced deliberately: (3) may make (2) unnecessary, and both are free.

## Low budget selects for businesses without websites (2026-08-29)

With removed items finally recorded by name, Europe's restaurant removals
resolve into a coherent group: *Fritland*, *Friterie Tabora*, *Friterie de la
Barrière*, *Patatak*, *Munch*, *Kaf Kaf*, *Ratz Food Market* (Brussels);
*Broodje van Kootje*, *Pietersma Snacks* (Amsterdam); *Doner Kebab Zizkov*,
*Station Anděl* (Prague); *Frittenwerk*, *Im Biss*, *Gref-Völsings Braterei*
(Frankfurt).

Friteries, snack bars, kebab counters and market stalls. The manifest asks for
low-cost restaurants, discovery correctly finds exactly the right places, and
then verified-link-or-seed removes them for having no verifiable URL — because
that class of business largely does not have a website.

Nothing is malfunctioning. Two policies set independently pull against each
other: *find me cheap local food* selects for the businesses least likely to
have a web presence, and *every item needs a verified link* then deletes them.

This is a genuine European-content characteristic in a way most of this note's
other findings are not — a US suburb's cheap restaurant usually still has a
site. It is worth deciding deliberately rather than reading as a discovery
failure. The candidate trail distinguishes the two cases directly:
`candidates_considered: 0` means nothing was ever found, while a non-zero count
with rejections means a link was found and refused.

Not to be confused with the landmark removals in the same reports — *Prague
Castle*, *St. Vitus Cathedral* — which had official pages ranking first or
second and were pipeline defects, since fixed. See `url-discovery-and-audit.md`.
