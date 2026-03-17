"""
Trip Clusterer — Groups photos into trips based on location and time.

Algorithm:
1. Filter photos to those >HOME_RADIUS km from home
2. Sort by timestamp
3. Cluster using DBSCAN on (lat, lon) with time-gap splitting
4. Merge clusters that overlap in time (same trip, different spots)
5. Enrich with face data, duration, location names

A "trip" is defined as:
- Photos taken >30km from home
- Spanning at least 2 calendar days (overnight = trip, not day out)
- Optionally: family members present in photos
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from collections import Counter

from sklearn.cluster import DBSCAN
import numpy as np

from .scanner import PhotoRecord


# Default home location (Stoke Newington, London)
DEFAULT_HOME = (51.5615, -0.0750)

# Minimum distance from home to count as "away" (km)
HOME_RADIUS_KM = 30

# Maximum time gap between photos in the same trip cluster (hours)
MAX_GAP_HOURS = 48

# Minimum trip duration (days) — 1 includes day trips
MIN_TRIP_DAYS = 1

# DBSCAN: max distance between photos in same location cluster (km)
LOCATION_EPS_KM = 15


@dataclass
class Trip:
    """A discovered trip from the photo library."""
    id: int
    start_date: datetime
    end_date: datetime
    photos: list[PhotoRecord] = field(default_factory=list)
    locations: list[tuple[float, float]] = field(default_factory=list)
    center_lat: float = 0.0
    center_lon: float = 0.0
    country: Optional[str] = None
    city: Optional[str] = None
    place_name: Optional[str] = None
    people: list[str] = field(default_factory=list)
    people_counts: dict[str, int] = field(default_factory=dict)
    is_family_trip: bool = False
    photo_count: int = 0
    favorite_count: int = 0
    trip_type: str = "stay"  # "stay", "road trip", or "day trip"
    spread_km: float = 0.0  # max distance between photos in the trip
    stops: list[dict] = field(default_factory=list)  # [{lat, lon, photo_count, label}]

    @property
    def duration_days(self) -> int:
        return max(1, (self.end_date - self.start_date).days + 1)

    @property
    def season(self) -> str:
        month = self.start_date.month
        if month in (12, 1, 2):
            return "winter"
        elif month in (3, 4, 5):
            return "spring"
        elif month in (6, 7, 8):
            return "summer"
        else:
            return "autumn"

    def summary(self) -> str:
        name = self.place_name or self.city or f"({self.center_lat:.2f}, {self.center_lon:.2f})"
        people_str = f" with {', '.join(self.people[:3])}" if self.people else ""
        return (
            f"{name} — {self.start_date.strftime('%b %Y')} "
            f"({self.duration_days} days, {self.photo_count} photos{people_str})"
        )


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two GPS points in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def cluster_trips(
    photos: list[PhotoRecord],
    home: tuple[float, float] = DEFAULT_HOME,
    home_radius_km: float = HOME_RADIUS_KM,
    min_trip_days: int = MIN_TRIP_DAYS,
    family_names: Optional[list[str]] = None,
    birth_years: Optional[dict[str, int]] = None,
    progress_callback=None,
) -> list[Trip]:
    """
    Cluster photos into trips.

    Args:
        photos: List of PhotoRecords from scanner
        home: (lat, lon) of home location
        home_radius_km: Distance from home to count as "away"
        min_trip_days: Minimum days for a trip (vs day out)
        family_names: Names of family members for family trip detection
        progress_callback: Optional status callback

    Returns:
        List of Trip objects, sorted by date
    """
    if not photos:
        return []

    # Step 1: Filter to photos away from home
    away_photos = [
        p for p in photos
        if haversine_km(home[0], home[1], p.latitude, p.longitude) > home_radius_km
    ]

    if progress_callback:
        progress_callback(f"{len(away_photos)} photos taken >{home_radius_km}km from home")

    if not away_photos:
        return []

    # Step 2: Sort by time
    away_photos.sort(key=lambda p: p.timestamp)

    # Step 3: Split into time-contiguous groups (max 48h gap)
    groups = []
    current_group = [away_photos[0]]

    for photo in away_photos[1:]:
        gap = (photo.timestamp - current_group[-1].timestamp).total_seconds() / 3600
        if gap > MAX_GAP_HOURS:
            groups.append(current_group)
            current_group = [photo]
        else:
            current_group.append(photo)
    groups.append(current_group)

    if progress_callback:
        progress_callback(f"Found {len(groups)} time-contiguous photo groups")

    # Step 4: For each time group, cluster by location using DBSCAN
    trips = []
    trip_id = 0

    for group in groups:
        if len(group) < 2:
            continue

        # Check duration
        duration = (group[-1].timestamp - group[0].timestamp).days + 1
        if duration < min_trip_days:
            continue

        # Use DBSCAN to find location clusters within this time group
        coords = np.array([[p.latitude, p.longitude] for p in group])
        # Convert eps from km to radians (haversine metric operates on radians)
        eps_rad = LOCATION_EPS_KM / 6371.0

        clustering = DBSCAN(eps=eps_rad, min_samples=2, metric='haversine').fit(
            np.radians(coords)
        )

        # Group photos by cluster label
        label_photos = {}
        for photo, label in zip(group, clustering.labels_):
            # Include noise points (-1) in the nearest cluster or their own
            effective_label = label if label >= 0 else -1
            label_photos.setdefault(effective_label, []).append(photo)

        # Merge all clusters in this time group into one trip
        # (they're part of the same journey)
        all_trip_photos = group
        all_faces = []
        for p in all_trip_photos:
            for face in p.faces:
                if birth_years and face in birth_years:
                    if p.timestamp.year < birth_years[face]:
                        continue
                all_faces.append(face)

        face_counts = Counter(all_faces)
        people = [name for name, count in face_counts.most_common(15) if count >= 1]

        # Determine if family trip
        is_family = False
        if family_names:
            family_present = [n for n in people if n in family_names]
            is_family = len(family_present) >= 1

        # Use the densest location cluster as center (not the mean of all photos)
        # This prevents road trip photos from pulling the pin away from the main destination
        best_label = None
        best_count = 0
        for label, photos_in_cluster in label_photos.items():
            if label == -1:
                continue  # skip noise
            if len(photos_in_cluster) > best_count:
                best_count = len(photos_in_cluster)
                best_label = label

        if best_label is not None and best_count > 0:
            primary_photos = label_photos[best_label]
            center_lat = np.mean([p.latitude for p in primary_photos])
            center_lon = np.mean([p.longitude for p in primary_photos])
        else:
            # Fallback to median (more robust than mean against outliers)
            center_lat = np.median([p.latitude for p in all_trip_photos])
            center_lon = np.median([p.longitude for p in all_trip_photos])

        # Calculate geographic spread (max distance between any two sampled photos)
        spread_km = 0.0
        sample = all_trip_photos[::max(1, len(all_trip_photos) // 20)]  # sample up to 20 points
        for i, p1 in enumerate(sample):
            for p2 in sample[i+1:]:
                d = haversine_km(p1.latitude, p1.longitude, p2.latitude, p2.longitude)
                if d > spread_km:
                    spread_km = d

        # Classify trip type
        if duration < 2:
            trip_type = "day trip"
        elif spread_km > 80:
            trip_type = "road trip"
        else:
            trip_type = "stay"

        # Build stops list from DBSCAN clusters (sorted by time)
        # Filter out stops near home (airport/transit photos) and tiny clusters
        stops = []
        for label, cluster_photos in sorted(label_photos.items(), key=lambda x: x[1][0].timestamp):
            if label == -1:
                continue  # skip noise
            clat = float(np.mean([p.latitude for p in cluster_photos]))
            clon = float(np.mean([p.longitude for p in cluster_photos]))
            dist_from_home = haversine_km(home[0], home[1], clat, clon)
            if dist_from_home < home_radius_km * 2:
                continue  # skip stops near home (airport/transit)
            if len(cluster_photos) < 3:
                continue  # skip tiny clusters (probably transit)
            stops.append({
                "lat": clat,
                "lon": clon,
                "photo_count": len(cluster_photos),
            })

        trip = Trip(
            id=trip_id,
            start_date=all_trip_photos[0].timestamp,
            end_date=all_trip_photos[-1].timestamp,
            photos=all_trip_photos,
            locations=[(p.latitude, p.longitude) for p in all_trip_photos],
            center_lat=float(center_lat),
            center_lon=float(center_lon),
            people=people,
            people_counts={name: count for name, count in face_counts.most_common(10) if count >= 2},
            is_family_trip=is_family,
            photo_count=len(all_trip_photos),
            favorite_count=sum(1 for p in all_trip_photos if p.is_favorite),
            trip_type=trip_type,
            spread_km=round(spread_km, 1),
            stops=stops,
        )
        trips.append(trip)
        trip_id += 1

    if progress_callback:
        progress_callback(f"Discovered {len(trips)} trips")

    return sorted(trips, key=lambda t: t.start_date)
