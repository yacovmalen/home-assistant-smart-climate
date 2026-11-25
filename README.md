# Smart Climate — Home Assistant Pyscript

Smart Climate is an autonomous, multi-zone climate controller for Home Assistant. It was developed for a four-zone installation built around a shared VRF system, with two central units and two independently controlled zones.

The controller evaluates each zone from a fresh snapshot of room sensors and live climate state. It combines apparent temperature for thermal comfort with dew point for moisture control, applies per-zone safety and Away policies, and coordinates the two central units for circulation and shared VRF direction. Manual HVAC mode changes are respected through temporary per-zone holds. Climate service calls are centralized so requested changes can be checked against live entity state and recorded in telemetry/logs.

This is a **reference implementation**, not a plug-and-play integration. It reflects one working installation and assumes compatible climate entities, sensor conventions, and Pyscript behavior. Review the defaults and adapt the configuration for your own equipment before enabling automatic control.

## Repository layout

```text
pyscript/
├── smart_climate.py
└── modules/
    ├── smart_climate_config.py
    └── smart_climate_defaults.py
```

- `smart_climate.py` contains the controller and execution logic.
- `modules/smart_climate_config.py` maps example zone and sensor names to Home Assistant entity IDs.
- `modules/smart_climate_defaults.py` contains the default temperature, dew-point, timing, safety, fan, circulation, and zone-weight settings.

## Installation

1. Install and configure the [Pyscript integration](https://github.com/custom-components/pyscript).
2. Copy `pyscript/smart_climate.py` to `/config/pyscript/smart_climate.py`.
3. Copy both files from `pyscript/modules/` to `/config/pyscript/modules/`.
4. Edit `smart_climate_config.py` to match your climate entities, room sensors, weather entity, Away helper, optional airflow light, and telemetry entity.
5. Review `smart_climate_defaults.py` and adjust the thresholds, schedules, timers, sensor weights, and safety limits for your home and equipment.
6. Reload Pyscript and inspect the logs and telemetry before allowing the controller to operate equipment.

Pyscript user modules are documented in the [Pyscript reference](https://hacs-pyscript.readthedocs.io/en/latest/reference.html).

## Entity and sensor assumptions

- Four climate entities are configured. The upstairs central unit is the VRF master; the downstairs and upstairs central units participate in the shared circulation behavior.
- Each room sensor mapping is a base entity ID. The engine reads separate numeric state entities by appending `_temperature` and `_humidity`. For example, `sensor.family_room` requires `sensor.family_room_temperature` and `sensor.family_room_humidity`. Adapt the sensor-reading logic if your integration exposes these values differently.
- The weather entity must expose a numeric `temperature` attribute.
- The Away helper is an input boolean.
- The optional Zone 3 light entity is used to adjust the assumed air velocity when that light is on.
- The telemetry entity ID must be available for the controller to create/update.

The supplied entity IDs are illustrative placeholders. Replace them before use.

## Control overview

Each zone is evaluated independently with humidity/dew-point control taking priority over temperature control, followed by fan behavior. Dew point is used as the moisture-control metric; apparent temperature is used for thermal comfort. DRY operation has minimum/maximum runtime, comfort-exit, and cooldown safeguards. The two central units have dedicated circulation coordination, including stagnation circulation when its configured conditions are met. The upstairs central unit is treated as the VRF master for direction arbitration.

The default schedule uses a day/night profile and a reduced sleep fan profile. Away mode changes the normal schedule and enables separate temperature safety limits. Zone-specific safety limits take precedence over Away limits where configured. Manual mode holds and several dwell/cooldown timers are held in memory and reset on a Pyscript reload or Home Assistant restart.

## Safety and validation

The controller can issue HVAC mode, target-temperature, and fan-mode commands. Before enabling it, verify supported modes, entity behavior, sensor values, equipment constraints, and every configured threshold. Keep an independent way to disable the automation. Start by observing logs and telemetry, then enable control cautiously.

The repository preserves the behavior of the source implementation as far as the configuration extraction allows; it is not independently validated against other VRF systems or climate integrations.

## License

This project is licensed under the [MIT License](LICENSE).
