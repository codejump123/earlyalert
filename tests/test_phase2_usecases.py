"""Phase 2 test cases: TC11, TC12.

The SRS holds the authoritative wording. The intent assumed here:

  TC11  An administrator uploads the seven files together; they are accepted,
        recorded as one batch, and ingested into Presentation and Student.
        Re-uploading supersedes the previous batch rather than deleting it.
  TC12  An upload that fails any check is rejected whole: no DataUpload row,
        no file kept, no change to Presentation or Student.

If the SRS says otherwise, the SRS wins and these tests get corrected.
"""

import shutil
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from audit.models import AuditEntry
from cohorts.ingest import IngestError, presentation_start_date
from cohorts.models import DataUpload, Presentation, Student
from pipeline.validate import REQUIRED_FILES

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def upload_root(settings, tmp_path):
    """Keep uploaded files inside the test's own tmp_path."""
    settings.UPLOAD_ROOT = tmp_path / "uploads"
    return settings.UPLOAD_ROOT


@pytest.fixture
def seven_files():
    """The fixture cohort as Django uploaded files."""
    def build(**overrides):
        files = []
        for name in REQUIRED_FILES:
            content = overrides.get(name, (FIXTURES / name).read_bytes())
            if content is None:
                continue
            files.append(SimpleUploadedFile(name, content, "text/csv"))
        return files

    return build


def _login_admin(client, administrator, password):
    client.login(username=administrator.username, password=password)


# --- TC11: a valid upload is accepted and ingested -----------------------

def test_tc11_seven_files_are_accepted_and_ingested(
    client, administrator, password, upload_root, seven_files
):
    _login_admin(client, administrator, password)
    response = client.post("/admin/upload/", {"files": seven_files()})
    assert response.status_code == 200
    result = response.context["result"]
    assert result.ok, result.error

    assert Presentation.objects.count() == 2
    assert Student.objects.count() == 50
    assert DataUpload.objects.filter(superseded=False).count() == 7
    assert {u.filename for u in DataUpload.objects.all()} == set(REQUIRED_FILES)
    assert AuditEntry.objects.filter(action="upload_accepted").count() == 1


def test_tc11_row_counts_and_uploader_are_recorded(
    client, administrator, password, upload_root, seven_files
):
    _login_admin(client, administrator, password)
    client.post("/admin/upload/", {"files": seven_files()})
    row = DataUpload.objects.get(filename="studentVle.csv")
    assert row.row_count == 1000
    assert len(row.checksum) == 64
    assert row.uploaded_by == administrator
    # One batch id across all seven files.
    assert DataUpload.objects.values("batch_id").distinct().count() == 1


def test_tc11_demographics_are_ingested_faithfully(
    client, administrator, password, upload_root, seven_files
):
    _login_admin(client, administrator, password)
    client.post("/admin/upload/", {"files": seven_files()})

    presentation = Presentation.objects.get(code_presentation="2013J")
    assert presentation.code_module == "AAA"
    assert presentation.length_days == 268
    assert presentation.year == 2013

    # k == 0: withdraws on day 7, disabled, first IMD band, no prior attempts.
    student = Student.objects.get(presentation=presentation, id_student=1001)
    assert student.final_result == "Withdrawn"
    assert student.disability is True
    assert student.imd_band == "0-10%"
    assert student.date_registration == -30
    assert student.date_unregistration == 7

    # k == 8 passes the course, so it is not disabled and never unregistered.
    other = Student.objects.get(presentation=presentation, id_student=1009)
    assert other.final_result != "Withdrawn"
    assert other.disability is False
    assert other.date_unregistration is None

    # k == 7 has no IMD band; '?' must land as NULL, not as a string.
    unbanded = Student.objects.get(presentation=presentation, id_student=1008)
    assert unbanded.imd_band is None

    # 8 withdrawals per presentation, by construction.
    assert Student.objects.filter(final_result="Withdrawn").count() == 16


def test_tc11_imd_band_is_normalized_on_ingest(
    client, administrator, password, upload_root, seven_files
):
    _login_admin(client, administrator, password)
    client.post("/admin/upload/", {"files": seven_files()})
    bands = set(
        Student.objects.exclude(imd_band=None).values_list("imd_band", flat=True)
    )
    assert "10-20" not in bands
    assert "10-20%" in bands
    assert all(band.endswith("%") for band in bands)


def test_tc11_reupload_supersedes_and_does_not_duplicate(
    client, administrator, password, upload_root, seven_files
):
    _login_admin(client, administrator, password)
    client.post("/admin/upload/", {"files": seven_files()})
    first_batch = DataUpload.objects.first().batch_id

    response = client.post("/admin/upload/", {"files": seven_files()})
    assert response.context["result"].ok

    assert DataUpload.objects.count() == 14          # nothing deleted
    assert DataUpload.objects.filter(superseded=True).count() == 7
    assert DataUpload.objects.filter(superseded=False).count() == 7
    assert not DataUpload.objects.filter(
        batch_id=first_batch, superseded=False
    ).exists()

    # Students are updated in place, not duplicated.
    assert Student.objects.count() == 50
    assert Presentation.objects.count() == 2
    assert AuditEntry.objects.filter(action="upload_accepted").count() == 2


def test_tc11_presentation_start_date_follows_the_code():
    """OULAD names the intake: B is February, J is October."""
    assert presentation_start_date("2013J").year == 2013
    assert presentation_start_date("2013J").month == 10
    assert presentation_start_date("2014B").month == 2
    with pytest.raises(IngestError):
        presentation_start_date("2013X")


def test_tc11_upload_is_administrator_only(
    client, advisor_a, password, upload_root, seven_files
):
    client.login(username=advisor_a.username, password=password)
    assert client.get("/admin/upload/").status_code == 403
    assert client.post("/admin/upload/", {"files": seven_files()}).status_code == 403
    assert DataUpload.objects.count() == 0
    assert Student.objects.count() == 0
    assert AuditEntry.objects.filter(action="denied", target="admin-area").count() == 2


# --- TC12: a failing upload is rejected whole ----------------------------

@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"studentVle.csv": None}, "missing required file(s): studentVle.csv"),
        (
            {"courses.csv": b"module,code_presentation,module_presentation_length\nAAA,2013J,268\n"},
            "courses: expected column code_module at position 1, found module",
        ),
        ({"vle.csv": b""}, "vle: file is empty, expected a header row"),
    ],
)
def test_tc12_invalid_upload_stores_nothing(
    client, administrator, password, upload_root, seven_files, overrides, fragment
):
    _login_admin(client, administrator, password)
    response = client.post("/admin/upload/", {"files": seven_files(**overrides)})
    assert response.status_code == 200

    result = response.context["result"]
    assert result.ok is False
    assert result.error == fragment

    assert DataUpload.objects.count() == 0
    assert Presentation.objects.count() == 0
    assert Student.objects.count() == 0
    assert AuditEntry.objects.filter(action="upload_accepted").count() == 0
    assert AuditEntry.objects.filter(action="upload_rejected").count() == 1


def test_tc12_rejection_leaves_no_file_behind(
    client, administrator, password, upload_root, seven_files
):
    _login_admin(client, administrator, password)
    client.post("/admin/upload/", {"files": seven_files(**{"studentVle.csv": None})})
    staged = list((upload_root / "staging").glob("*")) if upload_root.exists() else []
    assert staged == []


def test_tc12_a_good_batch_survives_a_later_bad_one(
    client, administrator, password, upload_root, seven_files
):
    """A rejected replacement must not supersede the data already in place."""
    _login_admin(client, administrator, password)
    client.post("/admin/upload/", {"files": seven_files()})

    response = client.post(
        "/admin/upload/", {"files": seven_files(**{"vle.csv": None})}
    )
    assert response.context["result"].ok is False
    assert DataUpload.objects.filter(superseded=False).count() == 7
    assert Student.objects.count() == 50


def test_tc12_empty_submission_is_rejected(
    client, administrator, password, upload_root
):
    _login_admin(client, administrator, password)
    response = client.post("/admin/upload/", {})
    assert response.context["result"].ok is False
    assert response.context["result"].error == "no files were selected"
    assert DataUpload.objects.count() == 0


def test_tc12_ingest_failure_rolls_back_and_is_audited(
    client, administrator, password, upload_root, seven_files
):
    """studentInfo naming a presentation courses.csv does not have.

    The files pass schema validation, so the failure comes from ingest. The
    batch must roll back whole and still be recorded as rejected.
    """
    info = (FIXTURES / "studentInfo.csv").read_bytes().replace(b"2013J", b"2015J")
    _login_admin(client, administrator, password)
    response = client.post(
        "/admin/upload/", {"files": seven_files(**{"studentInfo.csv": info})}
    )

    result = response.context["result"]
    assert result.ok is False
    assert "AAA/2015J" in result.error

    assert DataUpload.objects.count() == 0
    assert Student.objects.count() == 0
    assert Presentation.objects.count() == 0
    assert AuditEntry.objects.filter(action="upload_rejected").count() == 1
    assert AuditEntry.objects.filter(action="upload_accepted").count() == 0
