import threading
from unittest.mock import MagicMock, patch

import openai
import pytest
import requests

from generator.llm_client import LLMCircuitOpenError, MultiLLMClient, UsageTracker


def _make_client(provider: str = "openai", *, threshold: int = 3, cooldown: float = 60.0) -> MultiLLMClient:
    """Minimal MultiLLMClient for tests that bypass __init__ (no real API keys /
    network setup), matching the pattern established by the other tests in
    this file, extended with the circuit-breaker fields generate_json needs."""
    client = MultiLLMClient.__new__(MultiLLMClient)
    client.provider = provider
    client.model = "gpt-4o-mini"
    client.temperature = 0.7
    client.max_tokens = 128
    client.usage_tracker = UsageTracker()
    client._json_cache = {}
    client._json_cache_lock = threading.Lock()
    client._custom_provider = None
    client._circuit_breaker_threshold = threshold
    client._circuit_breaker_window_seconds = 180.0
    client._circuit_breaker_cooldown_seconds = cooldown
    client._circuit_breaker_lock = threading.Lock()
    client._circuit_breaker_failure_times = []
    client._circuit_breaker_open_until = 0.0
    client._fallback_client = None
    return client


def test_estimate_cost_prefers_longest_matching_model_prefix() -> None:
    """Regression for a real cost-accounting bug: 'gpt-4o-mini-2024-07-18' starts
    with both 'gpt-4o' and 'gpt-4o-mini'. Plain first-match iteration over the
    pricing dict picks 'gpt-4o' (listed first) and silently applies the full
    gpt-4o price ($5/$15 per 1M) instead of gpt-4o-mini's ($0.15/$0.60) -- a
    ~29x overestimate with no error surfaced. The longest matching prefix must
    win."""
    tracker = UsageTracker()
    cost = tracker._estimate_cost("openai", "gpt-4o-mini-2024-07-18", 1_000_000, 1_000_000)
    # gpt-4o-mini pricing: $0.15 in + $0.60 out = $0.75, not gpt-4o's $5 + $15 = $20.
    assert cost == 0.75


def test_estimate_cost_has_a_real_entry_for_claude_sonnet_5() -> None:
    """Regression for a real, costly incident (2026-08-15): this pricing
    table had no entry for "claude-sonnet-5" (only stale
    claude-3-5-sonnet-latest/claude-3-7-sonnet-latest rows that don't
    prefix-match it), so _estimate_cost's no-match fallback silently
    returned $0.00 for every real Claude Sonnet 5 call all session --
    right up until the Anthropic account actually ran out of credit.
    Confirmed against platform.claude.com/docs/en/about-claude/pricing:
    $2/MTok input, $10/MTok output."""
    tracker = UsageTracker()
    cost = tracker._estimate_cost("anthropic", "claude-sonnet-5", 1_000_000, 1_000_000)
    assert cost == 12.00
    assert cost != 0.0


def test_add_folds_web_search_tool_call_fee_into_total_estimated_cost() -> None:
    """Regression for the real cost-attribution gap this fix closes: both xAI
    ($5/1000 web_search calls) and OpenAI ($10/1000) bill the search tool
    SEPARATELY from token usage, per actual invocation -- previously
    UsageTracker.add() had no parameter for this at all, so every dipstick
    run's [LLM-COST] summary silently omitted it (real xAI billing ~$5/day
    vs. this estimator's ~$0.40/run). grok:grok-4-fast has no token pricing
    match against 0 tokens, so this isolates the tool-call fee cleanly."""
    tracker = UsageTracker()
    tracker.add(
        provider="grok",
        model="grok-4-fast",
        operation="url_discovery:search",
        prompt_tokens=0,
        completion_tokens=0,
        tool_calls=2,
    )
    summary = tracker.summary()
    # 2 calls * $5.00 / 1000 = $0.01
    assert summary["total_estimated_cost_usd"] == 0.01
    assert summary["total_tool_call_cost_usd"] == 0.01
    assert summary["models"][0]["tool_calls"] == 2
    assert summary["models"][0]["tool_call_cost_usd"] == 0.01


def test_add_combines_token_cost_and_tool_call_fee_for_openai() -> None:
    """OpenAI's rate ($10/1000 calls) differs from xAI's ($5/1000) -- the fee
    must be looked up per-provider, not a single blended constant, and must
    add on top of (not replace) the existing token-based cost."""
    tracker = UsageTracker()
    tracker.add(
        provider="openai",
        model="gpt-4.1-mini",
        operation="openai_search:search",
        prompt_tokens=1_000_000,
        completion_tokens=0,
        tool_calls=1000,
    )
    summary = tracker.summary()
    # token cost: 1M input tokens * $0.40/MTok = $0.40
    # tool fee: 1000 calls * $10.00/1000 = $10.00
    assert summary["total_tool_call_cost_usd"] == 10.00
    assert summary["total_estimated_cost_usd"] == 10.40


def test_add_defaults_tool_calls_to_zero_no_fee_when_omitted() -> None:
    """Every existing caller of add() that doesn't pass tool_calls (content
    generation via generate_json, non-search chat completions) must keep
    costing exactly as before this fix -- no fee appears from thin air."""
    tracker = UsageTracker()
    tracker.add(
        provider="openai",
        model="gpt-4o-mini",
        operation="destination_content:zion",
        prompt_tokens=1000,
        completion_tokens=500,
    )
    summary = tracker.summary()
    assert summary["models"][0]["tool_calls"] == 0
    assert summary["models"][0]["tool_call_cost_usd"] == 0.0
    assert summary["total_tool_call_cost_usd"] == 0.0


def test_generate_json_uses_exact_cache_for_repeated_identical_requests() -> None:
    client = _make_client()
    calls = {"count": 0}

    def fake_call_openai(system_prompt: str, user_prompt: str, temperature: float, max_tokens: int):
        calls["count"] += 1
        return (
            '{"answer": "cached"}',
            {"prompt_tokens": 3, "completion_tokens": 2, "model": "gpt-4o-mini"},
        )

    client._call_openai = fake_call_openai

    first = client.generate_json(
        system_prompt="system",
        user_prompt="prompt",
        operation="op-1",
        temperature=0.2,
        max_tokens=64,
    )
    second = client.generate_json(
        system_prompt="system",
        user_prompt="prompt",
        operation="op-2",
        temperature=0.2,
        max_tokens=64,
    )

    assert calls["count"] == 1
    assert first == {"answer": "cached"}
    assert second == {"answer": "cached"}
    assert client.usage_summary()["total_calls"] == 1


def test_generate_json_opens_circuit_breaker_after_repeated_transient_failures() -> None:
    """Content-generation calls had no circuit breaker at all before this --
    every concurrent/queued destination independently retried into a
    struggling provider. Once enough transient failures land, later callers
    must fail fast (LLMCircuitOpenError) instead of touching the network."""
    client = _make_client(threshold=2, cooldown=60.0)
    calls = {"count": 0}

    def fake_call_openai(system_prompt, user_prompt, temperature, max_tokens):
        calls["count"] += 1
        raise requests.exceptions.ConnectionError("connection refused")

    client._call_openai = fake_call_openai

    for _ in range(2):
        with pytest.raises(requests.exceptions.ConnectionError):
            client.generate_json(system_prompt="s", user_prompt="u", operation="op")
    assert calls["count"] == 2

    with pytest.raises(LLMCircuitOpenError):
        client.generate_json(system_prompt="s", user_prompt="u", operation="op")

    # Breaker short-circuited before touching the network again.
    assert calls["count"] == 2
    assert client.is_circuit_open() is True


def test_generate_json_resets_circuit_breaker_failure_count_after_success() -> None:
    client = _make_client(threshold=2, cooldown=60.0)
    responses = [
        requests.exceptions.ConnectionError("connection refused"),
        ('{"ok": true}', {"prompt_tokens": 1, "completion_tokens": 1, "model": "gpt-4o-mini"}),
        requests.exceptions.ConnectionError("connection refused"),
    ]

    def fake_call_openai(system_prompt, user_prompt, temperature, max_tokens):
        outcome = responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    client._call_openai = fake_call_openai

    with pytest.raises(requests.exceptions.ConnectionError):
        client.generate_json(system_prompt="s", user_prompt="u", operation="op")

    out = client.generate_json(system_prompt="s", user_prompt="u2", operation="op")
    assert out == {"ok": True}

    # The success cleared the failure count, so a lone subsequent failure
    # alone (threshold=2) must not trip the breaker.
    with pytest.raises(requests.exceptions.ConnectionError):
        client.generate_json(system_prompt="s", user_prompt="u3", operation="op")
    assert client.is_circuit_open() is False


def test_generate_json_does_not_count_non_transient_errors_toward_breaker() -> None:
    """A 400 bad-request or auth failure is never going to succeed on retry --
    it must not count toward tripping the breaker (which exists to protect
    against provider *outages*, not application-level request bugs)."""
    client = _make_client(threshold=1, cooldown=60.0)
    response = MagicMock()
    response.status_code = 400
    http_error = requests.exceptions.HTTPError("bad request", response=response)

    def fake_call_openai(system_prompt, user_prompt, temperature, max_tokens):
        raise http_error

    client._call_openai = fake_call_openai

    with pytest.raises(requests.exceptions.HTTPError):
        client.generate_json(system_prompt="s", user_prompt="u", operation="op")

    assert client.is_circuit_open() is False


def test_generate_json_fails_over_to_fallback_when_primary_circuit_open() -> None:
    """The actual point of this session's failover work: when the primary
    provider's breaker is open and a fallback is configured, calls route to
    the fallback transparently instead of raising LLMCircuitOpenError."""
    fallback = _make_client(provider="anthropic")
    fallback._call_anthropic = lambda *a, **k: (
        '{"answer": "from-fallback"}',
        {"prompt_tokens": 2, "completion_tokens": 2, "model": "claude-3-5-sonnet-latest"},
    )

    primary = _make_client(provider="openai", threshold=1, cooldown=60.0)
    primary._fallback_client = fallback
    primary._call_openai = lambda *a, **k: (_ for _ in ()).throw(
        requests.exceptions.ConnectionError("refused")
    )

    # Trip the primary's breaker (threshold=1).
    with pytest.raises(requests.exceptions.ConnectionError):
        primary.generate_json(system_prompt="s", user_prompt="u", operation="op")
    assert primary.is_circuit_open() is True

    # Next call must transparently use the fallback, not raise.
    out = primary.generate_json(system_prompt="s", user_prompt="u2", operation="op")
    assert out == {"answer": "from-fallback"}


def test_generate_json_without_fallback_still_raises_when_circuit_open() -> None:
    """No fallback configured (the default) -- unchanged behavior: raise
    LLMCircuitOpenError so the caller's own failure handling takes over."""
    client = _make_client(provider="openai", threshold=1, cooldown=60.0)
    client._call_openai = lambda *a, **k: (_ for _ in ()).throw(
        requests.exceptions.ConnectionError("refused")
    )

    with pytest.raises(requests.exceptions.ConnectionError):
        client.generate_json(system_prompt="s", user_prompt="u", operation="op")

    with pytest.raises(LLMCircuitOpenError):
        client.generate_json(system_prompt="s", user_prompt="u2", operation="op")


def test_generate_json_failover_shares_usage_tracker_with_primary() -> None:
    """Cost accounting must stay centralized -- spend incurred on the
    fallback provider must still show up in the primary's usage_tracker,
    the one the rest of the app queries for cost reporting."""
    shared_tracker = UsageTracker()
    fallback = _make_client(provider="anthropic")
    fallback.usage_tracker = shared_tracker
    fallback._call_anthropic = lambda *a, **k: (
        '{"ok": true}',
        {"prompt_tokens": 10, "completion_tokens": 5, "model": "claude-3-5-sonnet-latest"},
    )

    primary = _make_client(provider="openai", threshold=1, cooldown=60.0)
    primary.usage_tracker = shared_tracker
    primary._fallback_client = fallback
    primary._call_openai = lambda *a, **k: (_ for _ in ()).throw(
        requests.exceptions.ConnectionError("refused")
    )

    with pytest.raises(requests.exceptions.ConnectionError):
        primary.generate_json(system_prompt="s", user_prompt="u", operation="op")
    primary.generate_json(system_prompt="s", user_prompt="u2", operation="op")

    summary = shared_tracker.summary()
    assert summary["total_calls"] == 1
    assert summary["models"][0]["provider"] == "anthropic"


@pytest.mark.parametrize(
    "exc, expected",
    [
        (requests.exceptions.Timeout("timed out"), True),
        (requests.exceptions.ConnectionError("refused"), True),
        (openai.APIConnectionError(request=MagicMock()), True),
        (KeyError("missing"), False),
        (ValueError("bad json"), False),
    ],
)
def test_is_transient_llm_error_classifies_network_and_non_network_errors(exc, expected) -> None:
    assert MultiLLMClient._is_transient_llm_error(exc) is expected


@pytest.mark.parametrize("status_code, expected", [(429, True), (500, True), (503, True), (400, False), (404, False)])
def test_is_transient_llm_error_classifies_http_status_codes(status_code, expected) -> None:
    response = MagicMock()
    response.status_code = status_code
    exc = requests.exceptions.HTTPError("http error", response=response)
    assert MultiLLMClient._is_transient_llm_error(exc) is expected


def test_normalize_model_for_provider_remaps_grok_model_for_openai(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    out = MultiLLMClient._normalize_model_for_provider("openai", "grok-4.5")
    assert out == "gpt-4o-mini"


def test_normalize_model_for_provider_remaps_openai_model_for_grok() -> None:
    out = MultiLLMClient._normalize_model_for_provider("grok", "gpt-4o-mini")
    assert out == "grok-latest"


def test_normalize_model_for_provider_remaps_openai_model_for_anthropic(monkeypatch) -> None:
    """Regression for issue #65/#64: the original checks only ever guarded
    against grok model names leaking into other providers -- switching
    provider: openai -> anthropic while leaving a stale model: gpt-4o-mini
    behind previously sailed through untouched and only failed deep inside
    the first Anthropic API call."""
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    out = MultiLLMClient._normalize_model_for_provider("anthropic", "gpt-4o-mini")
    assert out == "claude-3-5-sonnet-latest"


def test_normalize_model_for_provider_remaps_claude_model_for_gemini(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "gemini-1.5-pro")
    out = MultiLLMClient._normalize_model_for_provider("gemini", "claude-3-5-sonnet-latest")
    assert out == "gemini-1.5-pro"


def test_normalize_model_for_provider_leaves_unrecognized_model_untouched() -> None:
    """A custom Azure deployment alias (or any model name not in the known
    provider families) must pass through unchanged rather than being
    silently overridden -- only a model recognizably belonging to a
    *different* provider's family should trigger a fallback."""
    out = MultiLLMClient._normalize_model_for_provider("azure_openai", "my-custom-deployment-name")
    assert out == "my-custom-deployment-name"


def test_construct_fails_fast_when_anthropic_api_key_missing(tmp_path, monkeypatch) -> None:
    """Regression for issue #65/#64: a missing ANTHROPIC_API_KEY used to only
    surface on the first generate_json call, potentially after Stage 1/2
    already ran -- it must fail at construct time like openai/azure_openai
    already do."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("ai:\n  provider: anthropic\n  model: claude-3-5-sonnet-latest\n", encoding="utf-8")

    try:
        MultiLLMClient(config_path=str(config_path))
        assert False, "expected missing ANTHROPIC_API_KEY to fail at construct time"
    except KeyError as exc:
        assert "ANTHROPIC_API_KEY" in str(exc)


def test_construct_fails_fast_when_gemini_api_key_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("ai:\n  provider: gemini\n  model: gemini-1.5-flash\n", encoding="utf-8")

    try:
        MultiLLMClient(config_path=str(config_path))
        assert False, "expected missing GEMINI_API_KEY to fail at construct time"
    except KeyError as exc:
        assert "GEMINI_API_KEY" in str(exc)


def test_construct_builds_fallback_client_from_config_and_fails_fast_on_missing_key(tmp_path, monkeypatch) -> None:
    """ai.fallback_provider/fallback_model must construct a real fallback
    MultiLLMClient eagerly at startup -- not lazily on first failover, which
    would surface a missing fallback API key deep into a run at exactly the
    moment the primary is already struggling."""
    monkeypatch.setenv("OPENAI_API_KEY", "primary-test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "ai:\n"
        "  provider: openai\n"
        "  model: gpt-4o-mini\n"
        "  fallback_provider: anthropic\n"
        "  fallback_model: claude-3-5-sonnet-latest\n",
        encoding="utf-8",
    )

    try:
        MultiLLMClient(config_path=str(config_path))
        assert False, "expected missing fallback ANTHROPIC_API_KEY to fail at construct time"
    except KeyError as exc:
        assert "ANTHROPIC_API_KEY" in str(exc)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fallback-test-key")
    client = MultiLLMClient(config_path=str(config_path))
    assert client._fallback_client is not None
    assert client._fallback_client.provider == "anthropic"
    assert client._fallback_client.model == "claude-3-5-sonnet-latest"
    assert client._fallback_client.usage_tracker is client.usage_tracker


def test_construct_ignores_fallback_provider_matching_primary(tmp_path, monkeypatch) -> None:
    """A fallback_provider equal to the primary provider is nonsensical
    (and would recurse into constructing itself as its own fallback via the
    same config.yaml) -- must be ignored, not acted on."""
    monkeypatch.setenv("OPENAI_API_KEY", "primary-test-key")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "ai:\n  provider: openai\n  model: gpt-4o-mini\n  fallback_provider: openai\n",
        encoding="utf-8",
    )

    client = MultiLLMClient(config_path=str(config_path))
    assert client._fallback_client is None


def test_call_anthropic_marks_system_prompt_as_ephemeral_cache_breakpoint(monkeypatch) -> None:
    """Regression for issue #66 ('reduce repeated context windows'): the same
    static system_prompt.txt is sent verbatim on every call in a run. Without
    a cache_control breakpoint on the system block, Anthropic re-bills that
    full prefix every single call instead of serving repeats from cache."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = MultiLLMClient.__new__(MultiLLMClient)
    client.model = "claude-3-5-sonnet-latest"

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "content": [{"type": "text", "text": "hello"}],
        "model": "claude-3-5-sonnet-latest",
        "usage": {
            "input_tokens": 5,
            "output_tokens": 3,
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 200,
        },
    }

    with patch("generator.llm_client.requests.post", return_value=fake_response) as mock_post:
        text, usage = client._call_anthropic("sys prompt", "user prompt", 0.2, 512)

    assert text == "hello"
    sent_body = mock_post.call_args.kwargs["json"]
    assert sent_body["system"] == [
        {"type": "text", "text": "sys prompt", "cache_control": {"type": "ephemeral"}}
    ]
    # Cache-tier tokens must be folded into prompt_tokens or usage tracking
    # goes blind to most of the real input cost once caching is active.
    assert usage["prompt_tokens"] == 5 + 100 + 200


def test_call_anthropic_warns_when_response_truncated(monkeypatch, caplog) -> None:
    """Regression for issue #65/#64: a truncated Anthropic response used to
    fail silently downstream at JSON parsing with a confusing error instead
    of a clear 'raise max_tokens' warning."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = MultiLLMClient.__new__(MultiLLMClient)
    client.model = "claude-3-5-sonnet-latest"

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "content": [{"type": "text", "text": '{"incomplete":'}],
        "model": "claude-3-5-sonnet-latest",
        "stop_reason": "max_tokens",
        "usage": {"input_tokens": 5, "output_tokens": 512},
    }

    with patch("generator.llm_client.requests.post", return_value=fake_response), caplog.at_level("WARNING"):
        client._call_anthropic("sys prompt", "user prompt", 0.2, 512)

    assert any("truncated" in rec.message for rec in caplog.records)


def test_call_gemini_warns_when_response_truncated(monkeypatch, caplog) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = MultiLLMClient.__new__(MultiLLMClient)
    client.model = "gemini-1.5-flash"

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "candidates": [
            {"content": {"parts": [{"text": '{"incomplete":'}]}, "finishReason": "MAX_TOKENS"}
        ],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 512},
    }

    with patch("generator.llm_client.requests.post", return_value=fake_response), caplog.at_level("WARNING"):
        client._call_gemini("sys prompt", "user prompt", 0.2, 512)

    assert any("truncated" in rec.message for rec in caplog.records)