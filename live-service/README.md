# SkyDelay live airspace API

A Cloudflare Worker backend for the SkyDelay GitHub Pages prototype. `GET /api/aircraft` returns observed aircraft positions around New York, sourced from [adsb.lol](https://www.adsb.lol/docs/open-data/api) under ODbL 1.0. It does not return schedules, airline status, route, delay forecasts, or guarantee complete coverage.

The worker stores one shared upstream snapshot in D1 for 210 seconds by default, serves the last snapshot for up to 15 minutes when the feed fails, and reports `status: "stale"` or `503` so the UI can label it honestly. The browser checks the backend every 30 seconds. Anonymous API use is limited to 500 requests per day per IP by the source; upstream limits may affect availability under traffic. `ADSB_LOL_API_KEY` and `REFRESH_SECONDS` are optional runtime settings for a higher-quota account. Do not commit a key.

Only the SkyDelay GitHub Pages origin and local development origins receive browser CORS permission. The endpoint contains public aircraft observations and no user data.

This directory is mirrored from the Sites Worker source used to host the backend. Deployment requires a public, readable Site for the GitHub Pages front end. The Site project ID is in `.openai/hosting.json`.
