"""Clean OpenSky airport movements and live state vectors.

**Flights.** OpenSky estimates which airport an aircraft left and reached
from where its track begins and ends, so either airport can be missing or a
non-hub. Rows are kept when at least one end is a configured hub, and the
estimate is mapped from ICAO (``KATL``) to the IATA code every other table
uses (``ATL``). The same aircraft and first-seen time appearing twice -- once
in a departure file and once in an arrival file -- is one flight.

**States.** A state vector's position can be older than the snapshot it
arrives in: OpenSky repeats the last known position until a new one comes
in. A position older than ``stale_position_seconds`` is dropped, because a
stale "on the ground" would count an aircraft that has already left. Each
remaining aircraft is assigned to the nearest hub within ``hub_radius_km``
(nearest, not every hub in range, so New York's three airports never count
the same aircraft twice).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from skydelay.pipeline import schema
from skydelay.pipeline.airports import AirportRegistry
from skydelay.pipeline.quality import ScreeningReport

FLIGHTS_SOURCE = "opensky_flights"
STATES_SOURCE = "opensky_states"

FLIGHT_COLUMNS: tuple[str, ...] = (
    schema.ICAO24,
    schema.CALLSIGN,
    schema.FIRST_SEEN_UTC,
    schema.LAST_SEEN_UTC,
    schema.DEPARTURE_HUB,
    schema.ARRIVAL_HUB,
)

STATE_COLUMNS: tuple[str, ...] = (
    schema.SNAPSHOT_UTC,
    schema.HUB,
    schema.ICAO24,
    schema.CALLSIGN,
    schema.POSITION_UTC,
    schema.LATITUDE,
    schema.LONGITUDE,
    schema.DISTANCE_TO_HUB_KM,
    schema.BARO_ALTITUDE_M,
    schema.ON_GROUND,
    schema.VELOCITY_MS,
    schema.TRUE_TRACK_DEG,
    schema.VERTICAL_RATE_MS,
)


def unix_utc(values: pd.Series) -> pd.Series:
    return pd.to_datetime(pd.to_numeric(values, errors="coerce"), unit="s", utc=True)


def clean_flights(
    raw: pd.DataFrame,
    registry: AirportRegistry,
    window: tuple[pd.Timestamp, pd.Timestamp],
    report: ScreeningReport,
) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            schema.ICAO24: raw["icao24"].astype("string").str.strip().str.lower(),
            schema.CALLSIGN: raw["callsign"].astype("string").str.strip(),
            schema.FIRST_SEEN_UTC: unix_utc(raw["firstSeen"]),
            schema.LAST_SEEN_UTC: unix_utc(raw["lastSeen"]),
            schema.DEPARTURE_HUB: registry.icao_to_iata(raw["estDepartureAirport"]),
            schema.ARRIVAL_HUB: registry.icao_to_iata(raw["estArrivalAirport"]),
        }
    )
    frame = report.drop(
        frame,
        frame[schema.ICAO24].isna() | frame[schema.FIRST_SEEN_UTC].isna(),
        FLIGHTS_SOURCE,
        "missing_identity_or_time",
    )
    frame = report.drop(
        frame,
        frame[schema.DEPARTURE_HUB].isna() & frame[schema.ARRIVAL_HUB].isna(),
        FLIGHTS_SOURCE,
        "no_hub",
    )
    start, end = window
    seen = frame[schema.FIRST_SEEN_UTC]
    frame = report.drop(
        frame, (seen < start) | (seen >= end), FLIGHTS_SOURCE, "outside_window"
    )
    frame = report.drop(
        frame,
        frame.duplicated([schema.ICAO24, schema.FIRST_SEEN_UTC], keep="first"),
        FLIGHTS_SOURCE,
        "duplicate_flight",
    )
    return (
        frame.loc[:, list(FLIGHT_COLUMNS)]
        .sort_values(schema.FIRST_SEEN_UTC, kind="stable")
        .reset_index(drop=True)
    )


def clean_states(
    raw: pd.DataFrame,
    registry: AirportRegistry,
    settings: Mapping[str, Any],
    report: ScreeningReport,
) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            schema.SNAPSHOT_UTC: unix_utc(raw["snapshot_time"]),
            schema.ICAO24: raw["icao24"].astype("string").str.strip().str.lower(),
            schema.CALLSIGN: raw["callsign"].astype("string").str.strip(),
            schema.POSITION_UTC: unix_utc(raw["time_position"]),
            schema.LATITUDE: pd.to_numeric(raw["latitude"], errors="coerce"),
            schema.LONGITUDE: pd.to_numeric(raw["longitude"], errors="coerce"),
            schema.BARO_ALTITUDE_M: pd.to_numeric(
                raw["baro_altitude"], errors="coerce"
            ),
            schema.ON_GROUND: raw["on_ground"]
            .astype("string")
            .str.lower()
            .map({"true": True, "false": False})
            .astype("boolean"),
            schema.VELOCITY_MS: pd.to_numeric(raw["velocity"], errors="coerce"),
            schema.TRUE_TRACK_DEG: pd.to_numeric(raw["true_track"], errors="coerce"),
            schema.VERTICAL_RATE_MS: pd.to_numeric(
                raw["vertical_rate"], errors="coerce"
            ),
        }
    )
    frame = report.drop(
        frame,
        frame[schema.LATITUDE].isna()
        | frame[schema.LONGITUDE].isna()
        | frame[schema.POSITION_UTC].isna(),
        STATES_SOURCE,
        "no_position",
    )
    age = (frame[schema.SNAPSHOT_UTC] - frame[schema.POSITION_UTC]).dt.total_seconds()
    frame = report.drop(
        frame,
        age > float(settings["stale_position_seconds"]),
        STATES_SOURCE,
        "stale_position",
    )

    hub, distance = registry.nearest_hub(
        frame[schema.LATITUDE],
        frame[schema.LONGITUDE],
        float(settings["hub_radius_km"]),
    )
    frame[schema.HUB] = hub
    frame[schema.DISTANCE_TO_HUB_KM] = distance
    frame = report.drop(
        frame, frame[schema.HUB].isna(), STATES_SOURCE, "not_near_a_hub"
    )
    frame = report.drop(
        frame,
        frame.duplicated([schema.SNAPSHOT_UTC, schema.ICAO24], keep="last"),
        STATES_SOURCE,
        "duplicate_aircraft_in_snapshot",
    )
    frame = report.outside(frame, schema.VELOCITY_MS, 0, 400, STATES_SOURCE)
    return (
        frame.loc[:, list(STATE_COLUMNS)]
        .sort_values([schema.SNAPSHOT_UTC, schema.HUB], kind="stable")
        .reset_index(drop=True)
    )


def load_flights(raw_dir: Path) -> pd.DataFrame:
    paths = sorted((raw_dir / FLIGHTS_SOURCE).glob("*.csv"))
    if not paths:
        raise FileNotFoundError(f"No OpenSky flights under {raw_dir / FLIGHTS_SOURCE}.")
    return pd.concat((pd.read_csv(p, dtype="string") for p in paths), ignore_index=True)


def load_states(raw_dir: Path) -> pd.DataFrame:
    paths = sorted((raw_dir / STATES_SOURCE).glob("*/states_*.csv"))
    if not paths:
        raise FileNotFoundError(
            f"No OpenSky snapshots under {raw_dir / STATES_SOURCE}; "
            "run ingest --source opensky-states first."
        )
    return pd.concat((pd.read_csv(p, dtype="string") for p in paths), ignore_index=True)
