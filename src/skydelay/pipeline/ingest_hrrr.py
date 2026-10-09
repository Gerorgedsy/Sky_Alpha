"""Download HRRR surface forecasts at each hub, by byte range.

HRRR is NOAA's 3 km, hourly-updated model over the continental US. A
``wrfsfc`` file is well over 100 MB and this project wants eight fields at
thirty points. Each file has an ``.idx`` sidecar giving every message's byte
offset, so only the wanted messages are fetched -- the same approach project
1 used for GFS. One message is one field over the whole grid, so all thirty
hubs are read from it at once: cost grows with cycles x leads x fields, not
with the number of hubs.

Every row keeps two clocks, as project 1's GFS rows did:

- ``init_time``: when the model run started;
- ``valid_time``: the hour it describes (``init_time + lead``).

Cleaning adds a third, ``available_at``, for when the run could actually be
read. A 2-6 hour delay forecast issued at noon may use the 11:00 run's
forecast for 15:00, but never the 14:00 run's, however close its valid time.

Rough cost, to plan with: each message is one to a few MB, so the default
eight fields x four leads x eight cycles is a few hundred MB per day. A year
at that density is a large download; start with a few weeks.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from skydelay.pipeline.airports import Airport, AirportRegistry
from skydelay.pipeline.http import HttpSource, write_atomic


@dataclass(frozen=True)
class GribField:
    """One GRIB message, named as the ``.idx`` file names it."""

    name: str
    level: str

    @property
    def key(self) -> str:
        return f"{self.name}:{self.level}"


@dataclass(frozen=True)
class IndexEntry:
    key: str
    start: int
    end: int | None

    @property
    def byte_range(self) -> str:
        return f"{self.start}-" if self.end is None else f"{self.start}-{self.end}"


def parse_index(text: str) -> dict[str, IndexEntry]:
    """Turn a ``.idx`` sidecar into a lookup from ``NAME:level`` to bytes.

    Each line is ``record:offset:d=YYYYMMDDHH:NAME:level:forecast:``. A
    message runs from its offset to the byte before the next one. Where a
    key repeats (APCP has a run-total and an hourly variant), the first is
    kept; the configured fields are all instantaneous.
    """
    offsets: list[tuple[int, str]] = []
    for line in text.splitlines():
        parts = line.strip().split(":")
        if len(parts) < 6 or not parts[1].isdigit():
            continue
        offsets.append((int(parts[1]), f"{parts[3]}:{parts[4]}"))
    offsets.sort()
    entries: dict[str, IndexEntry] = {}
    for position, (start, key) in enumerate(offsets):
        end = offsets[position + 1][0] - 1 if position + 1 < len(offsets) else None
        entries.setdefault(key, IndexEntry(key=key, start=start, end=end))
    return entries


def extract_points(message: bytes, airports: Sequence[Airport]) -> dict[str, float]:
    """The value at the grid point nearest each hub, from one GRIB message."""
    # Imported here so the module and its pure functions load without the
    # GRIB library, which keeps the test suite runnable anywhere.
    import eccodes

    handle = eccodes.codes_new_from_message(message)
    try:
        return {
            airport.iata: float(
                eccodes.codes_grib_find_nearest(
                    handle, airport.latitude, airport.longitude
                )[0].value
            )
            for airport in airports
        }
    finally:
        eccodes.codes_release(handle)


def cycles(
    start: pd.Timestamp, end: pd.Timestamp, hours: Sequence[int]
) -> Iterator[pd.Timestamp]:
    """Every configured cycle time inside the half-open window."""
    for day in pd.date_range(start.normalize(), end, freq="D"):
        for hour in sorted(hours):
            init = day + pd.Timedelta(hours=hour)
            if start <= init < end:
                yield init


class HrrrSource(HttpSource):
    name = "hrrr"

    def __init__(
        self, config: Mapping[str, Any], output_dir: Path, session=None
    ) -> None:
        super().__init__(config, output_dir, session)
        self.fields = tuple(GribField(**f) for f in config["fields"])
        self.leads = tuple(int(lead) for lead in config["leads"])

    def url(self, init: pd.Timestamp, lead: int) -> str:
        return self.config["url_template"].format(
            day=init.strftime("%Y%m%d"), cycle=init.strftime("%H"), lead=lead
        )

    def cycle_path(self, init: pd.Timestamp) -> Path:
        return self.output_dir / f"hrrr_{init:%Y%m%dT%H}Z.csv"

    def fetch_lead(
        self, init: pd.Timestamp, lead: int, airports: Sequence[Airport]
    ) -> pd.DataFrame:
        url = self.url(init, lead)
        index = parse_index(self.get(f"{url}.idx").text)
        values: dict[str, dict[str, float]] = {}
        for field in self.fields:
            entry = index.get(field.key)
            if entry is None:
                # A field absent from this file is recorded as missing rather
                # than failing the whole cycle.
                continue
            message = self.get(
                url, headers={"Range": f"bytes={entry.byte_range}"}
            ).content
            values[field.name] = extract_points(message, airports)
        rows = []
        for airport in airports:
            row: dict[str, Any] = {
                "init_time": init.isoformat(),
                "lead_hours": lead,
                "hub": airport.iata,
            }
            for field in self.fields:
                row[field.name] = values.get(field.name, {}).get(airport.iata)
            rows.append(row)
        return pd.DataFrame(rows)

    def ingest(
        self,
        start: pd.Timestamp,
        end: pd.Timestamp,
        registry: AirportRegistry,
        *,
        overwrite: bool = False,
    ) -> list[Path]:
        written: list[Path] = []
        for init in cycles(start, end, self.config["cycle_hours_utc"]):
            path = self.cycle_path(init)
            if self.done(path, overwrite=overwrite):
                written.append(path)
                continue
            frames = [
                self.fetch_lead(init, lead, registry.airports) for lead in self.leads
            ]
            frame = pd.concat(frames, ignore_index=True)
            write_atomic(lambda p, f=frame: f.to_csv(p, index=False), path)
            print(f"  HRRR {init:%Y-%m-%d %H}Z: {len(frame)} rows", flush=True)
            written.append(path)
        return written
