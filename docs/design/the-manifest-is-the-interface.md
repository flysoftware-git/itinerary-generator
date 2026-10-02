# The Manifest Is The Interface

Status: owner decision, 2026-10-01. The schema half is merged (#189, `seeds` may
be `{name, url}`); §3's two steps are not built.

**Scope.** This engine renders a trip from a manifest. It does not plan one, it
does not elaborate one, and it does not import or emit one. Anything about
taking a manifest in, working on the trip, and writing a manifest back out
belongs to the system that plans trips, and is that project's to design, own and
document.

What is recorded here is this repository's share: a decision the owner approved,
and what this engine owes the format.

---

## 1. The decision

`seeds` may be a bare name or `{name, url}` (#189), and the owner has approved
**full implementation** — which means a consumer. #189 shipped the field saying
so plainly:

> *"Nothing here makes URL discovery prefer the supplied link. The field is
> parsed, validated and exposed, and no caller reads it yet."*

That was offered as a deliberate cost with the follow-up left open. The
follow-up is approved.

**Why the schema had to widen.** A manifest that cannot hold a fact cannot carry
it. Before #189 a seed was a bare string, so an author who knew which page a hint
meant had nowhere to say it, and the knowledge was discarded at the door.
Discovery then guessed from the name — a trail with a common name, a creek that
shares its name with a hamlet two hundred kilometres away — and nothing recorded
that the author had ever had an opinion. No care elsewhere could recover a fact
no field held.

## 2. What this engine owes the format

1. **`requirements.md` §3 is the interface, not documentation.** It is the
   format this engine is sent. That is what makes #183's guard — every schema
   field named in §3, enforced by
   `tests/test_the_manifest_reference_names_every_field.py` — load-bearing
   rather than tidy. A field this engine accepts and never describes is a field
   the sender cannot know to send.

2. **It renders rather than elaborates.** The owner, 2026-10-01: *"generator
   doesn't need to manipulate or elaborate that information, it just needs to
   render it, and the requested format is the manifest as described in the
   generator's requirements document."* So the whole obligation is **not to
   lose** what it was handed.

3. **Content is in scope at every stage that could drop it.** One `trip` is
   threaded through every stage as its argument — `generate_all(trip)`,
   `discover(trip)`, `build_entity_registry(trip)`,
   `reconcile_trip_from_registry(trip, registry)`, `assemble(trip)` — and each
   stage records its reasoning rather than only its result (`_log_decision`,
   plus `destination_status_report.json`, `url_diff_report.json`,
   `validation_report.json`, `run_ledger.jsonl`). So "the author's page was not
   used" is answerable from a build's own artifacts.

   The caveat is why §3.2 is worth building: it is one *mutable* dict, so a
   stage that drops a key leaves nothing behind saying it was ever there, and
   the decision trail only covers decisions a stage knew it was making.

## 3. The two steps

Both independently reviewable, both leaving the tree green.

### 3.1 Consume `seed_links`

URL discovery prefers the author's page for a seed that named one, and records
that it did.

The risk is concentrated here: link selection has several assignment sites, some
of which exist to beat an incumbent link *on purpose* — a TripAdvisor row
upgraded to an official site, a remembered direct-batch row preferred over a
search result. The author's own page has to win against those, and the decision
has to appear in the item's trail rather than silently, or the next person
debugging a link finds a page nothing explains.

### 3.2 A guard that nothing handed in is quietly ignored

For every field §3 of `requirements.md` accepts, either it reaches the rendered
guide, or this repository says in one place that it deliberately does not render
it.

That list is the honest form of *"it just needs to render it"*, and **nobody can
state it today**. `seed_links` is the proof: parsed, validated, and read by
nothing, with no test noticing — because no test asks the question. A schema
walk can ask it for every field at once, the same walk #183's guard already
does.

Ordering: 3.1 before 3.2, or the list 3.2 produces would record `seed_links` as
"deliberately not rendered", which is the opposite of the decision in §1.

## 4. Two facts about this parser that its callers need

Stated as parser behaviour, because anything reading trip content back out will
meet them.

**`parse()` mutates what it returns, in exactly three places.**
`_normalize_seeds` rewrites `seeds` to plain names and lifts any page a seed
named into `seed_links` beside it. `_resolve_brand_icon` and
`_resolve_brand_touch_icon` replace an authored `icon: assets/logo.svg` with an
inlined `data:image/svg+xml,…` URI. `_merge_reservations_sidecar` merges the
sidecar in and records counts under `_meta`, which is bookkeeping rather than
content. Every other `_validate_*` step reads without mutating, so `legs`, the
travel-mode fields and the rest arrive as authored.

**The authored brand-icon value is not retained.** After parsing, nothing holds
the path the author wrote — only the data URI that replaced it. Any caller that
needs to know what the manifest said, rather than what the parser made of it,
has to be given it: the resolver would need to record the value it replaced.
Worth knowing before someone assumes the parsed trip is a faithful copy of its
input, because for this one field it is not.

## 5. Reservations are content

The schema accepts booked travel, and a manifest carrying it is carrying
content, not an attachment. The owner, 2026-10-01, on an earlier draft that
argued otherwise:

> *"the security issue is not relevant as suppression of content already
> provided, at generator via -private or upstream by planner, is entirely
> dependent upon security of generated manifests themselves, which should remain
> as an asset of potential long-term value for the endeavor."*

The draft had confused ingestion hygiene with the format: the reservations
sidecar exists so that *ingested confirmation emails* do not land in a file an
author is about to commit, which is a rule about where content arrives. It says
nothing about whether bookings are part of a manifest. They are.

What this engine owes that fact is one rule, and it is about direction:
**privacy redaction is a rendering step and must not be mistaken for the
content.** `main._apply_privacy_redaction` blanks lodging names and drops
transportation from the trip that becomes the published page, under
`--environment prod`. Anything reading trip content for a purpose other than
rendering must read the parsed manifest, never the redacted trip, or it will
record that the author never named their lodging — false rather than protected.
