from django.urls import path

from . import views

urlpatterns = [
    path("admin/retrain/", views.retrain_view, name="retrain"),
]
