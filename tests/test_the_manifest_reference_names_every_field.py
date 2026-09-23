"""The manifest is the whole trip-definition interface, so its reference has to
be complete.

There is no guided authoring mode here (GH #72): a trip is defined by a manifest
and nothing else. `MANIFEST_SCHEMA` does not set `additionalProperties: false`,
so a field the generator honours but nobody wrote down is a field no author can
find -- and a misspelling of one validates clean. Three such fields were live
when this test was written: `trip.llm_model`, which chooses the model the entire
guide is written by; `trip.short_name`, the installed app's icon label; and the
top-level `categories` block, which decides which priced discovery categories a
trip buys.

This test does not check that the prose is good. It checks that every field name
the schema accepts appears somewhere in section 3 of docs/requirements.md, which
is the cheapest guard that still fails when the two drift apart.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from generator.manifest_parser import MANIFEST_SCHEMA

REQUIREMENTS = Path(__file__).resolve().parents[1] / "docs" / "requirements.md"


def _section_three() -> str:
    """The text of '## 3. Trip Manifest Schema' up to the next top-level heading."""
    text = REQUIREMENTS.read_text(encoding="utf-8")
    start = re.search(r"^## 3\. Trip Manifest Schema", text, re.MULTILINE)
    assert start, "docs/requirements.md no longer has a '## 3. Trip Manifest Schema' section"
    rest = text[start.end():]
    end = re.search(r"^## \d+\.", rest, re.MULTILINE)
    return rest[: end.start()] if end else rest


def _field_names() -> list[tuple[str, str]]:
    """(path, field) for every named property in the manifest schema."""
    found: list[tuple[str, str]] = []

    def walk(node: object, path: str) -> None:
        if not isinstance(node, dict):
            return
        for name, child in (node.get("properties") or {}).items():
            found.append((f"{path}.{name}" if path else name, name))
            walk(child, f"{path}.{name}" if path else name)
        items = node.get("items")
        if isinstance(items, dict):
            walk(items, f"{path}[]")
        for branch in node.get("anyOf") or []:
            walk(branch, path)

    walk(MANIFEST_SCHEMA, "")
    return found


@pytest.mark.parametrize("path,field", _field_names(), ids=lambda value: str(value))
def test_section_three_names_the_field(path: str, field: str) -> None:
    section = _section_three()
    assert re.search(rf"`[^`]*\b{re.escape(field)}\b[^`]*`", section), (
        f"{path} is accepted by MANIFEST_SCHEMA but never named in section 3 of "
        f"docs/requirements.md. Add a row describing it -- the schema takes "
        f"unknown keys silently, so an undocumented field is an undiscoverable one."
    )
