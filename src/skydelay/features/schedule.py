"""Leakage-safe schedule features for the first pilot baseline.

All columns in this table are known at least two hours before departure.  The
builder intentionally excludes actual departure time, taxi time, delay causes
and every other post-outcome field.
"""

from __future__ import annotations

import pandas as pd

from skydelay.pipeline import schema

CATEGORICAL_FEATURES: tuple[str, ...] = (
    "hub",
    "carrier",
    "route",
    "departure_hour",
    "day_of_week",
    "month",
)

NUMERIC_FEATURES: tuple[str, ...] = (
    schema.SCHED_BLOCK_MIN,
    schema.DISTANCE_MI,
    "scheduled_departures_15m",
    "scheduled_departures_30m",
    "scheduled_departures_60m",
)

TARGET = schema.DEP_DELAYED_30


def _rolling_prior_counts(group: pd.DataFrame, minutes: int) -> pd.Series:
    times = group[schema.SCHED_DEP_UTC]
    # A value of one is counted over [t-window, t), so the current departure
    # never counts itself.
    values = pd.Series(1, index=pd.DatetimeIndex(times), dtype="int64")
    counts = values.rolling(f"{minutes}min", closed="left").sum().fillna(0)
    return pd.Series(counts.to_numpy(dtype="int64"), index=group.index)


def build_schedule_features(flights: pd.DataFrame) -> pd.DataFrame:
    """Return one labeled row per departure from a configured hub."""
    required = {
        schema.SCHED_DEP_UTC,
        schema.ORIGIN,
        schema.DEST,
        schema.CARRIER,
        schema.SCHED_BLOCK_MIN,
        schema.DISTANCE_MI,
        TARGET,
    }
    missing = sorted(required.difference(flights.columns))
    if missing:
        raise KeyError(f"Flights table is missing required columns: {missing}")

    frame = flights.loc[flights[TARGET].notna()].copy()
    frame[schema.SCHED_DEP_UTC] = pd.to_datetime(
        frame[schema.SCHED_DEP_UTC], utc=True, errors="coerce"
    )
    frame = frame.loc[frame[schema.SCHED_DEP_UTC].notna()]
    frame = frame.sort_values(
        [schema.ORIGIN, schema.SCHED_DEP_UTC], kind="stable"
    ).reset_index(drop=True)
    frame["hub"] = frame[schema.ORIGIN].astype("string")
    frame["route"] = (
        frame[schema.ORIGIN].astype("string")
        + "-"
        + frame[schema.DEST].astype("string")
    )
    timestamp = frame[schema.SCHED_DEP_UTC]
    frame["departure_hour"] = timestamp.dt.hour.astype("string")
    frame["day_of_week"] = timestamp.dt.dayofweek.astype("string")
    frame["month"] = timestamp.dt.month.astype("string")

    for minutes in (15, 30, 60):
        name = f"scheduled_departures_{minutes}m"
        counts = pd.Series(0, index=frame.index, dtype="int64")
        for _, group in frame.groupby("hub", sort=False):
            counts.loc[group.index] = _rolling_prior_counts(group, minutes)
        frame[name] = counts

    frame[TARGET] = frame[TARGET].astype(bool).astype("int8")
    selected = [
        schema.SCHED_DEP_UTC,
        *CATEGORICAL_FEATURES,
        *NUMERIC_FEATURES,
        TARGET,
    ]
    return frame.loc[:, selected].reset_index(drop=True)
