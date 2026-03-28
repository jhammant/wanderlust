"""Tests for the travel profiler."""

from datetime import datetime


def test_build_profile_empty_trips():
    """Test profile building with no trips."""
    from wanderlust.profiler import build_profile

    profile = build_profile([])
    assert profile.total_trips == 0
    assert profile.total_days_away == 0
    assert profile.countries_visited == []


def test_build_profile_basic_trip():
    """Test profile building with a single trip."""
    from wanderlust.clusterer import Trip
    from wanderlust.profiler import build_profile

    trips = [
        Trip(
            id=0,
            start_date=datetime(2024, 7, 1),
            end_date=datetime(2024, 7, 10),
            center_lat=48.8566,
            center_lon=2.3522,
            country="France",
            city="Paris",
        )
    ]

    profile = build_profile(trips)
    assert profile.total_trips == 1
    assert profile.total_days_away == 10
    assert "France" in profile.countries_visited
    assert profile.avg_trip_days == 10.0


def test_build_profile_season_analysis():
    """Test season preference detection."""
    from wanderlust.clusterer import Trip
    from wanderlust.profiler import build_profile

    trips = [
        # Winter trip
        Trip(
            id=0,
            start_date=datetime(2024, 1, 15),
            end_date=datetime(2024, 1, 20),
            center_lat=48.8566,
            center_lon=2.3522,
            country="France",
        ),
        # Winter trip
        Trip(
            id=1,
            start_date=datetime(2024, 12, 20),
            end_date=datetime(2025, 1, 5),
            center_lat=48.8566,
            center_lon=2.3522,
            country="France",
        ),
    ]

    profile = build_profile(trips)
    assert "winter" in profile.preferred_seasons
    assert profile.season_counts.get("winter") == 2


def test_build_profile_distance_analysis():
    """Test distance calculations."""
    from wanderlust.clusterer import Trip
    from wanderlust.profiler import build_profile

    home = (51.5615, -0.0750)
    trips = [
        # Trip to Paris (~430km from London)
        Trip(
            id=0,
            start_date=datetime(2024, 7, 1),
            end_date=datetime(2024, 7, 5),
            center_lat=48.8566,
            center_lon=2.3522,
            country="France",
        ),
        # Trip to New York (~5500km from London)
        Trip(
            id=1,
            start_date=datetime(2024, 8, 1),
            end_date=datetime(2024, 8, 7),
            center_lat=40.7128,
            center_lon=-74.0060,
            country="United States",
        ),
    ]

    profile = build_profile(trips, home=home)
    assert profile.avg_distance_km > 0
    assert profile.max_distance_km > profile.avg_distance_km


def test_build_profile_family_detection():
    """Test family trip percentage."""
    from wanderlust.clusterer import Trip
    from wanderlust.profiler import build_profile

    trips = [
        # Family trip
        Trip(
            id=0,
            start_date=datetime(2024, 7, 1),
            end_date=datetime(2024, 7, 5),
            center_lat=48.8566,
            center_lon=2.3522,
            country="France",
            people=["Anne", "Clara"],
            is_family_trip=True,
        ),
        # Solo trip
        Trip(
            id=1,
            start_date=datetime(2024, 8, 1),
            end_date=datetime(2024, 8, 3),
            center_lat=41.9028,
            center_lon=12.4964,
            country="Italy",
            people=[],
        ),
    ]

    profile = build_profile(trips, home=(51.5615, -0.0750))
    assert profile.family_trip_pct == 50.0
    assert "Anne" in profile.most_travelled_with


def test_build_profile_repeat_destinations():
    """Test repeat destination detection."""
    from wanderlust.clusterer import Trip
    from wanderlust.profiler import build_profile

    trips = [
        # Two trips to France
        Trip(
            id=0,
            start_date=datetime(2023, 7, 1),
            end_date=datetime(2023, 7, 5),
            center_lat=48.8566,
            center_lon=2.3522,
            country="France",
        ),
        Trip(
            id=1,
            start_date=datetime(2024, 7, 1),
            end_date=datetime(2024, 7, 5),
            center_lat=48.8566,
            center_lon=2.3522,
            country="France",
        ),
        # One trip to Italy
        Trip(
            id=2,
            start_date=datetime(2024, 8, 1),
            end_date=datetime(2024, 8, 5),
            center_lat=41.9028,
            center_lon=12.4964,
            country="Italy",
        ),
    ]

    profile = build_profile(trips, home=(51.5615, -0.0750))
    assert "France" in profile.repeat_destinations


def test_travel_profile_dataclass():
    """Test TravelProfile dataclass."""
    from wanderlust.profiler import TravelProfile

    profile = TravelProfile(
        total_trips=5, total_days_away=40, countries_visited=["France", "Italy"]
    )

    assert profile.total_trips == 5
    assert profile.countries_visited == ["France", "Italy"]
