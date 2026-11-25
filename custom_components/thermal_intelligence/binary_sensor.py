"""Thermal status binary sensors."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import const
from .coordinator import ThermalCoordinator
from .entity import ThermalEntity


class ThermalStatusBinarySensor(ThermalEntity, BinarySensorEntity):
    """Expose actionable status as read-only diagnostics."""

    _attr_should_poll = False

    def __init__(self, coordinator: ThermalCoordinator, entry: ConfigEntry, zone_id: str, kind: str) -> None:
        super().__init__(coordinator, entry, zone_id)
        self.kind = kind
        self._attr_translation_key = kind
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{entry.entry_id}_{zone_id}_{kind}"
        self._attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool | None:
        values = self.thermal.data[self.zone_id]
        if self.kind == "sensor_stale":
            return values["freshness"] in {"stale", "unavailable"}
        delta = values.get("sensor_disagreement")
        return None if delta is None else delta >= float(self.zone.get("disagreement_threshold_c", 2.0))

    @property
    def extra_state_attributes(self):
        return {"zone": self.zone["name"], "observational_only": True}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up diagnostic status entities."""
    coordinator: ThermalCoordinator = hass.data[const.DOMAIN][entry.entry_id]
    async_add_entities(
        ThermalStatusBinarySensor(coordinator, entry, zone_id, kind)
        for zone_id in coordinator.zones
        for kind in ("sensor_stale", "sensor_disagreement")
    )
