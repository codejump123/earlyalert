"""Presentation, Student, DataUpload.

One row per registration is the grain everywhere: a student may appear in
several presentations under the same OULAD id_student.

No identifying fields exist on Student and none may be added: OULAD's integer
id_student is the only student identifier in the schema.
"""

import uuid

from django.conf import settings
from django.db import models

# OULAD final_result values.
FINAL_RESULT_CHOICES = [
    ("Pass", "Pass"),
    ("Fail", "Fail"),
    ("Withdrawn", "Withdrawn"),
    ("Distinction", "Distinction"),
]


class Presentation(models.Model):
    """One module presentation, e.g. AAA/2013J."""

    code_module = models.CharField(max_length=3)
    code_presentation = models.CharField(max_length=5)
    start_date = models.DateField()
    length_days = models.IntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["code_module", "code_presentation"],
                name="uniq_presentation",
            )
        ]
        ordering = ["code_module", "code_presentation"]

    def __str__(self):
        return f"{self.code_module}/{self.code_presentation}"

    @property
    def year(self):
        """Presentation year as an int, e.g. 2013 for '2013J'."""
        return int(self.code_presentation[:4])


class Student(models.Model):
    """One registration of one student on one presentation."""

    presentation = models.ForeignKey(
        Presentation, on_delete=models.CASCADE, related_name="students"
    )
    id_student = models.IntegerField()
    gender = models.CharField(max_length=1)
    region = models.CharField(max_length=40)
    highest_education = models.CharField(max_length=40)
    imd_band = models.CharField(max_length=10, null=True, blank=True)
    age_band = models.CharField(max_length=10)
    disability = models.BooleanField()
    num_prev_attempts = models.IntegerField()
    studied_credits = models.IntegerField()
    date_registration = models.IntegerField(null=True, blank=True)
    date_unregistration = models.IntegerField(null=True, blank=True)
    final_result = models.CharField(max_length=12, choices=FINAL_RESULT_CHOICES)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["presentation", "id_student"], name="uniq_student_registration"
            )
        ]
        indexes = [models.Index(fields=["presentation", "id_student"])]
        ordering = ["presentation", "id_student"]

    def __str__(self):
        return f"{self.id_student} @ {self.presentation}"

    @property
    def is_withdrawn(self):
        return self.final_result == "Withdrawn"

    @property
    def unregistered(self):
        return self.date_unregistration is not None


class DataUpload(models.Model):
    """One accepted file within an upload batch.

    A batch is the seven OULAD files uploaded together; nothing is stored
    unless all seven pass every check. Replacing a batch marks the old one
    superseded, never deletes it.
    """

    filename = models.CharField(max_length=60)
    checksum = models.CharField(max_length=64)
    row_count = models.IntegerField()
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploads"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    batch_id = models.UUIDField(default=uuid.uuid4)
    superseded = models.BooleanField(default=False)

    class Meta:
        ordering = ["-uploaded_at", "filename"]
        indexes = [models.Index(fields=["batch_id", "superseded"])]

    def __str__(self):
        return f"{self.filename} ({self.batch_id})"
