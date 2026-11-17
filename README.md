# SkyDelay NYC delay models

Written by the "Train delay models" workflow. `departure.joblib` and
`arrival.joblib` are `skydelay.models.nyc_delay.DelayModel` bundles;
`metrics.json` holds their holdout scorecards; `eda.json` the dataset statistics; `schedule.parquet` is the
two-week schedule reference for the stand-in schedule. Load them with
the scikit-learn version pinned in `requirements.txt`.
