"""Retraining the current models and rewriting risk scores (UC11).

Runs in-process under the job lock, triggered by an administrator. Refuses,
with retrain_failed and a reason, if the features predate the current upload,
if the lock is held, or if any horizon's cohort is too small to train on.

What a run produces, for each horizon in settings.HORIZON_WEEKS:
  - one ModelVersion per classifier on the All feature set, so the baseline
    stays on the record beside the models that beat it;
  - the best of them by AUC-PR marked is_current, with its subgroup metrics
    and its fitted estimator saved to ARTIFACT_ROOT;
  - a RiskScore for every student the current model scores.

Only the test-year presentations are scored. The model is fitted on 2013 and
evaluated on 2014; scoring the rows it was fitted on would put optimistic
probabilities in front of an advisor and call them predictions.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
from django.conf import settings
from django.db import transaction

from audit.services import record
from cohorts.models import DataUpload
from earlyalert.joblock import JobLockHeld, job_lock
from pipeline.fairness import subgroup_metrics
from pipeline.train import (
    SUBGROUP_DIMS,
    build_cohort,
    explanation_basis,
    fit_and_evaluate,
    modules_without_training_data,
    select_set,
    split_by_year,
    top_features,
)

from .datasets import (
    features_frame,
    registrations_frame,
    student_pk_lookup,
    students_frame,
)
from .models import ModelVersion, RiskScore

logger = logging.getLogger("pipeline.train")

JOB_NAME = "retrain"

# The feature set the application's own models use. The full 4 x 3 x 3 grid is
# the experiment's job, not the application's.
PRODUCTION_FEATURE_SET = "All"
CANDIDATES = ("majority", "logreg", "rf", "hgb")

# A cohort smaller than this cannot support a trustworthy model.
MIN_COHORT_ROWS = 500
MIN_COHORT_POSITIVES = 50


class RetrainRefused(Exception):
    """Raised with the reason a retrain cannot proceed."""


@dataclass
class HorizonResult:
    horizon_week: int
    classifier: str
    auc_roc: float
    auc_pr: float
    scored: int


@dataclass
class RetrainResult:
    ok: bool
    error: str | None = None
    horizons: list[HorizonResult] = field(default_factory=list)
    seconds: float = 0.0
    excluded_modules: list[str] = field(default_factory=list)


def features_are_stale() -> str | None:
    """Output: the reason the features cannot be trained on, or None.

    The audit log is the record of when features were last built, so staleness
    is read from it rather than from a timestamp column that would have to be
    kept in step by hand.
    """
    from audit.models import AuditEntry

    upload = (
        DataUpload.objects.filter(superseded=False).order_by("-uploaded_at").first()
    )
    if upload is None:
        return "no complete upload is in place; upload the seven files first"
    built = (
        AuditEntry.objects.filter(action="rebuild_completed")
        .order_by("-timestamp")
        .first()
    )
    if built is None:
        return "features have never been built; rebuild them first"
    if built.timestamp < upload.uploaded_at:
        return (
            "features predate the current upload; rebuild them before retraining"
        )
    return None


def retrain(user) -> RetrainResult:
    """Input: the administrator triggering the retrain. Output: RetrainResult.

    Writes retrain_started then exactly one of retrain_completed or
    retrain_failed.
    """
    stale = features_are_stale()
    if stale is not None:
        return _fail(user, stale)

    try:
        with job_lock(JOB_NAME):
            record(user, "retrain_started", f"horizons:{settings.HORIZON_WEEKS}")
            started = time.monotonic()
            try:
                results, excluded = _run(user)
            except RetrainRefused as refused:
                # _run is atomic, so a refusal at the second horizon undoes the
                # first: a run either replaces every current model or none.
                return _fail(user, str(refused))
            elapsed = time.monotonic() - started
            record(user, "retrain_completed", f"horizons:{len(results)}")
            logger.info("retrain complete in %.1fs", elapsed)
            return RetrainResult(
                ok=True,
                horizons=results,
                seconds=elapsed,
                excluded_modules=excluded,
            )
    except JobLockHeld as held:
        return _fail(user, str(held))


def _fail(user, reason: str) -> RetrainResult:
    record(user, "retrain_failed", reason)
    logger.warning("retrain refused: %s", reason)
    return RetrainResult(ok=False, error=reason)


@transaction.atomic
def _run(user) -> tuple[list[HorizonResult], list[str]]:
    horizons = sorted(settings.HORIZON_WEEKS)
    students = students_frame()
    registrations = registrations_frame()
    features = features_frame(max(horizons))
    lookup = student_pk_lookup()
    logger.info(
        "retrain: %d registrations, %d weekly rows", len(students), len(features)
    )

    results: list[HorizonResult] = []
    excluded: list[str] = []
    for horizon in horizons:
        result, skipped = _train_one_horizon(
            horizon, features, students, registrations, lookup
        )
        results.append(result)
        for module in skipped:
            if module not in excluded:
                excluded.append(module)
    return results, excluded


def _train_one_horizon(horizon, features, students, registrations, lookup):
    X, y, groups = build_cohort(features, students, registrations, horizon)
    if len(X) < MIN_COHORT_ROWS:
        raise RetrainRefused(
            f"week {horizon} cohort has {len(X)} rows, fewer than the "
            f"{MIN_COHORT_ROWS} required"
        )
    positives = int(y.sum())
    if positives < MIN_COHORT_POSITIVES:
        raise RetrainRefused(
            f"week {horizon} cohort has {positives} withdrawals, fewer than the "
            f"{MIN_COHORT_POSITIVES} required"
        )

    excluded = modules_without_training_data(groups)
    X_tr, X_te, y_tr, y_te, _, groups_te = split_by_year(X, y, groups)
    if len(X_tr) < MIN_COHORT_ROWS:
        raise RetrainRefused(
            f"week {horizon} has {len(X_tr)} training rows after the year split, "
            f"fewer than the {MIN_COHORT_ROWS} required"
        )

    columns_tr = select_set(X_tr, PRODUCTION_FEATURE_SET)
    columns_te = select_set(X_te, PRODUCTION_FEATURE_SET)

    evaluations = []
    for classifier in CANDIDATES:
        evaluation = fit_and_evaluate(columns_tr, y_tr, columns_te, y_te, classifier)
        evaluations.append(evaluation)
        logger.info(
            "week %d %s: AUC-ROC %.3f AUC-PR %.3f Brier %.3f recall@p50 %.3f",
            horizon,
            classifier,
            evaluation.auc_roc,
            evaluation.auc_pr,
            evaluation.brier,
            evaluation.recall_at_p50,
        )

    # The baseline is recorded but never selected: it predicts one number.
    best = max(
        (e for e in evaluations if e.classifier != "majority"),
        key=lambda e: e.auc_pr,
    )
    report = subgroup_metrics(y_te, best.y_prob, groups_te, SUBGROUP_DIMS)

    scored = _store(
        horizon, evaluations, best, report, columns_tr, columns_te,
        y_tr, y_te, groups_te, lookup,
    )
    return (
        HorizonResult(
            horizon_week=horizon,
            classifier=best.classifier,
            auc_roc=best.auc_roc,
            auc_pr=best.auc_pr,
            scored=scored,
        ),
        excluded,
    )


@transaction.atomic
def _store(
    horizon, evaluations, best, report, columns_tr, columns_te,
    y_tr, y_te, groups_te, lookup,
):
    """Write the ModelVersion rows and the RiskScores for this horizon."""
    ModelVersion.objects.filter(horizon_week=horizon, is_current=True).update(
        is_current=False
    )

    artifact_root = Path(settings.ARTIFACT_ROOT)
    artifact_root.mkdir(parents=True, exist_ok=True)

    current = None
    for evaluation in evaluations:
        is_best = evaluation is best
        version = ModelVersion.objects.create(
            horizon_week=horizon,
            feature_set=PRODUCTION_FEATURE_SET,
            classifier=evaluation.classifier,
            auc_roc=_finite(evaluation.auc_roc),
            auc_pr=_finite(evaluation.auc_pr),
            brier=_finite(evaluation.brier),
            recall_at_p50=_finite(evaluation.recall_at_p50),
            subgroup_metrics=report if is_best else {},
            training_rows=evaluation.training_rows,
            test_rows=evaluation.test_rows,
            is_current=is_best,
        )
        if is_best:
            path = artifact_root / f"w{horizon}_{evaluation.classifier}_{version.pk}.joblib"
            joblib.dump(evaluation.model, path)
            version.artifact_path = str(path.relative_to(settings.BASE_DIR))
            version.save(update_fields=["artifact_path"])
            current = version

    basis = explanation_basis(
        best.model, columns_tr, y_tr, columns_te, y_te, best.feature_names
    )
    names = best.feature_names
    values = columns_te[names].to_numpy(dtype=float)

    scores = []
    for position in range(len(columns_te)):
        key = (
            groups_te.at[position, "code_module"],
            groups_te.at[position, "code_presentation"],
            int(groups_te.at[position, "id_student"]),
        )
        student_pk = lookup.get(key)
        if student_pk is None:
            continue
        scores.append(
            RiskScore(
                student_id=student_pk,
                model_version=current,
                probability=float(best.y_prob[position]),
                top_features=top_features(
                    best.model, values[position], names, basis=basis
                ),
            )
        )
    RiskScore.objects.bulk_create(scores, batch_size=5000)
    logger.info("week %d: %d risk scores written", horizon, len(scores))
    return len(scores)


def _finite(value) -> float:
    value = float(value)
    return 0.0 if value != value else value
