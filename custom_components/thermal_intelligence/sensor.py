"""Thermal diagnostic sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import const
from .coordinator import ThermalCoordinator
from .entity import ThermalEntity

SENSORS: tuple[tuple[str, str, str | None, str | None], ...] = (
    ("temperature", "temperature", None, None),
    ("temperature_rate", "rate", None, None),
    ("heating_rate", "heating_rate", None, None),
    ("cooling_rate", "cooling_rate", None, None),
    ("heat_retention", "retention", None, None),
    ("sensor_disagreement", "sensor_disagreement", None, None),
    ("sensor_freshness", "freshness", None, None),
    ("sensor_age", "sensor_age_minutes", None, None),
    ("heating_demand", "demand", None, None),
    ("burner_active", "burner", None, None),
    ("heating_source", "source", None, None),
    ("demand_duration", "demand_duration", None, None),
)


class ThermalDiagnosticSensor(ThermalEntity, SensorEntity):
    """Expose one thermal analysis metric."""

    _attr_should_poll = False

    def __init__(
        self,
        coordinator: ThermalCoordinator,
        entry: ConfigEntry,
        zone_id: str,
        key: str,
        metric: str,
        unit: str | None,
    ) -> None:
        super().__init__(coordinator, entry, zone_id)
        self.metric = metric
        self._attr_translation_key = key
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{entry.entry_id}_{zone_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        if metric == "temperature":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif metric in {"rate", "heating_rate", "cooling_rate", "sensor_disagreement"}:
            self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_registry_enabled_default = metric not in {"heating_rate", "cooling_rate"}

    @property
    def native_unit_of_measurement(self):
        if self.metric == "demand_duration":
            return UnitOfTime.SECONDS
        if self.metric == "sensor_age_minutes":
            return UnitOfTime.MINUTES
        if self.metric == "retention":
            return UnitOfTime.HOURS
        if self.metric in {"rate", "heating_rate", "cooling_rate"}:
            return "°C/h"
        if self.metric in {"temperature", "sensor_disagreement"}:
            return UnitOfTemperature.CELSIUS
        return None

    @property
    def native_value(self) -> Any:
        value = self.thermal.data[self.zone_id].get(self.metric)
        if self.metric in {"demand", "burner"}:
            return "on" if value else "off"
        if self.metric == "demand_duration":
            active = self.thermal.data[self.zone_id].get("demand_started")
            if active:
                from homeassistant.util import dt as dt_util

                start = dt_util.parse_datetime(active)
                return max(0, int((dt_util.utcnow() - dt_util.as_utc(start)).total_seconds())) if start else None
            return self.thermal.data[self.zone_id].get("last_demand_duration")
        return round(value, 2) if isinstance(value, float) else value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "zone": self.zone["name"],
            "primary_sensor": self.zone["primary_sensor"],
            "reference_sensors": self.zone.get("reference_sensors", []),
            "placement": self.zone.get("placement", {}),
            "calibration_offsets": self.zone.get("calibration_offsets", {}),
            "demand_is_burner_runtime": False,
            "observational_only": True,
        }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up thermal sensors."""
    coordinator: ThermalCoordinator = hass.data[const.DOMAIN][entry.entry_id]
    async_add_entities(
        ThermalDiagnosticSensor(coordinator, entry, zone_id, key, metric, unit)
        for zone_id in coordinator.zones
        for key, metric, unit, _ in SENSORS
    )
