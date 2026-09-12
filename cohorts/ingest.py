"""Load courses, studentInfo and studentRegistration into the ORM.

This is the Django side of ingest: pipeline/loader.py reads the files, this
module writes rows. Nothing here is called until validation has passed on all
seven files.

Only three of the seven files are read here. assessments, vle,
studentAssessment and studentVle stay on disk and are read by the feature
builder, which is chunked; none of them becomes a table.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from django.db import transaction

from pipeline.loader import (
    read_courses,
    read_student_info,
    read_student_registration,
)

from .models import Presentation, Student

logger = logging.getLogger("earlyalert")

# OULAD names a presentation by start month: B is the February intake, J the
# October one. The files carry no start date, so it is derived from the code.
# The day of month is a convention, not data; only the ordering and the year
# are used anywhere, and day offsets are always relative to day 0.
PRESENTATION_START_MONTH = {"B": 2, "J": 10}
PRESENTATION_START_DAY = 1


class IngestError(Exception):
    """Raised when the three files disagree with each other."""


@dataclass
class IngestCounts:
    presentations_created: int = 0
    presentations_updated: int = 0
    students_created: int = 0
    students_updated: int = 0

    @property
    def presentations(self) -> int:
        return self.presentations_created + self.presentations_updated

    @property
    def students(self) -> int:
        return self.students_created + self.students_updated


def presentation_start_date(code_presentation: str) -> dt.date:
    """Input: a presentation code such as '2013J'. Output: its start date.

    Raises IngestError on a code that is not four digits plus B or J.
    """
    code = str(code_presentation)
    suffix = code[4:5]
    if len(code) != 5 or not code[:4].isdigit() or suffix not in PRESENTATION_START_MONTH:
        raise IngestError(
            f"unrecognized presentation code {code!r}: expected four digits "
            "followed by B or J"
        )
    return dt.date(
        int(code[:4]), PRESENTATION_START_MONTH[suffix], PRESENTATION_START_DAY
    )


def _to_int(value) -> int | None:
    """pandas <NA>/NaN to None, anything else to int."""
    if value is None or pd.isna(value):
        return None
    return int(value)


def _to_str(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    return str(value)


@transaction.atomic
def ingest_batch(upload_dir: Path) -> IngestCounts:
    """Input: a directory holding the seven validated OULAD files.
    Output: IngestCounts of rows created and updated.

    Presentations come from courses.csv; students from studentInfo.csv joined
    to studentRegistration.csv on (code_module, code_presentation,
    id_student). Re-ingesting the same data updates rows in place rather than
    duplicating them, so an upload can be replaced without losing the flags
    and interventions that point at existing students.
    """
    upload_dir = Path(upload_dir)
    counts = IngestCounts()

    courses = read_courses(upload_dir / "courses.csv")
    counts.presentations_created, counts.presentations_updated = _ingest_presentations(
        courses
    )

    info = read_student_info(upload_dir / "studentInfo.csv")
    registration = read_student_registration(upload_dir / "studentRegistration.csv")
    counts.students_created, counts.students_updated = _ingest_students(
        info, registration
    )

    logger.info(
        "ingest: %d presentations (%d new), %d students (%d new)",
        counts.presentations,
        counts.presentations_created,
        counts.students,
        counts.students_created,
    )
    return counts


def _ingest_presentations(courses: pd.DataFrame) -> tuple[int, int]:
    existing = {
        (p.code_module, p.code_presentation): p
        for p in Presentation.objects.all()
    }
    created = updated = 0
    to_create: list[Presentation] = []
    to_update: list[Presentation] = []
    for row in courses.itertuples(index=False):
        key = (str(row.code_module), str(row.code_presentation))
        length = int(row.module_presentation_length)
        start = presentation_start_date(key[1])
        current = existing.get(key)
        if current is None:
            to_create.append(
                Presentation(
                    code_module=key[0],
                    code_presentation=key[1],
                    start_date=start,
                    length_days=length,
                )
            )
            created += 1
        else:
            current.start_date = start
            current.length_days = length
            to_update.append(current)
            updated += 1
    Presentation.objects.bulk_create(to_create, batch_size=500)
    Presentation.objects.bulk_update(
        to_update, ["start_date", "length_days"], batch_size=500
    )
    return created, updated


STUDENT_FIELDS = [
    "gender",
    "region",
    "highest_education",
    "imd_band",
    "age_band",
    "disability",
    "num_prev_attempts",
    "studied_credits",
    "date_registration",
    "date_unregistration",
    "final_result",
]


def _ingest_students(
    info: pd.DataFrame, registration: pd.DataFrame
) -> tuple[int, int]:
    keys = ["code_module", "code_presentation", "id_student"]
    merged = info.merge(registration, on=keys, how="left", validate="one_to_one")

    presentations = {
        (p.code_module, p.code_presentation): p.pk for p in Presentation.objects.all()
    }
    unknown = {
        (str(m), str(p))
        for m, p in merged[["code_module", "code_presentation"]]
        .drop_duplicates()
        .itertuples(index=False)
    } - set(presentations)
    if unknown:
        listed = ", ".join(f"{m}/{p}" for m, p in sorted(unknown))
        raise IngestError(
            f"studentInfo references presentations absent from courses.csv: {listed}"
        )

    existing = {
        (s.presentation_id, s.id_student): s
        for s in Student.objects.all().only("presentation_id", "id_student", *STUDENT_FIELDS)
    }

    to_create: list[Student] = []
    to_update: list[Student] = []
    for row in merged.itertuples(index=False):
        presentation_id = presentations[(str(row.code_module), str(row.code_presentation))]
        values = dict(
            gender=_to_str(row.gender) or "",
            region=_to_str(row.region) or "",
            highest_education=_to_str(row.highest_education) or "",
            imd_band=_to_str(row.imd_band),
            age_band=_to_str(row.age_band) or "",
            disability=_to_str(row.disability) == "Y",
            num_prev_attempts=int(row.num_of_prev_attempts),
            studied_credits=int(row.studied_credits),
            date_registration=_to_int(row.date_registration),
            date_unregistration=_to_int(row.date_unregistration),
            final_result=_to_str(row.final_result) or "",
        )
        key = (presentation_id, int(row.id_student))
        current = existing.get(key)
        if current is None:
            to_create.append(
                Student(
                    presentation_id=presentation_id,
                    id_student=int(row.id_student),
                    **values,
                )
            )
        else:
            for name, value in values.items():
                setattr(current, name, value)
            to_update.append(current)

    Student.objects.bulk_create(to_create, batch_size=2000)
    Student.objects.bulk_update(to_update, STUDENT_FIELDS, batch_size=2000)
    return len(to_create), len(to_update)
