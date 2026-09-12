"""Root URL configuration. Endpoint table is in CLAUDE.md / SRS."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("", include("cohorts.urls")),
    path("", include("features.urls")),
    path("", include("scoring.urls")),
]
