import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from skydelay.features.schedule import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    build_schedule_features,
)
from skydelay.models.publish_predictions import (
    build_payload,
    load_validation,
    score_schedule,
)
from skydelay.models.train_baseline import artifact_name, train_and_evaluate
from skydelay.pipeline import schema

SITE_DATA = Path(__file__).resolve().parents[2] / "prototype" / "data"


class FixedModel:
    """Returns a delay probability chosen per route."""

    feature_names_in_ = np.array([*CATEGORICAL_FEATURES, *NUMERIC_FEATURES])

    def __init__(self, by_route):
        self.by_route = by_route

    def predict_proba(self, frame):
        assert list(frame.columns) == list(self.feature_names_in_)
        p = frame["route"].map(self.by_route).fillna(0.2).to_numpy(dtype=float)
        return np.column_stack([1 - p, p])


def schedule():
    return pd.DataFrame(
        {
            schema.CARRIER: ["dl", "B6", "UA", "AA"],
            schema.FLIGHT_NUMBER: [100, 200, 300, 400],
            schema.ORIGIN: ["LGA", "JFK", "EWR", "ATL"],
            schema.DEST: ["ORD", "BOS", "SFO", "JFK"],
            schema.SCHED_DEP_UTC: [
                "2026-11-16T20:00:00Z",
                "2026-11-16T18:30:00Z",
                "2026-11-16T19:00:00Z",
                "2026-11-16T19:00:00Z",
            ],
            schema.SCHED_BLOCK_MIN: [150, 75, 380, 130],
            schema.DISTANCE_MI: [733, 187, 2565, 760],
        }
    )


ROUTES = {"LGA-ORD": 0.8, "JFK-BOS": 0.1, "EWR-SFO": 0.5, "ATL-JFK": 0.7}


def test_scores_only_new_york_departures_and_keeps_identity():
    scored = score_schedule(FixedModel(ROUTES), schedule())
    assert sorted(scored[schema.ORIGIN]) == ["EWR", "JFK", "LGA"]
    by_flight = scored.set_index(schema.FLIGHT_NUMBER)
    assert by_flight.loc[100, "prediction"] == "delayed"
    assert by_flight.loc[200, "prediction"] == "on_time"
    # The threshold is inclusive.
    assert by_flight.loc[300, "prediction"] == "delayed"
    assert by_flight.loc[100, schema.DEST] == "ORD"


def test_arrivals_use_the_destination_hub_and_derived_arrival_time():
    scored = score_schedule(FixedModel(ROUTES), schedule(), direction="arrival")
    # Only AA400 lands at a New York airport; its arrival is departure + block.
    assert list(scored[schema.FLIGHT_NUMBER]) == [400]
    assert scored.loc[0, schema.SCHED_ARR_UTC] == pd.Timestamp("2026-11-16T21:10:00Z")
    assert scored.loc[0, "prediction"] == "delayed"


def test_payload_holds_both_directions_with_confidence_of_the_call():
    model = FixedModel(ROUTES)
    payload = build_payload(
        {
            "departure": score_schedule(model, schedule()),
            "arrival": score_schedule(model, schedule(), direction="arrival"),
        },
        model_names={"departure": "dep", "arrival": "arr"},
        threshold=0.5,
        horizon_hours=2,
        stale_after_minutes=180,
        validation={"arrival": {"accuracy": 0.7}},
        generated_at=datetime(2026, 11, 16, 16, tzinfo=UTC),
    )
    flights = {(f["direction"], f["flight"]): f for f in payload["flights"]}
    assert payload["status"] == "published"
    assert payload["generated_at"] == "2026-11-16T16:00:00Z"
    assert payload["models"]["arrival"]["validation"] == {"accuracy": 0.7}
    assert payload["models"]["arrival"]["target"].startswith("Arrival")
    assert flights[("departure", "DL100")]["confidence"] == pytest.approx(0.8)
    assert flights[("departure", "B6200")]["confidence"] == pytest.approx(0.9)
    arrival = flights[("arrival", "AA400")]
    assert arrival["airport"] == "JFK"
    assert arrival["origin"] == "ATL"
    assert arrival["scheduled_utc"] == "2026-11-16T21:10:00Z"
    assert [f["flight"] for f in payload["flights"]] == [
        "B6200",
        "UA300",
        "DL100",
        "AA400",
    ]


def test_direction_without_a_model_is_marked_unpublished():
    payload = build_payload(
        {"departure": score_schedule(FixedModel(ROUTES), schedule().iloc[[3]])},
        model_names={},
        threshold=0.5,
        horizon_hours=2,
        stale_after_minutes=60,
    )
    assert payload["status"] == "no_flights"
    assert payload["models"]["arrival"] is None
    assert payload["models"]["departure"] is not None


def test_missing_schedule_column_is_reported():
    with pytest.raises(KeyError, match="distance_mi"):
        score_schedule(FixedModel({}), schedule().drop(columns=[schema.DISTANCE_MI]))


def test_arrival_features_key_on_scheduled_arrivals_at_the_hub():
    times = pd.date_range("2024-07-01 12:00", periods=4, freq="10min", tz="UTC")
    flights = pd.DataFrame(
        {
            schema.SCHED_ARR_UTC: times,
            schema.ORIGIN: ["ATL", "ORD", "BOS", "MIA"],
            schema.DEST: ["JFK"] * 4,
            schema.CARRIER: ["DL"] * 4,
            schema.SCHED_BLOCK_MIN: [120] * 4,
            schema.DISTANCE_MI: [760] * 4,
            schema.ARR_DELAYED_30: [False, True, False, True],
        }
    )
    table = build_schedule_features(flights, "arrival")
    assert set(table["hub"]) == {"JFK"}
    assert list(table["scheduled_departures_30m"]) == [0, 1, 2, 3]
    assert schema.ARR_DELAYED_30 in table.columns


def test_arrival_baseline_trains_on_arrival_labels():
    rows = 80
    times = pd.date_range("2024-01-01", periods=rows, freq="12h", tz="UTC")
    flights = pd.DataFrame(
        {
            schema.SCHED_ARR_UTC: times,
            schema.ORIGIN: ["ATL", "ORD"] * (rows // 2),
            schema.DEST: ["JFK"] * rows,
            schema.CARRIER: ["DL"] * rows,
            schema.SCHED_BLOCK_MIN: [120] * rows,
            schema.DISTANCE_MI: [760] * rows,
            schema.ARR_DELAYED_30: [False, True] * (rows // 2),
        }
    )
    _, metrics = train_and_evaluate(flights, test_days=10, direction="arrival")
    assert metrics["target"] == schema.ARR_DELAYED_30
    assert artifact_name("baseline_1_schedule.joblib", "arrival") == (
        "baseline_1_schedule_arrival.joblib"
    )


def test_validation_reads_one_model_from_a_comparison(tmp_path):
    path = tmp_path / "scorecard.json"
    path.write_text(
        json.dumps({"baseline_2": {"accuracy": 0.8, "brier_score": 0.1, "x": 1}})
    )
    assert load_validation(path, "baseline_2") == {
        "accuracy": 0.8,
        "brier_score": 0.1,
    }
    with pytest.raises(ValueError, match="--metrics-key"):
        load_validation(path, None)


@pytest.mark.parametrize("name", ["predictions.json", "predictions.sample.json"])
def test_site_files_match_the_published_format(name):
    payload = json.loads((SITE_DATA / name).read_text())
    assert payload["schema_version"] in {2, 3}
    assert set(payload["airports"]) == {"JFK", "LGA", "EWR"}
    assert set(payload["models"]) == {"departure", "arrival"}
    for flight in payload["flights"]:
        assert flight["direction"] in {"departure", "arrival"}
        hub = flight["origin"] if flight["direction"] == "departure" else flight["dest"]
        assert flight["airport"] == hub
        assert flight["prediction"] in {"delayed", "on_time"}
        assert 0.5 <= flight["confidence"] <= 1.0
    if name == "predictions.json":
        # The public file must never carry sample data.
        assert payload["sample"] is False
