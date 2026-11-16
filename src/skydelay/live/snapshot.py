"""Write live airport snapshots for the GitHub Pages site.

This is the static counterpart of ``live-service``: it fetches the same public
sources and writes JSON the page can read when the backend is unreachable.

- ``operations.json``: FAA NAS airport events and NOAA METAR observations for
  JFK, EWR and LGA, in the backend's ``/api/operations`` shape.
- ``schedules.json``: departures and arrivals per airport from the AirLabs
  Schedules API, when ``AIRLABS_API_KEY`` is set; otherwise
  ``status: not_configured``.

AirLabs' free plan allows about 1,000 requests a month and 50 flights per
request. One refresh costs six requests, so schedules are re-fetched only when
the saved file is older than ``--schedule-max-age-hours`` (default 6).

Example:

    PYTHONPATH=src python -m skydelay.live.snapshot --output live-data
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

AIRPORTS: tuple[str, ...] = ("JFK", "EWR", "LGA")
FAA_URL = "https://nasstatus.faa.gov/api/airport-status-information"
WEATHER_URL = (
    "https://aviationweather.gov/api/data/metar?ids=KJFK,KEWR,KLGA&format=json"
)
AIRLABS_URL = "https://airlabs.co/api/v9/schedules"
USER_AGENT = "SkyDelay-Alpha student project (github.com/Gerorgedsy/Sky_Alpha)"
SCHEDULE_LIMIT = 50
DIRECTIONS = {"departures": "dep", "arrivals": "arr"}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(url: str, *, accept: str = "application/json") -> bytes:
    request = urllib.request.Request(
        url, headers={"Accept": accept, "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def _text(element: ET.Element, tag: str) -> str | None:
    found = element.find(tag)
    value = (found.text or "").strip() if found is not None else ""
    return value or None


def _faa_time(value: str | None) -> str | None:
    if not value:
        return None
    # The feed writes e.g. "Mon Nov 16 19:02:31 2026 GMT".
    for pattern in ("%a %b %d %H:%M:%S %Y GMT", "%a %b %d %H:%M:%S %Y"):
        try:
            parsed = datetime.strptime(value, pattern).replace(tzinfo=UTC)
        except ValueError:
            continue
        return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def normalize_faa(xml: str) -> dict[str, Any]:
    """Return FAA events for the New York airports from the NAS status XML."""
    root = ET.fromstring(xml)
    if root.tag != "AIRPORT_STATUS_INFORMATION":
        raise ValueError("Invalid FAA status response")
    events: dict[str, list[dict[str, Any]]] = {code: [] for code in AIRPORTS}
    for group in root.iter("Delay_type"):
        kind = _text(group, "Name")
        for event in group.iter():
            code = (_text(event, "ARPT") or "").upper()
            if code not in events:
                continue
            # Arrival/departure delays keep their range one level down.
            detail = event.find("Arrival_Departure")
            low = _text(detail, "Min") if detail is not None else None
            high = _text(event, "Max") or (
                _text(detail, "Max") if detail is not None else None
            )
            events[code].append(
                {
                    "type": kind,
                    "reason": _text(event, "Reason"),
                    "averageDelay": _text(event, "Avg")
                    or (f"{low} to {high}" if low and high else None),
                    "maximumDelay": high,
                    "endTime": _text(event, "End_Time"),
                    "direction": detail.get("Type") if detail is not None else None,
                }
            )
    return {
        "status": "available",
        "updatedAt": _faa_time(_text(root, "Update_Time")),
        "events": events,
    }


def _number(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    return value if isinstance(value, int | float) else None


def _clean(row: dict[str, Any], key: str) -> str | None:
    return str(row.get(key) or "").strip() or None


def normalize_weather(rows: Any) -> dict[str, Any]:
    """Return the latest METAR summary per New York airport."""
    if not isinstance(rows, list):
        raise TypeError("Invalid aviation weather response")
    weather: dict[str, Any] = {code: None for code in AIRPORTS}
    for row in rows:
        code = str(row.get("icaoId") or "").upper().removeprefix("K")
        if code not in weather:
            continue
        observed = row.get("obsTime")
        weather[code] = {
            "observedAt": (
                datetime.fromtimestamp(observed, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                if isinstance(observed, int | float)
                else None
            ),
            "category": row.get("fltCat") or None,
            "windKt": _number(row, "wspd"),
            "gustKt": _number(row, "wgst"),
            "visibilityMi": None if row.get("visib") is None else str(row["visib"]),
            "temperatureC": _number(row, "temp"),
        }
    return {"status": "available", "airports": weather}


def fetch_operations() -> dict[str, Any]:
    """Fetch FAA and weather; one failing source leaves the other usable."""
    try:
        faa = normalize_faa(_get(FAA_URL, accept="application/xml").decode())
    except Exception as error:  # noqa: BLE001 - any failure means unavailable
        print(f"FAA status unavailable: {error}")
        faa = {"status": "unavailable", "updatedAt": None, "events": None}
    try:
        weather = normalize_weather(json.loads(_get(WEATHER_URL)))
    except Exception as error:  # noqa: BLE001
        print(f"Aviation weather unavailable: {error}")
        weather = {"status": "unavailable", "airports": None}
    if faa["status"] == weather["status"] == "unavailable":
        raise RuntimeError("Airport sources unavailable")
    return {
        "status": "live",
        "source": "FAA NAS Status and NOAA Aviation Weather Center",
        "fetchedAt": _now(),
        "faa": faa,
        "weather": weather,
    }


def clean_schedule(raw: Any, airport: str, direction: str) -> list[dict[str, Any]]:
    """Keep operating (not codeshare) flights for one airport and direction."""
    if not isinstance(raw, dict) or not isinstance(raw.get("response"), list):
        raise TypeError("Invalid schedule response")
    side = DIRECTIONS[direction]
    other = "arr" if side == "dep" else "dep"
    flights = []
    for item in raw["response"]:
        if item.get(f"{side}_iata") != airport or item.get("cs_flight_iata"):
            continue
        flight = str(item.get("flight_iata") or "").strip()
        local = str(item.get(f"{side}_time") or "").strip()
        if not flight or not local:
            continue
        flights.append(
            {
                "flight": flight,
                "airline": _clean(item, "airline_iata"),
                "origin": airport if side == "dep" else _clean(item, "dep_iata"),
                "destination": _clean(item, "arr_iata") if side == "dep" else airport,
                "otherAirport": _clean(item, f"{other}_iata"),
                "scheduledLocal": local,
                "scheduledUtc": _clean(item, f"{side}_time_utc"),
                "estimatedLocal": _clean(item, f"{side}_estimated"),
                "status": _clean(item, "status"),
                "delayMin": item.get(f"{side}_delayed") or item.get("delayed"),
                "terminal": _clean(item, f"{side}_terminal"),
                "gate": _clean(item, f"{side}_gate"),
                "baggage": _clean(item, "arr_baggage") if side == "arr" else None,
            }
        )
    flights.sort(key=lambda f: f["scheduledLocal"])
    return flights


def fetch_schedules(api_key: str) -> dict[str, Any]:
    """Fetch departures and arrivals for every New York airport."""
    airports: dict[str, dict[str, Any]] = {}
    for airport in AIRPORTS:
        airports[airport] = {}
        for direction, side in DIRECTIONS.items():
            query = urllib.parse.urlencode(
                {f"{side}_iata": airport, "limit": SCHEDULE_LIMIT, "api_key": api_key}
            )
            raw = json.loads(_get(f"{AIRLABS_URL}?{query}"))
            if raw.get("error"):
                message = raw["error"].get("message", "unknown error")
                raise RuntimeError(f"AirLabs error for {airport}: {message}")
            airports[airport][direction] = clean_schedule(raw, airport, direction)
    return {
        "status": "live",
        "source": "AirLabs",
        "fetchedAt": _now(),
        "coverage": (
            "Partial: the free plan returns at most 50 flights per airport and "
            "direction; gate, terminal and status may be missing."
        ),
        "airports": airports,
    }


def _age(path: Path) -> timedelta | None:
    try:
        fetched = json.loads(path.read_text()).get("fetchedAt")
        return datetime.now(UTC) - datetime.fromisoformat(fetched)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"Wrote {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--schedule-max-age-hours", type=float, default=6.0)
    parser.add_argument("--force-schedules", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    failures = 0
    try:
        _write(args.output / "operations.json", fetch_operations())
    except Exception as error:  # noqa: BLE001 - keep the previous snapshot
        print(f"Operations not updated: {error}")
        failures += 1

    schedules = args.output / "schedules.json"
    api_key = os.environ.get("AIRLABS_API_KEY", "").strip()
    age = _age(schedules)
    if not api_key:
        _write(
            schedules,
            {"status": "not_configured", "source": "AirLabs", "fetchedAt": None},
        )
    elif (
        args.force_schedules
        or age is None
        or age >= timedelta(hours=args.schedule_max_age_hours)
    ):
        try:
            _write(schedules, fetch_schedules(api_key))
        except Exception as error:  # noqa: BLE001 - keep the previous snapshot
            print(f"Schedules not updated: {error}")
            failures += 1
    else:
        print(f"Schedules are {age} old; keeping them to save AirLabs quota")
    if failures == 2:
        raise SystemExit("No source could be refreshed")


if __name__ == "__main__":
    main()
