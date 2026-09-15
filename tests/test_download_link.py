"""The guide's download control is a plain link, not a file built in the browser.

The header button built a Blob of `document.documentElement.outerHTML`, created
an <a> element and clicked it. That is the construction mail scanners treat as
HTML smuggling, and PR #126 had already measured a mail provider refusing an
attached guide on the strength of a similar in-browser build. The template also
built a Blob of a PWA manifest on file:// -- where no app can be installed -- and
pointed the manifest <link> at it. Both are gone.

The button also saved every guide as "southwest-road-trip-itinerary.html". The
link's file name now comes from the trip's title.
"""

import re

import pytest

from generator.html_assembler import HTMLAssembler


def _trip(title: str) -> dict:
    return {
        "trip": {"title": title},
        "_meta": {"generator_version": "9.9.9", "generated_at_utc": "2026-07-26T17:41:23+00:00"},
        "destinations": [],
    }


@pytest.fixture(scope="module")
def page() -> str:
    return HTMLAssembler(config_path="config.yaml").assemble(_trip("Old Hickory & Asheville"))


def _scripts(html: str) -> str:
    return "\n".join(re.findall(r"<script\b[^>]*>(.*?)</script>", html, flags=re.S))


@pytest.mark.parametrize(
    "construction",
    ["new Blob", "createObjectURL", "createElement(", ".download =", "outerHTML"],
)
def test_no_script_on_the_page_builds_a_file(page, construction):
    assert construction not in _scripts(page), construction


def test_the_download_control_is_a_plain_link_to_the_page_itself(page):
    link = re.search(r'<a [^>]*id="print-btn-a1b2"[^>]*>', page)
    assert link, "download link missing"
    tag = link.group(0)
    assert 'href=""' in tag
    assert 'download="old-hickory-asheville-itinerary.html"' in tag
    assert 'aria-label="Download this itinerary"' in tag
    assert 'title="Download this itinerary"' in tag


def test_the_download_control_is_an_icon_not_a_text_button(page):
    """Owner request: it was too big; use the standard download icon."""
    start = page.index('id="print-btn-a1b2"')
    control = page[start: page.index("</a>", start)]
    assert "<svg" in control and 'aria-hidden="true"' in control
    visible_text = re.sub(r"<[^>]+>", "", control.split(">", 1)[1]).strip()
    assert visible_text == "", f"visible text in the control: {visible_text!r}"
    assert "Download to Print" not in page


def test_no_guide_is_named_after_the_southwest_trip(page):
    assert "southwest-road-trip-itinerary" not in page


def test_no_placeholder_is_left_behind(page):
    assert "<!--DOWNLOAD_FILENAME-->" not in page


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Old Hickory & Asheville", "old-hickory-asheville-itinerary.html"),
        ("East Coast Greenway — Portland to New Haven", "east-coast-greenway-portland-to-new-haven-itinerary.html"),
        ("Pacific Crest Trail — Oregon and Washington", "pacific-crest-trail-oregon-and-washington-itinerary.html"),
        ("Łódź & Kraków", "odz-krakow-itinerary.html"),
        ("", "itinerary.html"),
        ("———", "itinerary.html"),
    ],
)
def test_the_file_name_is_the_trip_title_as_a_slug(title, expected):
    assert HTMLAssembler._download_filename(title) == expected


def test_a_long_title_stays_a_usable_name():
    name = HTMLAssembler._download_filename("A " * 200)
    assert name.endswith("-itinerary.html") and len(name) <= 60 + len("-itinerary.html")
    assert "--" not in name
