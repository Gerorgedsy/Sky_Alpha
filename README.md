# SkyDelay-Alpha

Predictive airline on-time performance and congestion forecasting from open
data: forecast departure delays at the 30 busiest US hubs two to six hours
ahead, using aircraft transponder positions and high-resolution weather
forecasts instead of lagging airline reports.

## Team

- Debshree Chowdhury
- George Deng

## Mid-semester status

The ingestion and cleaning pipeline is implemented for BTS, NOAA HRRR and
OpenSky. The repository now also includes:

- a leakage-safe schedule feature table;
- a schedule-only logistic-regression baseline with a temporal holdout;
- a probability-focused evaluation scorecard;
- a no-install pilot dashboard under `demo/`;
- the project definitions, research plan and risks in
  [`docs/midsemester.md`](docs/midsemester.md).

The demo uses clearly labeled illustrative scenarios. It does not report
trained-model performance. Real metrics are written only after running the
baseline against cleaned BTS data.

## Project scope

- **Target:** whether a scheduled departure from a hub leaves 30 or more
  minutes late, and the delay in minutes, 2-6 hours before departure.
- **Coverage:** the 30 busiest continental US hubs (`config/sources.json`).
  Honolulu is excluded because the HRRR weather grid covers only the
  continental US; Chicago Midway takes its place.
- **Success metrics (from the pitch):** over 85 % accuracy on delays above
  30 minutes; under 5 seconds inference latency.

## Data

All three sources are free.

| Source | What it gives | Role | Reach |
|---|---|---|---|
| US DOT BTS On-Time Performance | Every scheduled flight with actual times, taxi times, delays and causes | Labels, historical features | Historical, published ~2 months late |
| NOAA HRRR (AWS open data) | 3 km hourly weather forecasts: reflectivity, visibility, gusts, wind, ceiling, precipitation | Weather features | Historical archive from 2014, and live |
| OpenSky Network | Flights by airport; live aircraft positions (ADS-B) | Congestion features | Flights: historical (yesterday or earlier). Positions: **live only** |

**OpenSky positions cannot be downloaded for the past.** Anonymous requests
get only the current sky, and authenticated ones reach back one hour. Apron
congestion features built from positions therefore need an archive collected
going forward with `--source opensky-states`. Historical congestion is
available from OpenSky's flights-by-airport data, and taxi-out times from BTS.

## Repository structure

- `src/skydelay/pipeline/`: ingestion and cleaning
- `src/skydelay/features/`: feature engineering (spatial grid, taxi-out delta, sector density)
- `src/skydelay/models/`, `src/skydelay/evaluation/`: models and evaluation
- `demo/`: interactive pilot risk board with threshold and fallback controls
- `docs/midsemester.md`: customer discovery, data, modeling, evaluation and risk plan
- `config/sources.json`: hubs (codes, coordinates, timezones), study window, source settings
- `tests/unit/`, `tests/integration/`: tests, runnable offline
- `data/raw/`, `data/processed/`: downloaded and cleaned data (not committed)

## Setup

```bash
python -m venv .VENV
source .VENV/bin/activate        # Windows: .VENV\Scripts\Activate.ps1
pip install -r requirements.txt
pre-commit install --hook-type commit-msg
cp .env.example .env             # then add OpenSky API client credentials
```

OpenSky works without credentials, but with 400 credits a day instead of
4,000. Create an API client on your OpenSky account page and put its ID and
secret in `.env`. Commits follow
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/).

## Ingestion

```bash
PYTHONPATH=src python -m skydelay.pipeline.ingest --source bts
PYTHONPATH=src python -m skydelay.pipeline.ingest --source opensky-flights
PYTHONPATH=src python -m skydelay.pipeline.ingest --source hrrr --start 2024-07-01 --end 2024-07-08
PYTHONPATH=src python -m skydelay.pipeline.ingest --source opensky-states --polls 12
```

- The window comes from `config/sources.json`; `--start`/`--end` narrow it.
- Every source **resumes**: re-run the same command after an interruption and
  finished files are skipped. Files are written under a temporary name and
  renamed when complete, so a half-written file never looks finished.
- **BTS** downloads one zip per month and keeps only hub flights and the
  configured columns.
- **HRRR** reads `.idx` sidecars and fetches only the wanted GRIB messages by
  byte range, the same approach project 1 used for GFS. One message serves all
  30 hubs. It is still the largest download: try a week before a year, and
  thin `cycle_hours_utc` or `leads` in the config if needed.
- **OpenSky flights** fetch departures and arrivals per hub in one-day pieces
  (the API allows two days per request).
- **OpenSky states** poll one continental bounding box (4 credits per poll,
  every hub at once) and save each snapshot.

## Cleaning

```bash
PYTHONPATH=src python -m skydelay.pipeline.clean
```

Writes to `data/processed/`: `flights.parquet` (BTS), `opensky_flights.parquet`,
`opensky_states.parquet`, `weather.parquet` (HRRR) and `screening_report.csv`.
Sources with no raw data are skipped with a message; `--source` cleans a
subset and makes missing data an error.

What cleaning does, by source:

- **BTS.** Clock times are local `hhmm` at each airport. They are converted to
  UTC once, here: the hub's timezone gives the scheduled departure (or the
  scheduled arrival, when only the destination is a hub). Actual times are
  scheduled times plus the published delay minutes, so a flight that slips past
  midnight is dated correctly. `2400` is read as midnight at the end of the day.
  Local times in the daylight-saving gap or overlap are dropped and counted.
  `dep_delayed_30` / `arr_delayed_30` are the labels; they are missing, not
  false, for cancelled flights.
- **OpenSky flights.** ICAO airport estimates (`KATL`) become hub IATA codes;
  the same flight seen in a departure file and an arrival file is kept once.
- **OpenSky states.** Positions older than 60 seconds at snapshot time are
  dropped, and each aircraft is assigned to the *nearest* hub within 40 km, so
  New York's three airports never count the same aircraft twice.
- **HRRR.** Kelvin to Celsius, wind components to speed and the direction the
  wind blows from, precipitation rate to mm/h, and "no ceiling" kept as a flag
  rather than a 25 km cloud.

Every dropped row and masked value is counted in `screening_report.csv`.

### Leakage control

As in project 1, a value carries the time it describes and the time it became
known. HRRR rows have `init_time_utc`, `valid_time_utc` and
`available_at_utc` (start of the run plus the configured publication lag). A
feature for a prediction made at time *t* may use only rows available by *t*.
BTS rows are known only after the flight, which is why they are labels and
historical features, never live inputs.

### Before the first full run

- Check hub coordinates and timezones in `config/sources.json`.
- Run one BTS month, one HRRR day and one OpenSky day first, and look at the
  outputs and the screening report.
- Confirm the HRRR `available_after_minutes` lag against when cycles actually
  appear in the archive.

## Schedule baseline

After cleaning BTS data, train the first two-hour pilot baseline with:

```bash
PYTHONPATH=src python -m skydelay.models.train_baseline \
  --flights data/processed/flights.parquet \
  --test-days 30
```

The final 30 days form the holdout. The command writes the fitted model to
`artifacts/models/` and a JSON scorecard to `artifacts/metrics/`. The scorecard
includes Brier score, calibration error, PR-AUC, recall, precision, F1,
accuracy and a naive-prevalence Brier benchmark.

## Pilot demo

Open [`demo/index.html`](demo/index.html) in any browser. Select ATL, ORD or
JFK, adjust the alert threshold, and simulate an ADS-B outage to inspect the
weather-only fallback state.

The probabilities are representative UI-test scenarios until trained model
artifacts are connected.
