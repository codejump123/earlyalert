"""Schema validation for an upload of the seven OULAD CSV files.

No Django imports. Stores nothing: the caller decides what to do with the
result. Checks run in a fixed order and the first failure is returned, so an
upload that is wrong in several ways reports the first thing a person would
have to fix.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

# 500 MB per file (SRS fixed decision). studentVle.csv is ~433 MB.
MAX_FILE_BYTES = 500 * 1024 * 1024

# Header order as published by the Open University, not the reading order in
# the SRS data table. Validation compares position by position against this.
EXPECTED_COLUMNS: dict[str, list[str]] = {
    "courses.csv": [
        "code_module",
        "code_presentation",
        "module_presentation_length",
    ],
    "assessments.csv": [
        "code_module",
        "code_presentation",
        "id_assessment",
        "assessment_type",
        "date",
        "weight",
    ],
    "vle.csv": [
        "id_site",
        "code_module",
        "code_presentation",
        "activity_type",
        "week_from",
        "week_to",
    ],
    "studentInfo.csv": [
        "code_module",
        "code_presentation",
        "id_student",
        "gender",
        "region",
        "highest_education",
        "imd_band",
        "age_band",
        "num_of_prev_attempts",
        "studied_credits",
        "disability",
        "final_result",
    ],
    "studentRegistration.csv": [
        "code_module",
        "code_presentation",
        "id_student",
        "date_registration",
        "date_unregistration",
    ],
    "studentAssessment.csv": [
        "id_assessment",
        "id_student",
        "date_submitted",
        "is_banked",
        "score",
    ],
    "studentVle.csv": [
        "code_module",
        "code_presentation",
        "id_student",
        "id_site",
        "date",
        "sum_click",
    ],
}

REQUIRED_FILES = tuple(EXPECTED_COLUMNS)

# Read size for the checksum and row-count pass. studentVle.csv is never read
# into memory whole, here or anywhere else.
_READ_CHUNK = 4 * 1024 * 1024


@dataclass(frozen=True)
class FileStats:
    """One validated file. checksum is SHA-256; row_count excludes the header."""

    filename: str
    size_bytes: int
    checksum: str
    row_count: int


@dataclass
class ValidationResult:
    """ok is True only when all seven files passed every check.

    On failure, error carries the first failure as a single sentence and files
    is empty: a rejected upload produces no statistics.
    """

    ok: bool
    error: str | None = None
    files: dict[str, FileStats] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok


def validate_upload(paths: dict[str, Path]) -> ValidationResult:
    """Validate a complete upload.

    Input: {filename: path} for the files offered, filenames as published
    (e.g. "studentVle.csv").
    Output: ValidationResult. On success files holds one FileStats per
    required file; on failure error holds the first failure and files is empty.

    Order of checks: all seven names present, no unexpected names, no file over
    500 MB, then each header row against the expected columns in order.
    """
    missing = [name for name in REQUIRED_FILES if name not in paths]
    if missing:
        return ValidationResult(
            ok=False,
            error=f"missing required file(s): {', '.join(missing)}",
        )

    unexpected = sorted(set(paths) - set(REQUIRED_FILES))
    if unexpected:
        return ValidationResult(
            ok=False,
            error=f"unexpected file(s): {', '.join(unexpected)}",
        )

    for name in REQUIRED_FILES:
        path = Path(paths[name])
        if not path.is_file():
            return ValidationResult(ok=False, error=f"{name}: file not found")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            mb = size / (1024 * 1024)
            return ValidationResult(
                ok=False,
                error=f"{name}: {mb:.0f} MB exceeds the 500 MB per-file limit",
            )

    for name in REQUIRED_FILES:
        error = _check_header(name, Path(paths[name]))
        if error:
            return ValidationResult(ok=False, error=error)

    stats: dict[str, FileStats] = {}
    for name in REQUIRED_FILES:
        path = Path(paths[name])
        checksum, row_count = _checksum_and_rows(path)
        stats[name] = FileStats(
            filename=name,
            size_bytes=path.stat().st_size,
            checksum=checksum,
            row_count=row_count,
        )
    return ValidationResult(ok=True, files=stats)


def _check_header(name: str, path: Path) -> str | None:
    """Return a failure message for the first wrong column, else None.

    Message form: 'studentVle: expected column sum_click at position 6,
    found clicks'. Positions are 1-indexed, as a person reading the header
    would count them.
    """
    expected = EXPECTED_COLUMNS[name]
    stem = name.removesuffix(".csv")
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        header = next(csv.reader(handle), None)
    if header is None:
        return f"{stem}: file is empty, expected a header row"
    header = [column.strip() for column in header]
    for position, want in enumerate(expected, start=1):
        if position > len(header):
            return (
                f"{stem}: expected column {want} at position {position}, "
                "found end of header"
            )
        got = header[position - 1]
        if got != want:
            return (
                f"{stem}: expected column {want} at position {position}, "
                f"found {got}"
            )
    if len(header) > len(expected):
        extra = header[len(expected)]
        return (
            f"{stem}: expected {len(expected)} columns, "
            f"found {len(header)} (first extra: {extra})"
        )
    return None


def _checksum_and_rows(path: Path) -> tuple[str, int]:
    """Return (sha256 hex digest, data row count) in one streaming pass.

    Rows are counted by newline, so the count is the number of data lines
    after the header. The file is read in 4 MB chunks and never held whole.
    """
    digest = hashlib.sha256()
    newlines = 0
    trailing_byte = b""
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK):
            digest.update(chunk)
            newlines += chunk.count(b"\n")
            trailing_byte = chunk[-1:]
    if trailing_byte and trailing_byte != b"\n":
        # Final line has no newline terminator.
        newlines += 1
    return digest.hexdigest(), max(newlines - 1, 0)
