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

# The share of a cohort an advisory team is assumed able to contact. The
# false-negative rate is measured at this operating point: flag the top
# CAPACITY by predicted risk, and count the withdrawals that were missed.
CAPACITY = 0.10


def capacity_threshold(y_prob, capacity: float = CAPACITY) -> float:
    """Input: predicted probabilities for a whole test set.
    Output: the score at which the top `capacity` share of it is flagged.

    One threshold for everybody. Choosing a threshold per subgroup would give
    each group its own bar and make the false-negative rates incomparable,
    which is the opposite of what the comparison is for.
    """
    y_prob = np.asarray(y_prob, dtype=float)
    if y_prob.size == 0:
        return float("inf")
    return float(np.quantile(y_prob, 1.0 - capacity))


def false_negative_rate(y_true, y_prob, threshold: float) -> float | None:
    """Input: labels, scores, and the flagging threshold.
    Output: the share of withdrawers scoring below it — the students who
    withdrew and were never put in front of an advisor. None if nobody in the
    group withdrew.
    """
    y_true = np.asarray(y_true).astype(bool)
    y_prob = np.asarray(y_prob, dtype=float)
    positives = int(y_true.sum())
    if positives == 0:
        return None
    missed = int((y_true & (y_prob < threshold)).sum())
    return missed / positives


def subgroup_metrics(
    y_true,
    y_prob,
    groups: pd.DataFrame,
    dims: list[str],
    min_n: int = MIN_N,
    capacity: float = CAPACITY,
) -> dict:
    """Input: the labels, the predicted probabilities, a frame carrying one
    column per dimension, the dimensions to report, and the suppression floor.

    Output:
        {dim: {"levels": {level: entry}, "gap": float | None,
               "fnr_gap": float | None, "min_n": int, "capacity": float,
               "threshold": float}}

    where entry is either
        {"n", "positives", "positive_rate", "auc_roc", "auc_pr", "brier",
         "recall_at_p50", "false_negative_rate", "flagged", "flagged_rate"}
    or, below the floor,
        {"suppressed": True, "n": n}

    Four metrics are reported per level because they answer different
    questions. AUC-ROC and AUC-PR say how well the group is ranked; Brier says
    whether its probabilities can be believed; the false-negative rate at the
    shared capacity threshold says how many of its withdrawers were missed.
    The last is the one the problem statement is about.

    gap is the best-minus-worst AUC-ROC across the reported levels; fnr_gap is
    worst-minus-best false-negative rate, so both read as "larger is worse".
    Either is None when fewer than two levels survive suppression. A level
    whose students all share one label has no defined AUC; it is reported with
    a null metric rather than suppressed, because its size is not the problem.
    """
    y_true = np.asarray(y_true)
    y_prob = np.asarray(y_prob)
    if len(y_true) != len(groups):
        raise ValueError(
            f"groups has {len(groups)} rows but y_true has {len(y_true)}"
        )

    threshold = capacity_threshold(y_prob, capacity)
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
            flagged = int((subset_prob >= threshold).sum())
            levels[key] = {
                "n": count,
                "positives": int(subset_true.sum()),
                "positive_rate": float(subset_true.mean()),
                "auc_roc": _auc(subset_true, subset_prob),
                "auc_pr": _auc_pr(subset_true, subset_prob),
                "brier": _brier(subset_true, subset_prob),
                "recall_at_p50": _recall(subset_true, subset_prob),
                "false_negative_rate": false_negative_rate(
                    subset_true, subset_prob, threshold
                ),
                "flagged": flagged,
                "flagged_rate": flagged / count,
            }
        report[dimension] = {
            "levels": levels,
            "gap": _gap(levels, "auc_roc"),
            "fnr_gap": _gap(levels, "false_negative_rate"),
            "min_n": min_n,
            "capacity": capacity,
            "threshold": threshold,
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


def _auc_pr(y_true, y_prob) -> float | None:
    from sklearn.metrics import average_precision_score

    if len(np.unique(y_true)) < 2:
        return None
    return float(average_precision_score(y_true, y_prob))


def _brier(y_true, y_prob) -> float:
    from sklearn.metrics import brier_score_loss

    return float(brier_score_loss(np.asarray(y_true, dtype=float), y_prob))


def _gap(levels: dict, metric: str) -> float | None:
    """Spread of one metric across the levels that were reported.

    Always largest minus smallest, so a gap reads as "larger is worse" whether
    the metric itself is better high (AUC) or better low (false negatives).
    """
    scores = [
        entry[metric]
        for entry in levels.values()
        if not entry.get("suppressed") and entry.get(metric) is not None
    ]
    if len(scores) < 2:
        return None
    return float(max(scores) - min(scores))


def reportable_levels(report: dict, dimension: str) -> list[str]:
    """Output: the levels of this dimension that survived suppression."""
    levels = report.get(dimension, {}).get("levels", {})
    return sorted(key for key, entry in levels.items() if not entry.get("suppressed"))
