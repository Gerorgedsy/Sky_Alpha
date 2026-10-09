import io
import zipfile

import pandas as pd
import pytest

from skydelay.pipeline.ingest import window
from skydelay.pipeline.ingest_bts import months, read_month
from skydelay.pipeline.ingest_hrrr import GribField, cycles, parse_index
from skydelay.pipeline.ingest_opensky import (
    STATE_FIELDS,
    OpenSkyAuth,
    OpenSkyStatesCollector,
    flights_frame,
    intervals,
    states_frame,
)


def utc(text):
    return pd.Timestamp(text, tz="UTC")


# --- BTS -------------------------------------------------------------------


def test_months_cover_the_half_open_window():
    got = [str(m) for m in months(utc("2024-01-15"), utc("2024-03-01"))]
    assert got == ["2024-01", "2024-02"]


def test_read_month_keeps_hub_flights_and_configured_columns():
    csv = (
        "Year,FlightDate,Origin,Dest,Tail_Number,Extra\n"
        "2024,2024-07-01,ATL,LGA,N1,x\n"
        "2024,2024-07-01,PDX,CHA,N2,x\n"
        "2024,2024-07-01,PDX,JFK,N03,x\n"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("On_Time_2024_7.csv", csv)
        archive.writestr("readme.html", "<html/>")
    frame = read_month(
        buffer.getvalue(),
        ["FlightDate", "Origin", "Dest", "Tail_Number"],
        frozenset({"ATL", "JFK", "LGA"}),
    )
    assert list(frame.columns) == ["FlightDate", "Origin", "Dest", "Tail_Number"]
    assert frame["Tail_Number"].tolist() == ["N1", "N03"]


def test_read_month_names_missing_columns():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("x.csv", "Origin,Dest\nATL,LGA\n")
    with pytest.raises(KeyError, match="TaxiOut"):
        read_month(buffer.getvalue(), ["Origin", "Dest", "TaxiOut"], frozenset({"ATL"}))


# --- HRRR ------------------------------------------------------------------

INDEX = (
    "1:0:d=2024070112:REFC:entire atmosphere:1 hour fcst:\n"
    "2:500:d=2024070112:VIS:surface:1 hour fcst:\n"
    "3:900:d=2024070112:APCP:surface:0-1 hour acc fcst:\n"
    "4:1500:d=2024070112:APCP:surface:1-1 hour acc fcst:\n"
    "5:2000:d=2024070112:TMP:2 m above ground:1 hour fcst:\n"
)


def test_parse_index_gives_each_message_its_byte_range():
    entries = parse_index(INDEX)
    assert entries[GribField("VIS", "surface").key].byte_range == "500-899"
    # The last message runs to the end of the file.
    assert entries["TMP:2 m above ground"].byte_range == "2000-"
    # A repeated key keeps its first occurrence.
    assert entries["APCP:surface"].byte_range == "900-1499"


def test_cycles_fall_inside_the_window_at_configured_hours():
    got = list(cycles(utc("2024-07-01 05:00"), utc("2024-07-02 06:00"), [0, 6, 12, 18]))
    assert [t.strftime("%d %H") for t in got] == ["01 06", "01 12", "01 18", "02 00"]


# --- OpenSky ---------------------------------------------------------------


def test_intervals_respect_the_two_day_limit():
    pieces = list(intervals(utc("2024-07-01"), utc("2024-07-03 12:00"), 24))
    assert [(p.begin.day, p.end.day, p.end.hour) for p in pieces] == [
        (1, 2, 0),
        (2, 3, 0),
        (3, 3, 12),
    ]
    with pytest.raises(ValueError):
        list(intervals(utc("2024-07-01"), utc("2024-07-05"), 72))


def test_states_frame_pads_vectors_without_a_category():
    payload = {
        "time": 1719835200,
        "states": [
            [
                "abc123",
                "DAL100  ",
                "United States",
                1719835195,
                1719835199,
                -84.43,
                33.64,
                0.0,
                True,
                5.0,
                90.0,
                0.0,
                None,
                10.0,
                "1200",
                False,
                0,
            ]
        ],
    }
    frame = states_frame(payload)
    assert list(frame.columns) == ["snapshot_time", *STATE_FIELDS]
    assert frame.loc[0, "snapshot_time"] == 1719835200
    assert pd.isna(frame.loc[0, "category"])


def test_empty_flights_response_still_has_every_column():
    assert len(flights_frame([]).columns) == 12
    assert flights_frame(None).empty


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self):
        self.posts = 0
        self.gets = []

    def post(self, url, data, timeout):
        self.posts += 1
        return FakeResponse(body={"access_token": f"t{self.posts}", "expires_in": 1800})

    def get(self, url, params=None, headers=None, timeout=None):
        self.gets.append(headers)
        return FakeResponse(body={"time": 1719835200 + len(self.gets), "states": []})


def test_auth_reuses_a_token_until_it_nears_expiry():
    session = FakeSession()
    auth = OpenSkyAuth("https://token", "id", "secret", session)
    assert auth.headers() == {"Authorization": "Bearer t1"}
    assert auth.headers() == {"Authorization": "Bearer t1"}
    auth._expires_at = 0
    assert auth.headers() == {"Authorization": "Bearer t2"}


def test_anonymous_auth_sends_no_header():
    assert OpenSkyAuth("https://token", None, None, FakeSession()).headers() == {}


def test_collector_writes_one_file_per_snapshot(tmp_path, config):
    session = FakeSession()
    auth = OpenSkyAuth("https://token", None, None, session)
    collector = OpenSkyStatesCollector(config["opensky"], tmp_path, session, auth)
    paths = collector.collect(3, sleep=lambda seconds: None)
    assert len(paths) == 3 and all(p.exists() for p in paths)
    assert len({p.name for p in paths}) == 3


def test_window_can_narrow_but_never_widen(config):
    first, last = window(config, "2023-06-01", "2024-02-01")
    assert first == utc("2024-01-01")
    assert last == utc("2024-02-01")
    with pytest.raises(ValueError):
        window(config, "2024-06-01", "2024-05-01")
