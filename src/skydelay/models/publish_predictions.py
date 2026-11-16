"""Publish delay / on-time predictions for JFK, LGA and EWR to the website.

The website reads one static JSON file, ``prototype/data/predictions.json``.
This command scores an upcoming schedule with trained baseline artifacts and
rewrites that file. Commit and push the file to update the public page.

Departures leaving JFK, LGA or EWR are scored with ``--departure-model``;
arrivals landing at them are scored with ``--arrival-model`` (train one with
``train_baseline --direction arrival``). Either model may be omitted, and the
page then shows that direction as not yet published.

Example:

    PYTHONPATH=src python -m skydelay.models.publish_predictions \
        --schedule data/live/upcoming_flights.csv \
        --departure-model artifacts/models/baseline_1_schedule.joblib \
        --arrival-model artifacts/models/baseline_1_schedule_arrival.joblib \
        --metrics artifacts/metrics/baseline_comparison.json \
        --arrival-metrics artifacts/metrics/baseline_comparison_arrival.json \
        --metrics-key baseline_1

Add ``--weather data/processed/weather.parquet`` with schedule-plus-weather
artifacts. The schedule holds one row per flight with ``carrier``,
``flight_number``, ``origin``, ``dest``, ``sched_block_min``, ``distance_mi``
and at least one of ``sched_dep_utc`` / ``sched_arr_utc``; a missing one is
derived from the other and the block time.

Each flight gets a binary call: *delayed* when the model's probability of a
30-minute-or-later delay reaches ``--threshold``, otherwise *on time*. Its
confidence is the model's probability for that call, so it is never below 50 %
at the default threshold.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from skydelay.features.schedule import build_schedule_features, direction_columns
from skydelay.features.weather import build_schedule_weather_features
from skydelay.pipeline import schema

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = PROJECT_ROOT / "prototype" / "data" / "predictions.json"
SCHEMA_VERSION = 2
NYC_AIRPORTS: dict[str, str] = {
    "JFK": "John F. Kennedy International",
    "LGA": "LaGuardia",
    "EWR": "Newark Liberty International",
}
TARGET_TEXT = {
    "departure": "Departure 30 or more minutes late",
    "arrival": "Arrival 30 or more minutes late",
}
IDENTITY_COLUMNS: tuple[str, ...] = (
    schema.CARRIER,
    schema.FLIGHT_NUMBER,
    schema.ORIGIN,
    schema.DEST,
)
SCHEDULE_COLUMNS: tuple[str, ...] = (
    *IDENTITY_COLUMNS,
    schema.SCHED_BLOCK_MIN,
    schema.DISTANCE_MI,
)
# Validation metrics copied from a training scorecard, when they are present.
VALIDATION_KEYS: tuple[str, ...] = (
    "accuracy",
    "precision",
    "recall",
    "f1",
    "brier_score",
    "pr_auc",
    "test_rows",
    "test_start",
    "test_end",
)


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def prepare_schedule(schedule: pd.DataFrame, direction: str) -> pd.DataFrame:
    """Validate an upcoming schedule and keep New York flights of a direction."""
    time_column, hub_column, target = direction_columns(direction)
    missing = sorted(set(SCHEDULE_COLUMNS).difference(schedule.columns))
    if not {schema.SCHED_DEP_UTC, schema.SCHED_ARR_UTC} & set(schedule.columns):
        missing.append(f"{schema.SCHED_DEP_UTC} or {schema.SCHED_ARR_UTC}")
    if missing:
        raise KeyError(f"Schedule is missing required columns: {missing}")
    frame = schedule.copy()
    for column in (schema.ORIGIN, schema.DEST, schema.CARRIER):
        frame[column] = frame[column].astype("string").str.upper()
    block = pd.to_timedelta(
        pd.to_numeric(frame[schema.SCHED_BLOCK_MIN], errors="coerce"), unit="min"
    )
    for column in (schema.SCHED_DEP_UTC, schema.SCHED_ARR_UTC):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    if schema.SCHED_ARR_UTC not in frame.columns:
        frame[schema.SCHED_ARR_UTC] = frame[schema.SCHED_DEP_UTC] + block
    if schema.SCHED_DEP_UTC not in frame.columns:
        frame[schema.SCHED_DEP_UTC] = frame[schema.SCHED_ARR_UTC] - block
    frame = frame.loc[
        frame[hub_column].isin(list(NYC_AIRPORTS)) & frame[time_column].notna()
    ]
    frame = frame.drop_duplicates([*IDENTITY_COLUMNS, time_column])
    # The feature builders keep labelled rows only. Upcoming flights have no
    # outcome yet, so a placeholder label lets them through; it is never read.
    frame[target] = 0
    return frame.reset_index(drop=True)


def score_schedule(
    model: Any,
    schedule: pd.DataFrame,
    *,
    direction: str = "departure",
    weather: pd.DataFrame | None = None,
    threshold: float = 0.5,
    horizon_hours: int = 2,
) -> pd.DataFrame:
    """Return one prediction row per New York departure or arrival."""
    if not 0.0 < threshold < 1.0:
        raise ValueError("threshold must be between 0 and 1")
    time_column, hub_column, _ = direction_columns(direction)
    output_columns = [*IDENTITY_COLUMNS, time_column]
    prepared = prepare_schedule(schedule, direction)
    if prepared.empty:
        return pd.DataFrame(
            columns=[*output_columns, "direction", "delay_probability", "prediction"]
        )
    # The builders sort by hub and scheduled time, so the identifying columns
    # are carried through the same sort rather than joined back.
    ordered = prepared.sort_values([hub_column, time_column], kind="stable")
    ordered = ordered.reset_index(drop=True)
    if weather is None:
        table = build_schedule_features(ordered, direction)
    else:
        table = build_schedule_weather_features(
            ordered, weather, horizon_hours=horizon_hours, direction=direction
        )
    if len(table) != len(ordered):
        raise AssertionError("Feature builder changed the number of flights")
    columns = list(getattr(model, "feature_names_in_", table.columns))
    probability = model.predict_proba(table[columns])[:, 1]
    result = ordered.loc[:, output_columns].copy()
    result["direction"] = direction
    result["delay_probability"] = probability
    result["prediction"] = [
        "delayed" if p >= threshold else "on_time" for p in probability
    ]
    return result


def flight_records(scored: pd.DataFrame) -> list[dict[str, Any]]:
    """Turn scored rows of one direction into website flight records."""
    records = []
    for row in scored.to_dict("records"):
        direction = row["direction"]
        time_column, hub_column, _ = direction_columns(direction)
        p_delay = float(row["delay_probability"])
        delayed = row["prediction"] == "delayed"
        scheduled = pd.Timestamp(row[time_column]).tz_convert("UTC")
        records.append(
            {
                "direction": direction,
                "airport": str(row[hub_column]),
                "carrier": str(row[schema.CARRIER]),
                "flight": f"{row[schema.CARRIER]}{row[schema.FLIGHT_NUMBER]}",
                "origin": str(row[schema.ORIGIN]),
                "dest": str(row[schema.DEST]),
                "scheduled_utc": scheduled.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "prediction": row["prediction"],
                "delay_probability": round(p_delay, 4),
                "confidence": round(p_delay if delayed else 1.0 - p_delay, 4),
            }
        )
    return records


def build_payload(
    scored: dict[str, pd.DataFrame],
    *,
    model_names: dict[str, str],
    threshold: float,
    horizon_hours: int,
    stale_after_minutes: int,
    validation: dict[str, dict[str, Any]] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Shape scored flights into the website's ``predictions.json`` format.

    ``scored`` maps a direction to its scored rows; a direction left out has
    no published model and is shown as such on the page.
    """
    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    validation = validation or {}
    flights: list[dict[str, Any]] = []
    for frame in scored.values():
        flights.extend(flight_records(frame))
    flights.sort(
        key=lambda item: (item["scheduled_utc"], item["direction"], item["flight"])
    )
    models = {
        direction: (
            {
                "name": model_names.get(direction, direction),
                "target": TARGET_TEXT[direction],
                "threshold": threshold,
                "horizon_hours": horizon_hours,
                "validation": validation.get(direction, {}),
            }
            if direction in scored
            else None
        )
        for direction in ("departure", "arrival")
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "published" if flights else "no_flights",
        "sample": False,
        "generated_at": generated.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stale_after_minutes": stale_after_minutes,
        "models": models,
        "airports": NYC_AIRPORTS,
        "flights": flights,
    }


def load_validation(path: Path | None, model_key: str | None) -> dict[str, Any]:
    """Read holdout metrics for the published model from a scorecard file."""
    if path is None:
        return {}
    scorecard = json.loads(path.read_text())
    if model_key:
        scorecard = scorecard[model_key]
    elif "brier_score" not in scorecard:
        raise ValueError("Scorecard holds several models; pass --metrics-key")
    return {key: scorecard[key] for key in VALIDATION_KEYS if key in scorecard}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument(
        "--departure-model", "--model", dest="departure_model", type=Path
    )
    parser.add_argument("--arrival-model", type=Path, default=None)
    parser.add_argument("--weather", type=Path, default=None)
    parser.add_argument("--metrics", type=Path, default=None)
    parser.add_argument("--arrival-metrics", type=Path, default=None)
    parser.add_argument(
        "--metrics-key",
        default=None,
        help="Model entry inside a comparison scorecard, e.g. baseline_2",
    )
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--horizon-hours", type=int, default=2)
    parser.add_argument("--stale-after-minutes", type=int, default=180)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not (args.departure_model or args.arrival_model):
        parser.error("pass --departure-model, --arrival-model or both")

    schedule = _read_table(args.schedule)
    weather = _read_table(args.weather) if args.weather else None
    sources = {
        "departure": (args.departure_model, args.metrics),
        "arrival": (args.arrival_model, args.arrival_metrics),
    }
    scored: dict[str, pd.DataFrame] = {}
    names: dict[str, str] = {}
    validation: dict[str, dict[str, Any]] = {}
    for direction, (model_path, metrics_path) in sources.items():
        if model_path is None:
            continue
        scored[direction] = score_schedule(
            joblib.load(model_path),
            schedule,
            direction=direction,
            weather=weather,
            threshold=args.threshold,
            horizon_hours=args.horizon_hours,
        )
        names[direction] = model_path.stem
        validation[direction] = load_validation(metrics_path, args.metrics_key)

    payload = build_payload(
        scored,
        model_names=names,
        threshold=args.threshold,
        horizon_hours=args.horizon_hours,
        stale_after_minutes=args.stale_after_minutes,
        validation=validation,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=1) + "\n")
    for direction, frame in scored.items():
        counts = frame["prediction"].value_counts().to_dict()
        print(f"{direction}: {len(frame)} flights {counts}")
    print(f"Wrote {len(payload['flights'])} predictions to {args.output}")


if __name__ == "__main__":
    main()
