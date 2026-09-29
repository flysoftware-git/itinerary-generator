"""A guide is failed for defects, not for absences the operator asked for.

`--skip-images` is a documented cheap run: it exists so a build can be iterated
without paying for pictures. Every destination it produces carries zero images,
which is exactly what was asked for -- and the image-count check reported each
one as an error, so `main` turned the report into `validation_failed` and exited
`2`. **Every time.** The documented cheap path could not succeed.

Reproduced with no network and no provider call, by handing the validator a trip
whose destinations carry no images -- the state `--skip-images` leaves them in --
against the committed `config.yaml`: one error per destination.

The sharp form of it is that the same run already knew better one gate earlier.
`main` only counts an image shortfall `if not skip_images`, so the run-quality
path was deliberately taught that zero images is not a shortfall when the
operator said to skip them, while the gate immediately after it was taught the
opposite. This brings the two into agreement rather than inventing a rule.

**Why it is worth a fix and not a footnote.** An exit code that is always `2`
stops discriminating. An operator running the cheap path cannot tell a real
failure from this one, which is worse than having no check: the instrument is
not wrong, it is stuck, and a stuck instrument is read as noise until the day it
was right.
"""
import json

from generator.html_validator import HTMLValidator


NO_PICTURES = {
    "destinations": [
        {"id": "zion", "name": "Zion National Park", "images": [],
         "scenic_drives": []},
        {"id": "sedona", "name": "Sedona", "images": [], "scenic_drives": []},
    ]
}

BARE_HTML = """<!DOCTYPE html>
<html><head><title>Test</title></head>
<body>
<section id="section-zion" class="destination-section">
  <div class="dest-header"><div class="inner"></div></div>
</section>
<section id="section-sedona" class="destination-section">
  <div class="dest-header"><div class="inner"></div></div>
</section>
<div id="drive-modal-body"></div>
</body></html>"""


def _validator(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("images:\n  min_per_destination: 2\n  max_per_destination: 4\n")
    return HTMLValidator(str(cfg))


def _page(tmp_path):
    page = tmp_path / "index.html"
    page.write_text(BARE_HTML, encoding="utf-8")
    return page


def test_a_run_that_skipped_images_is_not_failed_for_having_none(tmp_path):
    """The defect itself: this is what `--skip-images` produces."""
    report = _validator(tmp_path).validate(
        _page(tmp_path), NO_PICTURES, skipped=["images"])

    assert report["valid"], (
        "a run that was told not to fetch images was failed for not having "
        "them -- which makes the documented cheap path always exit 2: "
        + json.dumps(report["errors"]))
    assert not any("image(s)" in e for e in report["errors"])


def test_a_run_that_wanted_images_and_has_none_still_fails(tmp_path):
    """The other direction, and the reason this is not simply deleting a check.

    A delivered guide with no pictures IS broken. The check was never wrong
    about that; it just could not see an operator who had said they did not
    want them.
    """
    report = _validator(tmp_path).validate(_page(tmp_path), NO_PICTURES)

    assert not report["valid"], "a guide that should have pictures and has none passed"
    assert sum("image(s)" in e for e in report["errors"]) == 2, report["errors"]


def test_skipping_something_else_does_not_excuse_missing_images(tmp_path):
    """`skipped` is read per stage, not as a blanket amnesty.

    Without this, any skip flag would silence every check -- which is the same
    defect one layer up and considerably harder to notice.
    """
    report = _validator(tmp_path).validate(
        _page(tmp_path), NO_PICTURES, skipped=["events", "url_discovery"])

    assert not report["valid"]
    assert sum("image(s)" in e for e in report["errors"]) == 2, report["errors"]


def test_a_caller_that_says_nothing_behaves_exactly_as_before(tmp_path):
    """The default keeps every existing caller unchanged."""
    said_nothing = _validator(tmp_path).validate(_page(tmp_path), NO_PICTURES)
    said_empty = _validator(tmp_path).validate(
        _page(tmp_path), NO_PICTURES, skipped=())

    assert said_nothing["errors"] == said_empty["errors"]
    assert not said_nothing["valid"]
