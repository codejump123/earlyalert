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


@pytest.fixture
def trainable_cohort(db, settings, tmp_path):
    """A synthetic cohort large enough to clear the retrain thresholds.

    Built through the ORM rather than from CSV, because the 50-student fixture
    is deliberately below the 500-row / 50-positive floor that TC16 tests.

    700 registrations per year across two presentations. A fifth withdraw, and
    withdrawing students click less and go quiet earlier, so the label is
    learnable rather than noise. Withdrawals unregister on day 120 or later,
    past every horizon, so the leakage filter keeps them in the cohort.
    """
    import datetime as dt

    from cohorts.models import Presentation, Student
    from features.models import WeeklyFeatures

    settings.BASE_DIR = tmp_path
    settings.UPLOAD_ROOT = tmp_path / "uploads"
    settings.ARTIFACT_ROOT = tmp_path / "artifacts"

    per_year = 700
    presentations = {}
    for year, code in ((2013, "2013J"), (2014, "2014J")):
        presentations[year] = Presentation.objects.create(
            code_module="AAA",
            code_presentation=code,
            start_date=dt.date(year, 10, 1),
            length_days=268,
        )

    bands = ["0-10%", "20-30%", "40-50%", "60-70%", "90-100%"]
    students = []
    for year, presentation in presentations.items():
        for index in range(per_year):
            withdraws = index % 5 == 0
            students.append(
                Student(
                    presentation=presentation,
                    id_student=year * 10000 + index,
                    gender="M" if index % 2 else "F",
                    region="Scotland" if index % 3 else "London Region",
                    highest_education="A Level or Equivalent",
                    imd_band=bands[index % len(bands)],
                    age_band="0-35" if index % 2 else "35-55",
                    disability=index % 7 == 0,
                    num_prev_attempts=index % 3,
                    studied_credits=60 + 30 * (index % 3),
                    date_registration=-30 + (index % 20),
                    date_unregistration=120 + index % 40 if withdraws else None,
                    final_result="Withdrawn" if withdraws else "Pass",
                )
            )
    Student.objects.bulk_create(students, batch_size=1000)

    weekly = []
    for student in Student.objects.all():
        withdraws = student.final_result == "Withdrawn"
        base = 4 if withdraws else 30
        quiet_from = 5 if withdraws else 99
        for week in range(1, 13):
            clicks = 0 if week >= quiet_from else base + (student.id_student % 7)
            active = 0 if clicks == 0 else 1 + (student.id_student % 3)
            weekly.append(
                WeeklyFeatures(
                    student=student,
                    week=week,
                    total_clicks=clicks,
                    active_days=active,
                    clicks_per_active_day=(clicks / active) if active else 0.0,
                    days_since_last_activity=(
                        7 * (week - quiet_from) if week >= quiet_from else 1
                    ),
                    assessments_submitted=0 if withdraws else week // 4,
                    assessments_late=1 if withdraws and week > 5 else 0,
                    mean_score=None if week < 5 else (20.0 if withdraws else 70.0),
                    weighted_score_to_date=(
                        None if week < 5 else (20.0 if withdraws else 70.0)
                    ),
                    activity_type_shares={"forumng": 1.0} if clicks else {},
                )
            )
    WeeklyFeatures.objects.bulk_create(weekly, batch_size=5000)
    return presentations
