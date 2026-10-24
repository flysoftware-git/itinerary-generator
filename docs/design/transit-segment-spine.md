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

Three fields were added (2026-10-18), all optional, all additive. A leg
authored before them parses and renders exactly as it did.

| Field | What it carries |
|---|---|
| `duration` | How long the journey takes, as the author states it |
| `depart_time` | Clock time it leaves, local to `from` |
| `arrive_time` | Clock time it arrives, local to `to` |

### 2.1 Why `duration` is not `travel_time`

`travel_time` and `distance_miles` already exist. They are **AI-generated
figures on the arriving stop's getting-here content** — a build's estimate,
regenerated and revised every run. An author who knows the sailing is fourteen
hours because it says so on the ticket had nowhere to put that, and the guide
showed the guess instead.

`duration` is a display string, like `dates` and `depart`: transcribed, not
computed. Nothing does arithmetic on it.

### 2.2 Why a leg carries no count of nights

A `nights: 1` integer shipped on the leg in the first version of this and was
taken off again (2026-10-19). It was the wrong shape, and the reason is a rule
about the format rather than about this component:

> **The dates are the authority in the manifest.** How long anything lasts is
> expressed through the destinations' `dates` and nowhere else.

A number on a leg saying how many nights it spans is a second answer to a
question `dates` already answers, and two answers to one question diverge. This
format has no mechanism that would keep them in step, and no way to say which
one wins when they disagree -- so the only safe number of such fields is none.

`duration` is not an exception, and the distinction is worth stating because it
is the one a reviewer will press on. `duration` is a **transcribed display
string**, like `dates` and `depart` -- "2h40", "about fifty minutes" -- read off
a ticket and never computed, summed or compared. It states what a document
says. `nights` was an integer expressing a length, which is the thing the rule
is about.

**So it is derived, and the capability is not lost.** `nights_in_transit` in
`generator/transit_spine.py` requires two things to agree:

1. **The dates must leave a night between the two stays.** The last day `from`
   claims and the first day `to` claims, differenced. Adjacent stays usually
   share a day -- you leave one place and reach the next on the same date --
   and that is nought nights. Either span failing to parse is nought too:
   `date_span.span` returns None rather than guessing, and a guide must not
   assert a night aboard off a typo.

2. **The leg must account for occupying that night.** Koper on the 4th and
   Rijeka on the 5th leaves a night between them, but the leg joining them is
   a 1h50 coast road and the traveler slept in a bed. So the leg's own
   `depart_time` plus its `duration` must still be running when the date turns
   over.

The smaller of the two wins. The dates can veto what the duration claims and
cannot be overridden by it, which is what "the dates are the authority" means
in practice.

**What is still never done.** `depart_time` and `arrive_time` are not
subtracted from each other. They are on two different clocks by design --
`transportation[].depart_time` carries the rule that neither is normalized to
one zone -- so a sailing leaving Venice at 18:00 and reaching Koper at 08:00
states two clocks, and differencing them gives a precise-looking wrong answer.
Condition 2 above uses a departure time and an **elapsed duration**: one clock
and a length, never two clocks. `arrive_time` is displayed and never computed
with.

**What this costs.** A leg that states no `depart_time` or no `duration` says
nothing about where its traveler slept, and renders no night even where the
dates leave one -- correctly, because the dates alone cannot tell an overnight
sailing from a morning drive the day after a hotel. An author who wants the
night shown states the journey's duration, which is a string and allowed.

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
| `generator/manifest_parser.py` | `LEG_ITEM_SCHEMA` — the three journey fields |
| `generator/transit_routing.py` | `resolve_authored_leg` — matches a `legs:` entry to the stop it arrives at, exactly as `resolve_leg_mode` does, so the segment and the stamped mode cannot disagree about which leg they describe |
| `generator/partials.py` | Loads a template partial, checksum-verified |
| `templates/partials/transit_spine.html` | The segment's markup template and CSS |
| `generator/transit_spine.py` | Renders one segment; `nights_in_transit` derives nights from the two stops' `dates` |
| `generator/date_span.py` | `span` — the dates, parsed. The authority on length |
| `generator/html_assembler.py` | Emits the partial's CSS once, and a segment per stop |

### 4.1 Why a partial

`templates/` held one monolithic template. A component meant to be drawn by
more than one consumer of this engine cannot live inside it: there is nothing
to include. `templates/partials/` plus `generator/partials.py` is the smallest
thing that makes a component addressable — a named file, loaded and
checksum-verified like the main template, so a consumer draws the same
component rather than its own copy of the idea.

### 4.2 Two ways to share it, and the public surface

§4.1 assumed a second consumer would draw the **partial**. One does not: it
renders its own markup for the same journey, and calls this module for the
arithmetic and the wording instead. That is a legitimate second way to share a
component and the cheaper half of it — the two surfaces can look different and
still never disagree about a fact.

What it needs is a surface to call. Measured 2026-10-23, that consumer was
calling `rail_length_px`, which is public, and `_overnight_sentence`, which was
not. A leading underscore tells a caller the name may be renamed without
notice, so sharing through one is the drift class this component exists to
avoid: a rename here breaks a caller nothing here knows about, nothing in this
repository fails, and the break surfaces on the other consumer's page.

So the sentence was renamed to `overnight_sentence` (2026-10-23), the old name
kept as a shim, and the surface pinned by
`tests/test_a_component_can_be_drawn_by_more_than_one_consumer.py`:

| Published | What it is for |
|---|---|
| `render_segment` | The whole segment, for a consumer drawing the partial |
| `styles` | The partial's CSS, emitted once per page |
| `rail_length_px` | Pixel height for a journey of N minutes |
| `duration_minutes` | An authored duration string, read for scaling |
| `nights_in_transit` | Nights derived from the two stops' `dates` |
| `overnight_sentence` | The wording, so two surfaces cannot word it differently |
| `en_route_lines` | What is on the way, as text |

**The wording is part of the contract, not an implementation detail.** Two
surfaces render that sentence by calling one function precisely so a reader is
told the same thing on both. Changing the string changes what more than one
page says, which is why a test pins the string and not merely the function.

Anything still spelled with an underscore — `_minutes_into_day`, `_marker`,
`_fact` — is genuinely private and may move. A consumer wanting one of those
should ask for it to be published rather than reach for it.

## 5. Open

Not settled. Listed so they are argued rather than inherited.

1. **The spine duplicates the getting-here card's en-route stops.** Both
   render the same places: the spine as a compact list hanging off the rail,
   the card below as linked cards. The follow-up is for the spine to take
   them over, which is not done here because the per-leg Google Maps URL is
   derived from that card's own selection (`visible_stops` in
   `_build_getting_here`) -- moving the stops means moving that derivation,
   which is a change to link behaviour rather than to rendering.

   `stretch_note` went the other way and is deliberately NOT drawn on the
   segment, though it is the author's note about exactly this leg.
   `requirements.md` §3.3 says it renders verbatim on the getting-here card,
   and it still does. Whether it should move to the segment instead is the
   open part; drawing it in both places is the one option that is wrong
   whichever way that is settled.

2. **A rail is at least as long as what is said about the journey.** The
   segment is a two-column grid, so the row is as tall as its taller side.
   On a leg with several en-route stops the body wins and the rail stops
   carrying duration for that segment. Capping the body instead would mean
   hiding content to protect a visual metaphor, which is the wrong trade --
   but it does mean length is a reliable signal only while the bodies are
   short.

3. **What the rail carries per mode beyond an icon.** Today a marker and a
   glyph. A sailing, a train and a drive differ in ways a reader plans around —
   whether there is a cabin, whether you can leave your seat — and none of that
   is on the rail.

4. **Whether an en-route suggestion is a node on the rail or a block beside
   it.** Currently a block beside it: the rail's nodes are stops the itinerary
   is built from, and promoting a suggestion to a node would say the traveler
   is stopping there when the point is that they might. The counter-argument is
   that a thing on the road genuinely is on the road, and a reader scanning the
   rail for "what happens between these two places" will not look to the side.

5. **Whether the length scale should be per-trip rather than fixed.** A
   cruise's legs are all long and a road trip's are all short, so a fixed scale
   makes one guide's rails uniformly tall and another's uniformly flat, which
   is the opposite of carrying duration. Normalizing per trip would make
   lengths comparable within a guide and meaningless across guides.

6. **Whether `see_on_the_way` belongs on a leg after all** (§2.3). The
   argument against is the drift class. The argument for is that
   `en_route_seeds` is name-hints-only by rule, so an author who knows *why* a
   stop is worth it still cannot say so — only discovery can, and it is
   rewritten every run.
