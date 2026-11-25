"""Home Assistant entity mapping for the Smart Climate engine.

Replace these example entity IDs with the entities in your own installation.
Operating thresholds and behavior defaults are configured in smart_climate_defaults.py.
"""

# Climate zones. The upstairs central unit is the VRF system master.
CLIMATE_ENTITIES = {
    "downstairs": "climate.zone_downstairs",
    "upstairs": "climate.zone_upstairs",
    "zone_3": "climate.zone_3",
    "zone_4": "climate.zone_4",
}

MASTER_CLIMATE_ENTITY = CLIMATE_ENTITIES["upstairs"]
CENTRAL_CIRCULATION_ENTITIES = (
    CLIMATE_ENTITIES["downstairs"],
    CLIMATE_ENTITIES["upstairs"],
)

# Each room sensor must expose numeric `<sensor_entity>_temperature` and
# `<sensor_entity>_humidity` states, as expected by the source integration.
ROOM_SENSOR_ENTITIES = {
    "downstairs": {
        "family_room": "sensor.family_room",
        "office": "sensor.office",
        "computer_nook": "sensor.computer_nook",
    },
    "upstairs": {
        "master_bedroom": "sensor.master_bedroom",
        "living_room": "sensor.living_room",
    },
    "zone_3": {"zone_3": "sensor.zone_3_room"},
    "zone_4": {"zone_4": "sensor.zone_4_room"},
}

# Supporting entities used by the controller.
WEATHER_ENTITY = "weather.home"
AWAY_MODE_ENTITY = "input_boolean.away_mode"
TELEMETRY_ENTITY = "sensor.smart_climate_telemetry"

# Zone 3 has an optional light-dependent air-velocity adjustment.
LIGHT_DEPENDENT_AIRFLOW_ZONE = CLIMATE_ENTITIES["zone_3"]
LIGHT_DEPENDENT_AIRFLOW_ENTITY = "light.zone_3_room_light"

# State changes to these entities/attributes are observed to reconcile
# controller-issued commands and detect manual HVAC mode changes.
CLIMATE_STATE_TRIGGERS = list(
    trigger
    for entity in CLIMATE_ENTITIES.values()
    for trigger in (entity, f"{entity}.temperature", f"{entity}.fan_mode")
)
