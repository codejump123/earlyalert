"""Per-presentation aggregates for the dashboard, with suppression.

The n < 20 rule is applied here rather than in the template, so a level's
numbers are never computed into a context a template could accidentally
render. A suppressed level keeps its name and its count and nothing else.
"""

from __future__ import annotations

from django.conf import settings

HIGH_RISK = 0.5

# Dimension name to the Student field holding it.
FIELD_FOR_DIMENSION = {
    "imd_band": "imd_band",
    "disability": "disability",
    "age_band": "age_band",
    "highest_education": "highest_education",
    "gender": "gender",
}

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
