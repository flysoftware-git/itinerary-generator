"""AllTrails answers 403 for a real trail and for an invented slug alike, so
the live page cannot say which it is holding. These guard the channel that can.

Measured 2026-10-22 on six URLs, three real and three fabricated, on this
app's own User-Agent and on a browser's: **403 for every one, byte-identical
at 773 bytes**, while `alltrails.com/` itself answered 200. That is page-level
bot protection (DataDome), and two shipped behaviours followed from it --
`classify_link_liveness` calls a 403 `unchecked`, `unchecked` publishes, and
`allow_blocked_alltrails` is set, so a fabricated slug reached a card like a
real trail; and `_passes_alltrails_post_search_filters` fetched the page for
its metadata and returned True on failure, so the ten `alltrails_filter_*`
settings could not bind on a path that is blocked, which is every path.

The archive answers both halves. Confirmed live 2026-10-23 through these exact
methods: `parus-trail` archived and meeting the criteria (3.3 mi, 141 ft,
easy), `angels-landing-trail` archived at 4.8 mi with its gain genuinely
unreadable, `emerald-pools-trail` archived and grading itself twice, and four
slugs withheld -- the three fabrications and `pa-rus-trail`, a near-miss on a
real trail whose canonical slug is `parus-trail`.

**The rule the whole thing turns on: an unknown criterion is never a pass.**
"""

from __future__ import annotations

import pytest

from generator.url_discovery import (
    DEFAULT_REQUIRE_ALLTRAILS_ARCHIVE_VALIDATION,
    WAYBACK_CDX_UNREADABLE,
    WAYBACK_NO_SNAPSHOT,
    URLDiscoverer,
)

TRAIL = "https://www.alltrails.com/trail/us/utah/parus-trail"
FABRICATED = "https://www.alltrails.com/trail/us/utah/zzzqqq-nonsense-trail-999"

#: The Pa'rus snapshot's own shape: each number is separated from its unit by a
#: `<span>`, which is why the patterns run against tag-stripped text.
PARUS_SNAPSHOT = (
    '<h1>Pa&#x27;rus Trail</h1>'
    '<p>Generally considered an easy route, this is a moderate hike that covers '
    '<span class="TrailStats_statVal__a">3.3</span>'
    '<span class="TrailStats_unit__b">mi</span>, with an elevation gain of '
    '<span class="TrailStats_statVal__a">141</span>'
    '<span class="TrailStats_unit__b">ft</span>.</p>'
)

#: Archived, and its gain is genuinely not in the page. The binding criterion.
GAIN_UNREADABLE_SNAPSHOT = (
    "<h1>Angels Landing Trail</h1>"
    "<p>Get to know this 2.1-mile trail near Springdale. Generally considered "
    "an easy route.</p>"
)


def _discoverer(**overrides):
    """A discoverer wired for the filter gate and nothing else.

    `config.yaml`'s own shipped values, so a test says what a real build does
    rather than what a convenient number does.
    """
    d = URLDiscoverer.__new__(URLDiscoverer)
    d._enable_filtered_alltrails_selection = True
    d._require_alltrails_archive_validation = True
    d._alltrails_filter_max_miles = 4.0
    d._max_trail_miles = 4.0
    d._alltrails_filter_max_gain_feet = 1000
    d._alltrails_filter_min_reviews = 5
    d._alltrails_filter_allowed_difficulties = ("easy", "moderate")
    d._verify_url_cached = lambda url: (False, 403)
    # Every direct fetch is the 403 that production measures.
    d._fetch_page_text = lambda url, timeout=8: (False, 403, "")
    for key, value in overrides.items():
        setattr(d, key, value)
    return d


def _archive(answers: dict[str, tuple[bool, int | str, str]]):
    """Stand in for `_fetch_wayback_alltrails_text` with a recorded answer."""

    def _fetch(url, timeout=8):
        return answers.get(url, (False, WAYBACK_NO_SNAPSHOT, ""))

    return _fetch


# ── the validity half ───────────────────────────────────────────────────────


def test_a_fabricated_slug_is_withheld_because_it_has_never_been_a_page():
    """The defect this exists for: a slug no model could have got from a real
    page reaching a card on the same footing as a trail that exists."""
    d = _discoverer(_fetch_wayback_alltrails_text=_archive({}))
    assert d._passes_alltrails_post_search_filters(FABRICATED, "Nonsense Trail", "Springdale") is False


def test_an_archived_trail_meeting_the_criteria_still_publishes():
    """The positive control. A gate that withholds everything is not a gate --
    and this is the live `parus-trail` result, 3.3 mi / 141 ft / easy."""
    d = _discoverer(
        _fetch_wayback_alltrails_text=_archive({TRAIL: (True, 200, PARUS_SNAPSHOT)})
    )
    assert d._passes_alltrails_post_search_filters(TRAIL, "Pa'rus Trail", "Springdale") is True


@pytest.mark.parametrize(
    "answer",
    [
        (False, 429, ""),
        (False, 503, ""),
        (False, "read timed out", ""),
        (False, WAYBACK_CDX_UNREADABLE, ""),
    ],
    ids=["throttled", "server error", "timeout", "unreadable answer"],
)
def test_an_archive_that_could_not_be_asked_does_not_withhold_anything(answer):
    """`could not ask` is not `never existed`, and conflating them is how a
    correct gate strips a guide of good links during an archive.org wobble --
    which `_fetch_wayback_alltrails_text`'s own docstring measured at 429 on
    20 of 20 real trail URLs under humble, correctly-paced volume."""
    d = _discoverer(_fetch_wayback_alltrails_text=_archive({TRAIL: answer}))
    assert d._passes_alltrails_post_search_filters(TRAIL, "Pa'rus Trail", "Springdale") is True


def test_only_an_answered_lookup_with_no_snapshot_reads_as_never_archived():
    d = _discoverer(
        _fetch_wayback_alltrails_text=_archive({TRAIL: (False, WAYBACK_NO_SNAPSHOT, "")})
    )
    assert d._alltrails_archive_verdict(TRAIL) == ("never_archived", "")
    d = _discoverer(
        _fetch_wayback_alltrails_text=_archive({TRAIL: (False, WAYBACK_CDX_UNREADABLE, "")})
    )
    assert d._alltrails_archive_verdict(TRAIL) == ("unknown", "")


def test_the_switch_off_restores_the_old_fail_open_behaviour():
    """`require_alltrails_archive_validation: false` is the way back, and a
    gate with no way back gets deleted rather than configured."""
    d = _discoverer(
        _require_alltrails_archive_validation=False,
        _fetch_wayback_alltrails_text=_archive({}),
    )
    assert d._passes_alltrails_post_search_filters(FABRICATED, "Nonsense Trail", "Springdale") is True


def test_the_validation_is_on_by_default():
    """Publishing a link the engine cannot tell from a fabrication is the
    defect, so the fix is not opt-in."""
    assert DEFAULT_REQUIRE_ALLTRAILS_ARCHIVE_VALIDATION is True


# ── the criteria half: unknown is never a pass ──────────────────────────────


def test_an_unreadable_elevation_gain_is_not_counted_as_a_pass():
    """The rule, on the criterion it binds on. Gain is unknown far more often
    than it is present -- 3 of 5 and 5 of 6 valid results in the Springdale
    bake-off -- and the shipped filter's `if value is not None` guard scored
    every one of those as meeting a criterion it had never read."""
    d = _discoverer(
        _fetch_wayback_alltrails_text=_archive(
            {TRAIL: (True, 200, GAIN_UNREADABLE_SNAPSHOT)}
        )
    )
    assert d._passes_alltrails_post_search_filters(TRAIL, "Angels Landing Trail", "Springdale") is False
    meta = URLDiscoverer._parse_alltrails_page_criteria(GAIN_UNREADABLE_SNAPSHOT)
    assert meta["gain_feet"] is None, "an unread gain must read as unknown, not as a number"
    assert meta["miles"] == 2.1


def test_a_snapshot_with_nothing_readable_is_not_a_pass():
    d = _discoverer(
        _fetch_wayback_alltrails_text=_archive({TRAIL: (True, 200, "<p>Nothing here.</p>")})
    )
    assert d._passes_alltrails_post_search_filters(TRAIL, "Pa'rus Trail", "Springdale") is False


# ── the four parser defects, each measured against a real snapshot ──────────


def test_a_span_between_the_number_and_its_unit_does_not_hide_the_gain():
    """Defect 2, and the one that mattered: a pattern over raw HTML matched
    nothing on the Pa'rus snapshot and reported gain unknown for all four
    valid trails -- unknown being exactly what the shipped filter passes."""
    meta = URLDiscoverer._parse_alltrails_page_criteria(PARUS_SNAPSHOT)
    assert meta["miles"] == 3.3
    assert meta["gain_feet"] == 141


#: What a real trail page's difficulty markup actually looks like. Counted on
#: the archived snapshots 2026-10-23: **26 `difficulty-*` class matches on
#: Emerald Pools and 39 on Angels Landing, spanning `easy`, `moderate`,
#: `strenuous` and `hard` on one page** -- the cards for neighbouring trails
#: and the filter buttons -- plus `difficulty-level-button`, which a loose
#: pattern reads as a grading called `level`.
NEIGHBOURS_AND_FILTERS = (
    '<nav><button class="difficulty-level-button">Difficulty</button>'
    '<button class="difficulty-easy">Easy</button>'
    '<button class="difficulty-moderate">Moderate</button>'
    '<button class="difficulty-hard">Hard</button></nav>'
    '<aside><a class="difficulty-strenuous">Angels Landing</a></aside>'
)


def test_a_neighbouring_cards_difficulty_class_does_not_outrank_this_trails_sentence():
    """Defect 4: a trail page carries cards for OTHER trails and a row of
    filter buttons, each with its own `difficulty-*` class. The class-first
    read called Lower Emerald Pool `hard` where its own page says *"Generally
    considered an easy route"*, and an 8.9-mile challenging route `moderate`."""
    html = (
        NEIGHBOURS_AND_FILTERS
        + "<h1>Lower Emerald Pool Trail</h1>"
        "<p>Generally considered an easy route, it covers 1.2 mi, with an "
        "elevation gain of 69 ft.</p>"
    )
    meta = URLDiscoverer._parse_alltrails_page_criteria(html)
    assert meta["difficulty"] == "easy"
    assert meta["difficulty_from"] == "sentence"


def test_the_difficulty_class_is_not_consulted_even_when_nothing_else_answers():
    """Defects 3 and 4, at the root rather than at the symptom.

    A loose `difficulty-([a-z]+)` read `difficulty=and` on five of eight
    snapshots, which looks like a pattern defect and was fixed as one. The
    counts above say that fix answered the wrong question: the constrained
    pattern matches real markup 26 to 39 times per page across four gradings,
    so `re.search` returns whichever the markup puts first and a tighter
    pattern only spells the coin flip better.

    So a page with no grading SENTENCE reads as unknown, and an unknown
    criterion is never a pass. That withholds a link -- the safe direction --
    rather than publishing a strenuous hike to somebody who filtered for easy
    ones because a filter button appeared earlier in the HTML.
    """
    html = NEIGHBOURS_AND_FILTERS + (
        "<h1>Some Trail</h1><p>It covers 1.2 mi, with an elevation gain of "
        "69 ft. No grading in words anywhere.</p>"
    )
    meta = URLDiscoverer._parse_alltrails_page_criteria(html)
    assert meta["miles"] == 1.2, "the readable criteria are still read"
    assert meta["gain_feet"] == 69
    assert meta["difficulty"] is None, (
        "a class the page carries four of is not evidence of this trail's grading"
    )
    assert meta["difficulty_said"] is None

    d = _discoverer(_fetch_wayback_alltrails_text=_archive({TRAIL: (True, 200, html)}))
    assert d._passes_alltrails_post_search_filters(TRAIL, "Some Trail", "Springdale") is False


def test_a_length_label_sitting_beside_the_gain_is_not_read_as_a_length():
    """The trap: on the Delicate Arch snapshot the markup renders `Length` and
    `193 m` adjacently, where 193 m is the elevation GAIN. A label-proximity
    parse reads the gain as the length and is wrong by a factor nobody would
    notice."""
    meta = URLDiscoverer._parse_alltrails_page_criteria(
        "<dl><dt>Length</dt><dd>193 m</dd></dl><p>No summary sentence.</p>"
    )
    assert meta["miles"] is None
    assert meta["gain_feet"] is None


def test_a_metric_snapshot_is_converted_rather_than_taken_at_face_value():
    """Snapshots come in both systems: the 2026-10-07 Delicate Arch one reads
    *"covers 5.1 km, with an elevation gain of 193 m"*."""
    meta = URLDiscoverer._parse_alltrails_page_criteria(
        "<p>A moderate hike that covers 5.1 km, with an elevation gain of 193 m.</p>"
    )
    assert meta["miles"] == 3.17
    assert meta["gain_feet"] == 633
    assert meta["difficulty"] == "moderate"
    assert meta["difficulty_from"] == "summary sentence"


def test_an_unmapped_grading_stays_unknown_rather_than_being_coerced():
    meta = URLDiscoverer._parse_alltrails_page_criteria(
        "<p>Considered a bracing route that covers 1.0 mi, with an elevation "
        "gain of 10 ft.</p>"
    )
    assert meta["difficulty"] is None
    assert "unmapped" in str(meta["difficulty_from"])


# ── a page that grades itself twice ────────────────────────────────────────


def test_a_page_that_grades_itself_twice_is_read_at_its_harder_grading():
    """Measured live on the Emerald Pools snapshot, which says *"Generally
    considered a moderately challenging route"* in its lede AND *"is considered
    a moderate hike that covers 3 mi, with an elevation gain of 580 ft"*
    further down. Both are this trail's own prose, so the provenance argument
    that rules out a neighbouring card's class is true and does not settle it.

    The harder reading needs no claim about which sentence is canonical, and
    the two errors are not symmetric: too hard withholds a link, too easy
    publishes a hike the configured policy excluded to a traveller who asked
    for easy ones.
    """
    html = (
        "<h1>Emerald Pools Trail</h1>"
        "<p>Get to know this 3.0-mile loop trail near Springdale, Utah. "
        "Generally considered a moderately challenging route, it takes an "
        "average of 1 h 28 min to complete.</p>"
        "<p>Emerald Pools Trail is considered a moderate hike that covers 3 mi, "
        "with an elevation gain of 580 ft.</p>"
    )
    meta = URLDiscoverer._parse_alltrails_page_criteria(html)
    assert meta["difficulty"] == "moderately challenging"
    assert "hardest of" in str(meta["difficulty_from"])
    assert "moderate" in str(meta["difficulty_from"])

    # And against the shipped `["easy", "moderate"]` it therefore does not
    # publish -- while the bake-off harness, which folds the two gradings
    # together, counted it as one of only two trails meeting all three
    # criteria in the Springdale measurement.
    d = _discoverer(_fetch_wayback_alltrails_text=_archive({TRAIL: (True, 200, html)}))
    assert d._passes_alltrails_post_search_filters(TRAIL, "Emerald Pools Trail", "Springdale") is False


def test_moderately_challenging_is_kept_distinct_from_moderate():
    """`scripts/trail_bakeoff.py` folds the two together and `config.yaml`'s
    `["easy", "moderate"]` has always excluded the harder one. Folding it in
    here would quietly admit a grade the configuration has been rejecting,
    which is a policy change wearing a parser fix's clothes."""
    assert URLDiscoverer._ALLTRAILS_DIFFICULTY_WORDS["moderately challenging"] == (
        "moderately challenging"
    )
    assert "moderately challenging" not in ("easy", "moderate")


# ── a seed exists, even though it is exempt from the criteria ───────────────


def test_a_seeds_fabricated_link_is_still_withheld():
    """A seed is exempt from the length/gain/difficulty gate, because the
    traveller named that trail. It is not exempt from existing: the slug was
    still found by a search or proposed by a model, and the item keeps its
    place on the page -- it loses only a link that was never a page."""
    d = _discoverer(
        _is_alltrails_trail_url=URLDiscoverer._is_alltrails_trail_url,
        _fetch_wayback_alltrails_text=_archive({}),
    )
    d._has_alltrails_closure_marker = lambda text: False
    assert d._alltrails_url_meets_seed_relaxed_standard(
        "https://www.alltrails.com/trail/us/utah/zzzqqq-nonsense-trail-999",
        "Zzzqqq Nonsense Trail",
    ) is False


def test_a_seed_is_still_exempt_from_the_criteria():
    """The seed exemption is load-bearing -- the dipstick60 "The Narrows" case,
    a trail a traveller asked for that the family-short-hike policy
    legitimately demotes. Validity must not smuggle the criteria back in."""
    d = _discoverer(
        _fetch_wayback_alltrails_text=_archive(
            {TRAIL: (True, 200, GAIN_UNREADABLE_SNAPSHOT)}
        )
    )
    d._has_alltrails_closure_marker = lambda text: False
    # The same snapshot that FAILS the criteria gate above.
    assert d._alltrails_url_meets_seed_relaxed_standard(TRAIL, "Pa'rus Trail") is True
