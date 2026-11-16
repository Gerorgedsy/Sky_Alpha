"""Day-ahead delay predictions for every flight at JFK, LGA and EWR.

Three steps, each a sub-command:

``prepare``
    Find the newest month BTS has published, download the last ``--months``
    months for the New York airports, and clean them into
    ``flights.parquet`` with the existing pipeline.
``train``
    Fit one model bundle per direction (departures from, arrivals at New York)
    and write it with its holdout scorecard and a two-week schedule reference.
``predict``
    Build a stand-in schedule for today and tomorrow from the reference, score
    every flight, and write the website's ``predictions.json``.

Each bundle holds

- a gradient-boosted classifier for "30 or more minutes late", calibrated
  with isotonic regression on a later, held-apart month, so its probabilities
  can be read as frequencies;
- gradient-boosted quantile regressors (10th, 50th and 90th percentiles) for
  the delay in minutes, fitted separately on flights that ran 30+ minutes late
  and on those that did not. The call picks the set, so a "delayed" flight
  always shows a delay of 30+ minutes and an "on time" one less, with the
  range that flights like it actually had.

Only information known from the published schedule is used: airport,
airline, the other airport, local time, weekday, month, holiday proximity,
block time, distance and how busy the airport's schedule is around the
flight. BTS is published about two months late, so no live delay state is
available; the scorecard says how well that works.

The stand-in schedule copies the latest same-weekday flights on record onto
the target date at the same local times. Real schedules change, so the page
labels it as a typical schedule.

Examples:

    PYTHONPATH=src python -m skydelay.models.nyc_delay prepare --work work
    PYTHONPATH=src python -m skydelay.models.nyc_delay train \
        --flights work/processed/flights.parquet --output artifacts/nyc
    PYTHONPATH=src python -m skydelay.models.nyc_delay predict \
        --artifacts artifacts/nyc --output prototype/data/predictions.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
)
from sklearn.isotonic import IsotonicRegression

from skydelay.evaluation.metrics import probability_scorecard
from skydelay.pipeline import schema

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "sources.json"
TIMEZONE = "America/New_York"
NYC = ("JFK", "LGA", "EWR")
NAMES = {
    "JFK": "John F. Kennedy International",
    "LGA": "LaGuardia",
    "EWR": "Newark Liberty International",
}
DELAY_MIN = 30
CLIP = (-30.0, 240.0)
QUANTILES = (0.1, 0.5, 0.9)
MAX_CATEGORIES = 250
HOLDOUT_DAYS = 30
CALIBRATION_DAYS = 30
REFERENCE_DAYS = 14

DIRECTIONS = {
    # direction: (scheduled time, hub side, other side, delay minutes, label)
    "departure": (
        schema.SCHED_DEP_UTC,
        schema.ORIGIN,
        schema.DEST,
        schema.DEP_DELAY_MIN,
        schema.DEP_DELAYED_30,
    ),
    "arrival": (
        schema.SCHED_ARR_UTC,
        schema.DEST,
        schema.ORIGIN,
        schema.ARR_DELAY_MIN,
        schema.ARR_DELAYED_30,
    ),
}
CATEGORICAL = ("hub", "carrier", "other")
NUMERIC = (
    "local_hour",
    "day_of_week",
    "month",
    "days_to_holiday",
    "block_min",
    "distance_mi",
    "hub_prev_60",
    "hub_next_60",
    "hub_day_total",
    "carrier_hub_day",
)
FEATURES = (*CATEGORICAL, *NUMERIC)


# ---------------------------------------------------------------- features


def _holidays(start: pd.Timestamp, end: pd.Timestamp) -> np.ndarray:
    span = USFederalHolidayCalendar().holidays(
        start - pd.Timedelta(days=60), end + pd.Timedelta(days=60)
    )
    return span.values.astype("datetime64[D]")


def _window_counts(times: np.ndarray, minutes: int) -> tuple[np.ndarray, np.ndarray]:
    """Flights strictly before / after each time within ``minutes``."""
    order = np.argsort(times, kind="stable")
    ordered = times[order]
    width = np.timedelta64(minutes, "m")
    before = np.searchsorted(ordered, ordered, "left") - np.searchsorted(
        ordered, ordered - width, "left"
    )
    after = np.searchsorted(ordered, ordered + width, "right") - np.searchsorted(
        ordered, ordered, "right"
    )
    prev, nxt = np.empty_like(before), np.empty_like(after)
    prev[order], nxt[order] = before, after
    return prev, nxt


def direction_frame(flights: pd.DataFrame, direction: str) -> pd.DataFrame:
    """Rows of one direction with the hub, local time and schedule fields."""
    time_col, hub_col, other_col, _, _ = DIRECTIONS[direction]
    frame = flights.loc[flights[hub_col].isin(NYC)].copy()
    frame["scheduled_utc"] = pd.to_datetime(frame[time_col], utc=True)
    frame = frame.loc[frame["scheduled_utc"].notna()]
    frame["hub"] = frame[hub_col].astype("string")
    frame["other"] = frame[other_col].astype("string")
    frame["local"] = frame["scheduled_utc"].dt.tz_convert(TIMEZONE)
    frame["local_date"] = frame["local"].dt.tz_localize(None).dt.normalize()
    return frame.reset_index(drop=True)


def build_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Schedule-only features for rows from :func:`direction_frame`."""
    local = frame["local"]
    out = pd.DataFrame(index=frame.index)
    out["hub"] = frame["hub"].astype(str)
    out["carrier"] = frame[schema.CARRIER].astype(str)
    out["other"] = frame["other"].astype(str)
    out["local_hour"] = local.dt.hour + local.dt.minute / 60.0
    out["day_of_week"] = local.dt.dayofweek
    out["month"] = local.dt.month
    days = frame["local_date"].values.astype("datetime64[D]")
    holidays = _holidays(frame["local_date"].min(), frame["local_date"].max())
    gaps = np.abs(days[:, None] - holidays[None, :]).astype("timedelta64[D]")
    out["days_to_holiday"] = np.minimum(gaps.min(axis=1).astype(int), 30)
    out["block_min"] = pd.to_numeric(frame[schema.SCHED_BLOCK_MIN], errors="coerce")
    out["distance_mi"] = pd.to_numeric(frame[schema.DISTANCE_MI], errors="coerce")
    prev = np.zeros(len(frame), dtype=int)
    nxt = np.zeros(len(frame), dtype=int)
    times = frame["scheduled_utc"].dt.tz_localize(None).values.astype("datetime64[m]")
    for index in frame.groupby("hub").indices.values():
        p, n = _window_counts(times[index], 60)
        prev[index], nxt[index] = p, n
    out["hub_prev_60"], out["hub_next_60"] = prev, nxt
    out["hub_day_total"] = frame.groupby(["hub", "local_date"])["hub"].transform("size")
    out["carrier_hub_day"] = frame.groupby(["hub", "local_date", schema.CARRIER])[
        "hub"
    ].transform("size")
    return out


def _encode(features: pd.DataFrame, categories: dict[str, list[str]]) -> pd.DataFrame:
    encoded = features.copy()
    for column in CATEGORICAL:
        # Categories unseen in training become missing, which the trees handle.
        encoded[column] = pd.Categorical(
            encoded[column].astype(str), categories=categories[column]
        )
    return encoded.loc[:, list(FEATURES)]


def _categories(features: pd.DataFrame) -> dict[str, list[str]]:
    result = {}
    for column in CATEGORICAL:
        counts = features[column].astype(str).value_counts()
        result[column] = sorted(counts.index[:MAX_CATEGORIES])
    return result


# ------------------------------------------------------------------- model


@dataclass
class DelayModel:
    """A calibrated delay classifier and delay-minute quantile regressors."""

    direction: str
    categories: dict[str, list[str]]
    classifier: HistGradientBoostingClassifier
    calibrator: IsotonicRegression
    regressors: dict[str, dict[float, HistGradientBoostingRegressor]]
    metrics: dict[str, Any] = field(default_factory=dict)

    def predict(self, features: pd.DataFrame, threshold: float = 0.5) -> pd.DataFrame:
        x = _encode(features, self.categories)
        raw = self.classifier.predict_proba(x)[:, 1]
        probability = np.clip(self.calibrator.predict(raw), 0.0, 1.0)
        late = probability >= threshold
        minutes = np.empty((len(x), len(QUANTILES)))
        for group, rows in (("late", late), ("on_time", ~late)):
            if rows.any():
                minutes[rows] = np.column_stack(
                    [self.regressors[group][a].predict(x[rows]) for a in QUANTILES]
                )
        minutes = np.sort(minutes, axis=1)  # quantiles must not cross
        # Keep the minutes on the side of the call they describe.
        cut = DELAY_MIN
        minutes[late] = np.maximum(minutes[late], cut)
        minutes[~late] = np.minimum(minutes[~late], cut - 1)
        return pd.DataFrame(
            {
                "delay_probability": probability,
                "delay_low_min": minutes[:, 0],
                "predicted_delay_min": minutes[:, 1],
                "delay_high_min": minutes[:, 2],
            },
            index=features.index,
        )


def _classifier() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        categorical_features="from_dtype",
        learning_rate=0.06,
        max_iter=350,
        max_leaf_nodes=31,
        min_samples_leaf=80,
        l2_regularization=1.0,
        early_stopping=False,
        random_state=0,
    )


def _regressor(alpha: float) -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        loss="quantile",
        quantile=alpha,
        categorical_features="from_dtype",
        learning_rate=0.06,
        max_iter=250,
        max_leaf_nodes=31,
        min_samples_leaf=80,
        early_stopping=False,
        random_state=0,
    )


def labelled(flights: pd.DataFrame, direction: str) -> tuple[pd.DataFrame, ...]:
    """Features, label and delay minutes for flights that operated."""
    _, _, _, delay_col, label_col = DIRECTIONS[direction]
    frame = direction_frame(flights, direction)
    features = build_features(frame)
    keep = frame[label_col].notna() & frame[delay_col].notna()
    label = frame.loc[keep, label_col].astype(bool).astype(int)
    minutes = frame.loc[keep, delay_col].astype(float).clip(*CLIP)
    return frame.loc[keep], features.loc[keep], label, minutes


def fit(
    features: pd.DataFrame,
    label: pd.Series,
    minutes: pd.Series,
    calibration: pd.Series,
    direction: str,
) -> DelayModel:
    """Fit on rows where ``calibration`` is false; calibrate on the rest."""
    categories = _categories(features.loc[~calibration])
    x = _encode(features, categories)
    fit_rows, cal_rows = ~calibration.to_numpy(), calibration.to_numpy()
    if label[fit_rows].nunique() < 2 or cal_rows.sum() == 0:
        raise ValueError("Need both outcomes for fitting and rows to calibrate.")
    classifier = _classifier().fit(x[fit_rows], label[fit_rows])
    raw = classifier.predict_proba(x[cal_rows])[:, 1]
    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    calibrator.fit(raw, label[cal_rows])
    regressors = {}
    for group, outcome in (("late", 1), ("on_time", 0)):
        rows = fit_rows & (label.to_numpy() == outcome)
        regressors[group] = {
            a: _regressor(a).fit(x[rows], minutes[rows]) for a in QUANTILES
        }
    return DelayModel(direction, categories, classifier, calibrator, regressors)


def _split_days(dates: pd.Series, days: int) -> pd.Series:
    return dates >= dates.max() - pd.Timedelta(days=days - 1)


def evaluate(flights: pd.DataFrame, direction: str) -> dict[str, Any]:
    """Train on older data and score the final ``HOLDOUT_DAYS`` days."""
    frame, features, label, minutes = labelled(flights, direction)
    dates = frame["local_date"]
    holdout = _split_days(dates, HOLDOUT_DAYS)
    development = ~holdout
    calibration = _split_days(dates[development], CALIBRATION_DAYS)
    model = fit(
        features[development],
        label[development],
        minutes[development],
        calibration,
        direction,
    )
    predicted = model.predict(features[holdout])
    p = predicted["delay_probability"].to_numpy()
    y = label[holdout].to_numpy()
    actual = minutes[holdout].to_numpy()
    scorecard = probability_scorecard(
        y, p, threshold=0.5, naive_probability=float(label[development].mean())
    )
    median = predicted["predicted_delay_min"].to_numpy()
    low, high = predicted["delay_low_min"], predicted["delay_high_min"]
    inside = (actual >= low.to_numpy()) & (actual <= high.to_numpy())
    correct = (p >= 0.5) == (y == 1)
    bins = pd.cut(p, [0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0], include_lowest=True)
    calibration_table = [
        {
            "bin": str(b),
            "mean_probability": round(float(p[bins == b].mean()), 3),
            "delay_rate": round(float(y[bins == b].mean()), 3),
            "flights": int((bins == b).sum()),
        }
        for b in bins.categories
        if (bins == b).sum()
    ]
    scorecard.update(
        {
            "direction": direction,
            "test_rows": int(holdout.sum()),
            "train_rows": int(development.sum()),
            "test_start": str(dates[holdout].min().date()),
            "test_end": str(dates[holdout].max().date()),
            "delay_rate": float(y.mean()),
            "mae_minutes": float(np.mean(np.abs(actual - median))),
            "mae_minutes_predict_zero": float(np.mean(np.abs(actual))),
            "within_15_min": float(np.mean(np.abs(actual - median) <= 15)),
            "range_80_coverage": float(np.mean(inside)),
            # Each range describes flights that went the way of the call, so it
            # is scored on the flights the call got right.
            "range_80_coverage_when_correct": float(np.mean(inside[correct]))
            if correct.any()
            else None,
            "recall_at_0.3": float(np.mean(p[y == 1] >= 0.3)) if y.any() else None,
            "calibration": calibration_table,
        }
    )
    return {k: v for k, v in scorecard.items() if not isinstance(v, np.ndarray)}


def train(flights: pd.DataFrame, direction: str) -> DelayModel:
    """Evaluate on a holdout, then refit on all data for publishing."""
    metrics = evaluate(flights, direction)
    frame, features, label, minutes = labelled(flights, direction)
    calibration = _split_days(frame["local_date"], CALIBRATION_DAYS)
    model = fit(features, label, minutes, calibration, direction)
    model.metrics = metrics
    return model


# ---------------------------------------------------------- stand-in schedule


SCHEDULE_COLUMNS = (
    schema.CARRIER,
    schema.FLIGHT_NUMBER,
    schema.ORIGIN,
    schema.DEST,
    schema.SCHED_DEP_UTC,
    schema.SCHED_ARR_UTC,
    schema.SCHED_BLOCK_MIN,
    schema.DISTANCE_MI,
)


def schedule_reference(flights: pd.DataFrame) -> pd.DataFrame:
    """The last two weeks of New York flights, cancelled ones included."""
    frame = flights.loc[
        flights[schema.ORIGIN].isin(NYC) | flights[schema.DEST].isin(NYC),
        list(SCHEDULE_COLUMNS),
    ].copy()
    start = pd.to_datetime(frame[schema.SCHED_DEP_UTC], utc=True).max() - pd.Timedelta(
        days=REFERENCE_DAYS
    )
    return frame.loc[pd.to_datetime(frame[schema.SCHED_DEP_UTC], utc=True) >= start]


def stand_in(
    reference: pd.DataFrame, direction: str, target: date
) -> tuple[pd.DataFrame, date | None]:
    """Copy the latest same-weekday flights onto ``target`` at the same local time."""
    frame = direction_frame(reference, direction)
    # The first and last days of the record are cut off by the UTC window
    # (a late-evening flight in New York is already the next day in UTC), so
    # only whole days are copied.
    edges = {frame["local_date"].min(), frame["local_date"].max()}
    frame = frame.loc[~frame["local_date"].isin(edges)]
    weekday = frame["local_date"].dt.dayofweek == target.weekday()
    if not weekday.any():
        return frame.iloc[0:0], None
    source_day = frame.loc[weekday, "local_date"].max()
    rows = frame.loc[frame["local_date"] == source_day].copy()
    clock = rows["local"].dt.tz_localize(None) - rows["local_date"]
    local = (pd.Timestamp(target) + clock).dt.tz_localize(
        TIMEZONE, nonexistent="shift_forward", ambiguous=False
    )
    rows["scheduled_utc"] = local.dt.tz_convert("UTC")
    rows["local"] = local
    rows["local_date"] = pd.Timestamp(target)
    block = pd.to_timedelta(rows[schema.SCHED_BLOCK_MIN].astype(float), unit="min")
    if direction == "departure":
        rows[schema.SCHED_DEP_UTC] = rows["scheduled_utc"]
        rows[schema.SCHED_ARR_UTC] = rows["scheduled_utc"] + block
    else:
        rows[schema.SCHED_ARR_UTC] = rows["scheduled_utc"]
        rows[schema.SCHED_DEP_UTC] = rows["scheduled_utc"] - block
    return rows.reset_index(drop=True), source_day.date()


# ----------------------------------------------------------------- publish


def _iso(value: pd.Timestamp) -> str:
    return value.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def predict_days(
    models: dict[str, DelayModel],
    reference: pd.DataFrame,
    targets: list[date],
    *,
    threshold: float = 0.5,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """Score stand-in schedules for each target date and direction."""
    flights: list[dict[str, Any]] = []
    sources: dict[str, dict[str, str]] = {}
    for target in targets:
        for direction, model in models.items():
            rows, source_day = stand_in(reference, direction, target)
            if rows.empty:
                continue
            sources.setdefault(target.isoformat(), {})[direction] = str(source_day)
            result = model.predict(build_features(rows))
            for row, pred in zip(
                rows.to_dict("records"), result.to_dict("records"), strict=True
            ):
                p = float(pred["delay_probability"])
                delayed = p >= threshold
                median = round(float(pred["predicted_delay_min"]))
                flights.append(
                    {
                        "direction": direction,
                        "airport": str(row["hub"]),
                        "carrier": str(row[schema.CARRIER]),
                        "flight": f"{row[schema.CARRIER]}{row[schema.FLIGHT_NUMBER]}",
                        "origin": str(row[schema.ORIGIN]),
                        "dest": str(row[schema.DEST]),
                        "scheduled_utc": _iso(row["scheduled_utc"]),
                        "prediction": "delayed" if delayed else "on_time",
                        "delay_probability": round(p, 4),
                        "confidence": round(p if delayed else 1 - p, 4),
                        "predicted_delay_min": median,
                        "delay_low_min": round(float(pred["delay_low_min"])),
                        "delay_high_min": round(float(pred["delay_high_min"])),
                        "estimated_utc": _iso(
                            row["scheduled_utc"] + pd.Timedelta(minutes=max(median, 0))
                        ),
                    }
                )
    flights.sort(key=lambda f: (f["scheduled_utc"], f["direction"], f["flight"]))
    return flights, sources


def payload(
    models: dict[str, DelayModel],
    flights: list[dict[str, Any]],
    sources: dict[str, dict[str, str]],
    *,
    threshold: float = 0.5,
    generated_at: datetime | None = None,
    trained_at: str | None = None,
) -> dict[str, Any]:
    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    validation_keys = (
        "accuracy",
        "precision",
        "recall",
        "f1",
        "brier_score",
        "naive_brier_score",
        "pr_auc",
        "test_rows",
        "test_start",
        "test_end",
        "delay_rate",
        "mae_minutes",
        "mae_minutes_predict_zero",
        "within_15_min",
        "range_80_coverage",
        "range_80_coverage_when_correct",
    )
    return {
        "schema_version": 3,
        "status": "published" if flights else "no_flights",
        "sample": False,
        "generated_at": generated.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stale_after_minutes": 36 * 60,
        "schedule": {
            "basis": "typical",
            "source_dates": sources,
            "note": (
                "Flights and times are a stand-in schedule copied from the latest "
                "same-weekday flights in BTS records; real schedules can differ."
            ),
        },
        "models": {
            direction: (
                {
                    "name": f"nyc_delay_{direction}",
                    "target": f"{direction.title()} {DELAY_MIN}+ minutes late",
                    "threshold": threshold,
                    "horizon_hours": None,
                    "trained_at": trained_at,
                    "validation": {
                        k: model.metrics[k]
                        for k in validation_keys
                        if model.metrics.get(k) is not None
                    },
                }
                if (model := models.get(direction))
                else None
            )
            for direction in DIRECTIONS
        },
        "airports": NAMES,
        "flights": flights,
    }


# --------------------------------------------------------------- prepare data


def bts_available(url: str) -> bool:
    import requests

    try:
        response = requests.head(url, timeout=60, allow_redirects=True)
    except requests.RequestException:
        return False
    return response.status_code == 200


def latest_bts_month(template: str, today: date, lookback: int = 8) -> pd.Period:
    month = pd.Period(today, freq="M") - 1
    for _ in range(lookback):
        if bts_available(template.format(year=month.year, month=month.month)):
            return month
        month -= 1
    raise RuntimeError("No BTS month found in the lookback window.")


def prepare(work: Path, months: int, config_path: Path = DEFAULT_CONFIG) -> Path:
    """Download and clean the last ``months`` BTS months for New York."""
    config = json.loads(config_path.read_text())
    latest = latest_bts_month(config["bts"]["url_template"], datetime.now(UTC).date())
    first = latest - (months - 1)
    config["window"] = {
        "start_utc": f"{first.start_time.date()}T00:00:00Z",
        "end_utc": f"{(latest + 1).start_time.date()}T00:00:00Z",
    }
    config["hubs"]["airports"] = [
        a for a in config["hubs"]["airports"] if a["iata"] in NYC
    ]
    work.mkdir(parents=True, exist_ok=True)
    nyc_config = work / "sources_nyc.json"
    nyc_config.write_text(json.dumps(config, indent=1))
    raw, processed = work / "raw", work / "processed"
    print(f"BTS months {first} to {latest}", flush=True)
    for command in (
        ["skydelay.pipeline.ingest", "--source", "bts", "--output-dir", str(raw)],
        [
            "skydelay.pipeline.clean",
            "--source",
            "bts",
            "--raw-dir",
            str(raw),
            "--output-dir",
            str(processed),
        ],
    ):
        subprocess.run(
            [sys.executable, "-m", *command, "--config", str(nyc_config)], check=True
        )
    return processed / "flights.parquet"


# --------------------------------------------------------------------- CLI


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_prep = sub.add_parser("prepare", help="download and clean BTS data")
    p_prep.add_argument("--work", type=Path, required=True)
    p_prep.add_argument("--months", type=int, default=12)
    p_prep.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p_train = sub.add_parser("train", help="fit and evaluate both directions")
    p_train.add_argument("--flights", type=Path, required=True)
    p_train.add_argument("--output", type=Path, required=True)
    p_pred = sub.add_parser("predict", help="publish predictions.json")
    p_pred.add_argument("--artifacts", type=Path, required=True)
    p_pred.add_argument("--output", type=Path, required=True)
    p_pred.add_argument("--days", type=int, default=2)
    p_pred.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    if args.command == "prepare":
        print(prepare(args.work, args.months, args.config))
    elif args.command == "train":
        flights = pd.read_parquet(args.flights)
        args.output.mkdir(parents=True, exist_ok=True)
        summary: dict[str, Any] = {"trained_at": datetime.now(UTC).isoformat()}
        for direction in DIRECTIONS:
            model = train(flights, direction)
            joblib.dump(model, args.output / f"{direction}.joblib", compress=3)
            summary[direction] = model.metrics
            print(json.dumps({direction: model.metrics}, indent=1, default=str))
        schedule_reference(flights).to_parquet(args.output / "schedule.parquet")
        (args.output / "metrics.json").write_text(
            json.dumps(summary, indent=1, default=str) + "\n"
        )
    else:
        models = {
            d: joblib.load(args.artifacts / f"{d}.joblib")
            for d in DIRECTIONS
            if (args.artifacts / f"{d}.joblib").exists()
        }
        if not models:
            raise SystemExit(f"No models in {args.artifacts}")
        reference = pd.read_parquet(args.artifacts / "schedule.parquet")
        today = pd.Timestamp.now(TIMEZONE).date()
        targets = [today + timedelta(days=i) for i in range(args.days)]
        flights, sources = predict_days(
            models, reference, targets, threshold=args.threshold
        )
        trained_at = None
        metrics_path = args.artifacts / "metrics.json"
        if metrics_path.exists():
            trained_at = json.loads(metrics_path.read_text()).get("trained_at")
        body = payload(
            models,
            flights,
            sources,
            threshold=args.threshold,
            trained_at=trained_at,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(body, separators=(",", ":")) + "\n")
        counts = pd.Series([f["prediction"] for f in flights]).value_counts()
        print(f"Wrote {len(flights)} predictions to {args.output}: {counts.to_dict()}")


if __name__ == "__main__":
    main()
