"""Every content link must carry the class that styles it.

The Europe build rendered 72 links in default browser blue. The CSS defined
FIVE link classes -- .attr-link, .attraction-link, .event-link, .rest-link,
.restaurant-link -- while html_assembler emitted only two. Restaurant and
en-route anchors carried no class at all.

.attraction-link and .restaurant-link were never emitted once in the project's
history. Two near-identical names for one job is how this hid: a search for
"restaurant-link" found a rule, so the styling looked present.
"""
import re
from pathlib import Path

import pytest

TEMPLATE = Path("templates/v2.5_template.html")
ASSEMBLER = Path("generator/html_assembler.py")


def _css_classes(text: str) -> set[str]:
    return set(re.findall(r"^\s*\.([a-z-]+link)\s*\{", text, re.M))


def _emitted_classes(text: str) -> set[str]:
    """Classes actually applied to markup, from Python OR the template's own JS.

    A first version matched only `class="one-link"` and so missed
    `class="event-link drive-route-map-link"`, reporting two live classes as
    dead. It also looked in html_assembler alone, while the drive modal builds
    its links in template JavaScript.
    """
    found: set[str] = set()
    for attr in re.findall(r'class="([^"]+)"', text):
        found.update(c for c in attr.split() if c.endswith("link"))
    # Classes referenced by selector rather than written into markup.
    found.update(re.findall(r"querySelectorAll\('\.([a-z-]+link)'\)", text))
    return found


class TestNoDeadOrMissingLinkClasses:
    def test_every_link_class_in_the_css_is_actually_emitted(self):
        """Dead CSS is not harmless here -- it made the missing styling look
        present to anyone who grepped for it."""
        template = TEMPLATE.read_text(encoding="utf-8")
        css = _css_classes(template)
        emitted = _emitted_classes(ASSEMBLER.read_text(encoding="utf-8")) | _emitted_classes(template)
        dead = css - emitted
        assert not dead, f"defined in CSS but never emitted: {sorted(dead)}"

    def test_every_emitted_link_class_is_styled(self):
        """The inverse: a class the CSS does not define renders unstyled."""
        template = TEMPLATE.read_text(encoding="utf-8")
        css = _css_classes(template)
        emitted = _emitted_classes(ASSEMBLER.read_text(encoding="utf-8")) | _emitted_classes(template)
        assert not (emitted - css), f"emitted but unstyled: {sorted(emitted - css)}"


class TestTheFourContentLinkKinds:
    @pytest.mark.parametrize("cls", ["attr-link", "rest-link", "event-link"])
    def test_the_class_is_emitted(self, cls):
        assert f'class="{cls}"' in ASSEMBLER.read_text(encoding="utf-8")

    @pytest.mark.parametrize("cls", ["attr-link", "rest-link", "event-link"])
    def test_the_class_is_styled(self, cls):
        assert re.search(rf"^\s*\.{cls}\s*\{{", TEMPLATE.read_text(encoding="utf-8"), re.M)

    def test_restaurant_anchors_are_classed(self):
        """The 20 TripAdvisor links that rendered blue."""
        src = ASSEMBLER.read_text(encoding="utf-8")
        assert 'class="rest-link" target="_blank"' in src

    def test_en_route_anchors_are_classed(self):
        src = ASSEMBLER.read_text(encoding="utf-8")
        assert src.count('class="attr-link" target="_blank"') >= 2
