"""A designator must not look like a control, and a control must look like one.

Two icons sit side by side on a card. One is a <span> saying what KIND of
source the title link is; the other is an <a> that opens Google Maps. When the
source happens to be a Maps URL both rendered the same glyph, and the
designator carried a hover background that implied it was clickable -- so it
read as a second, broken link. The clickable one was also the smaller of the
two: .badge-map inherited .badge's 12px while the designator rendered at
0.82rem.
"""

import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "v2.5_template.html"
CSS = TEMPLATE.read_text(encoding="utf-8")


def _rule(selector):
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert m, f"no rule for {selector}"
    return " ".join(m.group(1).split())


def test_the_designator_does_not_present_as_clickable():
    rule = _rule(".attr-external-link")
    assert "cursor: default" in rule


def test_the_designator_has_no_hover_state():
    """A hover background on a non-control is the thing that misled."""
    assert ".attr-external-link:hover" not in CSS


def test_the_map_control_is_larger_than_the_designator():
    control = _rule(".badge-map")
    size = re.search(r"font-size:\s*(\d+)px", control)
    assert size and int(size.group(1)) >= 15, control


def test_the_map_control_keeps_a_pointer_and_hover():
    assert "cursor: pointer" in _rule(".badge-map")
    assert ".badge-map:hover" in CSS


def test_the_map_control_is_keyboard_focusable():
    assert ".badge-map:focus-visible" in CSS
