# SkyDelay-Alpha

Predictive airline on-time performance and congestion forecasting from open
data: forecast departure delays at the 30 busiest US hubs two to six hours
ahead, using aircraft transponder positions and high-resolution weather
forecasts instead of lagging airline reports.

**Website:** [gerorgedsy.github.io/Sky_Alpha/prototype](https://gerorgedsy.github.io/Sky_Alpha/prototype/)

![SkyDelay NYC website: night-airfield hero with flight search and a live card of FAA and weather status for JFK, LGA and EWR](docs/images/website-hero.jpg)

## Team

- Debshree Chowdhury
- George Deng

## Mid-semester status

The ingestion and cleaning pipeline is implemented for BTS, NOAA HRRR and
OpenSky. The repository now also includes:

- a leakage-safe schedule feature table;
- schedule-only and schedule-plus-weather logistic-regression baselines with a
  shared temporal holdout;
- a probability-focused evaluation scorecard;
- a no-install pilot dashboard under `demo/`;
- a public New York website (`prototype/`) with departure and arrival
  delay predictions and model confidence, airport flight boards, live FAA and
  weather conditions, and a scheduled workflow that keeps its data current;
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
- `src/skydelay/models/`, `src/skydelay/evaluation/`: models and evaluation,
  including the daily NYC delay models (`nyc_delay`), `train_baseline
  --direction arrival` and `publish_predictions`
- `src/skydelay/live/`: snapshots of FAA, NOAA and AirLabs data for the website
- `prototype/`: the public website (GitHub Pages) and the 2023 forecast replay
- `live-service/`: optional Cloudflare Worker backend for live feeds
- `.github/workflows/`: CI, the monthly **Train delay models** job and the
  half-hourly **Live airport data** refresh
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

## Baseline models

The two baselines also have separate, directly runnable entry points:

- [`baseline_1_schedule.py`](src/skydelay/models/baseline_1_schedule.py):
  schedule-only logistic regression;
- [`baseline_2_schedule_weather.py`](src/skydelay/models/baseline_2_schedule_weather.py):
  schedule plus point-in-time HRRR logistic regression.

Run either model independently with:

```bash
PYTHONPATH=src python -m skydelay.models.baseline_1_schedule
PYTHONPATH=src python -m skydelay.models.baseline_2_schedule_weather
```

After cleaning BTS and HRRR data, train and compare both two-hour pilot
baselines with:

```bash
PYTHONPATH=src python -m skydelay.models.train_baseline \
  --baseline both \
  --flights data/processed/flights.parquet \
  --weather data/processed/weather.parquet \
  --test-days 30
```

Baseline 1 uses schedule, route, calendar and recent scheduled-departure
density. Baseline 2 adds the newest HRRR forecast available at the two-hour
cutoff. The join rejects weather cycles published after the cutoff and retains
an explicit availability flag when no eligible forecast exists.

The final 30 days form the shared holdout. The command writes two fitted models
to `artifacts/models/` and a comparison scorecard to `artifacts/metrics/`.
The scorecard includes Brier score, calibration error, PR-AUC, recall,
precision, F1, accuracy, weather coverage and improvement over Baseline 1.

## Live SkyDelay prototype

The [public website](https://gerorgedsy.github.io/Sky_Alpha/prototype/) is a
dark, glass-style flight-intelligence page for New York's three airports. From
top to bottom:

| Section | What it shows | Data |
|---|---|---|
| Hero | Flight / destination / airport search, shortcuts, and a **Right now** card with each airport's FAA status and weather | FAA NAS, NOAA METAR |
| Delay predictions | Every departure and arrival today and tomorrow called *delayed* (30+ min) or *on time*, with model confidence, expected delay in minutes and estimated time; Departures / Arrivals switch, per-airport tabs, summary tiles, search, filters and sort | `predictions.json` on `live-data` |
| Flight boards | Recent and in-air departures and arrivals with terminal, gate or baggage, status and how late they ran | AirLabs (free plan) |
| Airport conditions | FAA ground stops, delay programs and closures, plus flight category, wind and visibility | FAA NAS, NOAA METAR |
| Live airspace | Aircraft positions around New York on a plot and in a searchable list | adsb.lol via `live-service/` |
| [Replay](prototype/replay.html) | The November 18–24, 2023 forecast backtest | embedded export |

The heading reads *Will your flight leave on time?* for departures and
*…arrive on time?* for arrivals. The predictions board says plainly when nothing
has been published yet; add `?sample=1` to the URL to preview it with
illustrative data, `?view=arrivals` to open the arrivals board, or
`?airport=LGA` to open an airport. The night-airfield illustration is original
artwork drawn as SVG by `prototype/assets/build_hero_scene.py`. See
[`prototype/README.md`](prototype/README.md) for files and deployment.

### Live airport data

The page's **Airport conditions** and **Flight schedules and gates** sections
try the live backend first and otherwise read snapshots from the `live-data`
branch. The [Live airport data](.github/workflows/live-data.yml) workflow
writes them twice an hour with `python -m skydelay.live.snapshot`:

- `operations.json`: FAA NAS airport events and NOAA METAR weather for JFK,
  EWR and LGA (no key needed);
- `schedules.json`: departures and arrivals per airport from AirLabs, with
  terminal, gate, baggage and status. Add a free key from
  [airlabs.co](https://airlabs.co/signup) as the repository secret
  `AIRLABS_API_KEY` (Settings → Secrets and variables → Actions). Schedules are
  refreshed on a budget that fits the free plan's 1,000 monthly requests; run the workflow by hand with *force schedules* to refresh now.
  Departures refresh every three hours (they feed the delay model) and
  arrivals every twelve, about 900 requests a month.

### Daily delay predictions

Every flight scheduled at JFK, LGA and EWR today and tomorrow, departures and
arrivals, gets three things from
[`skydelay.models.nyc_delay`](src/skydelay/models/nyc_delay.py):

- **the call**: *delayed* when the calibrated probability of a 30+ minute
  delay is at least 50 %, otherwise *on time*;
- **the confidence**: the probability of that call (50–100 %), from a
  gradient-boosted classifier calibrated with isotonic regression on a later,
  held-apart month;
- **how late**: the median and 80 % range of the delay in minutes for flights
  like it that went the same way as the call (separate quantile models for
  late and on-time flights), and the estimated time.

Inputs are what is known when the prediction is made
([`nyc_context`](src/skydelay/models/nyc_context.py)):

- **schedule**: airport, airline, the other airport, local time, weekday,
  month, a near-holiday flag, block time, distance, and how busy the airport is
  around the flight;
- **weather**: the latest METAR at the airport (wind, gusts, visibility,
  ceiling, precipitation, thunderstorms, temperature and dew-point spread);
- **how late flights are running**: the mean delay and share of 30+ minute
  delays among the airport's departures in the last three hours, and the
  New York average;
- **how far ahead** the flight is.

Training rebuilds the weather and delay inputs for a random prediction time
up to 30 hours before each historical flight, from the Iowa Environmental
Mesonet METAR archive and the BTS flights that had left by then. Live scoring
reads the NOAA METARs and the AirLabs sample of recent departures that the
site already collects. The live delay sample is about a dozen flights, so
training draws the same number at random, and it hides the live inputs on
some rows so the model still works when a feed is down. The scorecard reports
accuracy with and without the live inputs and by how far ahead the prediction
was made.

**Stand-in schedule.** No free feed lists tomorrow's flights, so the flights
and times are copied from the latest same-weekday flights in BTS records onto
the target day at the same local time. The page says so; real schedules
change, especially across seasons.

**Automation.**

| Workflow | When | What |
|---|---|---|
| [Train delay models](.github/workflows/train-models.yml) | 12th of each month, or by hand | Finds the newest BTS month, downloads 12 months for New York, cleans them, trains and evaluates both directions, and force-pushes `departure.joblib`, `arrival.joblib`, `metrics.json` and `schedule.parquet` to the `model-artifacts` branch; the run summary shows the holdout scorecard |
| [Live airport data](.github/workflows/live-data.yml) | twice an hour | Fetches weather and FAA status (every run) and AirLabs departures (every 3 h) and arrivals (every 12 h), re-scores today and tomorrow with them, and publishes `predictions.json` to the `live-data` branch |

The page reads `predictions.json` from `live-data` and falls back to
`prototype/data/predictions.json`. To run the steps yourself:

```bash
PYTHONPATH=src python -m skydelay.models.nyc_delay prepare --work work --months 12
PYTHONPATH=src python -m skydelay.models.nyc_delay train \
  --flights work/processed/flights.parquet --metar work/processed/metar.csv \
  --output artifacts/nyc
PYTHONPATH=src python -m skydelay.models.nyc_delay predict \
  --artifacts artifacts/nyc --output prototype/data/predictions.json \
  --operations live/operations.json --schedules live/schedules.json
```

The holdout scorecard (`metrics.json`) reports accuracy, precision, recall,
Brier score against the base rate, calibration by probability band, delay
error in minutes against predicting zero, and range coverage. Check it before
quoting the model's accuracy; the pitch target is 85 % on 30+ minute delays.

### Publishing with the baseline models

`skydelay.models.publish_predictions` scores an upcoming schedule you supply
with the logistic-regression baselines from `train_baseline` (two-hour
horizon, optionally with HRRR weather) and writes the same file format. It
needs a schedule with `carrier`, `flight_number`, `origin`, `dest`,
`sched_block_min`, `distance_mi` and `sched_dep_utc` and/or `sched_arr_utc`:

```bash
PYTHONPATH=src python -m skydelay.models.publish_predictions \
  --schedule data/live/upcoming_flights.csv \
  --departure-model artifacts/models/baseline_2_schedule_weather.joblib \
  --arrival-model artifacts/models/baseline_2_schedule_weather_arrival.joblib \
  --weather data/processed/weather.parquet \
  --metrics artifacts/metrics/baseline_comparison.json \
  --arrival-metrics artifacts/metrics/baseline_comparison_arrival.json \
  --metrics-key baseline_2
```

The baselines use balanced class weights, so their raw probabilities overstate
delay risk; calibrate before treating their confidence as a hit rate.

## Pilot demo

Open [`demo/index.html`](demo/index.html) in any browser. Select ATL, ORD or
JFK, adjust the alert threshold, and simulate an ADS-B outage to inspect the
weather-only fallback state.

The probabilities are representative UI-test scenarios until trained model
artifacts are connected.
