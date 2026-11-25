import math
import json
from pathlib import Path
from datetime import datetime, time, timedelta

import smart_climate_config as CFG
import smart_climate_defaults as DEFAULTS

# =============================================================================
# CONFIGURATION CONSTANTS & ZONE MAP
# =============================================================================

# Shared state survives coordinator invocations, but not a Pyscript reload.
CENTRAL_CIRCULATION_ENTITIES = CFG.CENTRAL_CIRCULATION_ENTITIES
FAN_ONLY_TRACKING = {}
DEW_POINT_HIGH_START_TIMES = {}
DRY_MODE_START_TIMES = {}
DRY_MODE_COMFORT_START_TIMES = {}
DRY_MODE_COOLDOWN_UNTIL = {}
MANUAL_HOLD_UNTIL = {}
PENDING_CLIMATE_COMMANDS = {}
MASTER_DIRECTION = "cool"  # Startup fallback; updated from observed master modes.
LAST_STATE_CHANGE_TIMES = {
    entity_id: datetime.now().timestamp()
    for entity_id in CENTRAL_CIRCULATION_ENTITIES
}

# =============================================================================
# HOME ASSISTANT ACCESS, LOGGING, AND SHARED HELPERS
# =============================================================================
def write_debug_log(event_type, details):
    if not DEFAULTS.LOGGING_ENABLED:
        return
    now, path = datetime.now(), Path(DEFAULTS.LOG_FILE_PATH)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        logs = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except Exception:
        logs = []
    cutoff = now - timedelta(hours=DEFAULTS.MAX_LOG_AGE_HOURS)
    fresh = []
    for entry in logs:
        try:
            if datetime.fromisoformat(entry.get("timestamp", "1970-01-01")) >= cutoff:
                fresh.append(entry)
        except Exception:
            continue
    fresh.append({"timestamp": now.isoformat(), "event_type": event_type, "details": details})
    try:
        path.write_text(json.dumps(fresh, indent=2), encoding="utf-8")
    except Exception:
        pass

def _zone_hold_active(entity, now_ts=None):
    """Return whether this zone's temporary manual hold is still active."""
    until = MANUAL_HOLD_UNTIL.get(entity)
    if until is None:
        return False
    now_ts = now_ts if now_ts is not None else datetime.now().timestamp()
    if until <= now_ts:
        MANUAL_HOLD_UNTIL.pop(entity, None)
        return False
    return True

def _command_values_match(field, actual, expected):
    """Compare an observed climate value with the controller's expected value."""
    if field == "temperature":
        try:
            return abs(float(actual) - float(expected)) < 0.05
        except (TypeError, ValueError):
            return False
    return str(actual).lower() == str(expected).lower()

def get_state_safe(entity_id):
    try:
        val = state.get(entity_id)
        if val in [None, "unknown", "unavailable"]:
            return None
        return val
    except NameError:
        return None

def get_entity_attr(entity_id, attr_name, default=None):
    try:
        attrs = state.getattr(entity_id) or {}
        return attrs.get(attr_name, default)
    except NameError:
        return default

def reconcile_pending_climate_commands():
    """Reconcile expired controller commands against currently observed values."""
    now_ts = datetime.now().timestamp()

    for (entity, field), pending_commands in list(PENDING_CLIMATE_COMMANDS.items()):
        remaining = []

        for expected, expires_at in pending_commands:
            if expires_at > now_ts:
                remaining.append((expected, expires_at))
                continue

            # Read the currently observed value for this field.
            if field == "hvac_mode":
                observed = get_state_safe(entity)
            elif field == "temperature":
                observed = _context_float(
                    get_entity_attr(entity, "temperature")
                )
            else:
                observed = get_entity_attr(entity, "fan_mode")

            matches = _command_values_match(field, observed, expected)

            write_debug_log("HVAC_COMMAND_TIMEOUT", {
                "entity": entity,
                "field": field,
                "requested": expected,
                "observed": observed,
                "observed_matches_request": matches,
            })

        if remaining:
            PENDING_CLIMATE_COMMANDS[(entity, field)] = remaining
        else:
            PENDING_CLIMATE_COMMANDS.pop((entity, field), None)

def record_state_change(climate_entity, old_mode, new_mode, reason):
    """Record an automation-submitted HVAC mode change."""
    global MASTER_DIRECTION
    if climate_entity == CFG.MASTER_CLIMATE_ENTITY:
        if new_mode == "heat":
            MASTER_DIRECTION = "heat"
        elif new_mode in ("cool", "dry"):
            MASTER_DIRECTION = "cool"
    now_ts = datetime.now().timestamp()
    LAST_STATE_CHANGE_TIMES[climate_entity] = now_ts
    if old_mode != "dry" and new_mode == "dry":
        DRY_MODE_START_TIMES[climate_entity] = now_ts
        DRY_MODE_COMFORT_START_TIMES.pop(climate_entity, None)
    elif old_mode == "dry" and new_mode != "dry":
        DRY_MODE_START_TIMES.pop(climate_entity, None)
        DRY_MODE_COMFORT_START_TIMES.pop(climate_entity, None)
    write_debug_log("HVAC_MODE_COMMAND_SUBMITTED", {
        "entity": climate_entity,
        "from_mode": old_mode,
        "to_mode": new_mode,
        "reason": reason,
    })

def calculate_heat_index(temp_c, rh_percent, air_velocity=0.7):
    if temp_c is None or rh_percent is None:
        return None
    try:
        t = float(temp_c)
        rh = float(rh_percent)
        v = float(air_velocity)
    except (ValueError, TypeError):
        return None
    e = (rh / 100.0) * 6.105 * math.exp((17.27 * t) / (237.7 + t))
    apparent_t = t + (0.33 * e) - (0.70 * v) - 4.0
    return round(apparent_t, 1)


# =============================================================================
# EVALUATION CONTEXT
# =============================================================================
def _context_float(value):
    """Convert a value to float, returning None when unavailable or invalid."""
    if value in [None, "unknown", "unavailable", ""]:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

def calculate_dew_point(temperature, relative_humidity):
    """Calculate dew point in °C using the Magnus approximation."""
    temperature = _context_float(temperature)
    relative_humidity = _context_float(relative_humidity)
    if temperature is None or relative_humidity is None:
        return None
    if not 0.0 < relative_humidity <= 100.0:
        return None
    gamma = (
        math.log(relative_humidity / 100.0)
        + (17.62 * temperature) / (243.12 + temperature)
    )
    denominator = 17.62 - gamma
    if abs(denominator) < 1e-9:
        return None
    return round((243.12 * gamma) / denominator, 2)

class ZoneContext:
    """Inputs and derived values for one climate zone."""
    def telemetry(self):
        return {
            **{f"temp_{room}": value for room, value in self.room_temperatures.items()},
            **{f"rh_{room}": value for room, value in self.room_humidities.items()},
            "combined_temperature_c": self.combined_temperature,
            "apparent_temp": self.apparent_temperature,
            "humidity_rh_diagnostic": self.humidity,
            "dew_point_c": self.dew_point,
            "dew_point_points_c": self.dew_point_points,
            "dew_point_high_point_override_active": self.dew_point_high_point_override_active,
            "dew_point_calculation_mode": self.dew_point_calculation_mode,
            "dew_point_restore_target_c": DEFAULTS.DEW_POINT_RESTORE_TARGET,
            "dew_point_dry_trigger_c": DEFAULTS.DEW_POINT_DRY_TRIGGER,
            "dew_point_immediate_trigger_c": DEFAULTS.DEW_POINT_DRY_IMMEDIATE,
            "dew_point_high_duration_minutes": self.dew_point_high_duration_minutes,
            "dry_cycle_duration_minutes": self.dry_cycle_duration_minutes,
            "dry_comfort_duration_minutes": self.dry_comfort_duration_minutes,
            "dry_minimum_run_minutes": DEFAULTS.DRY_MODE_MIN_RUN_MINUTES,
            "dry_maximum_run_minutes": DEFAULTS.DRY_MODE_MAX_RUN_MINUTES,
            "dry_comfort_qualification_minutes": DEFAULTS.DRY_MODE_COMFORT_MINUTES,
            "dry_cooldown_remaining_minutes": self.dry_cooldown_remaining_minutes,
            "hvac_mode": self.hvac_mode, "fan_mode": self.fan_mode,
            "manual_hold_active": self.hold_active,
            "idle_minutes": round(self.idle_minutes, 1),
            "short_cycling": self.short_cycling,
            "target_temp": self.target_temperature,
            "humidity_calculation_mode": self.humidity_calculation_mode,
        }

def _temperature_profile(zone, night):
    """Resolve the legacy day/night temperature values and zone overrides."""
    period = "night" if night else "day"
    values = dict(DEFAULTS.TEMPERATURE_POLICY["defaults"][period])
    values.update(DEFAULTS.TEMPERATURE_POLICY["zones"].get(zone.entity_id, {}).get(period, {}))
    return values

def calculate_zone_targets_from_context(zone, context):
    """Resolve four per-zone hysteresis thresholds and the fan profile."""
    night = context.night_active
    fan = "low" if night and context.sleeptime else "auto"
    values = _temperature_profile(zone, night)

    heat_on = values["heat_on"]
    heat_off = values["heat_off"]
    cool_on = values["cool_on"]
    cool_off = values["cool_off"]

    # Heatwave mitigation remains an independent daytime cooling adjustment.
    heatwave = DEFAULTS.TEMPERATURE_POLICY["heatwave"]
    if (not night and context.outdoor_temp is not None
            and context.outdoor_temp > heatwave["outdoor_trigger"]):
        cool_off = round(min(
            heatwave["maximum_cool_off"],
            cool_off + (context.outdoor_temp - heatwave["outdoor_trigger"]) * heatwave["setback_per_degree"],
        ), 1)

    return {
        "heat_on": heat_on,
        "heat_off": heat_off,
        "cool_on": cool_on,
        "cool_off": cool_off,
        "fan_speed": fan,
    }


def effective_temperature_thresholds(zone, context):
    """Return the thresholds the temperature evaluator will actually apply."""
    thresholds = {
        key: zone.targets[key]
        for key in ("heat_on", "heat_off", "cool_on", "cool_off")
    }
    zone_safety = DEFAULTS.TEMPERATURE_POLICY["safety"]["zones"].get(zone.entity_id, {})
    if zone_safety:
        thresholds["heat_on"] = zone_safety.get("heat_on", thresholds["heat_on"])
        thresholds["cool_on"] = zone_safety.get("cool_on", thresholds["cool_on"])
    elif context.away_active:
        away = DEFAULTS.TEMPERATURE_POLICY["safety"]["away"]
        thresholds.update({
            "heat_on": away["heat_on"],
            "heat_off": away["heat_off"],
            "cool_on": away["cool_on"],
            "cool_off": away["cool_off"],
        })
    return thresholds


class EvaluationContext:
    """Fresh shared snapshot of raw inputs and derived zone values."""
    def build(self):
        now = datetime.now()
        clock, now_ts = now.time(), now.timestamp()
        self.timestamp = now
        self.away_active = get_state_safe(CFG.AWAY_MODE_ENTITY) == "on"
        self.outdoor_temp = _context_float(get_entity_attr(CFG.WEATHER_ENTITY, "temperature"))
        self.night_active = (
            clock >= time(DEFAULTS.NIGHT_START_HOUR, DEFAULTS.NIGHT_START_MIN)
            or clock <= time(DEFAULTS.NIGHT_END_HOUR, DEFAULTS.NIGHT_END_MIN)
        ) and not self.away_active
        self.sleeptime = clock > time(DEFAULTS.SLEEP_START_HOUR) or clock <= time(DEFAULTS.NIGHT_END_HOUR, DEFAULTS.NIGHT_END_MIN)
        self.zones = {}

        for entity, config in DEFAULTS.ZONE_CONFIG.items():
            rooms = config["room_sensors"]
            zone = ZoneContext()
            zone.entity_id, zone.config = entity, config
            zone.hvac_mode = get_state_safe(entity)
            zone.target_temperature = _context_float(get_entity_attr(entity, "temperature"))
            zone.fan_mode = get_entity_attr(entity, "fan_mode")
            zone.ac_temperature = _context_float(get_entity_attr(entity, "current_temperature"))

            # Read each room's raw temperature and RH exactly once per snapshot.
            zone.room_temperatures = {
                room: _context_float(get_state_safe(f"{sensor}_temperature"))
                for room, sensor in rooms.items()
            }
            zone.room_humidities = {
                room: _context_float(get_state_safe(f"{sensor}_humidity"))
                for room, sensor in rooms.items()
            }

            # Preserve the existing temperature synthesis: central zones average
            # room readings plus the AC's current temperature. Single-room zones
            # use their room sensor, falling back to the AC current temperature.
            if entity in CENTRAL_CIRCULATION_ENTITIES:
                temperature_values = list(zone.room_temperatures.values())
                temperature_values.append(zone.ac_temperature)
                valid_temperatures = [v for v in temperature_values if v is not None]
                zone.combined_temperature = (
                    round(sum(valid_temperatures) / len(valid_temperatures), 1)
                    if valid_temperatures else None
                )
            else:
                room_temperature = next(iter(zone.room_temperatures.values()), None)
                zone.combined_temperature = (
                    room_temperature if room_temperature is not None else zone.ac_temperature
                )

            # Missing RH keeps the established 50% fallback. Zones 3/4 also
            # treat a reported zero as missing.
            humidity_values = {}
            for room, raw_value in zone.room_humidities.items():
                value = raw_value
                if value is None or (entity in (CFG.CLIMATE_ENTITIES["zone_3"], CFG.CLIMATE_ENTITIES["zone_4"]) and value == 0.0):
                    value = 50.0
                humidity_values[room] = value

            humidity_weights = config.get("humidity_weights", {})
            total_humidity_weight = 0.0
            weighted_humidity_sum = 0.0
            for room, weight in humidity_weights.items():
                if room in humidity_values and weight > 0.0:
                    total_humidity_weight += weight
                    weighted_humidity_sum += humidity_values[room] * weight

            if config.get("require_temperature_for_humidity") and zone.combined_temperature is None:
                zone.humidity = None
            elif total_humidity_weight > 0.0:
                zone.humidity = round(
                    weighted_humidity_sum / total_humidity_weight, 1
                )
            else:
                zone.humidity = None

            zone.humidity_calculation_mode = (
                "weighted" if zone.humidity is not None else "unavailable"
            )

            # Apparent temperature remains the comfort-control input. It is
            # calculated from this same snapshot, never read from a derived HA
            # sensor. Zone 3 retains its light-dependent air-velocity adjustment.
            air_velocity = (
                DEFAULTS.LIGHT_ON_AIR_VELOCITY
                if entity == CFG.LIGHT_DEPENDENT_AIRFLOW_ZONE
                and get_state_safe(CFG.LIGHT_DEPENDENT_AIRFLOW_ENTITY) == "on"
                else DEFAULTS.DEFAULT_AIR_VELOCITY
            )
            zone.apparent_temperature = calculate_heat_index(
                zone.combined_temperature, zone.humidity, air_velocity
            )

            zone.dew_point_points = {}
            for room in rooms:
                point_temp = zone.room_temperatures.get(room)
                if point_temp is None:
                    point_temp = zone.ac_temperature
                point_rh = humidity_values.get(room)
                point_dew_point = calculate_dew_point(point_temp, point_rh)
                if point_dew_point is not None:
                    zone.dew_point_points[room] = point_dew_point

            valid_points = zone.dew_point_points
            dew_point_weights = config.get("dew_point_weights", {})
            highest_point = max(valid_points.values()) if valid_points else None
            zone.dew_point_high_point_override_active = (
                highest_point is not None and highest_point > DEFAULTS.DEW_POINT_DRY_TRIGGER
            )
            if zone.dew_point_high_point_override_active:
                # Preserve the existing high-individual-room override, including
                # rooms whose normal weighted contribution is zero.
                zone.dew_point = highest_point
                zone.dew_point_calculation_mode = "high_point_override"
            else:
                weighted_points = [
                    (valid_points[room], weight)
                    for room, weight in dew_point_weights.items()
                    if room in valid_points and weight > 0.0
                ]
                total_weight = 0.0
                weighted_dew_point_sum = 0.0
                for value, weight in weighted_points:
                    total_weight += weight
                    weighted_dew_point_sum += value * weight
                zone.dew_point = (
                    round(weighted_dew_point_sum / total_weight, 2)
                    if total_weight > 0.0 else None
                )
                zone.dew_point_calculation_mode = (
                    "weighted" if zone.dew_point is not None else "unavailable"
                )

            # Track each observed DRY cycle independently. If DRY was already
            # active on script reload, begin timing from this first observation.
            if zone.hvac_mode == "dry":
                dry_since = DRY_MODE_START_TIMES.setdefault(entity, now_ts)
                zone.dry_cycle_duration_minutes = max(0.0, (now_ts - dry_since) / 60.0)
                if (
                    zone.dry_cycle_duration_minutes >= DEFAULTS.DRY_MODE_MIN_RUN_MINUTES
                    and zone.dew_point is not None
                    and zone.dew_point <= DEFAULTS.DEW_POINT_DRY_TRIGGER
                ):
                    # The 10-minute comfort qualification begins only after
                    # the minimum DRY runtime has elapsed.
                    comfort_since = DRY_MODE_COMFORT_START_TIMES.setdefault(entity, now_ts)
                    zone.dry_comfort_duration_minutes = max(0.0, (now_ts - comfort_since) / 60.0)
                else:
                    DRY_MODE_COMFORT_START_TIMES.pop(entity, None)
                    zone.dry_comfort_duration_minutes = 0.0
            else:
                DRY_MODE_START_TIMES.pop(entity, None)
                DRY_MODE_COMFORT_START_TIMES.pop(entity, None)
                zone.dry_cycle_duration_minutes = 0.0
                zone.dry_comfort_duration_minutes = 0.0

            cooldown_until = DRY_MODE_COOLDOWN_UNTIL.get(entity)
            if cooldown_until is not None and cooldown_until <= now_ts:
                DRY_MODE_COOLDOWN_UNTIL.pop(entity, None)
                cooldown_until = None
            zone.dry_cooldown_remaining_minutes = (
                max(0.0, (cooldown_until - now_ts) / 60.0)
                if cooldown_until is not None else 0.0
            )
            zone.dry_cooldown_active = zone.dry_cooldown_remaining_minutes > 0.0

            zone.dew_point_high_duration_minutes = 0.0
            zone.hold_active = _zone_hold_active(entity, now_ts)
            last_change = LAST_STATE_CHANGE_TIMES.get(entity)
            elapsed = now_ts - last_change if last_change is not None else None
            zone.idle_minutes = max(0.0, elapsed / 60) if elapsed is not None else 0.0
            zone.short_cycling = elapsed is not None and elapsed < DEFAULTS.MIN_STATE_DWELL_MINUTES * 60
            self.zones[entity] = zone

        global MASTER_DIRECTION
        master_mode = self.zones[CFG.MASTER_CLIMATE_ENTITY].hvac_mode
        if master_mode == "heat":
            MASTER_DIRECTION = "heat"
        elif master_mode in ("cool", "dry"):
            MASTER_DIRECTION = "cool"

        self.humidity_control_blocked = False
        for zone in self.zones.values():
            if zone.hvac_mode == "heat":
                self.humidity_control_blocked = True
                break

        for zone in self.zones.values():
            high_since = DEW_POINT_HIGH_START_TIMES.get(zone.entity_id)
            if (
                self.humidity_control_blocked
                or zone.dry_cooldown_active
                or zone.dew_point is None
                or zone.dew_point <= DEFAULTS.DEW_POINT_DRY_TRIGGER
            ):
                DEW_POINT_HIGH_START_TIMES.pop(zone.entity_id, None)
                high_since = None
            elif high_since is None:
                high_since = now_ts
                DEW_POINT_HIGH_START_TIMES[zone.entity_id] = high_since
            zone.dew_point_high_duration_minutes = (
                max(0.0, (now_ts - high_since) / 60.0) if high_since is not None else 0.0
            )
            zone.targets = calculate_zone_targets_from_context(zone, self)
        return self

# =============================================================================
# REQUEST VALIDATION AND ZONE POLICIES
# =============================================================================
def make_climate_request(entity_id, fields, policy, reason, dry_cooldown_minutes=0):
    """Build a validated request; policy functions never call HA services."""
    if entity_id not in DEFAULTS.ZONE_CONFIG:
        raise ValueError(f"Unknown climate entity: {entity_id}")
    if (not isinstance(fields, dict) or not fields
            or not set(fields) <= {"hvac_mode", "temperature", "fan_mode"}):
        raise ValueError(f"Invalid climate request fields: {fields}")
    if not isinstance(policy, str) or not policy.strip() or not isinstance(reason, str) or not reason.strip():
        raise ValueError("Climate request policy and reason must be non-empty strings")
    if (not isinstance(dry_cooldown_minutes, int)
            or isinstance(dry_cooldown_minutes, bool) or dry_cooldown_minutes < 0):
        raise ValueError("dry_cooldown_minutes must be a non-negative integer")
    if dry_cooldown_minutes and fields.get("hvac_mode") in (None, "dry"):
        raise ValueError("A DRY cooldown requires a request to leave DRY mode")
    validated = dict(fields)
    for field, attr in (("hvac_mode", "hvac_modes"), ("fan_mode", "fan_modes")):
        if field not in validated:
            continue
        value = validated[field]
        supported = get_entity_attr(entity_id, attr)
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        if not isinstance(supported, (list, tuple)):
            raise ValueError(f"Cannot determine supported {field} values for {entity_id}")
        if value not in supported:
            raise ValueError(f"Unsupported {field} for {entity_id}: {value}")
    if "temperature" in validated:
        value = validated["temperature"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("temperature must be a finite numeric value")
        try:
            value = float(value)
        except (ValueError, TypeError, OverflowError):
            raise ValueError("temperature must be a finite numeric value")
        if not math.isfinite(value):
            raise ValueError("temperature must be finite")
        validated["temperature"] = value
    request = {"entity_id": entity_id, "fields": validated, "policy": policy, "reason": reason}
    if dry_cooldown_minutes:
        request["dry_cooldown_minutes"] = dry_cooldown_minutes
    return request

def _initial_dry_target(ac_temperature):
    return round(max(DEFAULTS.DRY_MODE_TARGET_MIN, min(DEFAULTS.DRY_MODE_TARGET_MAX, ac_temperature - DEFAULTS.DRY_MODE_SETBACK_OFFSET)), 1)

def evaluate_zone_humidity(zone, context):
    """Apply the per-zone dew-point activation and bounded DRY lifecycle."""
    dew_point = zone.dew_point
    mode = zone.hvac_mode
    hard_limit = (
        mode != "dry" and dew_point is not None
        and dew_point >= DEFAULTS.DEW_POINT_DRY_IMMEDIATE
    )
    ac_temp = zone.ac_temperature if zone.ac_temperature is not None else 24.5

    if mode == "dry":
        # DRY completion releases the unit to OFF. The temperature evaluator
        # may request HEAT or COOL on the next coordinator pass.
        fields = {"hvac_mode": "off", "fan_mode": "auto"}

        # A genuinely dry result ends the cycle immediately, even before the
        # minimum runtime. This is a moisture target, not a comfort timer.
        if dew_point is not None and dew_point <= DEFAULTS.DEW_POINT_RESTORE_TARGET:
            return make_climate_request(
                zone.entity_id, fields, "humidity",
                f"DRY Stop: dew point {dew_point:.2f}°C <= {DEFAULTS.DEW_POINT_RESTORE_TARGET:.1f}°C"
            )

        # The 60-minute cap is unconditional. Start cooldown only if the
        # executor successfully submits the mode change away from DRY.
        if zone.dry_cycle_duration_minutes >= (DEFAULTS.DRY_MODE_MAX_RUN_MINUTES - 0.1):
            return make_climate_request(
                zone.entity_id, fields, "humidity",
                f"DRY Maximum Runtime: {zone.dry_cycle_duration_minutes:.0f}m reached; stop DRY",
                dry_cooldown_minutes=DEFAULTS.DRY_MODE_COOLDOWN_MINUTES,
            )

        # Once the 30-minute minimum has elapsed, 10 continuous minutes in
        # the comfortable range is sufficient reason to stop dehumidifying.
        if (
            zone.dry_cycle_duration_minutes >= DEFAULTS.DRY_MODE_MIN_RUN_MINUTES
            and dew_point is not None
            and zone.dry_comfort_duration_minutes >= (DEFAULTS.DRY_MODE_COMFORT_MINUTES - 0.1)
        ):
            return make_climate_request(
                zone.entity_id, fields, "humidity",
                f"DRY Comfortable: dew point {dew_point:.2f}°C <= {DEFAULTS.DEW_POINT_DRY_TRIGGER:.1f}°C "
                f"for {zone.dry_comfort_duration_minutes:.0f}m after minimum run"
            )
        return None

    # The global HEAT guard suppresses new DRY starts only. Exit safeguards for
    # a zone already in DRY have already been evaluated above.
    if context.humidity_control_blocked and not hard_limit:
        return None

    # A missing dew point cannot start a new cycle, but it cannot defeat the
    # maximum runtime above for a cycle that is already active.
    if dew_point is None:
        return None

    # New DRY cycles are suppressed during the post-maximum cooldown.
    if zone.dry_cooldown_active:
        return None

    # Activation remains based on the established sustained/immediate limits.
    high_long_enough = zone.dew_point_high_duration_minutes >= DEFAULTS.DEW_POINT_DRY_TRIGGER_MINUTES
    immediate = dew_point >= DEFAULTS.DEW_POINT_DRY_IMMEDIATE
    should_start_dry = immediate or (
        dew_point > DEFAULTS.DEW_POINT_DRY_TRIGGER and high_long_enough
    )
    if should_start_dry and mode in ("heat", "cool", "off", "fan_only"):
        if mode == "heat" and not immediate:
            return None
        # Every new DRY mode transition, including the immediate hard limit,
        # remains subject to the zone's short-cycle protection.
        if not zone.short_cycling:
            target = _initial_dry_target(ac_temp)
            trigger_reason = (
                f"immediate threshold {DEFAULTS.DEW_POINT_DRY_IMMEDIATE:.1f}°C"
                if immediate else
                f"above {DEFAULTS.DEW_POINT_DRY_TRIGGER:.1f}°C for {zone.dew_point_high_duration_minutes:.0f}m"
            )
            request = make_climate_request(
                zone.entity_id, {"hvac_mode": "dry", "temperature": target, "fan_mode": "auto"},
                "humidity",
                f"High Dew Point Override: {dew_point:.2f}°C ({trigger_reason}; Live Temp: {ac_temp}°C -> Set: {target}°C)"
            )
            request["hard_limit_override"] = immediate
            return request
    return None

def evaluate_zone_fan(zone, context):
    """Evaluate independent fan/circulation needs for one zone."""

    # Stagnation circulation applies only to the central units.
    if zone.entity_id not in CENTRAL_CIRCULATION_ENTITIES:
        return None

    # Stagnation circulation is year-round, but remains disabled in Away.
    if context.away_active:
        return None

    # Outdoor temperature must be available and sufficiently warm.
    if (
        context.outdoor_temp is None
        or context.outdoor_temp < DEFAULTS.STAGNATION_MIN_OUTDOOR
    ):
        return None

    # Stagnation window: 06:30 through 23:00.
    # Use the snapshot timestamp directly rather than sleeptime:
    # sleeptime includes exactly 06:30, while stagnation starts at 06:30.
    now_time = context.timestamp.time()
    if not (time(6, 30) <= now_time <= time(23, 0)):
        return None

    # Only an idle, OFF unit can start a stagnation cycle.
    if zone.hvac_mode != "off":
        return None
    if zone.idle_minutes < DEFAULTS.STAGNATION_IDLE_MINUTES:
        return None
    if zone.short_cycling:
        return None
    return make_climate_request(
        zone.entity_id,
        {
            "hvac_mode": "fan_only",
            "fan_mode": "auto",
        },
        "fan",
        f"Stagnation Guard: Idle for {zone.idle_minutes:.0f}m",
    )

  

def _temperature_request(zone, mode, fan_speed, reason, target=None):
    fields = {"hvac_mode": mode, "fan_mode": fan_speed}
    if target is not None:
        fields["temperature"] = target
    return make_climate_request(
        zone.entity_id, fields, "temperature", reason
    )

def evaluate_zone_temperature(zone, context):
    """Evaluate independent per-zone heat/cool hysteresis and return a request."""
    apparent = zone.apparent_temperature
    current_mode = zone.hvac_mode

    # No temperature decision is possible without apparent temperature; DRY
    # owns the unit until its dew-point lifecycle requests an exit.
    if apparent is None or current_mode == "dry":
        return None

    thresholds = effective_temperature_thresholds(zone, context)
    heat_on = thresholds["heat_on"]
    heat_off = thresholds["heat_off"]
    cool_on = thresholds["cool_on"]
    cool_off = thresholds["cool_off"]
    fan_speed = zone.targets["fan_speed"]
    short_cycling = zone.short_cycling
    zone_safety = DEFAULTS.TEMPERATURE_POLICY["safety"]["zones"].get(zone.entity_id, {})

    # An active mode is stopped only at its own OFF threshold. The next
    # coordinator pass may then request the opposite direction if warranted.
    if current_mode == "heat":
        if apparent >= heat_off and not short_cycling:
            return _temperature_request(
                zone, "off", fan_speed,
                f"Heat OFF: {apparent:.2f}°C >= {heat_off:.1f}°C",
            )
        return None

    if current_mode == "cool":
        if apparent <= cool_off and not short_cycling:
            next_mode = "off" if context.night_active else "fan_only"
            return _temperature_request(
                zone, next_mode, fan_speed,
                f"Cool OFF: {apparent:.2f}°C <= {cool_off:.1f}°C",
            )
        return None

    # OFF and FAN_ONLY zones can request either direction at their ON
    # threshold. Direction conflicts are handled centrally by VRF arbitration.
    if current_mode in ("off", "fan_only"):
        if apparent <= heat_on and not short_cycling:
            heat_target = (
                DEFAULTS.TEMPERATURE_POLICY["safety"]["away"]["heat_target"]
                if context.away_active and not zone_safety
                else heat_off
            )
            return _temperature_request(
                zone, "heat", fan_speed,
                f"Heat ON: {apparent:.2f}°C <= {heat_on:.1f}°C",
                heat_target,
            )
        if apparent >= cool_on and not short_cycling:
            cool_target = (
                DEFAULTS.TEMPERATURE_POLICY["safety"]["away"]["cool_target"]
                if context.away_active and not zone_safety
                else cool_off
            )
            return _temperature_request(
                zone, "cool", fan_speed,
                f"Cool ON: {apparent:.2f}°C >= {cool_on:.1f}°C",
                cool_target,
            )
    return None

# =============================================================================
# CLIMATE SERVICE EXECUTION
# =============================================================================
def execute_climate_plan(plan):
    """Submit each requested field once, rechecking live state before every call."""
    results = []
    for action in plan:
        entity, fields = action["entity_id"], action["fields"]
        policy, reason = action["policy"], action["reason"]
        result = {"entity_id": entity, "policy": policy, "reason": reason, "fields": {}}
        for field in ("hvac_mode", "temperature", "fan_mode"):
            if field not in fields:
                continue
            requested = fields[field]
            try:
                if _zone_hold_active(entity):
                    result["fields"][field] = {"status": "skipped", "reason": "manual_hold"}
                    continue
                if field == "hvac_mode":
                    actual = get_state_safe(entity)
                    if actual == requested:
                        cooldown_minutes = action.get("dry_cooldown_minutes", 0)
                        if field == "hvac_mode" and requested != "dry" and cooldown_minutes:
                            DRY_MODE_COOLDOWN_UNTIL[entity] = (
                                datetime.now().timestamp() + cooldown_minutes * 60
                            )
                            result["dry_cooldown_started_minutes"] = cooldown_minutes
                        result["fields"][field] = {"status": "unchanged"}
                        continue
                    PENDING_CLIMATE_COMMANDS.setdefault((entity, field), []).append(
                        (requested, datetime.now().timestamp() + DEFAULTS.PENDING_COMMAND_TIMEOUT_SECONDS)
                    )
                    climate.set_hvac_mode(entity_id=entity, hvac_mode=requested)
                    if requested == "fan_only" and actual != "fan_only":
                        tracking = action.get("fan_only_reason")
                        if tracking:
                            FAN_ONLY_TRACKING[entity] = {"reason": tracking}
                        else:
                            FAN_ONLY_TRACKING.pop(entity, None)
                    elif actual == "fan_only" and requested != "fan_only":
                        FAN_ONLY_TRACKING.pop(entity, None)
                    record_state_change(entity, actual, requested, f"{policy}: {reason}")
                    cooldown_minutes = action.get("dry_cooldown_minutes", 0)
                    if actual == "dry" and requested != "dry" and cooldown_minutes:
                        DRY_MODE_COOLDOWN_UNTIL[entity] = (
                            datetime.now().timestamp() + cooldown_minutes * 60
                        )
                        result["dry_cooldown_started_minutes"] = cooldown_minutes
                elif field == "temperature":
                    actual = _context_float(get_entity_attr(entity, "temperature"))
                    if actual is not None and abs(actual - requested) < 0.05:
                        result["fields"][field] = {"status": "unchanged"}
                        continue
                    PENDING_CLIMATE_COMMANDS[(entity, field)] = [(
                        requested, datetime.now().timestamp() + DEFAULTS.PENDING_COMMAND_TIMEOUT_SECONDS
                    )]
                    climate.set_temperature(entity_id=entity, temperature=requested)
                    write_debug_log("HVAC_FIELD_COMMAND_SUBMITTED", {
                        "entity": entity, "field": field, "requested": requested,
                        "reason": reason,
                    })
                else:
                    actual = get_entity_attr(entity, "fan_mode")
                    if actual == requested:
                        result["fields"][field] = {"status": "unchanged"}
                        continue
                    PENDING_CLIMATE_COMMANDS[(entity, field)] = [(
                        requested, datetime.now().timestamp() + DEFAULTS.PENDING_COMMAND_TIMEOUT_SECONDS
                    )]
                    climate.set_fan_mode(entity_id=entity, fan_mode=requested)
                    write_debug_log("HVAC_FIELD_COMMAND_SUBMITTED", {
                        "entity": entity, "field": field, "requested": requested,
                        "reason": reason,
                    })
                result["fields"][field] = {"status": "submitted"}
            except Exception as err:
                PENDING_CLIMATE_COMMANDS.pop((entity, field), None)
                write_debug_log("HVAC_COMMAND_FAILED", {
                    "entity": entity, "field": field, "requested": requested,
                    "reason": reason, "error": str(err),
                })
                result["fields"][field] = {"status": "failed", "error": str(err)}
                log.error("Climate execution failed: %s %s=%s: %s", entity, field, requested, err)

        results.append(result)
    return results

@state_trigger(CFG.CLIMATE_STATE_TRIGGERS)
def observe_climate_change(var_name=None, value=None, old_value=None):
    """Match observed changes to controller commands; unmatched mode changes hold the zone."""
    if not var_name:
        return
    if var_name.endswith(".temperature"):
        entity, field = var_name[:-12], "temperature"
    elif var_name.endswith(".fan_mode"):
        entity, field = var_name[:-9], "fan_mode"
    elif var_name in DEFAULTS.ZONE_CONFIG:
        entity, field = var_name, "hvac_mode"
    else:
        return

    if old_value == value:
        return
    if field == "hvac_mode" and value in (None, "unknown", "unavailable"):
        return
    if field == "hvac_mode" and old_value in (None, "unknown", "unavailable"):
        return

    now_ts = datetime.now().timestamp()
    if field == "hvac_mode" and entity == CFG.MASTER_CLIMATE_ENTITY:
        global MASTER_DIRECTION
        if value == "heat":
            MASTER_DIRECTION = "heat"
        elif value in ("cool", "dry"):
            MASTER_DIRECTION = "cool"
    key = (entity, field)
    pending_commands = PENDING_CLIMATE_COMMANDS.get(key, [])

    if pending_commands:
        expected, expires_at = pending_commands[0]

        matches = _command_values_match(field, value, expected)

        if expires_at > now_ts and matches:
            pending_commands.pop(0)

            if not pending_commands:
                PENDING_CLIMATE_COMMANDS.pop(key, None)

            write_debug_log("HVAC_COMMAND_CONFIRMED", {
                "entity": entity, "field": field, "requested": expected,
                "observed": value, "confirmed_within_timeout": True,
            })
            return

        if expires_at <= now_ts:
            pending_commands.pop(0)

            if not pending_commands:
                PENDING_CLIMATE_COMMANDS.pop(key, None)

        write_debug_log("HVAC_COMMAND_MISMATCH", {
            "entity": entity, "field": field, "requested": expected,
            "observed": value, "expired": expires_at <= now_ts,
            "treated_as_external_change": True,
        })

    if field == "hvac_mode":
        # External mode transitions reset the dwell/idle clock even when the
        # zone is already held by an earlier manual transition.
        LAST_STATE_CHANGE_TIMES[entity] = now_ts
        hold_until = MANUAL_HOLD_UNTIL.get(entity)
        if hold_until is not None and hold_until > now_ts:
            remaining_seconds = hold_until - now_ts
            if remaining_seconds <= 10 * 60:
                MANUAL_HOLD_UNTIL[entity] = hold_until + DEFAULTS.MANUAL_HOLD_MINUTES * 60
                write_debug_log("MANUAL_HOLD_EXTENDED", {
                    "entity": entity,
                    "from_mode": old_value,
                    "to_mode": value,
                    "remaining_before_seconds": round(remaining_seconds, 1),
                    "extension_minutes": DEFAULTS.MANUAL_HOLD_MINUTES,
                })
            else:
                write_debug_log("MANUAL_MODE_CHANGE_DURING_HOLD", {
                    "entity": entity,
                    "from_mode": old_value,
                    "to_mode": value,
                    "remaining_seconds": round(remaining_seconds, 1),
                    "hold_renewed": False,
                })
        else:
            MANUAL_HOLD_UNTIL[entity] = now_ts + DEFAULTS.MANUAL_HOLD_MINUTES * 60
            write_debug_log("MANUAL_HOLD_STARTED", {
                "entity": entity,
                "from_mode": old_value,
                "to_mode": value,
                "duration_minutes": DEFAULTS.MANUAL_HOLD_MINUTES,
            })

# =============================================================================
# SHARED CENTRAL-UNIT CIRCULATION
# =============================================================================
def reconcile_central_circulation(context, selected_requests):
    """Apply shared central-unit circulation rules without calling HA services."""
    units = CENTRAL_CIRCULATION_ENTITIES
    requests = {e: {**r, "fields": dict(r["fields"])} for e, r in selected_requests.items()}
    def desired(entity):
        request = requests.get(entity)
        if request and request["fields"].get("hvac_mode") is not None:
            return request["fields"]["hvac_mode"]
        return context.zones[entity].hvac_mode
    def request_mode(entity, mode, reason, tracking=None):
        request = make_climate_request(entity, {"hvac_mode": mode, "fan_mode": "auto"}, "circulation", reason)
        if tracking:
            request["fan_only_reason"] = tracking
        requests[entity] = request
    def cancel_off(entity):
        request = requests.get(entity)
        if request and request["fields"].get("hvac_mode") == "off":
            request["fields"].pop("hvac_mode")
            if not request["fields"]:
                requests.pop(entity)

    # While Away, stop tracked circulation unless its own minimum runtime
    # has not elapsed. Holds protect only their own unit.
    if context.away_active:
        for entity in units:
            zone = context.zones[entity]
            if zone.hold_active or zone.hvac_mode != "fan_only":
                continue
            if desired(entity) not in (None, "off", "fan_only"):
                continue
            reason = FAN_ONLY_TRACKING.get(entity, {}).get("reason")
            dwell = DEFAULTS.STAGNATION_FAN_MINUTES if reason == "stagnation" else DEFAULTS.FAN_ONLY_DWELL_MINUTES if reason in ("post_cooling", "coupling") else None
            if dwell is not None and zone.idle_minutes < dwell:
                cancel_off(entity)
            else:
                request_mode(entity, "off", "Circulation cleanup: Away restriction")
        return requests

    # Label FAN_ONLY requests selected by zone policies.
    for entity in units:
        zone, request = context.zones[entity], requests.get(entity)
        if not request or zone.hvac_mode == "fan_only" or request["fields"].get("hvac_mode") != "fan_only":
            continue
        if request["policy"] == "fan":
            request["fan_only_reason"] = "stagnation"
        elif zone.hvac_mode == "cool":
            request["fan_only_reason"] = "post_cooling"

    # Capture both desired modes before applying either post-cooling conversion.
    initial = {entity: desired(entity) for entity in units}
    for entity, other in ((units[0], units[1]), (units[1], units[0])):
        zone = context.zones[entity]
        if zone.hvac_mode == "cool" and initial[entity] == "off" and initial[other] == "cool" and not zone.hold_active:
            request_mode(entity, "fan_only", "Post-cooling circulation", "post_cooling")

    # Complete tracked FAN_ONLY cycles, retaining them while the partner needs COOL.
    for entity, other in ((units[0], units[1]), (units[1], units[0])):
        zone = context.zones[entity]
        if zone.hold_active or zone.hvac_mode != "fan_only" or desired(entity) not in (None, "off", "fan_only"):
            continue
        reason = FAN_ONLY_TRACKING.get(entity, {}).get("reason")
        if reason is None:  # Unknown origins are deliberately left alone.
            continue
        other_cooling = desired(other) == "cool"
        if reason == "coupling":
            keep_running = other_cooling
        elif reason == "stagnation":
            keep_running = other_cooling or zone.idle_minutes < DEFAULTS.STAGNATION_FAN_MINUTES
        elif reason == "post_cooling":
            keep_running = other_cooling or zone.idle_minutes < DEFAULTS.FAN_ONLY_DWELL_MINUTES
        else:
            keep_running = False
        if keep_running:
            cancel_off(entity)
        else:
            request_mode(entity, "off", f"Circulation complete: {reason}")

    # An OFF central unit follows the partner's desired COOL operation.
    for entity, other in ((units[0], units[1]), (units[1], units[0])):
        zone = context.zones[entity]
        if zone.hold_active or desired(other) != "cool" or desired(entity) != "off":
            continue
        if zone.hvac_mode == "fan_only":
            cancel_off(entity)  # Do not assign a timer to an unknown FAN_ONLY origin.
        else:
            request_mode(entity, "fan_only", "COOL coupling", "coupling")
    return requests

# =============================================================================
# VRF SYSTEM-DIRECTION ARBITRATION
# =============================================================================
def _hvac_direction(mode):
    if mode == "heat":
        return "heat"
    if mode in ("cool", "dry"):
        return "cool"
    return None


def arbitrate_vrf_direction(context, selected_requests):
    """Resolve whole-system direction claims without overriding manual holds."""
    master = CFG.MASTER_CLIMATE_ENTITY
    requests = {e: {**r, "fields": dict(r["fields"])} for e, r in selected_requests.items()}
    claims = {e: _hvac_direction(r["fields"].get("hvac_mode")) for e, r in requests.items()}
    claims = {e: d for e, d in claims.items() if d is not None}
    hard = [e for e, r in requests.items() if r.get("hard_limit_override") and claims.get(e) == "cool"]
    if not claims:
        context.direction_arbitration = {"direction": MASTER_DIRECTION, "decision": "no_direction_request"}
        return requests, []

    if hard:
        direction = "cool"
    else:
        directions = set(claims.values())

        if len(directions) > 1:
            if MASTER_DIRECTION in directions:
                direction = MASTER_DIRECTION
            else:
                return requests, []
        else:
            direction = list(directions)[0]
    rejected = [e for e, d in claims.items() if d != direction]
    for entity in rejected:
        requests.pop(entity, None)
    targets = {e for e, d in claims.items() if d == direction}

    conflicts = []
    for entity, zone in context.zones.items():
        request = requests.get(entity)
        requested = request["fields"].get("hvac_mode") if request else None
        if entity not in targets and requested not in ("off", "fan_only"):
            if _hvac_direction(zone.hvac_mode) not in (None, direction):
                conflicts.append(entity)
    held = [e for e in conflicts if context.zones[e].hold_active]
    hard_override = bool(hard and direction == "cool")
    if held or (conflicts and not hard_override):
        for entity in targets:
            requests.pop(entity, None)
        context.direction_arbitration = {
            "direction": direction,
            "decision": "rejected_manual_hold" if held else "rejected_opposing_operation",
            "rejected_requests": sorted(targets),
            "conflicts": conflicts,
            "held_conflicts": held,
        }
        return requests, []

    if hard_override:
        for entity in conflicts:
            if entity != master:
                requests[entity] = make_climate_request(
                    entity, {"hvac_mode": "off"}, "direction",
                    "Hard dew-point limit: stop opposing VRF operation",
                )

    transition = []
    master_mode = context.zones[master].hvac_mode
    if master not in targets and MASTER_DIRECTION != direction:
        if context.zones[master].hold_active or master_mode == "fan_only":
            for entity in targets:
                requests.pop(entity, None)
            context.direction_arbitration = {
                "direction": direction, "decision": "rejected_master_unavailable",
                "rejected_requests": sorted(targets), "master_mode": master_mode,
            }
            return requests, []
        transition.append(make_climate_request(
            master, {"hvac_mode": direction}, "direction",
            f"Set VRF system direction to {direction.upper()}",
        ))
        transition.append(make_climate_request(
            master, {"hvac_mode": master_mode}, "direction",
            f"Restore master to its previous mode: {master_mode.upper()}",
        ))
        requests.pop(master, None)

    context.direction_arbitration = {
        "direction": direction,
        "decision": "hard_limit_override" if hard_override and conflicts else "accepted",
        "master_direction_before": MASTER_DIRECTION,
        "conflicts_stopped": [e for e in conflicts if e != master] if hard_override else [],
        "rejected_requests": rejected,
        "master_transition_required": bool(transition),
    }
    return requests, transition


# =============================================================================
# COORDINATION AND TELEMETRY
# =============================================================================
@time_trigger(
    "period(now, 5m)",
    "once(18:30:00)",
    "once(06:30:00)",
)
@state_trigger(CFG.AWAY_MODE_ENTITY)
def run_climate_coordinator():
    """Evaluate every zone, reconcile shared circulation, execute, and publish."""
    reconcile_pending_climate_commands()
    evaluation_context = EvaluationContext()
    context = evaluation_context.build()
    selected = {}
    evaluators = (evaluate_zone_humidity, evaluate_zone_temperature, evaluate_zone_fan)
    for entity, zone in context.zones.items():
        if zone.hold_active:
            continue
        for evaluator in evaluators:
            try:
                request = evaluator(zone, context)
            except Exception as err:
                log.error("Climate policy evaluation failed for %s: %s", entity, err)
                break  # A failed higher-priority evaluator must not fall through.
            if request is None:
                continue
            if request.get("entity_id") != entity:
                log.error("Invalid climate request entity: expected %s, got %s", entity, request.get("entity_id"))
                break
            selected[entity] = request
            break
    try:
        circulated = reconcile_central_circulation(context, selected)
        final, master_transition = arbitrate_vrf_direction(context, circulated)
    except Exception as err:
        log.error("Climate reconciliation failed: %s", err)
        return None

    # Stop hard-limit conflicts first, then establish master direction, then
    # run the master/requesting zone before the remaining zone actions.
    plan = []
    for entity in DEFAULTS.ZONE_CONFIG:
        request = final.get(entity)
        if (entity != CFG.MASTER_CLIMATE_ENTITY and request
                and request["fields"].get("hvac_mode") in ("off", "fan_only")
                and _hvac_direction(context.zones[entity].hvac_mode) is not None):
            plan.append(request)
    if master_transition:
        plan.extend(master_transition)
    elif CFG.MASTER_CLIMATE_ENTITY in final:
        plan.append(final[CFG.MASTER_CLIMATE_ENTITY])
    for entity in DEFAULTS.ZONE_CONFIG:
        if entity == CFG.MASTER_CLIMATE_ENTITY or entity in [a["entity_id"] for a in plan]:
            continue
        if entity in final:
            plan.append(final[entity])
    results = execute_climate_plan(plan)
    publish_climate_telemetry(context, selected, final, results)
    return {
        "timestamp": context.timestamp.isoformat(),
        "season_mode": "independent",
        "selected_requests": selected,
        "final_requests": final,
        "execution_results": results,
    }

def publish_climate_telemetry(context, selected_requests, final_requests, execution_results):
    """Publish the coordinator's snapshot, decisions, and execution outcomes."""
    zones = {}
    for entity_id, zone in context.zones.items():
        policy_thresholds = {
            key: zone.targets[key]
            for key in ("heat_on", "heat_off", "cool_on", "cool_off")
        }
        effective_thresholds = effective_temperature_thresholds(zone, context)
        zones[entity_id] = {
            **effective_thresholds,
            "policy_thresholds": policy_thresholds,
            "effective_thresholds": effective_thresholds,
            **zone.telemetry(),
        }
    payload = {
        
        "season_mode": "independent",
        "away_mode": context.away_active,
        "night_mode": context.night_active,
        "sleeptime": context.sleeptime,
        "outdoor_temp_c": context.outdoor_temp,
        "humidity_control_blocked": context.humidity_control_blocked,
        "direction_arbitration": getattr(context, "direction_arbitration", {}),
        "master_direction": MASTER_DIRECTION,
        "dry_policy": {
            "minimum_run_minutes": DEFAULTS.DRY_MODE_MIN_RUN_MINUTES,
            "maximum_run_minutes": DEFAULTS.DRY_MODE_MAX_RUN_MINUTES,
            "comfortable_dew_point_c": DEFAULTS.DEW_POINT_DRY_TRIGGER,
            "comfortable_duration_minutes": DEFAULTS.DRY_MODE_COMFORT_MINUTES,
            "immediate_stop_dew_point_c": DEFAULTS.DEW_POINT_RESTORE_TARGET,
            "cooldown_after_maximum_minutes": DEFAULTS.DRY_MODE_COOLDOWN_MINUTES,
        },
        "zones": zones,
        "selected_requests": selected_requests,
        "final_requests": final_requests,
        "execution_results": execution_results,
    }
    state.set(
        CFG.TELEMETRY_ENTITY, value="independent",
        json_payload=json.dumps(payload), 
        last_update=context.timestamp.isoformat(),
        friendly_name="Smart Climate Telemetry",
        icon="mdi:home-thermometer",
    )

# =============================================================================
# PERIODIC TELEMETRY HEARTBEAT
# =============================================================================

@time_trigger("period(now, 5m)")
def periodic_telemetry_heartbeat():
    if DEFAULTS.LOGGING_ENABLED:
        raw = get_entity_attr(CFG.TELEMETRY_ENTITY, "json_payload")
        if raw:
            try:
                write_debug_log("PERIODIC_SNAPSHOT", json.loads(raw))
            except Exception:
                pass
