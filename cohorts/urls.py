from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="presentation_selector"), name="home"),
    path("presentations/", views.presentation_selector, name="presentation_selector"),
]
