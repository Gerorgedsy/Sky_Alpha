"""Train and score the schedule-only pilot baseline.

Example:

    PYTHONPATH=src python -m skydelay.models.train_baseline \
        --flights data/processed/flights.parquet

The final ``--test-days`` of the table form the holdout.  No random split is
used because a disruption model must predict future operations from past data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from skydelay.evaluation.metrics import probability_scorecard
from skydelay.features.schedule import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    TARGET,
    build_schedule_features,
)
from skydelay.pipeline import schema

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FLIGHTS = PROJECT_ROOT / "data" / "processed" / "flights.parquet"
DEFAULT_MODEL = PROJECT_ROOT / "artifacts" / "models" / "schedule_baseline.joblib"
DEFAULT_METRICS = PROJECT_ROOT / "artifacts" / "metrics" / "schedule_baseline.json"


def build_model() -> Pipeline:
    categorical = Pipeline(
        [
            ("impute", SimpleImputer(strategy="most_frequent")),
            (
                "one_hot",
                OneHotEncoder(handle_unknown="ignore", min_frequency=5),
            ),
        ]
    )
    numeric = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]
    )
    transformer = ColumnTransformer(
        [
            ("categorical", categorical, list(CATEGORICAL_FEATURES)),
            ("numeric", numeric, list(NUMERIC_FEATURES)),
        ]
    )
    return Pipeline(
        [
            ("features", transformer),
            (
                "classifier",
                LogisticRegression(
                    class_weight="balanced", max_iter=1_000, solver="liblinear"
                ),
            ),
        ]
    )


def temporal_split(
    table: pd.DataFrame, test_days: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if test_days < 1:
        raise ValueError("test_days must be positive")
    cutoff = table[schema.SCHED_DEP_UTC].max() - pd.Timedelta(days=test_days)
    train = table.loc[table[schema.SCHED_DEP_UTC] < cutoff]
    test = table.loc[table[schema.SCHED_DEP_UTC] >= cutoff]
    if train.empty or test.empty:
        raise ValueError(
            "Temporal split produced an empty partition; use more data or fewer test days."
        )
    if train[TARGET].nunique() < 2:
        raise ValueError("Training partition must contain both target classes.")
    return train, test


def train_and_evaluate(
    flights: pd.DataFrame,
    *,
    test_days: int = 30,
    threshold: float = 0.5,
) -> tuple[Pipeline, dict[str, object]]:
    table = build_schedule_features(flights)
    train, test = temporal_split(table, test_days)
    features = [*CATEGORICAL_FEATURES, *NUMERIC_FEATURES]
    model = build_model()
    model.fit(train[features], train[TARGET])
    probability = model.predict_proba(test[features])[:, 1]
    metrics = probability_scorecard(
        test[TARGET].to_numpy(), probability, threshold=threshold
    )
    metrics.update(
        {
            "model": "schedule_only_logistic_regression",
            "train_rows": len(train),
            "test_rows": len(test),
            "train_start": train[schema.SCHED_DEP_UTC].min().isoformat(),
            "train_end": train[schema.SCHED_DEP_UTC].max().isoformat(),
            "test_start": test[schema.SCHED_DEP_UTC].min().isoformat(),
            "test_end": test[schema.SCHED_DEP_UTC].max().isoformat(),
            "feature_names": features,
        }
    )
    return model, metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flights", type=Path, default=DEFAULT_FLIGHTS)
    parser.add_argument("--model-output", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--metrics-output", type=Path, default=DEFAULT_METRICS)
    parser.add_argument("--test-days", type=int, default=30)
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    flights = pd.read_parquet(args.flights)
    model, metrics = train_and_evaluate(
        flights, test_days=args.test_days, threshold=args.threshold
    )
    args.model_output.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, args.model_output)
    args.metrics_output.write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
