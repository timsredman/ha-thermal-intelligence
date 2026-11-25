"""Base entities for Thermal Intelligence."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ThermalCoordinator, _slug


class ThermalEntity(CoordinatorEntity, Entity):
    """Base class for zone entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ThermalCoordinator, entry: ConfigEntry, zone_id: str) -> None:
        super().__init__(coordinator, context=zone_id)
        self.thermal = coordinator
        self.zone_id = zone_id
        self.zone = coordinator.zones[zone_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{zone_id}")},
            name=f"Thermal Intelligence {self.zone['name']}",
            manufacturer="Redman Labs",
            model="Thermal zone monitor",
            sw_version="0.1.0",
        )
        self._attr_unique_id = f"{entry.entry_id}_{zone_id}_{_slug(self._attr_translation_key or 'entity')}"

    @property
    def available(self) -> bool:
        return True
