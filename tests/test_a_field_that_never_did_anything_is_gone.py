"""`llm_features.code_execution` is removed, not implemented.

Found by `tests/test_a_manifest_field_is_honoured_or_declared.py` on its first
run: `trip.llm_features.code_execution` and the nested
`trip.llm.features.code_execution` were accepted by the schema and carried into
the override dict by `main._resolve_llm_overrides` as `features` — and read by
nothing. `MultiLLMClient.__init__` takes `provider`, `model`, `temperature` and
`max_tokens` from that dict and never `features`, and no provider's
`create_json_completion` has a tools parameter one could be passed to.

So the choice was to build the capability or to stop promising it. Removed,
because nothing in the repository set it (`manifests/tuning_surface.yaml` says
in a comment that it deliberately does not), no design note asks for it, and a
JSON travel-content call has no evident use for code execution. Building it on
spec would have been inventing a requirement.
"""

from __future__ import annotations

import logging

from generator.main import _resolve_llm_overrides
from generator.manifest_field_use import FIELDS_NOT_HONOURED, schema_field_names
from generator.manifest_parser import MANIFEST_SCHEMA

FIELDS = schema_field_names(MANIFEST_SCHEMA)


def test_the_schema_no_longer_accepts_it():
    assert "code_execution" not in FIELDS
    assert "llm_features" not in FIELDS
    trip_props = MANIFEST_SCHEMA["properties"]["trip"]["properties"]
    assert "features" not in trip_props["llm"]["properties"]


def test_the_declaration_is_empty_again():
    """The guard's first find was answered rather than kept.

    A list of fields that do nothing is only honest while it is short; the way
    to keep it short is to fix entries rather than add them.
    """
    assert FIELDS_NOT_HONOURED == {}


def test_the_override_resolver_no_longer_carries_features():
    """It was the one caller that moved the value, and it moved it nowhere."""
    overrides = _resolve_llm_overrides(
        {"trip": {"llm_provider": "grok", "llm_features": {"code_execution": True}}},
        cli_provider=None,
        cli_model=None,
    )

    assert "features" not in overrides
    assert overrides["provider"] == "grok", "the rest of the resolution is untouched"


class TestAManifestCarryingItIsTold:
    def _warnings(self, caplog, trip: dict) -> list[str]:
        from generator.manifest_parser import ManifestParser

        parser = ManifestParser.__new__(ManifestParser)
        with caplog.at_level(logging.WARNING, logger="generator.manifest_parser"):
            parser._warn_llm_features_never_did_anything({"trip": trip})
        return [r.getMessage() for r in caplog.records]

    def test_the_flat_form_is_named(self, caplog):
        said = self._warnings(caplog, {"llm_features": {"code_execution": True}})
        assert any("llm_features" in m for m in said), said
        assert any("read by" in m and "nothing" in m for m in said), said

    def test_the_nested_form_is_named(self, caplog):
        said = self._warnings(caplog, {"llm": {"features": {"code_execution": True}}})
        assert any("llm.features" in m for m in said), said

    def test_an_ordinary_manifest_says_nothing(self, caplog):
        assert self._warnings(caplog, {"llm": {"provider": "grok"}}) == []

    def test_it_is_a_warning_and_not_a_refusal(self, caplog):
        """A manifest carrying it built the same output before and builds the
        same output now, so failing the run would punish an author for a promise
        this project failed to keep."""
        self._warnings(caplog, {"llm_features": {"code_execution": True}})  # no raise
