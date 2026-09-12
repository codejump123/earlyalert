"""Subgroup metrics with small-cell suppression.

No Django imports. A level with fewer than min_n students is never reported,
in the UI or in stored metrics: with a handful of students an AUC is noise,
and publishing it invites a conclusion the data cannot carry.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .train import recall_at_precision

logger = logging.getLogger("pipeline.fairness")

MIN_N = 20


def subgroup_metrics(
    y_true,
    y_prob,
    groups: pd.DataFrame,
    dims: list[str],
    min_n: int = MIN_N,
) -> dict:
    """Input: the labels, the predicted probabilities, a frame carrying one
    column per dimension, the dimensions to report, and the suppression floor.

    Output:
        {dim: {"levels": {level: entry}, "gap": float | None, "min_n": int}}

    where entry is either
        {"n": n, "auc_roc": float | None, "recall_at_p50": float | None,
         "positives": int, "positive_rate": float}
    or, below the floor,
        {"suppressed": True, "n": n}

    gap is the best-minus-worst AUC-ROC across the reported levels of that
    dimension, or None when fewer than two levels survive suppression. A level
    whose students all share one label has no defined AUC; it is reported with
    auc_roc None rather than suppressed, because its size is not the problem.
    """
    y_true = np.asarray(y_true)
    y_prob = np.asarray(y_prob)
    if len(y_true) != len(groups):
        raise ValueError(
            f"groups has {len(groups)} rows but y_true has {len(y_true)}"
        )

    report: dict[str, dict] = {}
    for dimension in dims:
        if dimension not in groups.columns:
            raise ValueError(f"groups has no column {dimension!r}")
        levels: dict[str, dict] = {}
        for level, index in groups.groupby(
            groups[dimension].astype("object").where(
                groups[dimension].notna(), "not recorded"
            ),
            observed=True,
        ).indices.items():
            count = len(index)
            key = str(level)
            if count < min_n:
                levels[key] = {"suppressed": True, "n": count}
                continue
            subset_true = y_true[index]
            subset_prob = y_prob[index]
            levels[key] = {
                "n": count,
                "positives": int(subset_true.sum()),
                "positive_rate": float(subset_true.mean()),
                "auc_roc": _auc(subset_true, subset_prob),
                "recall_at_p50": _recall(subset_true, subset_prob),
            }
        report[dimension] = {
            "levels": levels,
            "gap": _gap(levels),
            "min_n": min_n,
        }
        suppressed = sum(1 for e in levels.values() if e.get("suppressed"))
        if suppressed:
            logger.info(
                "%s: %d of %d levels suppressed at n < %d",
                dimension,
                suppressed,
                len(levels),
                min_n,
            )
    return report


def _auc(y_true, y_prob) -> float | None:
    from sklearn.metrics import roc_auc_score

    if len(np.unique(y_true)) < 2:
        return None
    if len(np.unique(y_prob)) < 2:
        return 0.5
    return float(roc_auc_score(y_true, y_prob))


def _recall(y_true, y_prob) -> float | None:
    if len(np.unique(y_true)) < 2:
        return None
    value = recall_at_precision(y_true, y_prob, 0.50)
    return None if value != value else float(value)


def _gap(levels: dict) -> float | None:
    """Best minus worst AUC-ROC among the levels that were reported."""
    scores = [
        entry["auc_roc"]
        for entry in levels.values()
        if not entry.get("suppressed") and entry.get("auc_roc") is not None
    ]
    if len(scores) < 2:
        return None
    return float(max(scores) - min(scores))


def reportable_levels(report: dict, dimension: str) -> list[str]:
    """Output: the levels of this dimension that survived suppression."""
    levels = report.get(dimension, {}).get("levels", {})
    return sorted(key for key, entry in levels.items() if not entry.get("suppressed"))
