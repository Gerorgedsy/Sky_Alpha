import json

import numpy as np
import pandas as pd
import pytest

from skydelay.evaluation.tune import (
    best_f1_threshold,
    day_bootstrap_auc_difference,
    holdout_metrics,
    parameter_grid,
    rolling_folds,
    run,
)
from skydelay.features.schedule import TARGET
from skydelay.pipeline import schema
from skydelay.pipeline.clean_hrrr import CEILING_UNLIMITED

HUBS = ("ATL", "ORD", "JFK")


def synthetic(days=90, per_hub_per_day=24, seed=0):
    """Flights whose delays depend on hour of day and on forecast storms."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2024-06-01", tz="UTC")
    hours = pd.date_range(start, periods=days * 24, freq="h")
    storm = {hub: rng.random(len(hours)) < 0.15 for hub in HUBS}

    weather_rows = []
    for hub in HUBS:
        for init in hours[::3]:
            for lead in (0, 2, 4, 6):
                valid = init + pd.Timedelta(hours=lead)
                if valid >= hours[-1]:
                    continue
                stormy = storm[hub][hours.get_loc(valid)]
                weather_rows.append(
                    {
                        schema.HUB: hub,
                        schema.INIT_TIME_UTC: init,
                        schema.VALID_TIME_UTC: valid,
                        schema.AVAILABLE_AT_UTC: init + pd.Timedelta(hours=1),
                        schema.REFLECTIVITY_DBZ: 45.0 if stormy else 5.0,
                        schema.VISIBILITY_M: 3_000.0 if stormy else 16_000.0,
                        schema.GUST_MS: 15.0 if stormy else 6.0,
                        schema.TEMPERATURE_C: 25.0,
                        schema.WIND_SPEED_MS: 8.0,
                        schema.WIND_DIRECTION_DEG: 200.0,
                        schema.PRECIP_MM_H: 6.0 if stormy else 0.0,
                        schema.CEILING_M: 600.0 if stormy else 3_000.0,
                        CEILING_UNLIMITED: False,
                    }
                )

    flight_rows = []
    for hub in HUBS:
        for day in range(days):
            minutes = np.sort(rng.integers(6 * 60, 23 * 60, per_hub_per_day))
            for minute in minutes:
                departure = start + pd.Timedelta(days=day, minutes=int(minute))
                hour_index = hours.get_loc(departure.floor("h"))
                logit = (
                    -2.2 + 0.08 * (departure.hour - 12) + 2.5 * storm[hub][hour_index]
                )
                flight_rows.append(
                    {
                        schema.SCHED_DEP_UTC: departure,
                        schema.ORIGIN: hub,
                        schema.DEST: rng.choice(["LAX", "DEN", "BOS", "MIA"]),
                        schema.CARRIER: rng.choice(["DL", "AA", "UA"]),
                        schema.SCHED_BLOCK_MIN: 150,
                        schema.DISTANCE_MI: 900,
                        TARGET: rng.random() < 1 / (1 + np.exp(-logit)),
                    }
                )
    return pd.DataFrame(flight_rows), pd.DataFrame(weather_rows)


def test_rolling_folds_never_validate_on_the_past():
    times = pd.Series(pd.date_range("2024-01-01", periods=100, freq="D", tz="UTC"))
    folds = list(rolling_folds(times, folds=3, validation_days=10))
    assert len(folds) == 3
    for fold in folds:
        assert times.iloc[fold.train].max() < times.iloc[fold.validate].min()
    # Expanding window: each fold trains on more history than the last.
    assert [len(f.train) for f in folds] == sorted(len(f.train) for f in folds)


def test_rolling_folds_reject_too_many_folds():
    times = pd.Series(pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC"))
    with pytest.raises(ValueError, match="empty"):
        list(rolling_folds(times, folds=5, validation_days=5))


def test_parameter_grid_is_the_full_product():
    grid = parameter_grid({"C": (0.1, 1.0), "l1_ratio": (0.0, 1.0)})
    assert len(grid) == 4
    assert {"C": 0.1, "l1_ratio": 1.0} in grid


def test_best_f1_threshold_separates_perfectly_ranked_scores():
    truth = np.array([0, 0, 0, 1, 1])
    probability = np.array([0.1, 0.2, 0.3, 0.6, 0.8])
    threshold = best_f1_threshold(truth, probability)
    assert 0.3 < threshold <= 0.6
    assert holdout_metrics(truth, probability, threshold)["f1"] == 1.0


def test_holdout_metrics_report_the_always_on_time_accuracy():
    truth = np.array([0, 0, 0, 1])
    metrics = holdout_metrics(truth, np.array([0.1, 0.1, 0.1, 0.1]), 0.5)
    assert metrics["accuracy"] == metrics["accuracy_always_on_time"] == 0.75
    assert metrics["f1"] == 0.0
    assert metrics["rmse_probability"] == pytest.approx(np.sqrt((3 * 0.01 + 0.81) / 4))


def test_day_bootstrap_detects_a_better_model():
    rng = np.random.default_rng(1)
    truth = rng.integers(0, 2, 2_000)
    noise = rng.random(2_000)
    days = np.repeat(np.arange(40), 50)
    result = day_bootstrap_auc_difference(
        truth, noise, 0.7 * truth + 0.3 * noise, days, resamples=200
    )
    assert result["auc_difference"] > 0.2
    assert result["ci_low"] > 0


def test_run_tunes_and_compares_both_models(tmp_path):
    flights, weather = synthetic()
    report = run(
        flights,
        weather,
        grid_name="quick",
        folds=3,
        validation_days=10,
        test_days=14,
        bootstrap_resamples=100,
        metrics_dir=tmp_path / "metrics",
        figures_dir=tmp_path / "figures",
    )

    for name in ("baseline_1", "baseline_2"):
        holdout = report[name]["holdout"]
        assert set(report[name]["best_hyperparameters"]) == {
            "C",
            "l1_ratio",
            "min_frequency",
        }
        assert 0 < holdout["threshold"] < 1
        assert {"roc_auc", "f1", "accuracy", "rmse_probability"} <= set(holdout)
    assert (
        report["baseline_1"]["holdout"]["rows"]
        == report["baseline_2"]["holdout"]["rows"]
    )
    # Delays here are driven by forecast storms, so weather must help.
    assert report["baseline_2"]["holdout"]["roc_auc"] > (
        report["baseline_1"]["holdout"]["roc_auc"] + 0.05
    )
    assert report["baseline_2_minus_baseline_1"]["ci_low"] > 0

    saved = json.loads((tmp_path / "metrics" / "model_evaluation.json").read_text())
    assert saved["setup"]["holdout_weather_coverage"] > 0.9
    assert saved["setup"]["selection_metric"] == "mean validation ROC-AUC"
    assert (tmp_path / "metrics" / "tuning_scores.csv").exists()
    assert (tmp_path / "figures" / "holdout_roc.png").exists()
    assert (tmp_path / "figures" / "tuning_auc_vs_c.png").exists()
