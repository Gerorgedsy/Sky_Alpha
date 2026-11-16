import json
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from skydelay.models import nyc_context as ctx
from skydelay.models import nyc_delay
from skydelay.pipeline import schema


def synthetic_flights(days=100, per_day=240, seed=0):
    """BTS-like cleaned flights at the New York airports, with stormy days."""
    rng = np.random.default_rng(seed)
    rows = []
    start = pd.Timestamp("2026-05-01", tz=nyc_delay.TIMEZONE)
    others = ["ATL", "ORD", "BOS", "MIA", "LAX", "DCA"]
    stormy = set(rng.choice(days, size=days // 5, replace=False))
    for d in range(days):
        day = start + pd.Timedelta(days=d)
        for i in range(per_day):
            hub = ("JFK", "LGA", "EWR")[i % 3]
            other = others[rng.integers(len(others))]
            local = day + pd.Timedelta(minutes=int(rng.integers(6 * 60, 23 * 60)))
            block = int(rng.integers(60, 360))
            departing = i % 2 == 0
            # Evening flights, EWR and stormy afternoons run late more often.
            risk = 0.06 + 0.2 * (local.hour >= 17) + 0.1 * (hub == "EWR")
            risk += 0.5 * (d in stormy and local.hour >= 12)
            late = rng.random() < min(risk, 0.95)
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
                    schema.ACTUAL_DEP_UTC: dep + pd.Timedelta(minutes=delay),
                    schema.SCHED_BLOCK_MIN: block,
                    schema.DISTANCE_MI: block * 7.5,
                    schema.DEP_DELAY_MIN: delay,
                    schema.ARR_DELAY_MIN: delay,
                    schema.DEP_DELAYED_30: delay >= 30,
                    schema.ARR_DELAYED_30: delay >= 30,
                }
            )
    flights = pd.DataFrame(rows)
    flights.attrs["stormy"] = {
        (start + pd.Timedelta(days=int(d))).date() for d in stormy
    }
    return flights


def synthetic_metar(flights):
    """Hourly IEM-style reports: thunderstorms on stormy afternoons."""
    stormy = flights.attrs["stormy"]
    hours = pd.date_range(
        pd.to_datetime(flights[schema.SCHED_DEP_UTC]).min().floor("D"),
        pd.to_datetime(flights[schema.SCHED_ARR_UTC]).max().ceil("D"),
        freq="h",
    )
    rows = []
    for at in hours:
        local = at.tz_convert(nyc_delay.TIMEZONE)
        storm = local.date() in stormy and local.hour >= 11
        for hub in ("JFK", "LGA", "EWR"):
            rows.append(
                {
                    "station": hub,
                    "valid": at.strftime("%Y-%m-%d %H:%M"),
                    "tmpf": 75,
                    "dwpf": 70 if storm else 55,
                    "sknt": 25 if storm else 8,
                    "gust": 40 if storm else None,
                    "vsby": 2 if storm else 10,
                    "skyc1": "OVC" if storm else "FEW",
                    "skyl1": 1500 if storm else 25000,
                    "skyc2": None,
                    "skyl2": None,
                    "skyc3": None,
                    "skyl3": None,
                    "wxcodes": "+TSRA" if storm else None,
                }
            )
    return pd.DataFrame(rows)


def history_for(flights):
    return nyc_delay.History(
        weather=ctx.metar_table(synthetic_metar(flights)),
        state=ctx.state_table(flights),
    )


def live_for(now):
    return nyc_delay.Live.empty(now)


@pytest.fixture(scope="module")
def flights():
    return synthetic_flights()


@pytest.fixture(scope="module")
def history(flights):
    return history_for(flights)


@pytest.fixture(scope="module")
def models(flights, history):
    return {d: nyc_delay.train(flights, d, history) for d in nyc_delay.DIRECTIONS}


def test_model_learns_the_evening_risk_and_is_calibrated(models):
    metrics = models["departure"].metrics
    # Weather and delay inputs are what catch the stormy days.
    assert metrics["brier_score"] < metrics["without_live_inputs"]["brier_score"]
    assert metrics["recall"] > metrics["without_live_inputs"]["recall"]
    assert "0-3h" in metrics["by_horizon"]
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
    for direction in nyc_delay.DIRECTIONS:
        _, source = nyc_delay.stand_in(reference, direction, target)
        assert pd.Timestamp(source) == last - pd.Timedelta(days=7)


def test_payload_has_calls_minutes_and_confidence(flights, models):
    reference = nyc_delay.schedule_reference(flights)
    targets = [date(2026, 11, 16), date(2026, 11, 17)]
    records, sources = nyc_delay.predict_days(
        models, reference, targets, live=live_for(datetime(2026, 11, 16, 9, tzinfo=UTC))
    )
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


def test_live_inputs_raise_the_risk_on_a_stormy_afternoon(flights, models):
    reference = nyc_delay.schedule_reference(flights)
    # A date inside the synthetic season, so only the live inputs differ.
    now = datetime(2026, 7, 15, 16, tzinfo=UTC)  # noon in New York
    storm = {
        "weather": {
            "airports": {
                hub: {
                    "observedAt": "2026-07-15T15:51:00Z",
                    "windKt": 25,
                    "gustKt": 40,
                    "visibilityMi": "2",
                    "ceilingFt": 1500,
                    "wx": "+TSRA",
                    "temperatureC": 24,
                    "dewpointC": 21,
                }
                for hub in ("JFK", "LGA", "EWR")
            }
        }
    }
    late = [
        {
            "flight": f"XX{i}",
            "status": "active",
            "scheduledUtc": f"2026-07-15 {13 + i % 3}:{10 * (i % 6):02d}",
            "delayMin": 70,
        }
        for i in range(10)
    ]
    schedules = {
        "status": "live",
        "fetchedAt": "2026-07-15T15:58:00Z",
        "airports": {hub: {"departures": late} for hub in ("JFK", "LGA", "EWR")},
    }
    stormy = nyc_delay.Live(ctx.live_weather(storm), ctx.live_state(schedules), now)
    calm = nyc_delay.Live.empty(now)
    target = [date(2026, 7, 15)]

    def evening(live):
        records, _ = nyc_delay.predict_days(
            {"departure": models["departure"]}, reference, target, live=live
        )
        return np.mean(
            [
                f["delay_probability"]
                for f in records
                if "17:00" <= f["scheduled_utc"][11:16] <= "21:00"
            ]
        )

    assert ctx.live_state(schedules)["state_mean_delay"].tolist() == [70.0] * 3
    assert evening(stormy) > evening(calm) + 0.05


def test_metar_table_reads_ceiling_and_weather_codes():
    raw = pd.DataFrame(
        {
            "station": ["JFK", "LGA"],
            "valid": ["2026-07-01 18:51", "2026-07-01 18:51"],
            "tmpf": [80, 80],
            "dwpf": [70, 60],
            "sknt": [12, 5],
            "gust": [22, None],
            "vsby": [3, 10],
            "skyc1": ["SCT", "FEW"],
            "skyl1": [800, 4000],
            "skyc2": ["BKN", None],
            "skyl2": [2500, None],
            "skyc3": [None, None],
            "skyl3": [None, None],
            "wxcodes": ["TSRA BR", None],
        }
    )
    table = ctx.metar_table(raw).set_index("hub")
    assert table.loc["JFK", "wx_ceiling_ft"] == 2500
    assert table.loc["LGA", "wx_ceiling_ft"] == ctx.NO_CEILING_FT
    assert table.loc["JFK", "wx_thunder"] == 1 and table.loc["JFK", "wx_precip"] == 1
    assert table.loc["LGA", "wx_gust_kt"] == 5
