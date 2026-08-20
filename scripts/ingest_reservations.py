"""ingest_reservations.py — poll a mailbox and file confirmation emails into a trip.

Run from the repo root:

    python scripts/ingest_reservations.py --manifest trip_manifest.yaml

Reads UNSEEN messages from the configured IMAP mailbox, extracts booking
details with the configured LLM, matches each to an itinerary stop, and writes
`<manifest_stem>.reservations.yaml` beside the manifest. The generator merges
that sidecar automatically on the next build.

Nothing is written into the manifest itself, and the sidecar is gitignored --
see generator/reservation_ingest.py's module docstring for why.

Setup is documented in docs/design/reservation-email-ingestion.md.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import click
from dotenv import load_dotenv

from generator.manifest_parser import ManifestParser
from generator.reservation_ingest import (
    DEFAULT_MAILBOX,
    DEFAULT_MATCH_THRESHOLD,
    build_sidecar,
    email_to_text,
    extract_reservation,
    fetch_unseen_messages,
    load_sidecar,
    summarize,
    write_sidecar,
)

logger = logging.getLogger("ingest_reservations")


@click.command()
@click.option("--manifest", required=True, type=click.Path(exists=True, dir_okay=False),
              help="Trip manifest the reservations belong to.")
@click.option("--config-path", default="config.yaml", show_default=True,
              type=click.Path(exists=True, dir_okay=False),
              help="Generator config, used for LLM provider/model selection.")
@click.option("--env-file", default=".env", show_default=True,
              help="Env file holding RESERVATION_IMAP_* and the LLM API key.")
@click.option("--mailbox", default=DEFAULT_MAILBOX, show_default=True,
              help="IMAP folder to poll.")
@click.option("--threshold", default=DEFAULT_MATCH_THRESHOLD, show_default=True, type=float,
              help="Minimum match confidence to attach a reservation automatically.")
@click.option("--limit", default=50, show_default=True, type=int,
              help="Maximum messages to process in one run.")
@click.option("--dry-run", is_flag=True,
              help="Extract and match, print the result, write nothing. Leaves "
                   "messages unread so a real run still picks them up.")
@click.option("--log-level", default="info", show_default=True,
              type=click.Choice(["debug", "info", "warning", "error"]))
def main(manifest, config_path, env_file, mailbox, threshold, limit, dry_run, log_level) -> None:
    logging.basicConfig(level=getattr(logging, log_level.upper()), format="%(levelname)s %(message)s")
    load_dotenv(env_file)

    host = os.environ.get("RESERVATION_IMAP_HOST", "")
    user = os.environ.get("RESERVATION_IMAP_USER", "")
    password = os.environ.get("RESERVATION_IMAP_PASSWORD", "")
    if not (host and user and password):
        raise click.ClickException(
            "RESERVATION_IMAP_HOST, RESERVATION_IMAP_USER and RESERVATION_IMAP_PASSWORD "
            f"must all be set (looked in {env_file}). See "
            "docs/design/reservation-email-ingestion.md."
        )

    manifest_path = Path(manifest)
    trip = ManifestParser(config_path=config_path).parse(manifest_path)
    destinations = trip.get("destinations", []) or []
    click.echo(f"Manifest    : {manifest_path.name} ({len(destinations)} destinations)")

    click.echo(f"Mailbox     : {user} @ {host} / {mailbox}")
    messages = fetch_unseen_messages(
        host=host, user=user, password=password, mailbox=mailbox,
        # A dry run must not consume messages -- leaving them unread is what
        # makes it safe to re-run against the same mailbox while tuning.
        mark_seen=not dry_run, limit=limit,
    )
    click.echo(f"Unread mail : {len(messages)}")
    if not messages:
        return

    from generator.llm_client import MultiLLMClient

    llm = MultiLLMClient(config_path=config_path)
    entries = []
    for uid, raw in messages:
        subject, body = email_to_text(raw)
        if not body.strip():
            logger.warning("uid %s: no readable body; skipped", uid)
            continue
        try:
            reservation = extract_reservation(llm, subject, body)
        except Exception as exc:
            # One unparseable email must not abandon the rest of the mailbox --
            # and the message stays flagged read either way, so log loudly.
            logger.error("uid %s (%r): extraction failed: %s", uid, subject[:60], exc)
            continue
        kind = str(reservation.get("kind", "")).lower()
        click.echo(f"  uid {uid}: {kind or 'unrecognized'} — {subject[:70]}")
        entries.append({"source": {"uid": uid, "subject": subject}, "reservation": reservation})

    sidecar_path = ManifestParser.reservations_sidecar_path(manifest_path)
    sidecar = build_sidecar(
        entries, destinations, threshold=threshold, existing=load_sidecar(sidecar_path),
    )

    if dry_run:
        import yaml

        click.echo("\n--- dry run, nothing written ---")
        click.echo(yaml.safe_dump(sidecar, sort_keys=False, allow_unicode=True))
    else:
        write_sidecar(sidecar_path, sidecar)
        click.echo(f"\nWrote {sidecar_path}")

    click.echo(summarize(sidecar))
    pending = sidecar.get("pending", []) or []
    if pending:
        click.echo(
            click.style(
                f"{len(pending)} reservation(s) need a destination assigned before they "
                "will appear in a build.",
                fg="yellow",
            )
        )


if __name__ == "__main__":
    main()
