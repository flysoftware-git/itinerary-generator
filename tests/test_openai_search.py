"""Tests for generator.openai_search"""

from unittest.mock import MagicMock
import pytest
import requests

from generator.openai_search import OpenAiCircuitOpenError, OpenAiSearch, _extract_json_object


def _responses_stream_lines(
    text: str, *, usage: dict | None = None, web_search_calls: int = 0
) -> list[str]:
    """SSE lines matching the real /v1/responses streaming shape (confirmed
    2026-08-15 via a raw probe of api.openai.com -- identical event types to
    xAI's own /v1/responses, since xAI's was modeled on this shape).

    ``web_search_calls`` inserts that many "response.web_search_call.completed"
    events -- confirmed live 2026-08-17 that OpenAI's "usage" object carries
    no aggregate web_search invocation count (unlike xAI's
    server_side_tool_usage_details.web_search_calls), so each invocation's own
    completed event is what OpenAiSearch actually counts."""
    import json as _json
    lines = [
        'data: {"type": "response.created"}',
        'data: {"type": "response.output_item.added"}',
    ]
    for _ in range(web_search_calls):
        lines.append('data: {"type": "response.web_search_call.completed"}')
    lines.append("data: " + _json.dumps({"type": "response.output_text.delta", "delta": text}))
    lines.append("data: " + _json.dumps({
        "type": "response.completed",
        "response": {"usage": usage or {"input_tokens": 100, "output_tokens": 50}},
    }))
    return lines


def _make_streaming_response(lines: list[str]) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.iter_lines = MagicMock(return_value=iter(lines))
    return resp


def _make_real_sse_response(lines: list[str]) -> requests.Response:
    """Build a genuine requests.Response (not a MagicMock) backed by real
    UTF-8-encoded bytes, with the same charset-less "text/event-stream"
    Content-Type header the OpenAI API actually sends, and .encoding
    populated the same way requests.Session.send() populates it for a real
    HTTP response. Unlike a MagicMock (whose iter_lines just replays
    pre-decoded Python str objects, bypassing any real decoding), this
    exercises the actual requests decode path -- including its RFC-2616
    default of ISO-8859-1 for charset-less "text/*" responses -- so it can
    actually catch a regression of the dipstick58 mojibake fix."""
    import io

    body = ("\n".join(lines) + "\n").encode("utf-8")
    resp = requests.Response()
    resp.status_code = 200
    resp.headers["Content-Type"] = "text/event-stream"
    resp.raw = io.BytesIO(body)
    resp._content_consumed = False
    # Mirrors what requests.Session.send() does for every real response.
    resp.encoding = requests.utils.get_encoding_from_headers(resp.headers)
    return resp


def test_chat_completion_live_search_decodes_utf8_sse_body_correctly() -> None:
    """Dipstick58 bug 3: the /v1/responses SSE stream's Content-Type is
    "text/event-stream" with no charset param. requests' header-based
    encoding guess defaults any charset-less "text/*" content type to
    ISO-8859-1 (RFC 2616), so resp.iter_lines(decode_unicode=True) silently
    mis-decoded the (actually UTF-8) body -- multi-byte characters like a
    right single quote (U+2019) in a harvested restaurant name came out as
    mojibake ("Angelica's" -> "Angelicaâs") once the resulting text was
    later re-encoded as UTF-8 on disk. This uses a real requests.Response
    (not a mock) so the real decode path -- including the ISO-8859-1
    default -- is actually exercised. Same failure mode and fix as
    GrokSearch (see test_grok_search.py's equivalent test)."""
    import json as _json

    # Built with ensure_ascii=False, like the real OpenAI API response body --
    # the curly quote is genuine raw UTF-8 bytes on the wire, not a
    # \uXXXX escape (which would be ASCII-safe and immune to this bug).
    delta_line = "data: " + _json.dumps(
        {"type": "response.output_text.delta", "delta": "Angelica’s Mexican Grill (Irmita’s Casita)"},
        ensure_ascii=False,
    )
    completed_line = "data: " + _json.dumps(
        {"type": "response.completed", "response": {"usage": {"input_tokens": 100, "output_tokens": 50}}}
    )

    oa = OpenAiSearch(api_key="test", model="test")
    fake_response = _make_real_sse_response([delta_line, completed_line])
    session = MagicMock()
    session.post.return_value = fake_response
    oa._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = oa.chat_completion(system_prompt="sys", user_prompt="find a restaurant", live_search=True)

    assert out == "Angelica’s Mexican Grill (Irmita’s Casita)"
    assert "â" not in out  # the mis-decode's tell-tale "â" byte artifact


def test_extract_json_object_handles_raw_json():
    payload = '{"results": [{"title": "A", "url": "https://example.com", "snippet": "ok"}]}'
    parsed = _extract_json_object(payload)
    assert parsed["results"][0]["title"] == "A"


def test_chat_completion_posts_to_responses_endpoint_with_web_search_tool():
    oa = OpenAiSearch(api_key="test", model="test")
    fake_response = _make_streaming_response(
        _responses_stream_lines("<ul><li>Real Trail <a href='https://alltrails.com/x'>Source</a></li></ul>")
    )
    session = MagicMock()
    session.post.return_value = fake_response
    oa._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = oa.chat_completion(system_prompt="sys", user_prompt="find a trail", live_search=True)

    assert "Real Trail" in out
    from generator.openai_search import OPENAI_RESPONSES_ENDPOINT
    assert session.post.call_args.args[0] == OPENAI_RESPONSES_ENDPOINT
    sent = session.post.call_args.kwargs["json"]
    assert sent["tools"] == [{"type": "web_search"}]
    assert sent["stream"] is True
    assert "sys" in sent["input"] and "find a trail" in sent["input"]


def test_chat_completion_live_search_false_raises_not_implemented():
    """Every real caller in this codebase only ever passes live_search=True
    for the search client -- the non-search path is intentionally
    unsupported rather than silently maintained as dead code."""
    oa = OpenAiSearch(api_key="test", model="test")
    with pytest.raises(NotImplementedError):
        oa.chat_completion(system_prompt="sys", user_prompt="u", live_search=False)


def test_chat_completion_tracks_usage_with_responses_token_fields():
    tracker = MagicMock()
    oa = OpenAiSearch(api_key="test", model="test", usage_tracker=tracker)
    fake_response = _make_streaming_response(_responses_stream_lines("<ul></ul>"))
    session = MagicMock()
    session.post.return_value = fake_response
    oa._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    oa.chat_completion(system_prompt="sys", user_prompt="u", live_search=True)

    tracker.add.assert_called_once()
    assert tracker.add.call_args.kwargs["prompt_tokens"] == 100
    assert tracker.add.call_args.kwargs["completion_tokens"] == 50
    assert tracker.add.call_args.kwargs["provider"] == "openai"
    assert tracker.add.call_args.kwargs["tool_calls"] == 0


def test_chat_completion_tracks_web_search_call_count_from_completed_events() -> None:
    """OpenAI bills web_search separately from tokens ($10/1000 calls) and,
    unlike xAI, exposes no aggregate count in its "usage" object (confirmed
    live 2026-08-17) -- OpenAiSearch must instead count each
    "response.web_search_call.completed" SSE event as it streams by. A real
    2-round agentic search fires that event once per round, so this asserts
    the count is passed through to UsageTracker.add() as tool_calls, not
    silently dropped the way it was before this fix (the exact real-cost gap
    behind the user's ~$5/day-vs-~$0.40/run discrepancy)."""
    tracker = MagicMock()
    oa = OpenAiSearch(api_key="test", model="test", usage_tracker=tracker)
    fake_response = _make_streaming_response(
        _responses_stream_lines("<ul></ul>", web_search_calls=2)
    )
    session = MagicMock()
    session.post.return_value = fake_response
    oa._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    oa.chat_completion(system_prompt="sys", user_prompt="u", live_search=True)

    tracker.add.assert_called_once()
    assert tracker.add.call_args.kwargs["tool_calls"] == 2
    assert tracker.add.call_args.kwargs["provider"] == "openai"


def test_search_uses_web_search_tool_and_normalizes_results():
    oa = OpenAiSearch(api_key="test", model="test")
    fake_response = _make_streaming_response(
        _responses_stream_lines('{"results": [{"title": "Real Place", "url": "https://example.com/real", "snippet": "s"}]}')
    )
    session = MagicMock()
    session.post.return_value = fake_response
    oa._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    results = oa.search("best restaurant in St. George")

    assert results == [{"name": "Real Place", "snippet": "s", "url": "https://example.com/real"}]
    sent = session.post.call_args.kwargs["json"]
    assert sent["tools"] == [{"type": "web_search"}]
    # OpenAI's /v1/responses rejects "text": {"format": {"type": "json_object"}}
    # combined with the web_search tool (400: "Web Search cannot be used with
    # JSON mode.") -- discovered via a real all-OpenAI validation run where
    # this silently killed every _openai_search call. JSON-only output is
    # enforced via the system prompt instead.
    assert "text" not in sent


def test_search_retries_with_stricter_prompt_on_malformed_json():
    oa = OpenAiSearch(api_key="test", model="test")
    bad_response = _make_streaming_response(_responses_stream_lines('{"results": [{"title": "x",}]}'))
    good_response = _make_streaming_response(_responses_stream_lines('{"results": []}'))
    session = MagicMock()
    session.post.side_effect = [bad_response, good_response]
    oa._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    results = oa.search("ambiguous query")

    assert results == []
    assert session.post.call_count == 2


def test_search_returns_empty_list_on_request_exception():
    oa = OpenAiSearch(api_key="test", model="test", network_retries=0)
    session = MagicMock()
    session.post.side_effect = requests.exceptions.ConnectionError("boom")
    oa._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    assert oa.search("query") == []


def test_circuit_breaker_opens_after_threshold_transient_failures():
    oa = OpenAiSearch(api_key="test", model="test", network_retries=0)
    oa._circuit_breaker_threshold = 2
    session = MagicMock()
    session.post.side_effect = requests.exceptions.ConnectionError("boom")
    oa._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    for _ in range(2):
        oa.search("query")

    assert oa.is_circuit_open() is True
    stats = oa.get_circuit_breaker_stats()
    assert stats["trip_count"] == 1
    assert stats["currently_open"] is True


def test_circuit_breaker_check_raises_when_open():
    oa = OpenAiSearch(api_key="test", model="test")
    import time as _time
    oa._circuit_breaker_open_until = _time.monotonic() + 30.0

    with pytest.raises(OpenAiCircuitOpenError):
        oa._circuit_breaker_check()


def test_retries_transient_timeout_then_succeeds():
    oa = OpenAiSearch(api_key="test", model="test", network_retries=1)
    good_response = _make_streaming_response(_responses_stream_lines("<ul></ul>"))
    session = MagicMock()
    session.post.side_effect = [requests.exceptions.ReadTimeout("slow"), good_response]
    oa._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = oa.chat_completion(system_prompt="sys", user_prompt="u", live_search=True)

    assert out == "<ul></ul>"
    assert session.post.call_count == 2


def test_non_retryable_http_error_does_not_retry():
    oa = OpenAiSearch(api_key="test", model="test", network_retries=2)
    resp = MagicMock()
    http_error = requests.exceptions.HTTPError(response=MagicMock(status_code=401))
    resp.raise_for_status.side_effect = http_error
    session = MagicMock()
    session.post.return_value = resp
    oa._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = oa.chat_completion(system_prompt="sys", user_prompt="u", live_search=True)

    assert out == ""
    assert session.post.call_count == 1
