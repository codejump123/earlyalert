"""Phase 3 test cases: TC13, TC14.

The SRS holds the authoritative wording. The intent assumed here:

  TC13  An administrator rebuilds features from the current upload; every
        student's weekly rows are written, and the run is audited started and
        completed.
  TC14  A rebuild is refused when there is nothing to build from or when
        another job holds the lock, and a refusal changes nothing.

If the SRS says otherwise, the SRS wins and these tests get corrected.
"""

import shutil
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from audit.models import AuditEntry
from cohorts.models import Student
from earlyalert.joblock import JobLockHeld, current_job, job_lock, read_progress
from features.models import WeeklyFeatures
from features.rebuild import JOB_NAME
from pipeline.validate import REQUIRED_FILES

FIXTURES = Path(__file__).parent / "fixtures"
LAST_WEEK = 39


@pytest.fixture
def job_root(settings, tmp_path):
    """Keep lock and progress files inside the test's own tmp_path."""
    settings.BASE_DIR = tmp_path
    settings.UPLOAD_ROOT = tmp_path / "uploads"
    return tmp_path


@pytest.fixture
def uploaded(client, administrator, password, job_root):
    """An accepted upload of the fixture cohort, ready to rebuild from."""
    client.login(username=administrator.username, password=password)
    files = [
        SimpleUploadedFile(name, (FIXTURES / name).read_bytes(), "text/csv")
        for name in REQUIRED_FILES
    ]
    response = client.post("/admin/upload/", {"files": files})
    assert response.context["result"].ok
    return client


# --- TC13: a rebuild writes the weekly rows ------------------------------

def test_tc13_rebuild_writes_features_for_every_student(uploaded):
    response = uploaded.post("/admin/rebuild/")
    result = response.context["result"]
    assert result.ok, result.error

    assert result.rows == 50 * LAST_WEEK
    assert result.students == 50
    assert WeeklyFeatures.objects.count() == 50 * LAST_WEEK
    assert WeeklyFeatures.objects.values("student").distinct().count() == 50


def test_tc13_stored_values_match_the_builder(uploaded):
    uploaded.post("/admin/rebuild/")
    student = Student.objects.get(id_student=1001)

    # k=0, week 3: two active days at 5 clicks, gap of 5 to the week end.
    week3 = WeeklyFeatures.objects.get(student=student, week=3)
    assert week3.total_clicks == 10
    assert week3.active_days == 2
    assert week3.clicks_per_active_day == 5.0
    assert week3.days_since_last_activity == 5
    assert week3.mean_score is None
    assert week3.activity_type_shares == {"resource": 1.0}

    # Week 9: the unsubmitted second assessment drags the mean to 20.
    week9 = WeeklyFeatures.objects.get(student=student, week=9)
    assert week9.assessments_submitted == 1
    assert week9.mean_score == 20.0
    assert week9.weighted_score_to_date == 20.0


def test_tc13_rebuild_is_audited_started_and_completed(uploaded):
    uploaded.post("/admin/rebuild/")
    assert AuditEntry.objects.filter(action="rebuild_started").count() == 1
    completed = AuditEntry.objects.get(action="rebuild_completed")
    assert completed.target == f"rows:{50 * LAST_WEEK}"


def test_tc13_progress_is_logged_for_the_view(uploaded):
    uploaded.post("/admin/rebuild/")
    progress = read_progress(JOB_NAME)
    assert any("chunk 1:" in line for line in progress)
    assert any("rows read" in line for line in progress)
    assert any("rebuild complete" in line for line in progress)


def test_tc13_rebuild_replaces_rather_than_appends(uploaded):
    uploaded.post("/admin/rebuild/")
    first = WeeklyFeatures.objects.count()
    uploaded.post("/admin/rebuild/")
    assert WeeklyFeatures.objects.count() == first
    assert AuditEntry.objects.filter(action="rebuild_completed").count() == 2


def test_tc13_rebuild_is_administrator_only(client, advisor_a, password, job_root):
    client.login(username=advisor_a.username, password=password)
    assert client.get("/admin/rebuild/").status_code == 403
    assert client.post("/admin/rebuild/").status_code == 403
    assert WeeklyFeatures.objects.count() == 0


# --- TC14: refusals ------------------------------------------------------

def test_tc14_rebuild_refuses_without_an_upload(
    client, administrator, password, job_root
):
    client.login(username=administrator.username, password=password)
    response = client.post("/admin/rebuild/")
    result = response.context["result"]
    assert result.ok is False
    assert result.error == (
        "no complete upload is in place; upload the seven files first"
    )
    assert WeeklyFeatures.objects.count() == 0
    # A refusal changes no state, so it writes no audit entry: the SRS action
    # list has no rebuild_failed.
    assert not AuditEntry.objects.filter(action__startswith="rebuild").exists()


def test_tc14_rebuild_refuses_while_the_lock_is_held(uploaded):
    with job_lock(JOB_NAME):
        response = uploaded.post("/admin/rebuild/")
    result = response.context["result"]
    assert result.ok is False
    assert "already running" in result.error
    assert WeeklyFeatures.objects.count() == 0
    assert not AuditEntry.objects.filter(action="rebuild_started").exists()


def test_tc14_a_held_lock_refuses_a_second_holder(job_root):
    with job_lock(JOB_NAME):
        assert current_job(JOB_NAME) is not None
        with pytest.raises(JobLockHeld):
            with job_lock(JOB_NAME):
                pass
    assert current_job(JOB_NAME) is None


def test_tc14_a_lock_left_by_a_dead_process_is_broken(job_root):
    import json
    from datetime import datetime, timezone

    from earlyalert import joblock

    stale = joblock.jobs_dir() / f"{JOB_NAME}.lock"
    stale.write_text(json.dumps({
        "name": JOB_NAME,
        "pid": 2 ** 22,  # above any live pid on this machine
        "started_at": datetime.now(timezone.utc).isoformat(),
    }))
    assert current_job(JOB_NAME) is None
    assert not stale.exists()
    with job_lock(JOB_NAME):
        pass


def test_tc14_the_lock_is_released_when_the_job_raises(job_root):
    with pytest.raises(RuntimeError):
        with job_lock(JOB_NAME):
            raise RuntimeError("boom")
    assert current_job(JOB_NAME) is None


def test_tc14_rebuild_refuses_when_the_upload_files_are_gone(uploaded, job_root):
    """The DataUpload rows survive, but the directory they name does not."""
    for path in (job_root / "uploads").iterdir():
        if path.is_dir():
            shutil.rmtree(path)
    response = uploaded.post("/admin/rebuild/")
    assert response.context["result"].ok is False
    assert WeeklyFeatures.objects.count() == 0
