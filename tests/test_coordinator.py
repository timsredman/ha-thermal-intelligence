"""Coordinator behavior tests."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.util import dt as dt_util

from custom_components.thermal_intelligence.binary_sensor import ThermalStatusBinarySensor
from custom_components.thermal_intelligence.coordinator import (
    ThermalCoordinator,
    _aggregate_signal_state,
    _entity_is_active,
    _number,
    _slug,
)
from custom_components.thermal_intelligence.sensor import ThermalDiagnosticSensor


def test_custom_integration_translation_file_contains_entity_names():
    translation_path = (
        Path(__file__).parents[1] / "custom_components" / "thermal_intelligence" / "translations" / "en.json"
    )
    translations = json.loads(translation_path.read_text(encoding="utf-8"))

    assert translations["entity"]["sensor"]["temperature"]["name"] == "Temperature"
    assert translations["entity"]["sensor"]["heating_demand"]["name"] == "Heating demand"


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
    assert _aggregate_signal_state(hass, ["climate.nest"], demand=True) is None


async def test_signal_aggregation_distinguishes_unavailable_from_off(hass):
    assert _aggregate_signal_state(hass, []) is None
    assert _aggregate_signal_state(hass, ["binary_sensor.missing"]) is None
    hass.states.async_set("binary_sensor.call_for_heat", "unavailable")
    assert _aggregate_signal_state(hass, ["binary_sensor.call_for_heat"], demand=True) is None
    hass.states.async_set("binary_sensor.call_for_heat", "off")
    assert _aggregate_signal_state(hass, ["binary_sensor.call_for_heat"], demand=True) is False


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


async def test_setup_tolerates_null_or_invalid_persisted_timestamps(hass, config_entry, zone_config):
    coordinator = ThermalCoordinator(hass, config_entry)
    zone_id = "hall_and_landing"
    hass.states.async_set(zone_config["primary_sensor"], "21.9")
    coordinator._store.async_load = AsyncMock(
        return_value={
            zone_id: {
                "last_sample": None,
                "history": [
                    {"time": None, "temperature": 20.0},
                    {"time": "not-a-datetime", "temperature": 21.0},
                ],
            }
        }
    )

    await coordinator.async_setup()

    assert coordinator.data[zone_id]["last_sample"] is None
    assert list(coordinator.data[zone_id]["history"]) == []
    await coordinator.async_shutdown()


async def test_zone_entities_set_unique_ids_without_base_translation_key(hass, config_entry, zone_config):
    coordinator = ThermalCoordinator(hass, config_entry)
    zone_id = "hall_and_landing"
    coordinator.zones = {zone_id: zone_config}

    temperature = ThermalDiagnosticSensor(coordinator, config_entry, zone_id, "temperature", "temperature", None)
    stale = ThermalStatusBinarySensor(coordinator, config_entry, zone_id, "sensor_stale")

    assert temperature.unique_id == f"{config_entry.entry_id}_{zone_id}_temperature"
    assert stale.unique_id == f"{config_entry.entry_id}_{zone_id}_sensor_stale"


async def test_unconfigured_heating_signals_are_unknown_and_suppress_cooling_classification(
    hass, config_entry, zone_config
):
    coordinator = ThermalCoordinator(hass, config_entry)
    zone_id = "hall_and_landing"
    coordinator.zones = {zone_id: zone_config}
    coordinator.data = {zone_id: coordinator._empty_zone_data()}
    hass.states.async_set(zone_config["primary_sensor"], "21.9")
    hass.states.async_set("sensor.outdoor_temperature", "5")

    coordinator._refresh_zone(zone_id, initial=True)
    result = coordinator.data[zone_id]
    now = dt_util.utcnow()
    result["history"].extend(
        [
            {"time": (now - timedelta(minutes=20)).isoformat(), "temperature": 20.1},
            {"time": now.isoformat(), "temperature": 20.0},
        ]
    )
    result["last_sample"] = now
    coordinator.config["outdoor_sensor"] = "sensor.outdoor_temperature"
    coordinator._derive(zone_id)

    assert result["demand"] is None
    assert result["burner"] is None
    assert result["any_source_active"] is None
    assert result["source"] == "Not configured"
    assert result["rate"] is not None
    assert result["heating_rate"] is None
    assert result["cooling_rate"] is None
    assert result["retention"] is None
    demand_sensor = ThermalDiagnosticSensor(coordinator, config_entry, zone_id, "heating_demand", "demand", None)
    burner_sensor = ThermalDiagnosticSensor(coordinator, config_entry, zone_id, "burner_active", "burner", None)
    assert demand_sensor.native_value is None
    assert burner_sensor.native_value is None


async def test_unavailable_demand_does_not_end_an_observed_demand_interval(hass, config_entry, zone_config):
    coordinator = ThermalCoordinator(hass, config_entry)
    zone_id = "hall_and_landing"
    coordinator.zones = {zone_id: zone_config}
    coordinator.data = {zone_id: coordinator._empty_zone_data()}
    demand_entity = "binary_sensor.thermostat_call_for_heat"
    coordinator.config["demand_entities"] = [demand_entity]
    hass.states.async_set(zone_config["primary_sensor"], "21.9")
    hass.states.async_set(demand_entity, "on")

    coordinator._refresh_zone(zone_id, initial=True)
    started_at = coordinator.data[zone_id]["demand_started"]
    hass.states.async_set(demand_entity, "unavailable")
    coordinator._refresh_zone(zone_id)

    assert coordinator.data[zone_id]["demand"] is None
    assert coordinator.data[zone_id]["demand_started"] == started_at
    assert coordinator.data[zone_id]["last_demand_duration"] is None
