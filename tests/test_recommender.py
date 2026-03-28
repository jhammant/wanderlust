"""Tests for the recommender."""

import sys


def test_build_recommendation_prompt():
    """Test prompt building with travel profile."""
    from wanderlust.recommender import build_recommendation_prompt
    from wanderlust.profiler import TravelProfile
    from wanderlust.clusterer import Trip
    from datetime import datetime

    profile = TravelProfile(
        total_trips=12,
        date_range="Jan 2023 — Feb 2026",
        avg_trip_days=7,
        preferred_seasons=["summer", "spring"],
        countries_visited=["France", "Italy", "Spain"],
        domestic_pct=25,
        avg_distance_km=800,
        max_distance_km=5500,
        farthest_trip="New York",
        preferences={
            "style": "family",
            "range": "continental",
            "adventurousness": "balanced",
        },
        family_trip_pct=83,
        most_travelled_with=["Anne", "Clara"],
    )

    trips = [
        Trip(
            id=0,
            start_date=datetime(2024, 7, 1),
            end_date=datetime(2024, 7, 5),
        )
        for _ in range(3)
    ]

    prompt = build_recommendation_prompt(profile, trips)

    # Check key elements are in prompt
    assert "12 trips" in prompt
    assert "France" in prompt


def test_build_recommendation_prompt_with_constraints():
    """Test prompt building with constraints."""
    from wanderlust.recommender import build_recommendation_prompt
    from wanderlust.profiler import TravelProfile

    profile = TravelProfile(total_trips=5, date_range="2023-2024")
    trips = []

    constraints = {
        "budget": "medium",
        "kids_ages": [10, 7, 4],
        "time_of_year": "February half term",
        "duration_days": 10,
    }

    prompt = build_recommendation_prompt(profile, trips, constraints)

    assert "budget" in prompt.lower()
    assert "february half term" in prompt.lower()


def test_recommend_manual():
    """Test manual recommendation output."""
    from wanderlust.recommender import recommend_manual
    from wanderlust.profiler import TravelProfile

    profile = TravelProfile(
        total_trips=12,
        date_range="Jan 2023 — Feb 2026",
        countries_visited=["France", "Italy"],
        avg_distance_km=800,
        family_trip_pct=83,
    )

    output = recommend_manual(profile, [])

    assert "TRAVEL DNA" in output
    assert "France" in output or "Italy" in output


def test_recommend_manual_no_trips():
    """Test manual recommendation with no trips."""
    from wanderlust.recommender import recommend_manual
    from wanderlust.profiler import TravelProfile

    profile = TravelProfile(total_trips=0, date_range="N/A")

    output = recommend_manual(profile, [])

    assert "TRAVEL DNA" in output
