"""Baseline 1: schedule-only logistic regression.

This entry point is intentionally separate so the first baseline can be run,
reviewed and cited without loading weather data. The implementation delegates
to the shared training pipeline used for the controlled baseline comparison.
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
    BASELINE_1_FILE,
    DEFAULT_FLIGHTS,
    DEFAULT_METRICS,
    DEFAULT_MODEL_DIR,
    train_and_evaluate,
)


def train(
    flights: pd.DataFrame,
    *,
    test_days: int = 30,
    threshold: float = 0.5,
) -> tuple[Pipeline, dict[str, Any]]:
    """Train and evaluate the schedule-only baseline."""
    return train_and_evaluate(flights, test_days=test_days, threshold=threshold)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flights", type=Path, default=DEFAULT_FLIGHTS)
    parser.add_argument(
        "--model-output", type=Path, default=DEFAULT_MODEL_DIR / BASELINE_1_FILE
    )
    parser.add_argument(
        "--metrics-output",
        type=Path,
        default=DEFAULT_METRICS.with_name("baseline_1_schedule.json"),
    )
    parser.add_argument("--test-days", type=int, default=30)
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    model, metrics = train(
        pd.read_parquet(args.flights),
        test_days=args.test_days,
        threshold=args.threshold,
    )
    args.model_output.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, args.model_output)
    args.metrics_output.write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
