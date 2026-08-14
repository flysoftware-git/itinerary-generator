"""Tests for generator.providers.grok"""

from unittest.mock import MagicMock, patch

from generator.providers.grok import GrokProvider


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
