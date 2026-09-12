"""What the student detail view is allowed to show (UC04).

Two rules live here because both are easy to get wrong in a template:

  - A student with no WeeklyFeatures has no engagement data and therefore no
    probability. The page says so; it does not show a zero.
  - The flag control appears only for an advisor, only when no flag is open,
    and only when no flag on this registration was closed as resolved. A flag
    closed as closed_no_response does not block a new one — a student who did
    not answer last time may answer this time.
"""

from __future__ import annotations

from dataclasses import dataclass

from accounts.models import ROLE_ADVISOR
from interventions.models import STATUS_OPEN, STATUS_RESOLVED, Flag


@dataclass
class FlagControl:
    """Whether the flag control shows, and if not, why not.

    open_flag is set when a flag is already open on this student: UC05 offers a
    note on that flag in place of a second flag, so the detail view needs the
    flag itself, not just the fact of it.
    """

    visible: bool
    disabled: bool = False
    reason: str = ""
    open_flag: object = None


def flag_control_for(user, student) -> FlagControl:
    """Input: the viewer and the student registration.
    Output: a FlagControl.

    Ordering matters: an advisor looking at a student who has already
    unregistered sees a disabled control with the withdrawal day, not a hidden
    one, because the absence of a control is indistinguishable from a bug.
    """
    role = getattr(getattr(user, "profile", None), "role", None)
    if role != ROLE_ADVISOR:
        return FlagControl(visible=False, reason="only an advisor can raise a flag")

    flags = Flag.objects.filter(student=student)
    open_flag = flags.filter(status=STATUS_OPEN).first()
    if open_flag is not None:
        return FlagControl(
            visible=False,
            reason="a flag is already open for this student",
            open_flag=open_flag,
        )
    if flags.filter(status=STATUS_RESOLVED).exists():
        return FlagControl(
            visible=False,
            reason="a flag for this student was already closed as resolved",
        )

    if student.date_unregistration is not None:
        return FlagControl(
            visible=True,
            disabled=True,
            reason=(
                f"Student withdrew on day {student.date_unregistration}; "
                "outreach not available."
            ),
        )
    return FlagControl(visible=True)
