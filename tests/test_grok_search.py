"""Tests for generator.grok_search"""

import time
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


def _http_error_response(status_code: int) -> MagicMock:
    """Matches real `requests` behavior: raise_for_status() raises
    HTTPError with .response pointing back at the response object."""
    resp = MagicMock()
    resp.status_code = status_code
    error = requests.HTTPError(f"{status_code} error", response=resp)
    resp.raise_for_status.side_effect = error
    return resp


def test_post_with_retries_counts_non_retryable_http_error_toward_breaker() -> None:
    """Regression for the 2026-08-15 finding: a non-2xx response (e.g.
    Anthropic's 400 "credit balance is too low") used to come back as a
    successful requests.Session.post() call with no exception at all --
    _post_with_retries recorded it as a breaker SUCCESS, so a persistent
    account-level failure looked perfectly healthy in circuit_breaker_stats
    while every single call was actually being rejected. raise_for_status()
    must now happen inside _post_with_retries so this is visible."""
    gs = GrokSearch(api_key="test", model="test", network_retries=0)
    gs._circuit_breaker_threshold = 2
    gs._circuit_breaker_cooldown_seconds = 60.0

    session = MagicMock()
    session.post.return_value = _http_error_response(400)
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    with pytest.raises(requests.HTTPError):
        gs._post_with_retries({"model": "test"}, "query")
    # Not worth retrying THIS call again (identical payload, same broken
    # account) -- exactly one network attempt for a non-retryable status.
    assert session.post.call_count == 1

    with pytest.raises(requests.HTTPError):
        gs._post_with_retries({"model": "test"}, "query")
    assert session.post.call_count == 2

    # Second qualifying failure reaches threshold=2 -- breaker opens,
    # protecting a third caller without touching the network again.
    with pytest.raises(GrokCircuitOpenError):
        gs._post_with_retries({"model": "test"}, "query")
    assert session.post.call_count == 2


def test_post_with_retries_retries_retryable_http_status_within_same_call() -> None:
    """429/5xx are worth an immediate retry, same as a network timeout --
    unlike a 400/401/403, retrying might actually succeed."""
    gs = GrokSearch(api_key="test", model="test", network_retries=1)
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status = MagicMock()

    session = MagicMock()
    session.post.side_effect = [_http_error_response(429), fake_response]
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = gs._post_with_retries({"model": "test"}, "query")

    assert out is fake_response
    assert session.post.call_count == 2


def test_post_with_retries_does_not_retry_non_retryable_http_status_within_same_call() -> None:
    gs = GrokSearch(api_key="test", model="test", network_retries=2)
    session = MagicMock()
    session.post.return_value = _http_error_response(401)
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    with pytest.raises(requests.HTTPError):
        gs._post_with_retries({"model": "test"}, "query")

    assert session.post.call_count == 1


def test_circuit_breaker_check_returns_probe_flag_after_cooldown_elapses() -> None:
    """Once cooldown elapses, the check grants exactly one caller "probe"
    status (True) instead of fully closing the breaker for everyone at once."""
    gs = GrokSearch(api_key="test", model="test")
    gs._circuit_breaker_open_until = time.monotonic() - 0.01

    assert gs._circuit_breaker_check() is True


def test_circuit_breaker_check_returns_false_when_never_tripped() -> None:
    gs = GrokSearch(api_key="test", model="test")
    assert gs._circuit_breaker_check() is False


def test_circuit_breaker_check_rejects_non_probe_callers_while_probe_in_flight() -> None:
    """Regression for the 2026-08-15 half-open fix: the old design let the
    full concurrent backlog rush back in the instant cooldown elapsed,
    risking an near-instant re-trip off a noisy multi-caller signal. Only
    the first caller after cooldown should get through; everyone else must
    keep failing fast until that probe resolves."""
    gs = GrokSearch(api_key="test", model="test")
    gs._circuit_breaker_open_until = time.monotonic() - 0.01

    assert gs._circuit_breaker_check() is True

    with pytest.raises(GrokCircuitOpenError, match="probe in flight"):
        gs._circuit_breaker_check()


def test_successful_probe_fully_resets_breaker_state() -> None:
    gs = GrokSearch(api_key="test", model="test", network_retries=0)
    gs._circuit_breaker_open_until = time.monotonic() - 0.01
    gs._circuit_breaker_failure_times = [time.monotonic()]

    fake_response = MagicMock()
    fake_response.status_code = 200
    session = MagicMock()
    session.post.return_value = fake_response
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = gs._post_with_retries({"model": "test"}, "query")

    assert out is fake_response
    assert gs._circuit_breaker_open_until == 0.0
    assert gs._circuit_breaker_failure_times == []
    # Fully closed now, not "just recovered" -- a fresh caller isn't
    # treated as a probe.
    assert gs._circuit_breaker_check() is False


def test_failed_probe_reopens_breaker_immediately_without_needing_threshold_failures() -> None:
    """A single failed recovery probe is itself sufficient evidence the
    provider hasn't recovered -- must not require `threshold` fresh
    failures to reaccumulate before reopening."""
    gs = GrokSearch(api_key="test", model="test", network_retries=0)
    gs._circuit_breaker_threshold = 4  # would normally need 4 fresh failures
    gs._circuit_breaker_open_until = time.monotonic() - 0.01

    session = MagicMock()
    session.post.side_effect = _dns_error()
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    with pytest.raises(requests.ConnectionError):
        gs._post_with_retries({"model": "test"}, "query")

    assert gs._circuit_breaker_open_until > time.monotonic()
    with pytest.raises(GrokCircuitOpenError):
        gs._post_with_retries({"model": "test"}, "query")


def test_non_probe_callers_do_not_touch_network_while_probe_pending() -> None:
    gs = GrokSearch(api_key="test", model="test", network_retries=0)
    gs._circuit_breaker_open_until = time.monotonic() - 0.01

    session = MagicMock()
    session.post.side_effect = requests.exceptions.ReadTimeout("still slow")
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    with pytest.raises(requests.exceptions.ReadTimeout):
        gs._post_with_retries({"model": "test"}, "query")
    assert session.post.call_count == 1

    # A second caller arriving while that probe's lease is still active
    # must fail fast without touching the network a second time.
    with pytest.raises(GrokCircuitOpenError):
        gs._post_with_retries({"model": "test"}, "query")
    assert session.post.call_count == 1


def test_circuit_breaker_stats_track_trip_count_and_total_open_seconds() -> None:
    gs = GrokSearch(api_key="test", model="test", network_retries=0)
    gs._circuit_breaker_threshold = 1
    gs._circuit_breaker_cooldown_seconds = 60.0

    session = MagicMock()
    session.post.side_effect = _dns_error()
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    assert gs.get_circuit_breaker_stats() == {
        "trip_count": 0,
        "total_open_seconds": 0.0,
        "currently_open": False,
    }

    with pytest.raises(requests.ConnectionError):
        gs._post_with_retries({"model": "test"}, "query")

    stats_open = gs.get_circuit_breaker_stats()
    assert stats_open["trip_count"] == 1
    assert stats_open["currently_open"] is True

    # Simulate cooldown elapsing, then a successful probe recovers it.
    gs._circuit_breaker_open_until = time.monotonic() - 0.01
    fake_response = MagicMock()
    fake_response.status_code = 200
    session.post.side_effect = None
    session.post.return_value = fake_response
    gs._post_with_retries({"model": "test"}, "query")

    stats_recovered = gs.get_circuit_breaker_stats()
    assert stats_recovered["currently_open"] is False
    # A mocked, near-instant recovery can legitimately round to 0.0s open --
    # the point of this assertion is that recording ran (no exception, a
    # real float back), not that measurable wall-clock time elapsed.
    assert stats_recovered["total_open_seconds"] >= 0.0
    assert stats_recovered["trip_count"] == 1  # unchanged -- recovery isn't a new trip


def test_repeated_probe_failures_within_same_episode_do_not_inflate_trip_count() -> None:
    """A flapping sequence (trip -> failed probe -> failed probe -> ...) is
    one continuous outage episode, not N separate trips -- trip_count
    should reflect distinct open episodes, not every probe attempt."""
    gs = GrokSearch(api_key="test", model="test", network_retries=0)
    gs._circuit_breaker_threshold = 1

    session = MagicMock()
    session.post.side_effect = _dns_error()
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    with pytest.raises(requests.ConnectionError):
        gs._post_with_retries({"model": "test"}, "query")
    assert gs.get_circuit_breaker_stats()["trip_count"] == 1

    gs._circuit_breaker_open_until = time.monotonic() - 0.01
    with pytest.raises(requests.ConnectionError):
        gs._post_with_retries({"model": "test"}, "query")

    assert gs.get_circuit_breaker_stats()["trip_count"] == 1


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


def _responses_stream_lines(
    text: str, *, with_reasoning: bool = True, with_search_call: bool = True,
    usage: dict | None = None,
) -> list[str]:
    """SSE lines matching the real /v1/responses streaming shape captured
    2026-08-15 -- reasoning and web_search_call events interleave with
    response.output_text.delta events, terminated by response.completed
    carrying usage. Only output_text.delta content is the answer text."""
    lines: list[str] = []
    if with_reasoning:
        lines.append('data: {"type": "response.reasoning_summary_text.delta", "delta": "thinking..."}')
    if with_search_call:
        lines.append('data: {"type": "response.web_search_call.searching"}')
    import json as _json
    lines.append("data: " + _json.dumps({"type": "response.output_text.delta", "delta": text}))
    lines.append(
        "data: "
        + _json.dumps({
            "type": "response.completed",
            "response": {"usage": usage or {"input_tokens": 100, "output_tokens": 50}},
        })
    )
    return lines


def _make_streaming_response(lines: list[str]) -> MagicMock:
    """session.post(..., stream=True) is used directly (no `with` block) in
    _post_responses_streaming_with_retries, so the mock just needs
    raise_for_status() and iter_lines()."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.iter_lines = MagicMock(return_value=iter(lines))
    return resp


def _make_real_sse_response(lines: list[str]) -> requests.Response:
    """Build a genuine requests.Response (not a MagicMock) backed by real
    UTF-8-encoded bytes, with the same charset-less "text/event-stream"
    Content-Type header the xAI API actually sends, and .encoding populated
    the same way requests.Session.send() populates it for a real HTTP
    response. Unlike a MagicMock (whose iter_lines just replays pre-decoded
    Python str objects, bypassing any real decoding), this exercises the
    actual requests decode path -- including its RFC-2616 default of
    ISO-8859-1 for charset-less "text/*" responses -- so it can actually
    catch a regression of the dipstick58 mojibake fix."""
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
    default -- is actually exercised."""
    import json as _json

    # Built with ensure_ascii=False, like the real xAI API response body --
    # the curly quote is genuine raw UTF-8 bytes on the wire, not a
    # \uXXXX escape (which would be ASCII-safe and immune to this bug).
    delta_line = "data: " + _json.dumps(
        {"type": "response.output_text.delta", "delta": "Angelica’s Mexican Grill (Irmita’s Casita)"},
        ensure_ascii=False,
    )
    completed_line = "data: " + _json.dumps(
        {"type": "response.completed", "response": {"usage": {"input_tokens": 100, "output_tokens": 50}}}
    )

    gs = GrokSearch(api_key="test", model="test")
    fake_response = _make_real_sse_response([delta_line, completed_line])
    session = MagicMock()
    session.post.return_value = fake_response
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    out = gs.chat_completion(system_prompt="sys", user_prompt="find a restaurant", live_search=True)

    assert out == "Angelica’s Mexican Grill (Irmita’s Casita)"
    assert "â" not in out  # the mis-decode's tell-tale "â" byte artifact


def test_chat_completion_live_search_posts_to_responses_endpoint() -> None:
    """Regression for the 2026-08-14 fix: live_search used to add
    tools:[{"type":"live_search"}] to the (deprecated, now-410) chat-
    completions endpoint. Real search requires POSTing to /v1/responses with
    a differently-shaped request (top-level "input" string, not "messages")."""
    from generator.grok_search import GROK_RESPONSES_ENDPOINT

    gs = GrokSearch(api_key="test", model="test")
    fake_response = _make_streaming_response(
        _responses_stream_lines("<ul><li>Real Trail <a href='https://alltrails.com/x'>Source</a></li></ul>")
    )
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
    fake_response = _make_streaming_response(
        _responses_stream_lines("<ul><li>Clean output only</li></ul>")
    )
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
    fake_response = _make_streaming_response(_responses_stream_lines("<ul></ul>"))
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
    fake_response = _make_streaming_response(
        _responses_stream_lines(
            '{"results": [{"title": "Real Place", "url": "https://example.com/real", "snippet": "s"}]}'
        )
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
    # Balanced braces (so the brace-matching fallback in _extract_json_object
    # finds a candidate substring) but a trailing comma makes that substring
    # itself invalid JSON -- this is the specific shape that raises
    # json.JSONDecodeError (as opposed to unbalanced/no-brace text, which
    # raises ValueError instead and isn't what _grok_search's retry catches).
    bad_response = _make_streaming_response(
        _responses_stream_lines('{"results": [{"title": "x",}]}')
    )
    good_response = _make_streaming_response(_responses_stream_lines('{"results": []}'))
    session = MagicMock()
    session.post.side_effect = [bad_response, good_response]
    gs._get_session = MagicMock(return_value=session)  # type: ignore[method-assign]

    results = gs.search("ambiguous query")

    assert results == []
    assert session.post.call_count == 2
