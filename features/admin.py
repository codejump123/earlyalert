from django.contrib import admin

from .models import WeeklyFeatures


@admin.register(WeeklyFeatures)
class WeeklyFeaturesAdmin(admin.ModelAdmin):
    list_display = ("student", "week", "total_clicks", "active_days", "mean_score")
    list_filter = ("week",)
