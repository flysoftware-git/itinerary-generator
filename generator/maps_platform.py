"""One gate in front of every metered Google Maps Platform call.

Three modules reach Maps Platform products that cost money per request --
`place_resolver` and `places_filter` (Places Text Search) and
`transit_estimate` (Routes). Each was inert without a key and live with one,
and each decided that for itself by reading the environment in its own
constructor.

**A key in the environment is a credential, not a decision.** It arrives for
all sorts of reasons -- another project on the same machine, a CI secret shared
across jobs, an experiment from six months ago that was never unset -- and under
the old arrangement any of those silently turned three metered products on for
every subsequent run. Nothing was logged at the point of consent, because there
was no point of consent. The failure has a shape worth naming: without a key a
stray call fails loudly on the first request, which is itself a control; with a
funded key the same mistake succeeds and bills quietly.

So consent moves to `config.yaml`, where it is reviewable, diffable and
committed, and the environment keeps only the credential. Both are now
required: turning the gate on without a key still does nothing, and a key with
the gate shut is inert.

**This fails closed.** `read_transit_provider` fails open to its default,
correctly -- an unreadable config should not stop the pipeline running. That
argument does not transfer to a spend gate: the cost of failing closed is a
missing place_id, and the cost of failing open is an unbounded bill nobody
authorised. An unreadable config, a missing file, a malformed section and an
absent key all answer the same way here, which is "no".
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: Where the credential lives, in preference order. Both names are read because
#: Google's own documentation has used both, and a key under the older name is
#: still a funded key.
API_KEY_ENV_VARS = ("GOOGLE_MAPS_PLATFORM_KEY", "GOOGLE_MAPS_API_KEY")

DEFAULT_CONFIG_PATH = "config.yaml"

#: The section and key that carry the decision.
CONFIG_SECTION = "maps_platform"
CONFIG_KEY = "enabled"


def enabled(config_path: str | Path = DEFAULT_CONFIG_PATH) -> bool:
    """Whether metered Maps Platform calls are permitted at all.

    False for every kind of doubt: no config file, no section, an unparseable
    document, a value that is not a boolean. See the module docstring for why
    this direction is not the one `read_transit_provider` chose.
    """
    try:
        import yaml

        with Path(config_path).open(encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        section = cfg.get(CONFIG_SECTION) or {}
        return section.get(CONFIG_KEY) is True
    except FileNotFoundError:
        return False
    except Exception as exc:  # pragma: no cover - a corrupt config is not fatal
        logger.warning(
            "Could not read %s.%s from %s (%s); treating metered Maps Platform "
            "calls as not permitted.", CONFIG_SECTION, CONFIG_KEY, config_path, exc)
        return False


def api_key(config_path: str | Path = DEFAULT_CONFIG_PATH) -> str:
    """The configured key, or `""` when the gate is shut.

    The environment is not consulted at all unless the gate is open, so a key
    sitting in the environment cannot enable anything on its own -- which is the
    whole point, and is why callers ask for the key through here rather than
    reading `os.environ` and asking about the gate separately. Two places to get
    it right is one place too many for a spend control.
    """
    if not enabled(config_path):
        return ""
    for var in API_KEY_ENV_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return ""


def explain(config_path: str | Path = DEFAULT_CONFIG_PATH) -> str:
    """Why the metered products are off, for a log line that helps.

    "Skipped: no key" was the only diagnosis available before, and it was wrong
    half the time -- the key was there and the gate was shut. Empty when they
    are on.
    """
    if not enabled(config_path):
        return (
            f"{CONFIG_SECTION}.{CONFIG_KEY} is not true in {config_path}, so metered "
            "Google Maps Platform calls are not permitted. A key in the "
            "environment does not enable them on its own."
        )
    if not api_key(config_path):
        return (
            f"{CONFIG_SECTION}.{CONFIG_KEY} is true but no key is set: "
            f"expected one of {', '.join(API_KEY_ENV_VARS)} in the environment."
        )
    return ""
