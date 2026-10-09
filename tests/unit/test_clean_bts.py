from pathlib import Path

import pandas as pd

from skydelay.pipeline import schema
from skydelay.pipeline.airports import AirportRegistry
from skydelay.pipeline.clean_bts import clean, hhmm_minutes
from skydelay.pipeline.quality import ScreeningReport

REGISTRY = AirportRegistry.from_path(
    Path(__file__).resolve().parents[2] / "config" / "sources.json"
)

WINDOW = (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC"))


def flight(**overrides):
    row = {
        "FlightDate": "2024-07-01",
        "Reporting_Airline": "DL",
        "Tail_Number": "N123DL",
        "Flight_Number_Reporting_Airline": "100",
        "Origin": "ATL",
        "Dest": "LGA",
        "CRSDepTime": "0800",
        "DepTime": "0815",
        "DepDelay": "15",
        "DepDel15": "1",
        "TaxiOut": "18",
        "WheelsOff": "0833",
        "WheelsOn": "1005",
        "TaxiIn": "7",
        "CRSArrTime": "1015",
        "ArrTime": "1012",
        "ArrDelay": "-3",
        "ArrDel15": "0",
        "Cancelled": "0.00",
        "CancellationCode": None,
        "Diverted": "0.00",
        "CRSElapsedTime": "135",
        "ActualElapsedTime": "117",
        "Distance": "762",
        "CarrierDelay": None,
        "WeatherDelay": None,
        "NASDelay": None,
        "SecurityDelay": None,
        "LateAircraftDelay": None,
    }
    row.update(overrides)
    return row


def run(*rows):
    report = ScreeningReport()
    out = clean(pd.DataFrame(list(rows)), REGISTRY, WINDOW, report)
    return out, report.frame()


def utc(text):
    return pd.Timestamp(text, tz="UTC")


def test_hhmm_handles_padding_floats_and_midnight():
    minutes = hhmm_minutes(
        pd.Series(["0545", "545", "545.0", "2400", "0000", "2460", None])
    )
    assert minutes.tolist()[:5] == [345, 345, 345, 1440, 0]
    assert minutes.iloc[5:].isna().all()


def test_hub_origin_times_convert_from_local_to_utc():
    out, _ = run(flight())
    row = out.iloc[0]
    # 08:00 EDT is 12:00 UTC; arrival is departure plus the 135-minute block.
    assert row[schema.SCHED_DEP_UTC] == utc("2024-07-01 12:00")
    assert row[schema.SCHED_ARR_UTC] == utc("2024-07-01 14:15")
    assert row[schema.ACTUAL_DEP_UTC] == utc("2024-07-01 12:15")
    assert row[schema.ACTUAL_ARR_UTC] == utc("2024-07-01 14:12")
    assert not row[schema.DEP_DELAYED_30]


def test_a_delay_past_midnight_lands_on_the_next_day():
    out, _ = run(flight(CRSDepTime="2330", DepDelay="75"))
    # 23:30 EDT is 03:30 UTC next day; 75 minutes later is 04:45.
    assert out.iloc[0][schema.ACTUAL_DEP_UTC] == utc("2024-07-02 04:45")
    assert out.iloc[0][schema.DEP_DELAYED_30]


def test_red_eye_into_a_hub_arrives_the_next_day():
    # Non-hub origin (PDX), 23:30 PDT departure, lands JFK 07:30 EDT next day.
    out, _ = run(
        flight(
            Origin="PDX",
            Dest="JFK",
            CRSDepTime="2330",
            CRSArrTime="0730",
            CRSElapsedTime="300",
        )
    )
    assert out.iloc[0][schema.SCHED_ARR_UTC] == utc("2024-07-02 11:30")
    assert out.iloc[0][schema.SCHED_DEP_UTC] == utc("2024-07-02 06:30")


def test_westbound_hop_that_lands_before_it_leaves_on_the_clock_stays_same_day():
    # Non-hub origin on Eastern time, BNA on Central: 07:00 ET out, 06:50 CT in.
    out, _ = run(
        flight(
            Origin="CHA",
            Dest="BNA",
            CRSDepTime="0700",
            CRSArrTime="0650",
            CRSElapsedTime="50",
        )
    )
    assert out.iloc[0][schema.SCHED_ARR_UTC] == utc("2024-07-01 11:50")


def test_cancelled_flights_have_no_delay_label():
    out, _ = run(
        flight(Cancelled="1.00", CancellationCode="B", DepDelay=None, ArrDelay=None)
    )
    row = out.iloc[0]
    assert row[schema.CANCELLED]
    assert pd.isna(row[schema.DEP_DELAYED_30])
    assert pd.isna(row[schema.ACTUAL_DEP_UTC])


def test_a_departure_in_the_spring_forward_gap_is_dropped_and_counted():
    out, report = run(flight(FlightDate="2024-03-10", CRSDepTime="0230"))
    assert out.empty
    assert "unresolvable_schedule_time" in report["rule"].tolist()


def test_impossible_taxi_time_is_masked_not_the_whole_flight():
    out, report = run(flight(TaxiOut="999"))
    assert len(out) == 1
    assert pd.isna(out.iloc[0][schema.TAXI_OUT_MIN])
    assert out.iloc[0][schema.DEP_DELAY_MIN] == 15
    assert ("mask", schema.TAXI_OUT_MIN) in set(zip(report["action"], report["column"]))


def test_duplicates_and_non_hub_flights_are_dropped():
    out, report = run(flight(), flight(DepDelay="40"), flight(Origin="PDX", Dest="CHA"))
    assert len(out) == 1
    assert out.iloc[0][schema.DEP_DELAY_MIN] == 40
    assert {"duplicate_flight", "no_hub"} <= set(report["rule"])


def test_flights_outside_the_window_are_dropped():
    out, report = run(flight(FlightDate="2025-01-01", CRSDepTime="0100"))
    # 01:00 EST on 1 Jan 2025 is 06:00 UTC, past the window's end.
    assert out.empty
    assert "outside_window" in report["rule"].tolist()
