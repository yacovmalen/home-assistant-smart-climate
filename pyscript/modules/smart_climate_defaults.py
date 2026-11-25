"""Default operating policy and installation-level zone configuration.

Review these values for your equipment and household before enabling control.
Entity IDs themselves are maintained in smart_climate_config.py.
"""

import smart_climate_config as CFG

LOGGING_ENABLED = True
LOG_FILE_PATH = "/config/pyscript/data/smart_climate_log.json"
MAX_LOG_AGE_HOURS = 48

# --- GLOBAL DEFAULT FALLBACK TARGETS (°C Apparent Temp) ---
COOL_TARGET_OFF = 24.5      
COOL_TARGET_ON  = 26.3      
HEAT_TARGET_ON  = 17.5      
HEAT_TARGET_OFF = 20.0     
# --- NIGHTTIME / SLEEP PROFILE (18:30 - 06:30) ---
NIGHT_START_HOUR = 18
NIGHT_START_MIN  = 30
NIGHT_END_HOUR   = 6
NIGHT_END_MIN    = 30

NIGHT_COOL_TARGET_OFF = 23.8  
NIGHT_COOL_TARGET_ON  = 25.5  
NIGHT_HEAT_TARGET_ON  = 15.0  
NIGHT_HEAT_TARGET_OFF = 17.5
# --- EQUIPMENT PROTECTION & TIMERS ---
MIN_STATE_DWELL_MINUTES = 10  
FAN_ONLY_DWELL_MINUTES  = 10  
# --- AIR CIRCULATION & STAGNATION CONSTANTS ---
STAGNATION_IDLE_MINUTES = 45    
STAGNATION_FAN_MINUTES  = 10    
STAGNATION_MIN_OUTDOOR  = 18.0  
# --- PET & AWAY SAFETY BOUNDARIES (°C Apparent Temp) ---
ZONE_4_SAFETY_COOL_MAX = 25.5  
ZONE_4_SAFETY_HEAT_MIN = 19.0  
AWAY_SAFETY_COOL_MAX       = 28.0  
AWAY_SAFETY_HEAT_MIN       = 16.0  
# --- DEW-POINT CONTROL & DRY SAFEGUARDS (°C) ---
DEW_POINT_RESTORE_TARGET       = 12.0
DEW_POINT_DRY_TRIGGER          = 15.6
DEW_POINT_DRY_IMMEDIATE        = 16.0
DEW_POINT_DRY_TRIGGER_MINUTES  = 20
DRY_MODE_MIN_RUN_MINUTES       = 30
DRY_MODE_MAX_RUN_MINUTES       = 60
DRY_MODE_COMFORT_MINUTES       = 10
DRY_MODE_COOLDOWN_MINUTES      = 30
DRY_MODE_SETBACK_OFFSET        = 2.0
DRY_MODE_TARGET_MIN            = 20.0
DRY_MODE_TARGET_MAX            = 24.5
# --- HEATWAVE LOAD MITIGATION ---
HEATWAVE_OUTDOOR_TRIGGER = 32.0 
HEATWAVE_MAX_SETBACK     = 25.5 
# --- PER-ZONE TARGETS & OVERRIDES ---
ZONE_CONFIG = {
    CFG.CLIMATE_ENTITIES["downstairs"]: {
        "room_sensors": {
            "family_room": CFG.ROOM_SENSOR_ENTITIES["downstairs"]["family_room"],
            "office": CFG.ROOM_SENSOR_ENTITIES["downstairs"]["office"],
            "computer_nook": CFG.ROOM_SENSOR_ENTITIES["downstairs"]["computer_nook"],
        },
        "humidity_weights": {"family_room": 0.75, "office": 0.25, "computer_nook": 0.0},
        "dew_point_weights": {"family_room": 0.75, "office": 0.25, "computer_nook": 0.0},
    },
    CFG.MASTER_CLIMATE_ENTITY: {
        "room_sensors": {
            "master_bedroom": CFG.ROOM_SENSOR_ENTITIES["upstairs"]["master_bedroom"],
            "living_room": CFG.ROOM_SENSOR_ENTITIES["upstairs"]["living_room"],
        },
        "humidity_weights": {"living_room": 0.6, "master_bedroom": 0.4},
        "dew_point_weights": {"living_room": 0.6, "master_bedroom": 0.4},
        "require_temperature_for_humidity": True,
    },
    CFG.CLIMATE_ENTITIES["zone_3"]: {
        "room_sensors": {"zone_3": CFG.ROOM_SENSOR_ENTITIES["zone_3"]["zone_3"]},
        "humidity_weights": {"zone_3": 1.0},
        "dew_point_weights": {"zone_3": 1.0},
    },
    CFG.CLIMATE_ENTITIES["zone_4"]: {
        "room_sensors": {"zone_4": CFG.ROOM_SENSOR_ENTITIES["zone_4"]["zone_4"]},
        "humidity_weights": {"zone_4": 1.0},
        "dew_point_weights": {"zone_4": 1.0},
    },
}

# Temperature policy is kept separate from sensor/humidity configuration.
# Per-zone four-threshold temperature policy. Shared day/night defaults can
# be overridden by individual zones; safety adjustments remain separate.
TEMPERATURE_POLICY = {
    "defaults": {
        "day": {
            "heat_on": HEAT_TARGET_ON,
            "heat_off": HEAT_TARGET_OFF,
            "cool_on": COOL_TARGET_ON,
            "cool_off": COOL_TARGET_OFF,
        },
        "night": {
            "heat_on": NIGHT_HEAT_TARGET_ON,
            "heat_off": NIGHT_HEAT_TARGET_OFF,
            "cool_on": NIGHT_COOL_TARGET_ON,
            "cool_off": NIGHT_COOL_TARGET_OFF,
        },
    },
    "zones": {
        CFG.CLIMATE_ENTITIES["zone_4"]: {
            "day": {"cool_on": 25.7, "cool_off": 23.9},
            "night": {"heat_on": 19.0, "heat_off": 20.0, "cool_on": 25.0, "cool_off": 23.3},
        },
    },
    "safety": {
        # Zone-specific pet protection takes precedence over Away protection.
        # Release uses the zone's normal comfort OFF threshold.
        "zones": {
            CFG.CLIMATE_ENTITIES["zone_4"]: {
                "cool_on": ZONE_4_SAFETY_COOL_MAX,
                "heat_on": ZONE_4_SAFETY_HEAT_MIN,
            },
        },
        # Away protection is independent of the normal day/night profile.
        "away": {
            "cool_on": AWAY_SAFETY_COOL_MAX,
            "cool_off": 25.5,
            "cool_target": COOL_TARGET_OFF,
            "heat_on": AWAY_SAFETY_HEAT_MIN,
            "heat_off": 18.0,
            "heat_target": HEAT_TARGET_OFF,
        },
    },
    "heatwave": {
        "outdoor_trigger": HEATWAVE_OUTDOOR_TRIGGER,
        "maximum_cool_off": HEATWAVE_MAX_SETBACK,
        "setback_per_degree": 0.25,
    },
}


# Manual command reconciliation and sleep-profile defaults.
MANUAL_HOLD_MINUTES = 30
PENDING_COMMAND_TIMEOUT_SECONDS = 120
SLEEP_START_HOUR = 23

# Default air-speed assumptions used by the apparent-temperature model.
LIGHT_ON_AIR_VELOCITY = 1.4
DEFAULT_AIR_VELOCITY = 0.7
