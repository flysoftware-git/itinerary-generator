"""
geocoder.py — Geocode destination names to lat/lng using Nominatim.
"""
from __future__ import annotations
import logging
import time
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderRateLimited, GeocoderServiceError, GeocoderTimedOut

logger = logging.getLogger(__name__)

# Disambiguation hints: names that Nominatim resolves to the wrong place
# Maps destination name (lowercase) → "country/region" string to append
GEOCODE_COUNTRY_HINTS: dict[str, str] = {
    "santa fe": "New Mexico, USA",
}


class Geocoder:
    _cache: dict[str, tuple[float, float]] = {}

    def __init__(self, user_agent: str = "RoadTripItineraryGenerator/1.0", timeout: int = 5) -> None:
        self.geolocator = Nominatim(user_agent=user_agent, timeout=timeout)

    def _geocode(self, name: str, retries: int = 3) -> tuple[float, float]:
        hint = GEOCODE_COUNTRY_HINTS.get(name.lower())
        query = f"{name}, {hint}" if hint else name
        if hint:
            logger.debug("Geocoder disambiguation: '%s' → '%s'", name, query)

        if query in Geocoder._cache:
            return Geocoder._cache[query]

        for attempt in range(retries + 1):
            try:
                location = self.geolocator.geocode(query)
                if location:
                    Geocoder._cache[query] = (location.latitude, location.longitude)
                    return Geocoder._cache[query]
                raise ValueError(f"Nominatim returned no results for: '{query}'")
            except GeocoderRateLimited:
                if attempt == retries:
                    raise
                backoff = 15 * (attempt + 1)
                logger.warning("Geocoder rate-limited (429) for '%s', backing off %ds (attempt %d)", name, backoff, attempt + 1)
                time.sleep(backoff)
            except (GeocoderTimedOut, GeocoderServiceError) as exc:
                if attempt == retries:
                    raise
                logger.warning("Geocoder retry %d for '%s': %s", attempt + 1, name, exc)
                time.sleep(2)
        raise ValueError(f"Geocoding failed for: '{name}'")
