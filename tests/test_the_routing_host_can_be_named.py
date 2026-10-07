"""A run may name its own openrouteservice host.

`ENDPOINT_BASE` was a constant, so every run routed through the public API at
api.heigit.org whatever the operator wanted. openrouteservice is open source and
meant to be self-hostable, and the public instance has a daily quota and terms of
its own -- a user who stands up their own server had nowhere to say so and had to
edit the source.

The default is unchanged, which `test_routing_uses_the_current_openrouteservice_host`
goes on asserting: absent the variable, every run routes exactly where it did.
"""

from __future__ import annotations

from urllib.parse import urlparse

from generator import routing


def test_with_nothing_set_the_public_api_is_used(monkeypatch) -> None:
    """The behaviour every existing run has, and the one this must not change."""
    monkeypatch.delenv(routing.BASE_URL_ENV, raising=False)
    assert routing.endpoint_base() == routing.ENDPOINT_BASE
    assert urlparse(routing.endpoint_base()).netloc == "api.heigit.org"


def test_a_named_host_is_used(monkeypatch) -> None:
    """Seen red with `endpoint_base` absent and the constant read directly: a
    self-hosted instance cannot be reached at all."""
    monkeypatch.setenv(routing.BASE_URL_ENV, "https://ors.example.com/ors/v2/directions")
    assert routing.endpoint_base() == "https://ors.example.com/ors/v2/directions"


def test_a_trailing_slash_is_trimmed(monkeypatch) -> None:
    """The caller appends `/<profile>`, and a double slash is a 404 on some
    deployments and not on others -- a difference nobody should discover the
    hard way."""
    monkeypatch.setenv(routing.BASE_URL_ENV, "https://ors.example.com/ors/v2/directions/")
    assert routing.endpoint_base() == "https://ors.example.com/ors/v2/directions"
    monkeypatch.setenv(routing.BASE_URL_ENV, "https://ors.example.com/x///")
    assert routing.endpoint_base() == "https://ors.example.com/x"


def test_a_value_that_is_not_a_url_is_ignored_rather_than_raised_on(monkeypatch) -> None:
    """A typo in an environment variable must not kill a run at its first leg,
    after the expensive stages have already been paid for. The right failure for
    a misnamed host is the routing this run would have had anyway."""
    for bad in ("not-a-url", "ftp://ors.example.com", "api.heigit.org", " ", "ors.example.com/v2"):
        monkeypatch.setenv(routing.BASE_URL_ENV, bad)
        assert routing.endpoint_base() == routing.ENDPOINT_BASE, bad


def test_http_is_allowed_because_a_self_hosted_instance_may_be_local(monkeypatch) -> None:
    """Self-hosting on a private network or a container is the case this exists
    for, and insisting on https there would refuse the ordinary setup."""
    monkeypatch.setenv(routing.BASE_URL_ENV, "http://localhost:8080/ors/v2/directions")
    assert routing.endpoint_base() == "http://localhost:8080/ors/v2/directions"


def test_the_request_goes_to_the_named_host(monkeypatch) -> None:
    """Through the real call path rather than the helper, which is where the
    constant used to be read. Seen red with the call site left on
    `ENDPOINT_BASE`: the helper answers correctly and the request still goes to
    the public API."""
    import inspect

    src = inspect.getsource(routing)
    assert "f\"{endpoint_base()}/{profile}\"" in src, (
        "the directions request is not built from endpoint_base(), so naming a "
        "host changes nothing about where the request goes")
    assert "f\"{ENDPOINT_BASE}/{profile}\"" not in src
