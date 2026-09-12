"""Cohort construction, the year split, fitting and evaluation.

No Django imports. Everything is deterministic: random_state=42 wherever a
model takes one, and the split is by presentation year, never random.

Column naming
-------------
X carries every feature set at once, prefixed by the set it belongs to:
``d__`` demographic, ``a__`` assessment, ``e__`` engagement. ``select_set``
slices out one set, so the same cohort serves all four of D, A, E and All and
the one-hot columns are guaranteed identical across them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    precision_recall_curve,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger("pipeline.train")

RANDOM_STATE = 42

TRAIN_YEAR = 2013
TEST_YEAR = 2014

FEATURE_SETS = {
    "D": ("d__",),
    "A": ("a__",),
    "E": ("e__",),
    "All": ("d__", "a__", "e__"),
}
CLASSIFIERS = ("majority", "logreg", "rf", "hgb")

STUDENT_KEY = ["code_module", "code_presentation", "id_student"]

# Demographic columns, one-hot for the categorical ones.
CATEGORICAL = ["gender", "region", "highest_education", "imd_band", "age_band"]
NUMERIC_DEMOGRAPHIC = ["disability", "num_of_prev_attempts", "studied_credits"]

# Dimensions the fairness report breaks results down by.
SUBGROUP_DIMS = ["imd_band", "disability", "age_band", "highest_education", "gender"]

# Weeks used for the engagement slope.
SLOPE_WEEKS = 4


@dataclass
class Evaluation:
    """One fitted model's performance on the held-out year."""

    classifier: str
    auc_roc: float
    auc_pr: float
    brier: float
    recall_at_p50: float
    calibration: list[dict] = field(default_factory=list)
    training_rows: int = 0
    test_rows: int = 0
    train_positive_rate: float = 0.0
    test_positive_rate: float = 0.0
    model: object | None = None
    feature_names: list[str] = field(default_factory=list)
    y_prob: np.ndarray | None = None


# --- cohort ---------------------------------------------------------------

def build_cohort(
    features: pd.DataFrame,
    students: pd.DataFrame,
    registrations: pd.DataFrame,
    horizon_week: int,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    """Input: the weekly feature frame, studentInfo, studentRegistration, and
    the horizon in weeks.

    Output: (X, y, groups).
      X       one row per student registration, all three feature sets, prefixed.
      y       True where final_result == 'Withdrawn'.
      groups  the student key, the presentation year for the split, and the
              demographic columns the fairness report breaks down by.

    Students who unregistered on or before day 7 * horizon_week are excluded.
    Their withdrawal is already visible at the horizon, so keeping them would
    let the model learn the label from data recorded after the fact.
    """
    cohort = students.merge(registrations, on=STUDENT_KEY, how="left", validate="one_to_one")

    cutoff = 7 * horizon_week
    before = len(cohort)
    keep = cohort["date_unregistration"].isna() | (cohort["date_unregistration"] > cutoff)
    cohort = cohort[keep].reset_index(drop=True)
    logger.info(
        "horizon %d: %d of %d registrations remain after excluding unregistration "
        "on or before day %d",
        horizon_week,
        len(cohort),
        before,
        cutoff,
    )

    y = (cohort["final_result"] == "Withdrawn").astype(bool).reset_index(drop=True)
    y.name = "withdrawn"

    groups = cohort[STUDENT_KEY].copy()
    groups["year"] = cohort["code_presentation"].str[:4].astype(int)
    for dimension in SUBGROUP_DIMS:
        groups[dimension] = cohort[dimension]

    demographic = _demographic_features(cohort)
    weekly = _weekly_features(features, cohort, horizon_week)
    X = pd.concat([demographic, weekly], axis=1)
    return X, y, groups


def _demographic_features(cohort: pd.DataFrame) -> pd.DataFrame:
    """One-hot the categoricals, keep the numerics. Missing IMD band becomes
    its own level rather than being dropped: 1,111 students have none, and
    that is itself informative."""
    categorical = pd.get_dummies(
        cohort[CATEGORICAL].astype("object"),
        prefix=CATEGORICAL,
        prefix_sep="=",
        dummy_na=True,
        dtype=float,
    )
    # disability arrives as 'Y'/'N' from the CSV and as a boolean from the
    # ORM; pandas spells the string dtype differently across versions, so the
    # test is what the values are, not what the column calls itself.
    disability = cohort["disability"]
    if not (
        pd.api.types.is_numeric_dtype(disability)
        or pd.api.types.is_bool_dtype(disability)
    ):
        disability = disability.map({"Y": 1.0, "N": 0.0, True: 1.0, False: 0.0})
    numeric = pd.DataFrame(
        {
            "disability": disability.astype(float),
            "num_of_prev_attempts": cohort["num_of_prev_attempts"].astype(float),
            "studied_credits": cohort["studied_credits"].astype(float),
        }
    ).fillna(0.0)
    frame = pd.concat([categorical, numeric], axis=1)
    frame.columns = [f"d__{column}" for column in frame.columns]
    return frame.reset_index(drop=True)


def _weekly_features(
    features: pd.DataFrame, cohort: pd.DataFrame, horizon_week: int
) -> pd.DataFrame:
    """Aggregate WeeklyFeatures up to and including the horizon week.

    A student with no weekly rows at or before the horizon gets zeros for the
    engagement counts and a missing assessment score, which is what "no
    engagement data recorded yet" means numerically.
    """
    window = features[features["week"] <= horizon_week]
    by_student = window.groupby(STUDENT_KEY, observed=True)

    engagement = by_student.agg(
        total_clicks=("total_clicks", "sum"),
        active_days=("active_days", "sum"),
        mean_weekly_clicks=("total_clicks", "mean"),
        max_weekly_clicks=("total_clicks", "max"),
        mean_clicks_per_active_day=("clicks_per_active_day", "mean"),
    )
    engagement["silent_weeks"] = by_student["total_clicks"].apply(
        lambda clicks: int((clicks == 0).sum())
    )
    engagement["pre_start_clicks"] = (
        window[window["week"] <= 0]
        .groupby(STUDENT_KEY, observed=True)["total_clicks"]
        .sum()
    )
    engagement["days_since_last_activity"] = _at_horizon(
        window, "days_since_last_activity"
    )
    engagement["click_slope_4w"] = _click_slope(window, horizon_week)

    assessment = pd.DataFrame(index=engagement.index)
    for column in (
        "assessments_submitted",
        "assessments_late",
        "mean_score",
        "weighted_score_to_date",
    ):
        assessment[column] = _at_horizon(window, column)

    keys = cohort[STUDENT_KEY]
    engagement = keys.merge(
        engagement.reset_index(), on=STUDENT_KEY, how="left"
    ).drop(columns=STUDENT_KEY)
    assessment = keys.merge(
        assessment.reset_index(), on=STUDENT_KEY, how="left"
    ).drop(columns=STUDENT_KEY)

    engagement = engagement.fillna(0.0).astype(float)
    # A never-active student has no "last activity"; the whole window has
    # passed without one.
    no_activity = engagement["active_days"] == 0
    engagement.loc[no_activity, "days_since_last_activity"] = 7 * horizon_week

    # mean_score and weighted_score_to_date are genuinely undefined until the
    # first assessment falls due, which for the week 4 horizon is most of the
    # cohort. Filling them with 0 alone would say "scored nothing" where the
    # truth is "nothing was due yet", so the missingness gets its own column
    # and the fill value is then free for the model to discount.
    assessment["assessment_due"] = assessment["mean_score"].notna().astype(float)
    assessment = assessment.fillna(0.0).astype(float)

    engagement.columns = [f"e__{column}" for column in engagement.columns]
    assessment.columns = [f"a__{column}" for column in assessment.columns]
    return pd.concat(
        [engagement.reset_index(drop=True), assessment.reset_index(drop=True)], axis=1
    )


def _at_horizon(window: pd.DataFrame, column: str) -> pd.Series:
    """The value in the latest week at or before the horizon."""
    ordered = window.sort_values("week")
    return ordered.groupby(STUDENT_KEY, observed=True)[column].last()


def _click_slope(window: pd.DataFrame, horizon_week: int) -> pd.Series:
    """Least-squares slope of weekly clicks over the last SLOPE_WEEKS weeks.

    Negative means engagement is falling into the horizon, which is the shape
    that precedes a withdrawal.
    """
    recent = window[window["week"] > horizon_week - SLOPE_WEEKS]
    if recent.empty:
        return pd.Series(dtype=float)

    def slope(part: pd.DataFrame) -> float:
        if len(part) < 2:
            return 0.0
        weeks = part["week"].to_numpy(dtype=float)
        clicks = part["total_clicks"].to_numpy(dtype=float)
        spread = ((weeks - weeks.mean()) ** 2).sum()
        if spread == 0:
            return 0.0
        return float(((weeks - weeks.mean()) * (clicks - clicks.mean())).sum() / spread)

    return recent.groupby(STUDENT_KEY, observed=True)[["week", "total_clicks"]].apply(
        slope, include_groups=False
    )


def select_set(X: pd.DataFrame, feature_set: str) -> pd.DataFrame:
    """Input: the full X and one of 'D', 'A', 'E', 'All'.
    Output: the columns belonging to that set."""
    prefixes = FEATURE_SETS[feature_set]
    columns = [c for c in X.columns if c.startswith(prefixes)]
    if not columns:
        raise ValueError(f"no columns for feature set {feature_set!r}")
    return X[columns]


# --- split ----------------------------------------------------------------

def split_by_year(X: pd.DataFrame, y: pd.Series, groups: pd.DataFrame):
    """Train on 2013 presentations, test on 2014. Never random.

    Output: (X_train, X_test, y_train, y_test, groups_train, groups_test).
    """
    train = (groups["year"] == TRAIN_YEAR).to_numpy()
    test = (groups["year"] == TEST_YEAR).to_numpy()
    return (
        X.loc[train].reset_index(drop=True),
        X.loc[test].reset_index(drop=True),
        y.loc[train].reset_index(drop=True),
        y.loc[test].reset_index(drop=True),
        groups.loc[train].reset_index(drop=True),
        groups.loc[test].reset_index(drop=True),
    )


def modules_without_training_data(groups: pd.DataFrame) -> list[str]:
    """Output: modules that appear only in the test year.

    CCC is the known case: its two presentations are both 2014, so under the
    year split it has no training rows at all.
    """
    by_module = groups.groupby("code_module")["year"].agg(set)
    return sorted(
        module for module, years in by_module.items() if TRAIN_YEAR not in years
    )


# --- fit and evaluate -----------------------------------------------------

def make_classifier(name: str):
    """Input: one of CLASSIFIERS. Output: an unfitted estimator."""
    if name == "majority":
        return _BaseRate()
    if name == "logreg":
        return Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        class_weight="balanced",
                        max_iter=2000,
                        random_state=RANDOM_STATE,
                    ),
                ),
            ]
        )
    if name == "rf":
        return RandomForestClassifier(
            n_estimators=300,
            class_weight="balanced",
            random_state=RANDOM_STATE,
            n_jobs=-1,
        )
    if name == "hgb":
        return HistGradientBoostingClassifier(random_state=RANDOM_STATE)
    raise ValueError(f"unknown classifier {name!r}")


class _BaseRate:
    """Predicts the training base rate for everyone.

    The floor any real model has to clear: it has AUC-ROC 0.5 by construction
    and AUC-PR equal to the positive rate, so a grid cell that does not beat
    it has learned nothing.
    """

    def fit(self, X, y):
        self.rate_ = float(np.mean(y))
        return self

    def predict_proba(self, X):
        column = np.full(len(X), self.rate_)
        return np.column_stack([1 - column, column])


def fit_and_evaluate(X_tr, y_tr, X_te, y_te, classifier: str) -> Evaluation:
    """Input: the split cohort and a classifier name.
    Output: an Evaluation carrying the four metrics, the calibration bins and
    the fitted model."""
    model = make_classifier(classifier)
    X_tr = X_tr.astype(float)
    X_te = X_te.astype(float)
    model.fit(X_tr, np.asarray(y_tr))
    y_prob = model.predict_proba(X_te)[:, 1]
    y_true = np.asarray(y_te)

    return Evaluation(
        classifier=classifier,
        auc_roc=_auc_roc(y_true, y_prob),
        auc_pr=float(average_precision_score(y_true, y_prob)),
        brier=float(brier_score_loss(y_true, y_prob)),
        recall_at_p50=recall_at_precision(y_true, y_prob, 0.50),
        calibration=calibration_bins(y_true, y_prob),
        training_rows=len(X_tr),
        test_rows=len(X_te),
        train_positive_rate=float(np.mean(y_tr)),
        test_positive_rate=float(np.mean(y_true)),
        model=model,
        feature_names=list(X_tr.columns),
        y_prob=y_prob,
    )


def _auc_roc(y_true, y_prob) -> float:
    """0.5 when the scores carry no ordering, as for the base rate."""
    if len(np.unique(y_true)) < 2:
        return float("nan")
    if len(np.unique(y_prob)) < 2:
        return 0.5
    return float(roc_auc_score(y_true, y_prob))


def recall_at_precision(y_true, y_prob, target: float = 0.50) -> float:
    """Recall at the threshold where precision first reaches target.

    Scanning thresholds upward, precision rises and recall falls; this is the
    largest recall still achieving the target precision. 0.0 if precision
    never reaches it.
    """
    y_true = np.asarray(y_true)
    if len(np.unique(y_true)) < 2:
        return float("nan")
    precision, recall, _ = precision_recall_curve(y_true, y_prob)
    reached = precision >= target
    if not reached.any():
        return 0.0
    return float(recall[reached].max())


def calibration_bins(y_true, y_prob, bins: int = 10) -> list[dict]:
    """Output: one {bin, n, mean_predicted, observed} per non-empty decile of
    predicted probability."""
    y_true = np.asarray(y_true, dtype=float)
    y_prob = np.asarray(y_prob, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    index = np.clip(np.digitize(y_prob, edges[1:-1], right=False), 0, bins - 1)
    out = []
    for bin_number in range(bins):
        mask = index == bin_number
        count = int(mask.sum())
        if not count:
            continue
        out.append(
            {
                "bin": bin_number,
                "low": float(edges[bin_number]),
                "high": float(edges[bin_number + 1]),
                "n": count,
                "mean_predicted": float(y_prob[mask].mean()),
                "observed": float(y_true[mask].mean()),
            }
        )
    return out


# --- explanation ----------------------------------------------------------

@dataclass
class ExplanationBasis:
    """What top_features needs beyond a single student's row.

    weights   per-feature magnitude used for ranking.
    signs     +1 where a larger value raises predicted risk, -1 where it
              lowers it, 0 where the direction is unknown.
    baseline  the training median of each feature, used to say whether this
              student sits on the risk-raising side of it.
    kind      'coefficient' or 'permutation', which is what the ranking means.
    """

    weights: dict[str, float]
    signs: dict[str, float]
    baseline: dict[str, float]
    kind: str


def explanation_basis(model, X_tr, y_tr, X_te, y_te, feature_names) -> ExplanationBasis:
    """Input: a fitted model and both splits.
    Output: an ExplanationBasis.

    For logistic regression the weights are the coefficients, which carry
    their own sign. For the tree models the SRS calls for permutation
    importance computed once on the test set; that is unsigned, so the
    direction is taken from the sign of each feature's correlation with the
    label in training. That is an approximation, and it is stated as one:
    for a feature with a non-monotone effect the magnitude is still right and
    the direction may not be.
    """
    baseline = {name: float(X_tr[name].median()) for name in feature_names}

    estimator = model.named_steps["model"] if isinstance(model, Pipeline) else model
    if isinstance(estimator, LogisticRegression):
        coefficients = estimator.coef_[0]
        scale = model.named_steps["scale"].scale_ if isinstance(model, Pipeline) else np.ones_like(coefficients)
        # Undo the scaler so a weight applies to the feature's own units.
        weights = {
            name: float(abs(coefficient / s)) if s else 0.0
            for name, coefficient, s in zip(feature_names, coefficients, scale)
        }
        signs = {
            name: float(np.sign(coefficient))
            for name, coefficient in zip(feature_names, coefficients)
        }
        return ExplanationBasis(weights, signs, baseline, "coefficient")

    if isinstance(estimator, _BaseRate):
        zeros = {name: 0.0 for name in feature_names}
        return ExplanationBasis(zeros, zeros, baseline, "none")

    result = permutation_importance(
        model,
        X_te.astype(float),
        np.asarray(y_te),
        n_repeats=5,
        random_state=RANDOM_STATE,
        scoring="average_precision",
        n_jobs=-1,
    )
    weights = {
        name: float(value)
        for name, value in zip(feature_names, result.importances_mean)
    }
    y_train = np.asarray(y_tr, dtype=float)
    signs = {}
    for name in feature_names:
        column = X_tr[name].to_numpy(dtype=float)
        if column.std() == 0 or y_train.std() == 0:
            signs[name] = 0.0
            continue
        signs[name] = float(np.sign(np.corrcoef(column, y_train)[0, 1]))
    return ExplanationBasis(weights, signs, baseline, "permutation")


def top_features(model, x_row, feature_names, basis=None, k: int = 3) -> list[dict]:
    """Input: a fitted model, one student's feature row, the feature names, and
    the ExplanationBasis for the model.

    Output: the k largest contributions, each
    {feature, value, direction, text}, ordered largest first. direction is
    'raises' or 'lowers', read from the student's own position relative to the
    training median.
    """
    values = {
        name: float(value)
        for name, value in zip(feature_names, np.asarray(x_row, dtype=float).ravel())
    }
    if basis is None:
        basis = ExplanationBasis(
            {name: 0.0 for name in feature_names},
            {name: 0.0 for name in feature_names},
            {name: 0.0 for name in feature_names},
            "none",
        )

    scored = []
    for name in feature_names:
        weight = basis.weights.get(name, 0.0)
        if weight == 0:
            continue
        value = values[name]
        if basis.kind == "coefficient":
            # The contribution this student's own value makes.
            magnitude = abs(weight * (value - basis.baseline.get(name, 0.0)))
            direction = basis.signs.get(name, 0.0) * np.sign(
                value - basis.baseline.get(name, 0.0)
            )
        else:
            magnitude = abs(weight)
            direction = basis.signs.get(name, 0.0) * np.sign(
                value - basis.baseline.get(name, 0.0)
            )
        scored.append(
            {
                "feature": name,
                "value": value,
                "direction": "raises" if direction > 0 else "lowers",
                "text": describe_feature(name, value),
                "_magnitude": float(magnitude),
            }
        )

    scored.sort(key=lambda item: item["_magnitude"], reverse=True)
    for item in scored:
        del item["_magnitude"]
    return scored[:k]


def _plural(count: float, singular: str, plural: str | None = None) -> str:
    return singular if int(count) == 1 else (plural or singular + "s")


def describe_feature(name: str, value: float) -> str:
    """Input: a prefixed feature name and this student's value.
    Output: one plain-English clause an advisor can read without the schema.
    """
    body = name.split("__", 1)[-1]
    whole = int(round(value))

    if body == "days_since_last_activity":
        if whole <= 0:
            return "active in the VLE this week"
        return f"no VLE activity for {whole} {_plural(whole, 'day')}"
    if body == "total_clicks":
        return f"{whole} VLE {_plural(whole, 'click')} so far"
    if body == "silent_weeks":
        return f"{whole} {_plural(whole, 'week')} with no VLE activity"
    if body == "click_slope_4w":
        if value < 0:
            return "VLE activity falling over the last 4 weeks"
        if value > 0:
            return "VLE activity rising over the last 4 weeks"
        return "VLE activity flat over the last 4 weeks"
    if body == "pre_start_clicks":
        return (
            "no VLE activity before the presentation started"
            if whole == 0
            else f"{whole} VLE {_plural(whole, 'click')} before the start"
        )
    if body == "active_days":
        return f"active on {whole} {_plural(whole, 'day')} so far"
    if body in ("mean_weekly_clicks", "max_weekly_clicks", "mean_clicks_per_active_day"):
        return f"{body.replace('_', ' ')} {value:.0f}"
    if body == "assessments_submitted":
        return f"{whole} {_plural(whole, 'assessment')} submitted"
    if body == "assessments_late":
        if whole == 0:
            return "no assessments submitted late"
        return f"{whole} {_plural(whole, 'assessment')} submitted late"
    if body == "mean_score":
        return f"mean assessment score {value:.0f}"
    if body == "weighted_score_to_date":
        return f"weighted score {value:.0f} to date"
    if body == "num_of_prev_attempts":
        if whole == 0:
            return "first attempt at this module"
        return f"{whole} previous {_plural(whole, 'attempt')} at this module"
    if body == "studied_credits":
        return f"{whole} credits being studied"
    if body == "disability":
        return "declared disability" if value else "no declared disability"
    if "=" in body:
        # A one-hot level, e.g. imd_band=0-10%.
        field, level = body.split("=", 1)
        readable = field.replace("_", " ")
        if level in ("nan", "None", ""):
            return f"{readable} not recorded"
        return f"{readable} {level}" if value else f"{readable} is not {level}"
    return f"{body.replace('_', ' ')} {value:g}"
