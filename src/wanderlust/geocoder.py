"""
Geocoder — Reverse geocode trip coordinates to place names.

Uses geopy with Nominatim (free, no API key needed).
Rate-limited to 1 req/sec per Nominatim policy.
"""

import os
import time
import json
from pathlib import Path
from typing import Optional

# Fix SSL cert verification on macOS with Homebrew Python
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass

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

    # Validate coordinates
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return {"city": None, "country": None, "country_code": None, "display_name": None}

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
    except (GeocoderTimedOut, GeocoderServiceError, ValueError):
        result = {"city": None, "country": None, "country_code": None, "display_name": None}

    if cache is not None:
        cache[key] = result

    # Respect Nominatim rate limit
    time.sleep(1.1)

    return result


def reverse_geocode_poi(lat: float, lon: float, cache: Optional[dict] = None) -> str:
    """
    Reverse geocode to a POI/neighbourhood-level name for a stop.

    Returns a short descriptive name like "Disneyland Paris area" or "Marne-la-Vallée".
    """
    key = f"poi:{lat:.4f},{lon:.4f}"

    if cache and key in cache:
        return cache[key]

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return f"{lat:.2f}, {lon:.2f}"

    geolocator = Nominatim(user_agent="wanderlust-travel-discovery/0.1")

    try:
        # Use zoom=14 for POI/area-level results (landmarks, theme parks, etc.)
        location = geolocator.reverse(f"{lat}, {lon}", language="en", timeout=10, zoom=14)
        if location and location.raw.get("address"):
            addr = location.raw["address"]
            raw_name = location.raw.get("name", "")
            # Try to get the most specific/interesting name
            name = (
                addr.get("tourism")
                or addr.get("leisure")
                or addr.get("amenity")
                or raw_name  # Nominatim name field (e.g. "Main Street U.S.A." → Disneyland)
                or addr.get("building")
                or addr.get("neighbourhood")
                or addr.get("suburb")
                or addr.get("village")
                or addr.get("town")
                or addr.get("city")
                or addr.get("municipality")
            )
            # Add city context for clarity
            city = addr.get("city") or addr.get("town") or addr.get("village") or ""
            if name and name != city:
                result = f"{name}, {city}" if city else name
            elif city:
                result = city
            else:
                result = location.address.split(",")[0]
        else:
            result = f"{lat:.2f}, {lon:.2f}"
    except (GeocoderTimedOut, GeocoderServiceError, ValueError):
        result = f"{lat:.2f}, {lon:.2f}"

    if cache is not None:
        cache[key] = result

    time.sleep(1.1)
    return result


def enrich_trips(trips: list[Trip], progress_callback=None) -> list[Trip]:
    """Add place names to discovered trips and their stops via reverse geocoding."""
    cache = _load_cache()

    for i, trip in enumerate(trips):
        if progress_callback:
            progress_callback(f"Geocoding trip {i+1}/{len(trips)}...")

        result = reverse_geocode(trip.center_lat, trip.center_lon, cache=cache)
        trip.city = result.get("city")
        trip.country = result.get("country")
        state = result.get("state")
        if trip.city and trip.country:
            if state and state != trip.city and state != trip.country:
                trip.place_name = f"{trip.city}, {state}, {trip.country}"
            else:
                trip.place_name = f"{trip.city}, {trip.country}"
        else:
            trip.place_name = trip.country or trip.city or state

        # Geocode individual stops for multi-stop trips
        if len(trip.stops) > 1:
            for stop in trip.stops:
                stop_name = reverse_geocode_poi(stop["lat"], stop["lon"], cache=cache)
                stop["name"] = stop_name
            if progress_callback:
                progress_callback(f"  → {len(trip.stops)} stops geocoded for {trip.place_name or 'trip'}")

    _save_cache(cache)
    return trips
