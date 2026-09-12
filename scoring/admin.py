from django.contrib import admin

from .models import ModelVersion, RiskScore


@admin.register(ModelVersion)
class ModelVersionAdmin(admin.ModelAdmin):
    list_display = (
        "trained_at", "feature_set", "classifier", "horizon_week",
        "auc_roc", "auc_pr", "brier", "is_current",
    )
    list_filter = ("horizon_week", "feature_set", "classifier", "is_current")


@admin.register(RiskScore)
class RiskScoreAdmin(admin.ModelAdmin):
    list_display = ("student", "model_version", "probability", "scored_at")
    list_filter = ("model_version",)
