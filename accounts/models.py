"""Role and presentation assignment for Django's built-in User.

Three roles: instructor, advisor, administrator. Authorization is enforced in
accounts.authz, not with per-view decorators.
"""

from django.conf import settings
from django.db import models

ROLE_INSTRUCTOR = "instructor"
ROLE_ADVISOR = "advisor"
ROLE_ADMIN = "administrator"

ROLE_CHOICES = [
    (ROLE_INSTRUCTOR, "Instructor"),
    (ROLE_ADVISOR, "Advisor"),
    (ROLE_ADMIN, "Administrator"),
]


class Profile(models.Model):
    """Role, presentation assignments and lockout state for one user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    assignments = models.ManyToManyField(
        "cohorts.Presentation", blank=True, related_name="assigned_users"
    )
    failed_attempts = models.IntegerField(default=0)
    is_locked = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user.username} ({self.role})"

    @property
    def is_admin(self):
        return self.role == ROLE_ADMIN

    @property
    def is_advisor(self):
        return self.role == ROLE_ADVISOR

    def register_failure(self):
        """Count one failed login; lock at MAX_FAILED_LOGINS consecutive failures."""
        from django.conf import settings as dj_settings

        self.failed_attempts += 1
        if self.failed_attempts >= dj_settings.MAX_FAILED_LOGINS:
            self.is_locked = True
        self.save(update_fields=["failed_attempts", "is_locked"])
        return self.is_locked

    def register_success(self):
        """Reset the failure counter after an accepted login."""
        if self.failed_attempts:
            self.failed_attempts = 0
            self.save(update_fields=["failed_attempts"])

    def unlock(self):
        """Clear a lock. Admin-only path; the view enforces that."""
        self.is_locked = False
        self.failed_attempts = 0
        self.save(update_fields=["is_locked", "failed_attempts"])
