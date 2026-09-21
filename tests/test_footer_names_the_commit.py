"""The guide's footer names the commit that built it, not only the version.

A version number names a release; it does not name the code. Two guides built a
day apart from the same version can differ in every fix merged between them,
and a reader holding one cannot tell which. The commit was already recorded --
`development_build` carries it and the page's leading comment prints the
fingerprint -- but a comment in the source is not something a reader sees, so a
question about a guide in hand could not be traced to the code that made it.
"""

from __future__ import annotations

from generator.html_assembler import HTMLAssembler


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


def test_the_footer_names_the_commit_beside_the_version():
    footer = _footer({"short_commit": "abc1234", "commit": "abc1234" + "0" * 33, "dirty": False})
    assert "v9.9.9 · abc1234" in footer


def test_a_build_from_a_dirty_tree_says_so():
    """A dirty build's commit is not the whole story, and the stamp says so
    rather than implying the code is exactly that commit."""
    footer = _footer({"short_commit": "abc1234", "dirty": True})
    assert "abc1234 + local changes" in footer


def test_a_clean_build_does_not_claim_local_changes():
    footer = _footer({"short_commit": "abc1234", "dirty": False})
    assert "local changes" not in footer


def test_no_git_leaves_no_placeholder():
    """A copy installed from an archive has no commit: nothing is printed
    rather than something that looks like one."""
    # The footer already has a separator after the version, before the output
    # time, so the claim is that NOTHING is inserted between them.
    assert "v9.9.9 · Itinerary output" in _footer()
    assert "v9.9.9 · Itinerary output" in _footer({"short_commit": "", "dirty": False})


def test_the_commit_is_escaped():
    footer = _footer({"short_commit": "<b>x</b>", "dirty": False})
    assert "<b>x</b>" not in footer
    assert "&lt;b&gt;x&lt;/b&gt;" in footer
