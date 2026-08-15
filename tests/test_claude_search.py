"""Tests for generator.claude_search"""

import time
from unittest.mock import MagicMock
import pytest
import requests

from generator.claude_search import CLAUDE_ENDPOINT_DEFAULT, ClaudeCircuitOpenError, ClaudeSearch, _extract_json_object


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


def _dns_error() -> requests.ConnectionError:
    return requests.ConnectionError("Failed to resolve 'api.anthropic.com' ([Errno 11001] getaddrinfo failed)")


def test_post_with_retries_retries_dns_resolution_errors() -> None:
    cs = ClaudeSearch(api_key="test", model="test", network_retries=1)
    fake_response = MagicMock()
    fake_response.status_code = 200

    session = MagicMock()
    session.post.side_effect = [_dns_error(), fake_response]
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = cs._post_with_retries({"model": "test"}, "query")

    assert out is fake_response
    assert session.post.call_count == 2


def test_post_with_retries_opens_circuit_breaker_after_repeated_transient_failures() -> None:
    cs = ClaudeSearch(api_key="test", model="test", network_retries=0)
    cs._circuit_breaker_threshold = 2
    cs._circuit_breaker_cooldown_seconds = 60.0

    session = MagicMock()
    session.post.side_effect = _dns_error()
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    for _ in range(2):
        with pytest.raises(requests.ConnectionError):
            cs._post_with_retries({"model": "test"}, "query")
    assert session.post.call_count == 2

    with pytest.raises(ClaudeCircuitOpenError):
        cs._post_with_retries({"model": "test"}, "query")

    assert session.post.call_count == 2


def test_post_with_retries_resets_failure_count_after_success() -> None:
    cs = ClaudeSearch(api_key="test", model="test", network_retries=0)
    cs._circuit_breaker_threshold = 2
    cs._circuit_breaker_cooldown_seconds = 60.0

    fake_response = MagicMock()
    fake_response.status_code = 200
    session = MagicMock()
    session.post.side_effect = [_dns_error(), fake_response, _dns_error()]
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    with pytest.raises(requests.ConnectionError):
        cs._post_with_retries({"model": "test"}, "query")

    out = cs._post_with_retries({"model": "test"}, "query")
    assert out is fake_response

    with pytest.raises(requests.ConnectionError):
        cs._post_with_retries({"model": "test"}, "query")

    assert cs._circuit_breaker_open_until == 0.0


def _http_error_response(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    error = requests.HTTPError(f"{status_code} error", response=resp)
    resp.raise_for_status.side_effect = error
    return resp


def test_post_with_retries_counts_non_retryable_http_error_toward_breaker() -> None:
    """Regression for the real "credit balance is too low" incident
    (2026-08-15): a 400 used to come back as a successful post() call with
    no exception, so this breaker looked healthy while every call was
    actually being rejected."""
    cs = ClaudeSearch(api_key="test", model="test", network_retries=0)
    cs._circuit_breaker_threshold = 2
    cs._circuit_breaker_cooldown_seconds = 60.0

    session = MagicMock()
    session.post.return_value = _http_error_response(400)
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    with pytest.raises(requests.HTTPError):
        cs._post_with_retries({"model": "test"}, "query")
    assert session.post.call_count == 1

    with pytest.raises(requests.HTTPError):
        cs._post_with_retries({"model": "test"}, "query")
    assert session.post.call_count == 2

    with pytest.raises(ClaudeCircuitOpenError):
        cs._post_with_retries({"model": "test"}, "query")
    assert session.post.call_count == 2


def test_post_with_retries_retries_retryable_http_status_within_same_call() -> None:
    cs = ClaudeSearch(api_key="test", model="test", network_retries=1)
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status = MagicMock()

    session = MagicMock()
    session.post.side_effect = [_http_error_response(429), fake_response]
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = cs._post_with_retries({"model": "test"}, "query")

    assert out is fake_response
    assert session.post.call_count == 2


def test_post_with_retries_does_not_retry_non_retryable_http_status_within_same_call() -> None:
    cs = ClaudeSearch(api_key="test", model="test", network_retries=2)
    session = MagicMock()
    session.post.return_value = _http_error_response(401)
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    with pytest.raises(requests.HTTPError):
        cs._post_with_retries({"model": "test"}, "query")

    assert session.post.call_count == 1


def test_circuit_breaker_check_returns_probe_flag_after_cooldown_elapses() -> None:
    cs = ClaudeSearch(api_key="test", model="test")
    cs._circuit_breaker_open_until = time.monotonic() - 0.01

    assert cs._circuit_breaker_check() is True


def test_circuit_breaker_check_returns_false_when_never_tripped() -> None:
    cs = ClaudeSearch(api_key="test", model="test")
    assert cs._circuit_breaker_check() is False


def test_circuit_breaker_check_rejects_non_probe_callers_while_probe_in_flight() -> None:
    cs = ClaudeSearch(api_key="test", model="test")
    cs._circuit_breaker_open_until = time.monotonic() - 0.01

    assert cs._circuit_breaker_check() is True

    with pytest.raises(ClaudeCircuitOpenError, match="probe in flight"):
        cs._circuit_breaker_check()


def test_successful_probe_fully_resets_breaker_state() -> None:
    cs = ClaudeSearch(api_key="test", model="test", network_retries=0)
    cs._circuit_breaker_open_until = time.monotonic() - 0.01
    cs._circuit_breaker_failure_times = [time.monotonic()]

    fake_response = MagicMock()
    fake_response.status_code = 200
    session = MagicMock()
    session.post.return_value = fake_response
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = cs._post_with_retries({"model": "test"}, "query")

    assert out is fake_response
    assert cs._circuit_breaker_open_until == 0.0
    assert cs._circuit_breaker_failure_times == []
    assert cs._circuit_breaker_check() is False


def test_failed_probe_reopens_breaker_immediately_without_needing_threshold_failures() -> None:
    cs = ClaudeSearch(api_key="test", model="test", network_retries=0)
    cs._circuit_breaker_threshold = 4
    cs._circuit_breaker_open_until = time.monotonic() - 0.01

    session = MagicMock()
    session.post.side_effect = _dns_error()
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    with pytest.raises(requests.ConnectionError):
        cs._post_with_retries({"model": "test"}, "query")

    assert cs._circuit_breaker_open_until > time.monotonic()
    with pytest.raises(ClaudeCircuitOpenError):
        cs._post_with_retries({"model": "test"}, "query")


def test_non_probe_callers_do_not_touch_network_while_probe_pending() -> None:
    cs = ClaudeSearch(api_key="test", model="test", network_retries=0)
    cs._circuit_breaker_open_until = time.monotonic() - 0.01

    session = MagicMock()
    session.post.side_effect = requests.exceptions.ReadTimeout("still slow")
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    with pytest.raises(requests.exceptions.ReadTimeout):
        cs._post_with_retries({"model": "test"}, "query")
    assert session.post.call_count == 1

    with pytest.raises(ClaudeCircuitOpenError):
        cs._post_with_retries({"model": "test"}, "query")
    assert session.post.call_count == 1


def test_circuit_breaker_stats_track_trip_count_and_total_open_seconds() -> None:
    cs = ClaudeSearch(api_key="test", model="test", network_retries=0)
    cs._circuit_breaker_threshold = 1
    cs._circuit_breaker_cooldown_seconds = 60.0

    session = MagicMock()
    session.post.side_effect = _dns_error()
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    assert cs.get_circuit_breaker_stats() == {
        "trip_count": 0,
        "total_open_seconds": 0.0,
        "currently_open": False,
    }

    with pytest.raises(requests.ConnectionError):
        cs._post_with_retries({"model": "test"}, "query")

    stats_open = cs.get_circuit_breaker_stats()
    assert stats_open["trip_count"] == 1
    assert stats_open["currently_open"] is True

    cs._circuit_breaker_open_until = time.monotonic() - 0.01
    fake_response = MagicMock()
    fake_response.status_code = 200
    session.post.side_effect = None
    session.post.return_value = fake_response
    cs._post_with_retries({"model": "test"}, "query")

    stats_recovered = cs.get_circuit_breaker_stats()
    assert stats_recovered["currently_open"] is False
    assert stats_recovered["total_open_seconds"] >= 0.0
    assert stats_recovered["trip_count"] == 1


def test_is_circuit_open_reflects_current_breaker_state() -> None:
    cs = ClaudeSearch(api_key="test", model="test")

    assert cs.is_circuit_open() is False

    cs._circuit_breaker_open_until = __import__("time").monotonic() + 60.0
    assert cs.is_circuit_open() is True

    cs._circuit_breaker_open_until = __import__("time").monotonic() - 1.0
    assert cs.is_circuit_open() is False


def test_chat_completion_returns_empty_string_when_circuit_breaker_open() -> None:
    cs = ClaudeSearch(api_key="test", model="test")
    cs._circuit_breaker_open_until = __import__("time").monotonic() + 60.0

    session = MagicMock()
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = cs.chat_completion(system_prompt="sys", user_prompt="prompt")

    assert out == ""
    session.post.assert_not_called()


def _messages_payload(text: str, *, with_tool_result: bool = False) -> dict:
    """Matches the real Anthropic Messages API response shape: "content" is
    an array of blocks; text blocks carry the answer, web_search_tool_result
    blocks carry the raw search hits (not part of the answer text)."""
    content = []
    if with_tool_result:
        content.append({
            "type": "web_search_tool_result",
            "content": [{"type": "web_search_result", "url": "https://example.com", "title": "Example"}],
        })
    content.append({"type": "text", "text": text, "citations": []})
    return {"content": content, "usage": {"input_tokens": 100, "output_tokens": 50}}


def test_chat_completion_posts_to_messages_endpoint_without_search_tool_by_default() -> None:
    cs = ClaudeSearch(api_key="test", model="test")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = _messages_payload("<ul></ul>")
    session = MagicMock()
    session.post.return_value = fake_response
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = cs.chat_completion(system_prompt="sys", user_prompt="u", live_search=False)

    assert out == "<ul></ul>"
    assert session.post.call_args.args[0] == CLAUDE_ENDPOINT_DEFAULT
    sent = session.post.call_args.kwargs["json"]
    assert "tools" not in sent
    assert sent["system"] == "sys"
    assert sent["messages"] == [{"role": "user", "content": "u"}]


def test_chat_completion_live_search_adds_web_search_tool() -> None:
    cs = ClaudeSearch(api_key="test", model="test")
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = _messages_payload(
        "<ul><li>Real Trail <a href='https://alltrails.com/x'>Source</a></li></ul>", with_tool_result=True
    )
    session = MagicMock()
    session.post.return_value = fake_response
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = cs.chat_completion(system_prompt="sys", user_prompt="find a trail", live_search=True)

    assert "Real Trail" in out
    sent = session.post.call_args.kwargs["json"]
    assert sent["tools"] == [{"type": "web_search_20260318", "name": "web_search", "max_uses": 1}]


def test_chat_completion_skips_tool_result_blocks_from_returned_text() -> None:
    cs = ClaudeSearch(api_key="test", model="test")
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = _messages_payload("Clean output only", with_tool_result=True)
    session = MagicMock()
    session.post.return_value = fake_response
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = cs.chat_completion(system_prompt="sys", user_prompt="u", live_search=True)

    assert out == "Clean output only"
    assert "example.com" not in out


def test_chat_completion_tracks_usage_including_cache_token_fields() -> None:
    tracker = MagicMock()
    cs = ClaudeSearch(api_key="test", model="test", usage_tracker=tracker)
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    payload = _messages_payload("<ul></ul>")
    payload["usage"]["cache_creation_input_tokens"] = 20
    payload["usage"]["cache_read_input_tokens"] = 5
    fake_response.json.return_value = payload
    session = MagicMock()
    session.post.return_value = fake_response
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    cs.chat_completion(system_prompt="sys", user_prompt="u", live_search=True)

    tracker.add.assert_called_once()
    assert tracker.add.call_args.kwargs["prompt_tokens"] == 100 + 20 + 5
    assert tracker.add.call_args.kwargs["completion_tokens"] == 50
    assert tracker.add.call_args.kwargs["provider"] == "anthropic"


def test_chat_completion_respects_explicit_max_tokens_override() -> None:
    cs = ClaudeSearch(api_key="test", model="test")
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = _messages_payload("<ul></ul>")
    session = MagicMock()
    session.post.return_value = fake_response
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    cs.chat_completion(system_prompt="sys", user_prompt="prompt", max_tokens=42)

    assert session.post.call_args.kwargs["json"]["max_tokens"] == 42


def test_claude_search_uses_web_search_tool_and_returns_normalized_results() -> None:
    cs = ClaudeSearch(api_key="test", model="test")
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = _messages_payload(
        '{"results": [{"title": "Real Place", "url": "https://example.com/real", "snippet": "s"}]}'
    )
    session = MagicMock()
    session.post.return_value = fake_response
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    results = cs.search("best restaurant in St. George")

    assert results == [{"name": "Real Place", "snippet": "s", "url": "https://example.com/real"}]
    sent = session.post.call_args.kwargs["json"]
    assert sent["tools"] == [{"type": "web_search_20260318", "name": "web_search", "max_uses": 1}]


def test_claude_search_retries_with_stricter_prompt_on_malformed_json() -> None:
    cs = ClaudeSearch(api_key="test", model="test")
    bad_response = MagicMock()
    bad_response.raise_for_status = MagicMock()
    # Balanced braces (brace-matching finds a candidate) but a trailing comma
    # makes it invalid JSON -- this is the shape that raises
    # json.JSONDecodeError, as opposed to unbalanced/no-brace text (ValueError,
    # not caught by the retry, matching GrokSearch's behavior).
    bad_response.json.return_value = _messages_payload('{"results": [{"title": "x",}]}')
    good_response = MagicMock()
    good_response.raise_for_status = MagicMock()
    good_response.json.return_value = _messages_payload('{"results": []}')
    session = MagicMock()
    session.post.side_effect = [bad_response, good_response]
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    results = cs.search("ambiguous query")

    assert results == []
    assert session.post.call_count == 2


def test_search_returns_empty_list_on_exception() -> None:
    cs = ClaudeSearch(api_key="test", model="test")
    session = MagicMock()
    session.post.side_effect = requests.ConnectionError("boom")
    cs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]
    cs._network_retries = 0

    assert cs.search("anything") == []
