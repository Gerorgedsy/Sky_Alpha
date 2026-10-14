"""Tune and compare the two baselines with rolling time-series validation.

Both baselines are the same logistic-regression pipeline; Baseline 2 adds
HRRR weather features. This module answers two questions on identical rows:

1. **Which hyperparameters?** Inside the development period (everything
   before the final holdout), rolling-origin folds train on the past and
   validate on the next block of days. Every grid combination is scored by
   mean validation ROC-AUC, which does not depend on a decision threshold.
2. **How good is each model?** The chosen combination is refit on the whole
   development period and scored once on the untouched final holdout.

The alert threshold is a second hyperparameter. It is chosen to maximise F1
on the pooled out-of-fold validation predictions, never on the holdout.

Reported holdout metrics:

- ROC-AUC: ranking quality, independent of the threshold (primary);
- F1 at the tuned threshold: balance of missed delays and false alerts;
- accuracy, next to the accuracy of always predicting "on time", because
  with ~20 % delayed flights a useless model already scores about 80 %;
- probability RMSE (the square root of the Brier score), how far predicted
  probabilities sit from the 0/1 outcomes.

The AUC difference between the baselines gets a 95 % interval from a paired
bootstrap that resamples whole days, since flights on the same day share
weather and are not independent.

Example:

    PYTHONPATH=src python -m skydelay.evaluation.tune \\
        --flights data/processed/flights.parquet \\
        --weather data/processed/weather.parquet
"""

from __future__ import annotations

import argparse
import itertools
import json
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    f1_score,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)

from skydelay.features.schedule import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    TARGET,
    build_schedule_features,
)
from skydelay.features.weather import (
    WEATHER_AVAILABLE,
    WEATHER_NUMERIC_FEATURES,
    build_schedule_weather_features,
)
from skydelay.models.train_baseline import build_model, temporal_split
from skydelay.pipeline import schema

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FLIGHTS = PROJECT_ROOT / "data" / "processed" / "flights.parquet"
DEFAULT_WEATHER = PROJECT_ROOT / "data" / "processed" / "weather.parquet"
DEFAULT_METRICS_DIR = PROJECT_ROOT / "artifacts" / "metrics"
DEFAULT_FIGURES_DIR = PROJECT_ROOT / "reports" / "figures"

# C is the inverse regularization strength: small C shrinks coefficients
# hard (simpler model), large C lets them fit the training data closely.
# l1_ratio 0 is ridge (L2), 1 is lasso (L1, can zero out route dummies).
# min_frequency folds categories rarer than this into one "infrequent"
# column, which mostly affects the several hundred route dummies.
GRIDS: dict[str, dict[str, tuple[Any, ...]]] = {
    "quick": {
        "C": (0.01, 0.1, 1.0),
        "l1_ratio": (0.0,),
        "min_frequency": (5,),
    },
    "full": {
        "C": (0.001, 0.01, 0.1, 1.0, 10.0),
        "l1_ratio": (0.0, 1.0),
        "min_frequency": (5, 100),
    },
}

PARAMETER_PATHS: dict[str, str] = {
    "C": "classifier__C",
    "l1_ratio": "classifier__l1_ratio",
    "min_frequency": "features__categorical__one_hot__min_frequency",
}

MODELS: dict[str, str] = {
    "baseline_1": "Baseline 1: schedule",
    "baseline_2": "Baseline 2: schedule + HRRR",
}


@dataclass(frozen=True)
class Fold:
    number: int
    train: np.ndarray
    validate: np.ndarray
    validate_start: pd.Timestamp


def rolling_folds(
    times: pd.Series, *, folds: int, validation_days: int
) -> Iterator[Fold]:
    """Expanding-window folds over the last ``folds x validation_days`` days.

    Fold ``k`` trains on everything before its validation block and
    validates on the block itself, so a model never sees the future.
    """
    if folds < 1 or validation_days < 1:
        raise ValueError("folds and validation_days must be positive")
    times = pd.to_datetime(times, utc=True)
    end = times.max()
    step = pd.Timedelta(days=validation_days)
    position = np.arange(len(times))
    for number in range(folds):
        start = end - step * (folds - number)
        stop = start + step
        train = position[(times < start).to_numpy()]
        validate = position[((times >= start) & (times < stop)).to_numpy()]
        if number == folds - 1:
            validate = position[(times >= start).to_numpy()]
        if len(train) == 0 or len(validate) == 0:
            raise ValueError(
                f"Fold {number + 1} is empty; use fewer folds or shorter blocks."
            )
        yield Fold(number + 1, train, validate, start)


def parameter_grid(grid: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    names = list(grid)
    return [
        dict(zip(names, values, strict=True))
        for values in itertools.product(*(grid[name] for name in names))
    ]


def configured_model(numeric_features: tuple[str, ...], params: Mapping[str, Any]):
    model = build_model(numeric_features)
    model.set_params(**{PARAMETER_PATHS[k]: v for k, v in params.items()})
    return model


def best_f1_threshold(y_true: np.ndarray, probability: np.ndarray) -> float:
    """The probability cut that maximises F1 on validation predictions."""
    precision, recall, thresholds = precision_recall_curve(y_true, probability)
    # The last precision/recall pair has no threshold.
    precision, recall = precision[:-1], recall[:-1]
    f1 = np.divide(
        2 * precision * recall,
        precision + recall,
        out=np.zeros_like(precision),
        where=(precision + recall) > 0,
    )
    return float(thresholds[int(np.argmax(f1))])


def holdout_metrics(
    y_true: np.ndarray, probability: np.ndarray, threshold: float
) -> dict[str, float]:
    predicted = probability >= threshold
    majority = float(max(y_true.mean(), 1 - y_true.mean()))
    return {
        "rows": len(y_true),
        "delay_rate": float(y_true.mean()),
        "threshold": float(threshold),
        "roc_auc": float(roc_auc_score(y_true, probability)),
        "f1": float(f1_score(y_true, predicted, zero_division=0)),
        "accuracy": float(accuracy_score(y_true, predicted)),
        "accuracy_always_on_time": majority,
        "rmse_probability": float(np.sqrt(brier_score_loss(y_true, probability))),
        "f1_at_0_5": float(f1_score(y_true, probability >= 0.5, zero_division=0)),
        "accuracy_at_0_5": float(accuracy_score(y_true, probability >= 0.5)),
    }


def tune_model(
    name: str,
    development: pd.DataFrame,
    numeric_features: tuple[str, ...],
    grid: list[dict[str, Any]],
    folds: list[Fold],
) -> tuple[pd.DataFrame, dict[str, Any], float]:
    """Score every combination on every fold; return the scores, best, threshold."""
    features = [*CATEGORICAL_FEATURES, *numeric_features]
    x = development[features]
    y = development[TARGET].to_numpy()
    rows: list[dict[str, Any]] = []
    out_of_fold: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for index, params in enumerate(grid):
        started = time.perf_counter()
        truth, scores = [], []
        for fold in folds:
            model = configured_model(numeric_features, params)
            model.fit(x.iloc[fold.train], y[fold.train])
            probability = model.predict_proba(x.iloc[fold.validate])[:, 1]
            rows.append(
                {
                    "model": name,
                    **params,
                    "fold": fold.number,
                    "validate_start": fold.validate_start.isoformat(),
                    "train_rows": len(fold.train),
                    "validate_rows": len(fold.validate),
                    "roc_auc": roc_auc_score(y[fold.validate], probability),
                }
            )
            truth.append(y[fold.validate])
            scores.append(probability)
        out_of_fold[index] = (np.concatenate(truth), np.concatenate(scores))
        mean_auc = np.mean([r["roc_auc"] for r in rows[-len(folds) :]])
        print(
            f"  {name} {index + 1}/{len(grid)} {params}: mean AUC {mean_auc:.4f}"
            f" ({time.perf_counter() - started:.0f}s)",
            flush=True,
        )
    scores = pd.DataFrame(rows)
    # Rows were appended combination by combination, folds innermost.
    mean_auc = scores["roc_auc"].to_numpy().reshape(len(grid), len(folds)).mean(1)
    best_index = int(mean_auc.argmax())
    best = dict(grid[best_index])
    threshold = best_f1_threshold(*out_of_fold[best_index])
    return scores, best, threshold


def day_bootstrap_auc_difference(
    y_true: np.ndarray,
    probability_1: np.ndarray,
    probability_2: np.ndarray,
    days: np.ndarray,
    *,
    resamples: int = 1000,
    seed: int = 0,
) -> dict[str, float]:
    """AUC(model 2) - AUC(model 1) with a 95 % interval over resampled days."""
    rng = np.random.default_rng(seed)
    unique, inverse = np.unique(days, return_inverse=True)
    members = [np.flatnonzero(inverse == i) for i in range(len(unique))]
    differences = []
    for _ in range(resamples):
        chosen = rng.integers(0, len(unique), len(unique))
        rows = np.concatenate([members[i] for i in chosen])
        if np.unique(y_true[rows]).size < 2:
            continue
        differences.append(
            roc_auc_score(y_true[rows], probability_2[rows])
            - roc_auc_score(y_true[rows], probability_1[rows])
        )
    low, high = np.percentile(differences, [2.5, 97.5])
    return {
        "auc_difference": float(
            roc_auc_score(y_true, probability_2) - roc_auc_score(y_true, probability_1)
        ),
        "ci_low": float(low),
        "ci_high": float(high),
        "resamples": len(differences),
    }


def build_tables(
    flights: pd.DataFrame, weather: pd.DataFrame, horizon_hours: int
) -> pd.DataFrame:
    """One table for both models, so they are tuned and scored on the same rows.

    The weather builder keeps every schedule row (weather is left-joined), so
    Baseline 1 simply ignores the weather columns of the same table.
    """
    table = build_schedule_weather_features(
        flights, weather, horizon_hours=horizon_hours
    )
    expected = len(build_schedule_features(flights))
    if len(table) != expected:
        raise AssertionError("The weather join changed the number of flights.")
    return table.sort_values(schema.SCHED_DEP_UTC, kind="stable").reset_index(drop=True)


def plot(
    tuning: pd.DataFrame,
    roc: Mapping[str, tuple[np.ndarray, np.ndarray, float]],
    figures_dir: Path,
) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    figure, axis = plt.subplots(figsize=(6.5, 4.2))
    # One line per model and penalty; min_frequency is folded into the best
    # value at each C so the chart stays readable.
    colors = {"baseline_1": "tab:blue", "baseline_2": "tab:orange"}
    styles = {0.0: ("-", "o", "L2"), 1.0: ("--", "s", "L1")}
    per_fold = tuning.groupby(["model", "l1_ratio", "C", "fold"], sort=False)[
        "roc_auc"
    ].max()
    summary = per_fold.groupby(level=["model", "l1_ratio", "C"], sort=False).agg(
        ["mean", "std"]
    )
    for (model, l1_ratio), part in summary.groupby(
        level=["model", "l1_ratio"], sort=False
    ):
        line, marker, penalty = styles.get(l1_ratio, (":", "^", f"l1={l1_ratio}"))
        axis.errorbar(
            part.index.get_level_values("C"),
            part["mean"],
            yerr=part["std"],
            color=colors.get(model),
            linestyle=line,
            marker=marker,
            capsize=3,
            label=f"{MODELS[model]}, {penalty}",
        )
    axis.set_xscale("log")
    axis.set_xlabel("C (inverse regularization strength)")
    axis.set_ylabel("Mean validation ROC-AUC (± sd over folds)")
    axis.set_title("Hyperparameter search, rolling time-series folds")
    axis.legend(fontsize=7)
    figure.tight_layout()
    path = figures_dir / "tuning_auc_vs_c.png"
    figure.savefig(path, dpi=160)
    plt.close(figure)
    paths.append(path)

    figure, axis = plt.subplots(figsize=(5, 5))
    for model, (fpr, tpr, auc) in roc.items():
        axis.plot(fpr, tpr, label=f"{MODELS[model]} (AUC {auc:.3f})")
    axis.plot([0, 1], [0, 1], color="grey", linestyle="--", label="Chance (0.5)")
    axis.set_xlabel("False positive rate")
    axis.set_ylabel("True positive rate (recall)")
    axis.set_title("ROC on the final holdout")
    axis.legend(fontsize=8, loc="lower right")
    figure.tight_layout()
    path = figures_dir / "holdout_roc.png"
    figure.savefig(path, dpi=160)
    plt.close(figure)
    paths.append(path)
    return paths


def run(
    flights: pd.DataFrame,
    weather: pd.DataFrame,
    *,
    hubs: Sequence[str] | None = None,
    grid_name: str = "full",
    folds: int = 4,
    validation_days: int = 14,
    test_days: int = 30,
    horizon_hours: int = 2,
    bootstrap_resamples: int = 1000,
    metrics_dir: Path = DEFAULT_METRICS_DIR,
    figures_dir: Path | None = DEFAULT_FIGURES_DIR,
) -> dict[str, Any]:
    if hubs:
        flights = flights.loc[flights[schema.ORIGIN].isin(hubs)]
    table = build_tables(flights, weather, horizon_hours)
    development, holdout = temporal_split(table, test_days)
    development = development.reset_index(drop=True)
    holdout = holdout.reset_index(drop=True)
    fold_list = list(
        rolling_folds(
            development[schema.SCHED_DEP_UTC],
            folds=folds,
            validation_days=validation_days,
        )
    )
    grid = parameter_grid(GRIDS[grid_name])
    print(
        f"{len(development):,} development rows, {len(holdout):,} holdout rows;"
        f" {len(grid)} combinations x {len(fold_list)} folds x 2 models.",
        flush=True,
    )

    numeric = {
        "baseline_1": NUMERIC_FEATURES,
        "baseline_2": (*NUMERIC_FEATURES, *WEATHER_NUMERIC_FEATURES),
    }
    y_holdout = holdout[TARGET].to_numpy()
    tuning_frames, results, probabilities, roc = [], {}, {}, {}
    for name, numeric_features in numeric.items():
        scores, best, threshold = tune_model(
            name, development, numeric_features, grid, fold_list
        )
        tuning_frames.append(scores)
        model = configured_model(numeric_features, best)
        features = [*CATEGORICAL_FEATURES, *numeric_features]
        model.fit(development[features], development[TARGET].to_numpy())
        probability = model.predict_proba(holdout[features])[:, 1]
        probabilities[name] = probability
        fpr, tpr, _ = roc_curve(y_holdout, probability)
        metrics = holdout_metrics(y_holdout, probability, threshold)
        roc[name] = (fpr, tpr, metrics["roc_auc"])
        best_scores = scores
        for key, value in best.items():
            best_scores = best_scores.loc[best_scores[key] == value]
        results[name] = {
            "label": MODELS[name],
            "best_hyperparameters": best,
            "validation_auc_mean": float(best_scores["roc_auc"].mean()),
            "validation_auc_std": float(best_scores["roc_auc"].std(ddof=0)),
            "holdout": metrics,
        }

    days = holdout[schema.SCHED_DEP_UTC].dt.strftime("%Y-%m-%d").to_numpy()
    comparison = day_bootstrap_auc_difference(
        y_holdout,
        probabilities["baseline_1"],
        probabilities["baseline_2"],
        days,
        resamples=bootstrap_resamples,
    )
    tuning = pd.concat(tuning_frames, ignore_index=True)
    report = {
        "setup": {
            "hubs": sorted(table["hub"].unique().tolist()),
            "development_start": development[schema.SCHED_DEP_UTC].min().isoformat(),
            "holdout_start": holdout[schema.SCHED_DEP_UTC].min().isoformat(),
            "holdout_end": holdout[schema.SCHED_DEP_UTC].max().isoformat(),
            "development_weather_coverage": float(
                development[WEATHER_AVAILABLE].mean()
            ),
            "holdout_weather_coverage": float(holdout[WEATHER_AVAILABLE].mean()),
            "folds": folds,
            "validation_days": validation_days,
            "grid": GRIDS[grid_name],
            "selection_metric": "mean validation ROC-AUC",
            "threshold_rule": "max F1 on pooled out-of-fold predictions",
        },
        **results,
        "baseline_2_minus_baseline_1": comparison,
    }

    metrics_dir.mkdir(parents=True, exist_ok=True)
    tuning.to_csv(metrics_dir / "tuning_scores.csv", index=False)
    summary_rows = [
        {
            "model": MODELS[name],
            **{
                f"best_{k}": v for k, v in results[name]["best_hyperparameters"].items()
            },
            "validation_auc": round(results[name]["validation_auc_mean"], 4),
            **{
                k: round(v, 4) if isinstance(v, float) else v
                for k, v in results[name]["holdout"].items()
            },
        }
        for name in numeric
    ]
    pd.DataFrame(summary_rows).to_csv(metrics_dir / "model_evaluation.csv", index=False)
    (metrics_dir / "model_evaluation.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n"
    )
    if figures_dir is not None:
        plot(tuning, roc, figures_dir)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--flights", type=Path, default=DEFAULT_FLIGHTS)
    parser.add_argument("--weather", type=Path, default=DEFAULT_WEATHER)
    parser.add_argument(
        "--hubs", nargs="*", default=None, help="Origin hubs, e.g. ATL ORD JFK."
    )
    parser.add_argument("--grid", choices=tuple(GRIDS), default="full")
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--validation-days", type=int, default=14)
    parser.add_argument("--test-days", type=int, default=30)
    parser.add_argument("--horizon-hours", type=int, default=2)
    parser.add_argument("--bootstrap-resamples", type=int, default=1000)
    parser.add_argument("--metrics-dir", type=Path, default=DEFAULT_METRICS_DIR)
    parser.add_argument("--figures-dir", type=Path, default=DEFAULT_FIGURES_DIR)
    args = parser.parse_args()

    report = run(
        pd.read_parquet(args.flights),
        pd.read_parquet(args.weather),
        hubs=args.hubs,
        grid_name=args.grid,
        folds=args.folds,
        validation_days=args.validation_days,
        test_days=args.test_days,
        horizon_hours=args.horizon_hours,
        bootstrap_resamples=args.bootstrap_resamples,
        metrics_dir=args.metrics_dir,
        figures_dir=args.figures_dir,
    )
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
