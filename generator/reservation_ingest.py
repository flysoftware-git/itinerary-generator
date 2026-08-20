"""reservation_ingest.py — turn forwarded confirmation emails into manifest data.

Design notes that are easy to get wrong later:

* **Nothing here writes to the manifest.** Output goes to a sidecar
  (`reservations.yaml`) that `manifest_parser` merges at load. Rewriting
  `trip_manifest.yaml` through PyYAML would strip every comment in it -- that
  file is substantially documentation -- and it would put confirmation numbers
  into a tracked file. The sidecar is gitignored.
* **Matching is advisory.** A reservation that doesn't match a destination
  confidently lands in `pending` for a human, rather than attaching to the
  best guess. A hotel filed under the wrong stop is worse than one filed
  nowhere, because nothing downstream will ever flag it.
* **Extraction is LLM-based** via the existing MultiLLMClient rather than
  per-vendor parsers. Confirmation email formats are effectively unbounded and
  change without notice; there is no fixed grammar to target.
* **Everything here is untrusted input.** Email content is attacker-supplied in
  the general case -- anyone can send mail to the ingest address. Extracted
  strings are escaped at render time (html_assembler) and re-validated against
  the manifest schema after merge, so a malformed or hostile extraction fails
  the build rather than reaching the page.
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Below this, a reservation goes to `pending` instead of attaching.
DEFAULT_MATCH_THRESHOLD = 0.45

#: Mailbox folder polled by default.
DEFAULT_MAILBOX = "INBOX"

_MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)

EXTRACTION_SYSTEM_PROMPT = """\
You extract travel booking details from forwarded confirmation emails.

Return STRICT JSON with this shape:
{
  "kind": "lodging" | "transportation" | "none",
  "type": "plane" | "train" | "car" | "other",
  "provider": string,
  "label": string,
  "confirmation_number": string,
  "depart": string,
  "arrive": string,
  "website": string,
  "location": string,
  "checkin_time": string,
  "dates": string,
  "city": string
}

Rules:
- "kind": "lodging" for hotel/rental-property stays; "transportation" for
  flights, trains and rental cars; "none" if the email is not a booking
  confirmation at all (newsletters, receipts for something else, spam).
- "type" is only meaningful when kind is "transportation". Use "other" for a
  transportation booking that is none of plane/train/car.
- "city" must be the destination city or park the booking is FOR, as plainly
  as possible (e.g. "Springdale, UT"), since it is used to match the booking
  to an itinerary stop.
- Use "" for anything the email does not state. NEVER invent a confirmation
  number, price, date or URL. An empty string is always better than a guess.
- Return only the JSON object, no prose.
"""


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def email_to_text(raw_bytes: bytes, *, max_chars: int = 12000) -> tuple[str, str]:
    """Return (subject, best-effort plain-text body) for a raw RFC822 message.

    Prefers text/plain; falls back to de-tagged text/html, since a great many
    confirmation emails ship HTML only. Truncated because the body is going
    into an LLM prompt and confirmation emails carry long marketing tails that
    add tokens without adding facts.
    """
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
    subject = str(msg.get("Subject", "") or "")

    plain_parts: list[str] = []
    html_parts: list[str] = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart":
            continue
        try:
            content = part.get_content()
        except Exception:  # undecodable part -- skip rather than fail the message
            continue
        if not isinstance(content, str):
            continue
        if part.get_content_type() == "text/plain":
            plain_parts.append(content)
        elif part.get_content_type() == "text/html":
            html_parts.append(content)

    body = "\n".join(plain_parts).strip() or _strip_html("\n".join(html_parts))
    return subject, body[:max_chars]


def _normalize_tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) > 2}


def _month_day_tokens(text: str) -> set[str]:
    """Month names and day numbers mentioned in a free-text date string.

    Deliberately crude. `dates` in the manifest is free text ("October 7-9,
    2026") and so is whatever the email said, and no scheduling code consumes
    either -- this only has to be good enough to rank candidate stops, with a
    human confirming anything uncertain.
    """
    lowered = (text or "").lower()
    tokens = {m for m in _MONTHS if m in lowered}
    tokens |= {d for d in re.findall(r"\b([0-3]?\d)\b", lowered)}
    return tokens


def score_destination_match(reservation: dict[str, Any], dest: dict[str, Any]) -> float:
    """Confidence in [0,1] that this reservation belongs to this destination.

    Blends three weak signals rather than trusting any one: the city the
    extractor read off the email, overlap with the destination's own
    name/lodging location, and month/day overlap with its `dates` string. Weak
    on purpose -- it decides only whether a human needs to look, and the
    threshold is what turns it into a decision.
    """
    dest_tokens = _normalize_tokens(dest.get("name", ""))
    lodging = dest.get("lodging") if isinstance(dest.get("lodging"), dict) else {}
    dest_tokens |= _normalize_tokens(str(lodging.get("location", "") or ""))

    res_tokens = _normalize_tokens(
        " ".join(
            str(reservation.get(k, "") or "")
            for k in ("city", "location", "label", "arrive", "depart", "provider")
        )
    )

    # Normalize by the SMALLER side. Dividing by the destination's token count
    # punished stops with wordy lodging addresses: a flight stating only
    # "Springdale, UT" matched 1 of Zion's 5 tokens and scored 0.2, landing a
    # correct, unambiguous match in the review queue. What matters is that the
    # tokens the reservation does carry point at this stop, not that it failed
    # to restate the whole address.
    name_overlap = len(dest_tokens & res_tokens) / max(1, min(len(dest_tokens), len(res_tokens)))

    dest_dates = _month_day_tokens(str(dest.get("dates", "") or ""))
    res_dates = _month_day_tokens(str(reservation.get("dates", "") or ""))
    date_overlap = (
        len(dest_dates & res_dates) / max(1, len(dest_dates)) if dest_dates and res_dates else 0.0
    )

    # Name agreement dominates: dates alone match far too many stops on a trip
    # whose destinations sit days apart within one month.
    return round(min(1.0, (0.75 * name_overlap) + (0.25 * date_overlap)), 4)


def match_destination(
    reservation: dict[str, Any],
    destinations: list[dict[str, Any]],
    *,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Return (destination id or None, ranked candidates).

    None means "a human should decide" -- either nothing scored above the
    threshold, or the top two scored close enough together that picking one
    would be arbitrary.
    """
    ranked = sorted(
        (
            {"id": str(d.get("id", "")), "score": score_destination_match(reservation, d)}
            for d in destinations
            if isinstance(d, dict) and d.get("id")
        ),
        key=lambda c: c["score"],
        reverse=True,
    )
    if not ranked or ranked[0]["score"] < threshold:
        return None, ranked
    if len(ranked) > 1 and (ranked[0]["score"] - ranked[1]["score"]) < 0.10:
        # Two plausible stops: a near-tie is exactly the case where an
        # automatic pick silently files a booking under the wrong one.
        return None, ranked
    return ranked[0]["id"], ranked


def reservation_to_manifest_fragment(reservation: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Convert an extraction into the manifest shape it belongs in.

    Returns ("lodging"|"transportation", fragment). Empty strings are dropped
    so a partial extraction fills only what it actually knows and leaves the
    rest for the manifest's own values.
    """
    def _clean(keys: tuple[str, ...]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in keys:
            value = str(reservation.get(key, "") or "").strip()
            if value:
                out[key] = value
        return out

    if str(reservation.get("kind", "")).lower() == "lodging":
        return "lodging", _clean(("name", "location", "checkin_time", "confirmation_number", "website"))

    fragment = _clean(("provider", "label", "confirmation_number", "depart", "arrive", "website"))
    kind = str(reservation.get("type", "") or "").strip().lower()
    fragment["type"] = kind if kind in {"plane", "train", "car", "other"} else "other"
    return "transportation", fragment


def fetch_unseen_messages(
    *,
    host: str,
    user: str,
    password: str,
    mailbox: str = DEFAULT_MAILBOX,
    mark_seen: bool = True,
    limit: int = 50,
) -> list[tuple[str, bytes]]:
    """Return [(uid, raw_rfc822)] for UNSEEN messages in `mailbox`.

    Uses stdlib imaplib over implicit TLS. `mark_seen` is what makes repeat
    runs cheap and idempotent: an ingested message is not re-extracted (and
    not re-billed as LLM tokens) on the next poll. Pass False when testing
    against a real mailbox you don't want to disturb.
    """
    messages: list[tuple[str, bytes]] = []
    conn = imaplib.IMAP4_SSL(host)
    try:
        conn.login(user, password)
        conn.select(mailbox)
        status, data = conn.search(None, "UNSEEN")
        if status != "OK":
            logger.warning("IMAP search failed in mailbox %s: %s", mailbox, status)
            return []
        uids = (data[0] or b"").split()[:limit]
        for uid in uids:
            # BODY.PEEK leaves \Seen alone so a crash mid-run doesn't silently
            # consume messages that were never actually ingested.
            status, fetched = conn.fetch(uid, "(BODY.PEEK[])")
            if status != "OK" or not fetched or not isinstance(fetched[0], tuple):
                logger.warning("IMAP fetch failed for uid %s", uid)
                continue
            messages.append((uid.decode("ascii", "replace"), fetched[0][1]))
            if mark_seen:
                conn.store(uid, "+FLAGS", "\\Seen")
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return messages


def extract_reservation(llm_client: Any, subject: str, body: str) -> dict[str, Any]:
    """Ask the configured LLM to turn one email into a reservation dict."""
    result = llm_client.generate_json(
        system_prompt=EXTRACTION_SYSTEM_PROMPT,
        user_prompt=f"Subject: {subject}\n\n{body}",
        operation="reservation_extraction",
    )
    return result if isinstance(result, dict) else {}


def build_sidecar(
    entries: list[dict[str, Any]],
    destinations: list[dict[str, Any]],
    *,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fold extracted reservations into a sidecar document.

    Merges onto `existing` so a later poll adds to what earlier polls found
    rather than replacing it. Deduplicates on confirmation number, which is
    what makes re-ingesting the same forwarded email harmless.
    """
    sidecar: dict[str, Any] = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "destinations": dict((existing or {}).get("destinations", {}) or {}),
        "pending": list((existing or {}).get("pending", []) or []),
    }

    seen_confirmations = {
        str(leg.get("confirmation_number", "") or "")
        for block in sidecar["destinations"].values()
        if isinstance(block, dict)
        for leg in (block.get("transportation") or [])
        if isinstance(leg, dict)
    }
    seen_confirmations |= {
        str((block.get("lodging") or {}).get("confirmation_number", "") or "")
        for block in sidecar["destinations"].values()
        if isinstance(block, dict)
    }
    seen_confirmations.discard("")

    for entry in entries:
        reservation = entry.get("reservation") or {}
        if str(reservation.get("kind", "")).lower() == "none":
            continue

        confirmation = str(reservation.get("confirmation_number", "") or "").strip()
        if confirmation and confirmation in seen_confirmations:
            logger.info("Skipping already-ingested confirmation %s", confirmation)
            continue

        dest_id, candidates = match_destination(reservation, destinations, threshold=threshold)
        if not dest_id:
            sidecar["pending"].append(
                {
                    "reason": "no confident destination match",
                    "source": entry.get("source", {}),
                    "reservation": reservation,
                    "candidates": candidates[:3],
                }
            )
            continue

        section, fragment = reservation_to_manifest_fragment(reservation)
        block = sidecar["destinations"].setdefault(dest_id, {})
        if section == "lodging":
            block.setdefault("lodging", {}).update(fragment)
        else:
            block.setdefault("transportation", []).append(fragment)
        if confirmation:
            seen_confirmations.add(confirmation)

    return sidecar


def merge_sidecar_into_trip(trip: dict[str, Any], sidecar: dict[str, Any]) -> dict[str, int]:
    """Apply a sidecar to a parsed manifest in place.

    Ingested values FILL rather than overwrite: a field the manifest already
    states wins, because that value was written deliberately by a human and
    this one was read out of an email by a language model. Transportation legs
    append, deduplicated by confirmation number.
    """
    counts = {"lodging_fields": 0, "transportation_legs": 0}
    by_id = {
        str(d.get("id", "")): d
        for d in trip.get("destinations", []) or []
        if isinstance(d, dict)
    }

    for dest_id, block in (sidecar.get("destinations", {}) or {}).items():
        dest = by_id.get(str(dest_id))
        if dest is None or not isinstance(block, dict):
            logger.warning("Sidecar references unknown destination id %r; skipped", dest_id)
            continue

        lodging_fragment = block.get("lodging")
        if isinstance(lodging_fragment, dict) and lodging_fragment:
            lodging = dest.setdefault("lodging", {})
            for key, value in lodging_fragment.items():
                if not str(lodging.get(key, "") or "").strip():
                    lodging[key] = value
                    counts["lodging_fields"] += 1

        legs = block.get("transportation")
        if isinstance(legs, list) and legs:
            existing_legs = dest.setdefault("transportation", [])
            known = {
                str(leg.get("confirmation_number", "") or "")
                for leg in existing_legs
                if isinstance(leg, dict)
            }
            known.discard("")
            for leg in legs:
                if not isinstance(leg, dict):
                    continue
                confirmation = str(leg.get("confirmation_number", "") or "").strip()
                if confirmation and confirmation in known:
                    continue
                existing_legs.append(leg)
                counts["transportation_legs"] += 1
                if confirmation:
                    known.add(confirmation)

    return counts


def load_sidecar(path: Path | str) -> dict[str, Any]:
    import yaml

    path = Path(path)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def write_sidecar(path: Path | str, sidecar: dict[str, Any]) -> None:
    import yaml

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# reservations.yaml -- generated by scripts/ingest_reservations.py\n"
        "# Contains confirmation numbers and property names. Keep it out of\n"
        "# version control; the manifest merges it at load time.\n"
        "# 'pending' entries matched no destination confidently -- move each\n"
        "# under the right destination id, or delete it.\n"
    )
    path.write_text(
        header + yaml.safe_dump(sidecar, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def summarize(sidecar: dict[str, Any]) -> str:
    attached = sum(
        len(block.get("transportation") or []) + (1 if block.get("lodging") else 0)
        for block in (sidecar.get("destinations", {}) or {}).values()
        if isinstance(block, dict)
    )
    pending = len(sidecar.get("pending", []) or [])
    return f"{attached} reservation(s) attached, {pending} awaiting review"


__all__ = [
    "DEFAULT_MAILBOX",
    "DEFAULT_MATCH_THRESHOLD",
    "EXTRACTION_SYSTEM_PROMPT",
    "build_sidecar",
    "email_to_text",
    "extract_reservation",
    "fetch_unseen_messages",
    "load_sidecar",
    "match_destination",
    "merge_sidecar_into_trip",
    "reservation_to_manifest_fragment",
    "score_destination_match",
    "summarize",
    "write_sidecar",
]
