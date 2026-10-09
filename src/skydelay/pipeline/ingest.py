"""Download the raw data for every source.

Run from the repository root, for example:

    PYTHONPATH=src python -m skydelay.pipeline.ingest --source bts
    PYTHONPATH=src python -m skydelay.pipeline.ingest --source hrrr \
        --start 2024-07-01 --end 2024-07-08
    PYTHONPATH=src python -m skydelay.pipeline.ingest --source opensky-states --polls 12

Raw files go to ``data/raw/<source>/``. Every source resumes: run the same
command again after an interruption and finished files are skipped. The
window defaults to ``config/sources.json``; ``--start``/``--end`` narrow it,
which is the sensible way to try HRRR before committing to a long download.

``opensky-states`` is not part of ``all``. It records the live sky and has no
window; it is started deliberately, for as many polls as wanted.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from skydelay.pipeline.airports import AirportRegistry
from skydelay.pipeline.ingest_bts import BtsOnTimeSource
from skydelay.pipeline.ingest_hrrr import HrrrSource
from skydelay.pipeline.ingest_opensky import (
    OpenSkyFlightsSource,
    OpenSkyStatesCollector,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "sources.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "raw"

WINDOWED_SOURCES: tuple[str, ...] = ("bts", "opensky-flights", "hrrr")
ALL_SOURCES: tuple[str, ...] = (*WINDOWED_SOURCES, "opensky-states")


def utc(value: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return (
        timestamp.tz_localize("UTC")
        if timestamp.tzinfo is None
        else timestamp.tz_convert("UTC")
    )


def window(
    config: dict[str, Any], start: str | None, end: str | None
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The configured window, optionally narrowed (never widened)."""
    first = utc(config["window"]["start_utc"])
    last = utc(config["window"]["end_utc"])
    if start:
        first = max(first, utc(start))
    if end:
        last = min(last, utc(end))
    if first >= last:
        raise ValueError(f"Empty window: {first} to {last}.")
    return first, last


def run(
    sources: Sequence[str],
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    start: str | None = None,
    end: str | None = None,
    polls: int = 1,
    overwrite: bool = False,
) -> None:
    with config_path.open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    registry = AirportRegistry.from_config(config)
    first, last = window(config, start, end)

    for source in sources:
        print(f"Ingesting {source}...", flush=True)
        if source == "bts":
            BtsOnTimeSource(config["bts"], output_dir).ingest(
                first, last, registry, overwrite=overwrite
            )
        elif source == "opensky-flights":
            OpenSkyFlightsSource(config["opensky"], output_dir).ingest(
                first, last, registry, overwrite=overwrite
            )
        elif source == "hrrr":
            HrrrSource(config["hrrr"], output_dir).ingest(
                first, last, registry, overwrite=overwrite
            )
        elif source == "opensky-states":
            OpenSkyStatesCollector(config["opensky"], output_dir).collect(polls)
        else:
            raise ValueError(f"Unknown source {source!r}; choose from {ALL_SOURCES}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        action="append",
        choices=("all", *ALL_SOURCES),
        help="Source to ingest; repeat for several. 'all' means every windowed source.",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start", help="Narrow the window start (YYYY-MM-DD, UTC).")
    parser.add_argument(
        "--end", help="Narrow the window end (YYYY-MM-DD, UTC, exclusive)."
    )
    parser.add_argument(
        "--polls", type=int, default=1, help="Snapshots for opensky-states."
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    requested = args.source or ["all"]
    sources = [
        s
        for name in requested
        for s in (WINDOWED_SOURCES if name == "all" else (name,))
    ]
    run(
        list(dict.fromkeys(sources)),
        config_path=args.config,
        output_dir=args.output_dir,
        start=args.start,
        end=args.end,
        polls=args.polls,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
