import json
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from skydelay.models import nyc_delay
from skydelay.pipeline import schema


def synthetic_flights(days=120, per_day=60, seed=0):
    """BTS-like cleaned flights at the New York airports."""
    rng = np.random.default_rng(seed)
    rows = []
    start = pd.Timestamp("2026-05-01", tz=nyc_delay.TIMEZONE)
    others = ["ATL", "ORD", "BOS", "MIA", "LAX", "DCA"]
    for d in range(days):
        day = start + pd.Timedelta(days=d)
        for i in range(per_day):
            hub = ("JFK", "LGA", "EWR")[i % 3]
            other = others[rng.integers(len(others))]
            local = day + pd.Timedelta(minutes=int(rng.integers(6 * 60, 23 * 60)))
            block = int(rng.integers(60, 360))
            departing = i % 2 == 0
            # Evening flights and EWR run late more often.
            risk = 0.08 + 0.35 * (local.hour >= 17) + 0.15 * (hub == "EWR")
            late = rng.random() < risk
            delay = float(rng.integers(30, 150) if late else rng.integers(-10, 20))
            sched = local.tz_convert("UTC")
            dep = sched if departing else sched - pd.Timedelta(minutes=block)
            arr = dep + pd.Timedelta(minutes=block)
            rows.append(
                {
                    schema.CARRIER: rng.choice(["B6", "DL", "UA", "AA"]),
                    schema.FLIGHT_NUMBER: str(100 + i),
                    schema.ORIGIN: hub if departing else other,
                    schema.DEST: other if departing else hub,
                    schema.SCHED_DEP_UTC: dep,
                    schema.SCHED_ARR_UTC: arr,
                    schema.SCHED_BLOCK_MIN: block,
                    schema.DISTANCE_MI: block * 7.5,
                    schema.DEP_DELAY_MIN: delay,
                    schema.ARR_DELAY_MIN: delay,
                    schema.DEP_DELAYED_30: delay >= 30,
                    schema.ARR_DELAYED_30: delay >= 30,
                }
            )
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def flights():
    return synthetic_flights()


@pytest.fixture(scope="module")
def models(flights):
    return {d: nyc_delay.train(flights, d) for d in nyc_delay.DIRECTIONS}


def test_model_learns_the_evening_risk_and_is_calibrated(models):
    metrics = models["departure"].metrics
    assert metrics["test_rows"] > 0
    assert metrics["brier_score"] < metrics["naive_brier_score"]
    for row in metrics["calibration"]:
        if row["flights"] > 100:
            assert abs(row["mean_probability"] - row["delay_rate"]) < 0.12
    assert 0.6 < metrics["range_80_coverage_when_correct"] < 0.95


def test_features_use_local_time_and_schedule_density(flights):
    frame = nyc_delay.direction_frame(flights, "departure")
    features = nyc_delay.build_features(frame)
    assert set(features["hub"]) == {"JFK", "LGA", "EWR"}
    assert features["local_hour"].between(6, 23).all()
    assert (features["hub_day_total"] >= features["carrier_hub_day"]).all()


def test_stand_in_copies_the_latest_same_weekday_at_local_time(flights):
    reference = nyc_delay.schedule_reference(flights)
    target = date(2026, 11, 16)  # a Monday in standard time
    rows, source = nyc_delay.stand_in(reference, "departure", target)
    assert source.weekday() == target.weekday()
    original = nyc_delay.direction_frame(reference, "departure")
    original = original.loc[original["local_date"] == pd.Timestamp(source)]
    assert len(rows) == len(original)
    # Same local clock time after the move from daylight to standard time.
    assert sorted(rows["local"].dt.strftime("%H:%M")) == sorted(
        original["local"].dt.strftime("%H:%M")
    )
    assert (rows["local"].dt.date == target).all()


def test_stand_in_skips_days_cut_off_by_the_record_edges(flights):
    reference = nyc_delay.schedule_reference(flights)
    last = nyc_delay.direction_frame(reference, "departure")["local_date"].max()
    target = (last + pd.Timedelta(days=7)).date()
    _, source = nyc_delay.stand_in(reference, "departure", target)
    assert pd.Timestamp(source) == last - pd.Timedelta(days=7)


def test_payload_has_calls_minutes_and_confidence(flights, models):
    reference = nyc_delay.schedule_reference(flights)
    targets = [date(2026, 11, 16), date(2026, 11, 17)]
    records, sources = nyc_delay.predict_days(models, reference, targets)
    body = nyc_delay.payload(
        models, records, sources, generated_at=datetime(2026, 11, 16, 9, tzinfo=UTC)
    )
    assert body["schema_version"] == 3
    assert set(sources) == {"2026-11-16", "2026-11-17"}
    assert {f["direction"] for f in records} == {"departure", "arrival"}
    for f in records:
        assert f["airport"] in nyc_delay.NYC
        assert 0.5 <= f["confidence"] <= 1
        assert f["delay_low_min"] <= f["predicted_delay_min"] <= f["delay_high_min"]
        if f["prediction"] == "delayed":
            assert f["delay_low_min"] >= 30
        else:
            assert f["delay_high_min"] < 30
        hub = f["origin"] if f["direction"] == "departure" else f["dest"]
        assert hub == f["airport"]
    late = [f for f in records if f["prediction"] == "delayed"]
    assert late and all(f["delay_probability"] >= 0.5 for f in late)
    assert "accuracy" in body["models"]["arrival"]["validation"]
    json.dumps(body)


def test_latest_bts_month_walks_back(monkeypatch):
    published = {"2026_8"}
    monkeypatch.setattr(
        nyc_delay, "bts_available", lambda url: url.split("/")[-1] in published
    )
    month = nyc_delay.latest_bts_month("x/{year}_{month}", date(2026, 11, 16))
    assert str(month) == "2026-08"
