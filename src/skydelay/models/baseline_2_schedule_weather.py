"""Baseline 2: schedule plus HRRR logistic regression.

The weather join accepts only HRRR forecasts published by the prediction
cutoff. The model uses the same algorithm and temporal holdout as Baseline 1,
so the comparison isolates the incremental value of weather features.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from sklearn.pipeline import Pipeline

from skydelay.models.train_baseline import (
    BASELINE_2_FILE,
    DEFAULT_FLIGHTS,
    DEFAULT_METRICS,
    DEFAULT_MODEL_DIR,
    DEFAULT_WEATHER,
    train_weather_and_evaluate,
)


def train(
    flights: pd.DataFrame,
    weather: pd.DataFrame,
    *,
    test_days: int = 30,
    threshold: float = 0.5,
    horizon_hours: int = 2,
) -> tuple[Pipeline, dict[str, Any]]:
    """Train and evaluate the schedule-plus-weather baseline."""
    return train_weather_and_evaluate(
        flights,
        weather,
        test_days=test_days,
        threshold=threshold,
        horizon_hours=horizon_hours,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flights", type=Path, default=DEFAULT_FLIGHTS)
    parser.add_argument("--weather", type=Path, default=DEFAULT_WEATHER)
    parser.add_argument(
        "--model-output", type=Path, default=DEFAULT_MODEL_DIR / BASELINE_2_FILE
    )
    parser.add_argument(
        "--metrics-output",
        type=Path,
        default=DEFAULT_METRICS.with_name("baseline_2_schedule_weather.json"),
    )
    parser.add_argument("--test-days", type=int, default=30)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--horizon-hours", type=int, default=2)
    args = parser.parse_args()

    model, metrics = train(
        pd.read_parquet(args.flights),
        pd.read_parquet(args.weather),
        test_days=args.test_days,
        threshold=args.threshold,
        horizon_hours=args.horizon_hours,
    )
    args.model_output.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, args.model_output)
    args.metrics_output.write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
