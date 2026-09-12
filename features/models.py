"""Weekly per-student engagement and assessment features.

Week w covers days 7(w-1) to 7w-1 relative to presentation start (day 0).
Weeks <= 0 hold pre-start activity and are kept.
"""

from django.db import models


class WeeklyFeatures(models.Model):
    student = models.ForeignKey(
        "cohorts.Student", on_delete=models.CASCADE, related_name="weekly_features"
    )
    week = models.IntegerField()
    total_clicks = models.IntegerField(default=0)
    active_days = models.IntegerField(default=0)
    clicks_per_active_day = models.FloatField(default=0.0)
    days_since_last_activity = models.IntegerField(default=0)
    assessments_submitted = models.IntegerField(default=0)
    assessments_late = models.IntegerField(default=0)
    mean_score = models.FloatField(null=True, blank=True)
    weighted_score_to_date = models.FloatField(null=True, blank=True)
    # Click share per VLE activity_type for this week, {activity_type: share}.
    activity_type_shares = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "week"], name="uniq_weekly_features"
            )
        ]
        indexes = [models.Index(fields=["student", "week"])]
        ordering = ["student", "week"]
        verbose_name_plural = "weekly features"

    def __str__(self):
        return f"{self.student_id} week {self.week}"
