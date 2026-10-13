# Pilot demo

Open `demo/index.html` in a browser. No installation or API credentials are
required.

The pilot demonstrates the intended analyst workflow:

- compare two-hour disruption risk across ATL, ORD and JFK;
- select an airport and inspect the contributing signals;
- move the alert threshold to see the triage decision change;
- simulate an ADS-B outage and verify the weather-only fallback state.

The scenario probabilities are **illustrative interface-test data**, not
trained-model results. The production dashboard will read probabilities from
the baseline and full-model artifacts after those models complete evaluation.

