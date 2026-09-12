from django.urls import path

from . import views

urlpatterns = [
    path("admin/audit/", views.audit_view, name="audit"),
]
