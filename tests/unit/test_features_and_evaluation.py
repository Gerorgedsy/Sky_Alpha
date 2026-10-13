import numpy as np
import pandas as pd
import pytest

from skydelay.evaluation.metrics import (
    expected_calibration_error,
    probability_scorecard,
)
from skydelay.features.schedule import TARGET, build_schedule_features
from skydelay.models.train_baseline import temporal_split
from skydelay.pipeline import schema


def flights(rows=12):
    time = pd.date_range("2024-07-01 12:00", periods=rows, freq="10min", tz="UTC")
    return pd.DataFrame(
        {
            schema.SCHED_DEP_UTC: time,
            schema.ORIGIN: ["ATL"] * rows,
            schema.DEST: ["JFK", "ORD"] * (rows // 2),
            schema.CARRIER: ["DL"] * rows,
            schema.SCHED_BLOCK_MIN: [120] * rows,
            schema.DISTANCE_MI: [760] * rows,
            TARGET: [False, True] * (rows // 2),
        }
    )


def test_schedule_features_use_prior_departures_only():
    table = build_schedule_features(flights())
    assert table.loc[0, "scheduled_departures_15m"] == 0
    assert table.loc[1, "scheduled_departures_15m"] == 1
    assert table.loc[2, "scheduled_departures_15m"] == 1
    assert table.loc[6, "scheduled_departures_60m"] == 6


def test_scorecard_includes_probability_and_threshold_metrics():
    scorecard = probability_scorecard(
        np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.7, 0.9])
    )
    assert scorecard["accuracy"] == 1.0
    assert scorecard["brier_score"] < scorecard["naive_brier_score"]
    assert scorecard["pr_auc"] == pytest.approx(1.0)


def test_calibration_error_rejects_invalid_probability():
    with pytest.raises(ValueError, match="between 0 and 1"):
        expected_calibration_error(np.array([0, 1]), np.array([0.2, 1.2]))


def test_temporal_split_keeps_the_future_for_testing():
    frame = flights(60)
    frame[schema.SCHED_DEP_UTC] = pd.date_range(
        "2024-01-01", periods=60, freq="D", tz="UTC"
    )
    table = build_schedule_features(frame)
    train, test = temporal_split(table, test_days=14)
    assert train[schema.SCHED_DEP_UTC].max() < test[schema.SCHED_DEP_UTC].min()
