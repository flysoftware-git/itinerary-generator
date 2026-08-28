"""Regenerate a `.local` manifest from its public counterpart.

Why this exists
---------------
A public manifest and its gitignored `.local` twin differ only by private
details -- a home address instead of a hotel stand-in. Keeping them in step
was a manual copy-and-substitute, and it drifted: an `en_route_seeds` entry
added to the public file was never carried across, so every run afterwards
read three seeds where the manifest said four. That surfaced two steps
downstream as a feature apparently not working, after a paid run, and
nothing anywhere could have caught it -- the `.local` file is gitignored by
design, so CI cannot see it.

Why textual substitution and not a YAML round trip
--------------------------------------------------
The same reason the reservations sidecar exists rather than merging into the
manifest: PyYAML cannot preserve comments, and a load-and-dump would delete
every one. A large share of these manifests IS comments, and they are the
schema documentation. So this copies the public file's text verbatim and
substitutes marked regions.

The private file
----------------
`<stem>.private.yaml`, gitignored, holds the header and the substitutions:

    header: |
      # optional banner prepended to the generated file
    substitutions:
      - find: |
          <exact text from the public manifest>
        replace: |
          <text to put in its place>

Every `find` must appear EXACTLY ONCE. Zero matches means the public file
changed underneath the private one -- the precise failure this script was
written for -- and it is a hard error, never a silent skip.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Same as scripts/ingest_reservations.py: this is run as a file, not a
# module, so the repo root is not on sys.path and `generator` is unimportable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import click
import yaml


def _is_tracked_by_git(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


@click.command()
@click.option("--manifest", required=True, type=click.Path(exists=True, dir_okay=False),
              help="The PUBLIC manifest to generate a .local twin from.")
@click.option("--check", is_flag=True,
              help="Verify the existing .local file matches what would be generated, "
                   "and exit non-zero if it does not. Writes nothing.")
def main(manifest: str, check: bool) -> None:
    public = Path(manifest)
    if public.name.endswith(".local.yaml"):
        raise click.ClickException("Pass the PUBLIC manifest; the .local file is the output.")

    stem = public.name[: -len(".yaml")]
    private_path = public.with_name(f"{stem}.private.yaml")
    local_path = public.with_name(f"{stem}.local.yaml")

    if not private_path.exists():
        raise click.ClickException(
            f"No private overrides at {private_path}. See this script's docstring "
            "for its shape."
        )
    # A private file that is tracked means the address is already in history,
    # which is the whole thing this arrangement exists to prevent. Refuse
    # rather than help it along.
    if _is_tracked_by_git(private_path):
        raise click.ClickException(
            f"{private_path} is TRACKED BY GIT. It holds private data and must be "
            "gitignored. Remove it from the index before running this."
        )

    private = yaml.safe_load(private_path.read_text(encoding="utf-8")) or {}
    text = public.read_text(encoding="utf-8")

    for index, sub in enumerate(private.get("substitutions", []) or [], start=1):
        find = str((sub or {}).get("find", "") or "")
        replace = str((sub or {}).get("replace", "") or "")
        if not find:
            raise click.ClickException(f"substitution {index} has no `find`")
        count = text.count(find)
        if count != 1:
            raise click.ClickException(
                f"substitution {index} matched {count} times, expected exactly 1.\n"
                f"The public manifest has changed underneath {private_path.name}.\n"
                f"First line looked for: {find.splitlines()[0] if find.splitlines() else find!r}"
            )
        text = text.replace(find, replace, 1)

    generated = str(private.get("header", "") or "") + text

    if check:
        if not local_path.exists():
            raise click.ClickException(f"{local_path} does not exist")
        if local_path.read_text(encoding="utf-8") != generated:
            raise click.ClickException(
                f"{local_path.name} is STALE -- it does not match what the public "
                f"manifest plus {private_path.name} would generate. Re-run without "
                "--check to regenerate."
            )
        click.echo(f"{local_path.name} is up to date.")
        return

    local_path.write_text(generated, encoding="utf-8")
    click.echo(f"Wrote {local_path}")

    # Generating something the engine cannot read is worse than not
    # generating it, so validate before claiming success.
    from generator.manifest_parser import ManifestParser
    trip = ManifestParser().parse(local_path)
    click.echo(f"Valid — {len(trip.get('destinations', []) or [])} destination(s).")


if __name__ == "__main__":
    main()
