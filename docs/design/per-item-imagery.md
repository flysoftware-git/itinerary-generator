# Per-Item Imagery

**Probed 2026-08-24 against generator `v2.1.0`, branch `v2`. Design note; not yet built.**

Images today are fetched **per destination**. The itinerary shows ~24 images across ten
stops, so a page listing Angels Landing, Emerald Pools and the Narrows illustrates all
three with the same generic Zion vista.

This note records a free-source probe showing per-**item** imagery is achievable at
**zero API cost**, and scopes the work.

> **Companion notes:** `image-selection-and-filtering.md` (what the current fetcher
> does) · `cost-accounting-and-reduction.md` §6.4 (why Maps Platform is not an option)

---

## 1. Why not Google Places Photos

**Never attempted — no key obtained, no code written.** The reasoning below is a reading
of documented terms and mechanics, not a report of something that failed in practice.

**The short answer is cost, not terms.** Three earlier drafts of this section argued the
terms forbade it. All three arguments failed under questioning, and the record of how is
kept below, because the failure mode is more instructive than the conclusion.

### The actual case

Photo names cannot be stored and must be re-fetched from a billed Place Details / Search
call — roughly 180 of them for one itinerary. Every reader view then bills again, with no
cap, for as long as the page exists. That is an open-ended liability attached to an
artifact we no longer control, in a product whose whole cost problem is per-run spend.

Against that: **Wikimedia covers 89% of items for nothing** — no key, no expiry, no
per-view billing, and it is already integrated. Places Photos would have to be
dramatically better to justify the difference, and on this corpus it is not better at all.

### Three arguments that did not survive

| Argument | Why it fails |
|---|---|
| "The photo URL embeds the API key" | Maps browser keys are *designed* to be public and protected by HTTP-referrer restrictions. Ordinary practice, not a blocker |
| "Caching is prohibited" | True of *our* storage, but browsers cache images routinely and Google serves photos with cache headers expecting exactly that. The terms bind the API customer, not the reader's browser. Applied to hotlinking, this was simply the wrong rule |
| "Photo names expire, so a static page decays" | Real, but not fatal. `html_assembler` already emits `onerror="this.style.display='none'"`, so a dead image hides itself. The page degrades gracefully rather than breaking |

The common thread: **each argument reasoned from a rule's name rather than checking what
it governs, and none was tested against code we had already written.** The `onerror`
handler had been in the tree the whole time. This is the §8.3 failure mode of the cost
note — name-versus-behaviour — showing up in design reasoning instead of in code.

Only the caching point retains a narrow edge: `sw.js` calls `cache.addAll`, which is code
we wrote deliberately storing bytes, and that is much closer to "pre-fetch, cache, or
store" than incidental browser caching. Grey, and not load-bearing.

### It could be made to work

A live backend refreshing photo names on demand satisfies every constraint above except
cost. That is a real architecture and not this product's, which emits a static artifact
and then has no runtime. §6.4 of the cost note reached the same place for Directions. If a
backend ever enters scope, **both decisions should be reopened together** — the same
constraint closed both, and it would lift for both at once.

---

## 2. Probe: free per-item coverage

180 attraction and trail names from the 2026-08-24 all-options run, taken from that
run's own batch captures. Matching by token containment, not intersection — the looser
test produced false positives in an earlier probe (§8.6 of the cost note).

| Source | Items with a per-item image |
|---|---|
| **Wikimedia Commons** | **160 (89%)** |
| NPS `/thingstodo` + `/places` | 57 (32%) |
| **Either** | **164 (91%)** |
| Neither | 16 (9%) |

**Wikimedia is the workhorse, and it is already integrated** — just queried per
destination rather than per item. The gap is query granularity, not capability.

NPS covers 32% because only five of the ten destinations have a `parkCode`. Where it
does apply it is the better source, returning more than a URL:

```
Full Moon Hike
  url      nps.gov/common/uploads/cropped_image/EA3…
  credit   NPS Photo
  altText  "A full moon rises in a blue and purple sky over the…"
  crops    [{aspectRatio: 1.78, url: …}, …]
```

Real alt text and pre-cropped aspect ratios, both of which the pipeline currently lacks.

### The 16 misses are not random

Two clusters:

- **Small local businesses** — Riff Raff Brewing, Wolfe Brewing, Healing Waters Resort,
  Geothermal Growing Domes
- **Compound or variant route names** — *"Wall Street and Queens Garden Loop (short
  variant)"*, *"Corona and Bowtie Arch via Corona Arch Trail"*

The second cluster is probably recoverable by retrying on a simplified name; "Corona
Arch" almost certainly has a Commons image even when the full route string does not. So
91% is a floor, not a ceiling.

The first cluster is genuinely absent from free sources — **and from paid ones too**. A
Pagosa Springs brewery is not in Places Photos in a licensable form either. That is the
honest limit, and it is a limit of the world rather than of the provider chosen.

---

## 3. Two defects this probe exposed

Neither is about relevance, and both are visible in the shipped output.

**Alt text is the destination name, repeated.** `html_assembler` emits
`alt="{dest_escaped}"` for every image in a gallery, so a screen-reader user hears the
same words 24 times. NPS supplies real `altText`; Wikimedia supplies
`extmetadata.ImageDescription`. Both are discarded today.

**Images are not right-sized.** `config.yaml` sets `images.thumb_width: 960`, but the
2026-08-24 run's cache held a **33 MB** file. For an installable PWA meant to work in
Capitol Reef with no signal, that is a product defect, and it now also inflates what
`sw.js` precaches at install. NPS's `crops` array is the ready-made answer where NPS is
the source.

---

## 4. Scope

**Query granularity.** Fetch per item, falling back to the destination-level image when
an item has none — so the change can only add relevance, never remove an image.

**Source order per item:** NPS where the destination has a `parkCode` (public domain,
richest metadata), then Wikimedia Commons, then the existing destination-level image.
Unsplash stays where it is, as the destination-level backstop.

**Carry the metadata through.** `alt` from NPS `altText` or Wikimedia
`ImageDescription`; `credit` and `license` already flow and are rendered. Fixing alt
text is arguably worth more than the relevance improvement and is nearly free once the
records are per-item.

**Retry on a simplified name** before giving up — strip parentheticals and
`"X via Y"` constructions. Cheap, and it addresses most of the second miss cluster.

**Cost: zero.** Both APIs are free. The constraint is rate limits, not money: Wikimedia
is throttled at ~1 req/sec by `image_fetcher`'s existing limiter, so ~180 item lookups
add roughly three minutes of wall clock on a cold run, cached thereafter.

### Verification

Predict before running, per the cost note's §8.3 rule:

- images per run rises from ~24 toward ~90 (one per published item, not per destination)
- **distinct** `alt` values rise from 10 (destination names) to near the image count
- no image regresses to *fewer* than today — the destination-level fallback guarantees it
- run cost unchanged, since neither API is billed

**The failure mode to watch:** more images that are *less* relevant. A per-item query
that matches loosely is worse than an honest destination photo. The earlier probe caught
exactly this class — token intersection matched *Zion Lodge* to *Stargazing in Zion* —
so item-to-image matching must use containment, and a low-confidence match should fall
back rather than publish.

---

## 5. Open

- **The image counters read zero and are correct.** `nps_api_calls`,
  `wikimedia_api_calls` and `unsplash_api_calls` report 0 while 34 downloads occur,
  because provider lookups are served from `.cache/images/cache_index.json`. This was
  briefly mistaken for an instrumentation defect. It is not.
- **Consequently, no "cold start" run in this project has been fully cold.** The
  benchmark procedure names `.cache/url_discovery/` and not `.cache/images/`. No cost
  impact — image lookups are free — but the procedure should name both.
