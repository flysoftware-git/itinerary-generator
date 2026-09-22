"""A failed lookup is believed only when the resolver itself is working.

2026-09-21, an Old Hickory build on a machine whose DNS dropped for a few
seconds three times. Windows answers a lookup made during a drop with errno
11001 -- the AUTHORITATIVE "no such host" code, not 11002's "try again" -- so
the temporary-failure carve-out never saw it. The link gate withheld nine live
links as dead (five on alltrails.com, three on tnstateparks.com) and discovery
rejected a real opentable.com restaurant page. Every one resolved again within
the minute.

The strings below are the errors that build logged, verbatim.
"""

from __future__ import annotations

import pytest

import generator.url_discovery as ud
from generator.url_discovery import URLDiscoverer

#: The real re-check, captured at import -- before conftest's autouse stub
#: replaces it for each test. `monkeypatch.undo()` would restore it too, but it
#: would also undo every other autouse fixture, including the one that keeps
#: tests off the production URL cache.
REAL_HOST_RESOLVES = ud._host_resolves

ALLTRAILS = (
    "HTTPSConnectionPool(host='www.alltrails.com', port=443): Max retries exceeded "
    "with url: /trail/us/tennessee/peeler-park-loop (Caused by NameResolutionError("
    "\"HTTPSConnection(host='www.alltrails.com', port=443): Failed to resolve "
    "'www.alltrails.com' ([Errno 11001] getaddrinfo failed)\"))"
)
OPENTABLE = (
    "HTTPSConnectionPool(host='www.opentable.com', port=443): Max retries exceeded "
    "with url: /r/the-rutledge-franklin (Caused by NameResolutionError("
    "\"HTTPSConnection(host='www.opentable.com', port=443): Failed to resolve "
    "'www.opentable.com' ([Errno 11001] getaddrinfo failed)\"))"
)
NOWHERE = (
    "HTTPSConnectionPool(host='no-such-restaurant-xyz.example', port=443): Max "
    "retries exceeded with url: / (Caused by NameResolutionError(\"HTTPSConnection("
    "host='no-such-restaurant-xyz.example', port=443): Failed to resolve "
    "'no-such-restaurant-xyz.example' ([Errno 11001] getaddrinfo failed)\"))"
)
REFUSED = (
    "HTTPSConnectionPool(host='example.org', port=443): Max retries exceeded with "
    "url: / (Caused by NewConnectionError('<urllib3.connection.HTTPSConnection>: "
    "Failed to establish a new connection: [WinError 10061] No connection could be "
    "made because the target machine actively refused it'))"
)


def _resolver(resolving: set[str]):
    asked: list[str] = []

    def answer(host: str) -> bool:
        asked.append(host)
        return host in resolving
    return answer, asked


@pytest.mark.parametrize("error", [ALLTRAILS, OPENTABLE])
def test_a_host_that_resolves_on_a_second_look_was_never_dead(monkeypatch, error):
    """The blip had passed by the time the verdict was reached. Seen red
    before the re-check: both returned True, and both links were lost."""
    answer, _ = _resolver({"www.alltrails.com", "www.opentable.com", *ud.DNS_HEALTH_CANARIES})
    monkeypatch.setattr(ud, "_host_resolves", answer)

    assert URLDiscoverer._is_definitively_dead_status(error) is False


def test_nothing_resolving_means_the_connection_is_down_not_the_link(monkeypatch):
    """The blip is still happening: the host fails, and so does every canary.
    That is a statement about this machine, not about AllTrails."""
    answer, asked = _resolver(set())
    monkeypatch.setattr(ud, "_host_resolves", answer)

    assert URLDiscoverer._is_definitively_dead_status(ALLTRAILS) is False
    assert "www.alltrails.com" in asked and set(ud.DNS_HEALTH_CANARIES) <= set(asked)


def test_a_host_that_really_does_not_exist_is_still_dead(monkeypatch):
    """The reason the DNS markers exist at all. The canaries resolve and this
    host does not: that is what a host that is gone looks like."""
    answer, _ = _resolver(set(ud.DNS_HEALTH_CANARIES))
    monkeypatch.setattr(ud, "_host_resolves", answer)

    assert URLDiscoverer._is_definitively_dead_status(NOWHERE) is True


def test_a_refused_connection_is_not_a_dns_question(monkeypatch):
    """Refusal is not a lookup failure, and is decided exactly as before --
    without asking the resolver anything."""
    answer, asked = _resolver(set())
    monkeypatch.setattr(ud, "_host_resolves", answer)

    assert URLDiscoverer._is_definitively_dead_status(REFUSED) is True
    assert asked == [], "a refused connection asked the resolver"


def test_an_http_status_never_asks_the_resolver(monkeypatch):
    answer, asked = _resolver(set())
    monkeypatch.setattr(ud, "_host_resolves", answer)

    assert URLDiscoverer._is_definitively_dead_status(404) is True
    assert URLDiscoverer._is_definitively_dead_status(503) is False
    assert asked == []


def test_one_host_failing_many_times_is_looked_up_once(monkeypatch):
    """Nine AllTrails links failed in the same second. The re-check is cached,
    so a burst costs one lookup rather than one per link."""
    calls: list[str] = []

    def real_shaped(host, port):
        calls.append(host)
        return [("family", "type", "proto", "", ("1.2.3.4", port))]

    monkeypatch.setattr(ud, "_host_resolves", REAL_HOST_RESOLVES)
    monkeypatch.setattr(ud.socket, "getaddrinfo", real_shaped)
    ud._dns_recheck_cache.clear()
    try:
        for _ in range(9):
            assert ud._host_resolves("www.alltrails.com") is True
    finally:
        ud._dns_recheck_cache.clear()

    assert calls == ["www.alltrails.com"]


def test_a_hung_resolver_cannot_stall_the_run(monkeypatch):
    """getaddrinfo takes no timeout. A lookup that never returns is abandoned
    at the bound and counts as "did not resolve"."""
    import threading

    release = threading.Event()

    def hangs(host, port):
        release.wait(10)
        return []

    monkeypatch.setattr(ud, "_host_resolves", REAL_HOST_RESOLVES)
    monkeypatch.setattr(ud.socket, "getaddrinfo", hangs)
    monkeypatch.setattr(ud, "DNS_RECHECK_TIMEOUT_SECONDS", 0.2)
    ud._dns_recheck_cache.clear()
    try:
        assert ud._host_resolves("hangs.example") is False
    finally:
        release.set()
        ud._dns_recheck_cache.clear()
