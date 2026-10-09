"""Small raw fixtures in each source's on-disk format."""

from pathlib import Path

import pandas as pd


def write_raw(raw_dir: Path) -> Path:
    (raw_dir / "bts").mkdir(parents=True)
    (raw_dir / "opensky_flights").mkdir()
    (raw_dir / "hrrr").mkdir()
    pd.DataFrame(
        [
            {
                "FlightDate": "2024-07-01",
                "Reporting_Airline": "DL",
                "Tail_Number": "N1",
                "Flight_Number_Reporting_Airline": "100",
                "Origin": "ATL",
                "Dest": "LGA",
                "CRSDepTime": "0800",
                "DepTime": "0815",
                "DepDelay": "45",
                "DepDel15": "1",
                "TaxiOut": "18",
                "WheelsOff": "0833",
                "WheelsOn": "1005",
                "TaxiIn": "7",
                "CRSArrTime": "1015",
                "ArrTime": "1040",
                "ArrDelay": "25",
                "ArrDel15": "1",
                "Cancelled": "0",
                "CancellationCode": "",
                "Diverted": "0",
                "CRSElapsedTime": "135",
                "ActualElapsedTime": "115",
                "Distance": "762",
                "CarrierDelay": "",
                "WeatherDelay": "",
                "NASDelay": "",
                "SecurityDelay": "",
                "LateAircraftDelay": "",
            }
        ]
    ).to_csv(raw_dir / "bts" / "ontime_2024_07.csv", index=False)
    pd.DataFrame(
        [
            {
                "icao24": "abc123",
                "firstSeen": 1719835200,
                "estDepartureAirport": "KATL",
                "lastSeen": 1719840600,
                "estArrivalAirport": "KLGA",
                "callsign": "DAL100",
            }
        ]
    ).to_csv(
        raw_dir / "opensky_flights" / "KATL_departure_20240701T0000.csv", index=False
    )
    pd.DataFrame(
        [
            {
                "init_time": "2024-07-01T12:00:00+00:00",
                "lead_hours": 2,
                "hub": "ATL",
                "REFC": 10,
                "VIS": 20000,
                "GUST": 5,
                "TMP": 300,
                "UGRD": 1,
                "VGRD": 1,
                "PRATE": 0,
                "HGT": 3000,
            }
        ]
    ).to_csv(raw_dir / "hrrr" / "hrrr_20240701T12Z.csv", index=False)
    return raw_dir
