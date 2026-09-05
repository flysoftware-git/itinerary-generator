"""One setting, three entry points, one list — and the third one now checked.

`environment` reaches a run from `--environment`, from `trip.environment`, and
from the `ENVIRONMENT` variable. The first two were validated against a list of
values; the third was read straight into `output/<environment>/` and the run
ledger's path, so anything at all in that variable became a directory.
"""

from __future__ import annotations

import logging

import click
import pytest

from generator.environments import (
    DEFAULT_ENVIRONMENT,
    ENVIRONMENTS,
    UnknownEnvironment,
    resolve_environment,
)


def test_the_environments_are_dev_eval_prod():
    """`eval` rather than `test`: these runs are evaluated, and a directory
    called `test` beside `tests/` reads as somewhere pytest writes."""
    assert ENVIRONMENTS == ("dev", "eval", "prod")
    assert DEFAULT_ENVIRONMENT in ENVIRONMENTS


# ------------------------------------------------------------------ priority


def test_priority_is_cli_then_manifest_then_variable():
    assert resolve_environment(cli="prod", manifest="eval", variable="dev") == "prod"
    assert resolve_environment(manifest="eval", variable="dev") == "eval"
    assert resolve_environment(variable="prod") == "prod"
    assert resolve_environment() == DEFAULT_ENVIRONMENT


def test_the_value_is_normalised_whichever_source_it_came_from():
    assert resolve_environment(cli="PROD") == "prod"
    assert resolve_environment(variable=" Eval ") == "eval"


def test_an_empty_source_does_not_count_as_a_choice():
    """An unset variable is exported as an empty string often enough that
    treating `""` as a selection would send those runs nowhere."""
    assert resolve_environment(cli="", manifest="", variable="") == DEFAULT_ENVIRONMENT
    assert resolve_environment(cli="", manifest="prod") == "prod"


# ----------------------------------------------------------------- the gap


def test_the_variable_can_no_longer_name_a_directory_nobody_chose():
    """The gap this module exists for. The resolved value becomes
    `output/<environment>/` and the ledger's path, and this source was the one
    that never checked it."""
    with pytest.raises(UnknownEnvironment) as caught:
        resolve_environment(variable="staging")
    assert "ENVIRONMENT" in str(caught.value)
    assert "dev, eval, prod" in str(caught.value)


def test_a_variable_holding_a_path_is_refused_rather_than_walked():
    """`output/../../etc/` is a directory too, and it would have been created."""
    with pytest.raises(UnknownEnvironment):
        resolve_environment(variable="../../etc")


def test_the_refusal_names_the_source_it_came_from():
    """Three sources, so "that is not an environment" is not enough to act on."""
    with pytest.raises(UnknownEnvironment) as from_cli:
        resolve_environment(cli="staging")
    with pytest.raises(UnknownEnvironment) as from_manifest:
        resolve_environment(manifest="staging")

    assert "--environment" in str(from_cli.value)
    assert "trip.environment" in str(from_manifest.value)


def test_a_variable_that_lost_does_not_fail_a_run_it_had_no_part_in(caplog):
    """An `ENVIRONMENT` typo in a shell profile is worth a warning when a flag
    overrode it, and a refusal only when it is what the run would use."""
    with caplog.at_level(logging.WARNING):
        assert resolve_environment(cli="dev", variable="staging") == "dev"
    assert "ENVIRONMENT" in caplog.text
    assert "overridden" in caplog.text


def test_a_variable_that_lost_and_was_valid_says_nothing(caplog):
    with caplog.at_level(logging.WARNING):
        assert resolve_environment(cli="dev", variable="prod") == "dev"
    assert caplog.text == ""


# ------------------------------------------------------- the list, once only


def test_every_entry_point_reads_the_same_list():
    """The list was written out three times and the third copy was not a list at
    all. Each of these now reads the tuple rather than restating it."""
    from generator.main import main as cli
    from generator.manifest_parser import MANIFEST_SCHEMA

    option = next(
        param for param in cli.params
        if isinstance(param, click.Option) and "--environment" in param.opts
    )
    schema_enum = (
        MANIFEST_SCHEMA["properties"]["trip"]["properties"]["environment"]["enum"]
    )
    assert list(option.type.choices) == list(ENVIRONMENTS)
    assert schema_enum == list(ENVIRONMENTS)


def test_the_module_stays_importable_without_the_rest_of_the_package():
    """`main.py` names the choices in a decorator, so this is imported at module
    load. It must not drag the parser's imports in behind it."""
    import ast
    import pathlib

    source = pathlib.Path("generator/environments.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("generator")
        elif isinstance(node, ast.Import):
            assert not any(a.name.startswith("generator") for a in node.names)
