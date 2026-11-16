"""Train and compare the two pilot baselines.

Baseline 1 uses schedule information only. Baseline 2 uses the same rows and
adds the newest HRRR forecast available at the two-hour prediction cutoff.
Both models use a future holdout rather than a random split.

Example:

    PYTHONPATH=src python -m skydelay.models.train_baseline \
        --baseline both \
        --flights data/processed/flights.parquet \
        --weather data/processed/weather.parquet

Add ``--direction arrival`` to train the arrival models instead: they predict
an arrival at a hub landing 30 or more minutes late and are saved with an
``_arrival`` suffix.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

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
    build_schedule_features,
    event_time_column,
    target_column,
)
from skydelay.features.weather import (
    WEATHER_AVAILABLE,
    WEATHER_NUMERIC_FEATURES,
    build_schedule_weather_features,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FLIGHTS = PROJECT_ROOT / "data" / "processed" / "flights.parquet"
DEFAULT_WEATHER = PROJECT_ROOT / "data" / "processed" / "weather.parquet"
DEFAULT_MODEL_DIR = PROJECT_ROOT / "artifacts" / "models"
DEFAULT_METRICS = PROJECT_ROOT / "artifacts" / "metrics" / "baseline_comparison.json"
BASELINE_1_FILE = "baseline_1_schedule.joblib"
BASELINE_2_FILE = "baseline_2_schedule_weather.joblib"


def artifact_name(name: str, direction: str) -> str:
    """Suffix a model or metrics file name for arrival models."""
    if direction == "departure":
        return name
    stem, dot, suffix = name.partition(".")
    return f"{stem}_{direction}{dot}{suffix}"


def build_model(
    numeric_features: tuple[str, ...] = NUMERIC_FEATURES,
) -> Pipeline:
    """Build the shared logistic-regression pipeline."""
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
            ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scale", StandardScaler()),
        ]
    )
    transformer = ColumnTransformer(
        [
            ("categorical", categorical, list(CATEGORICAL_FEATURES)),
            ("numeric", numeric, list(numeric_features)),
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
    """Keep the latest ``test_days`` as an out-of-time holdout."""
    if test_days < 1:
        raise ValueError("test_days must be positive")
    time = event_time_column(table)
    cutoff = table[time].max() - pd.Timedelta(days=test_days)
    train = table.loc[table[time] < cutoff]
    test = table.loc[table[time] >= cutoff]
    if train.empty or test.empty:
        raise ValueError(
            "Temporal split produced an empty partition; use more data or fewer test days."
        )
    if train[target_column(table)].nunique() < 2:
        raise ValueError("Training partition must contain both target classes.")
    return train, test


def _fit_and_score(
    table: pd.DataFrame,
    *,
    numeric_features: tuple[str, ...],
    model_name: str,
    test_days: int,
    threshold: float,
) -> tuple[Pipeline, dict[str, Any]]:
    train, test = temporal_split(table, test_days)
    target = target_column(table)
    time = event_time_column(table)
    features = [*CATEGORICAL_FEATURES, *numeric_features]
    model = build_model(numeric_features)
    model.fit(train[features], train[target])
    probability = model.predict_proba(test[features])[:, 1]
    train_prevalence = float(train[target].mean())
    metrics = probability_scorecard(
        test[target].to_numpy(),
        probability,
        threshold=threshold,
        naive_probability=train_prevalence,
    )
    metrics.update(
        {
            "model": model_name,
            "train_rows": len(train),
            "test_rows": len(test),
            "target": target,
            "train_start": train[time].min().isoformat(),
            "train_end": train[time].max().isoformat(),
            "test_start": test[time].min().isoformat(),
            "test_end": test[time].max().isoformat(),
            "feature_names": features,
        }
    )
    return model, metrics


def train_and_evaluate(
    flights: pd.DataFrame,
    *,
    test_days: int = 30,
    threshold: float = 0.5,
    direction: str = "departure",
) -> tuple[Pipeline, dict[str, Any]]:
    """Train Baseline 1, the schedule-only logistic regression."""
    return _fit_and_score(
        build_schedule_features(flights, direction),
        numeric_features=NUMERIC_FEATURES,
        model_name="baseline_1_schedule_logistic_regression",
        test_days=test_days,
        threshold=threshold,
    )


def train_weather_and_evaluate(
    flights: pd.DataFrame,
    weather: pd.DataFrame,
    *,
    test_days: int = 30,
    threshold: float = 0.5,
    horizon_hours: int = 2,
    direction: str = "departure",
) -> tuple[Pipeline, dict[str, Any]]:
    """Train Baseline 2 using schedule and point-in-time HRRR features."""
    table = build_schedule_weather_features(
        flights, weather, horizon_hours=horizon_hours, direction=direction
    )
    if not table[WEATHER_AVAILABLE].any():
        raise ValueError("No HRRR row was available at any prediction cutoff.")
    model, metrics = _fit_and_score(
        table,
        numeric_features=(*NUMERIC_FEATURES, *WEATHER_NUMERIC_FEATURES),
        model_name="baseline_2_schedule_weather_logistic_regression",
        test_days=test_days,
        threshold=threshold,
    )
    train, test = temporal_split(table, test_days)
    metrics["train_weather_coverage"] = float(train[WEATHER_AVAILABLE].mean())
    metrics["test_weather_coverage"] = float(test[WEATHER_AVAILABLE].mean())
    metrics["prediction_horizon_hours"] = horizon_hours
    return model, metrics


def comparison_scorecard(
    baseline_1: dict[str, Any], baseline_2: dict[str, Any]
) -> dict[str, float | None]:
    """Summarize Baseline 2's change relative to Baseline 1."""
    brier_1 = float(baseline_1["brier_score"])
    brier_2 = float(baseline_2["brier_score"])
    pr_1 = baseline_1["pr_auc"]
    pr_2 = baseline_2["pr_auc"]
    return {
        "brier_improvement_pct": (
            100.0 * (brier_1 - brier_2) / brier_1 if brier_1 else None
        ),
        "pr_auc_change": (
            float(pr_2) - float(pr_1) if pr_1 is not None and pr_2 is not None else None
        ),
        "recall_change": float(baseline_2["recall"]) - float(baseline_1["recall"]),
    }


def train_both(
    flights: pd.DataFrame,
    weather: pd.DataFrame,
    *,
    test_days: int = 30,
    threshold: float = 0.5,
    horizon_hours: int = 2,
    direction: str = "departure",
) -> tuple[dict[str, Pipeline], dict[str, Any]]:
    """Train both baselines on an identical temporal evaluation window."""
    model_1, metrics_1 = train_and_evaluate(
        flights, test_days=test_days, threshold=threshold, direction=direction
    )
    model_2, metrics_2 = train_weather_and_evaluate(
        flights,
        weather,
        test_days=test_days,
        threshold=threshold,
        horizon_hours=horizon_hours,
        direction=direction,
    )
    if (metrics_1["test_start"], metrics_1["test_end"], metrics_1["test_rows"]) != (
        metrics_2["test_start"],
        metrics_2["test_end"],
        metrics_2["test_rows"],
    ):
        raise AssertionError("Baselines were evaluated on different holdout rows")
    return (
        {"baseline_1": model_1, "baseline_2": model_2},
        {
            "baseline_1": metrics_1,
            "baseline_2": metrics_2,
            "comparison": comparison_scorecard(metrics_1, metrics_2),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", choices=("1", "2", "both"), default="both")
    parser.add_argument("--flights", type=Path, default=DEFAULT_FLIGHTS)
    parser.add_argument("--weather", type=Path, default=DEFAULT_WEATHER)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument(
        "--metrics-output",
        type=Path,
        default=None,
        help="Defaults to artifacts/metrics/baseline_comparison[_arrival].json",
    )
    parser.add_argument("--test-days", type=int, default=30)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--horizon-hours", type=int, default=2)
    parser.add_argument(
        "--direction",
        choices=("departure", "arrival"),
        default="departure",
        help="Predict departures leaving, or arrivals landing, 30+ minutes late",
    )
    args = parser.parse_args()
    metrics_output = args.metrics_output or DEFAULT_METRICS.with_name(
        artifact_name(DEFAULT_METRICS.name, args.direction)
    )

    flights = pd.read_parquet(args.flights)
    weather = pd.read_parquet(args.weather) if args.baseline in {"2", "both"} else None
    if args.baseline == "1":
        model, metrics = train_and_evaluate(
            flights,
            test_days=args.test_days,
            threshold=args.threshold,
            direction=args.direction,
        )
        models = {"baseline_1": model}
        scorecard: dict[str, Any] = {"baseline_1": metrics}
    elif args.baseline == "2":
        assert weather is not None
        model, metrics = train_weather_and_evaluate(
            flights,
            weather,
            test_days=args.test_days,
            threshold=args.threshold,
            horizon_hours=args.horizon_hours,
            direction=args.direction,
        )
        models = {"baseline_2": model}
        scorecard = {"baseline_2": metrics}
    else:
        assert weather is not None
        models, scorecard = train_both(
            flights,
            weather,
            test_days=args.test_days,
            threshold=args.threshold,
            horizon_hours=args.horizon_hours,
            direction=args.direction,
        )

    args.model_dir.mkdir(parents=True, exist_ok=True)
    metrics_output.parent.mkdir(parents=True, exist_ok=True)
    for key, name in (("baseline_1", BASELINE_1_FILE), ("baseline_2", BASELINE_2_FILE)):
        if key in models:
            joblib.dump(
                models[key], args.model_dir / artifact_name(name, args.direction)
            )
    metrics_output.write_text(json.dumps(scorecard, indent=2) + "\n")
    print(json.dumps(scorecard, indent=2))


if __name__ == "__main__":
    main()
