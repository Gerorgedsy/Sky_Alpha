import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from skydelay.features.schedule import CATEGORICAL_FEATURES, NUMERIC_FEATURES
from skydelay.models.publish_predictions import (
    build_payload,
    load_validation,
    score_schedule,
)
from skydelay.pipeline import schema

SITE_DATA = Path(__file__).resolve().parents[2] / "prototype" / "data"


class FixedModel:
    """Returns a delay probability chosen per destination."""

    feature_names_in_ = np.array([*CATEGORICAL_FEATURES, *NUMERIC_FEATURES])

    def __init__(self, by_route):
        self.by_route = by_route

    def predict_proba(self, frame):
        assert list(frame.columns) == list(self.feature_names_in_)
        p = frame["route"].map(self.by_route).to_numpy(dtype=float)
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


def test_scores_only_new_york_departures_and_keeps_identity():
    model = FixedModel({"LGA-ORD": 0.8, "JFK-BOS": 0.1, "EWR-SFO": 0.5})
    scored = score_schedule(model, schedule())
    assert sorted(scored[schema.ORIGIN]) == ["EWR", "JFK", "LGA"]
    by_flight = scored.set_index(schema.FLIGHT_NUMBER)
    assert by_flight.loc[100, "prediction"] == "delayed"
    assert by_flight.loc[200, "prediction"] == "on_time"
    # The threshold is inclusive.
    assert by_flight.loc[300, "prediction"] == "delayed"
    assert by_flight.loc[100, schema.DEST] == "ORD"


def test_payload_confidence_is_probability_of_the_call():
    model = FixedModel({"LGA-ORD": 0.8, "JFK-BOS": 0.1, "EWR-SFO": 0.5})
    payload = build_payload(
        score_schedule(model, schedule()),
        model_name="baseline_1",
        threshold=0.5,
        horizon_hours=2,
        stale_after_minutes=180,
        generated_at=datetime(2026, 11, 16, 16, tzinfo=UTC),
    )
    flights = {item["flight"]: item for item in payload["flights"]}
    assert payload["status"] == "published"
    assert payload["generated_at"] == "2026-11-16T16:00:00Z"
    assert flights["DL100"]["confidence"] == pytest.approx(0.8)
    assert flights["B6200"]["confidence"] == pytest.approx(0.9)
    assert flights["B6200"]["scheduled_dep_utc"] == "2026-11-16T18:30:00Z"
    assert [item["flight"] for item in payload["flights"]] == [
        "B6200",
        "UA300",
        "DL100",
    ]


def test_empty_schedule_publishes_no_flights_status():
    model = FixedModel({})
    frame = schedule().iloc[[3]]
    payload = build_payload(
        score_schedule(model, frame),
        model_name="m",
        threshold=0.5,
        horizon_hours=2,
        stale_after_minutes=60,
    )
    assert payload["status"] == "no_flights"
    assert payload["flights"] == []


def test_missing_schedule_column_is_reported():
    with pytest.raises(KeyError, match="distance_mi"):
        score_schedule(FixedModel({}), schedule().drop(columns=[schema.DISTANCE_MI]))


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
    assert payload["schema_version"] == 1
    assert set(payload["airports"]) == {"JFK", "LGA", "EWR"}
    for flight in payload["flights"]:
        assert flight["airport"] in payload["airports"]
        assert flight["prediction"] in {"delayed", "on_time"}
        assert 0.5 <= flight["confidence"] <= 1.0
    if name == "predictions.json":
        # The public file must never carry sample data.
        assert payload["sample"] is False
