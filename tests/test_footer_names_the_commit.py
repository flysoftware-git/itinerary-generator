"""The guide says which commit built it -- in a fold, not on the footer line.

A version number names a release; it does not name the code. Two guides built a
day apart from the same version can differ in every fix merged between them,
and someone holding one cannot tell which. The commit was already recorded --
`development_build` carries it and the page's leading comment prints the
fingerprint -- but a comment in the source is not something a reader can open.

Owner, 2026-09-21: behind a fold beside *About the links*, not on the visible
line. The same ruling as #149's link notes -- detail that serves the person
tracing a guide is one click away, and is not printed at every traveller.
"""

from __future__ import annotations

from generator.html_assembler import HTMLAssembler

FULL = "abc1234" + "0" * 33


def _trip(git=None):
    meta = {
        "generator_version": "9.9.9",
        "template_version": "2.5",
        "generated_at_utc": "2026-07-26T17:41:23+00:00",
        "llm": {"provider": "openai", "model": "test",
                "usage": {"models": [], "total_estimated_cost_usd": 0.0}},
    }
    if git is not None:
        meta["development_build"] = {"fingerprint": "v9.9.9+abc1234+clean+r1", "git": git}
    return {"trip": {"title": "Test Trip", "theme_color": "#C0623E"},
            "_meta": meta, "destinations": []}


def _footer(git=None):
    return HTMLAssembler(config_path="config.yaml")._build_generator_footer(_trip(git))


def _visible_line(footer: str) -> str:
    """The footer with every fold removed: what a reader sees without clicking."""
    out, rest = "", footer
    while "<details" in rest:
        head, _, tail = rest.partition("<details")
        out += head
        rest = tail.partition("</details>")[2]
    return out + rest


def test_the_commit_is_in_a_fold_of_its_own():
    footer = _footer({"short_commit": "abc1234", "commit": FULL, "dirty": False})

    assert '<details class="build-notes"' in footer
    assert "About this build" in footer
    assert "Built from commit" in footer and "abc1234" in footer


def test_the_visible_line_carries_no_commit():
    """The ruling itself. Seen red with the commit put back beside the
    version, which is how this change was first written."""
    footer = _footer({"short_commit": "abc1234", "commit": FULL, "dirty": False})

    assert "abc1234" not in _visible_line(footer)
    assert "v9.9.9" in _visible_line(footer), "the version itself stays on the line"


def test_the_commit_links_to_the_code_it_names():
    """Tracing a guide to its code is one click, not a copy-and-search."""
    footer = _footer({"short_commit": "abc1234", "commit": FULL, "dirty": False})

    assert f'{HTMLAssembler._REPO_URL}/commit/{FULL}' in footer


def test_a_build_from_a_dirty_tree_says_so():
    """A dirty build's commit is not the whole story, and the stamp says so
    rather than letting the commit stand for code it does not contain."""
    footer = _footer({"short_commit": "abc1234", "commit": FULL, "dirty": True})

    assert "with local changes" in footer


def test_a_clean_build_never_claims_local_changes():
    footer = _footer({"short_commit": "abc1234", "commit": FULL, "dirty": False})

    assert "local changes" not in footer


def test_no_git_means_no_fold_rather_than_a_placeholder():
    """A copy installed from an archive has no commit. Nothing that looks like
    one is invented, and there is no empty fold to open."""
    footer = _footer()

    assert "About this build" not in footer
    assert "build-notes" not in footer
    assert "v9.9.9 · Itinerary output" in footer, "the visible line is unchanged"


def test_the_commit_is_escaped():
    footer = _footer({"short_commit": "<b>x</b>", "commit": "", "dirty": False})

    assert "<b>x</b>" not in footer
    assert "&lt;b&gt;x&lt;/b&gt;" in footer
