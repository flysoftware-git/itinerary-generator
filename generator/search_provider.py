"""
search_provider.py — search/harvest provider selection.

url_discovery.py and cultural_events.py both need to build a search client
(GrokSearch or ClaudeSearch) with identical config-lookup-then-construct
logic; this is that one shared factory, not a new abstraction layer -- it
exists because two call sites needed byte-identical selection behavior, not
in anticipation of more providers.

Both provider classes match the same surface (chat_completion/search/
is_circuit_open -- see claude_search.py's module docstring), so a caller
holding whichever client this returns can use it without knowing which
provider was selected.
"""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Any

from generator.claude_search import ClaudeSearch
from generator.grok_search import GrokSearch

logger = logging.getLogger(__name__)
_VALID_SEARCH_PROVIDERS = {"grok", "claude"}


def _read_search_provider(config_path: str | Any, config_section: str) -> str:
    try:
        import yaml
        with Path(config_path).open(encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        section = cfg.get(config_section, {}) or {}
        provider = str(section.get("search_provider", "grok") or "grok").strip().lower()
        if provider not in _VALID_SEARCH_PROVIDERS:
            logger.warning(
                "Unknown %s.search_provider '%s', falling back to grok",
                config_section,
                provider,
            )
            return "grok"
        return provider
    except Exception:
        # Same fail-open-to-default philosophy as _load_interest_filters in
        # url_discovery.py -- a missing/unreadable config file shouldn't
        # crash construction of a search client, it should just keep the
        # long-standing default (grok).
        return "grok"


def build_search_client(
    config_path: str | Any,
    *,
    config_section: str,
    grok_model: str | None = None,
    claude_model: str | None = None,
    usage_tracker: Any | None = None,
    usage_operation_prefix: str = "search",
) -> GrokSearch | ClaudeSearch:
    """Construct the configured search/harvest provider client.

    config_section is the top-level config.yaml block to read
    "search_provider" from (e.g. "url_discovery" or "cultural_events") --
    each call site selects grok or claude independently. Defaults to grok
    when unset, invalid, or unreadable -- unchanged behavior from before
    this provider selection existed.
    """
    provider = _read_search_provider(config_path, config_section)
    if provider == "claude":
        return ClaudeSearch(
            model=claude_model,
            usage_tracker=usage_tracker,
            usage_operation_prefix=usage_operation_prefix,
        )
    return GrokSearch(
        model=grok_model,
        usage_tracker=usage_tracker,
        usage_operation_prefix=usage_operation_prefix,
    )
