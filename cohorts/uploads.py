"""Accepting an upload of the seven OULAD files (UC09).

Nothing is stored unless all seven files pass every check: the files are
streamed to a staging directory, validated there, and only then moved into
place and recorded. A rejected upload leaves no DataUpload row, no file on
disk, and no change to Presentation or Student.

Replacing an upload marks the previous batch superseded. Nothing is deleted.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import transaction

from audit.services import record
from pipeline.validate import REQUIRED_FILES, validate_upload

from .ingest import IngestCounts, IngestError, ingest_batch
from .models import DataUpload

logger = logging.getLogger("earlyalert")

# Canonical name keyed by lowercase, so a file that arrived as
# "studentvle.csv" from a case-folding filesystem is still recognized.
_CANONICAL = {name.lower(): name for name in REQUIRED_FILES}

# Streamed to disk in this size; an uploaded file is never held in memory.
_WRITE_CHUNK = 4 * 1024 * 1024


@dataclass
class UploadResult:
    ok: bool
    error: str | None = None
    batch_id: uuid.UUID | None = None
    counts: IngestCounts | None = None
    superseded: int = 0


def canonical_name(filename: str) -> str | None:
    """Input: an uploaded file's name. Output: the canonical OULAD filename,
    or None if it is not one of the seven."""
    return _CANONICAL.get(Path(filename).name.lower())


def accept_upload(uploaded_files, user) -> UploadResult:
    """Input: the uploaded file objects and the administrator uploading them.
    Output: UploadResult.

    Writes exactly one audit entry: upload_accepted or upload_rejected. The
    audit write sits outside the database transaction that stores the batch,
    so a rejection is still recorded when the transaction rolls back.
    """
    staging = Path(settings.UPLOAD_ROOT) / "staging" / uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=True)
    try:
        paths: dict[str, Path] = {}
        for uploaded in uploaded_files:
            name = canonical_name(uploaded.name) or Path(uploaded.name).name
            destination = staging / name
            with destination.open("wb") as handle:
                for chunk in uploaded.chunks(_WRITE_CHUNK):
                    handle.write(chunk)
            paths[name] = destination

        result = validate_upload(paths)
        if not result.ok:
            return _reject(user, result.error or "validation failed")

        batch_id = uuid.uuid4()
        final_dir = Path(settings.UPLOAD_ROOT) / str(batch_id)
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(final_dir))

        try:
            superseded, counts = _store_batch(batch_id, final_dir, result, user)
        except IngestError as error:
            # The transaction rolled back, so the batch was never stored.
            # Drop the files with it: they describe no row in the database.
            shutil.rmtree(final_dir, ignore_errors=True)
            return _reject(user, str(error))

        record(user, "upload_accepted", f"batch:{batch_id}")
        logger.info("upload accepted: batch %s", batch_id)
        return UploadResult(
            ok=True, batch_id=batch_id, counts=counts, superseded=superseded
        )
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _reject(user, error: str) -> UploadResult:
    record(user, "upload_rejected", error)
    logger.info("upload rejected: %s", error)
    return UploadResult(ok=False, error=error)


@transaction.atomic
def _store_batch(batch_id, final_dir: Path, result, user):
    """Record the seven files as one batch and ingest them, all or nothing."""
    superseded = DataUpload.objects.filter(superseded=False).update(superseded=True)
    DataUpload.objects.bulk_create(
        [
            DataUpload(
                filename=stats.filename,
                checksum=stats.checksum,
                row_count=stats.row_count,
                uploaded_by=user,
                batch_id=batch_id,
                superseded=False,
            )
            for stats in result.files.values()
        ]
    )
    return superseded, ingest_batch(final_dir)


def current_batch() -> dict | None:
    """Output: {batch_id, uploaded_at, files, rows} for the live upload, or
    None if no complete non-superseded batch exists."""
    rows = list(DataUpload.objects.filter(superseded=False))
    if len(rows) != len(REQUIRED_FILES):
        return None
    return {
        "batch_id": rows[0].batch_id,
        "uploaded_at": max(r.uploaded_at for r in rows),
        "files": sorted(rows, key=lambda r: r.filename),
        "rows": sum(r.row_count for r in rows),
    }
