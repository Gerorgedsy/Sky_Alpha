"""Canonical column names for every cleaned table.

The three sources describe different things and keep different shapes:

- **flights** (BTS): one scheduled flight, with what actually happened to it.
  This is the ground truth the models learn and are scored against.
- **opensky_flights** / **opensky_states** (OpenSky): what transponders saw --
  movements by airport, and aircraft positions around each hub.
- **weather** (HRRR): a forecast for a hub at a valid time, issued at an
  initialisation time.

Every timestamp is UTC and timezone-aware. BTS publishes local clock times;
they are converted once, during cleaning, and nothing downstream sees a local
time again.

Leakage control follows project 1: a value carries the time it describes and,
where the two differ, the time it became known. A forecast for 15:00 issued
at 12:00 is usable at 13:00; one issued at 14:00 is not, even though both
describe 15:00.
"""

from __future__ import annotations

# Shared
HUB = "hub"  # IATA code of the hub a row belongs to

# BTS flights
FLIGHT_DATE = "flight_date"
CARRIER = "carrier"
FLIGHT_NUMBER = "flight_number"
TAIL_NUMBER = "tail_number"
ORIGIN = "origin"
DEST = "dest"
SCHED_DEP_UTC = "sched_dep_utc"
SCHED_ARR_UTC = "sched_arr_utc"
ACTUAL_DEP_UTC = "actual_dep_utc"
ACTUAL_ARR_UTC = "actual_arr_utc"
DEP_DELAY_MIN = "dep_delay_min"
ARR_DELAY_MIN = "arr_delay_min"
TAXI_OUT_MIN = "taxi_out_min"
TAXI_IN_MIN = "taxi_in_min"
SCHED_BLOCK_MIN = "sched_block_min"
DISTANCE_MI = "distance_mi"
CANCELLED = "cancelled"
CANCELLATION_CODE = "cancellation_code"
DIVERTED = "diverted"
DEP_DELAYED_30 = "dep_delayed_30"
ARR_DELAYED_30 = "arr_delayed_30"
DELAY_CAUSES: tuple[str, ...] = (
    "carrier_delay_min",
    "weather_delay_min",
    "nas_delay_min",
    "security_delay_min",
    "late_aircraft_delay_min",
)

# OpenSky
ICAO24 = "icao24"
CALLSIGN = "callsign"
FIRST_SEEN_UTC = "first_seen_utc"
LAST_SEEN_UTC = "last_seen_utc"
DEPARTURE_HUB = "departure_hub"
ARRIVAL_HUB = "arrival_hub"
SNAPSHOT_UTC = "snapshot_utc"
POSITION_UTC = "position_utc"
LATITUDE = "latitude"
LONGITUDE = "longitude"
BARO_ALTITUDE_M = "baro_altitude_m"
ON_GROUND = "on_ground"
VELOCITY_MS = "velocity_ms"
TRUE_TRACK_DEG = "true_track_deg"
VERTICAL_RATE_MS = "vertical_rate_ms"
DISTANCE_TO_HUB_KM = "distance_to_hub_km"

# HRRR
INIT_TIME_UTC = "init_time_utc"
VALID_TIME_UTC = "valid_time_utc"
AVAILABLE_AT_UTC = "available_at_utc"
LEAD_HOURS = "lead_hours"
REFLECTIVITY_DBZ = "reflectivity_dbz"
VISIBILITY_M = "visibility_m"
GUST_MS = "gust_ms"
TEMPERATURE_C = "temperature_c"
WIND_SPEED_MS = "wind_speed_ms"
WIND_DIRECTION_DEG = "wind_direction_deg"
PRECIP_MM_H = "precip_mm_h"
CEILING_M = "ceiling_m"

# Departures or arrivals at least this late count as delayed. The project's
# success metric is accuracy on delays over 30 minutes.
DELAY_THRESHOLD_MIN = 30


def kelvin_to_celsius(values):
    return values - 273.15
