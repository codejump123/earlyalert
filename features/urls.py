from django.urls import path

from . import views

urlpatterns = [
    path("admin/rebuild/", views.rebuild_view, name="rebuild"),
]
