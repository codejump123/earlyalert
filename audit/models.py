"""Append-only audit log.

No update or delete path exists for AuditEntry: save() refuses to rewrite an
existing row and delete() is disabled on both the instance and the queryset.
Every state-changing action writes exactly one entry.
"""

from django.conf import settings
from django.db import models

ACTIONS = [
    "login",
    "login_failed",
    "login_locked",
    "denied",
    "flag_created",
    "flag_note_added",
    "intervention_recorded",
    "outcome_recorded",
    "upload_accepted",
    "upload_rejected",
    "rebuild_started",
    "rebuild_completed",
    "retrain_started",
    "retrain_completed",
    "retrain_failed",
    "export",
]
ACTION_CHOICES = [(a, a) for a in ACTIONS]


class AppendOnly(Exception):
    """Raised on any attempt to modify or delete an audit entry."""


class AuditQuerySet(models.QuerySet):
    def delete(self):
        raise AppendOnly("AuditEntry rows cannot be deleted.")

    def update(self, **kwargs):
        raise AppendOnly("AuditEntry rows cannot be updated.")


class AuditEntry(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    role = models.CharField(max_length=20, blank=True)
    action = models.CharField(max_length=40, choices=ACTION_CHOICES)
    target = models.CharField(max_length=120, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ["-timestamp", "-id"]
        indexes = [
            models.Index(fields=["-timestamp"]),
            models.Index(fields=["action"]),
        ]
        verbose_name_plural = "audit entries"

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.action} {self.target}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise AppendOnly("AuditEntry rows cannot be modified once written.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnly("AuditEntry rows cannot be deleted.")
