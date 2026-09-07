# Access notes

**`trip.access_notes`** — what a generated guide says about *getting in*.

Off unless a manifest asks for it. When absent, nothing changes: no sentence is
added and the guide renders exactly as it did before.

---

## 1. The need

A traveller with a mobility need — or travelling with someone who has one —
plans differently. The questions are practical and they come *before* the trip:
how far is it on foot from the car park, is that path paved or loose, is there
step-free entry, is there an accessible toilet, can a wheelchair be hired.

The guide is already the artifact that answers practical questions per
destination. It had nothing to say about this one.

## 2. What the flag turns on

For every place the guide names, the destination prompt asks for three things:

1. **The foot approach, measured**, where the only way in is on foot: how far
   from parking or the nearest transit stop, and what the surface and gradient
   are like.
2. **The documented facilities**: step-free entry, accessible parking,
   accessible toilets, hire of mobility aids.
3. **The words "access not documented"** where that is the truth.

## 3. The third one is the point

**The failure mode of this feature is not omission. It is confident
invention.**

A model asked whether a place is accessible will readily answer *"yes,
wheelchair accessible"* on the strength of the name alone — it is the kind of
sentence that appears in travel copy everywhere, and it is exactly the sentence
a reader with a mobility need will act on. That answer is worse than saying
nothing, and the asymmetry is the whole argument:

| What the guide says | What it costs when wrong |
|---|---|
| "access not documented" | a phone call before setting out |
| "wheelchair accessible" | discovered at the door, after the journey |

A reader can plan around *unknown*. They can ring ahead, choose another day,
bring someone, or pick a different place. They cannot plan around a wrong yes,
because they only learn it is wrong once the journey is already spent.

So the guidance is mostly prohibitions: **never** infer accessibility from the
type of place, its age or its operator; do **not** claim a place is accessible
unless a source says so; and tell the reader that the venue's own information
is the one to trust before travelling. Nothing generated here is a substitute
for that, and the text says so rather than leaving it to be assumed.

`tests/test_access_notes.py::test_the_honesty_rule_is_the_load_bearing_half`
guards those clauses by name. They are the ones most likely to be trimmed by
somebody shortening a long prompt, and the ones whose absence turns a helpful
guide into a misleading one.

## 4. Why a flag rather than always on

Two reasons, and the second is the stronger.

Prompt budget is real: every destination call carries this text, and a guide
nobody asked it for pays for it on every destination.

More importantly, **an access section that appears unbidden on every guide
trains readers to skip it.** The traveller who needs it is best served by a
guide that carries it deliberately, and worst served by a paragraph of hedged
boilerplate that everybody has learned to scroll past.

## 5. What this is not

Not a verification. Nothing in this project checks a building for a ramp, and
the flag does not claim otherwise — it changes what the guide is asked to
report and how honestly it must report the gaps. A structured
`wheelchair_accessible: true` field would be a confident value derived from
nothing, which is the failure §3 exists to prevent, expressed as a schema.
