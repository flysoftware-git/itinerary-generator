"""Tests for generator.search_provider"""

from unittest.mock import MagicMock, patch

from generator.claude_search import ClaudeSearch
from generator.grok_search import GrokSearch
from generator.search_provider import build_search_client


def test_build_search_client_defaults_to_grok_when_config_omits_provider(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("url_discovery:\n  alltrails_source: search\n", encoding="utf-8")

    client = build_search_client(str(config_path), config_section="url_discovery", grok_model="grok-x")

    assert isinstance(client, GrokSearch)


def test_build_search_client_defaults_to_grok_when_config_file_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    missing_path = tmp_path / "does_not_exist.yaml"

    client = build_search_client(str(missing_path), config_section="url_discovery")

    assert isinstance(client, GrokSearch)


def test_build_search_client_selects_claude_when_configured(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("url_discovery:\n  search_provider: claude\n", encoding="utf-8")

    client = build_search_client(str(config_path), config_section="url_discovery", claude_model="claude-x")

    assert isinstance(client, ClaudeSearch)


def test_build_search_client_falls_back_to_grok_on_unknown_provider_value(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("url_discovery:\n  search_provider: bing\n", encoding="utf-8")

    client = build_search_client(str(config_path), config_section="url_discovery")

    assert isinstance(client, GrokSearch)


def test_build_search_client_reads_independent_sections(tmp_path, monkeypatch) -> None:
    """url_discovery and cultural_events select their search provider
    independently -- one section's setting must not leak into the other."""
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "url_discovery:\n  search_provider: claude\ncultural_events:\n  search_provider: grok\n",
        encoding="utf-8",
    )

    url_discovery_client = build_search_client(str(config_path), config_section="url_discovery")
    cultural_events_client = build_search_client(str(config_path), config_section="cultural_events")

    assert isinstance(url_discovery_client, ClaudeSearch)
    assert isinstance(cultural_events_client, GrokSearch)


def test_build_search_client_passes_usage_tracker_and_operation_prefix(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("url_discovery:\n  search_provider: grok\n", encoding="utf-8")
    tracker = MagicMock()

    with patch("generator.search_provider.GrokSearch") as mock_grok_cls:
        build_search_client(
            str(config_path),
            config_section="url_discovery",
            grok_model="grok-x",
            usage_tracker=tracker,
            usage_operation_prefix="url_discovery",
        )

    mock_grok_cls.assert_called_once_with(
        model="grok-x", usage_tracker=tracker, usage_operation_prefix="url_discovery"
    )


def test_build_search_client_provider_override_bypasses_config_entirely(tmp_path, monkeypatch) -> None:
    """--search-provider (2026-08-15): forces a provider regardless of what
    config.yaml says, for a clean single-provider comparison run."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("url_discovery:\n  search_provider: grok\n", encoding="utf-8")

    client = build_search_client(
        str(config_path), config_section="url_discovery", provider_override="claude"
    )

    assert isinstance(client, ClaudeSearch)


def test_build_search_client_provider_override_falls_back_to_grok_on_unknown_value(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("url_discovery:\n  search_provider: claude\n", encoding="utf-8")

    client = build_search_client(
        str(config_path), config_section="url_discovery", provider_override="bing"
    )

    assert isinstance(client, GrokSearch)
