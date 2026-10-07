"""The search must not spend its one answer on a URL the gate will refuse.

WHAT IT COST
------------
The Europe guide built 2026-10-07 on a45086a dropped four attractions -- Berlin's
Tiergarten, Frankfurt's Römerberg, the Prague Astronomical Clock and Amsterdam's
Canal Ring Boat Tour. All four had the same two-event trail:

    search_resolved
    no_verified_url_removed

A URL *was* found and then thrown away. For three of the four the resolved URL
was a **Facebook post**, refused at retention exit 30 as `social_media` -- and
correctly, since `url_policy_blocked_classes` carries `social_media` in
`enforce` mode.

THE DEFECT IS THAT TWO READERS DISAGREED
----------------------------------------
`_search_first_strict` ranked its candidates and deep-checked each with
`_is_relevant_result` ONLY. It never asked `_classify_url_policy_class`. So a
post titled "Tiergarten Berlin Germany" is maximally *relevant* -- it names the
item and the destination -- wins selection, and is then certain to be discarded
by the next gate. `max_deep_checks = min(3, len(ranked))` candidates were in
hand and the function returned at the first.

The proof that something better was there: `berlin.de` is where the same build's
direct batch found Brandenburg Gate. For the Tiergarten it was never reached.

WHAT IS PINNED
--------------
That the selector skips what retention refuses outright and goes on to the next
candidate -- and that it does NOT pre-empt the classes retention takes a
caller's permission for, or restaurant pass 1 (which hunts google.com/maps on
purpose) and the trail passes would break.
"""

from __future__ import annotations

import pytest

from generator.url_discovery import (
    CONTEXT_RESCUABLE_POLICY_CLASSES,
    URLDiscoverer,
)

BERLIN = "Berlin, Germany"
FACEBOOK = (
    "https://www.facebook.com/aarif.kamru.khan/photos/"
    "-tiergarten-berlin-germany-explore-tiergarten-berlins-famous-green-heart/1098897006013482/"
)
BERLIN_DE = "https://www.berlin.de/en/attractions-and-sights/3560256-3104052-tiergarten.en.html"


def _selector(results, *, blocked=("google_search", "google_maps_search",
                                   "google_maps_dir", "social_media"),
              mode="enforce", scores=None):
    """A URLDiscoverer whose only real logic is the selector under test.

    Everything the ranking loop leans on is stubbed permissively, so the one
    thing that can reject a candidate here is the rule being tested. `scores`
    orders the candidates; the Facebook post outranks the official page, which
    is the situation the live build was in.
    """
    d = URLDiscoverer.__new__(URLDiscoverer)
    d._url_policy_mode = mode
    d._url_policy_blocked_classes = set(blocked)
    d.skipped = []

    d._search_cached = lambda query, count=10: [{"url": u} for u in results]
    d._note_fallback_call_site = lambda *a, **k: None
    d._matches_site_filter = lambda url, site_filter: True
    d._is_alltrails_trail_url = lambda url: "alltrails.com" in url.lower()
    d._is_specific_result_url = lambda *a, **k: True
    d._meets_place_interest_threshold = lambda *a, **k: True
    d._normalize_restaurant_url = lambda url: url
    d._is_relevant_result = lambda url, *a, **k: True
    d._score_candidate_result = lambda item, *a, **k: (scores or {}).get(item["url"], 1)
    d._pick_better_candidate = lambda best, score, url: (
        best if best and best[0] >= score else (score, url)
    )
    d._should_short_circuit_search = lambda *a, **k: False
    d._log_decision = lambda **kw: d.skipped.append((kw.get("reason"), kw.get("url")))
    return d


def _resolve(d, item_name="Tiergarten", dest_name=BERLIN, **kw):
    return d._search_first_strict(
        query_variants=[f"{item_name} {dest_name} official site"],
        site_filter=None,
        site_hint=None,
        item_name=item_name,
        dest_name=dest_name,
        allow_alltrails=False,
        **kw,
    )


class TestTheCaseFromTheEuropeGuide:
    def test_the_official_page_wins_when_the_facebook_post_outranks_it(self):
        """The whole defect in one assertion.

        The post scores higher, so before this fix it was selected and then
        discarded, and the Tiergarten was removed from the guide.
        """
        d = _selector([FACEBOOK, BERLIN_DE], scores={FACEBOOK: 99, BERLIN_DE: 1})

        assert _resolve(d) == BERLIN_DE

    def test_the_skip_is_on_the_record(self):
        """Four attractions shared one trail and it took a full build to read.

        A skip that says nothing would move the silence one stage earlier.
        """
        d = _selector([FACEBOOK, BERLIN_DE], scores={FACEBOOK: 99, BERLIN_DE: 1})
        _resolve(d)

        assert ("search_candidate_refused_by_url_policy", FACEBOOK) in d.skipped

    def test_nothing_acceptable_still_fails_closed(self):
        """Fail-closed is the existing behaviour and must survive the fix.

        An item with only a refused candidate gets no link -- the same outcome as
        before, reached for a stated reason instead of by accident.
        """
        d = _selector([FACEBOOK], scores={FACEBOOK: 99})

        assert _resolve(d) is None
        assert ("search_candidate_refused_by_url_policy", FACEBOOK) in d.skipped


class TestItDoesNotPreEmptAGateThatTakesPermission:
    """Retention rescues some classes on the caller's say-so. Refusing them here
    would break the passes that exist to find them -- restaurant pass 1 hunts
    `google.com/maps`, the trail passes hunt AllTrails."""

    @pytest.mark.parametrize("url", [
        "https://www.google.com/maps/search/?api=1&query=Tiergarten%20Berlin",
        "https://www.google.com/maps/dir/?api=1&destination=Tiergarten",
        "https://www.alltrails.com/trail/germany/berlin/tiergarten-loop",
    ])
    def test_a_rescuable_class_is_left_for_retention_to_judge(self, url):
        d = _selector([url], blocked=("google_search", "google_maps_search",
                                      "google_maps_dir", "social_media", "alltrails"))

        assert d._policy_class_refused_outright(url) == ""

    def test_the_rescuable_set_matches_the_permissions_retention_takes(self):
        """One set, so the two gates cannot drift -- the defect that produced
        this file is two readers of one question, and a second copy of this list
        would be the same mistake in a new place."""
        assert CONTEXT_RESCUABLE_POLICY_CLASSES == {
            "google_maps_search", "google_maps_dir", "alltrails",
        }


class TestTheRuleFollowsTheConfiguration:
    def test_monitor_mode_refuses_nothing(self):
        """Retention refuses nothing in monitor mode, so neither may this."""
        d = _selector([FACEBOOK], mode="monitor", scores={FACEBOOK: 99})

        assert _resolve(d) == FACEBOOK

    def test_a_class_that_is_not_blocked_is_not_skipped(self):
        """`social_media` is blocked by config, not by default. The selector must
        read the configured set rather than a copy of its own."""
        d = _selector([FACEBOOK], blocked=("google_search",), scores={FACEBOOK: 99})

        assert _resolve(d) == FACEBOOK

    def test_an_ordinary_page_is_untouched(self):
        d = _selector([BERLIN_DE])

        assert _resolve(d) == BERLIN_DE
        assert d.skipped == []
