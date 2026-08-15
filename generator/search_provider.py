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


def _read_search_provider(config_path: str | Any, config_section: str, provider_key: str) -> str:
    try:
        import yaml
        with Path(config_path).open(encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        section = cfg.get(config_section, {}) or {}
        provider = str(section.get(provider_key, "grok") or "grok").strip().lower()
        if provider not in _VALID_SEARCH_PROVIDERS:
            logger.warning(
                "Unknown %s.%s '%s', falling back to grok",
                config_section,
                provider_key,
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
    provider_key: str = "search_provider",
    provider_override: str | None = None,
    grok_model: str | None = None,
    claude_model: str | None = None,
    usage_tracker: Any | None = None,
    usage_operation_prefix: str = "search",
) -> GrokSearch | ClaudeSearch:
    """Construct the configured search/harvest provider client.

    config_section is the top-level config.yaml block to read from (e.g.
    "url_discovery" or "cultural_events"); provider_key is which key within
    that section selects the provider. Defaults to "search_provider" --
    url_discovery.py also uses "nonbatch_search_provider" as a second,
    independent knob within the same section, since its direct-batch HTML
    harvest and its per-item search fallback are different clients that can
    be pinned to different providers (see url_discovery.py's __init__).
    Defaults to grok when unset, invalid, or unreadable -- unchanged
    behavior from before this provider selection existed.

    provider_override (2026-08-15, --search-provider CLI flag): when given,
    skips the config.yaml lookup entirely and forces this provider --
    for a clean, single-provider run with no cross-provider fallback, so a
    run's cost/behavior can be attributed to exactly one provider instead
    of whatever mix the normal batch/fallback split produces. Same
    validation (unknown value falls back to grok, logged) as the
    config-driven path.
    """
    if provider_override:
        provider = str(provider_override).strip().lower()
        if provider not in _VALID_SEARCH_PROVIDERS:
            logger.warning("Unknown --search-provider override '%s', falling back to grok", provider)
            provider = "grok"
    else:
        provider = _read_search_provider(config_path, config_section, provider_key)
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
