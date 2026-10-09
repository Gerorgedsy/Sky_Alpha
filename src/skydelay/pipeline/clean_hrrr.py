"""Turn raw HRRR point values into a hub weather panel in usable units.

Raw files keep HRRR's own names and units, as project 1 kept GFS's: kelvin
stays kelvin and wind stays two components until here. Cleaning converts
them once:

- ``TMP`` kelvin -> degrees Celsius;
- ``UGRD``/``VGRD`` -> wind speed and the direction it blows *from*, the
  convention a runway forecast uses;
- ``PRATE`` kg m-2 s-1 -> millimetres per hour (a kilogram of water over a
  square metre is a millimetre deep);
- ``HGT`` at cloud ceiling, metres. With no ceiling HRRR writes a very large
  height; anything above 20 km is "unlimited", recorded as a flag with the
  height left missing, so a clear sky is never averaged as a 99 km cloud.

``REFC`` (composite reflectivity, dBZ) is kept as published. Values at or
below zero mean no echo; thunderstorms start around 40 dBZ.

Each row gets ``available_at_utc``: the run's start plus the configured
publication lag. A feature for a forecast made at time *t* may only use rows
whose ``available_at_utc`` is at or before *t*.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from skydelay.pipeline import schema
from skydelay.pipeline.quality import ScreeningReport

SOURCE = "hrrr"
UNLIMITED_CEILING_M = 20_000.0
CEILING_UNLIMITED = "ceiling_unlimited"

OUTPUT_COLUMNS: tuple[str, ...] = (
    schema.HUB,
    schema.INIT_TIME_UTC,
    schema.LEAD_HOURS,
    schema.VALID_TIME_UTC,
    schema.AVAILABLE_AT_UTC,
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

# Physically possible bounds; values outside them are masked as faults.
RANGES: Mapping[str, tuple[float, float]] = {
    schema.REFLECTIVITY_DBZ: (-40.0, 90.0),
    schema.VISIBILITY_M: (0.0, 100_000.0),
    schema.GUST_MS: (0.0, 120.0),
    schema.TEMPERATURE_C: (-60.0, 60.0),
    schema.WIND_SPEED_MS: (0.0, 120.0),
    schema.PRECIP_MM_H: (0.0, 500.0),
}


def wind_from_components(u: pd.Series, v: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Speed, and meteorological direction (where the wind comes from)."""
    speed = np.hypot(u, v)
    direction = (270.0 - np.degrees(np.arctan2(v, u))) % 360.0
    return speed, direction.where(speed > 0)


def numeric(raw: pd.DataFrame, column: str) -> pd.Series:
    if column not in raw.columns:
        return pd.Series(np.nan, index=raw.index)
    return pd.to_numeric(raw[column], errors="coerce")


def clean(
    raw: pd.DataFrame,
    settings: Mapping[str, Any],
    window: tuple[pd.Timestamp, pd.Timestamp],
    report: ScreeningReport,
) -> pd.DataFrame:
    init = pd.to_datetime(raw["init_time"], utc=True, errors="coerce")
    lead = pd.to_numeric(raw["lead_hours"], errors="coerce")
    speed, direction = wind_from_components(numeric(raw, "UGRD"), numeric(raw, "VGRD"))
    ceiling = numeric(raw, "HGT")

    frame = pd.DataFrame(
        {
            schema.HUB: raw["hub"].astype("string"),
            schema.INIT_TIME_UTC: init,
            schema.LEAD_HOURS: lead.astype("Int64"),
            schema.VALID_TIME_UTC: init + pd.to_timedelta(lead, unit="h"),
            schema.AVAILABLE_AT_UTC: init
            + pd.Timedelta(minutes=float(settings["available_after_minutes"])),
            schema.REFLECTIVITY_DBZ: numeric(raw, "REFC"),
            schema.VISIBILITY_M: numeric(raw, "VIS"),
            schema.GUST_MS: numeric(raw, "GUST"),
            schema.TEMPERATURE_C: schema.kelvin_to_celsius(numeric(raw, "TMP")),
            schema.WIND_SPEED_MS: speed,
            schema.WIND_DIRECTION_DEG: direction,
            schema.PRECIP_MM_H: numeric(raw, "PRATE") * 3600.0,
            schema.CEILING_M: ceiling.where(ceiling <= UNLIMITED_CEILING_M),
            CEILING_UNLIMITED: (ceiling > UNLIMITED_CEILING_M),
        }
    )
    frame = report.drop(
        frame,
        frame[schema.INIT_TIME_UTC].isna()
        | frame[schema.LEAD_HOURS].isna()
        | frame[schema.HUB].isna(),
        SOURCE,
        "missing_identity",
    )
    start, end = window
    valid = frame[schema.VALID_TIME_UTC]
    frame = report.drop(
        frame, (valid < start) | (valid >= end), SOURCE, "outside_window"
    )
    for column, (low, high) in RANGES.items():
        frame = report.outside(frame, column, low, high, SOURCE)
    frame = report.drop(
        frame,
        frame.duplicated(
            [schema.HUB, schema.INIT_TIME_UTC, schema.LEAD_HOURS], keep="last"
        ),
        SOURCE,
        "duplicate_forecast",
    )
    return (
        frame.loc[:, list(OUTPUT_COLUMNS)]
        .sort_values(
            [schema.HUB, schema.INIT_TIME_UTC, schema.LEAD_HOURS], kind="stable"
        )
        .reset_index(drop=True)
    )


def load_raw(raw_dir: Path) -> pd.DataFrame:
    paths = sorted((raw_dir / SOURCE).glob("hrrr_*.csv"))
    if not paths:
        raise FileNotFoundError(
            f"No HRRR cycles under {raw_dir / SOURCE}; run ingest --source hrrr."
        )
    return pd.concat((pd.read_csv(p, dtype="string") for p in paths), ignore_index=True)
