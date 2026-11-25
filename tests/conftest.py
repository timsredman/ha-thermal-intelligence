"""Test fixtures."""

from __future__ import annotations

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.thermal_intelligence.const import CONF_ZONES, DOMAIN


@pytest.fixture
def zone_config():
    return {
        "name": "Hall and landing",
        "primary_sensor": "sensor.hall_temperature",
        "reference_sensors": ["sensor.landing_temperature"],
        "calibration_offsets": {"sensor.landing_temperature": 0.2},
        "placement": {"sensor.landing_temperature": {"height": "low", "wall": "external"}},
    }


@pytest.fixture
def config_entry(zone_config):
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ZONES: [zone_config], "sources": [], "demand_entities": [], "burner_entities": []},
        options={},
    )
