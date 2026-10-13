"""Point-in-time HRRR features for the schedule-plus-weather baseline.

For a departure scheduled at time ``t``, the prediction cutoff is ``t`` minus
two hours.  A weather row is eligible only when ``available_at_utc`` is at or
before that cutoff.  Among eligible forecasts, the join selects the newest run
whose valid hour is closest to the scheduled departure.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from skydelay.features.schedule import build_schedule_features
from skydelay.pipeline import schema
from skydelay.pipeline.clean_hrrr import CEILING_UNLIMITED

PREDICTION_CUTOFF = "prediction_cutoff_utc"
WEATHER_AVAILABLE = "weather_available"
WEATHER_AGE_HOURS = "weather_age_hours"
WEATHER_LEAD_HOURS = "weather_forecast_lead_hours"
WIND_DIRECTION_SIN = "wind_direction_sin"
WIND_DIRECTION_COS = "wind_direction_cos"

WEATHER_SOURCE_FEATURES: tuple[str, ...] = (
    schema.REFLECTIVITY_DBZ,
    schema.VISIBILITY_M,
    schema.GUST_MS,
    schema.TEMPERATURE_C,
    schema.WIND_SPEED_MS,
    schema.WIND_DIRECTION_DEG,
    schema.PRECIP_MM_H,
    schema.CEILING_M,
    CEILING_UNLIMITED,
)

WEATHER_NUMERIC_FEATURES: tuple[str, ...] = (
    schema.REFLECTIVITY_DBZ,
    schema.VISIBILITY_M,
    schema.GUST_MS,
    schema.TEMPERATURE_C,
    schema.WIND_SPEED_MS,
    WIND_DIRECTION_SIN,
    WIND_DIRECTION_COS,
    schema.PRECIP_MM_H,
    schema.CEILING_M,
    CEILING_UNLIMITED,
    WEATHER_AGE_HOURS,
    WEATHER_LEAD_HOURS,
    WEATHER_AVAILABLE,
)


def _utc(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="coerce")


def build_schedule_weather_features(
    flights: pd.DataFrame,
    weather: pd.DataFrame,
    *,
    horizon_hours: int = 2,
    valid_time_tolerance: str | pd.Timedelta = "90min",
) -> pd.DataFrame:
    """Return the schedule feature table with point-in-time HRRR features."""
    if horizon_hours < 1:
        raise ValueError("horizon_hours must be positive")
    tolerance = pd.Timedelta(valid_time_tolerance)
    if tolerance <= pd.Timedelta(0):
        raise ValueError("valid_time_tolerance must be positive")

    required = {
        schema.HUB,
        schema.INIT_TIME_UTC,
        schema.VALID_TIME_UTC,
        schema.AVAILABLE_AT_UTC,
        *WEATHER_SOURCE_FEATURES,
    }
    missing = sorted(required.difference(weather.columns))
    if missing:
        raise KeyError(f"Weather table is missing required columns: {missing}")

    table = build_schedule_features(flights)
    table[PREDICTION_CUTOFF] = table[schema.SCHED_DEP_UTC] - pd.Timedelta(
        hours=horizon_hours
    )
    table["_flight_id"] = np.arange(len(table), dtype="int64")

    forecast = weather.loc[:, sorted(required)].copy()
    for column in (
        schema.INIT_TIME_UTC,
        schema.VALID_TIME_UTC,
        schema.AVAILABLE_AT_UTC,
    ):
        forecast[column] = _utc(forecast[column])
    forecast = forecast.dropna(
        subset=[
            schema.HUB,
            schema.INIT_TIME_UTC,
            schema.VALID_TIME_UTC,
            schema.AVAILABLE_AT_UTC,
        ]
    )

    # HRRR fields are valid on clock hours.  Three candidate hours cover the
    # nearest available valid time even when the configured lead set skips an
    # hour.  The tolerance below rejects candidates that are too far away.
    target_hour = table[schema.SCHED_DEP_UTC].dt.round("h")
    candidates = pd.concat(
        [
            table.loc[
                :,
                ["_flight_id", "hub", schema.SCHED_DEP_UTC, PREDICTION_CUTOFF],
            ].assign(_candidate_valid_utc=target_hour + pd.Timedelta(hours=offset))
            for offset in (-1, 0, 1)
        ],
        ignore_index=True,
    )
    joined = candidates.merge(
        forecast,
        how="inner",
        left_on=["hub", "_candidate_valid_utc"],
        right_on=[schema.HUB, schema.VALID_TIME_UTC],
        validate="many_to_many",
    )
    joined["_valid_gap"] = (
        joined[schema.VALID_TIME_UTC] - joined[schema.SCHED_DEP_UTC]
    ).abs()
    eligible = joined.loc[
        (joined[schema.AVAILABLE_AT_UTC] <= joined[PREDICTION_CUTOFF])
        & (joined["_valid_gap"] <= tolerance)
    ].copy()
    eligible = eligible.sort_values(
        ["_flight_id", "_valid_gap", schema.AVAILABLE_AT_UTC, schema.INIT_TIME_UTC],
        ascending=[True, True, False, False],
        kind="stable",
    )
    best = eligible.drop_duplicates("_flight_id", keep="first")

    joined_columns = [
        "_flight_id",
        schema.INIT_TIME_UTC,
        schema.VALID_TIME_UTC,
        schema.AVAILABLE_AT_UTC,
        *WEATHER_SOURCE_FEATURES,
    ]
    table = table.merge(
        best.loc[:, joined_columns],
        how="left",
        on="_flight_id",
        validate="one_to_one",
    )
    table[WEATHER_AVAILABLE] = table[schema.AVAILABLE_AT_UTC].notna().astype("int8")
    table[WEATHER_AGE_HOURS] = (
        table[PREDICTION_CUTOFF] - table[schema.INIT_TIME_UTC]
    ).dt.total_seconds() / 3600.0
    table[WEATHER_LEAD_HOURS] = (
        table[schema.VALID_TIME_UTC] - table[schema.INIT_TIME_UTC]
    ).dt.total_seconds() / 3600.0

    radians = np.deg2rad(table[schema.WIND_DIRECTION_DEG])
    table[WIND_DIRECTION_SIN] = np.sin(radians)
    table[WIND_DIRECTION_COS] = np.cos(radians)
    table[CEILING_UNLIMITED] = table[CEILING_UNLIMITED].astype("float64")

    leaked = table[schema.AVAILABLE_AT_UTC] > table[PREDICTION_CUTOFF]
    if leaked.fillna(False).any():
        raise AssertionError("Weather join selected a forecast unavailable at cutoff")
    return table.drop(columns=["_flight_id", schema.WIND_DIRECTION_DEG])
