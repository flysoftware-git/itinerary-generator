"""Tests for generator.grok_search"""

from unittest.mock import MagicMock
import pytest
import requests

from generator.grok_search import GrokCircuitOpenError, GrokSearch, _extract_json_object


def test_extract_json_object_handles_raw_json():
    payload = '{"results": [{"title": "A", "url": "https://example.com", "snippet": "ok"}]}'

    parsed = _extract_json_object(payload)

    assert parsed["results"][0]["title"] == "A"


def test_extract_json_object_handles_code_fenced_json():
    payload = """```json
{"results": [{"title": "B", "url": "https://example.com/b", "snippet": "ok"}]}
```"""

    parsed = _extract_json_object(payload)

    assert parsed["results"][0]["url"] == "https://example.com/b"


def test_extract_json_object_handles_prefixed_text():
    payload = 'Here is the JSON: {"results": [{"title": "C", "url": "https://example.com/c", "snippet": "ok"}]}'

    parsed = _extract_json_object(payload)

    assert parsed["results"][0]["snippet"] == "ok"


def test_post_with_retries_retries_dns_resolution_errors() -> None:
    gs = GrokSearch(api_key="test", model="test", network_retries=1)
    fake_response = MagicMock()
    fake_response.status_code = 200

    session = MagicMock()
    session.post.side_effect = [
        requests.ConnectionError("Failed to resolve 'api.x.ai' ([Errno 11001] getaddrinfo failed)"),
        fake_response,
    ]

    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = gs._post_with_retries({"model": "test"}, "query")

    assert out is fake_response
    assert session.post.call_count == 2


def test_log_request_exception_suppresses_repeated_dns_warnings() -> None:
    gs = GrokSearch(api_key="test", model="test")
    gs._warning_cooldown_seconds = 3600

    warning_spy = MagicMock()
    original_warning = __import__("generator.grok_search", fromlist=["logger"]).logger.warning
    __import__("generator.grok_search", fromlist=["logger"]).logger.warning = warning_spy
    try:
        exc = requests.ConnectionError("Failed to resolve 'api.x.ai' ([Errno 11001] getaddrinfo failed)")
        gs._log_request_exception("query one", exc)
        gs._log_request_exception("query two", exc)
    finally:
        __import__("generator.grok_search", fromlist=["logger"]).logger.warning = original_warning

    assert warning_spy.call_count == 1


def _dns_error() -> requests.ConnectionError:
    return requests.ConnectionError("Failed to resolve 'api.x.ai' ([Errno 11001] getaddrinfo failed)")


def test_post_with_retries_opens_circuit_breaker_after_repeated_transient_failures() -> None:
    """Regression for issue #67: under a Grok timeout burst, each caller used to
    independently retry and wait out its own full timeout budget, piling more
    load onto an already-struggling endpoint. Once enough transient failures
    land in a short window, later callers must fail fast instead of touching
    the network again."""
    gs = GrokSearch(api_key="test", model="test", network_retries=0)
    gs._circuit_breaker_threshold = 2
    gs._circuit_breaker_cooldown_seconds = 60.0

    session = MagicMock()
    session.post.side_effect = _dns_error()
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    for _ in range(2):
        with pytest.raises(requests.ConnectionError):
            gs._post_with_retries({"model": "test"}, "query")
    assert session.post.call_count == 2

    with pytest.raises(GrokCircuitOpenError):
        gs._post_with_retries({"model": "test"}, "query")

    # The breaker short-circuited before touching the network again.
    assert session.post.call_count == 2


def test_post_with_retries_resets_failure_count_after_success() -> None:
    gs = GrokSearch(api_key="test", model="test", network_retries=0)
    gs._circuit_breaker_threshold = 2
    gs._circuit_breaker_cooldown_seconds = 60.0

    fake_response = MagicMock()
    fake_response.status_code = 200
    session = MagicMock()
    session.post.side_effect = [_dns_error(), fake_response, _dns_error()]
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    with pytest.raises(requests.ConnectionError):
        gs._post_with_retries({"model": "test"}, "query")

    out = gs._post_with_retries({"model": "test"}, "query")
    assert out is fake_response

    with pytest.raises(requests.ConnectionError):
        gs._post_with_retries({"model": "test"}, "query")

    # Success cleared the failure count, so the 3rd (single) failure alone
    # isn't enough to trip a threshold-of-2 breaker.
    assert gs._circuit_breaker_open_until == 0.0


def test_chat_completion_returns_empty_string_when_circuit_breaker_open() -> None:
    gs = GrokSearch(api_key="test", model="test")
    gs._circuit_breaker_open_until = __import__("time").monotonic() + 60.0

    session = MagicMock()
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = gs.chat_completion(system_prompt="sys", user_prompt="prompt")

    assert out == ""
    session.post.assert_not_called()


def test_chat_completion_defaults_to_configured_max_tokens_cap() -> None:
    """Regression for issue #66: chat_completion previously sent no max_tokens
    at all, so a stuck/rambling model could run to whatever the provider's own
    ceiling is instead of a bounded direct-batch harvest response."""
    gs = GrokSearch(api_key="test", model="test")
    gs._chat_max_tokens = 1234

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "choices": [{"message": {"content": "<ul></ul>"}}],
        "usage": {},
    }
    session = MagicMock()
    session.post.return_value = fake_response
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    gs.chat_completion(system_prompt="sys", user_prompt="prompt")

    assert session.post.call_args.kwargs["json"]["max_tokens"] == 1234


def test_chat_completion_respects_explicit_max_tokens_override() -> None:
    gs = GrokSearch(api_key="test", model="test")
    gs._chat_max_tokens = 1234

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "choices": [{"message": {"content": "<ul></ul>"}}],
        "usage": {},
    }
    session = MagicMock()
    session.post.return_value = fake_response
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    gs.chat_completion(system_prompt="sys", user_prompt="prompt", max_tokens=42)

    assert session.post.call_args.kwargs["json"]["max_tokens"] == 42


def test_is_circuit_open_reflects_current_breaker_state() -> None:
    gs = GrokSearch(api_key="test", model="test")

    assert gs.is_circuit_open() is False

    gs._circuit_breaker_open_until = __import__("time").monotonic() + 60.0
    assert gs.is_circuit_open() is True

    gs._circuit_breaker_open_until = __import__("time").monotonic() - 1.0
    assert gs.is_circuit_open() is False
