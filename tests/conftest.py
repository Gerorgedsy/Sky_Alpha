import json
from pathlib import Path

import pytest

from skydelay.pipeline.airports import AirportRegistry

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "sources.json"


@pytest.fixture
def config():
    return json.loads(CONFIG_PATH.read_text())


@pytest.fixture
def registry(config):
    return AirportRegistry.from_config(config)
