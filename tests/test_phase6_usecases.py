"""Phase 6 test cases: TC7, TC8, TC9.

The SRS holds the authoritative wording. The intent assumed here:

  TC7  An advisor raises a flag on a student, subject to the control rules,
       and the flag is audited once.
  TC8  An advisor records an intervention against an open flag; its date must
       be on or after the flag and not in the future.
  TC9  Recording an outcome closes the flag with the matching status, and a
       closed flag accepts nothing further.

If the SRS says otherwise, the SRS wins and these tests get corrected.
"""

import datetime as dt

import pytest
from django.utils import timezone

from audit.models import AuditEntry
from cohorts.models import Student
from interventions.models import (
    OUTCOME_TO_STATUS,
    STATUS_NO_RESPONSE,
    STATUS_OPEN,
    STATUS_REFERRED,
    STATUS_RESOLVED,
    STATUS_WITHDRAWN,
    Flag,
    Intervention,
    InterventionOutcome,
)
from interventions.services import WorkflowError, create_flag, record_intervention


@pytest.fixture
def student(db, presentation_a):
    return Student.objects.create(
        presentation=presentation_a,
        id_student=3001,
        gender="F",
        region="Scotland",
        highest_education="A Level or Equivalent",
        imd_band="20-30%",
        age_band="0-35",
        disability=False,
        num_prev_attempts=0,
        studied_credits=60,
        date_registration=-14,
        final_result="Fail",
    )


@pytest.fixture
def unregistered_student(db, presentation_a):
    return Student.objects.create(
        presentation=presentation_a,
        id_student=3002,
        gender="M",
        region="Scotland",
        highest_education="A Level or Equivalent",
        imd_band="20-30%",
        age_band="0-35",
        disability=False,
        num_prev_attempts=0,
        studied_credits=60,
        date_registration=-14,
        date_unregistration=42,
        final_result="Withdrawn",
    )


@pytest.fixture
def advisor_client(client, advisor_a, password):
    client.login(username=advisor_a.username, password=password)
    return client


@pytest.fixture
def open_flag(db, student, advisor_a):
    return Flag.objects.create(
        student=student, raised_by=advisor_a, reason="stopped logging in"
    )


def today() -> dt.date:
    return timezone.localdate()


# --- TC7: raising a flag -------------------------------------------------

def test_tc7_an_advisor_raises_a_flag(advisor_client, student, advisor_a):
    response = advisor_client.post(
        f"/students/{student.pk}/flag/", {"reason": "no activity for three weeks"}
    )
    flag = Flag.objects.get()
    assert response.status_code == 302
    assert response["Location"] == f"/flags/{flag.pk}/intervention/"
    assert flag.student == student
    assert flag.raised_by == advisor_a
    assert flag.status == STATUS_OPEN
    assert flag.reason == "no activity for three weeks"
    assert flag.closed_at is None


def test_tc7_raising_a_flag_writes_exactly_one_audit_entry(advisor_client, student):
    advisor_client.post(f"/students/{student.pk}/flag/", {"reason": "x"})
    entries = AuditEntry.objects.filter(action="flag_created")
    assert entries.count() == 1
    flag = Flag.objects.get()
    assert entries.get().target == f"flag:{flag.pk}:student:{student.id_student}"


def test_tc7_a_flag_may_be_raised_without_a_reason(advisor_client, student):
    advisor_client.post(f"/students/{student.pk}/flag/", {})
    assert Flag.objects.get().reason == ""


def test_tc7_an_instructor_cannot_raise_a_flag(
    client, instructor_a, password, student
):
    client.login(username=instructor_a.username, password=password)
    response = client.post(f"/students/{student.pk}/flag/", {"reason": "x"})
    assert response.status_code == 302          # bounced back to the detail page
    assert Flag.objects.count() == 0
    assert not AuditEntry.objects.filter(action="flag_created").exists()


def test_tc7_a_second_flag_cannot_be_raised_while_one_is_open(
    advisor_client, student, open_flag
):
    advisor_client.post(f"/students/{student.pk}/flag/", {"reason": "again"})
    assert Flag.objects.count() == 1
    assert not AuditEntry.objects.filter(action="flag_created").exists()


def test_tc7_a_flag_closed_as_resolved_blocks_a_new_one(
    advisor_client, student, open_flag
):
    open_flag.status = STATUS_RESOLVED
    open_flag.closed_at = timezone.now()
    open_flag.save()

    advisor_client.post(f"/students/{student.pk}/flag/", {"reason": "again"})
    assert Flag.objects.count() == 1


def test_tc7_a_flag_closed_as_no_response_does_not_block_a_new_one(
    advisor_client, student, open_flag
):
    """The one closed status that leaves the door open."""
    open_flag.status = STATUS_NO_RESPONSE
    open_flag.closed_at = timezone.now()
    open_flag.save()

    advisor_client.post(f"/students/{student.pk}/flag/", {"reason": "trying again"})
    assert Flag.objects.count() == 2
    assert Flag.objects.filter(status=STATUS_OPEN).count() == 1


def test_tc7_an_unregistered_student_cannot_be_flagged(
    advisor_client, unregistered_student
):
    response = advisor_client.post(
        f"/students/{unregistered_student.pk}/flag/", {"reason": "too late"}
    )
    assert response.status_code == 302
    assert Flag.objects.count() == 0
    assert not AuditEntry.objects.filter(action="flag_created").exists()


def test_tc7_the_service_refuses_the_same_cases_the_view_hides(
    advisor_a, unregistered_student
):
    """A hidden control is not an enforced rule; the service checks too."""
    with pytest.raises(WorkflowError, match="Student withdrew on day 42"):
        create_flag(advisor_a, unregistered_student)


def test_tc7_flagging_a_student_in_an_unassigned_presentation_is_403(
    client, advisor_a, presentation_b, password
):
    other = Student.objects.create(
        presentation=presentation_b, id_student=4001, gender="F", region="Scotland",
        highest_education="A Level or Equivalent", imd_band="20-30%", age_band="0-35",
        disability=False, num_prev_attempts=0, studied_credits=60, final_result="Pass",
    )
    client.login(username=advisor_a.username, password=password)
    assert client.post(f"/students/{other.pk}/flag/", {}).status_code == 403
    assert Flag.objects.count() == 0


# --- TC7: notes ----------------------------------------------------------

def test_tc7_a_note_is_appended_and_audited(advisor_client, open_flag):
    advisor_client.post(
        f"/flags/{open_flag.pk}/intervention/", {"note": "left a voicemail"}
    )
    open_flag.refresh_from_db()
    assert "stopped logging in" in open_flag.reason      # nothing overwritten
    assert "left a voicemail" in open_flag.reason
    assert AuditEntry.objects.filter(action="flag_note_added").count() == 1


def test_tc7_an_empty_note_is_refused(advisor_client, open_flag):
    advisor_client.post(f"/flags/{open_flag.pk}/intervention/", {"note": "   "})
    open_flag.refresh_from_db()
    assert open_flag.reason == "stopped logging in"
    assert not AuditEntry.objects.filter(action="flag_note_added").exists()


# --- TC8: recording an intervention --------------------------------------

def test_tc8_an_advisor_records_an_intervention(advisor_client, open_flag, advisor_a):
    response = advisor_client.post(
        f"/flags/{open_flag.pk}/intervention/",
        {"intervention_type": "email", "date": today().isoformat(), "notes": "sent"},
    )
    assert response.status_code == 302
    intervention = Intervention.objects.get()
    assert intervention.flag == open_flag
    assert intervention.advisor == advisor_a
    assert intervention.intervention_type == "email"
    assert intervention.date == today()
    assert intervention.notes == "sent"
    assert AuditEntry.objects.filter(action="intervention_recorded").count() == 1


def test_tc8_the_flag_stays_open_until_an_outcome_is_recorded(
    advisor_client, open_flag
):
    advisor_client.post(
        f"/flags/{open_flag.pk}/intervention/",
        {"intervention_type": "phone", "date": today().isoformat()},
    )
    open_flag.refresh_from_db()
    assert open_flag.status == STATUS_OPEN
    assert open_flag.closed_at is None


def test_tc8_several_interventions_may_be_recorded(advisor_client, open_flag):
    for kind in ("email", "phone", "meeting"):
        advisor_client.post(
            f"/flags/{open_flag.pk}/intervention/",
            {"intervention_type": kind, "date": today().isoformat()},
        )
    assert Intervention.objects.count() == 3
    assert AuditEntry.objects.filter(action="intervention_recorded").count() == 3


def test_tc8_a_future_date_is_refused(advisor_client, open_flag):
    tomorrow = today() + dt.timedelta(days=1)
    advisor_client.post(
        f"/flags/{open_flag.pk}/intervention/",
        {"intervention_type": "email", "date": tomorrow.isoformat()},
    )
    assert Intervention.objects.count() == 0
    assert not AuditEntry.objects.filter(action="intervention_recorded").exists()


def test_tc8_a_date_before_the_flag_is_refused(advisor_client, open_flag):
    before = timezone.localtime(open_flag.created_at).date() - dt.timedelta(days=1)
    advisor_client.post(
        f"/flags/{open_flag.pk}/intervention/",
        {"intervention_type": "email", "date": before.isoformat()},
    )
    assert Intervention.objects.count() == 0


def test_tc8_the_day_the_flag_was_raised_is_allowed(advisor_client, open_flag):
    """The boundary is inclusive at both ends."""
    raised_on = timezone.localtime(open_flag.created_at).date()
    advisor_client.post(
        f"/flags/{open_flag.pk}/intervention/",
        {"intervention_type": "email", "date": raised_on.isoformat()},
    )
    assert Intervention.objects.count() == 1


def test_tc8_an_unreadable_or_missing_date_is_refused(advisor_client, open_flag):
    for value in ("", "not-a-date", "2013-02-30"):
        advisor_client.post(
            f"/flags/{open_flag.pk}/intervention/",
            {"intervention_type": "email", "date": value},
        )
    assert Intervention.objects.count() == 0


def test_tc8_an_unknown_intervention_type_is_refused(advisor_client, open_flag):
    advisor_client.post(
        f"/flags/{open_flag.pk}/intervention/",
        {"intervention_type": "carrier_pigeon", "date": today().isoformat()},
    )
    assert Intervention.objects.count() == 0


def test_tc8_a_closed_flag_accepts_no_intervention(advisor_client, open_flag, advisor_a):
    open_flag.status = STATUS_RESOLVED
    open_flag.closed_at = timezone.now()
    open_flag.save()

    with pytest.raises(WorkflowError, match="closed"):
        record_intervention(advisor_a, open_flag, "email", today())
    assert Intervention.objects.count() == 0


def test_tc8_an_intervention_on_an_unassigned_presentation_is_403(
    client, advisor_a, presentation_b, password, advisor_client
):
    other = Student.objects.create(
        presentation=presentation_b, id_student=4002, gender="F", region="Scotland",
        highest_education="A Level or Equivalent", imd_band="20-30%", age_band="0-35",
        disability=False, num_prev_attempts=0, studied_credits=60, final_result="Pass",
    )
    foreign = Flag.objects.create(student=other, raised_by=advisor_a)
    response = advisor_client.post(
        f"/flags/{foreign.pk}/intervention/",
        {"intervention_type": "email", "date": today().isoformat()},
    )
    assert response.status_code == 403
    assert Intervention.objects.count() == 0


# --- TC9: recording an outcome -------------------------------------------

@pytest.mark.parametrize(
    "outcome,status",
    [
        ("re_engaged", STATUS_RESOLVED),
        ("responded_withdrew", STATUS_WITHDRAWN),
        ("no_response", STATUS_NO_RESPONSE),
        ("referred", STATUS_REFERRED),
    ],
)
def test_tc9_an_outcome_closes_the_flag_with_the_matching_status(
    advisor_client, open_flag, outcome, status
):
    intervention = Intervention.objects.create(
        flag=open_flag, advisor=open_flag.raised_by,
        intervention_type="email", date=today(),
    )
    response = advisor_client.post(
        f"/interventions/{intervention.pk}/outcome/", {"outcome": outcome}
    )
    assert response.status_code == 302

    open_flag.refresh_from_db()
    assert open_flag.status == status
    assert open_flag.closed_at is not None
    assert InterventionOutcome.objects.get().outcome == outcome
    assert AuditEntry.objects.filter(action="outcome_recorded").count() == 1


def test_tc9_the_mapping_covers_every_outcome():
    from interventions.models import OUTCOME_CHOICES

    assert set(OUTCOME_TO_STATUS) == {value for value, _ in OUTCOME_CHOICES}


def test_tc9_the_audit_entry_names_the_closing_status(advisor_client, open_flag):
    intervention = Intervention.objects.create(
        flag=open_flag, advisor=open_flag.raised_by,
        intervention_type="email", date=today(),
    )
    advisor_client.post(
        f"/interventions/{intervention.pk}/outcome/", {"outcome": "re_engaged"}
    )
    entry = AuditEntry.objects.get(action="outcome_recorded")
    assert entry.target.endswith(f"flag:{open_flag.pk}:{STATUS_RESOLVED}")


def test_tc9_an_outcome_cannot_be_recorded_twice(advisor_client, open_flag):
    intervention = Intervention.objects.create(
        flag=open_flag, advisor=open_flag.raised_by,
        intervention_type="email", date=today(),
    )
    advisor_client.post(
        f"/interventions/{intervention.pk}/outcome/", {"outcome": "re_engaged"}
    )
    advisor_client.post(
        f"/interventions/{intervention.pk}/outcome/", {"outcome": "no_response"}
    )
    assert InterventionOutcome.objects.count() == 1
    assert InterventionOutcome.objects.get().outcome == "re_engaged"
    open_flag.refresh_from_db()
    assert open_flag.status == STATUS_RESOLVED
    assert AuditEntry.objects.filter(action="outcome_recorded").count() == 1


def test_tc9_an_unknown_outcome_is_refused(advisor_client, open_flag):
    intervention = Intervention.objects.create(
        flag=open_flag, advisor=open_flag.raised_by,
        intervention_type="email", date=today(),
    )
    advisor_client.post(
        f"/interventions/{intervention.pk}/outcome/", {"outcome": "vanished"}
    )
    assert InterventionOutcome.objects.count() == 0
    open_flag.refresh_from_db()
    assert open_flag.status == STATUS_OPEN


def test_tc9_a_second_interventions_outcome_cannot_reclose_a_closed_flag(
    advisor_client, open_flag
):
    first, second = [
        Intervention.objects.create(
            flag=open_flag, advisor=open_flag.raised_by,
            intervention_type=kind, date=today(),
        )
        for kind in ("email", "phone")
    ]
    advisor_client.post(f"/interventions/{first.pk}/outcome/", {"outcome": "re_engaged"})
    advisor_client.post(f"/interventions/{second.pk}/outcome/", {"outcome": "no_response"})

    open_flag.refresh_from_db()
    assert open_flag.status == STATUS_RESOLVED      # the first close stands
    assert InterventionOutcome.objects.count() == 1


def test_tc9_closing_as_resolved_then_blocks_a_new_flag(advisor_client, student, open_flag):
    """The workflow end to end: raise, contact, resolve, and no re-flagging."""
    intervention = Intervention.objects.create(
        flag=open_flag, advisor=open_flag.raised_by,
        intervention_type="meeting", date=today(),
    )
    advisor_client.post(f"/interventions/{intervention.pk}/outcome/", {"outcome": "re_engaged"})
    advisor_client.post(f"/students/{student.pk}/flag/", {"reason": "again"})
    assert Flag.objects.count() == 1


def test_tc9_closing_as_no_response_allows_a_new_flag(advisor_client, student, open_flag):
    intervention = Intervention.objects.create(
        flag=open_flag, advisor=open_flag.raised_by,
        intervention_type="email", date=today(),
    )
    advisor_client.post(f"/interventions/{intervention.pk}/outcome/", {"outcome": "no_response"})
    advisor_client.post(f"/students/{student.pk}/flag/", {"reason": "second try"})
    assert Flag.objects.count() == 2


def test_tc9_an_outcome_on_an_unassigned_presentation_is_403(
    advisor_client, advisor_a, presentation_b
):
    other = Student.objects.create(
        presentation=presentation_b, id_student=4003, gender="F", region="Scotland",
        highest_education="A Level or Equivalent", imd_band="20-30%", age_band="0-35",
        disability=False, num_prev_attempts=0, studied_credits=60, final_result="Pass",
    )
    foreign = Flag.objects.create(student=other, raised_by=advisor_a)
    intervention = Intervention.objects.create(
        flag=foreign, advisor=advisor_a, intervention_type="email", date=today()
    )
    response = advisor_client.post(
        f"/interventions/{intervention.pk}/outcome/", {"outcome": "re_engaged"}
    )
    assert response.status_code == 403
    assert InterventionOutcome.objects.count() == 0


# --- one entry per state change ------------------------------------------

def test_the_whole_workflow_writes_one_audit_entry_per_action(
    advisor_client, student
):
    advisor_client.post(f"/students/{student.pk}/flag/", {"reason": "quiet"})
    flag = Flag.objects.get()
    advisor_client.post(f"/flags/{flag.pk}/intervention/", {"note": "tried calling"})
    advisor_client.post(
        f"/flags/{flag.pk}/intervention/",
        {"intervention_type": "phone", "date": today().isoformat()},
    )
    intervention = Intervention.objects.get()
    advisor_client.post(
        f"/interventions/{intervention.pk}/outcome/", {"outcome": "re_engaged"}
    )

    counts = {
        action: AuditEntry.objects.filter(action=action).count()
        for action in (
            "flag_created", "flag_note_added", "intervention_recorded",
            "outcome_recorded",
        )
    }
    assert counts == {
        "flag_created": 1, "flag_note_added": 1,
        "intervention_recorded": 1, "outcome_recorded": 1,
    }
