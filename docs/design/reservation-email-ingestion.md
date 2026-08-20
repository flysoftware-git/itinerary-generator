# Reservation Email Ingestion

How forwarded hotel, flight, train and rental-car confirmations become manifest
data — and why almost every decision here is biased toward *not* attaching
something rather than attaching it wrongly.

> **Read first:** [`design.md`](../design.md) §1.1 (the manifest holds only what
> the human provides) · §2.6 (honest fallback as a product surface)
>
> **Related:** [`html-assembly-pipeline.md`](html-assembly-pipeline.md) (how
> booking cards and chips render)

---

## 1. Shape

Ingestion is a **separate command**, never part of a build:

```bash
python scripts/ingest_reservations.py --manifest <path> --env-file <path> [--dry-run]
```

It polls one IMAP mailbox, extracts bookings with the configured LLM, matches
each to the trip, and writes `<manifest_stem>.reservations.yaml` beside the
manifest. `manifest_parser` merges that sidecar at load, then **re-validates the
merged result against the schema**.

A build never reads a mailbox. That is deliberate: a build that silently depends
on reachable mail, and spends LLM calls per message, fails for reasons that have
nothing to do with the trip. It is also one-way — it flags mail read and files
it — and one-way steps do not belong inside something you re-run freely.

**With no sidecar the build is still valid.** It simply renders no booking cards
and no trip-wide travel chips. That is invisible in the output, so the runner
reports sidecar presence before generating and the merge counts after.

---

## 2. Why a sidecar and not the manifest

- **PyYAML cannot preserve comments.** A load-and-dump round trip deletes every
  one — a quarter of `trip_manifest.yaml` is comments, and they *are* the schema
  documentation.
- **Provenance.** The sidecar makes "your value wins over the model's"
  enforceable and visible. Merged in place, nobody could later tell which
  check-in time was typed by a human and which was read off an email by an LLM.
- **The pending queue needs somewhere to live.** An unplaceable booking has no
  destination block to sit in, by definition.
- **Reversibility.** Delete the sidecar and the manifest is untouched.
- **Secrets.** Confirmation numbers stay out of any tracked file; the sidecar is
  gitignored (`*.reservations.yaml`).

---

## 3. Matching

Deliberately crude, with a review queue. It ranks candidate stops; the
thresholds turn a ranking into a decision.

**Signals**, blended 75/25:

- **Name overlap** between the booking's city/location/label/provider and the
  destination's name plus `lodging.location`
- **Month and day overlap** with the destination's free-text `dates`

Three refinements exist because the naive version got real bookings wrong:

**Normalize by the smaller side, with a floor of 3.** Dividing by the
destination's token count punished stops with wordy addresses. But a bare `min()`
let a *single* token carry a sparse booking: "Kanab, Utah" matched "St. George,
Utah" on the lone word `utah`, scored 0.5, and attached — so any thinly-extracted
Utah booking would land on whichever Utah stop came first. The floor means one
token can never clear the bar alone.

**Generic facility words are dropped.** `airport`, `international`, `hotel`,
`resort` and similar appear in nearly every travel address and identify nothing.
A gateway name reduces to very few tokens, so one shared generic word was worth
half the score — it pulled a Las Vegas car pickup toward the Albuquerque gateway.

**Endpoints are scored separately, best one wins.** A journey has two ends and
usually only one is on the trip. Pooling both let the off-trip end mismatch:
a rental collected at the departure gateway and returned at the return gateway
attached to the *last* stop. Fixing that by scoring flights on arrival alone then
broke the outbound flight home, which departs the return gateway and arrives
somewhere not on the itinerary at all. Exception: for a **car**, an extracted
pickup is the only end that counts — a rental belongs where it is collected. That
exception keys on whether the reservation *has* a pickup, not on whether the
pickup matches the stop being scored; the latter made every non-matching stop
fall back to the drop-off.

### 3.1 Gateways

`trip.departure` and `trip.return` are real places people book travel to and
from, but they are not itinerary stops — so nothing in `destinations` could ever
match a flight into them. The inbound and outbound flights, the two most likely
things anyone forwards, always went to review.

Gateway matches are **trip-wide**, resolving to a `TRIP_LEVEL_ID` sentinel rather
than to a destination. They render under the route overview map, because a flight
that lands at the gateway and a rental that spans every stop do not belong to any
one of them. A leg tied to a specific locale mid-trip still attaches to that
destination normally.

Lodging that somehow matches a gateway goes to review instead: a hotel is always
*at* a place, so that is a mismatch a human should see, not a trip-level "stay".

### 3.2 Three outcomes, not two

| Best score | Outcome | Sidecar | Mailbox |
|---|---|---|---|
| ≥ threshold, clear winner | **attached** | destination or `trip:` block | filed |
| ≥ `UNRELATED_SCORE_FLOOR`, below threshold or a near-tie | **pending** | `pending:` list | filed |
| < `UNRELATED_SCORE_FLOOR` | **unrelated** | *nothing* | **left in inbox, unread** |

The third row exists because people forward confirmations *as they book*, so mail
arrives for trips whose manifest does not exist yet. Matched against whatever
manifest happens to be running, those score ~0. Treated as ordinary
review items they were buried in the wrong trip's pending list and — once
archiving existed — moved out of the inbox, so the manifest they belonged to
could never find them.

The separation is wide and stable: measured against the real Southwest manifest,
a Split hotel and a Tokyo hotel both score **0.0**, while Kanab UT — a genuinely
SW-adjacent town — scores **0.25**.

**A near-tie also defers.** Two stops within 0.10 of each other means picking one
would be arbitrary, and nothing downstream ever flags a hotel filed under the
wrong stop.

---

## 4. Merge

- **Fill, never overwrite.** A field the manifest already states wins. A human
  wrote that; a model read this one off an email.
- **Legs append, deduplicated by confirmation number**, so re-forwarding the same
  email is harmless.
- **Re-validated against the schema after merging.** LLM-extracted content is
  untrusted, so a malformed or hostile extraction fails the build loudly rather
  than reaching the page.
- Unknown destination ids are logged and skipped — renaming a stop must not crash
  a build.

---

## 5. The mailbox

**Filing is a second pass, not part of fetching.** `fetch_unseen_messages` only
peeks (`BODY.PEEK`, no flag changes); `mark_messages_processed` files exactly the
uids the caller says it handled. Only matching knows which messages deserve
filing, and marking during the fetch consumed every message before anything knew.

Handled messages are flagged `\Seen` and moved to an archive folder. Filing is
**best-effort and never fatal** — the booking is already extracted by then, so a
permissions problem must not fail the run. `MOVE` (RFC 6851) is not universal, so
it falls back to `COPY` + `\Deleted`, with **deliberately no `EXPUNGE`**: that
would destroy mail permanently, and a silently-failed copy would take the
original with it. Nothing is ever deleted, so even a misclassified booking stays
recoverable in the archive folder.

Non-bookings (security alerts, newsletters) are filed too — no future manifest
will want them, and leaving them would make the queue accumulate noise.

`--dry-run` extracts, matches and prints, but writes nothing, marks nothing, and
files nothing, so it is safe to repeat while tuning `--threshold`.

### 5.1 Credentials

Three env vars — `RESERVATION_IMAP_HOST` / `USER` / `PASSWORD`. Gmail answers
every credential problem with the same opaque `[AUTHENTICATIONFAILED] Invalid
credentials`, so login failures are diagnosed from the values' *shape* and the
specific misconfiguration named. The two that actually occur:

- A username with no `@` — Gmail requires the full address, not the mailbox name
- An account password rather than a 16-character app password. Google removed
  less-secure-app access in 2022, so basic-auth IMAP requires an app password,
  which in turn requires 2-Step Verification enabled. With 2SV off, the
  app-password page reports the setting as *unavailable* rather than explaining
  why.

App passwords are displayed as four space-separated groups; whitespace is
stripped for providers whose app passwords are defined as space-free (Google,
iCloud) but **not** universally, since another provider could legitimately allow
a space in a password.

---

## 6. Security posture

This is the component where a bug is a privacy incident rather than a quality
defect, and it is treated accordingly.

- **Email content is untrusted.** Anyone can send mail to an ingest address.
  Extracted values are HTML-escaped at render and re-validated against the schema
  after merge.
- **Bookings redact out of published builds.** `lodging.name`, `.website`,
  `.confirmation_number` and every `transportation` leg — trip-wide and
  per-destination — are cleared in privacy-redacted builds.
  `lodging.location` and `.checkin_time` are deliberately *kept*: they drive
  geocoding, routing and arrival-day scheduling, so redacting them would degrade
  itinerary content rather than protect anything the name does not already.
- **The sidecar is gitignored**, and so are non-prod build directories — a dev or
  eval `index.html` renders every booking detail verbatim.

**Known limits.** Schema re-validation constrains the *shape* of what lands and
escaping constrains rendering, but neither prevents a plausible-looking *wrong*
address produced by a hostile or confused extraction. Whether a human review step
is required before a customer-facing build is an open product question, not a
solved one.

---

## 7. Non-goals

- **No mailbox writes beyond flagging and filing.** Never deletes, never expunges.
- **No multi-tenant routing.** One mailbox, one manifest per invocation. Mapping
  a forwarding sender to a trip is a different problem with a different threat
  model — a `From` header is not a credential.
- **No booking, cancellation or price tracking.** Read-only.
- **No structured date parsing.** `depart`/`arrive`/`dates` stay display strings;
  nothing downstream parses them, and matching only needs month/day tokens.
