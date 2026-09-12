"""Flag -> Intervention -> InterventionOutcome workflow.

A flag is raised by an advisor on one student registration, worked through one
or more interventions, and closed by recording an outcome.
"""

from django.conf import settings
from django.db import models

STATUS_OPEN = "open"
STATUS_RESOLVED = "closed_resolved"
STATUS_NO_RESPONSE = "closed_no_response"
STATUS_WITHDRAWN = "closed_withdrawn"
STATUS_REFERRED = "closed_referred"

FLAG_STATUS_CHOICES = [
    (STATUS_OPEN, "Open"),
    (STATUS_RESOLVED, "Closed - resolved"),
    (STATUS_NO_RESPONSE, "Closed - no response"),
    (STATUS_WITHDRAWN, "Closed - withdrew"),
    (STATUS_REFERRED, "Closed - referred"),
]

INTERVENTION_TYPE_CHOICES = [
    ("email", "Email"),
    ("phone", "Phone"),
    ("meeting", "Meeting"),
    ("referral", "Referral"),
]

OUTCOME_CHOICES = [
    ("re_engaged", "Re-engaged"),
    ("responded_withdrew", "Responded, withdrew"),
    ("no_response", "No response"),
    ("referred", "Referred"),
]

# Recording an outcome closes the flag with the matching status.
OUTCOME_TO_STATUS = {
    "re_engaged": STATUS_RESOLVED,
    "responded_withdrew": STATUS_WITHDRAWN,
    "no_response": STATUS_NO_RESPONSE,
    "referred": STATUS_REFERRED,
}


class Flag(models.Model):
    student = models.ForeignKey(
        "cohorts.Student", on_delete=models.CASCADE, related_name="flags"
    )
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="flags_raised"
    )
    reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=24, choices=FLAG_STATUS_CHOICES, default=STATUS_OPEN
    )
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["student", "status"])]

    def __str__(self):
        return f"flag {self.pk} on {self.student_id} ({self.status})"

    @property
    def is_open(self):
        return self.status == STATUS_OPEN


class Intervention(models.Model):
    flag = models.ForeignKey(
        Flag, on_delete=models.CASCADE, related_name="interventions"
    )
    advisor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="interventions"
    )
    intervention_type = models.CharField(
        max_length=20, choices=INTERVENTION_TYPE_CHOICES
    )
    date = models.DateField()
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.intervention_type} on {self.date} (flag {self.flag_id})"


class InterventionOutcome(models.Model):
    intervention = models.OneToOneField(
        Intervention, on_delete=models.CASCADE, related_name="outcome"
    )
    outcome = models.CharField(max_length=24, choices=OUTCOME_CHOICES)
    recorded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.outcome} (intervention {self.intervention_id})"
