"""Coordinator behavior tests."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from homeassistant.util import dt as dt_util

from custom_components.thermal_intelligence.coordinator import (
    ThermalCoordinator,
    _entity_is_active,
    _number,
    _slug,
)


def test_number_rejects_unavailable_and_non_finite():
    assert _number(None) is None
    assert _number(SimpleNamespace(state="unavailable")) is None
    assert _number(SimpleNamespace(state="nan")) is None
    assert _number(SimpleNamespace(state="19.8")) == 19.8


def test_zone_slug_is_stable():
    assert _slug("Hall, stairs & landing") == "hall_stairs_landing"


async def test_climate_hvac_action_is_demand_signal(hass):
    hass.states.async_set("climate.nest", "heat", {"hvac_action": "heating"})
    assert _entity_is_active(hass, "climate.nest", demand=True)
    assert not _entity_is_active(hass, "climate.nest")


async def test_climate_heat_mode_alone_is_not_demand(hass):
    hass.states.async_set("climate.nest", "heat", {"hvac_mode": "heat"})
    assert not _entity_is_active(hass, "climate.nest", demand=True)


async def test_rates_ignore_short_intervals_and_use_median(hass, config_entry, zone_config):
    coordinator = ThermalCoordinator(hass, config_entry)
    zone_id = "hall_and_landing"
    coordinator.zones = {zone_id: zone_config}
    coordinator.data = {zone_id: coordinator._empty_zone_data()}
    now = dt_util.utcnow()
    coordinator.data[zone_id]["history"].extend(
        [
            {"time": (now - timedelta(minutes=20)).isoformat(), "temperature": 19.0},
            {"time": (now - timedelta(minutes=10)).isoformat(), "temperature": 20.0},
            {"time": (now - timedelta(minutes=9)).isoformat(), "temperature": 40.0},
            {"time": now.isoformat(), "temperature": 21.0},
        ]
    )
    coordinator.data[zone_id]["temperature"] = 21.0
    coordinator._derive(zone_id)
    assert coordinator.data[zone_id]["rate"] == 6.0


async def test_unavailable_primary_is_not_sampled(hass, config_entry, zone_config):
    coordinator = ThermalCoordinator(hass, config_entry)
    zone_id = "hall_and_landing"
    coordinator.zones = {zone_id: zone_config}
    coordinator.data = {zone_id: coordinator._empty_zone_data()}
    hass.states.async_set(zone_config["primary_sensor"], "unavailable")
    coordinator._sample(zone_id)
    assert not coordinator.data[zone_id]["history"]
