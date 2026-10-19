"""Template partials: a component this engine draws, addressable by name.

WHY THIS EXISTS
---------------
`templates/` held one file -- `v2.5_template.html`, the whole guide -- plus its
checksum and version map. So there was nowhere to put a component. Anything
drawn on a page had to be either inline markup built by a Python function in
`html_assembler`, or more of that one template.

That is fine for a page nobody else draws. It is not fine for a component that
is meant to be drawn the same way by more than one consumer of this engine:
there is nothing to include, so the second consumer reimplements the idea and
the two drift. A named file that can be loaded is the smallest thing that makes
a component shared rather than copied.

WHAT A PARTIAL IS
-----------------
One HTML file under `templates/partials/`, holding named blocks:

    <!--PARTIAL:styles-->
    .thing { ... }
    <!--/PARTIAL:styles-->

    <!--PARTIAL:item-->
    <div class="thing">__LABEL__</div>
    <!--/PARTIAL:item-->

`load("name")` returns `{"styles": ..., "item": ...}`. Blocks rather than one
file per fragment because a component's CSS and its markup are one thing: they
share class names, and splitting them across files is how one gets edited
without the other.

Substitution is deliberately not a template language. A block is a string;
callers fill it with `str.replace`, the same way `html_assembler` fills the
main template's `<!--PLACEHOLDER-->` comments. Adding Jinja here would mean a
consumer in another language could not draw the component, which is the whole
reason the partial exists.

CHECKSUMS
---------
Partials are verified at load against `templates/checksums.txt`, the same file
and the same reason as the main template: a page's appearance must not change
because a template file was edited without anyone acknowledging it.

Known gap, stated rather than hidden: `generator.__template_version__` stamps
the MAIN template's version into every generated page. Partials have no such
stamp, so a partial edit is acknowledged (the checksum must be regenerated) but
is not visible to someone holding a guide. Closing that means deciding what a
page's version means when it is assembled from several files, which is a
question this note does not answer.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
PARTIALS_DIR = TEMPLATES_DIR / "partials"
CHECKSUM_PATH = TEMPLATES_DIR / "checksums.txt"

#: Repo-relative, forward-slashed, matching how the existing row names the main
#: template. The file is read on Windows and on Linux and must mean the same
#: thing on both.
MAIN_TEMPLATE_ROW = "templates/v2.5_template.html"

_BLOCK_RE = re.compile(
    r"<!--PARTIAL:(?P<name>[a-z0-9_]+)-->(?P<body>.*?)<!--/PARTIAL:\1-->",
    re.DOTALL,
)


def recorded_checksums() -> dict[str, str]:
    """`templates/checksums.txt` as {repo-relative path: sha256}.

    The file was a single `<sha>  <path>` line and the reader took
    `text.split()[0]`, which happens to still return the first line's digest
    once there are several rows -- but only while the main template stays
    first, which is not something a file format should rely on. Parsed by path
    instead, so the row order carries no meaning.
    """
    if not CHECKSUM_PATH.exists():
        raise FileNotFoundError(f"Checksum file not found: {CHECKSUM_PATH}")
    rows: dict[str, str] = {}
    for line in CHECKSUM_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        rows[parts[1].replace("\\", "/")] = parts[0]
    return rows


def sha256_of(path: Path) -> str:
    """Digest of a template file, over its text.

    Over `read_text(...).encode("utf-8")` rather than the raw bytes, matching
    `html_assembler._verify_checksum` exactly: the repository's `.gitattributes`
    normalizes line endings, and hashing bytes would make the digest depend on
    the checkout's newline flavour.
    """
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def partial_paths() -> list[Path]:
    """Every partial on disk, sorted. Empty when the directory is absent."""
    if not PARTIALS_DIR.exists():
        return []
    return sorted(PARTIALS_DIR.glob("*.html"))


def _verify(path: Path) -> None:
    row = f"templates/partials/{path.name}"
    recorded = recorded_checksums().get(row)
    actual = sha256_of(path)
    if recorded is None:
        raise RuntimeError(
            f"Partial {row} has no row in {CHECKSUM_PATH.name}.\n"
            f"Add one:  {actual}  {row}"
        )
    if recorded != actual:
        raise RuntimeError(
            f"Partial checksum mismatch for {row}!\n"
            f"  Expected: {recorded}\n"
            f"  Actual:   {actual}\n"
            "The partial has been modified. Regenerate the checksum row (do "
            "not hand-edit it) or restore the file from git."
        )


def load(name: str) -> dict[str, str]:
    """Named blocks of one partial, checksum-verified.

    Raises rather than returning empty for a missing file or an unrecorded
    checksum: a component that silently renders nothing is the failure this
    whole mechanism exists to avoid, and an exception at assembly time is seen
    before a page ships.
    """
    path = PARTIALS_DIR / f"{name}.html"
    if not path.exists():
        raise FileNotFoundError(f"Partial not found: {path}")
    _verify(path)
    blocks = {
        match.group("name"): match.group("body").strip("\n")
        for match in _BLOCK_RE.finditer(path.read_text(encoding="utf-8"))
    }
    if not blocks:
        raise RuntimeError(
            f"Partial {path.name} declares no <!--PARTIAL:name--> blocks, so "
            "nothing in it can be drawn."
        )
    return blocks


def block(name: str, block_name: str) -> str:
    """One block, with a message that names both halves when it is missing."""
    blocks = load(name)
    if block_name not in blocks:
        raise KeyError(
            f"Partial {name}.html has no block {block_name!r} -- it declares "
            + ", ".join(sorted(blocks))
            + "."
        )
    return blocks[block_name]
