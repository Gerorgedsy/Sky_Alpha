"""Clean every ingested source into the tables the models read.

Run from the repository root with:

    PYTHONPATH=src python -m skydelay.pipeline.clean

Pass ``--source`` (repeatable) to clean a subset. Outputs go to
``data/processed/``:

- ``flights.parquet`` -- BTS hub flights on UTC time, with delay labels;
- ``opensky_flights.parquet`` -- OpenSky movements at the hubs;
- ``opensky_states.parquet`` -- aircraft positions assigned to hubs;
- ``weather.parquet`` -- HRRR forecasts per hub, with init, valid and
  available times;
- ``screening_report.csv`` -- every row dropped or value masked, by rule.

Sources with no raw data are skipped with a message when cleaning ``all``,
so a teammate who has fetched only BTS can still clean it. Naming a source
explicitly makes missing data an error instead.

This stage does not build features. Spatial gridding, taxi-out deltas and
sector density belong in ``features/``, built from these tables.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path

import pandas as pd

from skydelay.pipeline import clean_bts, clean_hrrr, clean_opensky
from skydelay.pipeline.airports import AirportRegistry
from skydelay.pipeline.http import write_atomic
from skydelay.pipeline.ingest import window
from skydelay.pipeline.quality import ScreeningReport

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "sources.json"
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "processed"

OUTPUTS: dict[str, str] = {
    "bts": "flights.parquet",
    "opensky-flights": "opensky_flights.parquet",
    "opensky-states": "opensky_states.parquet",
    "hrrr": "weather.parquet",
}
REPORT_NAME = "screening_report.csv"


def run(
    sources: Sequence[str] | None = None,
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    raw_dir: Path = DEFAULT_RAW_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    overwrite: bool = False,
) -> dict[str, Path]:
    with config_path.open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    registry = AirportRegistry.from_config(config)
    span = window(config, None, None)
    explicit = sources is not None
    requested = list(sources) if explicit else list(OUTPUTS)

    steps: dict[str, Callable[[ScreeningReport], pd.DataFrame]] = {
        "bts": lambda r: clean_bts.clean(
            clean_bts.load_raw(raw_dir), registry, span, r
        ),
        "opensky-flights": lambda r: clean_opensky.clean_flights(
            clean_opensky.load_flights(raw_dir), registry, span, r
        ),
        "opensky-states": lambda r: clean_opensky.clean_states(
            clean_opensky.load_states(raw_dir), registry, config["opensky"], r
        ),
        "hrrr": lambda r: clean_hrrr.clean(
            clean_hrrr.load_raw(raw_dir), config["hrrr"], span, r
        ),
    }

    targets = {name: output_dir / OUTPUTS[name] for name in requested}
    targets["report"] = output_dir / REPORT_NAME
    existing = [p for p in targets.values() if p.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            f"{existing[0]} already exists; pass --overwrite to replace it."
        )

    report = ScreeningReport()
    written: dict[str, Path] = {}
    for name in requested:
        print(f"Cleaning {name}...", flush=True)
        try:
            frame = steps[name](report)
        except FileNotFoundError as missing:
            if explicit:
                raise
            print(f"  skipped: {missing}", flush=True)
            continue
        write_atomic(frame.to_parquet, targets[name])
        written[name] = targets[name]
        print(f"  {len(frame):,} rows -> {targets[name].name}", flush=True)

    write_atomic(lambda p: report.frame().to_csv(p, index=False), targets["report"])
    written["report"] = targets["report"]
    print(f"Screening dropped or masked {report.total:,} row(s) in total.", flush=True)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", choices=tuple(OUTPUTS))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    run(
        args.source,
        config_path=args.config,
        raw_dir=args.raw_dir,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
