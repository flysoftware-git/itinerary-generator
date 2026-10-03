"""Which manifest fields this engine actually honours, and which it does not.

WHY THIS EXISTS
---------------
This engine's whole obligation is not to lose what it was handed
(`docs/design/the-manifest-is-the-interface.md` §2). It had no way to say
whether it was meeting that obligation, and the gap was not hypothetical:
`seeds: [{name, url}]` shipped in #189 parsed, validated, documented in
`requirements.md` §3 — and read by nothing. No test noticed for two weeks,
because no test asked.

So the rule, from that note's §3.2: for every field `MANIFEST_SCHEMA` accepts,
either something outside the parser reads it, or **this file says it is not
honoured and why**. A field in neither state fails
`tests/test_a_manifest_field_is_honoured_or_declared.py`.

WHAT THE CHECK IS, AND WHAT IT IS NOT
-------------------------------------
"Something outside the parser reads it" is a proxy for "it reaches the guide",
and it is a deliberately cheap one. It cannot tell a field that is read and
used from one that is read and then dropped two stages later, and it says
nothing about whether the rendering is correct. The parser is excluded because
the parser reading a field proves only that it was parsed, which is exactly what
was true of `seed_links`.

What it does catch is the one failure that is otherwise invisible: a field an
author can legitimately set, that nothing anywhere consumes. On its first run it
found `code_execution`, which nobody had reported.

**It would not have caught `seed_links` itself, and that is worth saying.** The
check is keyed on names the schema accepts, and `seed_links` is not one — the
parser derives it from `seeds: [{name, url}]`. Nor would the schema's own `url`
have helped: a name that common is read somewhere by definition. So the case
that prompted all this is pinned by a named test
(`test_seed_links_is_honoured_now`) rather than by the walk. A guard that
covered everything would be a better guard; this one covers the part that can be
asked cheaply for ninety-odd fields at once, and the rest is named individually.

A field here is not a bug by definition. Some of these are decisions. Each entry
says which.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: Fields `MANIFEST_SCHEMA` accepts that nothing outside the parser reads.
#: Each value says why, and whether that is a decision or a known gap.
#:
#: Adding an entry here is a statement that an author can set this field and it
#: will do nothing. It should be rarer than fixing the field.
FIELDS_NOT_HONOURED: dict[str, str] = {
    "code_execution": (
        "KNOWN GAP, found 2026-10-01 by the test that reads this file. "
        "`trip.llm_features.code_execution` and the nested `trip.llm.features."
        "code_execution` are accepted by the schema and resolved into the "
        "override dict by main._resolve_llm_overrides as `features`. "
        "MultiLLMClient.__init__ then reads `provider`, `model`, `temperature` "
        "and `max_tokens` from that dict and never `features`, so the setting "
        "is dropped in silence. Either the client should honour it or the "
        "schema should stop accepting it; both are changes to make "
        "deliberately, which is why it is declared rather than quietly removed."
    ),
}


def schema_field_names(schema: dict[str, Any]) -> set[str]:
    """Every property name the schema accepts, at any depth."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        for name, child in (node.get("properties") or {}).items():
            found.add(name)
            walk(child)
        if isinstance(node.get("items"), dict):
            walk(node["items"])
        for branch in (node.get("anyOf") or []) + (node.get("oneOf") or []):
            walk(branch)

    walk(schema)
    return found


def modules_reading(field: str, root: Path | None = None) -> list[str]:
    """Modules outside the parser that name this field as a string literal.

    A literal rather than an attribute, because the trip is a dict all the way
    down -- every consumer reaches a manifest field as `dest["lodging"]` or
    `.get("max_hike_miles")`.
    """
    base = root or Path(__file__).resolve().parent
    pattern = re.compile(r"""["']""" + re.escape(field) + r"""["']""")
    reading: list[str] = []
    for path in sorted(base.rglob("*.py")):
        if path.name in {"manifest_parser.py", "manifest_field_use.py"}:
            continue
        if "__pycache__" in path.parts:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not pattern.search(line) or _only_removes_it(line):
                continue
            reading.append(path.name)
            break
    return reading


def _only_removes_it(line: str) -> bool:
    """Is this line deleting the field rather than honouring it?

    Found by fault injection: disabling the one real consumer of `seed_links`
    left this check green, because `_strip_destination_seeds` does
    `dest.pop("seed_links", None)` for `--noseed` and that counted as reading
    it. Taking a field out is the opposite of honouring it, and a guard that
    cannot tell the difference would have passed for `seed_links` throughout the
    two weeks nothing read it -- the exact case it exists to catch.
    """
    stripped = line.strip()
    if stripped.startswith("#"):
        return True
    return ".pop(" in stripped or stripped.startswith("del ")
