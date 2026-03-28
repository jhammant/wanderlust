"""Tests for the geocoder."""

import sys


def test_reverse_geocode_validation():
    """Test coordinate validation."""
    from wanderlust.geocoder import reverse_geocode

    # Invalid coordinates
    result = reverse_geocode(100, 200)
    assert result["city"] is None
    assert result["country"] is None

    # Invalid latitude
    result = reverse_geocode(100, 0)
    assert result["city"] is None

    # Invalid longitude
    result = reverse_geocode(0, 200)
    assert result["city"] is None


def test_reverse_geocode_out_of_range():
    """Test coordinates out of valid range."""
    from wanderlust.geocoder import reverse_geocode

    result = reverse_geocode(-91, 0)
    assert result["city"] is None

    result = reverse_geocode(0, -181)
    assert result["city"] is None


def test_reverse_geocode_cache():
    """Test geocoding cache functionality."""
    from wanderlust.geocoder import reverse_geocode

    # First call - no cache
    result1 = reverse_geocode(48.8566, 2.3522)

    # With cache
    cache = {}
    result2 = reverse_geocode(48.8566, 2.3522, cache=cache)

    assert "Paris" in result2.get("city", "") or "Paris" in result2.get(
        "display_name", ""
    )
    assert len(cache) >= 1


def test_reverse_geocode_poi():
    """Test POI-level geocoding."""
    from wanderlust.geocoder import reverse_geocode_poi

    result = reverse_geocode_poi(48.8566, 2.3522)
    # Should return some kind of place name
    assert result is not None


def test_reverse_geocode_poi_invalid():
    """Test POI geocoding with invalid coordinates."""
    from wanderlust.geocoder import reverse_geocode_poi

    result = reverse_geocode_poi(200, 200)
    assert "200.00" in result
