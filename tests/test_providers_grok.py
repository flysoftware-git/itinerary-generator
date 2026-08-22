"""Tests for generator.providers.grok"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from generator.providers.grok import (
    _DEFAULT_READ_TIMEOUT_FLOOR_SECONDS,
    GrokProvider,
    _timeouts,
)


def _provider() -> GrokProvider:
    provider = GrokProvider.__new__(GrokProvider)
    provider.api_key = "test-key"
    provider.model = "grok-latest"
    provider.base_url = "https://api.x.ai/v1/chat/completions"
    return provider


def test_create_json_completion_requests_json_object_response_format() -> None:
    """Regression: create_json_completion is a JSON-only contract (the caller
    always parses the response as JSON), but the payload never told the API
    that -- unlike the OpenAI/Azure paths in llm_client.py, which both set
    response_format={'type': 'json_object'}. Without it, Grok is free to wrap
    the JSON in prose or markdown fences."""
    provider = GrokProvider.__new__(GrokProvider)
    provider.api_key = "test-key"
    provider.model = "grok-latest"
    provider.base_url = "https://api.x.ai/v1/chat/completions"

    fake_response = MagicMock()
    fake_response.ok = True
    fake_response.json.return_value = {
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }

    with patch("generator.providers.grok.requests.post", return_value=fake_response) as mock_post:
        text, usage = provider.create_json_completion(
            system_prompt="sys",
            user_prompt="user",
            temperature=0.1,
            max_tokens=100,
        )

    assert text == '{"ok": true}'
    sent_payload = mock_post.call_args.kwargs["json"]
    assert sent_payload["response_format"] == {"type": "json_object"}


def test_read_timeout_clears_observed_generation_latency() -> None:
    """Regression: the read timeout was a flat 60s, inside the 25-90s band a
    content generation actually takes, so a healthy-but-slow response was cut
    off and surfaced as a ReadTimeout -- indistinguishable from an outage, and
    the retry re-paid for the whole generation.

    Connect and read are also separated now: an unreachable endpoint should
    fail in seconds while a working one is allowed to think."""
    provider = _provider()

    fake_response = MagicMock()
    fake_response.ok = True
    fake_response.json.return_value = {
        "choices": [{"message": {"content": "{}"}}],
        "usage": {},
    }

    with patch("generator.providers.grok.requests.post", return_value=fake_response) as mock_post:
        provider.create_json_completion(
            system_prompt="sys", user_prompt="user", temperature=0.1, max_tokens=100
        )

    connect_timeout, read_timeout = mock_post.call_args.kwargs["timeout"]
    assert connect_timeout <= 30
    assert read_timeout >= 120, "read timeout must clear the observed 25-90s generation band"


def test_timeouts_are_overridable_by_environment(monkeypatch) -> None:
    """Matches the XAI_TIMEOUT_SECONDS convention already used on the search
    path -- a deployment that knows its own model is slower should not need a
    code change. An explicit value pins the budget and skips the scaling."""
    monkeypatch.setenv("XAI_CONNECT_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("XAI_CONTENT_TIMEOUT_SECONDS", "600")
    assert _timeouts(4096) == (3.0, 600.0)


def test_read_timeout_scales_with_the_size_of_the_generation() -> None:
    """One flat number cannot fit every call. Latency is dominated by output
    generation, which is sequential, so a call allowed to produce four times as
    many tokens must be allowed proportionally longer to produce them."""
    small = _timeouts(1024)[1]
    large = _timeouts(8192)[1]
    assert large > small


def test_small_calls_still_get_a_floor() -> None:
    """A tiny max_tokens must not compute its way down to a two-second budget:
    the floor covers queueing and a slow start."""
    assert _timeouts(1)[1] >= _DEFAULT_READ_TIMEOUT_FLOOR_SECONDS


def test_enormous_calls_are_capped() -> None:
    """The ceiling is what stops a stuck connection hanging a whole run."""
    assert _timeouts(10_000_000)[1] <= 600


def test_nonsense_timeout_values_fall_back_to_defaults(monkeypatch) -> None:
    """A typo in an env var should not disable the timeout or crash the run."""
    monkeypatch.setenv("XAI_CONTENT_TIMEOUT_SECONDS", "not-a-number")
    assert _timeouts(4096)[1] == _DEFAULT_READ_TIMEOUT_FLOOR_SECONDS

    monkeypatch.setenv("XAI_CONTENT_TIMEOUT_SECONDS", "-5")
    assert _timeouts(4096)[1] == _DEFAULT_READ_TIMEOUT_FLOOR_SECONDS


def test_timeout_error_says_what_timed_out_and_how_to_change_it() -> None:
    """A bare ReadTimeout reads like a provider outage. The usual cause is a
    large generation on a slow model, and the fix is an env var."""
    provider = _provider()

    with patch(
        "generator.providers.grok.requests.post",
        side_effect=requests.exceptions.ReadTimeout("socket timeout"),
    ):
        with pytest.raises(requests.exceptions.ReadTimeout) as exc:
            provider.create_json_completion(
                system_prompt="sys", user_prompt="user", temperature=0.1, max_tokens=4096
            )

    message = str(exc.value)
    assert "timed out after" in message
    assert "XAI_CONTENT_TIMEOUT_SECONDS" in message
    assert "grok-latest" in message
