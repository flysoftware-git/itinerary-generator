"""places_filter.py — Places API as a FILTER, publishing nothing it returns.

Why
---
Four rewrites of the restaurant batch prompt did not stop Berlin returning
three Michelin restaurants on a "No fine dining" brief, and Amsterdam shipped a
single restaurant after every gate had taken its cut. One Places Text Search
call for Amsterdam, price-filtered, returns twenty real inexpensive places
including Vlaams Friteshuis Vleminckx.

Scope, deliberately narrow
--------------------------
This module decides WHICH of our own candidates survive. It publishes nothing.

  * A price level is read and used to drop a candidate, or to keep one whose
    price we could not determine ourselves. It is never rendered.
  * `place_id` is retained for matching within a run. It is the one field the
    terms exempt from caching, and it is not published either.
  * Names, cuisines, websites, ratings and badges continue to come from where
    they come from today.

That boundary is the whole point, and `docs/design/places-for-restaurants.md`
records why: Places content may not be stored, and this project publishes a
static file that is emailed and kept. Using the data to make a decision at
build time is defensible in a way that baking it into the artifact is not.

Nothing is cached to disk, for the same reason `transit_estimate` caches
nothing: persisting the response is the part the restriction is squarely about,
and one call per destination is a rounding error against a run.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

TEXT_SEARCH_ENDPOINT = "https://places.googleapis.com/v1/places:searchText"

#: Enough to decide, nothing more. Deliberately omits websiteUri, rating,
#: photos and editorial summaries -- fields we would be tempted to publish.
_FIELD_MASK = "places.id,places.displayName,places.priceLevel"

_LOW_BUDGET_LEVELS = ("PRICE_LEVEL_INEXPENSIVE", "PRICE_LEVEL_MODERATE")
_HIGH_BUDGET_LEVELS = ("PRICE_LEVEL_EXPENSIVE", "PRICE_LEVEL_VERY_EXPENSIVE")

#: Levels that disqualify a candidate on a low-cost brief.
_TOO_EXPENSIVE = frozenset(_HIGH_BUDGET_LEVELS)

_TIMEOUT_SECONDS = 20


def normalize_name(value: str) -> str:
    """Loose key for matching our candidate names against Places results.

    Case, punctuation and spacing are dropped: the batch writes
    "Sam's Sports Grill" and "Mustafa's Gemuse Kebab" where Places writes
    "Sam's Sports Grill" and "Mustafa's Gemüse Kebap". Accents are folded
    crudely rather than perfectly -- a missed match costs a rescue, not a
    wrong answer.
    """
    text = str(value or "").lower()
    for pair in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss"), ("é", "e"), ("è", "e"), ("á", "a")):
        text = text.replace(*pair)
    return re.sub(r"[^a-z0-9]", "", text)


class PlacesBudgetFilter:
    """One instance per run. Holds no disk state and publishes no field."""

    def __init__(self, api_key: str | None = None, *, timeout: int = _TIMEOUT_SECONDS) -> None:
        self._key = str(api_key or os.environ.get("GOOGLE_MAPS_PLATFORM_KEY", "") or "").strip()
        self._timeout = timeout
        self._by_destination: dict[str, dict[str, dict[str, Any]]] = {}
        self.call_count = 0

    @property
    def available(self) -> bool:
        return bool(self._key)

    def _search(self, destination: str, *, price_filtered: bool) -> dict[str, dict[str, Any]]:
        query = (
            f"inexpensive restaurants in {destination}"
            if price_filtered
            else f"restaurants in {destination}"
        )
        body: dict[str, Any] = {
            "textQuery": query,
            "includedType": "restaurant",
            "maxResultCount": 20,
        }
        if price_filtered:
            body["priceLevels"] = list(_LOW_BUDGET_LEVELS)

        results: dict[str, dict[str, Any]] = {}
        try:
            request = urllib.request.Request(
                TEXT_SEARCH_ENDPOINT,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": self._key,
                    "X-Goog-FieldMask": _FIELD_MASK,
                },
            )
            self.call_count += 1
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.load(response)
            for place in payload.get("places", []) or []:
                key = normalize_name((place.get("displayName") or {}).get("text", ""))
                if key:
                    results[key] = {
                        "place_id": place.get("id", ""),
                        "price_level": str(place.get("priceLevel", "") or ""),
                    }
        except urllib.error.HTTPError as exc:
            logger.warning("Places filter failed for '%s': HTTP %s", destination, exc.code)
        except Exception as exc:
            logger.warning("Places filter failed for '%s': %s", destination, exc)
        return results

    def lookup(self, destination: str, *, low_budget: bool) -> dict[str, dict[str, Any]]:
        """Places at this destination, keyed by normalized name.

        TWO searches on a low-cost brief, not one. Filtering server-side makes
        expensive places absent from the response, which is indistinguishable
        from a place Places has never heard of -- Ciel Bleu and a corner cafe
        both came back "unknown", so the filter could not reject either.

        The unfiltered pass supplies the price levels needed to say "too
        expensive"; the filtered pass supplies affordable places the unfiltered
        top-20 would have crowded out with famous ones. Ten calls a run for
        five destinations, which sits inside the free monthly tier.

        Returns {} on any failure, which the caller must treat as "no opinion"
        rather than "reject everything" -- a quota error must not empty a
        page's dining section.
        """
        destination = str(destination or "").strip()
        if not (self.available and destination):
            return {}

        cache_key = destination.lower()
        if cache_key in self._by_destination:
            return self._by_destination[cache_key]

        merged = self._search(destination, price_filtered=False)
        if low_budget:
            # Affordable results win on conflict: they carry the level that
            # actually qualified them.
            merged.update(self._search(destination, price_filtered=True))

        logger.info("Places filter: %d place(s) known for '%s'", len(merged), destination)
        self._by_destination[cache_key] = merged
        return merged

    def lookup_one(self, name: str, destination: str) -> dict[str, Any] | None:
        """Ask about ONE restaurant by name.

        The destination-wide passes see forty places; a city has thousands, so
        most of our candidates fall outside them -- Horvath, Comme Chez Soi and
        Madami were all "unknown" against a Berlin/Brussels sweep. This gets a
        definitive answer for a single name.

        One call each, so the caller must spend it only where the answer
        changes something: an item with no price of its own, or one that looks
        too expensive for the brief. Blanket use would be roughly 25 calls a
        run against a 1,000/month free tier.
        """
        name = str(name or "").strip()
        destination = str(destination or "").strip()
        if not (self.available and name and destination):
            return None

        memo_key = f"one::{normalize_name(name)}::{destination.lower()}"
        if memo_key in self._by_destination:
            table = self._by_destination[memo_key]
            return next(iter(table.values()), None)

        found = self._search(f"{name}, {destination}", price_filtered=False)
        target = normalize_name(name)
        hit = found.get(target)
        if hit is None:
            # Accept a containment match: Places writes "Mustafa's Gemüse Kebap"
            # where the batch wrote "Mustafa's Gemuse Kebab".
            for key, value in found.items():
                if target and (target in key or key in target):
                    hit = value
                    break
        self._by_destination[memo_key] = {target: hit} if hit else {}
        return hit

    def verdict_precise(self, name: str, destination: str) -> str:
        """Definitive verdict for one name, at the cost of one API call."""
        hit = self.lookup_one(name, destination)
        if not hit:
            return "unknown"
        return "too_expensive" if hit.get("price_level") in _TOO_EXPENSIVE else "confirmed_affordable"

    def verdict(self, name: str, destination: str, *, low_budget: bool) -> str:
        """One of "too_expensive", "confirmed_affordable", or "unknown".

        "unknown" covers both a failed lookup and a place Places has never
        heard of, and the caller must not treat it as a rejection: a small
        neighbourhood spot missing from a twenty-result page is exactly the
        kind of place this brief wants.
        """
        table = self.lookup(destination, low_budget=low_budget)
        if not table:
            return "unknown"
        hit = table.get(normalize_name(name))
        if not hit:
            return "unknown"
        if hit.get("price_level") in _TOO_EXPENSIVE:
            return "too_expensive"
        return "confirmed_affordable"
