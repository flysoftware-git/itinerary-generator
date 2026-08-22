# generator/providers/grok.py
# xAI Grok provider using the OpenAI-compatible /chat/completions endpoint.
# Uses the same XAI_API_KEY / XAI_MODEL env vars as grok_search.py.

import os
import requests
import logging
from typing import Any

_BASE_URL = "https://api.x.ai/v1/chat/completions"

# Content-generation calls to Grok are slow and variable -- measured at roughly
# 25-90s for a destination-content generation, and longer for larger prompts.
# The previous value here was a flat 60s, which sat *inside* that range: a
# slower-than-median but perfectly healthy response was cut off mid-flight and
# surfaced as a ReadTimeout, indistinguishable from a real provider outage.
# Retrying then re-paid for the whole generation.
#
# One flat number cannot be right for every call, because these calls are not
# the same size. Latency here is dominated by output generation, which is
# sequential, so the honest predictor of how long a call should be allowed to
# take is how many tokens it was allowed to produce. A 1k-token
# what-to-know call and a 4k-token merged destination generation differ by
# minutes, and a timeout sized for one is either far too tight or far too loose
# for the other.
#
# So the budget is derived rather than guessed:
#
#     read_timeout = overhead + max_tokens / tokens_per_second
#
# clamped to a floor and a ceiling. The floor keeps small calls from failing on
# a slow start; the ceiling stops a stuck connection hanging a run forever.
# Every term is overridable, matching the XAI_TIMEOUT_SECONDS /
# XAI_NETWORK_RETRIES convention already used on the search path, and
# XAI_CONTENT_TIMEOUT_SECONDS pins an explicit value when a deployment would
# rather not reason about it at all.
#
# The numbers are deliberately conservative: a timeout is a ceiling for a stuck
# connection, not a service-level target. Being generous costs nothing on the
# happy path, where a normal call still returns in well under a minute.
_DEFAULT_CONNECT_TIMEOUT_SECONDS = 10
_DEFAULT_OVERHEAD_SECONDS = 30
_DEFAULT_OUTPUT_TOKENS_PER_SECOND = 25
_DEFAULT_READ_TIMEOUT_FLOOR_SECONDS = 120
_DEFAULT_READ_TIMEOUT_CEILING_SECONDS = 600

logger = logging.getLogger(__name__)


def _env_number(name: str, default: float) -> float:
    """Positive number from the environment, or the default.

    A typo in an env var should never disable a timeout or crash a run, so a
    bad value warns and falls back rather than propagating.
    """
    raw = os.environ.get(name)
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using %s.", name, raw, default)
        return float(default)
    if value <= 0:
        logger.warning("%s=%r must be positive; using %s.", name, raw, default)
        return float(default)
    return value


def _timeouts(max_tokens: int) -> tuple[float, float]:
    """(connect, read) timeouts for a call allowed to produce `max_tokens`.

    Connect is separated from read so an unreachable endpoint fails in seconds
    while a working one is allowed to think.
    """
    connect = _env_number("XAI_CONNECT_TIMEOUT_SECONDS", _DEFAULT_CONNECT_TIMEOUT_SECONDS)

    pinned = os.environ.get("XAI_CONTENT_TIMEOUT_SECONDS")
    if pinned:
        return connect, _env_number("XAI_CONTENT_TIMEOUT_SECONDS", _DEFAULT_READ_TIMEOUT_FLOOR_SECONDS)

    overhead = _env_number("XAI_CONTENT_TIMEOUT_OVERHEAD_SECONDS", _DEFAULT_OVERHEAD_SECONDS)
    rate = _env_number("XAI_OUTPUT_TOKENS_PER_SECOND", _DEFAULT_OUTPUT_TOKENS_PER_SECOND)
    floor = _env_number("XAI_CONTENT_TIMEOUT_FLOOR_SECONDS", _DEFAULT_READ_TIMEOUT_FLOOR_SECONDS)
    ceiling = _env_number("XAI_CONTENT_TIMEOUT_CEILING_SECONDS", _DEFAULT_READ_TIMEOUT_CEILING_SECONDS)

    try:
        tokens = max(0, int(max_tokens))
    except (TypeError, ValueError):
        tokens = 0

    read = overhead + (tokens / rate)
    return connect, min(max(read, floor), max(floor, ceiling))


class GrokProvider:
    def __init__(self, model: str | None = None) -> None:
        self.api_key = os.environ["XAI_API_KEY"]
        self.model = model or os.environ.get("XAI_MODEL", "grok-latest")
        self.base_url = _BASE_URL

    def create_json_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, dict[str, Any]]:

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        connect_timeout, read_timeout = _timeouts(max_tokens)
        try:
            resp = requests.post(
                self.base_url,
                json=payload,
                headers=headers,
                timeout=(connect_timeout, read_timeout),
            )
        except requests.exceptions.ReadTimeout as exc:
            # Say what was waited for and how to change it. A bare ReadTimeout
            # reads like an outage; the usual cause is a large generation on a
            # slow model.
            raise requests.exceptions.ReadTimeout(
                f"xAI chat completion timed out after {read_timeout:.0f}s "
                f"(model={self.model}, prompt_lens=(system:{len(system_prompt or '')},"
                f"user:{len(user_prompt or '')}), max_tokens={max_tokens}). "
                "Set XAI_CONTENT_TIMEOUT_SECONDS to pin a longer budget, or "
                "XAI_OUTPUT_TOKENS_PER_SECOND if this model is simply slower than assumed."
            ) from exc
        if not resp.ok:
            body = ""
            try:
                body = resp.text or ""
            except Exception:
                body = ""
            body_snippet = body[:2000]
            logger.error(
                "xAI chat completion failed: status=%s model=%s prompt_lens=(system:%d,user:%d) body=%s",
                resp.status_code,
                self.model,
                len(system_prompt or ""),
                len(user_prompt or ""),
                body_snippet,
            )
            error = requests.HTTPError(
                (
                    f"xAI chat completion failed: status={resp.status_code} model={self.model}; "
                    f"body={body_snippet}"
                ),
                response=resp,
            )
            raise error
        data = resp.json()

        text = data["choices"][0]["message"]["content"]
        usage_raw = data.get("usage", {})
        usage = {
            "prompt_tokens": usage_raw.get("prompt_tokens", 0),
            "completion_tokens": usage_raw.get("completion_tokens", 0),
            "model": self.model,
        }

        return text, usage

