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

# Each prediction direction keys on its own scheduled time, hub side and label.
# For arrivals the hub is the destination, and the ``scheduled_departures_*``
# counts hold scheduled arrivals at that hub; the names are shared so both
# directions use the same model pipeline.
DIRECTIONS: dict[str, tuple[str, str, str]] = {
    "departure": (schema.SCHED_DEP_UTC, schema.ORIGIN, schema.DEP_DELAYED_30),
    "arrival": (schema.SCHED_ARR_UTC, schema.DEST, schema.ARR_DELAYED_30),
}


def direction_columns(direction: str) -> tuple[str, str, str]:
    """Return the scheduled-time, hub and target columns for a direction."""
    try:
        return DIRECTIONS[direction]
    except KeyError:
        raise ValueError(f"direction must be one of {sorted(DIRECTIONS)}") from None


def event_time_column(table: pd.DataFrame) -> str:
    """Return the scheduled-time column of a feature table."""
    for column in (schema.SCHED_DEP_UTC, schema.SCHED_ARR_UTC):
        if column in table.columns:
            return column
    raise KeyError("Feature table has no scheduled departure or arrival time")


def target_column(table: pd.DataFrame) -> str:
    """Return the label column of a feature table."""
    for column in (schema.DEP_DELAYED_30, schema.ARR_DELAYED_30):
        if column in table.columns:
            return column
    raise KeyError("Feature table has no departure or arrival delay label")


def _rolling_prior_counts(
    group: pd.DataFrame, minutes: int, time_column: str = schema.SCHED_DEP_UTC
) -> pd.Series:
    times = group[time_column]
    # A value of one is counted over [t-window, t), so the current departure
    # never counts itself.
    values = pd.Series(1, index=pd.DatetimeIndex(times), dtype="int64")
    counts = values.rolling(f"{minutes}min", closed="left").sum().fillna(0)
    return pd.Series(counts.to_numpy(dtype="int64"), index=group.index)


def build_schedule_features(
    flights: pd.DataFrame, direction: str = "departure"
) -> pd.DataFrame:
    """Return one labeled row per departure from (or arrival at) a hub."""
    time_column, hub_column, target = direction_columns(direction)
    required = {
        time_column,
        schema.ORIGIN,
        schema.DEST,
        schema.CARRIER,
        schema.SCHED_BLOCK_MIN,
        schema.DISTANCE_MI,
        target,
    }
    missing = sorted(required.difference(flights.columns))
    if missing:
        raise KeyError(f"Flights table is missing required columns: {missing}")

    frame = flights.loc[flights[target].notna()].copy()
    frame[time_column] = pd.to_datetime(frame[time_column], utc=True, errors="coerce")
    frame = frame.loc[frame[time_column].notna()]
    frame = frame.sort_values([hub_column, time_column], kind="stable").reset_index(
        drop=True
    )
    frame["hub"] = frame[hub_column].astype("string")
    frame["route"] = (
        frame[schema.ORIGIN].astype("string")
        + "-"
        + frame[schema.DEST].astype("string")
    )
    timestamp = frame[time_column]
    frame["departure_hour"] = timestamp.dt.hour.astype("string")
    frame["day_of_week"] = timestamp.dt.dayofweek.astype("string")
    frame["month"] = timestamp.dt.month.astype("string")

    for minutes in (15, 30, 60):
        name = f"scheduled_departures_{minutes}m"
        counts = pd.Series(0, index=frame.index, dtype="int64")
        for _, group in frame.groupby("hub", sort=False):
            counts.loc[group.index] = _rolling_prior_counts(group, minutes, time_column)
        frame[name] = counts

    frame[target] = frame[target].astype(bool).astype("int8")
    selected = [
        time_column,
        *CATEGORICAL_FEATURES,
        *NUMERIC_FEATURES,
        target,
    ]
    return frame.loc[:, selected].reset_index(drop=True)
