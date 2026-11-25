"""Configuration flow validation tests."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.thermal_intelligence.config_flow import _parse_sources, _parse_zones, _schema
from custom_components.thermal_intelligence.const import (
    CONF_BURNER_ENTITIES,
    CONF_DEMAND_ENTITIES,
    CONF_OUTDOOR_SENSOR,
    CONF_SOURCES,
    CONF_ZONES,
)


def test_zone_json_parses():
    assert _parse_zones('[{"name":"Hall","primary_sensor":"sensor.hall"}]')[0]["name"] == "Hall"


@pytest.mark.parametrize("value", ["not-json", "{}", "[]", '["zone"]'])
def test_zone_json_rejects_bad_shapes(value):
    with pytest.raises(vol.Invalid):
        _parse_zones(value)


def test_source_mapping_can_be_scoped_to_named_zones():
    sources = _parse_sources('[{"name":"Kitchen heater","zones":["Kitchen"],"demand_entities":["switch.heater"]}]')
    assert sources[0]["zones"] == ["Kitchen"]
    assert sources[0]["burner_entities"] == []


def test_options_schema_retains_existing_entity_mappings(zone_config):
    existing = {
        CONF_ZONES: [zone_config],
        CONF_SOURCES: [],
        CONF_OUTDOOR_SENSOR: "sensor.outdoor",
        CONF_DEMAND_ENTITIES: ["climate.nest"],
        CONF_BURNER_ENTITIES: ["binary_sensor.boiler_flame"],
    }
    restored = _schema(existing)({})
    assert restored[CONF_OUTDOOR_SENSOR] == "sensor.outdoor"
    assert restored[CONF_DEMAND_ENTITIES] == ["climate.nest"]
    assert restored[CONF_BURNER_ENTITIES] == ["binary_sensor.boiler_flame"]


@pytest.mark.parametrize(
    "value",
    [
        '[{"name":"Living room","primary_sensor":"sensor.a"},{"name":"Living-room","primary_sensor":"sensor.b"}]',
        '[{"name":"Hall","primary_sensor":"sensor.a","calibration_offsets":{"sensor.a":"nan"}}]',
    ],
)
def test_zone_json_rejects_collisions_and_non_finite_offsets(value):
    with pytest.raises(vol.Invalid):
        _parse_zones(value)


def test_source_json_rejects_unknown_zone_names():
    with pytest.raises(vol.Invalid):
        _parse_sources('[{"name":"Heater","zones":["Kitchen"]}]', {"Hall"})
