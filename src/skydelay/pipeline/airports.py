"""The hubs the project covers, and the geometry every source needs from them.

Every source refers to airports differently: BTS by IATA code (``ATL``),
OpenSky by ICAO code (``KATL``) or not at all (a bare position), HRRR by
latitude and longitude. The registry is the one place those meet.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class Airport:
    iata: str
    icao: str
    name: str
    latitude: float
    longitude: float
    timezone: str


def haversine_km(
    lat1: np.ndarray | float,
    lon1: np.ndarray | float,
    lat2: np.ndarray | float,
    lon2: np.ndarray | float,
) -> np.ndarray:
    """Great-circle distance in kilometres."""
    lat1, lon1, lat2, lon2 = (np.radians(v) for v in (lat1, lon1, lat2, lon2))
    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


@dataclass(frozen=True)
class AirportRegistry:
    airports: tuple[Airport, ...]

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> AirportRegistry:
        entries: Sequence[Mapping[str, Any]] = config["hubs"]["airports"]
        airports = tuple(Airport(**entry) for entry in entries)
        codes = [a.iata for a in airports]
        if len(set(codes)) != len(codes):
            raise ValueError("Hub IATA codes must be unique.")
        return cls(airports=airports)

    @classmethod
    def from_path(cls, path: Path) -> AirportRegistry:
        with path.open(encoding="utf-8") as config_file:
            return cls.from_config(json.load(config_file))

    @property
    def iata_codes(self) -> frozenset[str]:
        return frozenset(a.iata for a in self.airports)

    def by_iata(self, code: str) -> Airport:
        for airport in self.airports:
            if airport.iata == code:
                return airport
        raise KeyError(f"{code} is not a configured hub.")

    def icao_to_iata(self, codes: pd.Series) -> pd.Series:
        """Map ICAO codes to hub IATA codes; anything else becomes missing."""
        lookup = {a.icao: a.iata for a in self.airports}
        return codes.astype("string").str.strip().str.upper().map(lookup)

    def timezones(self) -> dict[str, str]:
        return {a.iata: a.timezone for a in self.airports}

    def nearest_hub(
        self, latitude: pd.Series, longitude: pd.Series, radius_km: float
    ) -> tuple[pd.Series, pd.Series]:
        """Assign each position to the nearest hub within ``radius_km``.

        Returns the hub code (missing beyond the radius) and the distance to
        the nearest hub. New York's three airports sit within 30 km of each
        other, so the nearest-hub rule matters: a position is counted once,
        for the closest airport, never for every airport whose circle it
        falls in.
        """
        lat = latitude.to_numpy(dtype="float64")[:, None]
        lon = longitude.to_numpy(dtype="float64")[:, None]
        hub_lat = np.array([a.latitude for a in self.airports])[None, :]
        hub_lon = np.array([a.longitude for a in self.airports])[None, :]
        distances = haversine_km(lat, lon, hub_lat, hub_lon)
        nearest = np.nanargmin(np.where(np.isnan(distances), np.inf, distances), axis=1)
        distance = distances[np.arange(len(nearest)), nearest]
        codes = np.array([a.iata for a in self.airports], dtype=object)[nearest]
        within = distance <= radius_km
        hub = pd.Series(
            np.where(within, codes, None), index=latitude.index, dtype="string"
        )
        return hub, pd.Series(distance, index=latitude.index)
