"""The email build must use the trip's own accent, not a frozen copy.

Regression: _PALETTE hardcoded #3A5F8A (Europe's rail blue), so every trip's
email attachment rendered blue accents while its web page rendered its real
theme_color. Caught only by comparing the two outputs for the same trip.
"""

from generator.email_safe import _accent_from, _colour_utilities, make_email_safe

PAGE = "<style>:root{{--terracotta: {accent};}}</style><body>x</body>"


def test_accent_is_read_from_the_page():
    assert _accent_from(PAGE.format(accent="#C0623E")) == "#C0623E"
    assert _accent_from(PAGE.format(accent="#3A5F8A")) == "#3A5F8A"


def test_accent_absent_falls_back_without_raising():
    assert _accent_from("<body>no theme here</body>") is None
    assert ".text-terracotta" in _colour_utilities(None)


def test_each_trip_email_keeps_its_own_accent():
    """Two trips, two accents -- neither may leak into the other."""
    sw, _ = make_email_safe(PAGE.format(accent="#C0623E"))
    eu, _ = make_email_safe(PAGE.format(accent="#3A5F8A"))
    assert ".text-terracotta{color:#C0623E}" in sw
    assert "#3A5F8A" not in sw
    assert ".text-terracotta{color:#3A5F8A}" in eu
    assert "#C0623E" not in eu
