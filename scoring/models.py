"""Trained model versions and the stored risk scores they produced.

Ranking reads stored RiskScore rows; nothing is scored at request time.
"""

from django.db import models

FEATURE_SETS = [
    ("D", "Demographic"),
    ("A", "Assessment"),
    ("E", "Engagement"),
    ("All", "All"),
]
CLASSIFIERS = [
    ("majority", "Majority baseline"),
    ("logreg", "Logistic regression"),
    ("rf", "Random forest"),
    ("hgb", "Histogram gradient boosting"),
]


class ModelVersion(models.Model):
    trained_at = models.DateTimeField(auto_now_add=True)
    horizon_week = models.IntegerField()
    feature_set = models.CharField(max_length=20, choices=FEATURE_SETS)
    classifier = models.CharField(max_length=40, choices=CLASSIFIERS)
    auc_roc = models.FloatField()
    auc_pr = models.FloatField()
    brier = models.FloatField()
    recall_at_p50 = models.FloatField()
    # {dimension: {level: {auc_roc, recall_at_p50, n} | {suppressed, n}}}
    subgroup_metrics = models.JSONField(default=dict, blank=True)
    artifact_path = models.CharField(max_length=255, blank=True)
    training_rows = models.IntegerField()
    test_rows = models.IntegerField()
    is_current = models.BooleanField(default=False)

    class Meta:
        ordering = ["-trained_at"]
        indexes = [models.Index(fields=["horizon_week", "is_current"])]

    def __str__(self):
        return f"{self.feature_set}/{self.classifier}@w{self.horizon_week}"


class RiskScore(models.Model):
    student = models.ForeignKey(
        "cohorts.Student", on_delete=models.CASCADE, related_name="risk_scores"
    )
    model_version = models.ForeignKey(
        ModelVersion, on_delete=models.CASCADE, related_name="risk_scores"
    )
    probability = models.FloatField()
    # Exactly three {feature, value, direction, text} dicts.
    top_features = models.JSONField(default=list, blank=True)
    scored_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "model_version"], name="uniq_risk_score"
            )
        ]
        indexes = [models.Index(fields=["model_version", "-probability"])]
        ordering = ["-probability"]

    def __str__(self):
        return f"{self.student_id}: {self.probability:.3f}"
