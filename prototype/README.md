# SkyDelay prototype

The default [`index.html`](index.html) is a **forecast workspace** for the New York region. Its **Delay predictions** board (`predictions.js`, `predictions.css`) reads [`data/predictions.json`](data/predictions.json) and shows, per airport (JFK, LGA, EWR), each upcoming departure's delayed / on-time call and model confidence, with search, filters, sorting and summary counts. It re-reads the file every five minutes, labels predictions older than the file's `stale_after_minutes` as out of date, and shows an explicit empty state before the first publish. `?sample=1` loads [`data/predictions.sample.json`](data/predictions.sample.json), illustrative data under a visible banner; `?airport=LGA` opens a given airport. Regenerate the live file with `python -m skydelay.models.publish_predictions` (see the root README). It uses a public, read-only backend to show live FAA airport advisories, NOAA aviation weather observations, and aircraft positions from [adsb.lol](https://www.adsb.lol/docs/open-data/api) under ODbL 1.0. The page checks aircraft positions every 30 seconds; the backend refreshes that source about every 3½ minutes without a key. Weather and FAA data refresh every five minutes. Stale and unavailable states are explicit.

The schedule area links to the official JFK, EWR, and LGA flight boards, where users can verify gates and flight status. An optional backend `AIRLABS_API_KEY` connects the [AirLabs Schedules API](https://airlabs.co/docs/schedules) for a partial in-page departures list. The free plan has limited coverage and may omit gate or status fields; the UI shows missing values rather than inventing them. AirLabs data is operational status, not a SkyDelay model forecast. Store any key only as a backend runtime secret, never in GitHub Pages source.

The [`replay.html`](replay.html) page preserves the 7,399-flight November 18–24, 2023 delay forecast backtest. It supports flight and route search, date selection, sorting, and per-flight details. The median delay was forecast two hours before departure. Confidence is the estimated chance that actual delay falls within ±15 minutes of the median; the 90th percentile is an upper forecast threshold, not a guaranteed worst case. The replay is not current flight status.

## Source and rebuild

- `index.html`, `live.js`, `operations.js`, `live.css`: forecast workspace and live operational views.
- `predictions.js`, `predictions.css`, `data/`: delay / on-time prediction board and its published data.
- `replay.html`, `app.js`: historical forecast replay and interaction.
- `styles.css`, `favicon.svg`: shared presentation.
- `template.html`: replay markup with an embedded-data placeholder.
- `build_site.py`: rebuilds `replay.html` from the template while preserving the embedded dataset.
- `flight_model.py`: research export script; it expects local input files and can produce a replacement `site_data.json` in its configured environment.
- `../live-service/`: backend source, shared D1 cache, migration, and tests. This is published separately from GitHub Pages.

After changing `template.html`, run `python3 prototype/build_site.py` from the repository root. To replace the replay dataset, run `python3 prototype/build_site.py --data path/to/site_data.json`. Changes to either JavaScript file do not need a build.

## Deployment

GitHub Pages serves `prototype/index.html` at `https://gerorgedsy.github.io/Sky_Alpha/prototype/` from the configured branch. Push the `prototype/` files to update the public frontend. The backend is a separate public Sites Worker with its project ID in `live-service/.openai/hosting.json`; changes to `live-service/` also require a new Sites deployment. No API key is committed. For a faster upstream refresh, obtain an adsb.lol key, store it as a backend runtime secret, and adjust `REFRESH_SECONDS` within the provider's quota.
