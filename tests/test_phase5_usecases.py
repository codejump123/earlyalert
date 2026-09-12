"""Phase 5 test cases: TC4, TC6, TC10, TC18.

The SRS holds the authoritative wording. The intent assumed here:

  TC4   The ranking lists scored students most at risk first, 50 to a page, at
        the chosen horizon, reading stored scores only.
  TC6   The student detail view shows demographics, engagement and the current
        risk with its reasons, and says so plainly when there is no engagement
        data rather than showing a zero.
  TC10  The dashboard breaks risk down by a demographic dimension, suppresses
        any group under 20, suppresses the whole table when every group is
        under 20, and shows no aggregate at all below 10 scored students.
  TC18  The CSV export covers the whole ranking rather than the page on
        screen, and is recorded in the audit log.

If the SRS says otherwise, the SRS wins and these tests get corrected.
"""

import csv
import datetime as dt
import io

import pytest

from accounts.models import ROLE_ADVISOR, ROLE_INSTRUCTOR, Profile
from audit.models import AuditEntry
from cohorts.models import Presentation, Student
from features.models import WeeklyFeatures
from interventions.models import (
    STATUS_NO_RESPONSE,
    STATUS_OPEN,
    STATUS_RESOLVED,
    Flag,
)
from scoring.models import ModelVersion, RiskScore


@pytest.fixture
def scored(db, advisor_a, presentation_a):
    """A presentation with 120 scored students, so pagination has to work.

    Risk is set to index/1000 so the expected order is exactly reversed from
    the student ids, and no two students tie.
    """
    model = ModelVersion.objects.create(
        horizon_week=8,
        feature_set="All",
        classifier="logreg",
        auc_roc=0.70,
        auc_pr=0.31,
        brier=0.21,
        recall_at_p50=0.04,
        training_rows=1000,
        test_rows=500,
        is_current=True,
        subgroup_metrics={
            "imd_band": {
                "min_n": 20,
                "gap": 0.05,
                "levels": {
                    "0-10%": {"n": 60, "auc_roc": 0.68, "recall_at_p50": 0.1,
                              "positive_rate": 0.2, "positives": 12},
                    "90-100%": {"suppressed": True, "n": 4},
                },
            },
            "disability": {"min_n": 20, "gap": None, "levels": {}},
            "age_band": {"min_n": 20, "gap": None, "levels": {}},
            "highest_education": {"min_n": 20, "gap": None, "levels": {}},
            "gender": {"min_n": 20, "gap": None, "levels": {}},
        },
    )
    students = [
        Student(
            presentation=presentation_a,
            id_student=2000 + index,
            gender="M" if index % 2 else "F",
            region="Scotland",
            highest_education="A Level or Equivalent",
            # 100 students in one band, 20 in another, 0 in a third.
            imd_band="0-10%" if index < 100 else "90-100%",
            age_band="0-35",
            disability=index % 10 == 0,
            num_prev_attempts=0,
            studied_credits=60,
            date_registration=-20,
            date_unregistration=100 if index == 0 else None,
            final_result="Withdrawn" if index < 20 else "Pass",
        )
        for index in range(120)
    ]
    Student.objects.bulk_create(students)
    students = list(Student.objects.filter(presentation=presentation_a).order_by("id_student"))

    WeeklyFeatures.objects.bulk_create(
        [
            WeeklyFeatures(
                student=student,
                week=week,
                total_clicks=10 * week,
                active_days=2,
                clicks_per_active_day=5.0,
                days_since_last_activity=3,
                assessments_submitted=1,
                assessments_late=0,
                mean_score=65.0,
                weighted_score_to_date=65.0,
                activity_type_shares={"forumng": 1.0},
            )
            # Index 60 is left with no weekly rows at all, so the "no
            # engagement data" case is a mid-ranked student rather than the
            # top-ranked one every other test reaches for.
            for index, student in enumerate(students)
            if index != 60
            for week in range(1, 9)
        ]
    )
    RiskScore.objects.bulk_create(
        [
            RiskScore(
                student=student,
                model_version=model,
                probability=index / 1000,
                top_features=[
                    {"feature": "e__days_since_last_activity", "value": 11,
                     "direction": "raises", "text": "no VLE activity for 11 days"},
                    {"feature": "a__mean_score", "value": 20,
                     "direction": "raises", "text": "mean assessment score 20"},
                    {"feature": "d__num_of_prev_attempts", "value": 0,
                     "direction": "lowers", "text": "first attempt at this module"},
                ],
            )
            for index, student in enumerate(students)
        ]
    )
    return {"model": model, "students": students, "presentation": presentation_a}


@pytest.fixture
def advisor_client(client, advisor_a, password):
    client.login(username=advisor_a.username, password=password)
    return client


# --- TC4: the ranking ----------------------------------------------------

def test_tc4_ranking_orders_most_at_risk_first(advisor_client, scored):
    response = advisor_client.get(f"/presentations/{scored['presentation'].pk}/ranking/")
    assert response.status_code == 200
    rows = response.context["rows"]
    probabilities = [row["score"].probability for row in rows]
    assert probabilities == sorted(probabilities, reverse=True)
    assert probabilities[0] == pytest.approx(0.119)


def test_tc4_pagination_is_fifty_rows(advisor_client, scored, settings):
    assert settings.RANKING_PAGE_SIZE == 50
    url = f"/presentations/{scored['presentation'].pk}/ranking/"
    first = advisor_client.get(url)
    assert len(first.context["rows"]) == 50
    assert first.context["total"] == 120
    assert first.context["page"].paginator.num_pages == 3

    last = advisor_client.get(url, {"page": 3})
    assert len(last.context["rows"]) == 20
    # No student appears on two pages.
    seen = {row["student"].pk for row in first.context["rows"]}
    assert seen.isdisjoint({row["student"].pk for row in last.context["rows"]})


def test_tc4_default_horizon_is_week_8(advisor_client, scored):
    response = advisor_client.get(f"/presentations/{scored['presentation'].pk}/ranking/")
    assert response.context["horizon"] == 8


def test_tc4_an_unrecognized_horizon_falls_back_to_the_default(advisor_client, scored):
    url = f"/presentations/{scored['presentation'].pk}/ranking/"
    for value in ("7", "nonsense", "-1", ""):
        assert advisor_client.get(url, {"horizon": value}).context["horizon"] == 8


def test_tc4_a_horizon_with_no_current_model_shows_a_message(advisor_client, scored):
    response = advisor_client.get(
        f"/presentations/{scored['presentation'].pk}/ranking/", {"horizon": 12}
    )
    assert response.context["model"] is None
    assert response.context["total"] == 0
    assert "No model is current at week 12" in response.content.decode()


def test_tc4_nothing_is_scored_at_request_time(advisor_client, scored):
    """The page must read stored rows: the count cannot change by loading it."""
    before = RiskScore.objects.count()
    advisor_client.get(f"/presentations/{scored['presentation'].pk}/ranking/")
    assert RiskScore.objects.count() == before
    assert ModelVersion.objects.count() == 1


def test_tc4_the_leading_reason_is_shown(advisor_client, scored):
    response = advisor_client.get(f"/presentations/{scored['presentation'].pk}/ranking/")
    assert response.context["rows"][0]["reason"] == "no VLE activity for 11 days"
    assert "no VLE activity for 11 days" in response.content.decode()


def test_tc4_flag_status_is_shown(advisor_client, scored, advisor_a):
    student = scored["students"][119]      # the highest risk
    Flag.objects.create(student=student, raised_by=advisor_a, status=STATUS_OPEN)
    response = advisor_client.get(f"/presentations/{scored['presentation'].pk}/ranking/")
    assert response.context["rows"][0]["has_open_flag"] is True
    assert response.context["rows"][1]["has_open_flag"] is False


def test_tc4_ranking_of_an_unassigned_presentation_is_403(
    client, advisor_a, presentation_b, password
):
    client.login(username=advisor_a.username, password=password)
    assert client.get(f"/presentations/{presentation_b.pk}/ranking/").status_code == 403
    assert AuditEntry.objects.filter(action="denied").exists()


def test_tc4_ranking_requires_authentication(client, scored):
    response = client.get(f"/presentations/{scored['presentation'].pk}/ranking/")
    assert response.status_code == 302
    assert response["Location"].startswith("/login/")


# --- TC6: the student detail page ----------------------------------------

def test_tc6_detail_shows_demographics_and_risk(advisor_client, scored):
    student = scored["students"][119]
    response = advisor_client.get(f"/students/{student.pk}/")
    assert response.status_code == 200
    assert response.context["student"] == student
    assert response.context["has_engagement"] is True
    assert response.context["score"].probability == pytest.approx(0.119)

    body = response.content.decode()
    assert str(student.id_student) in body
    assert "0-10%" in body or "90-100%" in body
    assert "no VLE activity for 11 days" in body
    assert "first attempt at this module" in body


def test_tc6_weekly_rows_stop_at_the_horizon(advisor_client, scored):
    student = scored["students"][10]
    response = advisor_client.get(f"/students/{student.pk}/", {"horizon": 4})
    assert [w.week for w in response.context["weekly"]] == [1, 2, 3, 4]


def test_tc6_a_student_with_no_engagement_shows_a_message_and_no_probability(
    advisor_client, scored
):
    """Index 60 was created without any WeeklyFeatures rows."""
    student = scored["students"][60]
    assert not WeeklyFeatures.objects.filter(student=student).exists()

    response = advisor_client.get(f"/students/{student.pk}/")
    assert response.context["has_engagement"] is False
    assert response.context["score"] is None

    body = response.content.decode()
    assert "No engagement data recorded yet." in body
    # The stored score must not leak onto the page.
    assert "Current risk" not in body


def test_tc6_detail_of_an_unassigned_presentation_is_403(
    client, advisor_a, presentation_b, password
):
    other = Student.objects.create(
        presentation=presentation_b,
        id_student=9001,
        gender="F",
        region="Scotland",
        highest_education="A Level or Equivalent",
        imd_band="0-10%",
        age_band="0-35",
        disability=False,
        num_prev_attempts=0,
        studied_credits=60,
        final_result="Pass",
    )
    client.login(username=advisor_a.username, password=password)
    assert client.get(f"/students/{other.pk}/").status_code == 403


# --- TC6: the flag control rules -----------------------------------------

def test_tc6_flag_control_shows_for_an_advisor(advisor_client, scored):
    student = scored["students"][50]
    control = advisor_client.get(f"/students/{student.pk}/").context["flag_control"]
    assert control.visible is True
    assert control.disabled is False


def test_tc6_flag_control_is_hidden_from_an_instructor(
    client, instructor_a, password, scored
):
    client.login(username=instructor_a.username, password=password)
    student = scored["students"][50]
    control = client.get(f"/students/{student.pk}/").context["flag_control"]
    assert control.visible is False
    assert "only an advisor" in control.reason


def test_tc6_an_open_flag_hides_the_control(advisor_client, scored, advisor_a):
    student = scored["students"][50]
    Flag.objects.create(student=student, raised_by=advisor_a, status=STATUS_OPEN)
    control = advisor_client.get(f"/students/{student.pk}/").context["flag_control"]
    assert control.visible is False
    assert "already open" in control.reason


def test_tc6_a_flag_closed_as_resolved_hides_the_control(
    advisor_client, scored, advisor_a
):
    student = scored["students"][50]
    Flag.objects.create(student=student, raised_by=advisor_a, status=STATUS_RESOLVED)
    control = advisor_client.get(f"/students/{student.pk}/").context["flag_control"]
    assert control.visible is False
    assert "closed as resolved" in control.reason


def test_tc6_a_flag_closed_as_no_response_does_not_block(
    advisor_client, scored, advisor_a
):
    """A student who did not answer last time may answer this time."""
    student = scored["students"][50]
    Flag.objects.create(student=student, raised_by=advisor_a, status=STATUS_NO_RESPONSE)
    control = advisor_client.get(f"/students/{student.pk}/").context["flag_control"]
    assert control.visible is True
    assert control.disabled is False


def test_tc6_an_unregistered_student_disables_the_control_with_the_day(
    advisor_client, scored
):
    student = scored["students"][0]
    assert student.date_unregistration == 100
    response = advisor_client.get(f"/students/{student.pk}/")
    control = response.context["flag_control"]
    assert control.visible is True
    assert control.disabled is True
    assert "unregistered on day 100" in control.reason
    assert "unregistered on day 100" in response.content.decode()


# --- TC10: the dashboard -------------------------------------------------

def test_tc10_dashboard_breaks_risk_down_by_dimension(advisor_client, scored):
    response = advisor_client.get(
        f"/presentations/{scored['presentation'].pk}/dashboard/",
        {"dim": "imd_band"},
    )
    assert response.status_code == 200
    assert response.context["dimension"] == "imd_band"
    summary = {entry["level"]: entry for entry in response.context["summary"]}
    assert set(summary) == {"0-10%", "90-100%"}
    assert summary["0-10%"]["n"] == 100
    assert summary["90-100%"]["n"] == 20
    assert summary["0-10%"]["mean_probability"] == pytest.approx(0.0495)


def test_tc10_a_group_under_twenty_is_named_but_not_described(
    advisor_client, scored, settings
):
    assert settings.MIN_SUBGROUP_N == 20
    # Move all but 5 of the smaller band into the larger one.
    ids = Student.objects.filter(imd_band="90-100%").values_list("pk", flat=True)[:15]
    Student.objects.filter(pk__in=list(ids)).update(imd_band="0-10%")

    response = advisor_client.get(
        f"/presentations/{scored['presentation'].pk}/dashboard/", {"dim": "imd_band"}
    )
    summary = {entry["level"]: entry for entry in response.context["summary"]}
    assert summary["90-100%"] == {
        "level": "90-100%", "n": 5, "suppressed": True,
        "mean_probability": None, "high_risk": None, "high_risk_share": None,
    }
    body = response.content.decode()
    assert "90-100%" in body
    assert "suppressed (n &lt; 20)" in body


def test_tc10_a_group_of_exactly_twenty_is_reported(advisor_client, scored):
    response = advisor_client.get(
        f"/presentations/{scored['presentation'].pk}/dashboard/", {"dim": "imd_band"}
    )
    summary = {entry["level"]: entry for entry in response.context["summary"]}
    assert summary["90-100%"]["suppressed"] is False
    assert summary["90-100%"]["n"] == 20


def test_tc10_the_table_is_suppressed_when_every_group_is_too_small(
    advisor_client, scored
):
    """Spread the cohort thin enough that no level reaches 20."""
    for index, student in enumerate(scored["students"]):
        student.imd_band = f"band-{index % 12}"       # 10 students per level
    Student.objects.bulk_update(scored["students"], ["imd_band"])

    response = advisor_client.get(
        f"/presentations/{scored['presentation'].pk}/dashboard/", {"dim": "imd_band"}
    )
    assert response.context["all_suppressed"] is True
    body = response.content.decode()
    assert "suppressed in full" in body
    assert "Mean risk" not in body


def test_tc10_no_aggregate_at_all_below_ten_scored_students(
    advisor_client, scored, settings
):
    assert settings.MIN_DASHBOARD_SCORED == 10
    keep = [score.pk for score in RiskScore.objects.all()[:9]]
    RiskScore.objects.exclude(pk__in=keep).delete()

    response = advisor_client.get(
        f"/presentations/{scored['presentation'].pk}/dashboard/", {"dim": "imd_band"}
    )
    assert response.context["scored_total"] == 9
    assert response.context["too_few"] is True
    body = response.content.decode()
    assert "fewer than the 10 needed" in body
    assert "Mean risk" not in body


def test_tc10_stored_model_metrics_are_shown_with_their_suppression(
    advisor_client, scored
):
    response = advisor_client.get(
        f"/presentations/{scored['presentation'].pk}/dashboard/", {"dim": "imd_band"}
    )
    levels = {entry["level"]: entry for entry in response.context["model_levels"]}
    assert levels["0-10%"]["auc_roc"] == 0.68
    assert levels["90-100%"]["suppressed"] is True
    assert levels["90-100%"]["auc_roc"] is None


def test_tc10_an_unknown_dimension_falls_back_to_the_first(advisor_client, scored):
    response = advisor_client.get(
        f"/presentations/{scored['presentation'].pk}/dashboard/",
        {"dim": "favourite_colour"},
    )
    assert response.context["dimension"] == "imd_band"


def test_tc10_every_offered_dimension_renders(advisor_client, scored):
    for dimension in advisor_client.get(
        f"/presentations/{scored['presentation'].pk}/dashboard/"
    ).context["dimensions"]:
        response = advisor_client.get(
            f"/presentations/{scored['presentation'].pk}/dashboard/",
            {"dim": dimension},
        )
        assert response.status_code == 200
        assert response.context["dimension"] == dimension


def test_tc10_disability_reads_as_yes_and_no(advisor_client, scored):
    response = advisor_client.get(
        f"/presentations/{scored['presentation'].pk}/dashboard/",
        {"dim": "disability"},
    )
    levels = {entry["level"] for entry in response.context["summary"]}
    assert levels == {"Yes", "No"}


def test_tc10_dashboard_of_an_unassigned_presentation_is_403(
    client, advisor_a, presentation_b, password
):
    client.login(username=advisor_a.username, password=password)
    assert client.get(f"/presentations/{presentation_b.pk}/dashboard/").status_code == 403


# --- TC18: the CSV export ------------------------------------------------

def read_csv_response(response) -> list[dict]:
    text = b"".join(response.streaming_content).decode() if response.streaming else response.content.decode()
    return list(csv.DictReader(io.StringIO(text)))


def test_tc18_export_covers_the_whole_ranking_not_one_page(advisor_client, scored):
    response = advisor_client.get(
        f"/presentations/{scored['presentation'].pk}/ranking/export.csv"
    )
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    rows = read_csv_response(response)
    assert len(rows) == 120        # not the 50 on screen


def test_tc18_export_is_ordered_and_carries_the_reasons(advisor_client, scored):
    rows = read_csv_response(
        advisor_client.get(
            f"/presentations/{scored['presentation'].pk}/ranking/export.csv"
        )
    )
    probabilities = [float(row["probability"]) for row in rows]
    assert probabilities == sorted(probabilities, reverse=True)
    assert rows[0]["reason_1"] == "no VLE activity for 11 days"
    assert rows[0]["reason_3"] == "first attempt at this module"
    assert rows[0]["horizon_week"] == "8"


def test_tc18_export_names_the_presentation_and_horizon_in_the_filename(
    advisor_client, scored
):
    response = advisor_client.get(
        f"/presentations/{scored['presentation'].pk}/ranking/export.csv"
    )
    disposition = response["Content-Disposition"]
    assert "attachment" in disposition
    assert "ranking_AAA_2013J_w8.csv" in disposition


def test_tc18_export_writes_exactly_one_audit_entry(advisor_client, scored):
    advisor_client.get(f"/presentations/{scored['presentation'].pk}/ranking/export.csv")
    entry = AuditEntry.objects.get(action="export")
    assert entry.target == "ranking:AAA/2013J:w8"
    assert entry.role == ROLE_ADVISOR


def test_tc18_export_of_an_unassigned_presentation_is_403_and_exports_nothing(
    client, advisor_a, presentation_b, password
):
    client.login(username=advisor_a.username, password=password)
    response = client.get(f"/presentations/{presentation_b.pk}/ranking/export.csv")
    assert response.status_code == 403
    assert not AuditEntry.objects.filter(action="export").exists()


def test_tc18_export_contains_no_identifying_field(advisor_client, scored):
    response = advisor_client.get(
        f"/presentations/{scored['presentation'].pk}/ranking/export.csv"
    )
    header = read_csv_response(response)[0].keys()
    for forbidden in ("name", "email", "first_name", "last_name", "contact"):
        assert not any(forbidden in column for column in header)
