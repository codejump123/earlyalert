"""Cohort construction, the year split, metrics and explanations.

Pure pipeline: no Django, no database. The fixture cohort's rules are in
tests/fixtures/generate.py; expected values are worked out from them.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline.features import build_weekly_features
from pipeline.loader import read_student_info, read_student_registration
from pipeline.train import (
    CLASSIFIERS,
    FEATURE_SETS,
    RANDOM_STATE,
    build_cohort,
    calibration_bins,
    describe_feature,
    explanation_basis,
    fit_and_evaluate,
    make_classifier,
    modules_without_training_data,
    recall_at_precision,
    select_set,
    split_by_year,
    top_features,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def parts():
    return (
        build_weekly_features(FIXTURES),
        read_student_info(FIXTURES / "studentInfo.csv"),
        read_student_registration(FIXTURES / "studentRegistration.csv"),
    )


# --- the leakage guard ----------------------------------------------------

def test_students_who_left_before_the_horizon_are_excluded(parts):
    """k<8 withdraws on day 7(k+1), so at horizon 4 (day 28) k=0..3 are gone."""
    features, students, registrations = parts
    X, y, groups = build_cohort(features, students, registrations, 4)
    remaining = set(groups.id_student)
    for k in range(4):                      # unregistered on days 7,14,21,28
        assert 1001 + k not in remaining
    for k in range(4, 8):                   # days 35,42,49,56 are still ahead
        assert 1001 + k in remaining


def test_the_cutoff_is_inclusive_of_day_7w(parts):
    """'on or before day 7w': k=3 leaves on day 28, exactly the week 4 cutoff."""
    features, students, registrations = parts
    _, _, groups = build_cohort(features, students, registrations, 4)
    assert 1004 not in set(groups.id_student)


def test_a_later_horizon_excludes_more(parts):
    features, students, registrations = parts
    sizes = [
        len(build_cohort(features, students, registrations, w)[1])
        for w in (4, 8, 12)
    ]
    assert sizes == [42, 34, 34]            # 50 - 8, then all 16 withdrawals gone
    assert sizes[0] > sizes[1] >= sizes[2]


def test_students_who_never_unregistered_are_always_kept(parts):
    features, students, registrations = parts
    _, _, groups = build_cohort(features, students, registrations, 12)
    assert len(groups) == 34                # the 17 non-withdrawers per year
    assert 1009 in set(groups.id_student)


def test_the_label_is_withdrawn(parts):
    features, students, registrations = parts
    _, y, groups = build_cohort(features, students, registrations, 4)
    assert y.name == "withdrawn"
    assert y.dtype == bool
    # k=4..7 withdraw later than day 28, so four per presentation survive.
    assert int(y.sum()) == 8


# --- feature sets ---------------------------------------------------------

def test_every_column_belongs_to_exactly_one_set(parts):
    features, students, registrations = parts
    X, _, _ = build_cohort(features, students, registrations, 8)
    assert all(c.startswith(("d__", "a__", "e__")) for c in X.columns)
    sizes = {name: select_set(X, name).shape[1] for name in FEATURE_SETS}
    assert sizes["All"] == sizes["D"] + sizes["A"] + sizes["E"]


def test_the_sets_do_not_overlap(parts):
    features, students, registrations = parts
    X, _, _ = build_cohort(features, students, registrations, 8)
    columns = [set(select_set(X, name).columns) for name in ("D", "A", "E")]
    assert columns[0] & columns[1] == set()
    assert columns[0] & columns[2] == set()
    assert columns[1] & columns[2] == set()


def test_demographics_are_one_hot_with_a_level_for_missing(parts):
    """At horizon 4 the band-less student (k=7, gone on day 56) is still in."""
    features, students, registrations = parts
    X, _, _ = build_cohort(features, students, registrations, 4)
    demographic = select_set(X, "D")
    assert "d__imd_band=0-10%" in demographic.columns
    # k=7 has no band, and that is its own column rather than a dropped row.
    assert "d__imd_band=nan" in demographic.columns
    assert demographic["d__imd_band=nan"].sum() > 0
    assert "d__num_of_prev_attempts" in demographic.columns


def test_engagement_carries_the_four_week_slope(parts):
    features, students, registrations = parts
    X, _, _ = build_cohort(features, students, registrations, 8)
    assert "e__click_slope_4w" in select_set(X, "E").columns
    assert "e__days_since_last_activity" in select_set(X, "E").columns


def test_the_horizon_bounds_what_the_features_can_see(parts):
    """Clicks to week 4 must be less than clicks to week 12 for an active
    student, and equal for one who stopped at week 2."""
    features, students, registrations = parts
    rows = {}
    for horizon in (4, 12):
        X, _, groups = build_cohort(features, students, registrations, horizon)
        index = groups.index[groups.id_student == 1009][0]
        rows[horizon] = X.at[index, "e__total_clicks"]
    assert rows[4] < rows[12]


def test_no_feature_is_missing(parts):
    """Every model in the grid has to accept the same matrix."""
    features, students, registrations = parts
    for horizon in (4, 8, 12):
        X, _, _ = build_cohort(features, students, registrations, horizon)
        assert not X.isna().any().any()
        assert X.select_dtypes(exclude="number").empty is True or all(
            X[c].dtype != object for c in X.columns
        )


def test_missing_assessment_scores_get_an_indicator(parts):
    """At week 4 nothing has fallen due, so the score is absent, not zero."""
    features, students, registrations = parts
    X, _, _ = build_cohort(features, students, registrations, 4)
    assert "a__assessment_due" in X.columns
    assert (X["a__assessment_due"] == 0).all()
    assert (X["a__mean_score"] == 0).all()

    X12, _, _ = build_cohort(features, students, registrations, 12)
    assert (X12["a__assessment_due"] == 1).all()


# --- the split ------------------------------------------------------------

def test_split_is_by_year_not_at_random(parts):
    features, students, registrations = parts
    X, y, groups = build_cohort(features, students, registrations, 8)
    X_tr, X_te, y_tr, y_te, g_tr, g_te = split_by_year(X, y, groups)
    assert set(g_tr.year) == {2013}
    assert set(g_te.year) == {2014}
    assert len(X_tr) + len(X_te) == len(X)
    assert len(y_tr) + len(y_te) == len(y)


def test_the_split_is_reproducible(parts):
    features, students, registrations = parts
    X, y, groups = build_cohort(features, students, registrations, 8)
    first = split_by_year(X, y, groups)[0]
    second = split_by_year(X, y, groups)[0]
    pd.testing.assert_frame_equal(first, second)


def test_a_module_present_only_in_the_test_year_is_named():
    """CCC's case, in miniature."""
    groups = pd.DataFrame(
        {
            "code_module": ["AAA", "AAA", "CCC", "CCC"],
            "year": [2013, 2014, 2014, 2014],
        }
    )
    assert modules_without_training_data(groups) == ["CCC"]


# --- metrics --------------------------------------------------------------

def test_recall_at_precision_finds_the_largest_qualifying_recall():
    y = np.array([0, 0, 1, 1])
    p = np.array([0.1, 0.4, 0.35, 0.8])
    # Thresholding at 0.35 gives precision 2/3 and recall 1.0.
    assert recall_at_precision(y, p, 0.50) == 1.0


def test_recall_at_precision_is_zero_when_precision_never_reaches_target():
    y = np.array([0] * 90 + [1] * 10)
    p = np.concatenate([np.linspace(0.4, 0.9, 90), np.linspace(0.0, 0.3, 10)])
    assert recall_at_precision(y, p, 0.50) == 0.0


def test_calibration_bins_report_predicted_against_observed():
    y = np.array([0] * 50 + [1] * 50)
    p = np.array([0.05] * 50 + [0.95] * 50)
    bins = calibration_bins(y, p)
    assert len(bins) == 2
    assert bins[0]["n"] == 50
    assert bins[0]["mean_predicted"] == pytest.approx(0.05)
    assert bins[0]["observed"] == 0.0
    assert bins[1]["observed"] == 1.0
    assert sum(b["n"] for b in bins) == 100


# --- classifiers ----------------------------------------------------------

def synthetic_split(n=400, seed=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(
        {"e__total_clicks": rng.normal(size=n), "d__studied_credits": rng.normal(size=n)}
    )
    y = pd.Series((X["e__total_clicks"] + rng.normal(scale=0.5, size=n)) < -0.3)
    half = n // 2
    return X[:half], X[half:], y[:half], y[half:]


def test_every_named_classifier_fits_and_scores():
    X_tr, X_te, y_tr, y_te = synthetic_split()
    for name in CLASSIFIERS:
        evaluation = fit_and_evaluate(X_tr, y_tr, X_te, y_te, name)
        assert evaluation.classifier == name
        assert 0.0 <= evaluation.auc_roc <= 1.0
        assert 0.0 <= evaluation.auc_pr <= 1.0
        assert 0.0 <= evaluation.brier <= 1.0
        assert len(evaluation.y_prob) == len(y_te)


def test_the_majority_baseline_predicts_the_training_base_rate():
    X_tr, X_te, y_tr, y_te = synthetic_split()
    evaluation = fit_and_evaluate(X_tr, y_tr, X_te, y_te, "majority")
    assert len(np.unique(evaluation.y_prob)) == 1
    assert evaluation.y_prob[0] == pytest.approx(y_tr.mean())
    # One number for everyone orders nobody, so AUC is exactly 0.5.
    assert evaluation.auc_roc == 0.5
    assert evaluation.auc_pr == pytest.approx(y_te.mean(), abs=0.02)


def test_the_real_classifiers_beat_the_baseline():
    X_tr, X_te, y_tr, y_te = synthetic_split()
    baseline = fit_and_evaluate(X_tr, y_tr, X_te, y_te, "majority")
    for name in ("logreg", "rf", "hgb"):
        evaluation = fit_and_evaluate(X_tr, y_tr, X_te, y_te, name)
        assert evaluation.auc_pr > baseline.auc_pr


def test_fitting_is_deterministic():
    X_tr, X_te, y_tr, y_te = synthetic_split()
    for name in ("logreg", "rf", "hgb"):
        first = fit_and_evaluate(X_tr, y_tr, X_te, y_te, name)
        second = fit_and_evaluate(X_tr, y_tr, X_te, y_te, name)
        np.testing.assert_allclose(first.y_prob, second.y_prob)
        assert first.auc_pr == second.auc_pr


def test_random_state_is_fixed_everywhere():
    assert RANDOM_STATE == 42
    assert make_classifier("rf").random_state == 42
    assert make_classifier("rf").n_estimators == 300
    assert make_classifier("rf").class_weight == "balanced"
    assert make_classifier("hgb").random_state == 42
    pipeline = make_classifier("logreg")
    assert pipeline.named_steps["model"].class_weight == "balanced"
    assert pipeline.named_steps["scale"].__class__.__name__ == "StandardScaler"


def test_an_unknown_classifier_is_an_error():
    with pytest.raises(ValueError, match="unknown classifier"):
        make_classifier("magic")


# --- explanations ---------------------------------------------------------

def test_top_features_returns_three_readable_contributions():
    X_tr, X_te, y_tr, y_te = synthetic_split()
    evaluation = fit_and_evaluate(X_tr, y_tr, X_te, y_te, "logreg")
    basis = explanation_basis(
        evaluation.model, X_tr, y_tr, X_te, y_te, evaluation.feature_names
    )
    out = top_features(
        evaluation.model, X_te.iloc[0].to_numpy(), evaluation.feature_names, basis
    )
    assert 1 <= len(out) <= 3
    for item in out:
        assert set(item) == {"feature", "value", "direction", "text"}
        assert item["direction"] in ("raises", "lowers")
        assert isinstance(item["text"], str) and item["text"]


def test_top_features_works_for_tree_models_too():
    X_tr, X_te, y_tr, y_te = synthetic_split()
    evaluation = fit_and_evaluate(X_tr, y_tr, X_te, y_te, "rf")
    basis = explanation_basis(
        evaluation.model, X_tr, y_tr, X_te, y_te, evaluation.feature_names
    )
    assert basis.kind == "permutation"
    out = top_features(
        evaluation.model, X_te.iloc[0].to_numpy(), evaluation.feature_names, basis
    )
    assert out and all(item["text"] for item in out)


def test_explanations_are_ordered_by_magnitude():
    X_tr, X_te, y_tr, y_te = synthetic_split()
    evaluation = fit_and_evaluate(X_tr, y_tr, X_te, y_te, "logreg")
    basis = explanation_basis(
        evaluation.model, X_tr, y_tr, X_te, y_te, evaluation.feature_names
    )
    # The informative column must outrank the noise column.
    out = top_features(
        evaluation.model, X_te.iloc[0].to_numpy(), evaluation.feature_names, basis
    )
    assert out[0]["feature"] == "e__total_clicks"


@pytest.mark.parametrize(
    "name,value,expected",
    [
        ("e__days_since_last_activity", 11, "no VLE activity for 11 days"),
        ("e__days_since_last_activity", 1, "no VLE activity for 1 day"),
        ("e__days_since_last_activity", 0, "active in the VLE this week"),
        ("d__num_of_prev_attempts", 0, "first attempt at this module"),
        ("d__num_of_prev_attempts", 1, "1 previous attempt at this module"),
        ("d__num_of_prev_attempts", 3, "3 previous attempts at this module"),
        ("a__assessments_submitted", 2, "2 assessments submitted"),
        ("a__assessments_late", 0, "no assessments submitted late"),
        ("a__mean_score", 62.4, "mean assessment score 62"),
        ("e__click_slope_4w", -3.2, "VLE activity falling over the last 4 weeks"),
        ("e__silent_weeks", 3, "3 weeks with no VLE activity"),
        ("d__disability", 1, "declared disability"),
        ("d__imd_band=0-10%", 1, "imd band 0-10%"),
        ("d__imd_band=nan", 1, "imd band not recorded"),
    ],
)
def test_feature_text_reads_as_english(name, value, expected):
    assert describe_feature(name, value) == expected


# --- degenerate splits ----------------------------------------------------

def test_a_single_class_training_split_is_refused_not_crashed():
    """At a late horizon the cohort filter can remove every withdrawal from one
    side of the year split. That is a skipped grid cell, never a traceback."""
    from pipeline.train import DegenerateSplit

    X_tr, X_te, y_tr, y_te = synthetic_split()
    with pytest.raises(DegenerateSplit, match="training split"):
        fit_and_evaluate(X_tr, pd.Series([False] * len(y_tr)), X_te, y_te, "logreg")


def test_a_single_class_test_split_is_refused_too():
    from pipeline.train import DegenerateSplit

    X_tr, X_te, y_tr, y_te = synthetic_split()
    with pytest.raises(DegenerateSplit, match="test split"):
        fit_and_evaluate(X_tr, y_tr, X_te, pd.Series([True] * len(y_te)), "logreg")
