# SkyDelay prototype

The default [`index.html`](index.html) is a **live aircraft position view** for the New York region. It uses a public, read-only backend at `https://skyalpha-live-airspace.headybuttercup5.chatgpt.site/api/aircraft` to show observations from [adsb.lol](https://www.adsb.lol/docs/open-data/api) under ODbL 1.0. The page checks for a newer shared snapshot every 30 seconds; the backend refreshes the source about every 3½ minutes without an API key. It shows observation age, stale or unavailable states, and incomplete coverage explicitly. It does **not** show live airline schedules, gate status, routes, or delay predictions.

The [`replay.html`](replay.html) page preserves the 7,399-flight November 18–24, 2023 delay forecast backtest. It supports flight and route search, date selection, sorting, and per-flight details. The median delay was forecast two hours before departure. Confidence is the estimated chance that actual delay falls within ±15 minutes of the median; the 90th percentile is an upper forecast threshold, not a guaranteed worst case. The replay is not current flight status.

## Source and rebuild

- `index.html`, `live.js`, `live.css`: live view and interaction.
- `replay.html`, `app.js`: historical forecast replay and interaction.
- `styles.css`, `favicon.svg`: shared presentation.
- `template.html`: replay markup with an embedded-data placeholder.
- `build_site.py`: rebuilds `replay.html` from the template while preserving the embedded dataset.
- `flight_model.py`: research export script; it expects local input files and can produce a replacement `site_data.json` in its configured environment.
- `../live-service/`: backend source, shared D1 cache, migration, and tests. This is published separately from GitHub Pages.

After changing `template.html`, run `python3 prototype/build_site.py` from the repository root. To replace the replay dataset, run `python3 prototype/build_site.py --data path/to/site_data.json`. Changes to either JavaScript file do not need a build.

## Deployment

GitHub Pages serves `prototype/index.html` at `https://gerorgedsy.github.io/Sky_Alpha/prototype/` from the configured branch. Push the `prototype/` files to update the public frontend. The backend is a separate public Sites Worker with its project ID in `live-service/.openai/hosting.json`; changes to `live-service/` also require a new Sites deployment. No API key is committed. For a faster upstream refresh, obtain an adsb.lol key, store it as a backend runtime secret, and adjust `REFRESH_SECONDS` within the provider's quota.
