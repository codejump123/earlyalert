"""The one authorization service.

Every view that touches Student, WeeklyFeatures, RiskScore, Flag or
Intervention calls assert_can_view before querying. No view queries those
tables without it, and no per-view permission decorators are used instead.
"""

from django.core.exceptions import PermissionDenied

from audit.services import record
from cohorts.models import Presentation

from .models import ROLE_ADMIN


def _profile(user):
    return getattr(user, "profile", None)


def is_admin(user) -> bool:
    """True if user holds the administrator role."""
    if not getattr(user, "is_authenticated", False):
        return False
    profile = _profile(user)
    return profile is not None and profile.role == ROLE_ADMIN


def presentations_for(user):
    """Presentations this user may see.

    Administrators see all of them; instructors and advisors see only their
    assignments. An unauthenticated or profile-less user sees none.
    """
    if not getattr(user, "is_authenticated", False):
        return Presentation.objects.none()
    if is_admin(user):
        return Presentation.objects.all()
    profile = _profile(user)
    if profile is None:
        return Presentation.objects.none()
    return profile.assignments.all()


def assert_can_view(user, presentation) -> None:
    """Raise PermissionDenied unless user may see this presentation.

    The denial is written to the audit log as 'denied' before raising, so
    attempts on unassigned presentations are recorded whether or not the view
    renders anything.
    """
    presentation_id = getattr(presentation, "pk", presentation)
    if presentations_for(user).filter(pk=presentation_id).exists():
        return
    record(user if getattr(user, "is_authenticated", False) else None,
           "denied", f"presentation:{presentation_id}")
    raise PermissionDenied("You are not assigned to this presentation.")


def assert_is_admin(user) -> None:
    """Raise PermissionDenied unless user is an administrator."""
    if is_admin(user):
        return
    record(user if getattr(user, "is_authenticated", False) else None,
           "denied", "admin-area")
    raise PermissionDenied("Administrator role required.")
