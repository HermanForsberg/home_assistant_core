"""Tests for the Mealie coordinators."""

from datetime import timedelta

from homeassistant.components.mealie.coordinator import PROMOTIONS_UPDATE_INTERVAL


def test_promotions_update_interval() -> None:
    """Test promotions are refreshed once a day."""
    assert timedelta(days=1) == PROMOTIONS_UPDATE_INTERVAL
