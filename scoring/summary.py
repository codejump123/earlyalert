"""Per-presentation aggregates for the dashboard, with suppression.

The n < 20 rule is applied here rather than in the template, so a level's
numbers are never computed into a context a template could accidentally
render. A suppressed level keeps its name and its count and nothing else.
"""

from __future__ import annotations

from django.conf import settings

HIGH_RISK = 0.5

# Dimension name to the Student field holding it. The dashboard offers one
# dimension the model's fairness report does not: UC08 lists number of previous
# attempts among the breakdowns an instructor may select, while the per-model
# breakdown is fixed at the five demographic dimensions.
FIELD_FOR_DIMENSION = {
    "imd_band": "imd_band",
    "gender": "gender",
    "age_band": "age_band",
    "disability": "disability",
    "highest_education": "highest_education",
    "num_prev_attempts": "num_prev_attempts",
}
DASHBOARD_DIMS = list(FIELD_FOR_DIMENSION)

# How a dimension is named on screen. Derived labels are not good enough here:
# a template filter that strips the underscore turns imd_band into "imdband".
DIMENSION_LABELS = {
    "imd_band": "IMD band",
    "gender": "gender",
    "age_band": "age band",
    "disability": "disability",
    "highest_education": "highest education",
    "num_prev_attempts": "previous attempts",
}


def dimension_label(dimension: str) -> str:
    """Input: a dimension name. Output: how it reads on screen."""
    return DIMENSION_LABELS.get(dimension, dimension.replace("_", " "))

NOT_RECORDED = "not recorded"


def level_label(dimension: str, value) -> str:
    """Output: how a level is named on screen.

    A missing IMD band is a group of 1,111 students, so it is labelled rather
    than dropped; disability reads as Yes/No rather than True/False.
    """
    if value is None or value == "":
        return NOT_RECORDED
    if dimension == "disability":
        return "Yes" if value else "No"
    if dimension == "num_prev_attempts":
        count = int(value)
        return "none" if count == 0 else f"{count}"
    return str(value)


def subgroup_summary(scores, dimension: str, min_n: int | None = None) -> list[dict]:
    """Input: a RiskScore queryset for one presentation, and a dimension.

    Output: one entry per level, ordered by level name:
        {level, n, suppressed, mean_probability, high_risk, high_risk_share}
    A suppressed entry carries level, n and suppressed only; the rest are None.
    """
    if min_n is None:
        min_n = settings.MIN_SUBGROUP_N
    field = FIELD_FOR_DIMENSION[dimension]

    buckets: dict[str, list[float]] = {}
    for value, probability in scores.values_list(f"student__{field}", "probability"):
        buckets.setdefault(level_label(dimension, value), []).append(probability)

    summary = []
    for level in sorted(buckets):
        probabilities = buckets[level]
        count = len(probabilities)
        if count < min_n:
            summary.append(
                {
                    "level": level,
                    "n": count,
                    "suppressed": True,
                    "mean_probability": None,
                    "high_risk": None,
                    "high_risk_share": None,
                }
            )
            continue
        high_risk = sum(1 for p in probabilities if p >= HIGH_RISK)
        summary.append(
            {
                "level": level,
                "n": count,
                "suppressed": False,
                "mean_probability": sum(probabilities) / count,
                "high_risk": high_risk,
                "high_risk_share": high_risk / count,
            }
        )
    return summary


def model_subgroup_levels(model, dimension: str) -> list[dict]:
    """Input: the current ModelVersion and a dimension.
    Output: the stored per-level metrics for that dimension, already
    suppressed when they were stored, as a list ordered by level name.

    These describe the model across the whole test year, not this
    presentation: a per-presentation AUC would itself be a small-n statistic.
    """
    if model is None:
        return []
    report = (model.subgroup_metrics or {}).get(dimension)
    if not report:
        return []
    out = []
    for level in sorted(report.get("levels", {})):
        entry = report["levels"][level]
        out.append(
            {
                "level": level,
                "n": entry.get("n"),
                "suppressed": bool(entry.get("suppressed")),
                "auc_roc": entry.get("auc_roc"),
                "recall_at_p50": entry.get("recall_at_p50"),
                "positive_rate": entry.get("positive_rate"),
            }
        )
    return out


def gap_for(model, dimension: str):
    """Output: the stored best-minus-worst AUC gap for this dimension."""
    if model is None:
        return None
    return (model.subgroup_metrics or {}).get(dimension, {}).get("gap")
