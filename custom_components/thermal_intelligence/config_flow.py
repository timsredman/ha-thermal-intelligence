"""UI configuration for Thermal Intelligence."""

from __future__ import annotations

import json
import math
import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BURNER_ENTITIES,
    CONF_DEMAND_ENTITIES,
    CONF_OUTDOOR_SENSOR,
    CONF_SOURCES,
    CONF_ZONES,
    DOMAIN,
)


def _entity_id(value: Any) -> bool:
    """Return whether a value has Home Assistant's domain.object_id shape."""
    return isinstance(value, str) and re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", value) is not None


def _parse_zones(value: str) -> list[dict[str, Any]]:
    """Parse zones and validate names, entity IDs and per-zone metadata."""
    try:
        zones = json.loads(value)
    except (TypeError, json.JSONDecodeError) as err:
        raise vol.Invalid("Zones must be a JSON array") from err
    if not isinstance(zones, list) or not zones:
        raise vol.Invalid("At least one zone is required")
    names: set[str] = set()
    zone_ids: set[str] = set()
    for zone in zones:
        if not isinstance(zone, dict):
            raise vol.Invalid("Each zone must be an object")
        name = zone.get("name")
        primary = zone.get("primary_sensor")
        if not isinstance(name, str) or not name.strip() or name.strip().lower() in names:
            raise vol.Invalid("Each zone needs a unique non-empty name")
        names.add(name.strip().lower())
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "zone"
        if slug in zone_ids:
            raise vol.Invalid("Zone names must produce unique entity IDs")
        zone_ids.add(slug)
        if not _entity_id(primary):
            raise vol.Invalid(f"Zone {name!r} needs a primary_sensor entity ID")
        for key in ("reference_sensors",):
            value_list = zone.setdefault(key, [])
            if not isinstance(value_list, list) or not all(_entity_id(entity) for entity in value_list):
                raise vol.Invalid(f"{key} for zone {name!r} must be a list of entity IDs")
        offsets = zone.setdefault("calibration_offsets", {})
        if not isinstance(offsets, dict):
            raise vol.Invalid(f"calibration_offsets for zone {name!r} must be an object")
        try:
            parsed_offsets = {entity: float(offset) for entity, offset in offsets.items()}
        except (TypeError, ValueError) as err:
            raise vol.Invalid(f"Calibration offsets for zone {name!r} must be numeric") from err
        if not all(_entity_id(entity) and math.isfinite(offset) for entity, offset in parsed_offsets.items()):
            raise vol.Invalid(f"Calibration offsets for zone {name!r} must use entity IDs and finite values")
        zone["calibration_offsets"] = parsed_offsets
        for key, minimum in (("stale_minutes", 1.0), ("disagreement_threshold_c", 0.0)):
            if key in zone:
                try:
                    setting = float(zone[key])
                except (TypeError, ValueError) as err:
                    raise vol.Invalid(f"{key} for zone {name!r} must be numeric") from err
                if not math.isfinite(setting) or setting < minimum:
                    raise vol.Invalid(f"{key} for zone {name!r} must be at least {minimum}")
                zone[key] = setting
        placement = zone.setdefault("placement", {})
        if not isinstance(placement, dict):
            raise vol.Invalid(f"placement for zone {name!r} must be an object")
        zone["name"] = name.strip()
    return zones


def _parse_sources(value: str, zone_names: set[str] | None = None) -> list[dict[str, Any]]:
    """Parse named source mappings."""
    try:
        sources = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError) as err:
        raise vol.Invalid("Sources must be a JSON array") from err
    if not isinstance(sources, list):
        raise vol.Invalid("Sources must be a JSON array")
    source_names: set[str] = set()
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("name"), str):
            raise vol.Invalid("Each source needs a name")
        for key in ("demand_entities", "burner_entities"):
            entities = source.setdefault(key, [])
            if not isinstance(entities, list) or not all(_entity_id(entity) for entity in entities):
                raise vol.Invalid(f"{key} must be a list of entity IDs")
        source.setdefault("zones", [])
        if not isinstance(source["zones"], list) or not all(
            isinstance(zone, str) and zone.strip() for zone in source["zones"]
        ):
            raise vol.Invalid("zones must be a list of zone names")
        source["name"] = source["name"].strip()
        if not source["name"]:
            raise vol.Invalid("Each source needs a non-empty name")
        if source["name"].casefold() in source_names:
            raise vol.Invalid("Source names must be unique")
        source_names.add(source["name"].casefold())
        if zone_names is not None and any(name not in zone_names for name in source["zones"]):
            raise vol.Invalid("Source zone names must match configured zones")
    return sources


def _schema(existing: dict[str, Any] | None = None):
    """Build setup/options schema."""
    existing = existing or {}
    zones = existing.get(CONF_ZONES, [])
    sources = existing.get(CONF_SOURCES, [])
    zones_text = zones if isinstance(zones, str) else json.dumps(zones, indent=2)
    sources_text = sources if isinstance(sources, str) else json.dumps(sources, indent=2)
    fields: dict[Any, Any] = {
        vol.Required(CONF_ZONES, default=zones_text): selector.TextSelector(
            selector.TextSelectorConfig(multiline=True)
        ),
        vol.Optional(CONF_SOURCES, default=sources_text): selector.TextSelector(
            selector.TextSelectorConfig(multiline=True)
        ),
        vol.Optional(CONF_DEMAND_ENTITIES, default=existing.get(CONF_DEMAND_ENTITIES, [])): selector.EntitySelector(
            selector.EntitySelectorConfig(multiple=True)
        ),
        vol.Optional(CONF_BURNER_ENTITIES, default=existing.get(CONF_BURNER_ENTITIES, [])): selector.EntitySelector(
            selector.EntitySelectorConfig(multiple=True)
        ),
    }
    outdoor_sensor = existing.get(CONF_OUTDOOR_SENSOR)
    outdoor_key = (
        vol.Optional(CONF_OUTDOOR_SENSOR, default=outdoor_sensor)
        if outdoor_sensor
        else vol.Optional(CONF_OUTDOOR_SENSOR)
    )
    fields[outdoor_key] = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", multiple=False))
    return vol.Schema(fields)


class ThermalIntelligenceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure the integration through Home Assistant's UI."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                zones = _parse_zones(user_input[CONF_ZONES])
                sources = _parse_sources(
                    user_input.get(CONF_SOURCES, "[]"),
                    {zone["name"] for zone in zones},
                )
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Thermal Intelligence",
                    data={
                        CONF_ZONES: zones,
                        CONF_SOURCES: sources,
                        CONF_OUTDOOR_SENSOR: user_input.get(CONF_OUTDOOR_SENSOR),
                        CONF_DEMAND_ENTITIES: user_input.get(CONF_DEMAND_ENTITIES, []),
                        CONF_BURNER_ENTITIES: user_input.get(CONF_BURNER_ENTITIES, []),
                    },
                )
            except (vol.Invalid, ValueError, TypeError, KeyError):
                errors["base"] = "invalid_configuration"
        return self.async_show_form(step_id="user", data_schema=_schema(user_input), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ThermalIntelligenceOptionsFlow(config_entry)


class ThermalIntelligenceOptionsFlow(config_entries.OptionsFlow):
    """Edit the zones and source mappings after setup."""

    def __init__(self, config_entry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        existing = {**self._entry.data, **self._entry.options}
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                zones = _parse_zones(user_input[CONF_ZONES])
                sources = _parse_sources(
                    user_input.get(CONF_SOURCES, "[]"),
                    {zone["name"] for zone in zones},
                )
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_ZONES: zones,
                        CONF_SOURCES: sources,
                        CONF_OUTDOOR_SENSOR: user_input.get(CONF_OUTDOOR_SENSOR),
                        CONF_DEMAND_ENTITIES: user_input.get(CONF_DEMAND_ENTITIES, []),
                        CONF_BURNER_ENTITIES: user_input.get(CONF_BURNER_ENTITIES, []),
                    },
                )
            except (vol.Invalid, ValueError, TypeError, KeyError):
                errors["base"] = "invalid_configuration"
                existing = {**existing, **user_input}
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(existing),
            errors=errors,
        )
