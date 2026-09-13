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


# --------------------------------------------- the run says so, not only the log
#
# The fix above made an exhausted balance loud in the log, once. A log line is
# read by whoever is watching at the moment it is written. The run itself still
# completed, its validation report and run ledger carried no trace of it, and
# the quality gate reported the resulting removals as items with no verified
# URL -- the exact misreading the original regression was about, one layer up.


class _Discoverer:
    def __init__(self, batch=None, fallback=None):
        self._search = batch
        self._search_fallback = fallback


class _Clean:
    quota_exhausted = False


class _Spent:
    quota_exhausted = True


class _NoAttribute:
    """A provider that has never learned to report quota at all."""


def test_a_run_that_never_searched_is_not_a_clean_one():
    """None and [] mean different things and must not collapse together."""
    from generator.main import _search_quota_exhausted

    assert _search_quota_exhausted(None) is None


def test_a_run_that_searched_and_ran_out_is_named_by_client():
    from generator.main import _search_quota_exhausted

    got = _search_quota_exhausted(_Discoverer(batch=_Clean(), fallback=_Spent()))
    assert got == ["url_discovery_fallback"]


def test_a_run_that_searched_cleanly_reports_an_empty_list():
    from generator.main import _search_quota_exhausted

    assert _search_quota_exhausted(_Discoverer(batch=_Clean(), fallback=_Clean())) == []


def test_a_provider_that_cannot_report_quota_is_not_mistaken_for_one_that_ran_out():
    from generator.main import _search_quota_exhausted

    assert _search_quota_exhausted(_Discoverer(batch=_NoAttribute(), fallback=None)) == []


def test_a_real_serper_client_that_hit_the_balance_is_detected(monkeypatch, client):
    """The detection reads `quota_exhausted` by duck typing, so a rename of that
    attribute would make every run report [] forever with nothing failing. Only
    a real client -- not a stub carrying the right name -- can catch that."""
    from generator.main import _search_quota_exhausted

    _post(monkeypatch, client, _Resp(400, '{"message":"Not enough credits"}'))
    client.search("Rijksmuseum Amsterdam")
    assert _search_quota_exhausted(_Discoverer(fallback=client)) == ["url_discovery_fallback"]


def test_the_quality_gate_names_the_cause_ahead_of_the_removals(capsys):
    """Those items were not looked for and found missing. They were not looked
    for at all, and the gate has to say which before it lists what was lost."""
    from generator.main import _run_quality_gate

    # A trip with a real removal, so there is something for the cause to come
    # ahead of. An earlier version passed an empty trip, where the credit line
    # was the only warning and therefore both first and last -- so it held with
    # the insert moved to the end, and asserted nothing about order at all.
    removed = {"rejection_reasons": ["no_verified_url_removed"],
               "section_target": "dinner_recommendations",
               "entity_class": "restaurant"}
    trip = {"destinations": [{"ai_content": {}, "_registry_decisions": [removed]}]}

    _run_quality_gate(trip, None, search_quota_exhausted=["url_discovery_fallback"])
    out = capsys.readouterr().out
    cause = out.find("search credits exhausted (url_discovery_fallback)")
    loss = out.find("restaurants removed for no verified URL")
    assert cause != -1 and loss != -1, out
    assert cause < loss, "the removal was listed before the reason for it"
    assert "never searched for" in out


@pytest.mark.parametrize("state", [None, []])
def test_the_quality_gate_says_nothing_about_credits_when_there_is_nothing_to_say(capsys, state):
    from generator.main import _run_quality_gate

    _run_quality_gate({"destinations": []}, None, search_quota_exhausted=state)
    assert "credits" not in capsys.readouterr().out
