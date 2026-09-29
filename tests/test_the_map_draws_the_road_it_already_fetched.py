"""The map draws the road, where the road is known, and says where it is not.

The overview map drew the whole trip as one dashed polyline through the stops:
a straight line between each pair, whatever the road did. Meanwhile the real
shape had been fetched from the router, decoded, simplified to a bounded number
of points and cached -- and then discarded one step from the page. A `grep` for
consumers of `RoutedLeg.geometry` outside `routing.py` found a single docstring
reference.

So a guide drew a road nobody drove, using data it had already paid for.

**The honesty half is not decoration.** `routing.py` records that a cache entry
written before geometry was kept has none, and that a leg is not re-asked just
to fetch a shape. A real guide therefore mixes routed and unrouted legs for a
while, and a map that quietly straight-lines some of them makes the same claim
about both. Each leg is drawn solid where the shape is known and dashed where
it is not, and a note on the map says how many are straight lines.

**Rendering must never become a provider call**, which is why this reads
`routing.cached_leg` rather than `route_leg`: the latter fetches, and with no
API key returns None before it even looks in the cache.
"""
import json

import pytest

from generator import routing


ONE_ROAD = ((47.60, -122.33), (47.55, -122.30), (47.50, -122.20))


def _cache(tmp_path, entries):
    path = tmp_path / "routes.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_a_cached_leg_is_read_without_a_key_or_a_request(tmp_path, monkeypatch):
    """The property a renderer depends on: no key, no network, no cost.

    `route_leg` cannot be used for this. With no key it returns None before
    looking at the cache at all, and with one it will go and fetch.
    """
    key = routing._cache_key(ONE_ROAD[0], ONE_ROAD[-1], avoid_ferries=False,
                             profile=routing.DEFAULT_PROFILE)
    path = _cache(tmp_path, {key: {"miles": 12.0, "minutes": 20.0,
                                   "ferry_share": 0.0,
                                   "geometry": [list(p) for p in ONE_ROAD],
                                   "ferry_spans": []}})

    monkeypatch.delenv("OPENROUTESERVICE_API_KEY", raising=False)

    def refuse(*a, **k):                      # any request is a failure here
        raise AssertionError("rendering asked the router for a leg")
    monkeypatch.setattr(routing.urllib.request, "urlopen", refuse)

    leg = routing.cached_leg(ONE_ROAD[0], ONE_ROAD[-1], cache_path=path)

    assert leg is not None, "a leg already on disk was not returned"
    assert leg.geometry == ONE_ROAD


def test_a_leg_the_cache_cannot_answer_is_none_not_an_exception(tmp_path):
    """Shape unknown and no road are indistinguishable from here, and both
    must leave the page renderable."""
    assert routing.cached_leg(ONE_ROAD[0], ONE_ROAD[-1],
                              cache_path=_cache(tmp_path, {})) is None


def test_an_entry_written_before_geometry_was_kept_still_loads(tmp_path):
    """Such an entry is a leg with no shape, not a miss -- `routing.py` says so
    in its own docstring, and a map must draw that leg dashed rather than drop
    it."""
    key = routing._cache_key(ONE_ROAD[0], ONE_ROAD[-1], avoid_ferries=False,
                             profile=routing.DEFAULT_PROFILE)
    path = _cache(tmp_path, {key: {"miles": 12.0, "minutes": 20.0,
                                   "ferry_share": 0.0}})

    leg = routing.cached_leg(ONE_ROAD[0], ONE_ROAD[-1], cache_path=path)

    assert leg is not None and leg.geometry is None


def test_a_typo_in_the_profile_is_a_bug_not_a_lookup(tmp_path):
    """Same rule `route_leg` already holds: a caller's typo raises rather than
    quietly answering None, which here would look exactly like an unrouted leg
    and be drawn as a straight line forever."""
    with pytest.raises(ValueError):
        routing.cached_leg(ONE_ROAD[0], ONE_ROAD[-1], profile="drivng-car",
                           cache_path=_cache(tmp_path, {}))


def test_the_template_draws_each_leg_and_states_the_unrouted_ones():
    """The page contract, read from the template itself.

    Asserted on the template rather than on a rendered page because the
    property is about what the page is CAPABLE of saying: a build with no
    routed legs at all would render a page whose every leg is dashed, and a
    test over that output could not tell the fix from its absence.
    """
    from pathlib import Path
    template = (Path(__file__).parent.parent / "templates"
                / "v2.5_template.html").read_text(encoding="utf-8")

    assert "<!--ROUTE_LEGS_JSON-->" in template, "the shape never reaches the page"
    assert "leg.routed?null:'7,6'" in template, (
        "every leg is drawn the same way, so a routed road and a straight line "
        "make the same claim")
    assert "not routed, drawn as a straight line" in template, (
        "the map does not say which legs are straight lines, which is the "
        "honesty half of this change")
