from django.urls import path

from . import views

urlpatterns = [
    path("admin/retrain/", views.retrain_view, name="retrain"),
    path(
        "presentations/<int:presentation_id>/ranking/",
        views.ranking_view,
        name="ranking",
    ),
    path(
        "presentations/<int:presentation_id>/ranking/export.csv",
        views.ranking_export,
        name="ranking_export",
    ),
    path(
        "presentations/<int:presentation_id>/dashboard/",
        views.dashboard_view,
        name="dashboard",
    ),
]
