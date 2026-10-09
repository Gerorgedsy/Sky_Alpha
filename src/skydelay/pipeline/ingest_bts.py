"""Download BTS Reporting Carrier On-Time Performance, one month at a time.

This is the ground truth: every scheduled domestic flight by a reporting
carrier, with its scheduled and actual times, taxi times, delay minutes and
delay causes. The models learn from it and are scored against it.

Each month is published as a zip of one CSV with more than 100 columns and
every US airport. Only the configured columns, and only flights that touch a
configured hub, are kept -- about a third of the rows and a quarter of the
columns -- so the raw archive stays small enough to rebuild quickly.

BTS publishes a month roughly two months after it ends. That makes it a
training and evaluation source only: a live forecast cannot use it, which is
why the real-time features come from OpenSky.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

from skydelay.pipeline.airports import AirportRegistry
from skydelay.pipeline.http import HttpSource, write_atomic

# Codes are kept as text: a flight number or tail number is an identifier,
# and reading one as a number would drop leading zeros.
TEXT_COLUMNS: tuple[str, ...] = (
    "Reporting_Airline",
    "Tail_Number",
    "Flight_Number_Reporting_Airline",
    "Origin",
    "Dest",
    "CRSDepTime",
    "DepTime",
    "WheelsOff",
    "WheelsOn",
    "CRSArrTime",
    "ArrTime",
    "CancellationCode",
)


def months(start: pd.Timestamp, end: pd.Timestamp) -> Iterator[pd.Period]:
    """Every calendar month that overlaps the half-open window."""
    first = start.tz_localize(None).to_period("M")
    last = (end.tz_localize(None) - pd.Timedelta(seconds=1)).to_period("M")
    yield from pd.period_range(first, last, freq="M")


def read_month(
    content: bytes, columns: list[str], hubs: frozenset[str]
) -> pd.DataFrame:
    """Read the CSV inside one month's zip, keeping hub flights and columns."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"Expected one CSV in the BTS zip, found {members}.")
        with archive.open(members[0]) as handle:
            frame = pd.read_csv(
                handle,
                usecols=lambda column: column in columns,
                dtype={c: "string" for c in TEXT_COLUMNS},
                low_memory=False,
            )
    missing = sorted(set(columns).difference(frame.columns))
    if missing:
        raise KeyError(f"BTS file is missing configured columns: {missing}.")
    touches_hub = frame["Origin"].isin(hubs) | frame["Dest"].isin(hubs)
    return frame.loc[touches_hub, columns]


class BtsOnTimeSource(HttpSource):
    name = "bts"

    def month_path(self, month: pd.Period) -> Path:
        return self.output_dir / f"ontime_{month.year}_{month.month:02d}.csv"

    def ingest(
        self,
        start: pd.Timestamp,
        end: pd.Timestamp,
        registry: AirportRegistry,
        *,
        overwrite: bool = False,
    ) -> list[Path]:
        written: list[Path] = []
        columns = list(self.config["columns"])
        for month in months(start, end):
            path = self.month_path(month)
            if self.done(path, overwrite=overwrite):
                written.append(path)
                continue
            url = self.config["url_template"].format(year=month.year, month=month.month)
            print(f"  BTS {month}: downloading", flush=True)
            frame = read_month(
                self.get(url, timeout=600).content, columns, registry.iata_codes
            )
            write_atomic(lambda p, f=frame: f.to_csv(p, index=False), path)
            print(
                f"  BTS {month}: {len(frame):,} hub flights -> {path.name}", flush=True
            )
            written.append(path)
        return written
