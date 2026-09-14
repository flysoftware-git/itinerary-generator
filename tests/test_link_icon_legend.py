"""The footer explains the small icon after a card's link, and only when one is shown.

`_link_source_icon` marks what kind of page a link opens (a trail page, a map,
anything else). Live, unchecked and dead links all render the same icon, so the
icon cannot mean "this link was confirmed working" -- and nothing on the page
said what it did mean. The legend says so, and points at the footer's liveness
statement, which is the only place the page reports link checking.
"""

import re

from generator.html_assembler import HTMLAssembler

_LEGEND = '<div class="link-icon-legend"'

_REPORT = {
    "counts": {"live": 2, "dead": 0, "unchecked": 1},
    "published_count": 3,
    "unchecked_by_domain": {"www.yelp.com": 1},
}


def _assembler() -> HTMLAssembler:
    return HTMLAssembler.__new__(HTMLAssembler)


def _trip(report: dict | None = None) -> dict:
    trip = {
        "trip": {"title": "Test Trip"},
        "_meta": {
            "generator_version": "9.9.9",
            "generated_at_utc": "2026-07-26T17:41:23+00:00",
        },
        "destinations": [],
    }
    if report is not None:
        trip["_link_liveness"] = report
    return trip


def _card_with_link_icon(assembler: HTMLAssembler) -> str:
    # A real card fragment, rendered by the assembler's own code path.
    return assembler._build_leg_trail_link_html(
        {"trail_url": "https://www.alltrails.com/trail/us/utah/angels-landing", "trail_label": "Angels Landing"}
    )


def _page(body: str) -> str:
    return f"<html><body>\n{body}\n</body></html>"


def test_a_guide_with_a_link_icon_explains_it():
    assembler = _assembler()
    card = _card_with_link_icon(assembler)

    html = assembler._inject_generator_footer(_page(card), _trip())

    assert _LEGEND in html
    assert "<strong>About the link icons.</strong>" in html
    assert "not whether it was checked" in html


def test_a_guide_without_a_link_icon_has_no_legend():
    assembler = _assembler()

    html = assembler._inject_generator_footer(_page("<p>No links here.</p>"), _trip(_REPORT))

    assert _LEGEND not in html
    assert "About the link icons." not in html


def test_the_stylesheet_naming_the_class_does_not_count_as_an_icon():
    """The template's CSS selects `.attr-external-link`; a guide with no cards
    must still get no legend, so presence is matched on the attribute."""
    assembler = HTMLAssembler(config_path="config.yaml")

    html = assembler.assemble(_trip(_REPORT))

    assert ".attr-external-link" in html
    assert _LEGEND not in html


def test_the_card_icon_keeps_its_class():
    card = _card_with_link_icon(_assembler())

    # A trail card: the title is the legend's own words for 🥾.
    assert '<span class="attr-external-link" title="opens a trail page">' in card


def test_the_legend_never_claims_a_link_was_verified():
    assembler = _assembler()
    for report in (None, _REPORT):
        footer = assembler._build_generator_footer(_trip(report), has_link_icons=True)
        legend = footer[footer.index(_LEGEND):]
        legend = legend[: legend.index("</div>")]
        text = re.sub(r"<[^>]+>", "", legend).lower()

        for claim in ("verified", "confirmed", "working", "valid", "tested", "checked and"):
            assert claim not in text, f"legend says {claim!r}: {text!r}"


def test_the_legend_points_below_only_when_the_statement_is_there():
    assembler = _assembler()

    with_report = assembler._build_generator_footer(_trip(_REPORT), has_link_icons=True)
    assert "how many links could be checked is stated below" in with_report
    # Directly above the statement it points at.
    assert with_report.index(_LEGEND) < with_report.index('<div class="link-liveness-note"')

    without = assembler._build_generator_footer(_trip(), has_link_icons=True)
    assert _LEGEND in without
    assert "below" not in without


def test_the_footer_is_unchanged_when_no_icon_is_shown():
    assembler = _assembler()

    assert assembler._build_generator_footer(_trip(_REPORT)) == (
        assembler._build_generator_footer(_trip(_REPORT), has_link_icons=False)
    )
    assert _LEGEND not in assembler._build_generator_footer(_trip(_REPORT))
