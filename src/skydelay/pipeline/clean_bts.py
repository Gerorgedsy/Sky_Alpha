"""Turn raw BTS months into one table of hub flights on UTC time.

The hard part is time. BTS gives each clock time as local ``hhmm`` at the
airport where it happened, with ``FlightDate`` the local departure date.
Joining that to ADS-B positions and HRRR forecasts, which are UTC, means
converting every flight once, carefully:

1. **Scheduled departure.** When the origin is a hub, its timezone is known
   and the conversion is exact. ``2400`` means midnight at the end of the
   day and is read as minute 1440, not rejected.
2. **When only the destination is a hub,** the origin's timezone is not in
   the config. The scheduled arrival is local at the hub, but its *date* is
   uncertain: a red-eye lands the next day. Both candidate dates are tried,
   and the one whose implied departure (arrival minus scheduled block time)
   lies nearest the stated local departure is kept. US timezones differ by
   at most six hours and the wrong date by about a day, so the choice is not
   close.
3. **Actual times** are scheduled times plus the published delay minutes.
   Reading ``DepTime`` directly would misdate every flight that slipped past
   midnight; adding minutes cannot.

Times that do not exist or are ambiguous locally -- the skipped and repeated
hours at daylight-saving changes -- are dropped and counted rather than
guessed.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from skydelay.pipeline import schema
from skydelay.pipeline.airports import AirportRegistry
from skydelay.pipeline.quality import ScreeningReport

SOURCE = "bts"

RENAMES = {
    "Reporting_Airline": schema.CARRIER,
    "Tail_Number": schema.TAIL_NUMBER,
    "Flight_Number_Reporting_Airline": schema.FLIGHT_NUMBER,
    "Origin": schema.ORIGIN,
    "Dest": schema.DEST,
    "DepDelay": schema.DEP_DELAY_MIN,
    "ArrDelay": schema.ARR_DELAY_MIN,
    "TaxiOut": schema.TAXI_OUT_MIN,
    "TaxiIn": schema.TAXI_IN_MIN,
    "CRSElapsedTime": schema.SCHED_BLOCK_MIN,
    "Distance": schema.DISTANCE_MI,
    "CancellationCode": schema.CANCELLATION_CODE,
    "CarrierDelay": "carrier_delay_min",
    "WeatherDelay": "weather_delay_min",
    "NASDelay": "nas_delay_min",
    "SecurityDelay": "security_delay_min",
    "LateAircraftDelay": "late_aircraft_delay_min",
}

OUTPUT_COLUMNS: tuple[str, ...] = (
    schema.FLIGHT_DATE,
    schema.CARRIER,
    schema.FLIGHT_NUMBER,
    schema.TAIL_NUMBER,
    schema.ORIGIN,
    schema.DEST,
    schema.SCHED_DEP_UTC,
    schema.SCHED_ARR_UTC,
    schema.ACTUAL_DEP_UTC,
    schema.ACTUAL_ARR_UTC,
    schema.DEP_DELAY_MIN,
    schema.ARR_DELAY_MIN,
    schema.DEP_DELAYED_30,
    schema.ARR_DELAYED_30,
    schema.TAXI_OUT_MIN,
    schema.TAXI_IN_MIN,
    schema.SCHED_BLOCK_MIN,
    schema.DISTANCE_MI,
    schema.CANCELLED,
    schema.CANCELLATION_CODE,
    schema.DIVERTED,
    *schema.DELAY_CAUSES,
)

KEY: tuple[str, ...] = (
    schema.FLIGHT_DATE,
    schema.CARRIER,
    schema.FLIGHT_NUMBER,
    schema.ORIGIN,
)

# Taxi times beyond four hours are recording faults, not congestion.
MAX_TAXI_MIN = 240


def hhmm_minutes(values: pd.Series) -> pd.Series:
    """``hhmm`` clock strings as minutes after local midnight.

    Accepts unpadded values (``545``) and values read as floats (``545.0``).
    ``2400`` is 1440 minutes: midnight at the end of the day.
    """
    text = values.astype("string").str.strip().str.replace(r"\.0+$", "", regex=True)
    digits = pd.to_numeric(text, errors="coerce")
    hours, minutes = digits // 100, digits % 100
    valid = digits.notna() & (hours <= 24) & (minutes < 60) & (digits <= 2400)
    return (hours * 60 + minutes).where(valid)


def localize(local: pd.Series, zones: pd.Series) -> pd.Series:
    """Convert naive local times to UTC, one timezone at a time.

    Non-existent and ambiguous local times become missing.
    """
    result = pd.Series(pd.NaT, index=local.index, dtype="datetime64[ns, UTC]")
    for zone, rows in local.groupby(zones, dropna=True).groups.items():
        converted = (
            local.loc[rows]
            .dt.tz_localize(zone, ambiguous="NaT", nonexistent="NaT")
            .dt.tz_convert("UTC")
        )
        result.loc[rows] = converted
    return result


def scheduled_times(
    frame: pd.DataFrame, registry: AirportRegistry
) -> tuple[pd.Series, pd.Series]:
    """Scheduled departure and arrival in UTC for every hub flight."""
    zones = registry.timezones()
    date = pd.to_datetime(frame["FlightDate"], errors="coerce")
    dep_min = hhmm_minutes(frame["CRSDepTime"])
    arr_min = hhmm_minutes(frame["CRSArrTime"])
    block = pd.to_timedelta(
        pd.to_numeric(frame[schema.SCHED_BLOCK_MIN], errors="coerce"), unit="min"
    )

    dep_local = date + pd.to_timedelta(dep_min, unit="min")
    origin_zone = frame[schema.ORIGIN].map(zones)
    sched_dep = localize(dep_local, origin_zone)

    # Destination-only hubs: pick the arrival date whose implied departure is
    # closest to the stated departure clock read in the destination's zone.
    dest_only = origin_zone.isna() & frame[schema.DEST].isin(registry.iata_codes)
    if dest_only.any():
        dest_zone = frame.loc[dest_only, schema.DEST].map(zones)
        arr_same = date[dest_only] + pd.to_timedelta(arr_min[dest_only], unit="min")
        candidates = [
            localize(arr_same + pd.Timedelta(days=shift), dest_zone) for shift in (0, 1)
        ]
        reference = localize(dep_local[dest_only], dest_zone)
        gaps = [
            ((c - block[dest_only]) - reference).abs().dt.total_seconds().fillna(np.inf)
            for c in candidates
        ]
        arrival = candidates[0].where(gaps[0] <= gaps[1], candidates[1])
        sched_dep.loc[dest_only] = arrival - block[dest_only]

    sched_arr = sched_dep + block
    return sched_dep, sched_arr


def flag(values: pd.Series) -> pd.Series:
    return pd.to_numeric(values, errors="coerce").fillna(0).astype(bool)


def clean(
    raw: pd.DataFrame,
    registry: AirportRegistry,
    window: tuple[pd.Timestamp, pd.Timestamp],
    report: ScreeningReport,
) -> pd.DataFrame:
    frame = raw.rename(columns=RENAMES).copy()
    for column in (
        schema.CARRIER,
        schema.FLIGHT_NUMBER,
        schema.TAIL_NUMBER,
        schema.ORIGIN,
        schema.DEST,
    ):
        frame[column] = frame[column].astype("string").str.strip()

    hubs = registry.iata_codes
    frame = report.drop(
        frame,
        ~(frame[schema.ORIGIN].isin(hubs) | frame[schema.DEST].isin(hubs)),
        SOURCE,
        "no_hub",
    )
    frame[schema.FLIGHT_DATE] = pd.to_datetime(
        frame["FlightDate"], errors="coerce"
    ).dt.date
    frame = report.drop(
        frame,
        pd.to_numeric(frame[schema.SCHED_BLOCK_MIN], errors="coerce").fillna(0) <= 0,
        SOURCE,
        "no_scheduled_block_time",
    )

    sched_dep, sched_arr = scheduled_times(frame, registry)
    frame[schema.SCHED_DEP_UTC] = sched_dep
    frame[schema.SCHED_ARR_UTC] = sched_arr
    frame = report.drop(
        frame, frame[schema.SCHED_DEP_UTC].isna(), SOURCE, "unresolvable_schedule_time"
    )

    start, end = window
    in_window = (frame[schema.SCHED_DEP_UTC] >= start) & (
        frame[schema.SCHED_DEP_UTC] < end
    )
    frame = report.drop(frame, ~in_window, SOURCE, "outside_window")

    frame[schema.CANCELLED] = flag(frame["Cancelled"])
    frame[schema.DIVERTED] = flag(frame["Diverted"])
    numeric = [
        schema.DEP_DELAY_MIN,
        schema.ARR_DELAY_MIN,
        schema.TAXI_OUT_MIN,
        schema.TAXI_IN_MIN,
        schema.SCHED_BLOCK_MIN,
        schema.DISTANCE_MI,
        *schema.DELAY_CAUSES,
    ]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    for column in (schema.TAXI_OUT_MIN, schema.TAXI_IN_MIN):
        frame = report.outside(frame, column, 0, MAX_TAXI_MIN, SOURCE)

    frame[schema.ACTUAL_DEP_UTC] = frame[schema.SCHED_DEP_UTC] + pd.to_timedelta(
        frame[schema.DEP_DELAY_MIN], unit="min"
    )
    frame[schema.ACTUAL_ARR_UTC] = frame[schema.SCHED_ARR_UTC] + pd.to_timedelta(
        frame[schema.ARR_DELAY_MIN], unit="min"
    )

    # Labels are missing, not False, where the delay is unknown: a cancelled
    # flight was not "on time", and treating it so would flatter any model.
    for delay, label in (
        (schema.DEP_DELAY_MIN, schema.DEP_DELAYED_30),
        (schema.ARR_DELAY_MIN, schema.ARR_DELAYED_30),
    ):
        frame[label] = (
            (frame[delay] >= schema.DELAY_THRESHOLD_MIN)
            .astype("boolean")
            .mask(frame[delay].isna())
        )

    duplicated = frame.duplicated(list(KEY), keep="last")
    frame = report.drop(frame, duplicated, SOURCE, "duplicate_flight")
    return (
        frame.loc[:, list(OUTPUT_COLUMNS)]
        .sort_values(schema.SCHED_DEP_UTC, kind="stable")
        .reset_index(drop=True)
    )


def load_raw(raw_dir: Path) -> pd.DataFrame:
    paths = sorted((raw_dir / SOURCE).glob("ontime_*.csv"))
    if not paths:
        raise FileNotFoundError(
            f"No BTS months under {raw_dir / SOURCE}; run ingest --source bts."
        )
    return pd.concat((pd.read_csv(p, dtype="string") for p in paths), ignore_index=True)
