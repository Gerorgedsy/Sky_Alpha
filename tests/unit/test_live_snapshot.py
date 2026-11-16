import json
from datetime import UTC, datetime, timedelta

import pytest

from skydelay.live import snapshot

FAA_XML = """<AIRPORT_STATUS_INFORMATION>
<Update_Time>Mon Nov 16 19:02:31 2026 GMT</Update_Time>
<Delay_type><Name>Ground Delay Programs</Name><Ground_Delay_List>
<Ground_Delay><ARPT>EWR</ARPT><Reason>low ceilings</Reason><Avg>57 minutes</Avg>
<Max>2 hours</Max></Ground_Delay>
<Ground_Delay><ARPT>SFO</ARPT><Reason>wind</Reason><Avg>1 hour</Avg></Ground_Delay>
</Ground_Delay_List></Delay_type>
<Delay_type><Name>Ground Stop Programs</Name><Ground_Stop_List>
<Program><ARPT>JFK</ARPT><Reason>thunderstorms</Reason><End_Time>3:15 pm EST</End_Time></Program>
</Ground_Stop_List></Delay_type>
<Delay_type><Name>General Arrival/Departure Delay Info</Name><Arrival_Departure_Delay_List>
<Delay><ARPT>JFK</ARPT><Reason>WX:Wind</Reason><Arrival_Departure Type="Departure">
<Min>16 minutes</Min><Max>30 minutes</Max><Trend>Increasing</Trend></Arrival_Departure></Delay>
</Arrival_Departure_Delay_List></Delay_type>
<Delay_type><Name>Airport Closures</Name><Airport_Closure_List>
<Airport><ARPT>LGA</ARPT><Reason>runway work</Reason></Airport>
</Airport_Closure_List></Delay_type>
</AIRPORT_STATUS_INFORMATION>"""


def test_faa_events_are_kept_for_new_york_airports_only():
    faa = snapshot.normalize_faa(FAA_XML)
    assert faa["updatedAt"] == "2026-11-16T19:02:31Z"
    assert faa["events"]["EWR"] == [
        {
            "type": "Ground Delay Programs",
            "reason": "low ceilings",
            "averageDelay": "57 minutes",
            "maximumDelay": "2 hours",
            "endTime": None,
            "direction": None,
        }
    ]
    stop, delay = faa["events"]["JFK"]
    assert stop["endTime"] == "3:15 pm EST"
    assert delay["averageDelay"] == "16 minutes to 30 minutes"
    assert delay["direction"] == "Departure"
    assert faa["events"]["LGA"][0]["type"] == "Airport Closures"


def test_faa_rejects_other_documents():
    with pytest.raises(ValueError, match="Invalid FAA"):
        snapshot.normalize_faa("<html/>")


def test_weather_maps_metar_fields():
    rows = [
        {
            "icaoId": "KJFK",
            "obsTime": 1763319600,
            "fltCat": "MVFR",
            "wspd": 14,
            "wgst": 22,
            "visib": "6+",
            "temp": 9.4,
        },
        {"icaoId": "KBOS", "obsTime": 1763319600},
    ]
    weather = snapshot.normalize_weather(rows)["airports"]
    assert weather["JFK"]["category"] == "MVFR"
    assert weather["JFK"]["gustKt"] == 22
    assert weather["JFK"]["visibilityMi"] == "6+"
    assert weather["JFK"]["observedAt"].endswith("Z")
    assert weather["EWR"] is None and "BOS" not in weather


def test_schedule_keeps_operating_flights_for_the_direction():
    raw = {
        "response": [
            {
                "flight_iata": "B6123",
                "airline_iata": "B6",
                "dep_iata": "JFK",
                "arr_iata": "BOS",
                "dep_time": "2026-11-16 15:10",
                "dep_gate": "7",
                "dep_terminal": "5",
                "status": "scheduled",
            },
            {
                "flight_iata": "AA9001",
                "cs_flight_iata": "B6123",
                "dep_iata": "JFK",
                "arr_iata": "BOS",
                "dep_time": "2026-11-16 15:10",
            },
            {
                "flight_iata": "DL400",
                "dep_iata": "ATL",
                "arr_iata": "JFK",
                "arr_time": "2026-11-16 14:00",
                "arr_baggage": "4",
            },
        ]
    }
    departures = snapshot.clean_schedule(raw, "JFK", "departures")
    assert [f["flight"] for f in departures] == ["B6123"]
    assert departures[0]["gate"] == "7" and departures[0]["destination"] == "BOS"
    arrivals = snapshot.clean_schedule(raw, "JFK", "arrivals")
    assert arrivals[0]["origin"] == "ATL" and arrivals[0]["baggage"] == "4"


def test_recent_schedules_are_not_refetched(tmp_path, monkeypatch):
    fetched = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    path = tmp_path / "schedules.json"
    path.write_text(json.dumps({"status": "live", "fetchedAt": fetched}))
    monkeypatch.setenv("AIRLABS_API_KEY", "key")
    monkeypatch.setattr(snapshot, "fetch_operations", lambda: {"status": "live"})
    monkeypatch.setattr(
        snapshot, "fetch_schedules", lambda key: pytest.fail("quota spent")
    )
    monkeypatch.setattr("sys.argv", ["snapshot", "--output", str(tmp_path)])
    snapshot.main()
    assert json.loads(path.read_text())["fetchedAt"] == fetched


def test_departures_refresh_more_often_than_arrivals(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    path = tmp_path / "schedules.json"
    path.write_text(
        json.dumps(
            {
                "status": "live",
                "fetchedAt": (now - timedelta(hours=2)).isoformat(),
                "directionsFetchedAt": {
                    "departures": (now - timedelta(hours=4)).isoformat(),
                    "arrivals": (now - timedelta(hours=2)).isoformat(),
                },
                "airports": {"JFK": {"arrivals": [{"flight": "KEEP1"}]}},
            }
        )
    )
    asked = []

    def fake(api_key, directions, previous):
        asked.append(directions)
        fetched = {**previous["directionsFetchedAt"], "departures": "now"}
        return {**previous, "directionsFetchedAt": fetched}

    monkeypatch.setenv("AIRLABS_API_KEY", "key")
    monkeypatch.setattr(snapshot, "fetch_operations", lambda: {"status": "live"})
    monkeypatch.setattr(snapshot, "fetch_schedules", fake)
    monkeypatch.setattr("sys.argv", ["snapshot", "--output", str(tmp_path)])
    snapshot.main()
    assert asked == [("departures",)]
    kept = json.loads(path.read_text())["airports"]["JFK"]["arrivals"]
    assert kept == [{"flight": "KEEP1"}]


def test_weather_keeps_ceiling_dewpoint_and_codes():
    rows = [
        {
            "icaoId": "KEWR",
            "obsTime": 1763319600,
            "temp": 20,
            "dewp": 18,
            "wxString": "-TSRA",
            "clouds": [{"cover": "SCT", "base": 900}, {"cover": "BKN", "base": 2500}],
        },
    ]
    ewr = snapshot.normalize_weather(rows)["airports"]["EWR"]
    assert ewr["ceilingFt"] == 2500 and ewr["dewpointC"] == 18 and ewr["wx"] == "-TSRA"
