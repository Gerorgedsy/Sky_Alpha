"""Exploratory statistics for the New York delay dataset.

Summarises the cleaned BTS flights (and the METAR history, when given) into
one JSON file: dataset size and coverage, the target's base rate overall and
by airport, local hour, month, weekday and airline, the spread of delay
minutes, and how the delay rate moves with weather and with how late the
airport has been running. The training workflow publishes it next to the
models.

    PYTHONPATH=src python -m skydelay.models.nyc_eda \
        --flights work/processed/flights.parquet --metar work/processed/metar.csv \
        --output artifacts/nyc/eda.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from skydelay.models import nyc_context as ctx
from skydelay.models.nyc_delay import DIRECTIONS, NYC, direction_frame
from skydelay.pipeline import schema

MINUTE_BINS = [-np.inf, -15, 0, 15, 30, 60, 120, np.inf]
MINUTE_LABELS = [
    "15+ early",
    "0–15 early",
    "0–15 late",
    "15–30",
    "30–60",
    "60–120",
    "120+",
]


def _rate(frame: pd.DataFrame, label: str, by: Any) -> dict[str, dict[str, float]]:
    grouped = frame.groupby(by, observed=True)[label]
    return {
        str(k): {"delay_rate": round(float(v.mean()), 4), "flights": int(v.size)}
        for k, v in grouped
    }


def summarise(
    flights: pd.DataFrame, metar: pd.DataFrame | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    dates = pd.to_datetime(flights[schema.SCHED_DEP_UTC], utc=True)
    out["dataset"] = {
        "flights": len(flights),
        "start": str(dates.min().date()),
        "end": str(dates.max().date()),
        "carriers": int(flights[schema.CARRIER].nunique()),
        "routes": int(
            (
                flights[schema.ORIGIN].astype(str)
                + "-"
                + flights[schema.DEST].astype(str)
            ).nunique()
        ),
        "cancelled_share": round(
            float(flights.get(schema.CANCELLED, pd.Series(0)).mean()), 4
        )
        if schema.CANCELLED in flights
        else None,
    }
    weather = ctx.metar_table(metar) if metar is not None else None
    state = ctx.state_table(flights)
    for direction, (_, _, _, delay_col, label_col) in DIRECTIONS.items():
        frame = direction_frame(flights, direction)
        frame = frame.loc[frame[label_col].notna() & frame[delay_col].notna()].copy()
        frame["late"] = frame[label_col].astype(bool).astype(int)
        local = frame["local"]
        minutes = frame[delay_col].astype(float)
        part: dict[str, Any] = {
            "flights": len(frame),
            "delay_rate": round(float(frame["late"].mean()), 4),
            "median_delay_min": float(minutes.median()),
            "mean_delay_min": round(float(minutes.mean()), 1),
            "p90_delay_min": float(minutes.quantile(0.9)),
            "by_airport": _rate(frame, "late", "hub"),
            "by_local_hour": _rate(frame, "late", local.dt.hour),
            "by_month": _rate(frame, "late", local.dt.strftime("%Y-%m")),
            "by_weekday": _rate(frame, "late", local.dt.day_name().str[:3]),
            "minutes_histogram": {
                str(k): int(v)
                for k, v in pd.cut(minutes, MINUTE_BINS, labels=MINUTE_LABELS)
                .value_counts(sort=False)
                .items()
            },
        }
        carriers = frame[schema.CARRIER].value_counts().index[:10]
        part["by_carrier"] = _rate(
            frame.loc[frame[schema.CARRIER].isin(carriers)], "late", schema.CARRIER
        )
        # Conditions an hour before the scheduled time, as the model would see.
        t0 = frame["scheduled_utc"] - pd.Timedelta(hours=1)
        context = ctx.attach(
            frame,
            t0.dt.floor("h"),
            weather if weather is not None else ctx.live_weather({}),
            state,
            state_tolerance=pd.Timedelta(minutes=59),
        )
        if weather is not None:
            conditions = {
                "thunderstorm": context["wx_thunder"] == 1,
                "rain_or_snow": (context["wx_precip"] == 1)
                & (context["wx_thunder"] != 1),
                "ceiling_below_1000ft": context["wx_ceiling_ft"] < 1000,
                "gusts_25kt_plus": context["wx_gust_kt"] >= 25,
                "clear_and_calm": (context["wx_precip"] == 0)
                & (context["wx_ceiling_ft"] >= 3000)
                & (context["wx_gust_kt"] < 20),
            }
            part["by_weather"] = {
                name: {
                    "delay_rate": round(float(frame.loc[m, "late"].mean()), 4),
                    "flights": int(m.sum()),
                }
                for name, m in conditions.items()
                if m.sum()
            }
        buckets = pd.cut(
            context["state_mean_delay"],
            [-np.inf, 0, 15, 30, 60, np.inf],
            labels=["early", "0–15 min", "15–30 min", "30–60 min", "60+ min"],
        )
        part["by_recent_airport_delay"] = _rate(
            frame.assign(bucket=buckets), "late", "bucket"
        )
        out[direction] = part
    out["airports"] = list(NYC)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flights", type=Path, required=True)
    parser.add_argument("--metar", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    flights = pd.read_parquet(args.flights)
    metar = pd.read_csv(args.metar, low_memory=False) if args.metar else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summarise(flights, metar), indent=1) + "\n")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
