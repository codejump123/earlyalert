from django.contrib import admin

from .models import Flag, Intervention, InterventionOutcome


@admin.register(Flag)
class FlagAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "raised_by", "status", "created_at", "closed_at")
    list_filter = ("status",)


@admin.register(Intervention)
class InterventionAdmin(admin.ModelAdmin):
    list_display = ("id", "flag", "advisor", "intervention_type", "date")
    list_filter = ("intervention_type",)


@admin.register(InterventionOutcome)
class InterventionOutcomeAdmin(admin.ModelAdmin):
    list_display = ("id", "intervention", "outcome", "recorded_at")
    list_filter = ("outcome",)
