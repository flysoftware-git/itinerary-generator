"""A template edit must move __template_version__.

generator.__template_version__ read "2.5" from the initial rebuild commit
until 2026-08-30 and never moved, while templates/v2.5_template.html changed
repeatedly -- twice on 2026-08-30 alone, for the tip-link and lunch-badge CSS.
Every published page and the Travel-apps gallery card reported that constant
as though it tracked the file.

templates/checksums.txt already forces a template edit to be acknowledged.
It does not tie that edit to a version, which is what this adds: the checksum
recorded for the CURRENT version must match the file, so changing the file
without adding a version fails here.

Limitation, stated rather than hidden: the map is append-only by convention.
Rewriting an existing row instead of adding one satisfies this test. It turns
silent drift into a deliberate act, which is the achievable guarantee.
"""

import hashlib
import json
from pathlib import Path

import pytest

from generator import __template_version__

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "v2.5_template.html"
VERSIONS = ROOT / "templates" / "template_versions.json"


def _versions():
    return json.loads(VERSIONS.read_text(encoding="utf-8"))["versions"]


def _template_sha():
    return hashlib.sha256(TEMPLATE.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def test_the_declared_version_has_a_recorded_checksum():
    assert __template_version__ in _versions(), (
        f"__template_version__ is {__template_version__!r} with no entry in "
        f"{VERSIONS.name}. Add one recording this template's checksum."
    )


def test_the_recorded_checksum_matches_the_template_on_disk():
    """Fails when the template changed but the version did not."""
    recorded = _versions()[__template_version__]
    actual = _template_sha()
    assert recorded == actual, (
        "The template file has changed but __template_version__ is still "
        f"{__template_version__!r}. Bump it and add a row to {VERSIONS.name} "
        f"with checksum {actual}."
    )


def test_no_two_versions_claim_the_same_checksum():
    """Two versions with one checksum means a bump that changed nothing."""
    shas = list(_versions().values())
    assert len(shas) == len(set(shas))


@pytest.mark.parametrize("sha", [None])
def test_every_recorded_checksum_is_a_sha256(sha):
    for version, value in _versions().items():
        assert len(value) == 64 and all(c in "0123456789abcdef" for c in value), version


def test_it_agrees_with_the_standalone_checksum_file():
    """checksums.txt and this map must not disagree about the same file."""
    stored = (ROOT / "templates" / "checksums.txt").read_text(encoding="utf-8").split()[0]
    assert stored == _versions()[__template_version__] == _template_sha()
