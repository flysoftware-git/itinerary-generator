"""A quota failure must be reported as a quota failure.

Regression: Serper returns HTTP 400 with {"message":"Not enough credits"} for
an exhausted balance -- the same status it uses for a bad query. The client
logged only the status and discarded the body, so an account with no credits
looked like a run where the web simply had nothing to offer. Three trips
published with 39, 93 and an unknown number of items silently dropped by the
verified-link-or-seed policy before anyone noticed.
"""

import logging

import pytest

from generator.serper_search import SerperSearch


class _Resp:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    return SerperSearch()


def _post(monkeypatch, client, resp):
    monkeypatch.setattr(client._session, "post", lambda *a, **k: resp)


def test_out_of_credits_is_named_not_just_counted(monkeypatch, client, caplog):
    _post(monkeypatch, client, _Resp(400, '{"message":"Not enough credits","statusCode":400}'))
    with caplog.at_level(logging.ERROR):
        assert client.search("Rijksmuseum Amsterdam") == []
    assert client.quota_exhausted is True
    assert "out of credits" in caplog.text
    # the operator needs to know why the itinerary got thinner, not just that a call failed
    assert "verified-link-or-seed" in caplog.text


def test_the_loud_error_is_logged_once_not_per_call(monkeypatch, client, caplog):
    _post(monkeypatch, client, _Resp(400, '{"message":"Not enough credits"}'))
    with caplog.at_level(logging.ERROR):
        for _ in range(5):
            client.search("anything")
    assert caplog.text.count("out of credits") == 1


def test_a_genuine_bad_request_still_logs_the_body(monkeypatch, client, caplog):
    _post(monkeypatch, client, _Resp(400, '{"message":"Invalid query syntax"}'))
    with caplog.at_level(logging.WARNING):
        assert client.search("weird query") == []
    assert client.quota_exhausted is False
    assert "Invalid query syntax" in caplog.text


@pytest.mark.parametrize("status,body,expected", [
    (400, '{"message":"Not enough credits"}', True),
    (400, '{"message":"quota exceeded"}', True),
    (402, "", True),
    (429, "", True),
    (400, '{"message":"Invalid query"}', False),
    (500, "upstream exploded", False),
])
def test_quota_predicate(status, body, expected):
    assert SerperSearch._is_quota_error(status, body) is expected
