"""Publish delay / on-time predictions for JFK, LGA and EWR to the website.

The website reads one static JSON file, ``prototype/data/predictions.json``.
This command scores an upcoming departure schedule with a trained baseline
artifact and rewrites that file. Commit and push the file to update the public
page.

Example (schedule-only model):

    PYTHONPATH=src python -m skydelay.models.publish_predictions \
        --schedule data/live/upcoming_departures.csv \
        --model artifacts/models/baseline_1_schedule.joblib \
        --metrics artifacts/metrics/baseline_comparison.json

Add ``--weather data/processed/weather.parquet`` with the schedule-plus-weather
artifact. The schedule must hold one row per departure with ``carrier``,
``flight_number``, ``origin``, ``dest``, ``sched_dep_utc``, ``sched_block_min``
and ``distance_mi``. Rows that do not depart JFK, LGA or EWR are ignored.

Each flight gets a binary call: *delayed* when the model's probability of
leaving 30 or more minutes late reaches ``--threshold``, otherwise *on time*.
Its confidence is the model's probability for that call, so it is never below
50 % at the default threshold.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from skydelay.features.schedule import TARGET, build_schedule_features
from skydelay.features.weather import build_schedule_weather_features
from skydelay.pipeline import schema

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = PROJECT_ROOT / "prototype" / "data" / "predictions.json"
SCHEMA_VERSION = 1
NYC_AIRPORTS: dict[str, str] = {
    "JFK": "John F. Kennedy International",
    "LGA": "LaGuardia",
    "EWR": "Newark Liberty International",
}
SCHEDULE_COLUMNS: tuple[str, ...] = (
    schema.CARRIER,
    schema.FLIGHT_NUMBER,
    schema.ORIGIN,
    schema.DEST,
    schema.SCHED_DEP_UTC,
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


def prepare_schedule(schedule: pd.DataFrame) -> pd.DataFrame:
    """Validate an upcoming schedule and keep New York departures."""
    missing = sorted(set(SCHEDULE_COLUMNS).difference(schedule.columns))
    if missing:
        raise KeyError(f"Schedule is missing required columns: {missing}")
    frame = schedule.copy()
    frame[schema.ORIGIN] = frame[schema.ORIGIN].astype("string").str.upper()
    frame[schema.DEST] = frame[schema.DEST].astype("string").str.upper()
    frame[schema.CARRIER] = frame[schema.CARRIER].astype("string").str.upper()
    frame[schema.SCHED_DEP_UTC] = pd.to_datetime(
        frame[schema.SCHED_DEP_UTC], utc=True, errors="coerce"
    )
    frame = frame.loc[
        frame[schema.ORIGIN].isin(list(NYC_AIRPORTS))
        & frame[schema.SCHED_DEP_UTC].notna()
    ]
    frame = frame.drop_duplicates(
        [schema.CARRIER, schema.FLIGHT_NUMBER, schema.ORIGIN, schema.SCHED_DEP_UTC]
    )
    # The feature builders keep labelled rows only. Upcoming flights have no
    # outcome yet, so a placeholder label lets them through; it is never read.
    frame[TARGET] = 0
    return frame.reset_index(drop=True)


def _features(
    schedule: pd.DataFrame, weather: pd.DataFrame | None, horizon_hours: int
) -> pd.DataFrame:
    if weather is None:
        return build_schedule_features(schedule)
    return build_schedule_weather_features(
        schedule, weather, horizon_hours=horizon_hours
    )


def score_schedule(
    model: Any,
    schedule: pd.DataFrame,
    *,
    weather: pd.DataFrame | None = None,
    threshold: float = 0.5,
    horizon_hours: int = 2,
) -> pd.DataFrame:
    """Return one prediction row per New York departure."""
    if not 0.0 < threshold < 1.0:
        raise ValueError("threshold must be between 0 and 1")
    prepared = prepare_schedule(schedule)
    if prepared.empty:
        return pd.DataFrame(
            columns=[*SCHEDULE_COLUMNS, "delay_probability", "prediction"]
        )
    # The builders sort by origin and departure time, so the identifying
    # columns are carried through the same sort rather than joined back.
    ordered = prepared.sort_values(
        [schema.ORIGIN, schema.SCHED_DEP_UTC], kind="stable"
    ).reset_index(drop=True)
    table = _features(ordered, weather, horizon_hours)
    if len(table) != len(ordered):
        raise AssertionError("Feature builder changed the number of flights")
    columns = list(getattr(model, "feature_names_in_", table.columns))
    probability = model.predict_proba(table[columns])[:, 1]
    result = ordered.loc[:, list(SCHEDULE_COLUMNS)].copy()
    result["delay_probability"] = probability
    result["prediction"] = [
        "delayed" if p >= threshold else "on_time" for p in probability
    ]
    return result


def build_payload(
    scored: pd.DataFrame,
    *,
    model_name: str,
    threshold: float,
    horizon_hours: int,
    stale_after_minutes: int,
    validation: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Shape scored flights into the website's ``predictions.json`` format."""
    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    flights = []
    for row in scored.itertuples(index=False):
        p_delay = float(row.delay_probability)
        delayed = row.prediction == "delayed"
        departure = pd.Timestamp(row.sched_dep_utc).tz_convert("UTC")
        flights.append(
            {
                "airport": str(row.origin),
                "carrier": str(row.carrier),
                "flight": f"{row.carrier}{row.flight_number}",
                "dest": str(row.dest),
                "scheduled_dep_utc": departure.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "prediction": row.prediction,
                "delay_probability": round(p_delay, 4),
                "confidence": round(p_delay if delayed else 1.0 - p_delay, 4),
            }
        )
    flights.sort(key=lambda item: (item["scheduled_dep_utc"], item["flight"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "published" if flights else "no_flights",
        "sample": False,
        "generated_at": generated.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stale_after_minutes": stale_after_minutes,
        "model": {
            "name": model_name,
            "target": "Departure 30 or more minutes late",
            "threshold": threshold,
            "horizon_hours": horizon_hours,
            "validation": validation or {},
        },
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
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--weather", type=Path, default=None)
    parser.add_argument("--metrics", type=Path, default=None)
    parser.add_argument(
        "--metrics-key",
        default=None,
        help="Model entry inside a comparison scorecard, e.g. baseline_2",
    )
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--horizon-hours", type=int, default=2)
    parser.add_argument("--stale-after-minutes", type=int, default=180)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    model = joblib.load(args.model)
    weather = _read_table(args.weather) if args.weather else None
    scored = score_schedule(
        model,
        _read_table(args.schedule),
        weather=weather,
        threshold=args.threshold,
        horizon_hours=args.horizon_hours,
    )
    payload = build_payload(
        scored,
        model_name=args.model_name or args.model.stem,
        threshold=args.threshold,
        horizon_hours=args.horizon_hours,
        stale_after_minutes=args.stale_after_minutes,
        validation=load_validation(args.metrics, args.metrics_key),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=1) + "\n")
    counts = scored["prediction"].value_counts().to_dict() if len(scored) else {}
    print(f"Wrote {len(payload['flights'])} predictions to {args.output}: {counts}")


if __name__ == "__main__":
    main()
