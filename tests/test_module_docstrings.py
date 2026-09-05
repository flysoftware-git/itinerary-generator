"""Module docstrings must not describe code that is not there.

`design.md` Appendix B carried a docstring-drift entry for three modules at
once: html_assembler said it injected an "attribution block" six versions
after that block was replaced, image_fetcher described a two-provider chain
and a base64 data-URI mode that never existed, and main.py's flag list named
18 of 30 options while the note recording the gap said eight of 26. Every one
was found by reading, none by a test, and the main.py figure had drifted twice
before anyone noticed the note was stale too.

These are the two claims worth binding. Not "the docstring is accurate" --
prose cannot be asserted -- but the two specific shapes that rotted: a
docstring naming a flag that no longer exists, and a docstring promising an
enumeration it does not deliver.
"""
import re
from pathlib import Path

import pytest

GENERATOR = Path(__file__).parent.parent / "generator"


def _module_docstring(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if not text.startswith('"""'):
        return ""
    return text[3:text.index('"""', 3)]


def _click_options(text: str) -> set[str]:
    """Every flag main.py actually declares, long form."""
    found: set[str] = set()
    for match in re.finditer(r'@click\.option\(\s*"([^"]+)"(?:\s*,\s*"(--[^"]+)")?', text):
        first, second = match.group(1), match.group(2)
        name = second if (second and not first.startswith("--")) else first
        # "--trails/--no-trails" declares two spellings of one option.
        found.update(part for part in name.split("/") if part.startswith("--"))
    return found


def test_main_docstring_names_no_flag_that_does_not_exist():
    """A renamed or deleted flag leaves the docstring quietly lying. This is
    the half of accuracy a test can actually hold: every flag NAMED here must
    be real. Whether the list is complete is deliberately not asserted -- see
    the next test for why."""
    main_py = GENERATOR / "main.py"
    text = main_py.read_text(encoding="utf-8")
    doc = _module_docstring(main_py)

    # A trailing-hyphen token like "--no-" is prose describing a family of
    # flags, not the name of one, so the pattern requires a final word char.
    named = set(re.findall(r"--[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", doc))
    real = _click_options(text)
    # --help is click's own and is never declared as an option.
    named.discard("--help")

    assert named, "the docstring should still describe the flag surface"
    assert not (named - real), f"docstring names flags that do not exist: {sorted(named - real)}"


def test_main_docstring_does_not_promise_a_complete_flag_list():
    """It enumerated them once and was wrong twice -- 18 of 30, under a note
    claiming eight of 26 were missing. A list that must be updated by hand on
    every new option is a list that will be stale again, so the docstring
    points at --help and describes the shape instead. If someone restores the
    enumeration, this fails and asks them to reconsider."""
    doc = _module_docstring(GENERATOR / "main.py")
    text = (GENERATOR / "main.py").read_text(encoding="utf-8")

    named = set(re.findall(r"--[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", doc)) - {"--help"}
    real = _click_options(text)

    assert "--help" in doc, "the docstring must point at the list that cannot go stale"
    assert len(named) < len(real), (
        "the docstring appears to enumerate every flag again; that is the shape "
        "that rotted twice"
    )


@pytest.mark.parametrize("phrase", ["base64", "data URI", "data-uri"])
def test_image_fetcher_does_not_claim_a_data_uri_mode_it_lacks(phrase):
    """The docstring promised images "embedded as data URIs in the HTML
    (base64) OR stored as relative paths depending on config". Neither half
    was true: nothing emits a data URI, and the page loads each image from its
    source url with the local cache used only as a fallback."""
    image_fetcher = GENERATOR / "image_fetcher.py"
    text = image_fetcher.read_text(encoding="utf-8")
    doc = _module_docstring(image_fetcher)

    if phrase.lower() in doc.lower():
        # Mentioning it to say it does not exist is fine; implementing it is
        # fine. Claiming it while not implementing it is the defect.
        body = text[len(doc) + 6:]
        assert re.search(r"b64encode|data:image", body), (
            f"docstring mentions {phrase!r} but nothing in the module produces one"
        )


def test_image_fetcher_docstring_names_every_provider_it_calls():
    """It named two of three for long enough that a run reporting zero
    Unsplash calls looked like a configuration fault rather than a provider
    that had silently stopped being reached."""
    image_fetcher = GENERATOR / "image_fetcher.py"
    text = image_fetcher.read_text(encoding="utf-8")
    doc = _module_docstring(image_fetcher).lower()

    providers = {
        name.lower()
        for name in re.findall(r"def _fetch_from_(\w+?)(?:_once)?\(", text)
    }
    assert providers, "expected _fetch_from_* provider methods"
    missing = [p for p in providers if p not in doc]
    assert not missing, f"docstring does not mention provider(s): {sorted(missing)}"
