"""Regenerate templates/checksums.txt from the template files on disk.

Run from the repo root:
    python scripts/regenerate_template_checksums.py

WHY A SCRIPT
------------
`templates/checksums.txt` is verified at run time, and `html_assembler` fails
the whole build on a mismatch. Hand-editing it is therefore the one way to
satisfy that check without having looked at what changed: paste the digest the
error message printed and the guard is quiet again. The digest it prints is the
file's ACTUAL digest, so pasting it always "works" -- which makes the error
message a worse thing to copy from than nothing.

This computes the rows instead, and prints what moved so the change is seen.

It does NOT touch `templates/template_versions.json` or
`generator.__template_version__`. A template edit also needs a version bump,
and that is a judgement about what a reader's guide should say it was built
from, not something to generate. `tests/test_template_version_guard.py` fails
until it is done by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from generator import partials  # noqa: E402  (after sys.path)


def _rows() -> list[tuple[str, str]]:
    """(repo-relative path, digest) for the main template and every partial."""
    main = partials.TEMPLATES_DIR / "v2.5_template.html"
    if not main.exists():
        raise SystemExit(f"Main template missing: {main}")
    rows = [(partials.MAIN_TEMPLATE_ROW, partials.sha256_of(main))]
    for path in partials.partial_paths():
        rows.append((f"templates/partials/{path.name}", partials.sha256_of(path)))
    return rows


def main() -> int:
    before = partials.recorded_checksums() if partials.CHECKSUM_PATH.exists() else {}
    rows = _rows()

    text = "".join(f"{digest}  {row}\n" for row, digest in rows)
    partials.CHECKSUM_PATH.write_text(text, encoding="utf-8")

    after = dict((row, digest) for row, digest in rows)
    for row, digest in after.items():
        was = before.get(row)
        if was is None:
            print(f"added    {row}  {digest}")
        elif was != digest:
            print(f"changed  {row}  {was} -> {digest}")
    for row in before:
        if row not in after:
            print(f"removed  {row}")
    if before == after:
        print("no change")

    print(f"\n{len(rows)} row(s) written to {partials.CHECKSUM_PATH}")
    print(
        "Remember the version: a changed main template also needs "
        "generator.__template_version__ bumped and a row added to "
        "templates/template_versions.json."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
