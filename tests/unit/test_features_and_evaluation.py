import numpy as np
import pandas as pd
import pytest

from skydelay.evaluation.metrics import (
    expected_calibration_error,
    probability_scorecard,
)
from skydelay.features.schedule import TARGET, build_schedule_features
from skydelay.features.weather import (
    WEATHER_AVAILABLE,
    build_schedule_weather_features,
)
from skydelay.models.train_baseline import temporal_split, train_both
from skydelay.pipeline import schema
from skydelay.pipeline.clean_hrrr import CEILING_UNLIMITED


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


def weather_row(valid, *, init, temperature=20.0, hub="ATL"):
    return {
        schema.HUB: hub,
        schema.INIT_TIME_UTC: pd.Timestamp(init, tz="UTC"),
        schema.VALID_TIME_UTC: pd.Timestamp(valid, tz="UTC"),
        schema.AVAILABLE_AT_UTC: pd.Timestamp(init, tz="UTC") + pd.Timedelta(hours=1),
        schema.REFLECTIVITY_DBZ: 5.0,
        schema.VISIBILITY_M: 16_000.0,
        schema.GUST_MS: 8.0,
        schema.TEMPERATURE_C: temperature,
        schema.WIND_SPEED_MS: 5.0,
        schema.WIND_DIRECTION_DEG: 180.0,
        schema.PRECIP_MM_H: 0.0,
        schema.CEILING_M: 2_500.0,
        CEILING_UNLIMITED: False,
    }


def test_weather_join_uses_only_forecasts_available_at_cutoff():
    frame = flights(2)
    frame[schema.SCHED_DEP_UTC] = pd.to_datetime(
        ["2024-07-01 16:10Z", "2024-07-01 16:20Z"]
    )
    frame.loc[1, schema.ORIGIN] = "ORD"
    weather = pd.DataFrame(
        [
            weather_row("2024-07-01 16:00", init="2024-07-01 12:00", temperature=20.0),
            weather_row("2024-07-01 16:00", init="2024-07-01 14:00", temperature=99.0),
        ]
    )

    table = build_schedule_weather_features(frame, weather)

    atl = table.loc[table["hub"] == "ATL"].iloc[0]
    ord_row = table.loc[table["hub"] == "ORD"].iloc[0]
    assert atl[schema.TEMPERATURE_C] == 20.0
    assert atl[schema.AVAILABLE_AT_UTC] <= atl["prediction_cutoff_utc"]
    assert atl[WEATHER_AVAILABLE] == 1
    assert ord_row[WEATHER_AVAILABLE] == 0


def test_both_baselines_share_the_same_future_holdout():
    frame = flights(120)
    frame[schema.SCHED_DEP_UTC] = pd.date_range(
        "2024-01-01 12:00", periods=120, freq="D", tz="UTC"
    )
    weather = pd.DataFrame(
        [
            weather_row(
                departure.strftime("%Y-%m-%d %H:%M"),
                init=(departure - pd.Timedelta(hours=4)).strftime("%Y-%m-%d %H:%M"),
                temperature=5.0 + (index % 20),
            )
            for index, departure in enumerate(frame[schema.SCHED_DEP_UTC])
        ]
    )

    models, scorecard = train_both(frame, weather, test_days=14)

    assert set(models) == {"baseline_1", "baseline_2"}
    assert scorecard["baseline_1"]["test_rows"] == scorecard["baseline_2"]["test_rows"]
    assert scorecard["baseline_2"]["test_weather_coverage"] == 1.0
    assert "brier_improvement_pct" in scorecard["comparison"]
