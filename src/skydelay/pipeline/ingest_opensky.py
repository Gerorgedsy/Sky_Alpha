"""Download OpenSky Network movements and aircraft positions around the hubs.

OpenSky offers two things this project needs, with very different reach:

**Flights by airport** (``/flights/departure``, ``/flights/arrival``) list
each aircraft OpenSky saw leave or reach an airport, with first- and
last-seen times. They are historical -- built by a nightly batch, so
yesterday is the newest day -- and a request may span at most two days.
This is the source of historical congestion features.

**State vectors** (``/states/all``) are positions, speeds and on-ground
flags at one instant. They are *live only*: anonymous requests ignore the
``time`` parameter, and authenticated ones reach back one hour at most. A
states archive cannot be downloaded after the fact; it has to be collected
going forward by running :class:`OpenSkyStatesCollector`. That is a real
constraint on the project's apron-congestion features, stated here so it is
planned for rather than discovered.

Authentication is OAuth2 client credentials (basic auth is no longer
accepted). Without credentials the modules run anonymously, with a much
smaller daily credit allowance.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from skydelay.pipeline.airports import AirportRegistry
from skydelay.pipeline.http import HttpSource, write_atomic

# Order of a state vector array, from the OpenSky REST documentation.
STATE_FIELDS: tuple[str, ...] = (
    "icao24",
    "callsign",
    "origin_country",
    "time_position",
    "last_contact",
    "longitude",
    "latitude",
    "baro_altitude",
    "on_ground",
    "velocity",
    "true_track",
    "vertical_rate",
    "sensors",
    "geo_altitude",
    "squawk",
    "spi",
    "position_source",
    "category",
)

FLIGHT_FIELDS: tuple[str, ...] = (
    "icao24",
    "firstSeen",
    "estDepartureAirport",
    "lastSeen",
    "estArrivalAirport",
    "callsign",
    "estDepartureAirportHorizDistance",
    "estDepartureAirportVertDistance",
    "estArrivalAirportHorizDistance",
    "estArrivalAirportVertDistance",
    "departureAirportCandidatesCount",
    "arrivalAirportCandidatesCount",
)

DIRECTIONS: tuple[str, ...] = ("departure", "arrival")


class OpenSkyAuth:
    """Bearer tokens from client credentials, refreshed before they expire.

    Tokens last 30 minutes. Refreshing a minute early means a long
    collection run never sends an expired one.
    """

    refresh_margin_seconds = 60

    def __init__(
        self,
        token_url: str,
        client_id: str | None,
        client_secret: str | None,
        session: requests.Session,
    ) -> None:
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = session
        self._token: str | None = None
        self._expires_at = 0.0

    @classmethod
    def from_environment(cls, token_url: str, session: requests.Session) -> OpenSkyAuth:
        return cls(
            token_url,
            os.getenv("OPENSKY_CLIENT_ID") or None,
            os.getenv("OPENSKY_CLIENT_SECRET") or None,
            session,
        )

    @property
    def anonymous(self) -> bool:
        return not (self.client_id and self.client_secret)

    def headers(self) -> dict[str, str]:
        if self.anonymous:
            return {}
        if self._token is None or time.time() >= self._expires_at:
            response = self.session.post(
                self.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=30,
            )
            response.raise_for_status()
            body = response.json()
            self._token = body["access_token"]
            lifetime = float(body.get("expires_in", 1800))
            self._expires_at = time.time() + lifetime - self.refresh_margin_seconds
        return {"Authorization": f"Bearer {self._token}"}


@dataclass(frozen=True)
class Interval:
    begin: pd.Timestamp
    end: pd.Timestamp

    @property
    def slug(self) -> str:
        return self.begin.strftime("%Y%m%dT%H%M")


def intervals(
    start: pd.Timestamp, end: pd.Timestamp, chunk_hours: int
) -> Iterator[Interval]:
    """Split a window into request-sized pieces (OpenSky allows two days)."""
    if not 0 < chunk_hours <= 48:
        raise ValueError("OpenSky airport requests may span at most 48 hours.")
    step = pd.Timedelta(hours=chunk_hours)
    begin = start
    while begin < end:
        yield Interval(begin, min(begin + step, end))
        begin += step


def flights_frame(payload: list[Mapping[str, Any]] | None) -> pd.DataFrame:
    """A flights response as a frame with every documented column present."""
    frame = pd.DataFrame(list(payload or []))
    for column in FLIGHT_FIELDS:
        if column not in frame.columns:
            frame[column] = pd.NA
    return frame.loc[:, list(FLIGHT_FIELDS)]


def states_frame(payload: Mapping[str, Any]) -> pd.DataFrame:
    """A ``/states/all`` response as a frame, stamped with its snapshot time."""
    rows = payload.get("states") or []
    # Older vectors have 17 fields (no category); pad so columns line up.
    padded = [list(row) + [None] * (len(STATE_FIELDS) - len(row)) for row in rows]
    frame = pd.DataFrame(padded, columns=list(STATE_FIELDS))
    frame.insert(0, "snapshot_time", payload.get("time"))
    frame["sensors"] = frame["sensors"].astype("string")
    return frame


class OpenSkyBase(HttpSource):
    def __init__(
        self, config, output_dir, session=None, auth: OpenSkyAuth | None = None
    ):
        super().__init__(config, output_dir, session)
        self.auth = auth or OpenSkyAuth.from_environment(
            config["token_url"], self.session
        )

    def api(self, path: str, params: Mapping[str, Any]) -> requests.Response:
        url = f"{self.config['api_base']}{path}"
        response = self.session.get(
            url, params=params, headers=self.auth.headers(), timeout=60
        )
        if response.status_code == 401 and not self.auth.anonymous:
            # The token was revoked or expired early; get a fresh one once.
            self.auth._token = None
            response = self.session.get(
                url, params=params, headers=self.auth.headers(), timeout=60
            )
        return response


class OpenSkyFlightsSource(OpenSkyBase):
    """Historical departures and arrivals for every hub."""

    name = "opensky_flights"

    def path(self, icao: str, direction: str, interval: Interval) -> Path:
        return self.output_dir / f"{icao}_{direction}_{interval.slug}.csv"

    def ingest(
        self,
        start: pd.Timestamp,
        end: pd.Timestamp,
        registry: AirportRegistry,
        *,
        overwrite: bool = False,
    ) -> list[Path]:
        # Airport flights come from a nightly batch: today is not there yet.
        newest = pd.Timestamp.now(tz="UTC").normalize()
        end = min(end, newest)
        written: list[Path] = []
        for interval in intervals(start, end, int(self.config["flights_chunk_hours"])):
            for airport in registry.airports:
                for direction in DIRECTIONS:
                    path = self.path(airport.icao, direction, interval)
                    if self.done(path, overwrite=overwrite):
                        written.append(path)
                        continue
                    response = self.api(
                        f"/flights/{direction}",
                        {
                            "airport": airport.icao,
                            "begin": int(interval.begin.timestamp()),
                            "end": int(interval.end.timestamp()),
                        },
                    )
                    # 404 means OpenSky saw no flights, which is an answer,
                    # so it is written as an empty file and not refetched.
                    if response.status_code == 404:
                        frame = flights_frame([])
                    else:
                        response.raise_for_status()
                        frame = flights_frame(response.json())
                    write_atomic(lambda p, f=frame: f.to_csv(p, index=False), path)
                    written.append(path)
            print(f"  OpenSky flights {interval.slug}: done", flush=True)
        return written


class OpenSkyStatesCollector(OpenSkyBase):
    """Poll live state vectors over the hub region and archive each snapshot.

    One request over the continental bounding box costs four credits and
    covers every hub at once, which is cheaper than thirty small boxes.
    A standard account's 4,000 daily credits allow about 1,000 snapshots a
    day -- one every ~90 seconds; the configured interval leaves headroom.
    """

    name = "opensky_states"

    def snapshot(self) -> tuple[pd.Timestamp, pd.DataFrame]:
        """One snapshot and its time, taken from the response itself.

        The time comes from the payload, not the first row: an empty sky (a
        quiet night, or an outage) is a valid snapshot with no rows.
        """
        response = self.api("/states/all", dict(self.config["states_bbox"]))
        response.raise_for_status()
        payload = response.json()
        stamp = pd.Timestamp(int(payload.get("time") or time.time()), unit="s")
        return stamp, states_frame(payload)

    def collect(self, polls: int, *, sleep=time.sleep) -> list[Path]:
        interval = float(self.config["states_poll_seconds"])
        written: list[Path] = []
        for poll in range(polls):
            stamp, frame = self.snapshot()
            path = (
                self.output_dir
                / stamp.strftime("%Y%m%d")
                / f"states_{stamp:%H%M%S}.csv"
            )
            write_atomic(lambda p, f=frame: f.to_csv(p, index=False), path)
            written.append(path)
            print(f"  snapshot {poll + 1}/{polls}: {len(frame):,} aircraft", flush=True)
            if poll + 1 < polls:
                sleep(interval)
        return written
