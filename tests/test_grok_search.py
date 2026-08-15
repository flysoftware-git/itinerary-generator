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


def _responses_payload(text: str, *, with_reasoning: bool = True, with_search_call: bool = True) -> dict:
    """Matches the real /v1/responses shape captured 2026-08-14 -- output is
    an interleaved array of reasoning, web_search_call, and message items;
    only message/output_text blocks are the actual answer text."""
    output = []
    if with_reasoning:
        output.append({"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking..."}]})
    if with_search_call:
        output.append({
            "type": "web_search_call",
            "status": "completed",
            "action": {"type": "search", "query": "test query", "sources": [{"type": "url", "url": "https://example.com"}]},
        })
    output.append({
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    })
    return {"output": output, "usage": {"input_tokens": 100, "output_tokens": 50}}


def test_chat_completion_live_search_posts_to_responses_endpoint() -> None:
    """Regression for the 2026-08-14 fix: live_search used to add
    tools:[{"type":"live_search"}] to the (deprecated, now-410) chat-
    completions endpoint. Real search requires POSTing to /v1/responses with
    a differently-shaped request (top-level "input" string, not "messages")."""
    from generator.grok_search import GROK_RESPONSES_ENDPOINT

    gs = GrokSearch(api_key="test", model="test")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = _responses_payload("<ul><li>Real Trail <a href='https://alltrails.com/x'>Source</a></li></ul>")
    session = MagicMock()
    session.post.return_value = fake_response
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = gs.chat_completion(system_prompt="sys", user_prompt="find a trail", live_search=True)

    assert "Real Trail" in out
    assert session.post.call_args.args[0] == GROK_RESPONSES_ENDPOINT
    sent = session.post.call_args.kwargs["json"]
    assert sent["tools"] == [{"type": "web_search"}]
    assert "sys" in sent["input"] and "find a trail" in sent["input"]
    assert "messages" not in sent


def test_chat_completion_live_search_skips_reasoning_and_search_call_items() -> None:
    """Only message/output_text content is the answer -- reasoning summaries
    and the raw web_search_call action must not leak into the returned text
    that callers parse as HTML."""
    gs = GrokSearch(api_key="test", model="test")
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = _responses_payload("<ul><li>Clean output only</li></ul>")
    session = MagicMock()
    session.post.return_value = fake_response
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = gs.chat_completion(system_prompt="sys", user_prompt="u", live_search=True)

    assert out == "<ul><li>Clean output only</li></ul>"
    assert "thinking" not in out
    assert "web_search_call" not in out


def test_chat_completion_live_search_tracks_usage_with_responses_token_fields() -> None:
    """The /v1/responses endpoint reports input_tokens/output_tokens, not
    chat-completions' prompt_tokens/completion_tokens -- usage tracking must
    read the right fields or search-path spend goes uncounted."""
    tracker = MagicMock()
    gs = GrokSearch(api_key="test", model="test", usage_tracker=tracker)
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = _responses_payload("<ul></ul>")
    session = MagicMock()
    session.post.return_value = fake_response
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    gs.chat_completion(system_prompt="sys", user_prompt="u", live_search=True)

    tracker.add.assert_called_once()
    assert tracker.add.call_args.kwargs["prompt_tokens"] == 100
    assert tracker.add.call_args.kwargs["completion_tokens"] == 50


def test_chat_completion_live_search_false_still_uses_chat_completions_endpoint() -> None:
    """Unchanged path: live_search=False (the default) must keep hitting the
    plain chat-completions endpoint exactly as before this fix."""
    gs = GrokSearch(api_key="test", model="test")
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"choices": [{"message": {"content": "<ul></ul>"}}], "usage": {}}
    session = MagicMock()
    session.post.return_value = fake_response
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    gs.chat_completion(system_prompt="sys", user_prompt="u", live_search=False)

    from generator.grok_search import GROK_ENDPOINT
    assert session.post.call_args.args[0] == GROK_ENDPOINT
    assert "messages" in session.post.call_args.kwargs["json"]


def test_grok_search_uses_real_web_search_via_responses_endpoint() -> None:
    """Regression for the 2026-08-14 fix: _grok_search used to just tell the
    model 'You are a web search engine' via the system prompt on the plain
    chat-completions endpoint, granting no actual search tool -- results
    could be entirely reproduced from training-data memory. Now routes
    through /v1/responses with the real web_search tool."""
    from generator.grok_search import GROK_RESPONSES_ENDPOINT

    gs = GrokSearch(api_key="test", model="test")
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = _responses_payload(
        '{"results": [{"title": "Real Place", "url": "https://example.com/real", "snippet": "s"}]}'
    )
    session = MagicMock()
    session.post.return_value = fake_response
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    results = gs.search("best restaurant in St. George")

    assert results == [{"name": "Real Place", "snippet": "s", "url": "https://example.com/real"}]
    assert session.post.call_args.args[0] == GROK_RESPONSES_ENDPOINT
    sent = session.post.call_args.kwargs["json"]
    assert sent["tools"] == [{"type": "web_search"}]
    assert sent["text"] == {"format": {"type": "json_object"}}


def test_grok_search_retries_with_stricter_prompt_on_malformed_json() -> None:
    gs = GrokSearch(api_key="test", model="test")
    bad_response = MagicMock()
    bad_response.raise_for_status = MagicMock()
    # Balanced braces (so the brace-matching fallback in _extract_json_object
    # finds a candidate substring) but a trailing comma makes that substring
    # itself invalid JSON -- this is the specific shape that raises
    # json.JSONDecodeError (as opposed to unbalanced/no-brace text, which
    # raises ValueError instead and isn't what _grok_search's retry catches).
    bad_response.json.return_value = _responses_payload('{"results": [{"title": "x",}]}')
    good_response = MagicMock()
    good_response.raise_for_status = MagicMock()
    good_response.json.return_value = _responses_payload('{"results": []}')
    session = MagicMock()
    session.post.side_effect = [bad_response, good_response]
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    results = gs.search("ambiguous query")

    assert results == []
    assert session.post.call_count == 2
