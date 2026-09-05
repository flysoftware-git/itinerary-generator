"""Tests for `generator/place_resolver.py` and its one call site.

Two properties carry this feature and get the most attention:

* **unconfigured is invisible** -- no key means byte-identical output to before;
* **configured-and-broken is loud but not fatal** -- a refused key disables the
  enrichment with a warning, and the guide still gets its link.
"""

from __future__ import annotations

import logging

import pytest

from generator.place_resolver import (
    API_KEY_ENV_VARS,
    PLACE_ID_ONLY_FIELD_MASK,
    PlaceResolutionRefused,
    PlaceResolver,
    maps_place_url,
)
from generator.url_discovery import SAFE_FALLBACK_URL_PREFIXES, URLDiscoverer


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class _Session:
    """Records posts and returns queued responses."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.posts = []

    def post(self, url, **kw):
        self.posts.append((url, kw))
        return self._responses.pop(0) if self._responses else _Resp(200, {"places": []})


def _resolver(*responses, **kw):
    return PlaceResolver(api_key="k", session=_Session(*responses), **kw)


# --------------------------------------------------------- unconfigured

def test_no_key_means_disabled_and_silent(monkeypatch):
    for var in API_KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    r = PlaceResolver()
    assert r.enabled is False
    assert r.resolve("Delicate Arch, Moab") is None
    assert r.stats["calls"] == 0


@pytest.mark.parametrize("var", API_KEY_ENV_VARS)
def test_either_env_var_carries_the_key_once_config_permits_it(
    monkeypatch, tmp_path, var
):
    """Both names still work -- and neither is sufficient on its own.

    This test asserted that setting either variable made the resolver live, and
    that was the behaviour rather than an accident of the test: a key reaching
    the environment for any reason turned a metered product on for every run
    afterwards. The credential moved to `generator/maps_platform.py`, which asks
    config first, so the environment now answers "with what" and config answers
    "may I".
    """
    for v in API_KEY_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv(var, "abc123")
    monkeypatch.chdir(tmp_path)

    config = tmp_path / "config.yaml"

    config.write_text("maps_platform:\n  enabled: false\n", encoding="utf-8")
    assert PlaceResolver().enabled is False, "a key alone switched it on"

    config.write_text("maps_platform:\n  enabled: true\n", encoding="utf-8")
    assert PlaceResolver().enabled is True


# ------------------------------------------------------------ resolving

def test_resolves_a_place_id_and_builds_a_place_url():
    r = _resolver(_Resp(200, {"places": [{"id": "ChIJabc"}]}))
    assert r.resolve("Delicate Arch, Moab, Utah") == "ChIJabc"
    # The documented Maps URLs scheme: api=1 with query + query_place_id.
    # The old /maps/place/?q=place_id: form carried no api=1, so Google routed
    # it to a legacy handler and reported that an API was required.
    assert r.maps_url_for("Delicate Arch, Moab, Utah") == (
        "https://www.google.com/maps/search/?api=1"
        "&query=Delicate%20Arch%2C%20Moab%2C%20Utah&query_place_id=ChIJabc"
    )


def test_requests_only_the_id_field():
    """The compliance guarantee: it cannot cache what it never asks for."""
    r = _resolver(_Resp(200, {"places": [{"id": "X"}]}))
    r.resolve("somewhere")
    _, kw = r.session.posts[0]
    assert kw["headers"]["X-Goog-FieldMask"] == PLACE_ID_ONLY_FIELD_MASK == "places.id"


def test_no_match_is_a_normal_answer():
    r = _resolver(_Resp(200, {"places": []}))
    assert r.resolve("a place that does not exist") is None
    assert r.maps_url_for("a place that does not exist") == ""
    assert r.enabled is True, "no-match must not disable the resolver"
    assert r.stats["no_match"] == 1


def test_repeat_queries_are_cached():
    r = _resolver(_Resp(200, {"places": [{"id": "ChIJabc"}]}))
    r.resolve("same place")
    r.resolve("same place")
    assert r.stats["calls"] == 1
    assert r.stats["cache_hits"] == 1


def test_call_cap_disables_rather_than_spending():
    r = _resolver(*[_Resp(200, {"places": [{"id": f"p{i}"}]}) for i in range(5)], max_calls=2)
    for i in range(4):
        r.resolve(f"place {i}")
    assert r.stats["calls"] == 2
    assert r.enabled is False
    assert "call cap" in r.disabled_reason


def test_maps_place_url_of_nothing_is_empty():
    assert maps_place_url("") == ""
    assert maps_place_url(None) == ""


# ------------------------------------------------ refusal is loud, not fatal

@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_refusal_raises_rather_than_looking_like_no_match(status):
    r = _resolver(_Resp(status, {}, text="denied"))
    with pytest.raises(PlaceResolutionRefused):
        r.resolve("anywhere")


def test_disable_logs_once_at_warning(caplog):
    r = _resolver()
    with caplog.at_level(logging.WARNING):
        r.disable("HTTP 403 from Places")
        r.disable("a second reason")
    assert r.enabled is False
    assert r.disabled_reason == "HTTP 403 from Places"
    assert sum("Place resolution disabled" in m for m in caplog.messages) == 1


def test_transport_error_does_not_disable_the_resolver():
    """A flaky socket is noise, not a broken key."""
    import requests

    class _Boom:
        def post(self, *a, **k):
            raise requests.RequestException("connection reset")

    r = PlaceResolver(api_key="k", session=_Boom())
    assert r.resolve("somewhere") is None
    assert r.enabled is True


# ------------------------------------------------------------- call site

def _discoverer():
    d = URLDiscoverer.__new__(URLDiscoverer)
    d._place_resolver = None
    return d


def _attach(d, item, name="Delicate Arch", dest="Moab, Utah"):
    logged = []
    d._log_decision = lambda **kw: logged.append(kw)
    d._attach_secondary_maps_link(item, kind="attraction", dest_name=dest, item_name=name)
    return logged


def test_unconfigured_call_site_produces_the_old_search_url():
    """The compatibility guarantee: no key, no change."""
    d = _discoverer()
    item = {"url": "https://www.nps.gov/arch/index.htm"}
    logged = _attach(d, item)
    assert item["maps_url"].startswith("https://www.google.com/maps/search/?api=1&query=")
    assert logged and logged[0]["reason"].endswith(":maps_search")


def test_configured_call_site_upgrades_to_a_place_id_link():
    d = _discoverer()
    d._place_resolver = _resolver(_Resp(200, {"places": [{"id": "ChIJdelicate"}]}))
    item = {"url": "https://www.nps.gov/arch/index.htm"}
    logged = _attach(d, item)
    assert item["maps_url"].startswith("https://www.google.com/maps/search/?api=1&query=")
    assert item["maps_url"].endswith("&query_place_id=ChIJdelicate")
    assert logged[0]["reason"].endswith(":maps_place_id")


def test_refusal_at_the_call_site_keeps_the_link_and_disables_the_resolver(caplog):
    """Loud, and the customer still gets their guide."""
    d = _discoverer()
    d._place_resolver = _resolver(_Resp(403, {}, text="API_KEY_SERVICE_BLOCKED"))
    item = {"url": "https://www.nps.gov/arch/index.htm"}
    with caplog.at_level(logging.WARNING):
        _attach(d, item)
    assert item["maps_url"].startswith("https://www.google.com/maps/search/")
    assert d._place_resolver.enabled is False
    assert any("Place resolution disabled" in m for m in caplog.messages)


def test_a_no_match_falls_back_without_disabling():
    d = _discoverer()
    d._place_resolver = _resolver(_Resp(200, {"places": []}))
    item = {"url": "https://www.nps.gov/arch/index.htm"}
    _attach(d, item)
    assert item["maps_url"].startswith("https://www.google.com/maps/search/")
    assert d._place_resolver.enabled is True


def test_place_urls_are_treated_as_safe_fallbacks():
    """Otherwise every enriched link burns an HTTP validation request."""
    assert "https://www.google.com/maps/place/" in SAFE_FALLBACK_URL_PREFIXES
    assert any(
        maps_place_url("ChIJabc").lower().startswith(p) for p in SAFE_FALLBACK_URL_PREFIXES
    )


def test_place_url_carries_api_1() -> None:
    """Without api=1 the link is not part of the keyless Maps URLs scheme.

    The Old Hickory build published 83 links as
    `/maps/place/?q=place_id:<id>` -- no api=1 -- and Google answered that an
    API was required. The 53 links on that page that did carry api=1 worked.
    """
    from generator.place_resolver import maps_place_url

    url = maps_place_url("ChIJabc", "Some Place")
    assert "api=1" in url
    assert "query_place_id=ChIJabc" in url
    assert "/maps/place/?q=place_id:" not in url


def test_place_url_still_pins_the_id_without_query_text() -> None:
    from generator.place_resolver import maps_place_url

    url = maps_place_url("ChIJabc")
    assert "api=1" in url and "query_place_id=ChIJabc" in url


def test_no_place_id_yields_no_link() -> None:
    from generator.place_resolver import maps_place_url

    assert maps_place_url("") == ""
