"""
Travel Profiler — Builds a "Travel DNA" profile from discovered trips.

Analyses patterns like:
- Preferred seasons (do you always go away in summer?)
- Typical trip duration
- Domestic vs international ratio
- Beach vs city vs nature (inferred from coordinates)
- Family vs couples vs solo
- Repeat destinations
- Adventure range (how far do you typically go?)
- Photography density (engagement proxy)
"""

from dataclasses import dataclass, field
from collections import Counter
from typing import Optional

from .clusterer import Trip, haversine_km


@dataclass
class TravelProfile:
    """Your travel DNA, derived from photo evidence."""

    # Basic stats
    total_trips: int = 0
    total_days_away: int = 0
    total_photos: int = 0
    date_range: str = ""

    # Patterns
    avg_trip_days: float = 0.0
    preferred_seasons: list[str] = field(default_factory=list)
    season_counts: dict = field(default_factory=dict)

    # Geography
    countries_visited: list[str] = field(default_factory=list)
    domestic_pct: float = 0.0
    avg_distance_km: float = 0.0
    max_distance_km: float = 0.0
    farthest_trip: Optional[str] = None

    # Social
    family_trip_pct: float = 0.0
    most_travelled_with: list[str] = field(default_factory=list)
    solo_trip_pct: float = 0.0

    # Engagement
    avg_photos_per_day: float = 0.0
    favorite_rate: float = 0.0

    # Repeat visits
    repeat_destinations: list[str] = field(default_factory=list)

    # Derived preferences (for recommendation engine)
    preferences: dict = field(default_factory=dict)


def build_profile(
    trips: list[Trip],
    home: tuple[float, float] = (51.5615, -0.0750),
    home_country: str = "United Kingdom",
) -> TravelProfile:
    """Build a travel profile from discovered trips."""

    if not trips:
        return TravelProfile()

    profile = TravelProfile()
    profile.total_trips = len(trips)
    profile.total_days_away = sum(t.duration_days for t in trips)
    profile.total_photos = sum(t.photo_count for t in trips)
    profile.date_range = f"{trips[0].start_date.strftime('%b %Y')} — {trips[-1].end_date.strftime('%b %Y')}"

    # Average trip duration
    profile.avg_trip_days = profile.total_days_away / len(trips)

    # Season analysis
    season_counts = Counter(t.season for t in trips)
    profile.season_counts = dict(season_counts)
    profile.preferred_seasons = [s for s, _ in season_counts.most_common(2)]

    # Country analysis
    countries = [t.country for t in trips if t.country]
    profile.countries_visited = sorted(set(countries))
    domestic = sum(1 for c in countries if c == home_country)
    profile.domestic_pct = (domestic / len(trips) * 100) if trips else 0

    # Distance analysis
    distances = [
        haversine_km(home[0], home[1], t.center_lat, t.center_lon)
        for t in trips
    ]
    profile.avg_distance_km = sum(distances) / len(distances) if distances else 0
    if distances:
        max_idx = distances.index(max(distances))
        profile.max_distance_km = distances[max_idx]
        profile.farthest_trip = trips[max_idx].place_name

    # Social analysis
    family_trips = sum(1 for t in trips if t.is_family_trip)
    profile.family_trip_pct = (family_trips / len(trips) * 100) if trips else 0

    all_people = []
    for t in trips:
        all_people.extend(t.people)
    people_counts = Counter(all_people)
    profile.most_travelled_with = [name for name, _ in people_counts.most_common(5)]

    solo_trips = sum(1 for t in trips if not t.people)
    profile.solo_trip_pct = (solo_trips / len(trips) * 100) if trips else 0

    # Engagement
    profile.avg_photos_per_day = profile.total_photos / max(1, profile.total_days_away)
    total_favs = sum(t.favorite_count for t in trips)
    profile.favorite_rate = (total_favs / max(1, profile.total_photos)) * 100

    # Repeat destinations
    place_counts = Counter(
        t.country for t in trips if t.country and t.country != home_country
    )
    profile.repeat_destinations = [
        place for place, count in place_counts.items() if count >= 2
    ]

    # Build preference signals for recommendation engine
    profile.preferences = _derive_preferences(profile, trips)

    return profile


def _derive_preferences(profile: TravelProfile, trips: list[Trip]) -> dict:
    """Derive travel preferences from patterns."""
    prefs = {}

    # Trip length preference
    if profile.avg_trip_days <= 4:
        prefs["trip_length"] = "short_breaks"
    elif profile.avg_trip_days <= 8:
        prefs["trip_length"] = "week_long"
    else:
        prefs["trip_length"] = "extended"

    # Distance preference
    if profile.avg_distance_km < 500:
        prefs["range"] = "regional"
    elif profile.avg_distance_km < 3000:
        prefs["range"] = "continental"
    else:
        prefs["range"] = "global"

    # Season preference
    if profile.preferred_seasons:
        prefs["preferred_season"] = profile.preferred_seasons[0]

    # Travel style
    if profile.family_trip_pct > 60:
        prefs["style"] = "family"
    elif profile.solo_trip_pct > 60:
        prefs["style"] = "solo"
    else:
        prefs["style"] = "mixed"

    # Photography engagement (proxy for how much they explore)
    if profile.avg_photos_per_day > 30:
        prefs["engagement"] = "avid_photographer"
    elif profile.avg_photos_per_day > 10:
        prefs["engagement"] = "casual_photographer"
    else:
        prefs["engagement"] = "light_documenter"

    # Adventurousness (repeat vs new)
    unique_countries = len(profile.countries_visited)
    if unique_countries > profile.total_trips * 0.7:
        prefs["adventurousness"] = "explorer"
    elif len(profile.repeat_destinations) > unique_countries * 0.5:
        prefs["adventurousness"] = "loyalist"
    else:
        prefs["adventurousness"] = "balanced"

    return prefs
