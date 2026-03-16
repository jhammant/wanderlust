"""
Geocoder — Reverse geocode trip coordinates to place names.

Uses geopy with Nominatim (free, no API key needed).
Rate-limited to 1 req/sec per Nominatim policy.
"""

import time
import json
from pathlib import Path
from typing import Optional

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

from .clusterer import Trip


# Cache file to avoid repeated lookups
CACHE_DIR = Path.home() / ".wanderlust"
CACHE_FILE = CACHE_DIR / "geocache.json"


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def _save_cache(cache: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def reverse_geocode(lat: float, lon: float, cache: Optional[dict] = None) -> dict:
    """
    Reverse geocode a coordinate to a place name.

    Returns dict with: city, country, country_code, display_name
    """
    key = f"{lat:.3f},{lon:.3f}"

    if cache and key in cache:
        return cache[key]

    geolocator = Nominatim(user_agent="wanderlust-travel-discovery/0.1")

    try:
        location = geolocator.reverse(f"{lat}, {lon}", language="en", timeout=10)
        if location and location.raw.get("address"):
            addr = location.raw["address"]
            result = {
                "city": (
                    addr.get("city")
                    or addr.get("town")
                    or addr.get("village")
                    or addr.get("municipality")
                    or addr.get("county")
                ),
                "state": addr.get("state"),
                "country": addr.get("country"),
                "country_code": addr.get("country_code", "").upper(),
                "display_name": location.address,
            }
        else:
            result = {"city": None, "country": None, "country_code": None, "display_name": None}
    except (GeocoderTimedOut, GeocoderServiceError):
        result = {"city": None, "country": None, "country_code": None, "display_name": None}

    if cache is not None:
        cache[key] = result

    # Respect Nominatim rate limit
    time.sleep(1.1)

    return result


def enrich_trips(trips: list[Trip], progress_callback=None) -> list[Trip]:
    """Add place names to discovered trips via reverse geocoding."""
    cache = _load_cache()

    for i, trip in enumerate(trips):
        if progress_callback:
            progress_callback(f"Geocoding trip {i+1}/{len(trips)}...")

        result = reverse_geocode(trip.center_lat, trip.center_lon, cache=cache)
        trip.city = result.get("city")
        trip.country = result.get("country")
        trip.place_name = (
            f"{trip.city}, {trip.country}" if trip.city and trip.country
            else trip.country or trip.city
        )

    _save_cache(cache)
    return trips
