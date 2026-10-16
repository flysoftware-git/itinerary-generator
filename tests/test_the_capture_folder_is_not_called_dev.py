"""The direct-batch HTML captures do not live in a folder called `dev`.

WHAT IT COST
------------
A prod run wrote its diagnostic captures to
`output/prod/dev/url_discovery_direct_batch_html`, so every prod output
directory contained a folder literally named `dev`. The owner asked twice where
the dev output had gone, and both times the answer was that no dev guide exists
and this is not one -- a prod run renders one environment, plus the private copy
of the same environment since #218.

It also looked like a privacy incident and had to be checked as one: a `dev`
build is NOT redacted, so a `dev` directory inside published output would be a
leak. It was not one -- the captures are AI-authored HTML about attractions and
carry no `index.html` -- but establishing that took a read of the published
repo's ignore rules.

THE SECOND DEFECT, WHICH WAS REAL
---------------------------------
`main.py` spelled the path out while `url_discovery` held it in
`DEFAULT_DIRECT_BATCH_HTML_CAPTURE_SUBDIR`, and the subdir is **configurable**
(`direct_batch_html_capture_subdir`). So a run that overrode it wrote captures
to one directory and had its parity checked against another, reporting
`0 captured HTML inputs` for a run that captured plenty. One fact, two copies,
and the easy query reaching the wrong one.

ORDER OF OPERATIONS
-------------------
The published repo's `.gitignore` is what keeps these out of a public Pages
upload, and its own comment says so: a publish step runs `git add -A` with no
environment check. That rule was given `captures/` BEFORE this rename, and keeps
`dev/` as well, because older builds on disk still use the old path. Renaming
first would have opened a window where the new path was unprotected.
"""

from __future__ import annotations

import inspect

from generator import main as main_mod
from generator.url_discovery import DEFAULT_DIRECT_BATCH_HTML_CAPTURE_SUBDIR


class TestTheNameNoLongerSaysDev:
    def test_the_capture_subdir_is_not_under_a_dev_folder(self):
        parts = DEFAULT_DIRECT_BATCH_HTML_CAPTURE_SUBDIR.split("/")

        assert "dev" not in parts, (
            "a folder named `dev` inside a prod output directory reads as a dev "
            "BUILD, which is a privacy question rather than a naming quibble"
        )
        assert "eval" not in parts, "same reason, and `eval/` is ignored alongside it"

    def test_it_is_still_a_relative_subdirectory(self):
        """It is joined onto the run's output directory, so an absolute path or a
        parent escape would write outside the run."""
        assert not DEFAULT_DIRECT_BATCH_HTML_CAPTURE_SUBDIR.startswith("/")
        assert ".." not in DEFAULT_DIRECT_BATCH_HTML_CAPTURE_SUBDIR.split("/")

    def test_it_still_names_what_it_holds(self):
        assert DEFAULT_DIRECT_BATCH_HTML_CAPTURE_SUBDIR == (
            "captures/url_discovery_direct_batch_html"
        )


class TestTheReaderAndTheWriterShareOnePath:
    """The defect that was real rather than cosmetic."""

    def test_the_parity_reader_does_not_spell_the_path_out(self):
        """Asserted over CODE lines only.

        The first version of this searched the whole source and tripped on the
        function's own comment, which quotes the old path to explain why it
        moved. A guard that fires on the explanation of the fix is the same
        wrong-population mistake this file is partly about -- so the comments
        are stripped and the assertion is about what executes.
        """
        src = inspect.getsource(main_mod._latest_direct_batch_parity_summary)
        code = "\n".join(
            line.split("#", 1)[0] for line in src.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )

        assert "DEFAULT_DIRECT_BATCH_HTML_CAPTURE_SUBDIR" in code, (
            "the reader must use the writer's constant, or a configured override "
            "sends the two to different directories"
        )
        assert '"dev"' not in code and "'dev'" not in code, (
            "no second copy of the path in executable code"
        )

    def test_the_reader_finds_what_the_writer_wrote(self, tmp_path):
        """Behavioural, not a source assertion: write a capture where the
        constant says, and check the parity summary counts it."""
        capture_dir = tmp_path / DEFAULT_DIRECT_BATCH_HTML_CAPTURE_SUBDIR
        capture_dir.mkdir(parents=True)
        (capture_dir / "zion.attraction.abc.html").write_text(
            '<a href="https://www.nps.gov/zion/">Zion</a>', encoding="utf-8"
        )

        summary = main_mod._latest_direct_batch_parity_summary(output_dir=tmp_path)

        assert summary["captured_html_input_count"] == 1
        assert summary["unique_captured_urls"] == 1

    def test_no_captures_reports_zero_rather_than_failing(self, tmp_path):
        """A run with captures disabled is not a broken run."""
        summary = main_mod._latest_direct_batch_parity_summary(output_dir=tmp_path)

        assert summary["captured_html_input_count"] == 0
