import pandas as pd
import pytest

from skydelay.pipeline import schema
from skydelay.pipeline.clean_hrrr import clean as clean_hrrr
from skydelay.pipeline.clean_hrrr import wind_from_components
from skydelay.pipeline.clean_opensky import clean_flights, clean_states
from skydelay.pipeline.quality import ScreeningReport

WINDOW = (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC"))
T0 = 1719835200  # 2024-07-01 12:00 UTC


def flight_row(**overrides):
    row = {
        "icao24": "ABC123",
        "firstSeen": str(T0),
        "estDepartureAirport": "KATL",
        "lastSeen": str(T0 + 5400),
        "estArrivalAirport": "KLGA",
        "callsign": "DAL100  ",
    }
    row.update(overrides)
    return row


def test_flights_map_to_hubs_and_merge_departure_and_arrival_copies(registry):
    raw = pd.DataFrame(
        [
            flight_row(),
            flight_row(),
            flight_row(
                icao24="def456", estDepartureAirport="EGLL", estArrivalAirport=None
            ),
        ]
    )
    report = ScreeningReport()
    out = clean_flights(raw, registry, WINDOW, report)
    assert len(out) == 1
    row = out.iloc[0]
    assert (row[schema.DEPARTURE_HUB], row[schema.ARRIVAL_HUB]) == ("ATL", "LGA")
    assert row[schema.ICAO24] == "abc123"
    assert row[schema.CALLSIGN] == "DAL100"
    assert {"duplicate_flight", "no_hub"} <= set(report.frame()["rule"])


def state_row(**overrides):
    row = {
        "snapshot_time": str(T0),
        "icao24": "abc123",
        "callsign": "DAL100",
        "time_position": str(T0 - 5),
        "latitude": "33.64",
        "longitude": "-84.43",
        "baro_altitude": "",
        "on_ground": "True",
        "velocity": "5.0",
        "true_track": "90",
        "vertical_rate": "",
    }
    row.update(overrides)
    return row


def test_states_drop_stale_and_far_positions_and_assign_hubs(registry, config):
    raw = pd.DataFrame(
        [
            state_row(),
            state_row(icao24="old111", time_position=str(T0 - 600)),
            state_row(icao24="far222", latitude="38.5", longitude="-98.0"),
        ]
    )
    report = ScreeningReport()
    out = clean_states(raw, registry, config["opensky"], report)
    assert out[schema.ICAO24].tolist() == ["abc123"]
    assert out.iloc[0][schema.HUB] == "ATL"
    assert bool(out.iloc[0][schema.ON_GROUND])
    assert {"stale_position", "not_near_a_hub"} <= set(report.frame()["rule"])


@pytest.mark.parametrize(
    ("u", "v", "direction"),
    [(0.0, -5.0, 0.0), (5.0, 0.0, 270.0), (0.0, 5.0, 180.0), (-5.0, 0.0, 90.0)],
)
def test_wind_direction_is_where_the_wind_comes_from(u, v, direction):
    speed, got = wind_from_components(pd.Series([u]), pd.Series([v]))
    assert float(speed.iloc[0]) == pytest.approx(5.0)
    assert float(got.iloc[0]) == pytest.approx(direction)


def test_hrrr_units_clocks_and_unlimited_ceiling(config):
    raw = pd.DataFrame(
        [
            {
                "init_time": "2024-07-01T12:00:00+00:00",
                "lead_hours": "4",
                "hub": "ATL",
                "REFC": "45",
                "VIS": "16000",
                "GUST": "12",
                "TMP": "303.15",
                "UGRD": "3",
                "VGRD": "4",
                "PRATE": "0.001",
                "HGT": "25000",
            }
        ]
    )
    report = ScreeningReport()
    out = clean_hrrr(raw, config["hrrr"], WINDOW, report)
    row = out.iloc[0]
    assert row[schema.VALID_TIME_UTC] == pd.Timestamp("2024-07-01 16:00", tz="UTC")
    assert row[schema.AVAILABLE_AT_UTC] == pd.Timestamp("2024-07-01 13:00", tz="UTC")
    assert row[schema.TEMPERATURE_C] == pytest.approx(30.0)
    assert row[schema.WIND_SPEED_MS] == pytest.approx(5.0)
    assert row[schema.PRECIP_MM_H] == pytest.approx(3.6)
    assert pd.isna(row[schema.CEILING_M]) and row["ceiling_unlimited"]


def test_hrrr_masks_impossible_values(config):
    raw = pd.DataFrame(
        [
            {
                "init_time": "2024-07-01T12:00:00Z",
                "lead_hours": "0",
                "hub": "ATL",
                "TMP": "500",
            }
        ]
    )
    report = ScreeningReport()
    out = clean_hrrr(raw, config["hrrr"], WINDOW, report)
    assert pd.isna(out.iloc[0][schema.TEMPERATURE_C])
    assert "range" in report.frame()["rule"].tolist()
