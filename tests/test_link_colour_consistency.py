"""Every in-content link resolves through one colour token.

Three sources were in play at once: the theme accent (attractions, events,
drives), a fixed canyon brown (restaurants, tips, lunch) and a hardcoded
#c0714a (the route link). One card could show blue, brown and orange links
side by side, and the mix shifted per trip because the accent does -- green
on Old Hickory, blue on Europe, terracotta on the Southwest.
"""

import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "v2.5_template.html"
CSS = TEMPLATE.read_text(encoding="utf-8")

LINK_CLASSES = [
    "attr-link", "rest-link", "event-link", "tip-link", "lunch-link",
    "gmaps-link", "drive-link",
]


def _rule(selector):
    m = re.search(r"^\s*\." + selector + r"\s*\{([^}]*)\}", CSS, re.M)
    assert m, f"no rule for .{selector}"
    return " ".join(m.group(1).split())


#: Anchors that deliberately are not link-coloured.
#:   .attr-external-link -- a designator, muted so it does not read as a control
#:   .badge-map          -- a control with its own blue affordance
#:   a[href]::after      -- the print stylesheet's URL display, grey by design
_NOT_CONTENT_LINKS = (".attr-external-link", ".badge-map", "a[href]::after")


def _anchor_rules_with_colour():
    """Every rule that colours an anchor, however it selects one.

    A fixed list of class names missed the rules that select anchors by
    descent -- .rest-name a, .lodging-val a, .links-list li a,
    .group-base-pointer a -- which stayed canyon brown while the seven named
    classes went to the token. That is what "links still inconsistent" meant.
    """
    css = CSS[: CSS.index("</style>")]
    out = {}
    for m in re.finditer(r"([^{}]*)\{([^}]*)\}", css):
        sel = m.group(1).strip().splitlines()[-1].strip()
        if not re.search(r"(^|[\s,>])a|a\.|link", sel, re.I):
            continue
        if sel.startswith("/*") or sel.startswith(_NOT_CONTENT_LINKS):
            continue
        colour = re.search(r"(?<!-)color:\s*([^;]+)", m.group(2))
        if colour:
            out[sel] = colour.group(1).strip()
    return out


def test_every_link_class_uses_the_shared_token():
    wrong = {c: _rule(c) for c in LINK_CLASSES if "var(--link)" not in _rule(c)}
    assert not wrong, f"not using var(--link): {wrong}"


def test_every_anchor_rule_uses_the_shared_token():
    """The broader question the class list could not answer."""
    wrong = {s: v for s, v in _anchor_rules_with_colour().items() if v != "var(--link)"}
    assert not wrong, f"anchor rules off the token: {wrong}"


def test_no_link_class_hardcodes_a_colour():
    """#c0714a on the route link was invisible in the token system."""
    for cls in LINK_CLASSES:
        assert not re.search(r"color:\s*#[0-9a-fA-F]{3,6}", _rule(cls)), cls


def test_the_token_is_defined_from_the_theme():
    assert re.search(r"--link:\s*<!--THEME_COLOR-->;", CSS)


def test_no_link_class_is_declared_twice():
    """.drive-link had two rules; the earlier one silently lost the cascade."""
    for cls in LINK_CLASSES:
        assert len(re.findall(r"^\s*\." + cls + r"\s*\{", CSS, re.M)) == 1, cls
