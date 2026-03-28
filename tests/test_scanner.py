"""Tests for the photo scanner."""
import pytest
from datetime import datetime, timedelta


def test_core_data_to_datetime():
    """Test Core Data timestamp conversion."""
    from wanderlust.scanner import core_data_to_datetime
    
    # 757689600 seconds from Core Data epoch (2001-01-01) 
    # Should give approximately 2024-01-15
    result = core_data_to_datetime(757689600)
    
    # Verify it's around 2024-2025 (approx 23 years after epoch)
    assert result.year == 2025
    assert result.month == 1


def test_core_data_to_datetime_none():
    """Test None handling in timestamp conversion."""
    from wanderlust.scanner import core_data_to_datetime
    
    result = core_data_to_datetime(None)
    assert result is None


def test_core_data_negative_timestamp():
    """Test negative timestamps (before Core Data epoch)."""
    from wanderlust.scanner import core_data_to_datetime
    
    # -31536000 = 2000-01-01 (one year before epoch)
    result = core_data_to_datetime(-31536000)
    assert result.year == 2000
    assert result.month == 1


def test_core_data_to_datetime_days_calculation():
    """Verify the Core Data epoch calculation."""
    from wanderlust.scanner import core_data_to_datetime, CORE_DATA_EPOCH
    
    # Verify epoch is correct: 2001-01-01
    assert CORE_DATA_EPOCH.year == 2001
    assert CORE_DATA_EPOCH.month == 1
    assert CORE_DATA_EPOCH.day == 1
    
    # Convert a known timestamp (20 years after epoch = ~2021)
    result = core_data_to_datetime(631152000)
    assert result.year == 2021
