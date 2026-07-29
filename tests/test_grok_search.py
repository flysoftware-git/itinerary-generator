"""Tests for generator.grok_search"""

from unittest.mock import MagicMock
import requests

from generator.grok_search import GrokSearch, _extract_json_object


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
