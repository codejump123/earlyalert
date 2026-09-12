"""Upload validation. Pure pipeline: no Django, no database.

Every case runs against a copy of the synthetic fixture cohort, so a test that
corrupts a file cannot damage the fixture.
"""

import shutil
from pathlib import Path

import pytest

from pipeline.validate import (
    EXPECTED_COLUMNS,
    MAX_FILE_BYTES,
    REQUIRED_FILES,
    validate_upload,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Hand-counted from tests/fixtures/generate.py; see its docstring for the rules.
EXPECTED_ROWS = {
    "courses.csv": 2,
    "assessments.csv": 6,
    "vle.csv": 12,
    "studentInfo.csv": 50,
    "studentRegistration.csv": 50,
    "studentAssessment.csv": 90,
    "studentVle.csv": 1000,
}


@pytest.fixture
def upload(tmp_path):
    """A writable copy of the fixture cohort as a {filename: path} mapping."""
    for name in REQUIRED_FILES:
        shutil.copy(FIXTURES / name, tmp_path / name)
    return {name: tmp_path / name for name in REQUIRED_FILES}


def test_the_fixture_cohort_validates(upload):
    result = validate_upload(upload)
    assert result.ok, result.error
    assert result.error is None
    assert bool(result) is True
    assert set(result.files) == set(REQUIRED_FILES)


def test_row_counts_exclude_the_header(upload):
    result = validate_upload(upload)
    counted = {name: stats.row_count for name, stats in result.files.items()}
    assert counted == EXPECTED_ROWS


def test_checksum_is_sha256_of_the_file(upload):
    import hashlib

    result = validate_upload(upload)
    expected = hashlib.sha256((FIXTURES / "courses.csv").read_bytes()).hexdigest()
    assert result.files["courses.csv"].checksum == expected
    assert len(expected) == 64


def test_row_count_survives_a_missing_final_newline(upload):
    path = upload["courses.csv"]
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    result = validate_upload(upload)
    assert result.files["courses.csv"].row_count == 2


# --- failures return the first problem and no statistics -----------------

def test_missing_file_is_named(upload):
    del upload["studentVle.csv"]
    result = validate_upload(upload)
    assert not result.ok
    assert result.error == "missing required file(s): studentVle.csv"
    assert result.files == {}


def test_all_missing_files_are_listed(upload):
    del upload["courses.csv"]
    del upload["vle.csv"]
    result = validate_upload(upload)
    assert result.error == "missing required file(s): courses.csv, vle.csv"


def test_unexpected_file_is_rejected(upload, tmp_path):
    extra = tmp_path / "notes.csv"
    extra.write_text("a,b\n1,2\n")
    upload["notes.csv"] = extra
    result = validate_upload(upload)
    assert not result.ok
    assert result.error == "unexpected file(s): notes.csv"


def test_missing_files_are_reported_before_oversize_ones(upload, monkeypatch):
    monkeypatch.setattr("pipeline.validate.MAX_FILE_BYTES", 1)
    del upload["courses.csv"]
    result = validate_upload(upload)
    assert result.error.startswith("missing required file(s)")


def test_file_over_the_limit_is_rejected(upload, monkeypatch):
    monkeypatch.setattr("pipeline.validate.MAX_FILE_BYTES", 10)
    result = validate_upload(upload)
    assert not result.ok
    assert "exceeds the 500 MB per-file limit" in result.error


def test_the_limit_is_500_mb():
    assert MAX_FILE_BYTES == 500 * 1024 * 1024


# --- header checks --------------------------------------------------------

def _rewrite_header(path: Path, header: list[str]) -> None:
    lines = path.read_text().splitlines()
    lines[0] = ",".join(header)
    path.write_text("\n".join(lines) + "\n")


def test_renamed_column_reports_name_and_position(upload):
    """The message form the SRS gives, verbatim."""
    header = list(EXPECTED_COLUMNS["studentVle.csv"])
    header[5] = "clicks"
    _rewrite_header(upload["studentVle.csv"], header)
    result = validate_upload(upload)
    assert not result.ok
    assert result.error == (
        "studentVle: expected column sum_click at position 6, found clicks"
    )


def test_positions_are_one_indexed(upload):
    header = list(EXPECTED_COLUMNS["courses.csv"])
    header[0] = "module"
    _rewrite_header(upload["courses.csv"], header)
    result = validate_upload(upload)
    assert result.error == (
        "courses: expected column code_module at position 1, found module"
    )


def test_reordered_columns_are_rejected(upload):
    header = list(EXPECTED_COLUMNS["studentInfo.csv"])
    header[3], header[4] = header[4], header[3]
    _rewrite_header(upload["studentInfo.csv"], header)
    result = validate_upload(upload)
    assert result.error == (
        "studentInfo: expected column gender at position 4, found region"
    )


def test_truncated_header_is_rejected(upload):
    header = EXPECTED_COLUMNS["courses.csv"][:2]
    _rewrite_header(upload["courses.csv"], header)
    result = validate_upload(upload)
    assert result.error == (
        "courses: expected column module_presentation_length at position 3, "
        "found end of header"
    )


def test_extra_trailing_column_is_rejected(upload):
    header = EXPECTED_COLUMNS["courses.csv"] + ["notes"]
    _rewrite_header(upload["courses.csv"], header)
    result = validate_upload(upload)
    assert result.error == (
        "courses: expected 3 columns, found 4 (first extra: notes)"
    )


def test_empty_file_is_rejected(upload):
    upload["vle.csv"].write_text("")
    result = validate_upload(upload)
    assert result.error == "vle: file is empty, expected a header row"


def test_quoted_headers_are_accepted(upload):
    """Real OULAD quotes every header field; the fixture does not."""
    header = [f'"{c}"' for c in EXPECTED_COLUMNS["courses.csv"]]
    _rewrite_header(upload["courses.csv"], header)
    assert validate_upload(upload).ok


def test_header_is_checked_in_file_order(upload):
    """courses is checked before studentVle, so its failure is the one seen."""
    _rewrite_header(upload["courses.csv"], ["wrong", "b", "c"])
    _rewrite_header(upload["studentVle.csv"], ["also_wrong"] + EXPECTED_COLUMNS["studentVle.csv"][1:])
    assert validate_upload(upload).error.startswith("courses:")


def test_validation_stores_nothing(upload, tmp_path):
    before = sorted(p.name for p in tmp_path.iterdir())
    validate_upload(upload)
    assert sorted(p.name for p in tmp_path.iterdir()) == before
