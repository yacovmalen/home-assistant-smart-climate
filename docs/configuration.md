# Configuration guide

The project separates Home Assistant entity IDs from operating defaults:

- `pyscript/modules/smart_climate_config.py`: entity IDs and entity-to-zone mapping.
- `pyscript/modules/smart_climate_defaults.py`: operating thresholds, schedules, safety limits, timers, sensor weights, and other policy defaults.

## Climate zones

| Config key | Purpose | Example entity |
|---|---|---|
| `downstairs` | Central downstairs unit | `climate.zone_downstairs` |
| `upstairs` | Central upstairs unit and VRF master | `climate.zone_upstairs` |
| `zone_3` | Third independently controlled zone | `climate.zone_3` |
| `zone_4` | Fourth independently controlled zone | `climate.zone_4` |

The two central units are the only units included in shared circulation logic.

## Room sensors

Each configured room sensor is a base entity ID. The engine reads two separate state entities by appending `_temperature` and `_humidity` to that ID. For example, `sensor.family_room` requires `sensor.family_room_temperature` and `sensor.family_room_humidity`. The values must be numeric or unavailable.

Room keys and their humidity/dew-point weights are defined in `smart_climate_defaults.py`. Preserve those room keys when changing sensor IDs.

## Supporting entities

| Config variable | Expected entity behavior |
|---|---|
| `WEATHER_ENTITY` | Weather entity with numeric `temperature` attribute |
| `AWAY_MODE_ENTITY` | Input boolean; `on` means Away mode |
| `TELEMETRY_ENTITY` | Entity ID published by the controller |
| `LIGHT_DEPENDENT_AIRFLOW_ENTITY` | Optional light entity used for Zone 3 air-velocity adjustment |

## Operating defaults

Review `smart_climate_defaults.py` before enabling the controller. It contains day/night apparent-temperature thresholds, zone-specific overrides, Away and zone safety limits, heatwave mitigation, dew-point triggers, DRY runtime safeguards, circulation timing, manual hold duration, logging settings, and room weighting. These are example defaults from the source installation, not universal comfort or equipment recommendations.

## Before enabling

1. Replace every sample entity ID in `smart_climate_config.py`.
2. Confirm each room's temperature and humidity state entities exist and return numeric values.
3. Confirm the upstairs climate entity is the actual VRF master.
4. Confirm all climate entities support the modes used by the engine (`off`, `heat`, `cool`, `dry`, and `fan_only`, as applicable).
5. Review every default and safety threshold in `smart_climate_defaults.py`.
6. Check Pyscript logs and telemetry after reload before allowing automatic commands.
