"""The optional-category switches must be settable in BOTH directions.

Before these flags the CLI could only turn trails/events off (--notrails,
--skip-events); turning one ON for a single run meant editing config.yaml and
remembering to put it back. Since these are the priced categories, they are
exactly the ones worth enabling deliberately for one run.
"""
import textwrap

import pytest

from generator.main import _category_enabled, _resolve_category


@pytest.fixture()
def cfg(tmp_path):
    def _write(body: str) -> str:
        p = tmp_path / "config.yaml"
        p.write_text(textwrap.dedent(body), encoding="utf-8")
        return str(p)

    return _write


@pytest.mark.parametrize(
    "cli, enabled_in_config, expected",
    [
        (None, True, True),     # no flag -> config decides
        (None, False, False),
        (True, False, True),    # flag ON overrides config OFF  <- the new case
        (False, True, False),   # flag OFF overrides config ON
    ],
)
def test_cli_choice_wins_over_config(cfg, cli, enabled_in_config, expected):
    path = cfg(f"""
        trails:
          enabled: {str(enabled_in_config).lower()}
    """)
    assert _resolve_category(cli, path, "trails") is expected


def test_absent_section_stays_off_unless_a_flag_says_otherwise(cfg):
    path = cfg("other: {}\n")
    assert _resolve_category(None, path, "trails") is False
    assert _resolve_category(True, path, "trails") is True


def test_restaurants_keep_their_on_default(cfg):
    """Restaurants are core content; the switch exists without changing it."""
    path = cfg("other: {}\n")
    assert _resolve_category(None, path, "restaurants", default=True) is True
    assert _resolve_category(False, path, "restaurants", default=True) is False


def test_unreadable_config_still_fails_closed(tmp_path):
    """A config this cannot read must not silently buy a priced category."""
    missing = str(tmp_path / "nope.yaml")
    assert _category_enabled(missing, "trails") is False
    assert _resolve_category(None, missing, "trails") is False
    # ...but an explicit flag is a deliberate instruction, so it is honoured.
    assert _resolve_category(True, missing, "trails") is True
