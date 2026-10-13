"""Evaluation metrics for probabilistic disruption forecasts.

SkyDelay-Alpha is an alerting product, so a single accuracy number is not
enough.  The scorecard combines probability quality, rare-event ranking and
thresholded alert performance.  All functions operate on held-out data.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


def expected_calibration_error(
    y_true: np.ndarray,
    y_probability: np.ndarray,
    *,
    bins: int = 10,
) -> float:
    """Return equal-width expected calibration error.

    Empty bins contribute zero weight.  The result is on a 0-1 scale where
    lower is better.
    """
    if bins < 2:
        raise ValueError("bins must be at least 2")
    truth = np.asarray(y_true, dtype=float)
    probability = np.asarray(y_probability, dtype=float)
    if truth.shape != probability.shape:
        raise ValueError("y_true and y_probability must have the same shape")
    if len(truth) == 0:
        raise ValueError("evaluation arrays cannot be empty")
    if np.any((probability < 0) | (probability > 1)):
        raise ValueError("probabilities must be between 0 and 1")

    edges = np.linspace(0.0, 1.0, bins + 1)
    membership = np.clip(np.digitize(probability, edges[1:-1]), 0, bins - 1)
    error = 0.0
    for index in range(bins):
        mask = membership == index
        if not mask.any():
            continue
        error += float(mask.mean()) * abs(
            float(probability[mask].mean()) - float(truth[mask].mean())
        )
    return error


def probability_scorecard(
    y_true: np.ndarray,
    y_probability: np.ndarray,
    *,
    threshold: float = 0.5,
    calibration_bins: int = 10,
) -> dict[str, Any]:
    """Compute the pilot scorecard on held-out labels and probabilities."""
    truth = np.asarray(y_true, dtype=int)
    probability = np.asarray(y_probability, dtype=float)
    if not 0 < threshold < 1:
        raise ValueError("threshold must be between 0 and 1")
    predicted = probability >= threshold
    prevalence = float(truth.mean())
    naive_probability = np.full(len(truth), prevalence)

    # ROC-AUC is undefined when a holdout contains only one class.  Returning
    # None makes the limitation explicit in the JSON artifact.
    roc_auc = (
        float(roc_auc_score(truth, probability)) if np.unique(truth).size == 2 else None
    )
    pr_auc = (
        float(average_precision_score(truth, probability))
        if np.unique(truth).size == 2
        else None
    )

    return {
        "rows": len(truth),
        "positive_rate": prevalence,
        "threshold": float(threshold),
        "accuracy": float(accuracy_score(truth, predicted)),
        "precision": float(precision_score(truth, predicted, zero_division=0)),
        "recall": float(recall_score(truth, predicted, zero_division=0)),
        "f1": float(f1_score(truth, predicted, zero_division=0)),
        "roc_auc": roc_auc,
        "pr_auc": pr_auc,
        "brier_score": float(brier_score_loss(truth, probability)),
        "naive_brier_score": float(brier_score_loss(truth, naive_probability)),
        "expected_calibration_error": expected_calibration_error(
            truth, probability, bins=calibration_bins
        ),
    }
