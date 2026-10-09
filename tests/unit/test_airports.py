import pandas as pd

from skydelay.pipeline.airports import haversine_km


def test_thirty_unique_conus_hubs(registry):
    assert len(registry.airports) == 30
    assert "HNL" not in registry.iata_codes
    assert all(a.icao == "K" + a.iata for a in registry.airports)


def test_haversine_matches_a_known_distance():
    # JFK to LAX is about 3,980 km great-circle.
    jfk_lax = haversine_km(40.6413, -73.7781, 33.9416, -118.4085)
    assert 3950 < float(jfk_lax) < 4010


def test_a_position_counts_for_the_nearest_new_york_airport_only(registry):
    # A point just off LaGuardia's runways is inside all three NY circles.
    hub, distance = registry.nearest_hub(pd.Series([40.78]), pd.Series([-73.87]), 40)
    assert hub.tolist() == ["LGA"]
    assert float(distance.iloc[0]) < 2


def test_positions_beyond_the_radius_get_no_hub(registry):
    # Mid-Kansas is far from every hub.
    hub, _ = registry.nearest_hub(pd.Series([38.5]), pd.Series([-98.0]), 40)
    assert hub.isna().all()


def test_icao_codes_map_to_hub_iata(registry):
    mapped = registry.icao_to_iata(pd.Series(["KATL", " kjfk", "EGLL", None]))
    assert mapped.tolist()[:2] == ["ATL", "JFK"]
    assert mapped.iloc[2:].isna().all()
