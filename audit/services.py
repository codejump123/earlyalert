"""Single write path for the audit log.

Every state-changing action calls record() exactly once. Nothing else creates
AuditEntry rows.
"""

from .models import AuditEntry


def record(user, action, target="", role=None):
    """Append one audit entry.

    user may be None (an anonymous or unknown-username login attempt). role is
    taken from the user's profile when not given explicitly, so the log keeps
    the role held at the time of the action.
    """
    if role is None:
        role = ""
        if user is not None and getattr(user, "is_authenticated", False):
            profile = getattr(user, "profile", None)
            role = profile.role if profile is not None else ""
    if user is not None and not getattr(user, "is_authenticated", False):
        user = None
    return AuditEntry.objects.create(
        user=user, role=role, action=action, target=str(target)[:120]
    )
