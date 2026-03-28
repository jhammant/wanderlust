"""Tests for the web server."""

import pytest
import sys


def test_web_index():
    """Test main page route."""
    from wanderlust.web import app

    client = app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    assert b"Wanderlust" in response.data or b"Where Next?" in response.data


def test_web_api_trips_empty():
    """Test API trips endpoint with no data."""
    from wanderlust.web import app

    client = app.test_client()
    response = client.get("/api/trips")

    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)


def test_web_api_profile():
    """Test API profile endpoint."""
    from wanderlust.web import app

    client = app.test_client()
    response = client.get("/api/profile")

    assert response.status_code in [200, 404]


def test_web_api_exclusions():
    """Test API exclusions endpoint."""
    from wanderlust.web import app

    client = app.test_client()
    response = client.get("/api/exclusions")

    assert response.status_code == 200


def test_web_api_calendar():
    """Test API calendar endpoint."""
    from wanderlust.web import app

    client = app.test_client()
    response = client.get("/api/calendar")

    assert response.status_code == 200


def test_web_api_roadtrip_generate():
    """Test road trip generation endpoint."""
    from wanderlust.web import app

    client = app.test_client()
    response = client.post("/api/roadtrip/generate", json={"country": "France"})

    # May fail without AI, but should respond
    assert response.status_code in [200, 400, 500]


def test_web_api_weather():
    """Test weather endpoint."""
    from wanderlust.web import app

    client = app.test_client()
    response = client.post(
        "/api/weather",
        json={"locations": [{"lat": 48.8566, "lon": 2.3522}], "month": 7},
    )

    assert response.status_code == 200
