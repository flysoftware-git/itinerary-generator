"""config.yaml is UTF-8, and every reader has to say so.

`Path(config_path).open()` with no encoding decodes the file in the *locale*
encoding. That is UTF-8 on most Linux and macOS machines and cp1252 on a default
Windows install, so the same config.yaml loaded two different ways depending on
where the generator ran -- and nothing in a Linux CI run could see it.

It had two symptoms, and the second is the worse one:

  * **Mojibake.** A "©" in the file is the bytes C2 A9. Read as cp1252 that is
    two characters, "Â©", so the map credited "Â© OpenStreetMap contributors".
    That became visible when the tile attribution moved out of the template and
    into config.yaml, and it shipped in every page generated on such a machine.
  * **A crash.** cp1252 leaves five byte values undefined. "Łódź" encodes to
    C5 81 ..., and 0x81 is one of them, so a config naming it did not load
    wrongly -- it raised UnicodeDecodeError before anything was generated.

Both tests force the cp1252 default on any platform, so they fail on the defect
wherever the suite runs, rather than only on the machines that had the bug.
"""

from __future__ import annotations

import pathlib
from pathlib import Path

import pytest

COPYRIGHT = "©"
LODZ = "Łódź"  # C5 81 C3 B3 64 C5 BA -- 0x81 is undefined in cp1252


@pytest.fixture
def cp1252_locale(monkeypatch):
    """Make a text-mode open() with no encoding decode as a cp1252 locale would.

    Binary opens and opens that name an encoding are untouched, so this changes
    exactly one thing: what "no encoding given" means.
    """
    real_open = pathlib.Path.open

    def open_as_cp1252(self, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        if "b" not in mode and encoding is None:
            encoding = "cp1252"
        return real_open(self, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(pathlib.Path, "open", open_as_cp1252)


def _utf8_config(tmp_path: Path, *, prefix: str = "", tiles: dict | None = None) -> str:
    """The shipped config.yaml, with non-ASCII written as real UTF-8 bytes.

    `allow_unicode=True` is load-bearing. yaml.safe_dump's default escapes "©" to
    the ASCII sequence \\xA9, and an all-ASCII file decodes identically in every
    encoding -- so a fixture built the default way passes with the defect still
    present. The assertion below makes that failure loud instead of silent.
    """
    import yaml

    data = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    if tiles is not None:
        data["map"] = {"tiles": tiles}
    raw = (prefix + yaml.safe_dump(data, allow_unicode=True, sort_keys=False)).encode("utf-8")
    assert any(byte > 0x7F for byte in raw), (
        "this fixture has no non-ASCII bytes, so it cannot exercise the decoding defect"
    )
    path = tmp_path / "config.yaml"
    path.write_bytes(raw)
    return str(path)


def _trip() -> dict:
    return {
        "trip": {"title": "Test Trip", "theme_color": "#C0623E"},
        "_meta": {
            "generator_version": "test",
            "template_version": "test",
            "generated_at_utc": "2026-07-24T00:00:00+00:00",
            "llm": {"provider": "openai", "model": "test",
                    "usage": {"models": [], "total_estimated_cost_usd": 0.0}},
        },
        "destinations": [],
    }


def test_a_copyright_sign_in_config_reaches_the_page_as_one(tmp_path, cp1252_locale) -> None:
    from generator.html_assembler import HTMLAssembler

    config = _utf8_config(tmp_path, tiles={
        "url": "https://tiles.example.com/{z}/{x}/{y}.png",
        "attribution": f"{COPYRIGHT} Example contributors",
    })

    html = HTMLAssembler(config_path=config).assemble(_trip())

    assert f"{COPYRIGHT} Example contributors" in html
    assert f"Â{COPYRIGHT}" not in html, "the credit was decoded as cp1252"


@pytest.mark.parametrize("reader", [
    "html_assembler", "html_validator", "image_fetcher", "ai_content",
])
def test_every_config_reader_loads_a_non_latin1_config(tmp_path, cp1252_locale, reader) -> None:
    """A place name the locale codec cannot decode must not stop the run."""
    config = _utf8_config(tmp_path, prefix=f"# {LODZ}, Kraków {COPYRIGHT}\n")

    if reader == "html_assembler":
        from generator.html_assembler import HTMLAssembler
        HTMLAssembler(config_path=config)
    elif reader == "html_validator":
        from generator.html_validator import HTMLValidator
        HTMLValidator(config_path=config)
    elif reader == "image_fetcher":
        from generator.image_fetcher import ImageFetcher
        ImageFetcher(config_path=config, output_dir=tmp_path / "images")
    else:
        from generator.ai_content import AIContentGenerator
        AIContentGenerator(config_path=config, llm_client=object())
