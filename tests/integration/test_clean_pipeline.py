import pandas as pd
import pytest
from tests_support import write_raw

from skydelay.pipeline import clean

pytest.importorskip("pyarrow")


def test_clean_writes_available_sources_and_skips_missing_ones(tmp_path):
    raw_dir = write_raw(tmp_path / "raw")
    out_dir = tmp_path / "processed"
    written = clean.run(raw_dir=raw_dir, output_dir=out_dir)

    # OpenSky states were never collected, so that table is skipped, not an error.
    assert set(written) == {"bts", "opensky-flights", "hrrr", "report"}
    flights = pd.read_parquet(written["bts"])
    assert len(flights) == 1
    weather = pd.read_parquet(written["hrrr"])
    assert weather["hub"].tolist() == ["ATL"]


def test_naming_a_missing_source_is_an_error(tmp_path):
    raw_dir = write_raw(tmp_path / "raw")
    with pytest.raises(FileNotFoundError):
        clean.run(["opensky-states"], raw_dir=raw_dir, output_dir=tmp_path / "out")


def test_existing_outputs_are_protected(tmp_path):
    raw_dir = write_raw(tmp_path / "raw")
    clean.run(raw_dir=raw_dir, output_dir=tmp_path / "out")
    with pytest.raises(FileExistsError):
        clean.run(raw_dir=raw_dir, output_dir=tmp_path / "out")
