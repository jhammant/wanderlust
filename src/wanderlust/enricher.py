"""
Trip Enricher — Uses an LLM to generate trip narratives from photo metadata.

Given a trip's photo timestamps, GPS coordinates, faces, and favorites,
asks an LLM to infer what activities likely happened and build a story.
"""

import os

# Fix SSL cert verification on macOS with Homebrew Python
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass

from collections import defaultdict
from datetime import datetime
from typing import Optional

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

from .clusterer import Trip
from .scanner import PhotoRecord


def _geocode_point(lat: float, lon: float, geolocator, cache: dict) -> str:
    """Reverse geocode a single point to a short place description."""
    key = f"{lat:.4f},{lon:.4f}"
    if key in cache:
        return cache[key]

    try:
        location = geolocator.reverse(f"{lat}, {lon}", language="en", timeout=10)
        if location and location.raw.get("address"):
            addr = location.raw["address"]
            # Try to get a specific venue or neighbourhood
            name = (
                addr.get("tourism")
                or addr.get("amenity")
                or addr.get("leisure")
                or addr.get("building")
                or addr.get("neighbourhood")
                or addr.get("suburb")
                or addr.get("village")
                or addr.get("town")
                or addr.get("city")
                or "unknown area"
            )
            result = name
        else:
            result = f"{lat:.3f}, {lon:.3f}"
    except (GeocoderTimedOut, GeocoderServiceError, ValueError):
        result = f"{lat:.3f}, {lon:.3f}"

    cache[key] = result
    return result


def build_trip_timeline(trip: Trip) -> list[dict]:
    """
    Build a day-by-day timeline of photo activity for a trip.

    Returns a list of day summaries with locations, people, and photo counts.
    """
    if not trip.photos:
        return []

    by_day = defaultdict(list)
    for photo in trip.photos:
        day_key = photo.timestamp.strftime("%Y-%m-%d")
        by_day[day_key].append(photo)

    timeline = []
    for day_str in sorted(by_day.keys()):
        photos = by_day[day_str]
        day_date = datetime.strptime(day_str, "%Y-%m-%d")

        # Group photos by time of day
        morning = [p for p in photos if p.timestamp.hour < 12]
        afternoon = [p for p in photos if 12 <= p.timestamp.hour < 17]
        evening = [p for p in photos if p.timestamp.hour >= 17]

        # Collect unique locations (deduplicated by ~100m grid)
        seen_locs = set()
        locations = []
        for p in photos:
            grid_key = (round(p.latitude, 3), round(p.longitude, 3))
            if grid_key not in seen_locs:
                seen_locs.add(grid_key)
                locations.append((p.latitude, p.longitude))

        # Collect faces
        all_faces = []
        for p in photos:
            all_faces.extend(p.faces)
        from collections import Counter
        face_counts = Counter(all_faces)
        people = [name for name, _ in face_counts.most_common(5)]

        # Count favorites
        favs = sum(1 for p in photos if p.is_favorite)

        timeline.append({
            "date": day_str,
            "day_name": day_date.strftime("%A"),
            "photo_count": len(photos),
            "morning_photos": len(morning),
            "afternoon_photos": len(afternoon),
            "evening_photos": len(evening),
            "locations": locations[:10],  # Cap at 10 distinct locations
            "people": people,
            "favorites": favs,
        })

    return timeline


def build_enrichment_prompt(trip: Trip, timeline: list[dict], location_names: dict) -> str:
    """
    Build a prompt for the LLM to generate a trip narrative.
    """
    # Day-by-day summary
    day_summaries = []
    for day in timeline:
        locs = []
        for lat, lon in day["locations"][:5]:
            key = f"{lat:.4f},{lon:.4f}"
            name = location_names.get(key, f"{lat:.3f},{lon:.3f}")
            locs.append(name)

        time_parts = []
        if day["morning_photos"]:
            time_parts.append(f"{day['morning_photos']} morning")
        if day["afternoon_photos"]:
            time_parts.append(f"{day['afternoon_photos']} afternoon")
        if day["evening_photos"]:
            time_parts.append(f"{day['evening_photos']} evening")

        people_str = f" | People: {', '.join(day['people'][:4])}" if day["people"] else ""
        favs_str = f" | {day['favorites']} favourited" if day["favorites"] else ""
        loc_str = ", ".join(locs[:5]) if locs else "unknown"

        day_summaries.append(
            f"  {day['date']} ({day['day_name']}): "
            f"{day['photo_count']} photos ({', '.join(time_parts)}) "
            f"| Locations: {loc_str}{people_str}{favs_str}"
        )

    days_text = "\n".join(day_summaries)

    prompt = f"""Based on this photo metadata from a trip, write a short, vivid trip narrative.
Infer what activities likely happened from the locations, times, and patterns.

**Trip: {trip.place_name or 'Unknown'}**
- Dates: {trip.start_date.strftime('%d %b %Y')} to {trip.end_date.strftime('%d %b %Y')} ({trip.duration_days} days)
- Total photos: {trip.photo_count} ({trip.favorite_count} favourited)
- People: {', '.join(trip.people) if trip.people else 'Unknown'}
- Family trip: {'Yes' if trip.is_family_trip else 'No'}

**Day-by-day photo activity:**
{days_text}

Write a 3-5 sentence narrative of what this trip was probably like. Be specific about
likely activities based on the locations and times. Mention the people if known.
Don't hedge with "might have" — write it as a confident, warm recollection.
Then list 3-5 bullet points of likely highlights/activities.

Format:
**Summary:** [narrative]

**Likely highlights:**
- [highlight 1]
- [highlight 2]
...
"""
    return prompt


def enrich_trip(
    trip: Trip,
    provider: str = "ollama",
    model: Optional[str] = None,
    progress_callback=None,
) -> str:
    """
    Enrich a single trip with an LLM-generated narrative.

    Returns the narrative text.
    """
    if progress_callback:
        progress_callback(f"Building timeline for {trip.place_name or 'trip'}...")

    timeline = build_trip_timeline(trip)
    if not timeline:
        return "No photo data available for this trip."

    # Geocode key locations (sample up to 20 unique points across the trip)
    if progress_callback:
        progress_callback("Geocoding photo locations...")

    geolocator = Nominatim(user_agent="wanderlust-enricher/0.1")
    location_cache = {}
    all_locations = []
    for day in timeline:
        all_locations.extend(day["locations"])

    # Deduplicate and sample
    seen = set()
    unique_locs = []
    for lat, lon in all_locations:
        key = (round(lat, 3), round(lon, 3))
        if key not in seen:
            seen.add(key)
            unique_locs.append((lat, lon))

    # Geocode up to 20 points (with rate limiting)
    import time
    for lat, lon in unique_locs[:20]:
        _geocode_point(lat, lon, geolocator, location_cache)
        time.sleep(1.1)

    if progress_callback:
        progress_callback("Generating narrative...")

    prompt = build_enrichment_prompt(trip, timeline, location_cache)

    # Call LLM
    if provider == "openrouter":
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            return "Set OPENROUTER_API_KEY to use OpenRouter enrichment."
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
        resp = client.chat.completions.create(
            model=model or "anthropic/claude-sonnet-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=800,
        )
        return resp.choices[0].message.content
    elif provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return "Set OPENAI_API_KEY to use OpenAI enrichment."
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model or "gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=800,
        )
        return resp.choices[0].message.content
    elif provider == "ollama":
        import requests
        try:
            resp = requests.post(
                "http://localhost:11434/api/generate",
                json={"model": model or "qwen2.5:14b", "prompt": prompt, "stream": False},
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json()["response"]
        except Exception as e:
            return f"Ollama error: {e}. Is Ollama running?"
    else:
        return prompt  # Return the prompt for manual use
