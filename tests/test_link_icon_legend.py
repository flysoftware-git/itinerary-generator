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
    assert "not whether it was checked" in html
    # Inside the collapsed "About the links" disclosure, not on the footer face.
    notes = html[html.index('<details class="link-notes"'):]
    assert notes.index("<summary") < notes.index(_LEGEND) < notes.index("</details>")


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

    assert '<span class="attr-external-link" title="opens the trail page">' in card


def test_each_icon_says_what_it_opens():
    """Every icon's hover text said "opens the source page", the 🗺️ and 🥾 included."""
    a = _assembler()
    assert 'title="opens the trail page">🥾<' in a._link_icon_html("https://www.alltrails.com/trail/us/utah/x")
    assert 'title="opens in Google Maps">🗺️<' in a._link_icon_html(
        "https://www.google.com/maps/search/?api=1&query=36.28%2C-86.66"
    )
    assert 'title="opens the source page">🔗<' in a._link_icon_html("https://www.example-grill.com/")


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
    assert "How many links could be checked is below." in with_report
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


# ── Owner direction, 2026-09-14: link detail off the footer face ────────────


def test_link_detail_is_behind_a_collapsed_disclosure():
    """"Showing this detail is not recommended on the guide footer itself,
    perhaps another link accessible to the user could provide that info."

    A native <details>: no script, so it works offline, and collapsed by
    default -- no `open` attribute -- so the footer face shows one quiet
    "About the links" and nothing else about link checking.
    """
    footer = _assembler()._build_generator_footer(_trip(_REPORT), has_link_icons=True)

    assert '<details class="link-notes"' in footer
    details = footer[footer.index('<details class="link-notes"'):footer.index("</details>")]
    assert " open" not in details.split(">", 1)[0]
    assert "<summary" in details and ">About the links</summary>" in details
    assert '<div class="link-liveness-note"' in details
    # Nothing about link checking outside it.
    outside = footer.replace(footer[footer.index('<details class="link-notes"'):footer.index("</details>") + len("</details>")], "")
    for phrase in ("fetched and found working", "could not be reached", "not whether it was checked"):
        assert phrase not in outside


def test_no_disclosure_when_there_is_nothing_to_disclose():
    footer = _assembler()._build_generator_footer(_trip(), has_link_icons=False)

    assert "link-notes" not in footer
    assert "About the links" not in footer
