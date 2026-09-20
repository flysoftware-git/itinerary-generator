"""Tests for generator.providers.grok.

The content call streams. It did not until 2026-09-19, when every
`destination_bundle` call of a five-stop trip failed on `grok-latest` after
exactly 275.76s -- `30 + 6144/25`, this module's own formula for how long a
generation "should" take, reported as "timed out after 276s" and read all day
as xAI dropping calls. A token count predicts typing time and says nothing
about thinking time.

So the budget is no longer a guess about total duration. A streamed reply
answers the two questions a socket can actually answer: has it gone quiet, and
has the whole thing run away.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from generator.providers.grok import (
    _DEFAULT_STREAM_QUIET_SECONDS,
    _DEFAULT_STREAM_TOTAL_SECONDS,
    GrokProvider,
    _timeouts,
)


def _provider() -> GrokProvider:
    provider = GrokProvider.__new__(GrokProvider)
    provider.api_key = "test-key"
    provider.model = "grok-latest"
    provider.base_url = "https://api.x.ai/v1/chat/completions"
    return provider


def _chunk(text: str) -> str:
    return 'data: {"choices": [{"delta": {"content": "%s"}}]}' % text


def _usage_chunk(prompt: int, completion: int) -> str:
    return ('data: {"choices": [], "usage": {"prompt_tokens": %d, '
            '"completion_tokens": %d}}' % (prompt, completion))


def _streamed(*lines: str, ok: bool = True) -> MagicMock:
    resp = MagicMock()
    resp.ok = ok
    resp.iter_lines.return_value = iter(lines)
    return resp


def _call(provider: GrokProvider, response: MagicMock, max_tokens: int = 100):
    with patch("generator.providers.grok.requests.post", return_value=response) as post:
        text, usage = provider.create_json_completion(
            system_prompt="sys", user_prompt="user", temperature=0.1, max_tokens=max_tokens)
    return text, usage, post


def test_the_answer_is_assembled_from_the_chunks() -> None:
    text, usage, post = _call(_provider(), _streamed(
        _chunk('{'), _chunk('\\"ok\\": '), _chunk('true}'),
        _usage_chunk(1200, 640), "data: [DONE]"))

    assert text == '{"ok": true}'
    assert post.call_args.kwargs["stream"] is True
    assert usage == {"prompt_tokens": 1200, "completion_tokens": 640, "model": "grok-latest"}


def test_usage_is_asked_for_or_every_call_would_cost_nothing() -> None:
    """A streamed reply carries no usage unless it is requested, and a call
    reported at zero tokens is a call reported at zero dollars -- the silent
    cost bug this repo has already had three times."""
    _text, _usage, post = _call(_provider(), _streamed(_chunk("{}"), "data: [DONE]"))

    assert post.call_args.kwargs["json"]["stream_options"] == {"include_usage": True}


def test_the_json_only_contract_is_still_declared() -> None:
    _text, _usage, post = _call(_provider(), _streamed(_chunk("{}"), "data: [DONE]"))

    assert post.call_args.kwargs["json"]["response_format"] == {"type": "json_object"}


def test_the_stream_is_read_as_utf8() -> None:
    """An SSE Content-Type carries no charset, so requests guesses Latin-1 and
    every accented letter arrives as mojibake (grok_search.py, dipstick58)."""
    resp = _streamed(_chunk("{}"), "data: [DONE]")

    _call(_provider(), resp)

    assert resp.encoding == "utf-8"


def test_the_budget_no_longer_depends_on_how_many_tokens_were_allowed() -> None:
    """The whole of the 276s defect: a token count predicted typing time, and
    the model was thinking. `_timeouts` has no token count to reason from."""
    assert _timeouts() == (10.0, _DEFAULT_STREAM_QUIET_SECONDS, _DEFAULT_STREAM_TOTAL_SECONDS)


def test_the_budget_is_overridable(monkeypatch) -> None:
    monkeypatch.setenv("XAI_CONNECT_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("XAI_CONTENT_QUIET_SECONDS", "45")
    monkeypatch.setenv("XAI_CONTENT_TIMEOUT_SECONDS", "900")

    assert _timeouts() == (3.0, 45.0, 900.0)


def test_a_nonsense_budget_falls_back_rather_than_disabling_the_timeout(monkeypatch) -> None:
    monkeypatch.setenv("XAI_CONTENT_QUIET_SECONDS", "not-a-number")
    monkeypatch.setenv("XAI_CONTENT_TIMEOUT_SECONDS", "-5")

    assert _timeouts() == (10.0, _DEFAULT_STREAM_QUIET_SECONDS, _DEFAULT_STREAM_TOTAL_SECONDS)


def test_a_quiet_connection_says_the_budget_was_ours() -> None:
    """'timed out after 276s' was read as the provider dropping calls for a
    whole day of builds. It was this module's arithmetic, and the message never
    said so."""
    provider = _provider()

    with patch("generator.providers.grok.requests.post",
               side_effect=requests.exceptions.ReadTimeout("socket timeout")):
        with pytest.raises(requests.exceptions.ReadTimeout) as caught:
            provider.create_json_completion(system_prompt="sys", user_prompt="user",
                                            temperature=0.1, max_tokens=4096)

    said = str(caught.value)
    assert "the generator's own budget" in said
    assert "The provider did not refuse the call." in said
    assert "XAI_CONTENT_QUIET_SECONDS" in said
    assert "grok-latest" in said and "max_tokens=4096" in said


def test_a_stream_that_never_ends_is_cut_at_the_ceiling(monkeypatch) -> None:
    """A stream can stay alive and never finish; the ceiling is what stops it
    hanging a run. It says which budget ended the call, and which knob moves
    it."""
    ticks = iter([0.0, 10.0] + [10_000.0] * 60)
    monkeypatch.setattr("generator.providers.grok.time.monotonic", lambda: next(ticks))
    forever = _streamed(*[_chunk("x") for _ in range(40)])

    with patch("generator.providers.grok.requests.post", return_value=forever):
        with pytest.raises(requests.exceptions.ReadTimeout) as caught:
            _provider().create_json_completion(system_prompt="sys", user_prompt="user",
                                               temperature=0.1, max_tokens=100)

    said = str(caught.value)
    assert "the generator's own budget" in said
    assert "the whole call ran past" in said
    assert "XAI_CONTENT_TIMEOUT_SECONDS" in said


def test_a_stream_carrying_nothing_is_not_an_empty_answer() -> None:
    """Returning "" hands the caller a JSON syntax error to explain instead of
    the thing that actually happened."""
    with pytest.raises(requests.exceptions.ReadTimeout) as caught:
        _call(_provider(), _streamed("data: [DONE]"))

    assert "streamed no content" in str(caught.value)


def test_a_refusal_is_still_reported_with_its_body() -> None:
    """A 400 or a 429 is the provider's answer and must not be dressed up as a
    timeout -- llm_client's breaker and #164's fallback both read the status."""
    refused = _streamed(ok=False)
    refused.status_code = 429
    refused.text = "rate limit reached"

    with pytest.raises(requests.HTTPError) as caught:
        _call(_provider(), refused)

    assert "status=429" in str(caught.value)
    assert "rate limit reached" in str(caught.value)


def test_a_slow_model_is_not_cut_off_for_being_slow(monkeypatch) -> None:
    """The regression this module exists for: a generation that takes longer
    than any token-derived budget still completes, because no budget is derived
    from tokens any more. 276 seconds of thinking, then the answer."""
    ticks = iter([0.0, 276.0, 277.0, 278.0, 279.0, 280.0])
    monkeypatch.setattr("generator.providers.grok.time.monotonic", lambda: next(ticks))

    text, _usage, _post = _call(_provider(), _streamed(
        _chunk('{\\"late\\": true}'), "data: [DONE]"), max_tokens=6144)

    assert text == '{"late": true}'
