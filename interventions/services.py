"""The intervention workflow rules (UC05, UC06, UC07).

Every rule the SRS lists for this workflow is enforced here rather than in a
view or a template, so the same check applies however the request arrives.
Each successful action writes exactly one audit entry.
"""

from __future__ import annotations

import datetime as dt

from django.db import transaction
from django.utils import timezone

from audit.services import record
from cohorts.detail import flag_control_for

from .models import (
    INTERVENTION_TYPE_CHOICES,
    OUTCOME_CHOICES,
    OUTCOME_TO_STATUS,
    Flag,
    Intervention,
    InterventionOutcome,
)

INTERVENTION_TYPES = {value for value, _ in INTERVENTION_TYPE_CHOICES}
OUTCOMES = {value for value, _ in OUTCOME_CHOICES}


class WorkflowError(Exception):
    """A rule was broken. The message is shown to the advisor as written."""


@transaction.atomic
def create_flag(user, student, reason: str = "") -> Flag:
    """Input: the advisor, the student registration, an optional reason.
    Output: the new Flag.

    Re-checks the same control rules the detail view used to decide whether to
    show the control, because a hidden control is not an enforced one.
    """
    control = flag_control_for(user, student)
    if not control.visible:
        raise WorkflowError(control.reason or "you cannot raise a flag here")
    if control.disabled:
        raise WorkflowError(control.reason or "this student has unregistered")

    flag = Flag.objects.create(student=student, raised_by=user, reason=reason.strip())
    record(user, "flag_created", f"flag:{flag.pk}:student:{student.id_student}")
    return flag


@transaction.atomic
def add_note(user, flag: Flag, note: str) -> Flag:
    """Append a note to an open flag's reason.

    The SRS lists flag_note_added as an audit action but gives it no endpoint
    of its own, so the note is posted to the flag's own workflow page. Notes
    are appended and dated; nothing already written is overwritten.
    """
    note = note.strip()
    if not note:
        raise WorkflowError("a note cannot be empty")
    if not flag.is_open:
        raise WorkflowError("this flag is closed; notes can only be added while open")

    stamp = timezone.now().strftime("%Y-%m-%d %H:%M")
    entry = f"[{stamp} {user.username}] {note}"
    flag.reason = f"{flag.reason}\n{entry}".strip() if flag.reason else entry
    flag.save(update_fields=["reason"])
    record(user, "flag_note_added", f"flag:{flag.pk}")
    return flag


@transaction.atomic
def record_intervention(
    user, flag: Flag, intervention_type: str, date: dt.date, notes: str = ""
) -> Intervention:
    """Input: the advisor, an open flag, the contact type, the date it
    happened, and any notes. Output: the new Intervention.

    The date must be on or after the day the flag was raised and not in the
    future: an intervention recorded before its own flag, or tomorrow, is a
    data-entry error rather than a record of anything.
    """
    if not flag.is_open:
        raise WorkflowError(
            "this flag is closed; reopen the case by raising a new flag"
        )
    if intervention_type not in INTERVENTION_TYPES:
        raise WorkflowError(f"unknown intervention type {intervention_type!r}")
    if date is None:
        raise WorkflowError("an intervention needs a date")

    today = timezone.localdate()
    raised_on = timezone.localtime(flag.created_at).date()
    if date > today:
        raise WorkflowError(f"the date cannot be in the future (today is {today})")
    if date < raised_on:
        raise WorkflowError(
            f"the date cannot be before the flag was raised on {raised_on}"
        )

    intervention = Intervention.objects.create(
        flag=flag,
        advisor=user,
        intervention_type=intervention_type,
        date=date,
        notes=notes.strip(),
    )
    record(
        user,
        "intervention_recorded",
        f"intervention:{intervention.pk}:flag:{flag.pk}",
    )
    return intervention


@transaction.atomic
def record_outcome(user, intervention: Intervention, outcome: str) -> InterventionOutcome:
    """Input: the advisor, the intervention, and what came of it.
    Output: the new InterventionOutcome.

    Recording an outcome closes the flag with the matching status, so an
    outcome is the end of the case rather than a note on it.
    """
    if outcome not in OUTCOMES:
        raise WorkflowError(f"unknown outcome {outcome!r}")
    if hasattr(intervention, "outcome"):
        raise WorkflowError("this intervention already has an outcome recorded")

    flag = intervention.flag
    if not flag.is_open:
        raise WorkflowError("this flag is already closed")

    recorded = InterventionOutcome.objects.create(
        intervention=intervention, outcome=outcome
    )
    flag.status = OUTCOME_TO_STATUS[outcome]
    flag.closed_at = timezone.now()
    flag.save(update_fields=["status", "closed_at"])

    record(
        user,
        "outcome_recorded",
        f"outcome:{recorded.pk}:flag:{flag.pk}:{flag.status}",
    )
    return recorded


def parse_date(value: str) -> dt.date | None:
    """Input: a date as typed. Output: the date, or None if unreadable."""
    try:
        return dt.date.fromisoformat((value or "").strip())
    except ValueError:
        return None
