"""Subgroup metrics and small-cell suppression. Pure pipeline, no database.

The suppression rule is the one thing here that is not a statistic: a level
with fewer than 20 students is never reported, however tempting its number.
"""

import numpy as np
import pandas as pd
import pytest

from pipeline.fairness import MIN_N, reportable_levels, subgroup_metrics


def cohort(sizes: dict[str, int], seed: int = 42):
    """Build a synthetic cohort with a given number of students per level.

    Scores are informative, so an unsuppressed AUC is a real number rather
    than a coin flip.
    """
    rng = np.random.default_rng(seed)
    levels, labels, scores = [], [], []
    for level, size in sizes.items():
        outcome = rng.integers(0, 2, size)
        levels.extend([level] * size)
        labels.extend(outcome.tolist())
        scores.extend((outcome * 0.4 + rng.random(size) * 0.6).tolist())
    groups = pd.DataFrame({"imd_band": levels})
    return np.array(labels), np.array(scores), groups


def test_the_floor_is_twenty():
    assert MIN_N == 20


def test_a_level_below_the_floor_is_suppressed():
    y, p, g = cohort({"0-10%": 40, "90-100%": 19})
    report = subgroup_metrics(y, p, g, ["imd_band"])
    levels = report["imd_band"]["levels"]

    assert levels["90-100%"] == {"suppressed": True, "n": 19}
    assert "auc_roc" not in levels["90-100%"]
    assert "positive_rate" not in levels["90-100%"]


def test_a_level_exactly_at_the_floor_is_reported():
    y, p, g = cohort({"0-10%": 40, "90-100%": 20})
    levels = subgroup_metrics(y, p, g, ["imd_band"])["imd_band"]["levels"]
    assert levels["90-100%"]["n"] == 20
    assert "suppressed" not in levels["90-100%"]
    assert 0.0 <= levels["90-100%"]["auc_roc"] <= 1.0


def test_suppression_hides_the_count_of_nothing_else():
    """n is kept so a reader can see why the level is missing."""
    y, p, g = cohort({"0-10%": 40, "90-100%": 3})
    entry = subgroup_metrics(y, p, g, ["imd_band"])["imd_band"]["levels"]["90-100%"]
    assert entry["n"] == 3


def test_a_custom_floor_is_honoured():
    y, p, g = cohort({"0-10%": 40, "90-100%": 19})
    levels = subgroup_metrics(y, p, g, ["imd_band"], min_n=10)["imd_band"]["levels"]
    assert "suppressed" not in levels["90-100%"]
    assert levels["90-100%"]["n"] == 19


def test_metrics_are_computed_per_level_not_overall():
    y, p, g = cohort({"a": 60, "b": 40, "c": 30})
    levels = subgroup_metrics(y, p, g, ["imd_band"])["imd_band"]["levels"]
    assert {"a", "b", "c"} == set(levels)
    assert [levels[k]["n"] for k in "abc"] == [60, 40, 30]
    assert sum(levels[k]["n"] for k in "abc") == len(y)


def test_gap_is_best_minus_worst_auc():
    y, p, g = cohort({"a": 60, "b": 40, "c": 30})
    report = subgroup_metrics(y, p, g, ["imd_band"])["imd_band"]
    aucs = [entry["auc_roc"] for entry in report["levels"].values()]
    assert report["gap"] == pytest.approx(max(aucs) - min(aucs))


def test_gap_ignores_suppressed_levels():
    y, p, g = cohort({"a": 60, "b": 40, "tiny": 5})
    report = subgroup_metrics(y, p, g, ["imd_band"])["imd_band"]
    reported = [
        entry["auc_roc"]
        for entry in report["levels"].values()
        if not entry.get("suppressed")
    ]
    assert len(reported) == 2
    assert report["gap"] == pytest.approx(max(reported) - min(reported))


def test_gap_is_none_when_fewer_than_two_levels_survive():
    y, p, g = cohort({"a": 60, "tiny": 5})
    assert subgroup_metrics(y, p, g, ["imd_band"])["imd_band"]["gap"] is None


def test_a_single_class_level_reports_no_auc_rather_than_crashing():
    """A large level where nobody withdrew has no AUC, but it is not small."""
    groups = pd.DataFrame({"imd_band": ["a"] * 30 + ["b"] * 30})
    y = np.array([0] * 30 + [0, 1] * 15)
    p = np.linspace(0, 1, 60)
    entry = subgroup_metrics(y, p, groups, ["imd_band"])["imd_band"]["levels"]["a"]
    assert entry["n"] == 30
    assert "suppressed" not in entry
    assert entry["auc_roc"] is None
    assert entry["recall_at_p50"] is None


def test_missing_level_becomes_its_own_category():
    """1,111 students have no IMD band; they are a group, not a gap."""
    groups = pd.DataFrame({"imd_band": ["a"] * 30 + [None] * 25})
    y = np.array([0, 1] * 27 + [0])
    p = np.linspace(0, 1, 55)
    levels = subgroup_metrics(y, p, groups, ["imd_band"])["imd_band"]["levels"]
    assert set(levels) == {"a", "not recorded"}
    assert levels["not recorded"]["n"] == 25


def test_every_requested_dimension_is_reported():
    frame = pd.DataFrame(
        {
            "imd_band": ["a"] * 30 + ["b"] * 30,
            "disability": ["Y"] * 25 + ["N"] * 35,
            "gender": ["M", "F"] * 30,
        }
    )
    y = np.array([0, 1] * 30)
    p = np.linspace(0, 1, 60)
    report = subgroup_metrics(y, p, frame, ["imd_band", "disability", "gender"])
    assert set(report) == {"imd_band", "disability", "gender"}
    for dimension in report:
        assert report[dimension]["min_n"] == MIN_N


def test_an_unknown_dimension_is_an_error():
    y, p, g = cohort({"a": 30})
    with pytest.raises(ValueError, match="no column"):
        subgroup_metrics(y, p, g, ["not_a_column"])


def test_mismatched_lengths_are_an_error():
    y, p, g = cohort({"a": 30})
    with pytest.raises(ValueError, match="rows"):
        subgroup_metrics(y[:10], p[:10], g, ["imd_band"])


def test_reportable_levels_lists_only_what_survived():
    y, p, g = cohort({"a": 60, "b": 40, "tiny": 5})
    report = subgroup_metrics(y, p, g, ["imd_band"])
    assert reportable_levels(report, "imd_band") == ["a", "b"]
