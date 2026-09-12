"""Rebuilding WeeklyFeatures from the current upload (UC10).

Runs in-process under the job lock, triggered by an administrator. Refuses if
no complete non-superseded upload exists, or if the lock is held.

A refusal writes no audit entry: the SRS action list has rebuild_started and
rebuild_completed and no rebuild_failed, and a refusal changes no state.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import transaction

from audit.services import record
from cohorts.models import DataUpload, Presentation, Student
from cohorts.uploads import current_batch
from earlyalert.joblock import JobLockHeld, job_lock
from pipeline.features import build_weekly_features

from .models import WeeklyFeatures

logger = logging.getLogger("pipeline.features")

JOB_NAME = "rebuild"
# Model instances are materialized a slice at a time so a full rebuild never
# holds a million of them at once.
WRITE_BATCH = 20_000


@dataclass
class RebuildResult:
    ok: bool
    error: str | None = None
    rows: int = 0
    students: int = 0
    seconds: float = 0.0


def upload_dir_for_current_batch() -> Path | None:
    """Output: the directory of the live upload, or None if there isn't one."""
    batch = current_batch()
    if batch is None:
        return None
    path = Path(settings.UPLOAD_ROOT) / str(batch["batch_id"])
    return path if path.is_dir() else None


def rebuild(user) -> RebuildResult:
    """Input: the administrator triggering the rebuild.
    Output: RebuildResult.

    Writes rebuild_started before the work and rebuild_completed after it.
    """
    upload_dir = upload_dir_for_current_batch()
    if upload_dir is None:
        return RebuildResult(
            ok=False,
            error="no complete upload is in place; upload the seven files first",
        )

    try:
        with job_lock(JOB_NAME):
            record(user, "rebuild_started", f"dir:{upload_dir.name}")
            started = time.monotonic()
            pairs = list(
                Presentation.objects.values_list("code_module", "code_presentation")
            )
            frame = build_weekly_features(upload_dir, pairs)
            rows, students = _store(frame)
            elapsed = time.monotonic() - started
            logger.info(
                "rebuild complete: %d rows for %d students in %.1fs",
                rows,
                students,
                elapsed,
            )
            record(user, "rebuild_completed", f"rows:{rows}")
            return RebuildResult(
                ok=True, rows=rows, students=students, seconds=elapsed
            )
    except JobLockHeld as held:
        return RebuildResult(ok=False, error=str(held))


@transaction.atomic
def _store(frame) -> tuple[int, int]:
    """Replace WeeklyFeatures with the rows in frame.

    Input: the DataFrame from build_weekly_features.
    Output: (rows written, students covered).

    The old rows go first, inside the same transaction, so a rebuild never
    leaves a mix of two runs behind.
    """
    student_ids = {
        (module, presentation, id_student): pk
        for pk, module, presentation, id_student in Student.objects.values_list(
            "pk", "presentation__code_module", "presentation__code_presentation",
            "id_student",
        )
    }
    WeeklyFeatures.objects.all().delete()

    written = 0
    covered: set[int] = set()
    batches = max(1, math.ceil(len(frame) / WRITE_BATCH))
    for number in range(batches):
        slice_ = frame.iloc[number * WRITE_BATCH : (number + 1) * WRITE_BATCH]
        objects = []
        for row in slice_.itertuples(index=False):
            student_pk = student_ids.get(
                (row.code_module, row.code_presentation, int(row.id_student))
            )
            if student_pk is None:
                # Activity for a student who is not in studentInfo.csv.
                continue
            covered.add(student_pk)
            objects.append(
                WeeklyFeatures(
                    student_id=student_pk,
                    week=int(row.week),
                    total_clicks=int(row.total_clicks),
                    active_days=int(row.active_days),
                    clicks_per_active_day=float(row.clicks_per_active_day),
                    days_since_last_activity=int(row.days_since_last_activity),
                    assessments_submitted=int(row.assessments_submitted),
                    assessments_late=int(row.assessments_late),
                    mean_score=_float_or_none(row.mean_score),
                    weighted_score_to_date=_float_or_none(row.weighted_score_to_date),
                    activity_type_shares=row.activity_type_shares or {},
                )
            )
        WeeklyFeatures.objects.bulk_create(objects, batch_size=WRITE_BATCH)
        written += len(objects)
        logger.info("stored %d / %d rows", written, len(frame))
    return written, len(covered)


def _float_or_none(value):
    if value is None:
        return None
    value = float(value)
    return None if value != value else value
