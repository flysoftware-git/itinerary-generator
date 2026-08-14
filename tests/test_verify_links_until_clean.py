from pathlib import Path

from scripts.verify_links_until_clean import _count_rendered_items_without_links


def test_count_rendered_items_without_links_counts_plaintext_cards(tmp_path: Path) -> None:
    html = "".join(
        [
            '<div class="attr-item"><span class="attr-name">Plain Attraction</span></div>',
            '<div class="attr-item"><span class="attr-name"><a href="https://example.com/a">Linked Attraction</a></span></div>',
            '<div class="rest-item"><span class="rest-name">Plain Restaurant</span></div>',
            '<div class="stop-card"><div class="stop-body"><strong>Plain Stop</strong></div></div>',
            '<div class="stop-card"><div class="stop-body"><strong><a href="https://example.com/s">Linked Stop</a></strong></div></div>',
        ]
    )
    path = tmp_path / "index.html"
    path.write_text(html, encoding="utf-8")

    counts = _count_rendered_items_without_links(path)

    assert counts == {
        "attractions": 1,
        "restaurants": 1,
        "en_route_stops": 1,
    }