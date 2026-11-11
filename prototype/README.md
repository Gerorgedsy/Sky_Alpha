# SkyDelay · Flight Delay Prediction Prototype

A stock-app-style prototype: each row shows a flight, its predicted departure delay, and how much to trust that prediction.

## Open it
- **Locally:** double-click `index.html`.
- **GitHub Pages:** put `index.html` in the repo root → Settings → Pages → Deploy from branch `main` / root. The site will be at `https://<username>.github.io/<repo>/`.

The page is a single self-contained file (data is embedded); no server or build step needed.

## Features
- **航班号 tab:** search a flight number (`DL393`, `B6 1131`, or just `393`). With an empty search, shows the day's highest-risk flights.
- **航线 tab:** pick a NYC origin (EWR / JFK / LGA / all) and type a destination code or city; results are sorted from shortest to longest predicted delay.
- **Date chips:** Dec 18–31, 2023.
- **Tap a flight:** 80% delay range, top 3 drivers (in minutes), and the backtest result (actual delay).

## What the numbers mean
- **预计延误:** median predicted departure delay, made 2 hours before scheduled departure.
- **预测可信度:** calibrated probability that the actual delay lands within ±15 min of the prediction.
- **最坏:** 90th-percentile prediction.

## Data & model
- nycflights23 (all 2023 departures from EWR/JFK/LGA, from BTS On-Time) + hourly airport weather (ASOS).
- LightGBM quantile models (q10/q50/q90) trained on Jan–Aug; confidence model trained on Sep–Oct; Nov–Dec is held-out test.
- Features use only information available at prediction time: carrier, destination, schedule, airport delay state & backlog, NYC-wide state, last weather observation.
- Rebuild: `flight_model.py` → `site_data.json` → inject into `template.html` at `/*__DATA__*/null`.

This is a replay of historical data for demo purposes, not live predictions.
