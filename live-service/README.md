# SkyDelay live airspace API

The website does not depend on this backend alone: when it is unreachable, the airport-conditions and flight-board sections fall back to the snapshots that the **Live airport data** GitHub workflow publishes on the `live-data` branch (see `src/skydelay/live/snapshot.py`). The live airspace plot still needs this backend.

A Cloudflare Worker backend for the SkyDelay GitHub Pages prototype. `GET /api/aircraft` returns observed aircraft positions around New York, sourced from [adsb.lol](https://www.adsb.lol/docs/open-data/api) under ODbL 1.0. `GET /api/operations` returns current FAA NAS airport advisories and NOAA Aviation Weather Center observations for JFK, EWR, and LGA. `GET /api/schedules?airport=JFK` (or EWR/LGA) returns `not_configured` until an AirLabs key is supplied, then a partial live schedule board. It does not return SkyDelay model forecasts or confidence, and none of the sources guarantees complete coverage.

The worker stores one shared upstream snapshot in D1 for 210 seconds by default, serves the last snapshot for up to 15 minutes when the feed fails, and reports `status: "stale"` or `503` so the UI can label it honestly. The browser checks the backend every 30 seconds. Anonymous API use is limited to 500 requests per day per IP by the source; upstream limits may affect availability under traffic. `ADSB_LOL_API_KEY` and `REFRESH_SECONDS` are optional runtime settings for a higher-quota account. Do not commit a key.

The FAA/weather snapshot is cached for five minutes. Optional AirLabs schedules are cached for four hours per airport to stay within its limited free plan; at most 50 flights are returned per airport request. Set `AIRLABS_API_KEY` as a Sites runtime secret after signing up at [AirLabs](https://airlabs.co/signup). The free response may omit gate, terminal, or status. A current schedule alone is insufficient for a forecast: the historical LightGBM model in the repository is source code and a 2023 replay export, not a deployable trained artifact. Connect and validate an artifact before enabling a numeric confidence level.

Only the SkyDelay GitHub Pages origin and local development origins receive browser CORS permission. The endpoint contains public aircraft observations and no user data.

This directory is mirrored from the Sites Worker source used to host the backend. Deployment requires a public, readable Site for the GitHub Pages front end. The Site project ID is in `.openai/hosting.json`.
