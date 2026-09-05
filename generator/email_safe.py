"""email_safe.py — a script-free variant of the itinerary, for attaching to mail.

Why
---
Gmail refused index.html as an attachment, reporting a virus. Nothing malicious
is in the file: no eval, no base64 payload, no iframe, no obfuscation. What it
carried was the shape generic heuristics score as Trojan:HTML/Phish -- inline
`onerror` handlers (removed separately) beside three remote `<script src>` loads
pulling executable code from third-party CDNs.

Removing the inline handlers was not enough on its own, because the CDN loads
remain and Gmail inspects HTML attachments closely. **Any** script in an HTML
attachment is a realistic trigger, so the reliable answer is a file with none.

What this is not
----------------
Not a replacement for index.html. The full artifact keeps its interactive map
and stays the thing to publish or open locally. This is a second file for the
one job the full one cannot do: survive a mail scanner.

What is lost, precisely
-----------------------
Measured against the 2026-08-27 five-city Europe build:

  * **Leaflet map** -- one `#overview-map` div. Replaced by a link to the same
    route in Google Maps, so the information survives even though the embedded
    map does not.
  * **Lucide icons** -- `data-lucide` appears 0 times. Nothing is lost.
  * **Tailwind CDN** -- 21 distinct utility classes across ~30 uses. Replaced
    by static CSS below, so layout is preserved.
  * **Inline behaviour** -- ~17KB across four blocks: the image error handler,
    drive-description modals, and map wiring. Images that fail now show the
    browser's own broken-image placeholder rather than hiding; everything else
    is either map-related or progressive enhancement.

The trade is deliberate: an attachment that arrives and reads correctly beats
one that is quarantined.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: Custom palette from the template's tailwind.config, inlined so the utility
#: classes below resolve without the CDN compiling them in the browser.
#:
#: "terracotta" is the per-trip accent (the template fills it from the
#: manifest's theme_color), so the value here is only a fallback -- the real
#: one is read out of the page being converted by _accent_from(). Hardcoding
#: it shipped Europe's rail blue in the Southwest email for one release.
_PALETTE = {
    "terracotta": "#C0623E",
    "sandstone": "#F5EFE0",
    "canyon": "#8B2E0A",
    "sage": "#6B8C6B",
    "offwhite": "#FAFAF7",
    "dark": "#2C1A0E",
}

#: Only the utilities the rendered page actually uses. Deliberately not a
#: Tailwind subset -- shipping a general framework to satisfy 30 attributes is
#: how the file got large enough to look suspicious in the first place.
_UTILITY_CSS = """
.mx-auto{margin-left:auto;margin-right:auto}
.px-4{padding-left:1rem;padding-right:1rem}
.py-2{padding-top:.5rem;padding-bottom:.5rem}
.py-8{padding-top:2rem;padding-bottom:2rem}
.flex{display:flex}
.items-center{align-items:center}
.items-start{align-items:flex-start}
.justify-between{justify-content:space-between}
.gap-2{gap:.5rem}
.flex-shrink-0{flex-shrink:0}
.rounded-lg{border-radius:.5rem}
.shadow-sm{box-shadow:0 1px 2px rgba(0,0,0,.06)}
.text-sm{font-size:.875rem}
.text-3xl{font-size:1.875rem;line-height:2.25rem}
.text-white{color:#fff}
.bg-white{background-color:#fff}
"""


def _accent_from(html: str) -> str | None:
    """Read the page's own --terracotta accent, filled from the trip manifest."""
    m = re.search(r"--terracotta:\s*(#[0-9A-Fa-f]{3,8})\s*;", html)
    return m.group(1) if m else None


def _colour_utilities(accent: str | None = None) -> str:
    palette = dict(_PALETTE)
    if accent:
        palette["terracotta"] = accent
    rules = []
    for name, value in palette.items():
        rules.append(f".text-{name}{{color:{value}}}")
        rules.append(f".bg-{name}{{background-color:{value}}}")
    # bg-offwhite/95 -- Tailwind's slash-opacity syntax, used once.
    rules.append(".bg-offwhite\\/95{background-color:#FAFAF7F2}")
    return "\n".join(rules)


def _map_replacement(html: str) -> tuple[str, bool]:
    """Swap the Leaflet container for a link to the same route.

    The route link is already elsewhere in the page, so rather than construct
    one, reuse the first full-route directions URL found. If there is none, the
    placeholder simply says the map needs the full version -- better than an
    empty bordered box with no explanation.
    """
    match = re.search(r'<div[^>]*id="overview-map"[^>]*>.*?</div>', html, re.S | re.I)
    if not match:
        return html, False

    route = re.search(r'href="(https://www\.google\.com/maps/dir/[^"]+)"', html)
    if route:
        link = (
            f'<a href="{route.group(1)}" target="_blank" rel="noopener">'
            "Open the full route in Google Maps →</a>"
        )
    else:
        link = "Open the full itinerary file to view the route map."

    placeholder = (
        '<div style="padding:20px;border:1.5px dashed #d4c4a8;border-radius:12px;'
        'background:#faf6ed;text-align:center;font-size:.95rem;color:#5b4a3a;">'
        "<strong>Route map</strong><br>"
        "Interactive maps are omitted from this mail-safe copy. "
        f"{link}</div>"
    )
    return html[: match.start()] + placeholder + html[match.end():], True


def make_email_safe(html: str) -> tuple[str, dict[str, int]]:
    """Return (script-free html, what changed)."""
    stats = {"scripts_removed": 0, "cdn_scripts_removed": 0, "map_replaced": 0}

    stats["cdn_scripts_removed"] = len(re.findall(r"<script[^>]+src=", html, re.I))
    stats["scripts_removed"] = len(re.findall(r"<script", html, re.I))

    out, replaced = _map_replacement(html)
    stats["map_replaced"] = 1 if replaced else 0

    # Both forms: paired blocks first, then any self-closing or src-only tags.
    out = re.sub(r"<script\b[^>]*>.*?</script>", "", out, flags=re.S | re.I)
    out = re.sub(r"<script\b[^>]*/?>", "", out, flags=re.I)

    # Leaflet's stylesheet is now dead weight and is another third-party fetch.
    out = re.sub(r'<link[^>]+leaflet[^>]*>', "", out, flags=re.I)

    style_block = (
        "<style>\n/* Inlined for the mail-safe copy: no CDN, no script. */\n"
        + _UTILITY_CSS.strip()
        + "\n"
        + _colour_utilities(_accent_from(html))
        + "\n</style>\n"
    )
    if "</head>" in out:
        out = out.replace("</head>", style_block + "</head>", 1)
    else:
        out = style_block + out

    return out, stats
