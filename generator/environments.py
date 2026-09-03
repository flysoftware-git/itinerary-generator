"""The environments a run can be in, and the single place they are named.

`environment` is reachable three ways, in this priority order:

1. ``--environment`` on the command line
2. ``trip.environment`` in the manifest
3. the ``ENVIRONMENT`` variable

Two of those validated the value and the third did not, and the resolved value
becomes a directory segment (``output/<environment>/``) and the run ledger's
path. So an ``ENVIRONMENT`` left over in a shell could send a run's output and
its ledger somewhere no flag can name and nothing later reads — silently, since
a made-up directory is created as readily as a real one.

This module is deliberately tiny and imports nothing from the rest of the
package, so `main.py` can name the choices in a decorator without paying for the
parser's imports, and `manifest_parser.py` can build its enum from the same
tuple. The list existing once is the point: it was written out three times, and
the third copy was the one that was not a list at all.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Every environment a run may select. `eval` rather than `test`: these runs are
#: evaluated, and a directory called `test` beside `tests/` reads as somewhere
#: pytest writes.
ENVIRONMENTS: tuple[str, ...] = ("dev", "eval", "prod")

DEFAULT_ENVIRONMENT = "dev"


class UnknownEnvironment(ValueError):
    """A value that is not an environment, named along with where it came from."""

    def __init__(self, value: str, source: str) -> None:
        self.value = value
        self.source = source
        super().__init__(
            f"{source} is {value!r}, which is not an environment. "
            f"Valid values are: {', '.join(ENVIRONMENTS)}."
        )


def resolve_environment(
    *,
    cli: str | None = None,
    manifest: str | None = None,
    variable: str | None = None,
) -> str:
    """The environment this run is in, or a refusal that says why.

    Priority is CLI, then manifest, then variable, then the default — unchanged.
    What is new is that whichever one wins is *checked*, and that a variable
    which loses is not allowed to fail a run it had no part in: an
    ``ENVIRONMENT`` typo in some shell profile is worth a warning when a flag
    overrode it, and worth a refusal only when it is what the run would use.
    """
    for value, source in ((cli, "--environment"), (manifest, "trip.environment")):
        if value:
            selected = str(value).strip().lower()
            if selected not in ENVIRONMENTS:
                raise UnknownEnvironment(str(value), source)
            _warn_if_unusable(variable)
            return selected

    if variable and str(variable).strip():
        selected = str(variable).strip().lower()
        if selected not in ENVIRONMENTS:
            raise UnknownEnvironment(str(variable), "the ENVIRONMENT variable")
        return selected

    return DEFAULT_ENVIRONMENT


def _warn_if_unusable(variable: str | None) -> None:
    """Say so when ENVIRONMENT holds something that could never have been used.

    Not an error, because it did not decide this run. Not silence either: a
    variable that would refuse the next run deserves to be found on the run it
    could not affect rather than on the one it stops.
    """
    if not variable or not str(variable).strip():
        return
    if str(variable).strip().lower() not in ENVIRONMENTS:
        logger.warning(
            "ENVIRONMENT is %r, which is not an environment (%s). It was overridden "
            "this run; on a run without an override it would be refused.",
            variable,
            ", ".join(ENVIRONMENTS),
        )
