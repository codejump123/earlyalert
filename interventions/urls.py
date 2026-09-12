from django.urls import path

from . import views

urlpatterns = [
    path(
        "students/<int:student_id>/flag/",
        views.create_flag_view,
        name="create_flag",
    ),
    path(
        "flags/<int:flag_id>/intervention/",
        views.record_intervention_view,
        name="record_intervention",
    ),
    path(
        "interventions/<int:intervention_id>/outcome/",
        views.record_outcome_view,
        name="record_outcome",
    ),
]
