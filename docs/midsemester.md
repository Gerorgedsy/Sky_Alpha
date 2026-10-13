# Mid-semester project definition

This document separates completed engineering work from planned research. It
supports the October 14 mid-semester presentation and should be updated when
new evidence or results become available.

## Project and user decision

SkyDelay-Alpha estimates the probability that a scheduled departure from ATL,
ORD or JFK will leave at least 30 minutes late. The prediction is made two
hours before scheduled departure using only information available at that
cutoff.

The primary user is an airline or hub operations analyst deciding whether to
review gate sequencing, ground staffing or other disruption preparations.

## Customer discovery

### Current status

No verified customer-interview notes are stored in the repository. The team
must not present hypothetical findings as completed research.

### Interview sample

Recruit at least five participants:

1. two people with airline, airport or aviation-operations experience;
2. one person who works with operational analytics or alerting systems;
3. two Duke students who can act as operations-dashboard usability proxies.

### Core questions

1. How do you identify a developing hub disruption today?
2. Which decision can still change two hours before departure?
3. Which is more costly: a missed disruption or a false alert?
4. What evidence would make a probability actionable?
5. How should the system behave when a live data feed fails?
6. Which requested feature would add complexity without changing a decision?

Record the participant role, current workflow, quoted or paraphrased finding,
and the product decision it changed. Store the notes in a private approved
location if they contain identifying information; commit only an anonymized
summary.

## Datasets

| Dataset | Role | Availability | Current treatment |
|---|---|---|---|
| BTS On-Time Performance | Departure-delay labels and historical schedule fields | Published with an approximate two-month lag | Training and evaluation only |
| NOAA HRRR | Forecast wind, visibility, precipitation, temperature, ceiling and reflectivity | Historical and live forecasts | Preserve initialization, valid and available times |
| OpenSky flights | Historical airport movements | Previous day or earlier | Candidate historical movement proxy |
| OpenSky state vectors | Live aircraft position and movement | Live or at most a short lookback | Collect prospectively; do not claim a historical archive |

## Features

### Implemented pilot features

- hub, carrier and route;
- scheduled departure hour, weekday and month;
- scheduled block time and distance;
- scheduled departure counts during the prior 15, 30 and 60 minutes.

These features are known before the two-hour prediction cutoff. Actual times,
taxi times, cancellations and delay causes are excluded from the feature set.

### Planned weather and movement features

- latest HRRR forecast available by the prediction cutoff;
- wind components relative to runway orientation;
- visibility, ceiling, reflectivity and precipitation;
- historical OpenSky arrival/departure counts;
- live surface and terminal-aircraft density once enough states are archived.

## Baselines and model development

1. **Naive prevalence:** predict the training-period delayed-departure rate.
2. **Schedule baseline:** logistic regression using the implemented schedule
   features. This model is now implemented and can be trained with
   `python -m skydelay.models.train_baseline` after cleaned BTS data exists.
3. **Schedule plus weather:** add point-in-time HRRR features to measure the
   value of weather.
4. **Full model:** evaluate gradient-boosted trees after movement features are
   available. Compare its lift with both baselines before adopting it.

The final model must support calibrated probabilities. Feature-contribution
views are a product requirement, but a specific explanation method will be
selected only after the model family is chosen.

## Evaluation metrics

| Metric | Purpose |
|---|---|
| Brier score | Primary probability-quality measure; compare with naive prevalence |
| Expected calibration error | Check whether displayed probabilities match observed frequency |
| PR-AUC | Measure rare-event ranking under class imbalance |
| Recall at an alert threshold | Estimate the share of disruptions caught |
| Precision and F1 | Quantify alert quality and threshold trade-offs |
| Accuracy and ROC-AUC | Secondary model diagnostics |
| Per-hub calibration | Detect geographic performance differences |
| Inference latency | Confirm the operational target of less than five seconds |

Use rolling time splits and a final held-out future month. Do not use a random
split for headline results.

## Pilot demo

The static browser demo under `demo/` validates the proposed interaction before
model training finishes. It includes airport selection, driver visibility,
alert-threshold control and a weather-only fallback state. All displayed
probabilities are explicitly marked illustrative.

## Next steps

1. Complete a small real-data ingestion run and audit the screening report.
2. Build the three-airport modeling table and run the schedule baseline.
3. Add point-in-time HRRR joins and compare the weather baseline.
4. Conduct and summarize customer interviews.
5. Add the historical movement proxy and test its incremental lift.
6. Calibrate the strongest model and connect it to the pilot interface.
7. Run a task-based usability test and simulated disruption replay.

## Challenges, risks and mitigations

| Risk | Effect | Mitigation |
|---|---|---|
| Historical ADS-B state vectors are unavailable | The advertised density feature cannot be trained retrospectively | Use historical movements for the MVP and collect states prospectively |
| Thirty-hub scope is too broad | Slower iteration and weak per-hub evidence | Lock the first experiment to ATL, ORD and JFK |
| Point-in-time joins can leak future information | Inflated offline performance | Filter every feature on `available_at <= prediction_cutoff` |
| Delays are imbalanced | Accuracy can look strong while alerts fail | Use Brier score, PR-AUC and threshold scorecards |
| Coverage differs across hubs and weather conditions | Aggregate metrics can hide weak locations | Report missingness and calibration by hub |
| High recall may cause alert fatigue | Analysts may ignore the product | Let users set a threshold and evaluate recall at an alert budget |
| External APIs can rate-limit or change schema | Pipeline interruption | Retain raw files, validate schemas and provide fallback modes |

