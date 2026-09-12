"""Phase 4 test cases: TC15, TC16.

The SRS holds the authoritative wording. The intent assumed here:

  TC15  An administrator retrains; a model is fitted per horizon on the 2013
        presentations, the best is marked current with its subgroup metrics,
        risk scores are written for the 2014 presentations, and the run is
        audited started and completed.
  TC16  A retrain that cannot proceed is refused with a reason and writes
        retrain_failed, changing nothing.

If the SRS says otherwise, the SRS wins and these tests get corrected.
"""

import datetime as dt

import pytest
from django.utils import timezone

from audit.models import AuditEntry
from audit.services import record
from cohorts.models import DataUpload
from earlyalert.joblock import job_lock
from scoring.models import ModelVersion, RiskScore
from scoring.retrain import JOB_NAME, MIN_COHORT_POSITIVES, MIN_COHORT_ROWS


@pytest.fixture
def ready(db, administrator, trainable_cohort):
    """Cohort in place, features newer than the upload: retrain may proceed."""
    DataUpload.objects.create(
        filename="studentInfo.csv",
        checksum="0" * 64,
        row_count=1400,
        uploaded_by=administrator,
    )
    for name in (
        "courses.csv", "assessments.csv", "vle.csv", "studentRegistration.csv",
        "studentAssessment.csv", "studentVle.csv",
    ):
        DataUpload.objects.create(
            filename=name, checksum="0" * 64, row_count=1,
            uploaded_by=administrator,
        )
    record(administrator, "rebuild_completed", "rows:16800")
    return administrator


@pytest.fixture
def admin_client(client, ready, password):
    client.login(username=ready.username, password=password)
    return client


# --- TC15: a retrain fits, stores and scores -----------------------------

def test_tc15_retrain_produces_a_current_model_per_horizon(admin_client, settings):
    response = admin_client.post("/admin/retrain/")
    result = response.context["result"]
    assert result.ok, result.error

    current = ModelVersion.objects.filter(is_current=True)
    assert current.count() == len(settings.HORIZON_WEEKS)
    assert sorted(current.values_list("horizon_week", flat=True)) == sorted(
        settings.HORIZON_WEEKS
    )
    # The baseline is recorded beside the models, but never chosen.
    assert ModelVersion.objects.filter(classifier="majority").exists()
    assert not current.filter(classifier="majority").exists()


def test_tc15_the_chosen_model_beats_the_baseline(admin_client):
    admin_client.post("/admin/retrain/")
    for horizon in (4, 8, 12):
        best = ModelVersion.objects.get(horizon_week=horizon, is_current=True)
        baseline = ModelVersion.objects.get(
            horizon_week=horizon, classifier="majority"
        )
        assert best.auc_pr > baseline.auc_pr
        assert best.auc_roc > 0.5


def test_tc15_metrics_and_provenance_are_stored(admin_client):
    admin_client.post("/admin/retrain/")
    model = ModelVersion.objects.get(horizon_week=8, is_current=True)
    assert 0.0 <= model.auc_roc <= 1.0
    assert 0.0 <= model.auc_pr <= 1.0
    assert 0.0 <= model.brier <= 1.0
    assert model.feature_set == "All"
    assert model.training_rows > 0 and model.test_rows > 0
    assert model.artifact_path.endswith(".joblib")


def test_tc15_subgroup_metrics_are_stored_on_the_current_model(admin_client):
    admin_client.post("/admin/retrain/")
    model = ModelVersion.objects.get(horizon_week=8, is_current=True)
    report = model.subgroup_metrics
    assert set(report) == {
        "imd_band", "disability", "age_band", "highest_education", "gender",
    }
    for dimension in report.values():
        assert dimension["min_n"] == 20
        for entry in dimension["levels"].values():
            if entry.get("suppressed"):
                assert entry["n"] < 20
            else:
                assert entry["n"] >= 20


def test_tc15_risk_scores_are_written_for_the_test_year_only(admin_client):
    """The model is fitted on 2013; scoring it would be scoring its own
    training data."""
    admin_client.post("/admin/retrain/")
    model = ModelVersion.objects.get(horizon_week=8, is_current=True)
    scores = RiskScore.objects.filter(model_version=model)
    assert scores.count() > 0
    years = {
        score.student.presentation.code_presentation[:4] for score in scores[:50]
    }
    assert years == {"2014"}
    assert not RiskScore.objects.filter(
        student__presentation__code_presentation__startswith="2013"
    ).exists()


def test_tc15_every_score_carries_three_readable_reasons(admin_client):
    admin_client.post("/admin/retrain/")
    for score in RiskScore.objects.all()[:20]:
        assert 0.0 <= score.probability <= 1.0
        assert len(score.top_features) == 3
        for item in score.top_features:
            assert set(item) == {"feature", "value", "direction", "text"}
            assert item["direction"] in ("raises", "lowers")
            assert item["text"] and not item["text"].startswith("d__")


def test_tc15_retrain_is_audited_started_and_completed(admin_client):
    admin_client.post("/admin/retrain/")
    assert AuditEntry.objects.filter(action="retrain_started").count() == 1
    assert AuditEntry.objects.filter(action="retrain_completed").count() == 1
    assert not AuditEntry.objects.filter(action="retrain_failed").exists()


def test_tc15_retraining_replaces_the_previous_current_model(admin_client):
    admin_client.post("/admin/retrain/")
    first = set(
        ModelVersion.objects.filter(is_current=True).values_list("pk", flat=True)
    )
    admin_client.post("/admin/retrain/")
    current = ModelVersion.objects.filter(is_current=True)
    assert current.count() == 3
    assert set(current.values_list("pk", flat=True)) & first == set()
    # The superseded versions are kept, not deleted.
    assert ModelVersion.objects.filter(pk__in=first, is_current=False).count() == 3


def test_tc15_retrain_is_administrator_only(client, advisor_a, password):
    client.login(username=advisor_a.username, password=password)
    assert client.get("/admin/retrain/").status_code == 403
    assert client.post("/admin/retrain/").status_code == 403
    assert ModelVersion.objects.count() == 0


# --- TC16: refusals ------------------------------------------------------

def test_tc16_refuses_when_no_upload_exists(client, administrator, password, trainable_cohort):
    client.login(username=administrator.username, password=password)
    response = client.post("/admin/retrain/")
    result = response.context["result"]
    assert result.ok is False
    assert "no complete upload" in result.error
    assert _failed_reason() == result.error
    assert ModelVersion.objects.count() == 0


def test_tc16_refuses_when_features_have_never_been_built(
    client, administrator, password, trainable_cohort
):
    DataUpload.objects.create(
        filename="studentInfo.csv", checksum="0" * 64, row_count=1,
        uploaded_by=administrator,
    )
    client.login(username=administrator.username, password=password)
    response = client.post("/admin/retrain/")
    assert "never been built" in response.context["result"].error
    assert ModelVersion.objects.count() == 0


def test_tc16_refuses_when_features_predate_the_upload(admin_client, ready):
    """A new upload lands after the last rebuild: the features are stale."""
    AuditEntry.objects.filter(action="rebuild_completed").delete.__self__.model
    later = timezone.now() + dt.timedelta(hours=1)
    DataUpload.objects.filter(filename="studentInfo.csv").update(uploaded_at=later)

    response = admin_client.post("/admin/retrain/")
    result = response.context["result"]
    assert result.ok is False
    assert "predate the current upload" in result.error
    assert _failed_reason() == result.error
    assert ModelVersion.objects.count() == 0


def test_tc16_refuses_while_the_lock_is_held(admin_client):
    with job_lock(JOB_NAME):
        response = admin_client.post("/admin/retrain/")
    result = response.context["result"]
    assert result.ok is False
    assert "already running" in result.error
    assert _failed_reason() == result.error
    assert ModelVersion.objects.count() == 0


def test_tc16_refuses_a_cohort_below_the_row_floor(admin_client):
    from cohorts.models import Student

    keep = list(
        Student.objects.values_list("pk", flat=True)[: MIN_COHORT_ROWS - 100]
    )
    Student.objects.exclude(pk__in=keep).delete()

    response = admin_client.post("/admin/retrain/")
    result = response.context["result"]
    assert result.ok is False
    assert f"fewer than the {MIN_COHORT_ROWS} required" in result.error
    assert _failed_reason() == result.error
    assert ModelVersion.objects.count() == 0
    assert RiskScore.objects.count() == 0


def test_tc16_refuses_a_cohort_below_the_positive_floor(admin_client):
    from cohorts.models import Student

    # Leave 10 withdrawals, far below the floor, but plenty of rows.
    withdrawn = list(
        Student.objects.filter(final_result="Withdrawn").values_list("pk", flat=True)
    )
    Student.objects.filter(pk__in=withdrawn[10:]).update(final_result="Pass")

    response = admin_client.post("/admin/retrain/")
    result = response.context["result"]
    assert result.ok is False
    assert f"fewer than the {MIN_COHORT_POSITIVES} required" in result.error
    assert ModelVersion.objects.count() == 0


def test_tc16_a_refusal_mid_run_leaves_nothing_behind(admin_client):
    """Week 4 trains, week 8 refuses: the run must undo week 4 too."""
    from cohorts.models import Student

    # Everyone unregisters on day 57: inside week 8's cutoff, outside week 4's.
    Student.objects.filter(final_result="Withdrawn").update(date_unregistration=57)

    response = admin_client.post("/admin/retrain/")
    assert response.context["result"].ok is False
    assert ModelVersion.objects.count() == 0
    assert RiskScore.objects.count() == 0
    assert AuditEntry.objects.filter(action="retrain_started").count() == 1
    assert AuditEntry.objects.filter(action="retrain_failed").count() == 1
    assert not AuditEntry.objects.filter(action="retrain_completed").exists()


def _failed_reason() -> str:
    return AuditEntry.objects.filter(action="retrain_failed").latest("timestamp").target
