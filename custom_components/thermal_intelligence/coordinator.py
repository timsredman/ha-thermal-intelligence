"""Event-driven thermal calculations and persistence."""

from __future__ import annotations

import logging
import math
import re
from collections import deque
from datetime import datetime, timedelta
from statistics import median
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEMAND_ENTITIES,
    CONF_OUTDOOR_SENSOR,
    CONF_SOURCES,
    CONF_ZONES,
    DEFAULT_STALE_MINUTES,
    HISTORY_LIMIT,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


def _number(state: State | None) -> float | None:
    """Return a finite numeric state, ignoring unavailable values."""
    if state is None or state.state in ("unknown", "unavailable", "none", ""):
        return None
    try:
        value = float(state.state)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def _entity_signal_state(hass: HomeAssistant, entity_id: str, *, demand: bool = False) -> bool | None:
    """Interpret an entity as an active, inactive, or unknown signal."""
    state = hass.states.get(entity_id)
    if state is None or state.state.lower() in {"unknown", "unavailable", "none", ""}:
        return None
    if state.domain == "climate":
        if not demand:
            # Climate activity cannot verify actual burner telemetry.
            return None
        action = state.attributes.get("hvac_action")
        if action is not None:
            action_state = str(action).lower()
            if action_state == "heating":
                return True
            if action_state in {"idle", "off"}:
                return False
            return None
        # `hvac_mode: heat` is a selected mode, not an active call for heat.
        return False if state.state.lower() == "off" else None
    if state.state.lower() in {"off", "false", "0", "idle"}:
        return False
    return True


def _aggregate_signal_state(hass: HomeAssistant, entity_ids: list[str], *, demand: bool = False) -> bool | None:
    """Combine multiple signals without treating missing data as inactive."""
    if not entity_ids:
        return None
    states = [_entity_signal_state(hass, entity_id, demand=demand) for entity_id in entity_ids]
    if True in states:
        return True
    if all(value is False for value in states):
        return False
    return None


def _entity_is_active(hass: HomeAssistant, entity_id: str, *, demand: bool = False) -> bool:
    """Return whether a configured HA entity is positively active."""
    return _entity_signal_state(hass, entity_id, demand=demand) is True


def _slug(value: str) -> str:
    """Create a stable entity key from a zone name."""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "zone"


def _parse_datetime(value: Any) -> datetime | None:
    """Parse a persisted datetime value, tolerating null or malformed data."""
    if not isinstance(value, str):
        return None
    try:
        return dt_util.parse_datetime(value)
    except (TypeError, ValueError):
        return None


class ThermalCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Track configured sensor state with bounded, persisted histories."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name="Thermal Intelligence")
        self.hass = hass
        self.entry = entry
        self.config = {**entry.data, **entry.options}
        self.zones = {_slug(zone["name"]): zone for zone in self.config.get(CONF_ZONES, [])}
        self.data: dict[str, dict[str, Any]] = {zone_id: self._empty_zone_data() for zone_id in self.zones}
        self._store = Store(hass, STORAGE_VERSION, f"thermal_intelligence_{entry.entry_id}")
        self._unsub = None
        self._unsub_stale = None
        self._last_reported: dict[str, datetime] = {}

    @staticmethod
    def _empty_zone_data() -> dict[str, Any]:
        return {
            "temperature": None,
            "rate": None,
            "heating_rate": None,
            "cooling_rate": None,
            "retention": None,
            "sensor_disagreement": None,
            "freshness": "unknown",
            "demand": None,
            "burner": None,
            "source": "None",
            "any_source_active": None,
            "last_sample": None,
            "history": deque(maxlen=HISTORY_LIMIT),
            "demand_started": None,
            "demand_accumulated_seconds": 0,
            "last_demand_duration": None,
            "sensor_age_minutes": None,
        }

    @property
    def tracked_entities(self) -> set[str]:
        """All entity IDs this coordinator observes."""
        entities: set[str] = set(self.config.get(CONF_DEMAND_ENTITIES, []))
        entities.update(self.config.get("burner_entities", []))
        if self.config.get(CONF_OUTDOOR_SENSOR):
            entities.add(self.config[CONF_OUTDOOR_SENSOR])
        for zone in self.zones.values():
            entities.add(zone["primary_sensor"])
            entities.update(zone.get("reference_sensors", []))
        for source in self.config.get(CONF_SOURCES, []):
            entities.update(source.get("demand_entities", []))
            entities.update(source.get("burner_entities", []))
        return entities

    async def async_setup(self) -> None:
        """Restore samples and listen for source entity changes."""
        saved = await self._store.async_load() or {}
        for zone_id, restored in saved.items():
            if zone_id not in self.data or not isinstance(restored, dict):
                continue
            history = restored.get("history", [])
            if not isinstance(history, list):
                history = []
            valid_history = [
                item
                for item in history
                if isinstance(item, dict)
                and _parse_datetime(item.get("time")) is not None
                and _finite_value(item.get("temperature")) is not None
            ]
            values = {key: value for key, value in restored.items() if key != "history"}
            values["last_sample"] = _parse_datetime(values.get("last_sample"))
            values["demand_started"] = None
            # Never include server downtime in demand duration after restart.
            values["demand_accumulated_seconds"] = 0
            self.data[zone_id].update(values)
            self.data[zone_id]["history"] = deque(valid_history, maxlen=HISTORY_LIMIT)
            self.data[zone_id]["demand"] = None
            self.data[zone_id]["burner"] = None
            self.data[zone_id]["any_source_active"] = None
        for zone_id in self.zones:
            self._refresh_zone(zone_id, initial=True)
            self._derive(zone_id)
        self.async_set_updated_data(self.data)
        self._unsub = async_track_state_change_event(self.hass, self.tracked_entities, self._state_changed)
        self._unsub_stale = async_track_time_interval(self.hass, self._refresh_freshness, timedelta(minutes=1))

    async def async_shutdown(self) -> None:
        """Stop listeners and persist final state."""
        if self._unsub:
            self._unsub()
        if self._unsub_stale:
            self._unsub_stale()
        await self._save()

    @callback
    def _refresh_freshness(self, now: datetime) -> None:
        """Refresh age diagnostics without polling source entities."""
        for zone_id in self.zones:
            self._refresh_zone(zone_id)
            self._derive(zone_id)
        self.async_set_updated_data(self.data)

    @callback
    def _state_changed(self, event: Event) -> None:
        """Sample only primary sensor changes, then refresh affected metrics."""
        entity_id = event.data["entity_id"]
        self._last_reported[entity_id] = dt_util.utcnow()
        for zone_id, zone in self.zones.items():
            if entity_id == zone["primary_sensor"]:
                self._sample(zone_id)
            self._refresh_zone(zone_id)
            self._derive(zone_id)
        self.async_set_updated_data(self.data)
        self.hass.async_create_task(self._save())

    def _sample(self, zone_id: str) -> None:
        """Append a valid calibrated primary-sensor reading."""
        zone = self.zones[zone_id]
        value = _number(self.hass.states.get(zone["primary_sensor"]))
        if value is None:
            return
        value += float(zone.get("calibration_offsets", {}).get(zone["primary_sensor"], 0))
        now = dt_util.utcnow()
        history: deque = self.data[zone_id]["history"]
        if history:
            last_time = _parse_datetime(history[-1].get("time"))
            if last_time is not None and dt_util.as_utc(last_time) >= dt_util.as_utc(now):
                return
        history.append({"time": now.isoformat(), "temperature": value})
        self.data[zone_id]["last_sample"] = now

    def _derive(self, zone_id: str) -> None:
        """Calculate bounded robust rates and a cautious retention indicator."""
        result = self.data[zone_id]
        history = list(result["history"])
        cutoff = dt_util.utcnow() - timedelta(hours=6)
        points: list[tuple[datetime, float]] = []
        for item in history:
            stamp = _parse_datetime(item.get("time"))
            value = _finite_value(item.get("temperature"))
            if stamp is not None and value is not None and dt_util.as_utc(stamp) >= dt_util.as_utc(cutoff):
                points.append((dt_util.as_utc(stamp), value))
        # Median pairwise slopes (Theil–Sen) resist a single bad sensor sample.
        # Keep calculation bounded even if the persisted history is large.
        recent_points = points[-60:]
        slopes = []
        for index, (left_time, left_value) in enumerate(recent_points):
            for right_time, right_value in recent_points[index + 1 :]:
                elapsed_hours = (right_time - left_time).total_seconds() / 3600
                if elapsed_hours >= 2 / 60:
                    slopes.append((right_value - left_value) / elapsed_hours)
        if slopes:
            rate = max(-10.0, min(10.0, median(slopes)))
            result["rate"] = rate
            if result["any_source_active"] is True:
                result["heating_rate"] = rate
                result["cooling_rate"] = None
                result["retention"] = None
            elif result["any_source_active"] is False:
                result["cooling_rate"] = rate
                result["heating_rate"] = None
                outdoor_entity = self.config.get(CONF_OUTDOOR_SENSOR)
                outdoor = _number(self.hass.states.get(outdoor_entity)) if outdoor_entity else None
                indoor = result["temperature"]
                if (
                    result["freshness"] == "fresh"
                    and outdoor is not None
                    and indoor is not None
                    and indoor > outdoor + 0.5
                    and rate < 0
                ):
                    result["retention"] = round((indoor - outdoor) / -rate, 2)
                else:
                    result["retention"] = None
            else:
                result["heating_rate"] = None
                result["cooling_rate"] = None
                result["retention"] = None
        elif len(points) < 2:
            result["rate"] = None
            result["heating_rate"] = None
            result["cooling_rate"] = None
            result["retention"] = None

    def _refresh_zone(self, zone_id: str, *, initial: bool = False) -> None:
        """Refresh sensor quality, demand, burner state and source labels."""
        zone = self.zones[zone_id]
        result = self.data[zone_id]
        now = dt_util.utcnow()
        primary = self.hass.states.get(zone["primary_sensor"])
        current = _number(primary)
        last_sample = result.get("last_sample")
        if isinstance(last_sample, str):
            last_sample = _parse_datetime(last_sample)
            result["last_sample"] = last_sample
        last_reported = self._last_reported.get(zone["primary_sensor"])
        if last_reported is None and primary is not None:
            last_reported = primary.last_updated
        age = max(0.0, (now - dt_util.as_utc(last_reported)).total_seconds() / 60) if last_reported else None
        result["sensor_age_minutes"] = age
        stale_limit = max(1.0, float(zone.get("stale_minutes", DEFAULT_STALE_MINUTES)))
        if current is None:
            result["freshness"] = "unavailable"
        elif age is None:
            result["freshness"] = "stale"
        else:
            result["freshness"] = "stale" if age > stale_limit else "fresh"
        primary_offset = float(zone.get("calibration_offsets", {}).get(zone["primary_sensor"], 0))
        result["temperature"] = None if current is None else current + primary_offset

        stale_limit = max(1.0, float(zone.get("stale_minutes", DEFAULT_STALE_MINUTES)))
        references = []
        for entity_id in zone.get("reference_sensors", []):
            ref_state = self.hass.states.get(entity_id)
            value = _number(ref_state)
            reported = self._last_reported.get(entity_id) or (ref_state.last_updated if ref_state is not None else None)
            if reported is None or (now - dt_util.as_utc(reported)).total_seconds() > stale_limit * 60:
                continue
            if value is not None:
                value += float(zone.get("calibration_offsets", {}).get(entity_id, 0))
                references.append(value)
        temperature = result["temperature"]
        result["sensor_disagreement"] = (
            max(abs(temperature - value) for value in references) if temperature is not None and references else None
        )

        demand_entities = list(self.config.get(CONF_DEMAND_ENTITIES, []))
        burner_entities = list(self.config.get("burner_entities", []))
        source_name = "None"
        source_active = False
        for source in self.config.get(CONF_SOURCES, []):
            if source.get("zones") and zone["name"] not in source["zones"]:
                continue
            source_demands = source.get("demand_entities", [])
            source_burners = source.get("burner_entities", [])
            demand_entities.extend(source_demands)
            burner_entities.extend(source_burners)
            active_demand_for_source = _aggregate_signal_state(self.hass, source_demands, demand=True)
            active_burner_for_source = _aggregate_signal_state(self.hass, source_burners)
            active = active_demand_for_source is True or active_burner_for_source is True
            source_active = source_active or active
            if active:
                source_name = source.get("name", "Configured source")
        demand = _aggregate_signal_state(self.hass, demand_entities, demand=True)
        burner = _aggregate_signal_state(self.hass, burner_entities)
        active_demand = demand is True
        if demand is True and source_name == "None":
            source_name = "Configured demand"
        if burner is True and source_name == "None":
            source_name = "Configured burner signal"
        if demand is None and burner is None:
            source_name = "Not configured"
        elif source_name == "None" and demand is False and burner is False:
            source_name = "No active mapped source"
        elif source_name == "None":
            source_name = "Source status unknown"
        result["demand"] = demand
        result["burner"] = burner
        result["any_source_active"] = (
            True
            if source_active or demand is True or burner is True
            else False
            if demand is False and burner is False
            else None
        )
        result["source"] = source_name

        if initial:
            # Start a new interval at setup; never backdate from HA's current state.
            result["demand_started"] = now.isoformat() if active_demand else None
            result["demand_accumulated_seconds"] = 0
        elif demand is True and not result.get("demand_started"):
            result["demand_started"] = now.isoformat()
            result["demand_accumulated_seconds"] = 0
        elif demand is False and result.get("demand_started"):
            elapsed = _elapsed_seconds(now, result.get("demand_started"))
            result["last_demand_duration"] = int(result.get("demand_accumulated_seconds", 0)) + elapsed
            result["demand_started"] = None
            result["demand_accumulated_seconds"] = 0

    async def _save(self) -> None:
        """Persist bounded history and numeric summaries."""
        payload: dict[str, dict[str, Any]] = {}
        for zone_id, values in self.data.items():
            payload[zone_id] = {
                key: list(value) if key == "history" else value.isoformat() if isinstance(value, datetime) else value
                for key, value in values.items()
            }
        await self._store.async_save(payload)


def _finite_value(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _elapsed_seconds(now: datetime, started: Any) -> int:
    parsed = _parse_datetime(started) if isinstance(started, str) else started
    if not isinstance(parsed, datetime):
        return 0
    return max(0, int((now - dt_util.as_utc(parsed)).total_seconds()))
