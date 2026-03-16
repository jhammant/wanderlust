"""Tests for the trip clusterer."""

import pytest
from datetime import datetime, timedelta
from wanderlust.scanner import PhotoRecord
from wanderlust.clusterer import cluster_trips, haversine_km, Trip


def test_haversine_london_paris():
    """London to Paris should be ~340km."""
    d = haversine_km(51.5074, -0.1278, 48.8566, 2.3522)
    assert 330 < d < 350


def test_haversine_same_point():
    d = haversine_km(51.5, -0.1, 51.5, -0.1)
    assert d < 0.01


def test_cluster_basic_trip():
    """Photos in Paris over 3 days should form one trip."""
    home = (51.5615, -0.0750)  # Stoke Newington
    base_time = datetime(2024, 7, 15, 10, 0)

    photos = []
    for day in range(3):
        for hour in range(5):
            photos.append(PhotoRecord(
                uuid=f"paris-{day}-{hour}",
                timestamp=base_time + timedelta(days=day, hours=hour),
                latitude=48.8566 + (day * 0.01),  # slight movement
                longitude=2.3522 + (hour * 0.005),
            ))

    trips = cluster_trips(photos, home=home, min_trip_days=2)
    assert len(trips) == 1
    assert trips[0].photo_count == 15
    assert trips[0].duration_days == 3


def test_cluster_excludes_day_trips():
    """A single day out should not count as a trip."""
    home = (51.5615, -0.0750)
    photos = [
        PhotoRecord(uuid="brighton-1", timestamp=datetime(2024, 8, 1, 10, 0),
                    latitude=50.8225, longitude=-0.1372),
        PhotoRecord(uuid="brighton-2", timestamp=datetime(2024, 8, 1, 14, 0),
                    latitude=50.8225, longitude=-0.1372),
    ]
    trips = cluster_trips(photos, home=home, min_trip_days=2)
    assert len(trips) == 0


def test_cluster_excludes_near_home():
    """Photos near home shouldn't form trips."""
    home = (51.5615, -0.0750)
    photos = [
        PhotoRecord(uuid=f"local-{i}", timestamp=datetime(2024, 6, 1 + i, 10, 0),
                    latitude=51.56, longitude=-0.08)
        for i in range(5)
    ]
    trips = cluster_trips(photos, home=home)
    assert len(trips) == 0


def test_cluster_two_separate_trips():
    """Two trips months apart should be separate."""
    home = (51.5615, -0.0750)

    photos = []
    # Trip 1: Spain, July
    for day in range(4):
        photos.append(PhotoRecord(
            uuid=f"spain-{day}",
            timestamp=datetime(2024, 7, 10 + day, 12, 0),
            latitude=41.3851, longitude=2.1734,
        ))
    # Trip 2: Italy, October
    for day in range(5):
        photos.append(PhotoRecord(
            uuid=f"italy-{day}",
            timestamp=datetime(2024, 10, 5 + day, 12, 0),
            latitude=41.9028, longitude=12.4964,
        ))

    trips = cluster_trips(photos, home=home, min_trip_days=2)
    assert len(trips) == 2


def test_cluster_family_detection():
    """Trips with family faces should be flagged."""
    home = (51.5615, -0.0750)
    photos = [
        PhotoRecord(uuid=f"fam-{i}", timestamp=datetime(2024, 8, 1 + i, 10, 0),
                    latitude=43.7102, longitude=7.2620,
                    faces=["Anne", "Kid1"])
        for i in range(3)
    ]
    trips = cluster_trips(photos, home=home, min_trip_days=2, family_names=["Anne"])
    assert len(trips) == 1
    assert trips[0].is_family_trip is True
    assert "Anne" in trips[0].people


def test_trip_season():
    t = Trip(id=0, start_date=datetime(2024, 1, 15), end_date=datetime(2024, 1, 20))
    assert t.season == "winter"
    t2 = Trip(id=1, start_date=datetime(2024, 7, 1), end_date=datetime(2024, 7, 10))
    assert t2.season == "summer"


def test_trip_duration():
    t = Trip(id=0, start_date=datetime(2024, 7, 10), end_date=datetime(2024, 7, 15))
    assert t.duration_days == 6
