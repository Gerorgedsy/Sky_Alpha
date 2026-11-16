"""Weather and airport-delay context for the New York delay models.

A prediction is made at some time ``t0`` before the flight's scheduled time.
At ``t0`` two things are known besides the schedule:

- the latest weather observation (METAR) at the airport, and
- how late the airport's departures have been running in the last three
  hours.

Training rebuilds both for a random ``t0`` before every historical flight:
weather from the Iowa Environmental Mesonet METAR archive, and delays from
the BTS flights that had actually left by ``t0``. Live scoring reads them from
the website's snapshots: NOAA METARs and the AirLabs sample of recent
departures. The live delay sample is small, so training draws the same
number of flights at random rather than using every flight, and hides the
context on some rows so the model also works when a live feed is down.
"""

from __future__ import annotations

import io
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from skydelay.pipeline import schema

NYC = ("JFK", "LGA", "EWR")
IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
IEM_FIELDS = (
    "tmpf",
    "dwpf",
    "sknt",
    "gust",
    "vsby",
    "skyc1",
    "skyc2",
    "skyc3",
    "skyl1",
    "skyl2",
    "skyl3",
    "wxcodes",
)
WEATHER_COLUMNS = (
    "wx_wind_kt",
    "wx_gust_kt",
    "wx_vis_mi",
    "wx_ceiling_ft",
    "wx_precip",
    "wx_thunder",
    "wx_temp_c",
    "wx_spread_c",
)
STATE_COLUMNS = (
    "state_mean_delay",
    "state_share_30",
    "state_flights",
    "nyc_state_mean_delay",
)
CONTEXT_COLUMNS = ("horizon_h", *WEATHER_COLUMNS, *STATE_COLUMNS)
STATE_WINDOW = pd.Timedelta(hours=3)
SAMPLE_SIZE = 12
MIN_SAMPLE = 3
NO_CEILING_FT = 12_000.0
CEILING_COVERS = ("BKN", "OVC", "OVX", "VV")
PRECIP = r"RA|SN|DZ|PL|GR|GS|SG|IC|UP"
HORIZON_HOURS = (0.5, 30.0)
HIDE_STATE, HIDE_WEATHER = 0.15, 0.05
WEATHER_TOLERANCE = pd.Timedelta(hours=3)
LIVE_STATE_TOLERANCE = pd.Timedelta(hours=8)


# ------------------------------------------------------------------ weather


def download_metar(start: pd.Timestamp, end: pd.Timestamp, path: Path) -> Path:
    """Hourly and special METARs for the three airports, one month per request."""
    import requests

    frames = []
    for month in pd.period_range(
        start.tz_localize(None), end.tz_localize(None), freq="M"
    ):
        sts = max(month.start_time, start.tz_localize(None))
        ets = min((month + 1).start_time, end.tz_localize(None))
        params = [("station", s) for s in NYC] + [("data", f) for f in IEM_FIELDS]
        params += [
            ("sts", sts.strftime("%Y-%m-%dT%H:%MZ")),
            ("ets", ets.strftime("%Y-%m-%dT%H:%MZ")),
            ("tz", "Etc/UTC"),
            ("format", "onlycomma"),
            ("missing", "empty"),
            ("trace", "0.0001"),
            ("report_type", "3"),
            ("report_type", "4"),
        ]
        for attempt in range(6):
            response = requests.get(IEM_URL, params=params, timeout=300)
            if response.status_code == 200 and response.text.startswith("station"):
                frames.append(pd.read_csv(io.StringIO(response.text)))
                print(f"  METAR {month}: {len(frames[-1]):,} reports", flush=True)
                break
            time.sleep(5 * 2**attempt)
        else:
            raise RuntimeError(f"IEM METAR download failed for {month}")
    raw = pd.concat(frames, ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw.to_csv(path, index=False)
    return path


def _ceiling(covers: pd.DataFrame, bases: pd.DataFrame) -> pd.Series:
    ceiling = pd.Series(NO_CEILING_FT, index=covers.index)
    for i in range(covers.shape[1]):
        cover = covers.iloc[:, i].astype("string").str.upper()
        base = pd.to_numeric(bases.iloc[:, i], errors="coerce")
        hit = cover.isin(CEILING_COVERS).fillna(False) & base.notna()
        ceiling = ceiling.where(~hit, np.minimum(ceiling, base))
    return ceiling.clip(upper=NO_CEILING_FT)


def metar_table(raw: pd.DataFrame) -> pd.DataFrame:
    """Archive METARs as model weather columns, one row per report."""
    out = pd.DataFrame(
        {
            "hub": raw["station"].astype(str).str.upper().str.removeprefix("K"),
            "valid_utc": pd.to_datetime(raw["valid"], utc=True, errors="coerce"),
        }
    )
    num = {c: pd.to_numeric(raw.get(c), errors="coerce") for c in IEM_FIELDS[:5]}
    out["wx_wind_kt"] = num["sknt"]
    out["wx_gust_kt"] = num["gust"].fillna(num["sknt"])
    out["wx_vis_mi"] = num["vsby"].clip(0, 10)
    out["wx_ceiling_ft"] = _ceiling(
        raw[["skyc1", "skyc2", "skyc3"]], raw[["skyl1", "skyl2", "skyl3"]]
    )
    codes = raw.get("wxcodes", pd.Series("", index=raw.index)).fillna("").astype(str)
    out["wx_precip"] = codes.str.contains(PRECIP, regex=True).astype(float)
    out["wx_thunder"] = codes.str.contains("TS").astype(float)
    out["wx_temp_c"] = (num["tmpf"] - 32) * 5 / 9
    out["wx_spread_c"] = (num["tmpf"] - num["dwpf"]) * 5 / 9
    out = out.loc[out["hub"].isin(NYC) & out["valid_utc"].notna()]
    return out.sort_values(["hub", "valid_utc"]).reset_index(drop=True)


def _visibility(value: Any) -> float:
    try:
        return min(float(str(value).rstrip("+")), 10.0)
    except ValueError:
        return np.nan


def live_weather(operations: dict[str, Any]) -> pd.DataFrame:
    """The snapshot's NOAA METARs as model weather columns."""
    rows = []
    for hub, w in ((operations.get("weather") or {}).get("airports") or {}).items():
        if not w or not w.get("observedAt"):
            continue
        wind = w.get("windKt")
        temp, dew = w.get("temperatureC"), w.get("dewpointC")
        wx = str(w.get("wx") or "")
        rows.append(
            {
                "hub": hub,
                "valid_utc": pd.Timestamp(w["observedAt"]),
                "wx_wind_kt": wind,
                "wx_gust_kt": w.get("gustKt") or wind,
                "wx_vis_mi": _visibility(w.get("visibilityMi")),
                "wx_ceiling_ft": min(
                    float(w["ceilingFt"])
                    if w.get("ceilingFt") is not None
                    else NO_CEILING_FT,
                    NO_CEILING_FT,
                ),
                "wx_precip": float(bool(pd.Series([wx]).str.contains(PRECIP)[0])),
                "wx_thunder": float("TS" in wx),
                "wx_temp_c": temp,
                "wx_spread_c": None if temp is None or dew is None else temp - dew,
            }
        )
    columns = ["hub", "valid_utc", *WEATHER_COLUMNS]
    return pd.DataFrame(rows, columns=columns).astype(
        {c: float for c in WEATHER_COLUMNS}
    )


# ------------------------------------------------------------ airport delays


def _summarise(delays: np.ndarray) -> tuple[float, float, float]:
    if len(delays) < MIN_SAMPLE:
        return np.nan, np.nan, float(len(delays))
    return float(delays.mean()), float((delays >= 30).mean()), float(len(delays))


def state_table(flights: pd.DataFrame, seed: int = 0) -> pd.DataFrame:
    """Hourly departure-delay state per airport, from flights gone by each hour."""
    rng = np.random.default_rng(seed)
    rows = []
    departures = flights.loc[
        flights[schema.ORIGIN].isin(NYC)
        & flights[schema.ACTUAL_DEP_UTC].notna()
        & flights[schema.DEP_DELAY_MIN].notna()
    ]
    sched_all = pd.to_datetime(departures[schema.SCHED_DEP_UTC], utc=True)
    hours = pd.date_range(
        sched_all.min().floor("h") + STATE_WINDOW,
        sched_all.max().ceil("h"),
        freq="h",
    )
    for hub in NYC:
        part = departures.loc[departures[schema.ORIGIN] == hub]
        order = np.argsort(pd.to_datetime(part[schema.SCHED_DEP_UTC], utc=True).values)
        sched = pd.to_datetime(part[schema.SCHED_DEP_UTC], utc=True).values[order]
        actual = pd.to_datetime(part[schema.ACTUAL_DEP_UTC], utc=True).values[order]
        delay = part[schema.DEP_DELAY_MIN].to_numpy(dtype=float)[order]
        starts = np.searchsorted(sched, (hours - STATE_WINDOW).values)
        ends = np.searchsorted(sched, hours.values)
        for at, a, b in zip(hours, starts, ends, strict=True):
            gone = delay[a:b][actual[a:b] < at.to_datetime64()]
            if len(gone) > SAMPLE_SIZE:
                gone = rng.choice(gone, SAMPLE_SIZE, replace=False)
            rows.append((hub, at, *_summarise(gone)))
    table = pd.DataFrame(
        rows, columns=["hub", "at_utc", *STATE_COLUMNS[:3]]
    ).sort_values(["at_utc", "hub"])
    table["nyc_state_mean_delay"] = table.groupby("at_utc")[
        "state_mean_delay"
    ].transform("mean")
    return table.reset_index(drop=True)


def live_state(schedules: dict[str, Any]) -> pd.DataFrame:
    """Departure-delay state from the AirLabs sample of recent departures."""
    fetched = (schedules.get("directionsFetchedAt") or {}).get(
        "departures"
    ) or schedules.get("fetchedAt")
    columns = ["hub", "at_utc", *STATE_COLUMNS]
    if schedules.get("status") != "live" or not fetched:
        return pd.DataFrame(columns=columns)
    at = pd.Timestamp(fetched)
    rows = []
    for hub in NYC:
        flights = ((schedules.get("airports") or {}).get(hub) or {}).get(
            "departures"
        ) or []
        delays = []
        for f in flights:
            if f.get("status") not in {"active", "landed"} or not f.get("scheduledUtc"):
                continue
            scheduled = pd.Timestamp(f["scheduledUtc"].replace(" ", "T"), tz="UTC")
            if at - STATE_WINDOW <= scheduled < at:
                delays.append(float(f.get("delayMin") or 0))
        rows.append((hub, at, *_summarise(np.array(delays[:SAMPLE_SIZE]))))
    table = pd.DataFrame(rows, columns=columns[:5])
    table["nyc_state_mean_delay"] = table["state_mean_delay"].mean()
    return table


# ------------------------------------------------------------------- attach


def _asof(
    rows: pd.DataFrame,
    table: pd.DataFrame,
    time_column: str,
    columns: tuple[str, ...],
    tolerance: pd.Timedelta,
) -> pd.DataFrame:
    """Latest ``table`` row per hub at or before each row's ``t0``."""
    if table.empty:
        return pd.DataFrame(np.nan, index=rows.index, columns=list(columns))
    left = rows[["hub", "t0"]].assign(_row=np.arange(len(rows))).sort_values("t0")
    right = table[["hub", time_column, *columns]].sort_values(time_column)
    right = right.astype({time_column: left["t0"].dtype})
    merged = pd.merge_asof(
        left,
        right,
        left_on="t0",
        right_on=time_column,
        by="hub",
        direction="backward",
        tolerance=tolerance,
    ).sort_values("_row")
    return pd.DataFrame(
        merged[list(columns)].to_numpy(), index=rows.index, columns=list(columns)
    )


def attach(
    frame: pd.DataFrame,
    t0: pd.Series,
    weather: pd.DataFrame,
    state: pd.DataFrame,
    *,
    state_tolerance: pd.Timedelta,
) -> pd.DataFrame:
    """Context columns for ``frame`` rows predicted at times ``t0``."""
    rows = pd.DataFrame({"hub": frame["hub"].astype(str), "t0": t0}, index=frame.index)
    rows["t0"] = pd.to_datetime(rows["t0"], utc=True).astype("datetime64[ns, UTC]")
    horizon = (frame["scheduled_utc"] - rows["t0"]).dt.total_seconds() / 3600
    out = pd.DataFrame(
        {"horizon_h": horizon.clip(lower=HORIZON_HOURS[0])}, index=frame.index
    )
    out[list(WEATHER_COLUMNS)] = _asof(
        rows, weather, "valid_utc", WEATHER_COLUMNS, WEATHER_TOLERANCE
    )
    out[list(STATE_COLUMNS)] = _asof(
        rows, state, "at_utc", STATE_COLUMNS, state_tolerance
    )
    return out.astype(float)


def training_context(
    frame: pd.DataFrame, weather: pd.DataFrame, state: pd.DataFrame, seed: int = 1
) -> pd.DataFrame:
    """Context for historical rows at a random prediction time before each flight."""
    rng = np.random.default_rng(seed)
    hours = rng.uniform(*HORIZON_HOURS, size=len(frame))
    t0 = frame["scheduled_utc"] - pd.to_timedelta(hours, unit="h")
    context = attach(
        frame,
        t0.dt.floor("h"),
        weather,
        state,
        state_tolerance=pd.Timedelta(minutes=59),
    )
    context["horizon_h"] = hours
    # Live feeds can be down; teach the model to work without them.
    context.loc[rng.random(len(frame)) < HIDE_STATE, list(STATE_COLUMNS)] = np.nan
    context.loc[rng.random(len(frame)) < HIDE_WEATHER, list(WEATHER_COLUMNS)] = np.nan
    return context


def live_context(
    frame: pd.DataFrame, now: datetime, weather: pd.DataFrame, state: pd.DataFrame
) -> pd.DataFrame:
    t0 = pd.Series(pd.Timestamp(now), index=frame.index)
    return attach(frame, t0, weather, state, state_tolerance=LIVE_STATE_TOLERANCE)
