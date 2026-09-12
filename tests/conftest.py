"""Shared fixtures for the test suite.

Everything here is synthetic. No real OULAD rows appear in any test.
"""

import datetime as dt

import pytest
from django.contrib.auth import get_user_model

from accounts.models import ROLE_ADMIN, ROLE_ADVISOR, ROLE_INSTRUCTOR, Profile
from cohorts.models import Presentation

PASSWORD = "correct-horse-battery"


def _make_user(username, role, presentations=()):
    user = get_user_model().objects.create_user(username=username, password=PASSWORD)
    profile = Profile.objects.create(user=user, role=role)
    if presentations:
        profile.assignments.set(presentations)
    return user


@pytest.fixture
def password():
    return PASSWORD


@pytest.fixture
def presentation_a(db):
    return Presentation.objects.create(
        code_module="AAA",
        code_presentation="2013J",
        start_date=dt.date(2013, 10, 7),
        length_days=268,
    )


@pytest.fixture
def presentation_b(db):
    return Presentation.objects.create(
        code_module="BBB",
        code_presentation="2014J",
        start_date=dt.date(2014, 10, 6),
        length_days=262,
    )


@pytest.fixture
def advisor_a(db, presentation_a):
    """Advisor assigned to presentation A only."""
    return _make_user("advisor_a", ROLE_ADVISOR, [presentation_a])


@pytest.fixture
def instructor_a(db, presentation_a):
    return _make_user("instructor_a", ROLE_INSTRUCTOR, [presentation_a])


@pytest.fixture
def administrator(db):
    """Administrators see every presentation without an explicit assignment."""
    return _make_user("admin_user", ROLE_ADMIN)
