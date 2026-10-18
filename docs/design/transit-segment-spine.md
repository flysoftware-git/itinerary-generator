# The Transit-Segment Spine

**Design note · drafted 2026-10-18 against generator `v3.3.0`, branch `v3`.**

A guide is a sequence of stops, and between every pair of them is a journey.
This note is about the journey: what the manifest can say about it, and how a
page draws it.

> **Requires:** `requirements.md` §3.1.2 (authored leg shape) ·
> `multimodal-routing.md` §3.2 (the `legs:` id contract) ·
> `the-manifest-is-the-interface.md` §2 (the obligation is not to lose what was
> handed in)

---

## 1. What was wrong

Movement between stops rendered as a short bar wedged under a day rule,
sometimes with a word on it. Everything else about the journey — how long it
takes, whether it crosses a night, what is on the road — either had nowhere to
go or went into prose inside the arriving stop's own section.

The consequence is that **every journey was the same size**. A nine-mile drive
between two towns and a fourteen-hour overnight sailing rendered at identical
length, because length carried nothing. Duration is the one property of a
journey a reader actually plans around, and it was the one the rendering threw
away.

It was thrown away earlier than the rendering, though, and that is the part
that had to be fixed first.

## 2. Why the schema was the gating problem

`LEG_ITEM_SCHEMA` accepted exactly:

```yaml
legs:
  - from: venice      # a destination id
    to: koper         # the next destination id
    mode: transit
```

with `additionalProperties: False`. So a leg could say where it starts, where
it ends, and how it is travelled. There was no field for how long it takes, for
a night spent in transit, or for anything else about the journey — and because
the shape is closed, an author could not add one by writing an extra key. The
manifest was **refused**, not quietly degraded. That is the better of the two
failures, but the fact still could not be carried.

Four fields were added (2026-10-18), all optional, all additive. A leg authored
before them parses and renders exactly as it did.

| Field | What it carries |
|---|---|
| `duration` | How long the journey takes, as the author states it |
| `depart_time` | Clock time it leaves, local to `from` |
| `arrive_time` | Clock time it arrives, local to `to` |
| `nights` | Nights spent **in transit** on this leg |

### 2.1 Why `duration` is not `travel_time`

`travel_time` and `distance_miles` already exist. They are **AI-generated
figures on the arriving stop's getting-here content** — a build's estimate,
regenerated and revised every run. An author who knows the sailing is fourteen
hours because it says so on the ticket had nowhere to put that, and the guide
showed the guess instead.

`duration` is a display string, like `dates` and `depart`: transcribed, not
computed. Nothing does arithmetic on it.

### 2.2 Why `nights` is stated rather than derived

`depart_time: "18:00"` with `arrive_time: "08:00"` is the same pair of strings
whether the arrival is the next morning or fourteen hours of the same day read
off two different clocks. The two differ by whether the traveler is sleeping
somewhere, which is the whole question a reader is asking.

Nothing may close that gap by arithmetic, because
`transportation[].depart_time` — whose meaning these two deliberately copy —
carries an explicit rule that the clocks are never normalized to one zone. A
guide asserting a night aboard on the strength of subtracting two times it was
told not to compare would be inventing a fact that looks precise.

So `nights` is the author's statement. Omitted means the journey lands the day
it left, which is every leg authored before this field existed. `0` says so
explicitly, and the two are kept distinguishable: the first is a fact a
renderer may repeat, the second is an author not having said.

### 2.3 Why there is no `see_on_the_way` on a leg

Places on the road are already expressible: `en_route_seeds` on the **arriving**
destination nominates them for the leg into it, and discovery returns a
description and a detour cost per stop. The leg `{from: koper, to: rijeka}` and
`rijeka.en_route_seeds` are two statements about the same journey, so a
renderer can draw one segment carrying both.

A second list on the leg would be one value restated in two places, free to
diverge — the defect class `_validate_legs` already raises on for `mode`. What
en-route content was missing was not a field; it was somewhere on the page to
be.

This is recorded as a decision, not a closed question. See §5.

## 3. What the page draws

A **segment**: a vertical rail in the left margin between two stop nodes.

- **The rail between two nodes is the journey.** Its length is scaled from the
  leg's duration, so a long journey is long on the page. That is the property
  the old rendering discarded.
- **The mode rides on the rail as a marker**, not as a word in a sentence.
- **A day rule crosses the rail rather than interrupting it.** A journey that
  spans a night is one journey with a date boundary drawn across it, which is
  what a traveler experiences; the old rendering had to cut the journey in two
  or drop the night entirely.
- **En-route stops hang off the rail** at their position along it.

### 3.1 Length

Duration is parsed for scaling only, and only from forms an author actually
writes (`2h40`, `14 hours`, `50 min`, `1:50`). The parse is for pixels; the
string the author wrote is what is displayed, always. An unparseable duration
renders the string and takes the default length — the figure is never dropped
because the scaler could not read it.

Scaling is bounded. Fourteen hours drawn to the same pixels-per-hour as forty
minutes would be metres of page, so length is compressed above a threshold:
the rail says *longer* reliably and *how much longer* approximately. A reader
gets the figure from the text.

### 3.2 The quiet case

Most legs have nothing to say beyond a mode. A segment with no duration, no
times, no nights and no stops renders as a **bare rail with its mode marker**:
no card, no header, no badges, no empty rows. This is deliberate and is the
case that decides whether the page reads calmer or busier than before.

## 4. Where the code is

| File | Role |
|---|---|
| `generator/manifest_parser.py` | `LEG_ITEM_SCHEMA` — the four fields |
| `generator/transit_routing.py` | `stamp_resolved_leg` — resolves the leg once and stamps it on the arriving stop, the same pattern and for the same reason as `RESOLVED_MODE_KEY` |
| `generator/partials.py` | Loads a template partial, checksum-verified |
| `templates/partials/transit_spine.html` | The segment's markup template and CSS |
| `generator/transit_spine.py` | Renders one segment from a leg plus its arriving stop |
| `generator/html_assembler.py` | Emits the partial's CSS once, and a segment per stop |

### 4.1 Why a partial

`templates/` held one monolithic template. A component meant to be drawn by
more than one consumer of this engine cannot live inside it: there is nothing
to include. `templates/partials/` plus `generator/partials.py` is the smallest
thing that makes a component addressable — a named file, loaded and
checksum-verified like the main template, so a consumer draws the same
component rather than its own copy of the idea.

## 5. Open

Not settled. Listed so they are argued rather than inherited.

1. **What the rail carries per mode beyond an icon.** Today a marker and a
   glyph. A sailing, a train and a drive differ in ways a reader plans around —
   whether there is a cabin, whether you can leave your seat — and none of that
   is on the rail.

2. **Whether an en-route suggestion is a node on the rail or a block beside
   it.** Currently a block beside it: the rail's nodes are stops the itinerary
   is built from, and promoting a suggestion to a node would say the traveler
   is stopping there when the point is that they might. The counter-argument is
   that a thing on the road genuinely is on the road, and a reader scanning the
   rail for "what happens between these two places" will not look to the side.

3. **Whether the length scale should be per-trip rather than fixed.** A
   cruise's legs are all long and a road trip's are all short, so a fixed scale
   makes one guide's rails uniformly tall and another's uniformly flat, which
   is the opposite of carrying duration. Normalizing per trip would make
   lengths comparable within a guide and meaningless across guides.

4. **Whether `see_on_the_way` belongs on a leg after all** (§2.3). The
   argument against is the drift class. The argument for is that
   `en_route_seeds` is name-hints-only by rule, so an author who knows *why* a
   stop is worth it still cannot say so — only discovery can, and it is
   rewritten every run.
