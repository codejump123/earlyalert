"""One-at-a-time lock for the long in-process jobs.

Rebuild and retrain run in the administrator's request, on one machine, with
no broker and no worker. This is what keeps two of them from running at once
and what gives the admin views something to show while one is running.

The lock is a file created with O_CREAT|O_EXCL, so acquiring it is atomic
without a database round trip and it survives a process that dies holding it:
a lock whose recorded pid is gone is stale and is broken by the next caller.

Progress goes to a log file beside the lock. The job writes to it through the
'pipeline' logger, so pipeline code needs no knowledge of any of this, and the
admin view reads the same file whether the job is still running or finished.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

# Lines of progress the admin views show.
JOB_LOG_LINES = 200

LOG_FORMAT = "%(asctime)s %(message)s"
LOG_DATEFMT = "%H:%M:%S"


class JobLockHeld(Exception):
    """Raised when another job already holds the lock."""

    def __init__(self, holder: "JobStatus"):
        self.holder = holder
        super().__init__(
            f"{holder.name} is already running (started {holder.started_at:%H:%M:%S})."
        )


@dataclass(frozen=True)
class JobStatus:
    name: str
    pid: int
    started_at: datetime
    running: bool


def jobs_dir() -> Path:
    path = Path(settings.BASE_DIR) / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _lock_path(name: str) -> Path:
    return jobs_dir() / f"{name}.lock"


def log_path(name: str) -> Path:
    return jobs_dir() / f"{name}.log"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_lock(name: str) -> JobStatus | None:
    path = _lock_path(name)
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None
    return JobStatus(
        name=payload.get("name", name),
        pid=int(payload.get("pid", -1)),
        started_at=datetime.fromisoformat(payload["started_at"]),
        running=_pid_alive(int(payload.get("pid", -1))),
    )


def current_job(name: str) -> JobStatus | None:
    """Output: the live holder of this lock, or None.

    A lock left behind by a dead process is not a holder; it is removed.
    """
    status = _read_lock(name)
    if status is None:
        return None
    if status.running:
        return status
    _lock_path(name).unlink(missing_ok=True)
    return None


def read_progress(name: str, max_lines: int = JOB_LOG_LINES) -> list[str]:
    """Output: the last lines of this job's progress log, oldest first."""
    try:
        lines = log_path(name).read_text().splitlines()
    except FileNotFoundError:
        return []
    return lines[-max_lines:]


@contextmanager
def job_lock(name: str):
    """Hold the named lock for the duration of the block.

    Raises JobLockHeld if another live process holds it. Truncates and
    attaches the progress log, so everything the 'pipeline' logger emits
    inside the block is captured for the admin view.
    """
    holder = current_job(name)
    if holder is not None:
        raise JobLockHeld(holder)

    path = _lock_path(name)
    payload = json.dumps(
        {
            "name": name,
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # Someone acquired it between the check and here.
        raise JobLockHeld(_read_lock(name) or JobStatus(name, -1, datetime.now(timezone.utc), True))
    with os.fdopen(descriptor, "w") as handle:
        handle.write(payload)

    progress = log_path(name)
    progress.write_text("")
    handler = logging.FileHandler(progress, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
    pipeline_logger = logging.getLogger("pipeline")
    pipeline_logger.addHandler(handler)
    try:
        yield progress
    finally:
        pipeline_logger.removeHandler(handler)
        handler.close()
        path.unlink(missing_ok=True)
