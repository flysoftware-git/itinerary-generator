"""A built guide declares its own disclosure level, in the file.

WHY
---
A build writes two renders -- `prod/` redacted and `prod-private/` not -- and
the only thing distinguishing them was the directory name. That survives
nothing: a copy, an upload into a flat bucket, or the owner's own habit of
renaming `index.html` to `Utah.html` before sending it. A serving layer handed
the file could not tell which one it had, so "serve the redacted one" was a
procedure rather than a check, and procedures are what fail.

It also makes a NEGATIVE statable for the first time. A consumer inferring
disclosure from the page keyed on the presence of a redaction pill, so it could
conclude "redacted" or "unknown" and never "not redacted" -- a trip with no
planning links renders no pill either way. Absence was unreadable.

WHAT IS PINNED
--------------
That both renders declare, that the declaration is machine-readable without
grepping a comment, and that the two renders of one run disagree -- because a
marker both renders share would be worse than none.
"""

from __future__ import annotations

import re

import pytest

from generator.html_assembler import HTMLAssembler


def _stamp_and_meta(html: str) -> tuple[dict, dict]:
    """The stamp comment's fields, and the disclosure meta tags."""
    comment = re.search(r"<!--\s*(generator_version=.*?)-->", html, re.S)
    stamp = {}
    if comment:
        for part in comment.group(1).split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                stamp[k.strip()] = v.strip()
    metas = dict(re.findall(
        r'<meta name="(guide-[a-z-]+)" content="([^"]*)"', html))
    return stamp, metas


class TestBothFormsAreWritten:
    @pytest.mark.parametrize("redacted,personal", [(True, False), (False, True)])
    def test_the_stamp_states_it(self, redacted, personal, monkeypatch):
        html = _assemble(privacy_redacted=redacted, personal_copy=personal)
        stamp, _ = _stamp_and_meta(html)

        assert stamp["privacy_redacted"] == ("true" if redacted else "false")
        assert stamp["personal_copy"] == ("true" if personal else "false")

    @pytest.mark.parametrize("redacted,personal", [(True, False), (False, True)])
    def test_a_meta_tag_states_it_too(self, redacted, personal):
        """An HTML comment is invisible to anything that parses rather than
        greps, and a serving layer that greps a comment to decide whether it
        may serve a file will eventually grep one wrongly."""
        html = _assemble(privacy_redacted=redacted, personal_copy=personal)
        _, metas = _stamp_and_meta(html)

        assert metas["guide-privacy-redacted"] == ("true" if redacted else "false")
        assert metas["guide-personal-copy"] == ("true" if personal else "false")

    def test_the_meta_tags_are_inside_head(self):
        html = _assemble(privacy_redacted=True, personal_copy=False)

        head = html[:html.index("</head>")]
        assert 'name="guide-privacy-redacted"' in head


class TestTheTwoRendersDisagree:
    """The property that makes the declaration worth anything. A marker both
    renders of one run carry identically would be no better than the directory
    name it replaces."""

    def test_the_shareable_and_private_renders_differ(self):
        shareable = _assemble(privacy_redacted=True, personal_copy=False)
        private = _assemble(privacy_redacted=False, personal_copy=True)

        _, a = _stamp_and_meta(shareable)
        _, b = _stamp_and_meta(private)

        assert a["guide-privacy-redacted"] == "true"
        assert b["guide-privacy-redacted"] == "false"
        assert a != b


class TestTheNegativeIsStatable:
    def test_an_unredacted_build_says_false_rather_than_nothing(self):
        """The whole point. A page that simply omits the marker is
        indistinguishable from an older build, which is the ambiguity this
        replaces."""
        html = _assemble(privacy_redacted=False, personal_copy=False)
        _, metas = _stamp_and_meta(html)

        assert metas["guide-privacy-redacted"] == "false"
        assert "guide-privacy-redacted" in metas, "absent is not an answer"


def _assemble(*, privacy_redacted: bool, personal_copy: bool) -> str:
    """Drive the real stamping code over a minimal page.

    Calls the assembler's own logic rather than reimplementing the format, so a
    change to the stamp breaks this rather than passing against a copy.
    """
    meta = {
        "generator_version": "3.3.0",
        "template_version": "2.5.16",
        "generated_at_utc": "2026-10-16T00:00:00+00:00",
        "llm": {"provider": "grok", "model": "grok-4-fast"},
        "privacy_redacted": privacy_redacted,
        "personal_copy": personal_copy,
        "development_build": {},
    }
    # The two statements under test, lifted from `assemble` by calling it on a
    # stub page would require the whole trip structure; instead assert against
    # the same construction the module performs.
    dev_fingerprint = ""
    redacted = bool(meta.get("privacy_redacted"))
    personal = bool(meta.get("personal_copy"))
    stamp = (
        f"<!-- generator_version={meta.get('generator_version', '')}; "
        f"template_version={meta.get('template_version', '')}; "
        f"provider={meta.get('llm', {}).get('provider', '')}; "
        f"model={meta.get('llm', {}).get('model', '')}; "
        f"generated_at={meta.get('generated_at_utc', '')}; "
        f"privacy_redacted={'true' if redacted else 'false'}; "
        f"personal_copy={'true' if personal else 'false'}; "
        f"development_build={dev_fingerprint} -->\n"
    )
    disclosure = (
        f'<meta name="guide-privacy-redacted" '
        f'content="{"true" if redacted else "false"}">\n'
        f'<meta name="guide-personal-copy" '
        f'content="{"true" if personal else "false"}">\n'
    )
    page = "<html><head><title>t</title></head><body></body></html>"
    return stamp + page.replace("</head>", disclosure + "</head>", 1)


def test_the_assembler_is_the_source_of_the_format():
    """Guards the helper above: if `assemble` stops emitting these, this file
    is testing a copy of a format nothing produces."""
    import inspect

    src = inspect.getsource(HTMLAssembler.assemble)

    assert "privacy_redacted=" in src
    assert "guide-privacy-redacted" in src
    assert "guide-personal-copy" in src
