# Reservation Email Ingestion

Forward a hotel, flight, train or rental-car confirmation to a dedicated
mailbox; the next build shows it on the right destination as a collapsed
booking card.

Nothing here runs during a normal build. `scripts/ingest_reservations.py` is a
separate command you run when you have new confirmations; the generator only
ever reads the sidecar file it produces.

---

## Setup

### 1. Create a dedicated mailbox

Use a purpose-made address, not your personal inbox. The credential below
grants full read access to whatever mailbox it points at, and anyone who
learns the address can send mail into your ingestion pipeline.

### 2. Generate an app password

Never use your account password.

| Provider | IMAP host | App password |
|---|---|---|
| Gmail | `imap.gmail.com` | <https://myaccount.google.com/apppasswords> (needs 2FA on) |
| Fastmail | `imap.fastmail.com` | Settings → Privacy & Security → App Passwords |
| iCloud | `imap.mail.me.com` | <https://account.apple.com> → Sign-In & Security |

### 3. Add credentials to `.env`

```
RESERVATION_IMAP_HOST=imap.gmail.com
RESERVATION_IMAP_USER=trips@example.com
RESERVATION_IMAP_PASSWORD=your-app-password-here
```

`.env` is gitignored. `.env.example` documents these without values.

### 4. Forward some confirmations, then run it

```bash
python scripts/ingest_reservations.py --manifest trip_manifest.yaml --dry-run
```

`--dry-run` extracts, matches and prints, but writes nothing and leaves the
messages unread — so you can tune `--threshold` and re-run against the same
mailbox as often as you like. Drop the flag when the output looks right.

---

## What happens to a message

1. **Fetch** — unread messages are pulled over IMAP with `BODY.PEEK`, so a
   crash mid-run doesn't silently consume mail that was never ingested. They
   are flagged read only on a real (non-dry) run, which is what keeps repeat
   polls cheap and idempotent.
2. **Extract** — the body (plain text, or HTML with tags stripped) goes to the
   configured LLM, which returns a structured booking. It is instructed to
   emit `""` rather than guess: a missing confirmation number is recoverable,
   an invented one is not.
3. **Match** — the booking is scored against each destination on city/name
   overlap and month-day overlap with the stop's `dates`. Name agreement is
   weighted 3:1 over dates, because dates alone match far too many stops on a
   trip whose destinations sit days apart within one month.
4. **File** — a confident match attaches; anything else goes to `pending`.
5. **Merge** — the next build folds the sidecar into the manifest.

## The sidecar

Written to `<manifest_stem>.reservations.yaml` beside the manifest —
`sw_manifest.yaml` → `sw_manifest.reservations.yaml`, so several trips can
share a directory without bleeding into each other.

```yaml
destinations:
  zion:
    lodging:
      name: Zion Lodge
      confirmation_number: ZL-4471902
    transportation:
      - type: plane
        provider: United Airlines
        confirmation_number: XR7Q2M
pending:
  - reason: no confident destination match
    source: {uid: "1841", subject: "Your reservation is confirmed"}
    reservation: {kind: lodging, city: "Torrey, UT", confirmation_number: CR-88}
    candidates: [{id: capitolreef, score: 0.41}, {id: moab, score: 0.12}]
```

To resolve a `pending` entry, move its `reservation` block under the right
destination id and delete the entry. Nothing in `pending` reaches a build.

**The sidecar is gitignored** (`*.reservations.yaml`). It holds confirmation
numbers and property names. It is deliberately *not* written into
`trip_manifest.yaml`: a PyYAML round-trip would strip every comment from that
file, which is substantially documentation, and it would put booking details
into a tracked file.

---

## Design decisions worth keeping

**Uncertain matches wait for a human.** A booking that scores below the
threshold, or ties with a second destination inside 0.10, goes to `pending`
rather than attaching to the best guess. A hotel filed under the wrong stop is
worse than one filed nowhere, because nothing downstream will ever flag it.
Raise `--threshold` to send more to review; lower it to attach more
automatically.

**Ingested values fill, never overwrite.** If the manifest already states a
lodging name, the sidecar's is ignored. A human wrote one; a language model
read the other out of an email. Transportation legs append, deduplicated by
confirmation number, so re-forwarding an email is harmless.

**Email content is untrusted.** Anyone can mail the ingest address. Extracted
values are re-validated against the manifest schema after merge, so a
malformed or hostile extraction fails the build loudly instead of reaching the
page; and every field is HTML-escaped at render.

**Extraction is LLM-based, not per-vendor parsers.** Confirmation formats are
effectively unbounded and change without notice. This costs tokens per message
— but only once per message, since ingested mail is flagged read.

---

## Privacy

Everything ingested here is redacted out of privacy-redacted builds — `prod` by
default, see `main._resolve_privacy_redaction`:

| Field | dev / test build | prod build |
|---|---|---|
| `lodging.name`, `.website`, `.confirmation_number` | shown | blank; card disappears |
| `transportation` (all legs) | shown | list emptied; cards disappear |
| `lodging.location`, `.checkin_time` | shown | **kept** — drives geocoding, routing and arrival-day scheduling |

The cards degrade to *absence* rather than to a greyed placeholder, unlike
`planning_links`. A visible "Lodging" affordance would still announce that the
traveler has a room booked at this stop on these dates, which is most of what
redaction is protecting.

So: hand a travel companion the `dev` build folder and they get every booking
detail with no login. The published `prod` copy carries none of it.
