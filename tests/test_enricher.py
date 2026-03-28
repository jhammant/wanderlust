"""Tests for the enricher."""

import sys
from datetime import datetime


def test_build_trip_timeline():
    """Test building trip timeline from photos."""
    from wanderlust.enricher import build_trip_timeline
    from wanderlust.clusterer import Trip
    from wanderlust.scanner import PhotoRecord
    from datetime import timedelta

    photos = []
    for day in range(3):
        for hour in range(5):
            photos.append(
                PhotoRecord(
                    uuid=f"photo-{day}-{hour}",
                    timestamp=datetime(2024, 7, 15) + timedelta(days=day, hours=hour),
                    latitude=48.8566,
                    longitude=2.3522,
                    faces=["Anne"] if hour % 2 == 0 else [],
                    is_favorite=(hour == 12),
                )
            )

    trip = Trip(
        id=0,
        start_date=datetime(2024, 7, 15),
        end_date=datetime(2024, 7, 17),
        photos=photos,
        center_lat=48.8566,
        center_lon=2.3522,
    )

    timeline = build_trip_timeline(trip)

    assert len(timeline) == 3
    assert timeline[0]["date"] == "2024-07-15"
    assert timeline[0]["photo_count"] == 5
    # Check that at least some days have people listed
    has_people = any(t.get("people") for t in timeline)
    assert has_people


def test_build_enrichment_prompt():
    """Test enrichment prompt building."""
    from wanderlust.enricher import build_enrichment_prompt
    from wanderlust.clusterer import Trip

    trip = Trip(
        id=0,
        start_date=datetime(2024, 7, 15),
        end_date=datetime(2024, 7, 20),
        photos=[],
        locations=[],
        center_lat=48.8566,
        center_lon=2.3522,
    )

    timeline = [
        {
            "date": "2024-07-15",
            "day_name": "Monday",
            "photo_count": 10,
            "morning_photos": 5,
            "afternoon_photos": 3,
            "evening_photos": 2,
            "locations": [(48.8566, 2.3522)],
            "people": ["Anne", "Clara"],
            "favorites": 0,
        }
    ]

    location_names = {"48.8566,2.3522": "Paris"}

    prompt = build_enrichment_prompt(trip, timeline, location_names)

    assert "Monday" in prompt
    assert "2024-07-15" in prompt


def test_build_enrichment_prompt_empty_timeline():
    """Test enrichment prompt with no timeline."""
    from wanderlust.enricher import build_enrichment_prompt
    from wanderlust.clusterer import Trip

    trip = Trip(
        id=0,
        start_date=datetime(2024, 7, 15),
        end_date=datetime(2024, 7, 20),
        photos=[],
        locations=[],
        center_lat=48.8566,
        center_lon=2.3522,
    )

    prompt = build_enrichment_prompt(trip, [], {})

    assert "photo" in prompt.lower()


def test_enrich_trip_no_photos():
    """Test enrichment with no photo data."""
    from wanderlust.enricher import enrich_trip
    from wanderlust.clusterer import Trip

    trip = Trip(
        id=0,
        start_date=datetime(2024, 7, 15),
        end_date=datetime(2024, 7, 20),
        photos=[],
        locations=[],
        center_lat=48.8566,
        center_lon=2.3522,
    )

    result = enrich_trip(trip)
    assert "No photo data" in result
