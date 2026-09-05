"""Resolve a place name to a Google `place_id`, and render a Maps link from it.

## What this replaces, and why it is better

When an item has a real primary URL, the engine attaches a *secondary* Maps link
so the reader can find the place on a map. Today that link is a **text search**:

    https://www.google.com/maps/search/?api=1&query=Delicate+Arch+Moab+Utah

A search URL is a guess. It resolves to whatever Maps decides the words mean,
which for a common name ("Riverside Trail", "The Mill") can be a different place
in a different state -- the same ambiguity `_maps_fallback_query_text` already
works hard to reduce by padding the query with address text.

A `place_id` is not a guess. It names one specific place, permanently:

    https://www.google.com/maps/place/?q=place_id:ChIJ...

So where a key is configured, this module upgrades the secondary link from
"search for these words" to "this exact place".

## Optional by construction

**With no key configured, nothing changes.** `enabled` is False, no request is
made, and the caller keeps the search URL it would have built anyway. An
open-source user without a Google account sees identical output to before, which
is the bar any optional enrichment has to clear.

## Loud when configured and broken, without being fatal

A configured key that is refused must not fail *silently* -- a run that quietly
stops enriching looks exactly like a run that had nothing to enrich. But this is
a secondary link on an item that already has a good primary one, so aborting the
whole run over it would be disproportionate.

The middle course: the first refusal raises `PlaceResolutionRefused`, the caller
disables the resolver for the remainder of the run, and the reason is logged at
WARNING and counted in `stats`. The run finishes correct; the failure is
impossible to miss; and a broken key is not retried a thousand times.

## Compliance is enforced by the field mask

Google's terms permit storing a `place_id` indefinitely and prohibit caching
other Places content. **This module requests `places.id` and nothing else.** Not
names, not addresses, not ratings, not websites. It cannot leak prohibited
content into a generated artifact because it never asks for it -- which is a
stronger guarantee than a rule someone has to remember.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

PLACES_SEARCH_TEXT_ENDPOINT = "https://places.googleapis.com/v1/places:searchText"

# The whole compliance story in one constant: ask for the identifier, nothing else.
PLACE_ID_ONLY_FIELD_MASK = "places.id"

#: Radius for the location bias, in metres. Wide enough to cover a region
#: named loosely ("Cumberland Plateau") and tight enough to exclude a
#: same-named place in another state.
DEFAULT_BIAS_RADIUS_M = 50_000.0

# Checked in order. The first is the name Google itself uses for the product.
API_KEY_ENV_VARS = ("GOOGLE_MAPS_PLATFORM_KEY", "GOOGLE_MAPS_API_KEY")

# A generated trip is bounded work, so a resolver that has made thousands of
# calls is a bug rather than a big trip. Cheap insurance against a retry loop
# quietly spending a quota.
DEFAULT_MAX_CALLS_PER_RUN = 400

DEFAULT_TIMEOUT_SECONDS = 10.0


class PlaceResolutionRefused(RuntimeError):
    """The provider refused a call for quota, rate or permission reasons.

    Raised rather than returned as "no match", because those two must never be
    confused: a refusal means the enrichment is broken, while no-match is a
    normal answer about one obscure place.
    """


def maps_place_url(place_id: str, query_text: str = "") -> str:
    """A permanent Maps link to one specific place.

    Uses the documented Maps URLs scheme, which needs no key and is not
    metered: `/maps/search/?api=1&query=<text>&query_place_id=<id>`.

    The earlier form -- `/maps/place/?q=place_id:<id>` -- omitted `api=1` and
    is not part of that scheme. Without `api=1` Google routes the request to a
    legacy handler and reports that an API is required, which is what the Old
    Hickory build showed on 83 of its links while the 53 carrying `api=1`
    worked. `query` is required by the scheme even when `query_place_id`
    pins the result, and doubles as the fallback if the id ever stops
    resolving.
    """
    pid = str(place_id or "").strip()
    if not pid:
        return ""
    text = str(query_text or "").strip()
    query = quote(text) if text else quote(f"place_id:{pid}")
    return f"https://www.google.com/maps/search/?api=1&query={query}&query_place_id={quote(pid)}"


class PlaceResolver:
    """Resolves place names to `place_id`s. Inert unless a key is configured."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        session: Any = None,
        max_calls: int = DEFAULT_MAX_CALLS_PER_RUN,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if api_key is None:
            api_key = next((os.environ.get(v, "").strip() for v in API_KEY_ENV_VARS if os.environ.get(v, "").strip()), "")
        self.api_key = api_key or ""
        self.session = session or (requests.Session() if self.api_key else None)
        self.max_calls = int(max_calls)
        self.timeout = float(timeout)
        self._disabled_reason = ""
        self._cache: dict[str, str | None] = {}
        self.stats: dict[str, int] = {"calls": 0, "resolved": 0, "no_match": 0, "refused": 0, "cache_hits": 0}

    @property
    def enabled(self) -> bool:
        """False when unconfigured or shut down after a refusal."""
        return bool(self.api_key) and not self._disabled_reason

    @property
    def disabled_reason(self) -> str:
        return self._disabled_reason

    def disable(self, reason: str) -> None:
        """Stop making calls for the rest of the run, loudly and once."""
        if not self._disabled_reason:
            self._disabled_reason = reason
            logger.warning(
                "Place resolution disabled for the rest of this run: %s. "
                "Secondary map links will fall back to text-search URLs.",
                reason,
            )

    def resolve(
        self,
        query_text: str,
        *,
        bias_lat: float | None = None,
        bias_lng: float | None = None,
        bias_radius_m: float = DEFAULT_BIAS_RADIUS_M,
    ) -> str | None:
        """Return a `place_id` for `query_text`, or None if there is no match.

        `bias_lat`/`bias_lng` bias the search toward where the thing actually
        is, rather than relying on the query text to say so. En-route stops
        need this: their name is qualified with the leg's ARRIVAL destination,
        which can be hundreds of miles away. "Cumberland Plateau Asheville,
        North Carolina" found nothing -- the plateau is in Tennessee -- and
        "Blount Mansion Asheville, North Carolina" resolved only because the
        name is distinctive enough to survive a misleading qualifier. A
        coordinate the pipeline already geocoded is better evidence than
        either.

        Raises `PlaceResolutionRefused` if the provider refuses the call.
        """
        query = str(query_text or "").strip()
        if not query or not self.enabled:
            return None
        has_bias = isinstance(bias_lat, (int, float)) and isinstance(bias_lng, (int, float))
        # The bias is part of the question, so it is part of the cache key.
        # Without it, the same name near two different places would return
        # whichever was asked first.
        cache_key = (
            f"{query}@{round(float(bias_lat), 4)},{round(float(bias_lng), 4)}"
            if has_bias else query
        )
        if cache_key in self._cache:
            self.stats["cache_hits"] += 1
            return self._cache[cache_key]
        if self.stats["calls"] >= self.max_calls:
            self.disable(f"per-run call cap of {self.max_calls} reached")
            return None

        try:
            self.stats["calls"] += 1
            payload: dict[str, Any] = {"textQuery": query, "maxResultCount": 1}
            if has_bias:
                payload["locationBias"] = {
                    "circle": {
                        "center": {"latitude": float(bias_lat), "longitude": float(bias_lng)},
                        "radius": float(bias_radius_m),
                    }
                }
            response = self.session.post(
                PLACES_SEARCH_TEXT_ENDPOINT,
                json=payload,
                headers={
                    "X-Goog-Api-Key": self.api_key,
                    "X-Goog-FieldMask": PLACE_ID_ONLY_FIELD_MASK,
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            # Transport trouble is not a refusal; it is noise. Do not shut down
            # the resolver over one flaky socket.
            logger.debug("Place resolution request failed for %r: %s", query, exc)
            self._cache[cache_key] = None
            return None

        status = getattr(response, "status_code", 0)
        if status in (401, 403, 429) or status >= 500:
            self.stats["refused"] += 1
            body = (getattr(response, "text", "") or "")[:200]
            raise PlaceResolutionRefused(f"HTTP {status} from Places: {body}")

        try:
            places = (response.json() or {}).get("places") or []
        except ValueError:
            self._cache[cache_key] = None
            return None

        place_id = str((places[0] or {}).get("id", "")).strip() if places else ""
        if place_id:
            self.stats["resolved"] += 1
        else:
            self.stats["no_match"] += 1
        self._cache[cache_key] = place_id or None
        return place_id or None

    def maps_url_for(self, query_text: str) -> str:
        """Resolve and render, or return "" so the caller keeps its own fallback.

        Swallows nothing: a refusal still propagates. The empty string means
        "no match", never "something went wrong".
        """
        place_id = self.resolve(query_text)
        return maps_place_url(place_id, query_text) if place_id else ""
