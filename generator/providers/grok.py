# generator/providers/grok.py
# xAI Grok provider using the OpenAI-compatible /chat/completions endpoint.
# Uses the same XAI_API_KEY / XAI_MODEL env vars as grok_search.py.

import json
import logging
import os
import time
from typing import Any

import requests

_BASE_URL = "https://api.x.ai/v1/chat/completions"

# The content call streams, for the reason the search path streams
# (grok_search.py, 2026-08-15): a blocking read cannot tell a model that is
# thinking from a connection that is dead, so any timeout tight enough to catch
# the dead one kills the slow one.
#
# What that cost, measured: this path used to size a single blocking read as
#
#     read_timeout = overhead + max_tokens / tokens_per_second
#
# -- 30 + 6144/25 = 276s for a destination bundle. On 2026-09-19 every
# destination_bundle call of a five-stop trip failed at exactly that number on
# `grok-latest`, three attempts each, and the message said "timed out after
# 276s", which reads as the provider dropping calls. It was our own arithmetic:
# the formula models typing speed, and a model that reasons before it types is
# not in it at all. Nothing was wrong with the calls; we stopped listening.
#
# Streaming replaces one guess about total duration with two honest questions:
#
#   * has the connection gone quiet? -- `_DEFAULT_STREAM_QUIET_SECONDS`, the
#     per-chunk read timeout, which is what actually catches a dead socket. The
#     search path measured real gaps up to ~29s between events on this same
#     API, so this is set well clear of that.
#   * has the whole thing run away? -- `_DEFAULT_STREAM_TOTAL_SECONDS`, a
#     wall-clock ceiling so a stream that stays alive but never finishes cannot
#     hang a run forever.
#
# Neither depends on how fast the model types, so a slower model costs time
# rather than the whole call. Both are overridable, matching the
# XAI_TIMEOUT_SECONDS / XAI_NETWORK_RETRIES convention on the search path;
# XAI_CONTENT_TIMEOUT_SECONDS still pins the ceiling outright.
_DEFAULT_CONNECT_TIMEOUT_SECONDS = 10
_DEFAULT_STREAM_QUIET_SECONDS = 90
_DEFAULT_STREAM_TOTAL_SECONDS = 600

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


def _timeouts() -> tuple[float, float, float]:
    """(connect, quiet, total) seconds for one streamed content call.

    Connect is separated so an unreachable endpoint fails in seconds while a
    working one is allowed to think. `quiet` bounds the gap between chunks --
    the only question a socket can actually answer -- and `total` is the
    wall-clock ceiling.

    None of them is derived from `max_tokens` any more. A number of tokens
    predicts typing time and says nothing about thinking time, which is what
    killed every destination bundle of 2026-09-19 at 276s.
    """
    connect = _env_number("XAI_CONNECT_TIMEOUT_SECONDS", _DEFAULT_CONNECT_TIMEOUT_SECONDS)
    quiet = _env_number("XAI_CONTENT_QUIET_SECONDS", _DEFAULT_STREAM_QUIET_SECONDS)
    # The old name, kept: it pinned the budget that mattered then, and it pins
    # the one that matters now.
    total = _env_number(
        "XAI_CONTENT_TIMEOUT_SECONDS",
        _env_number("XAI_CONTENT_TIMEOUT_CEILING_SECONDS", _DEFAULT_STREAM_TOTAL_SECONDS),
    )
    return connect, quiet, max(total, quiet)


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
        """The model's JSON answer and what it cost, read from a stream.

        Streamed rather than awaited whole (see the module header): the chunks
        are what distinguish a model still working from a connection that has
        died, and one blocking read cannot.
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "stream": True,
            # Without this the streamed reply carries no usage at all, and
            # every content call would report zero tokens and zero cost.
            "stream_options": {"include_usage": True},
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        connect_timeout, quiet_timeout, total_timeout = _timeouts()
        started = time.monotonic()
        try:
            resp = requests.post(
                self.base_url,
                json=payload,
                headers=headers,
                timeout=(connect_timeout, quiet_timeout),
                stream=True,
            )
        except requests.exceptions.ReadTimeout as exc:
            raise requests.exceptions.ReadTimeout(self._budget_message(
                waited=quiet_timeout, which="quiet", max_tokens=max_tokens,
                system_prompt=system_prompt, user_prompt=user_prompt)) from exc

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
            raise requests.HTTPError(
                (
                    f"xAI chat completion failed: status={resp.status_code} model={self.model}; "
                    f"body={body_snippet}"
                ),
                response=resp,
            )

        # The charset trap grok_search.py documents: an SSE Content-Type has no
        # charset, requests then guesses Latin-1 for any text/*, and every
        # accented letter and curly quote in the answer arrives as mojibake.
        resp.encoding = "utf-8"
        parts: list[str] = []
        usage_raw: dict[str, Any] = {}
        try:
            for line in resp.iter_lines(decode_unicode=True):
                if time.monotonic() - started > total_timeout:
                    raise requests.exceptions.ReadTimeout(self._budget_message(
                        waited=total_timeout, which="total", max_tokens=max_tokens,
                        system_prompt=system_prompt, user_prompt=user_prompt))
                if not line or not line.startswith("data: "):
                    continue
                body = line[6:].strip()
                if body == "[DONE]":
                    break
                try:
                    event = json.loads(body)
                except (ValueError, json.JSONDecodeError):
                    continue
                # The usage-bearing chunk carries an empty `choices`, and every
                # content chunk carries no usage; both arrive on this one path.
                if isinstance(event.get("usage"), dict):
                    usage_raw = event["usage"]
                for choice in event.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        parts.append(str(piece))
        except requests.exceptions.ReadTimeout as exc:
            if self._is_ours(exc):
                raise
            raise requests.exceptions.ReadTimeout(self._budget_message(
                waited=quiet_timeout, which="quiet", max_tokens=max_tokens,
                system_prompt=system_prompt, user_prompt=user_prompt)) from exc
        finally:
            resp.close()

        text = "".join(parts)
        if not text.strip():
            # An empty stream is not an answer. Said plainly here rather than
            # left for the JSON parser to report as a syntax error.
            raise requests.exceptions.ReadTimeout(
                f"xAI streamed no content for model={self.model} after "
                f"{time.monotonic() - started:.0f}s. This is the generator's own "
                "reading of the stream, not a provider error."
            )
        usage = {
            "prompt_tokens": usage_raw.get("prompt_tokens", 0),
            "completion_tokens": usage_raw.get("completion_tokens", 0),
            "model": self.model,
        }
        return text, usage

    _OURS = "the generator's own budget"

    @classmethod
    def _is_ours(cls, exc: BaseException) -> bool:
        return cls._OURS in str(exc)

    def _budget_message(self, *, waited: float, which: str, max_tokens: int,
                        system_prompt: str, user_prompt: str) -> str:
        """Why the wait ended, said as ours rather than as the provider's.

        "timed out after 276s" was read as xAI dropping calls for a whole day
        of builds. It was this file's own arithmetic, and the message never
        said so.
        """
        what = ("no data arrived for" if which == "quiet"
                else "the whole call ran past")
        knob = ("XAI_CONTENT_QUIET_SECONDS" if which == "quiet"
                else "XAI_CONTENT_TIMEOUT_SECONDS")
        return (
            f"xAI chat completion stopped by {self._OURS}: {what} {waited:.0f}s "
            f"(model={self.model}, prompt_lens=(system:{len(system_prompt or '')},"
            f"user:{len(user_prompt or '')}), max_tokens={max_tokens}). "
            f"The provider did not refuse the call. Raise {knob} if this model "
            "is simply slower than the budget allows."
        )
