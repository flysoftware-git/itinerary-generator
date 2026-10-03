"""A field the manifest accepts is read by something, or declared unread.

The gap this closes, from `docs/design/the-manifest-is-the-interface.md` §3.2:
this engine renders what it is handed, and had no way to say whether it was
doing so. `seeds: [{name, url}]` shipped in #189 parsed, validated and
documented — and read by nothing, for two weeks, because no test asked the
question.

Asking it for all 93 fields at once costs one schema walk. The first run of this
test found a second instance nobody had reported (`code_execution`), which is
the argument for it.
"""

from __future__ import annotations

import pytest

from generator.manifest_field_use import (
    FIELDS_NOT_HONOURED,
    modules_reading,
    schema_field_names,
)
from generator.manifest_parser import MANIFEST_SCHEMA

FIELDS = sorted(schema_field_names(MANIFEST_SCHEMA))


def test_the_schema_walk_finds_the_fields_at_every_depth():
    """Fixture premise. A walk that missed a level would pass the suite empty."""
    assert len(FIELDS) > 80, len(FIELDS)
    for expected in (
        "title",            # trip, top level
        "seeds",            # destination
        "checkin_time",     # destination.lodging
        "confirmation_number",  # destination.transportation[] item
        "depart_time",      # ...and its stops[]
        "distributor_url",  # trip.brand
        "mode",             # legs[] item
    ):
        assert expected in FIELDS, expected


@pytest.mark.parametrize("field", FIELDS)
def test_the_field_is_read_by_something_or_declared_unread(field: str):
    """The rule. Either something outside the parser reads it, or it is listed.

    A field in neither state is one an author can set to no effect, with
    nothing anywhere saying so -- which is what `seed_links` was until #195.
    """
    if field in FIELDS_NOT_HONOURED:
        pytest.skip(f"declared not honoured: {FIELDS_NOT_HONOURED[field][:60]}…")
    assert modules_reading(field), (
        f"{field!r} is accepted by MANIFEST_SCHEMA and read by nothing outside "
        "manifest_parser.py. Either make something honour it, or declare it in "
        "generator/manifest_field_use.py FIELDS_NOT_HONOURED with the reason."
    )


def test_seed_links_is_honoured_now():
    """The field this guard was written for, pinned at the far end.

    #189 added it and nothing read it; #195 made discovery prefer it. If that
    consumer is ever removed, this says so in its own words rather than leaving
    the parametrized case to explain it.
    """
    assert "seed_links" not in FIELDS_NOT_HONOURED
    assert "url_discovery.py" in modules_reading("seed_links")


class TestTheDeclarationIsKeptHonest:
    def test_every_declared_field_is_actually_in_the_schema(self):
        """A stale entry would silence a field that no longer exists, and worse,
        would go on silencing a NEW field that later took the same name."""
        unknown = sorted(set(FIELDS_NOT_HONOURED) - set(FIELDS))
        assert not unknown, f"declared but not in the schema: {unknown}"

    def test_every_declared_field_is_really_unread(self):
        """A field that got a consumer must leave the list.

        Otherwise the list grows into a place where working fields hide, and the
        next reader cannot tell which entries are real.
        """
        now_read = {f: modules_reading(f) for f in FIELDS_NOT_HONOURED}
        stale = {f: mods for f, mods in now_read.items() if mods}
        assert not stale, (
            f"declared not honoured, but read by: {stale}. Remove the entry."
        )

    @pytest.mark.parametrize("field", sorted(FIELDS_NOT_HONOURED))
    def test_the_reason_says_something(self, field: str):
        """A one-word reason is how a list like this stops being read."""
        reason = FIELDS_NOT_HONOURED[field]
        assert len(reason) > 80, f"{field}: {reason!r}"
        assert any(
            word in reason.lower() for word in ("gap", "decision", "deliberate")
        ), f"{field}: say whether this is a decision or a gap"
