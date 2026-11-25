# Thermal Intelligence for Home Assistant

**Thermal Intelligence** is an observational custom integration from Redman Labs. It turns existing Home Assistant temperature, outdoor temperature, heating-demand and burner-state entities into thermal-zone diagnostics. It is a Python integration installed through HACS, not a Supervisor add-on.

> v0.1 never calls climate, switch, script, or automation services. It does not change heating settings. Demand and burner activity are tracked separately. Observed correlations are not evidence that one factor caused another.

## v0.1 features

- UI setup and options flow; configure any number of thermal zones.
- Per-zone primary and reference temperature entities, optional offsets, and placement metadata (height, wall, nearby window/radiator, notes).
- Optional outdoor sensor and named heating-source mappings, each with separate demand-call and actual-burner entities.
- Native temperature, robust rate-of-change, demand state/duration, burner state, source, sensor age/freshness and disagreement entities.
- Outdoor-normalised heat-retention estimate (temperature difference divided by observed cooling rate), only while the zone is cooling and the indoor/outdoor difference is meaningful.
- Event-driven source monitoring, bounded local sample history and restart persistence. No cloud APIs or external dependencies.
- Dashboard example, tests and CI.

## Requirements and compatibility

- Home Assistant Core 2026.11.2 or later (custom integration; not a Supervisor add-on). The automated test stack tracks the Core version with Home Assistant's maintained `pytest-homeassistant` package.
- HACS for convenient updates, or manual copy into `custom_components`.
- Source temperature sensors should use °C or be converted by Home Assistant. At v0.1 values are interpreted as °C.

## Setup

1. For a local trial, copy `custom_components/thermal_intelligence` to `<config>/custom_components/thermal_intelligence`.
2. Restart Home Assistant, then go to **Settings → Devices & services → Add integration → Thermal Intelligence**.
3. Enter zones as a JSON array in the Zones field. Use the actual entity IDs from your installation. Example:

```json
[
  {
    "name": "Hall, stairs and landing",
    "primary_sensor": "sensor.hall_temperature",
    "reference_sensors": ["sensor.landing_hue_temperature", "sensor.landing_smoke_temperature"],
    "calibration_offsets": {},
    "placement": {
      "sensor.landing_hue_temperature": {"floor": "first", "height": "low", "wall": "external", "notes": "Hue motion sensor"},
      "sensor.landing_smoke_temperature": {"floor": "first", "height": "low", "wall": "external", "notes": "Smoke detector reference"}
    },
    "disagreement_threshold_c": 2.0,
    "stale_minutes": 30
  },
  {"name": "Living room", "primary_sensor": "sensor.living_room_temperature"},
  {"name": "Bedroom", "primary_sensor": "sensor.bedroom_temperature"},
  {"name": "Kitchen", "primary_sensor": "sensor.kitchen_temperature"}
]
```

5. Optionally specify outdoor temperature, demand entities (thermostat call-for-heat or TRV demand), and burner entities (an entity that actually indicates boiler firing). Heating source mappings use JSON, for example:

```json
[
  {"name":"Gas combi","zones":["Hall, stairs and landing","Living room","Bedroom"],"demand_entities":["binary_sensor.nest_heating_demand"],"burner_entities":["binary_sensor.boiler_burner"]},
  {"name":"Kitchen electric heater","zones":["Kitchen"],"demand_entities":["switch.kitchen_heater"],"burner_entities":[]}
]
```

The demand and burner selectors also accept a system-wide set. If actual boiler flame/runtime is not exposed, leave burner entities empty; demand must not be described as burner runtime.

### Configuration form note

The initial form uses HA entity selectors for outdoor, demand and burner entities. Zone definitions and named source mappings are JSON text so multi-zone and per-sensor placement data can be represented without a large brittle fixed form. Validate every entity ID against **Developer tools → States** before saving.

## Entities

For each zone the integration creates diagnostics for temperature, total/heating/cooling rate, heat retention, sensor disagreement, freshness, age, demand, burner activity, heating source and demand duration, plus stale/disagreement binary sensors. Per-zone entities are grouped under a Thermal Intelligence device. Values may be unknown until valid samples arrive.

Rates use the median of adjacent sample slopes from up to the last six hours, require at least two minutes between samples and are bounded to ±10 °C/h to avoid spikes. Heat retention is an observed apparent time constant, not a building-standard U-value: `hours ≈ (indoor °C − outdoor °C) / cooling °C per hour`. It is only emitted while cooling without observed demand/burner. Solar gain, occupancy, ventilation, doors, cooking, emitters and sensor placement all affect it.

Sensor freshness uses HA state update timestamps; devices whose integrations suppress unchanged reports can appear stale. Stale reference sensors are omitted from disagreement calculations, and retention is withheld when the primary sensor is stale. Staleness is a diagnostic, not proof of a failed sensor. Disagreement compares calibrated primary and available reference sensor states; it does not correct for height or spatial gradients. Offsets are user-supplied and should only be set after calibration.

## Dashboard example

Add a manual dashboard card after setup and replace the example entity IDs with the IDs created for your zone:

```yaml
type: vertical-stack
cards:
  - type: entities
    title: Hall, stairs and landing — thermal observations
    entities:
      - sensor.thermal_intelligence_hall_stairs_and_landing_temperature
      - sensor.thermal_intelligence_hall_stairs_and_landing_temperature_rate
      - sensor.thermal_intelligence_hall_stairs_and_landing_heating_rate
      - sensor.thermal_intelligence_hall_stairs_and_landing_cooling_rate
      - sensor.thermal_intelligence_hall_stairs_and_landing_heat_retention
      - sensor.thermal_intelligence_hall_stairs_and_landing_sensor_disagreement
      - sensor.thermal_intelligence_hall_stairs_and_landing_sensor_freshness
      - binary_sensor.thermal_intelligence_hall_stairs_and_landing_sensor_stale
      - binary_sensor.thermal_intelligence_hall_stairs_and_landing_sensor_disagreement
      - sensor.thermal_intelligence_hall_stairs_and_landing_heating_demand
      - sensor.thermal_intelligence_hall_stairs_and_landing_burner_active
      - sensor.thermal_intelligence_hall_stairs_and_landing_demand_duration
  - type: history-graph
    hours_to_show: 24
    entities:
      - entity: sensor.thermal_intelligence_hall_stairs_and_landing_temperature
        name: Zone temperature
      - entity: sensor.outdoor_temperature
        name: Outdoor temperature
```

Entity IDs are generated by Home Assistant and may differ from this illustrative example. Choose them from the entity picker after installation.

## Limitations and interpretation

- v0.1 is an analysis/collection tool, not a diagnostic building survey and not a controller.
- A short snapshot or a zone temperature gradient cannot identify heat loss or its cause. Placement and air stratification are especially relevant for a low sensor near an external wall.
- The apparent retention metric is affected by weather, solar/internal gains, ventilation, occupancy and heating sources; compare longer periods with similar conditions.
- Demand entity semantics are installation-specific. Nest cloud values can be delayed or unavailable, and an on-demand signal is not verified burner operation.
- No Glowmarkt data is inferred or fetched. Whole-home gas attribution requires a compatible HA entity and careful treatment of hot water/cooking loads.
- No baseline can be reconstructed from aggregate history after installation; collect data before interpreting rates.
- Recorder retention and the integration's own rolling sample history are different. The latter is capped at 240 valid primary readings per zone (about five days at hourly sampling, shorter at faster sampling).

## Develop and verify

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements_test.txt
pytest
ruff check .
```

Use Python 3.14.2 or later for local tests. CI runs pytest, Ruff and Home Assistant `hassfest`/HACS validation. See `CONTRIBUTING.md`.

## Install rollback

1. Remove Thermal Intelligence from **Settings → Devices & services**.
2. Remove the integration through HACS, or delete `<config>/custom_components/thermal_intelligence` if manually installed.
3. Restart Home Assistant. No heating settings or automations were edited by v0.1. Removing the config entry also removes its integration-owned storage.

## Project

Repository: [timsredman/ha-thermal-intelligence](https://github.com/timsredman/ha-thermal-intelligence). License: MIT. A repository-local HACS brand icon is included and the HACS validation check passes. Add the repository in HACS as a custom repository to install it; listing in HACS default store is a separate submission. GitHub releases are optional.
