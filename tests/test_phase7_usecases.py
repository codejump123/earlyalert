"""Phase 7 test case: TC17.

The SRS holds the authoritative wording. The intent assumed here:

  TC17  An administrator reads the audit log, filters it by action, role, user,
        target and date, and exports the filtered set as CSV. The log cannot be
        edited or deleted through the application, and taking a copy is itself
        recorded.

If the SRS says otherwise, the SRS wins and this test gets corrected.
"""

import csv
import datetime as dt
import io
from unittest import mock

import pytest
from django.utils import timezone

from audit.filters import parse_filters
from audit.models import ACTIONS, AppendOnly, AuditEntry
from audit.services import record
from audit.views import PAGE_SIZE


@pytest.fixture
def log(db, administrator, advisor_a, instructor_a):
    """A log with a known shape: 3 users, several actions, two days.

    The two older entries are written under a wound-back clock rather than
    updated afterwards. The log refuses updates, which is the point of it, so a
    test that needs history has to create history.
    """
    yesterday = timezone.now() - dt.timedelta(days=1)
    with mock.patch("django.utils.timezone.now", return_value=yesterday):
        record(advisor_a, "login", "user:advisor_a")
        record(advisor_a, "flag_created", "flag:1:student:1001")

    record(advisor_a, "intervention_recorded", "intervention:1:flag:1")
    record(advisor_a, "outcome_recorded", "outcome:1:flag:1:closed_resolved")
    record(instructor_a, "login", "user:instructor_a")
    record(instructor_a, "denied", "presentation:99")
    record(administrator, "upload_accepted", "batch:abc")
    record(administrator, "rebuild_completed", "rows:1000")
    record(None, "login_failed", "user:ghost")
    return {"yesterday": yesterday.date(), "today": timezone.localdate()}


@pytest.fixture
def admin_client(client, administrator, password):
    client.login(username=administrator.username, password=password)
    return client


def rows_of(response) -> list[dict]:
    return list(csv.DictReader(io.StringIO(response.content.decode())))


# --- TC17: reading the log -----------------------------------------------

def test_tc17_the_log_lists_newest_first(admin_client, log):
    response = admin_client.get("/admin/audit/")
    assert response.status_code == 200
    stamps = [entry.timestamp for entry in response.context["page"].object_list]
    assert stamps == sorted(stamps, reverse=True)


def test_tc17_the_login_that_opened_the_page_is_in_the_log(admin_client, log):
    response = admin_client.get("/admin/audit/")
    actions = [e.action for e in response.context["page"].object_list]
    assert "login" in actions


def test_tc17_an_anonymous_entry_shows_without_a_user(admin_client, log):
    response = admin_client.get("/admin/audit/", {"action": "login_failed"})
    entry = response.context["page"].object_list[0]
    assert entry.user is None
    assert entry.target == "user:ghost"
    assert "—" in response.content.decode()


def test_tc17_pagination_is_one_hundred_rows(admin_client, administrator):
    for index in range(PAGE_SIZE + 10):
        record(administrator, "export", f"ranking:{index}")
    response = admin_client.get("/admin/audit/", {"action": "export"})
    assert PAGE_SIZE == 100
    assert len(response.context["page"].object_list) == 100
    assert response.context["total"] == PAGE_SIZE + 10
    assert response.context["page"].paginator.num_pages == 2


def test_tc17_the_log_is_administrator_only(client, advisor_a, password, log):
    client.login(username=advisor_a.username, password=password)
    assert client.get("/admin/audit/").status_code == 403
    assert client.get("/admin/audit/", {"export": "csv"}).status_code == 403


def test_tc17_the_log_requires_authentication(client, log):
    response = client.get("/admin/audit/")
    assert response.status_code == 302
    assert response["Location"].startswith("/login/")


# --- TC17: filters -------------------------------------------------------

def test_tc17_filter_by_action(admin_client, log):
    response = admin_client.get("/admin/audit/", {"action": "flag_created"})
    entries = list(response.context["page"].object_list)
    assert len(entries) == 1
    assert entries[0].target == "flag:1:student:1001"


def test_tc17_filter_by_role(admin_client, log, advisor_a):
    response = admin_client.get("/admin/audit/", {"role": "advisor"})
    entries = list(response.context["page"].object_list)
    assert entries
    assert {e.role for e in entries} == {"advisor"}
    assert {e.user for e in entries} == {advisor_a}


def test_tc17_filter_by_user(admin_client, log, instructor_a):
    response = admin_client.get(
        "/admin/audit/", {"username": instructor_a.username}
    )
    entries = list(response.context["page"].object_list)
    assert {e.user for e in entries} == {instructor_a}
    assert {e.action for e in entries} == {"login", "denied"}


def test_tc17_filter_by_target_substring(admin_client, log):
    response = admin_client.get("/admin/audit/", {"target": "flag:1"})
    actions = {e.action for e in response.context["page"].object_list}
    assert actions == {"flag_created", "intervention_recorded", "outcome_recorded"}


def test_tc17_filter_by_date_range(admin_client, log):
    only_yesterday = admin_client.get(
        "/admin/audit/", {"date_to": log["yesterday"].isoformat()}
    )
    assert only_yesterday.context["total"] == 2

    from_today = admin_client.get(
        "/admin/audit/", {"date_from": log["today"].isoformat()}
    )
    assert from_today.context["total"] == AuditEntry.objects.count() - 2


def test_tc17_date_to_includes_the_whole_day(admin_client, log):
    """'to the 12th' means the end of the 12th, not its first instant."""
    response = admin_client.get(
        "/admin/audit/", {"date_to": log["today"].isoformat()}
    )
    assert response.context["total"] == AuditEntry.objects.count()


def test_tc17_filters_combine(admin_client, log, advisor_a):
    response = admin_client.get(
        "/admin/audit/",
        {"role": "advisor", "action": "login", "date_from": log["yesterday"].isoformat()},
    )
    entries = list(response.context["page"].object_list)
    assert len(entries) == 1
    assert entries[0].action == "login"
    assert entries[0].user == advisor_a


def test_tc17_a_bad_action_or_role_is_dropped_not_applied(admin_client, log):
    """A typo shows the whole log rather than silently showing none of it."""
    response = admin_client.get(
        "/admin/audit/", {"action": "not_an_action", "role": "wizard"}
    )
    assert response.context["filters"].action == ""
    assert response.context["filters"].role == ""
    assert response.context["total"] == AuditEntry.objects.count()


def test_tc17_an_unreadable_date_falls_back_to_the_default_window(admin_client, log):
    """A date that cannot be parsed is dropped, leaving the UC12 default."""
    response = admin_client.get("/admin/audit/", {"date_from": "the-first"})
    filters = response.context["filters"]
    assert filters.windowed is True
    assert filters.date_from == dt.date.today() - dt.timedelta(days=30)
    assert response.context["total"] == AuditEntry.objects.count()


def test_tc17_the_log_defaults_to_the_last_thirty_days(admin_client, log):
    """UC12: thirty days spans the interval between two horizons."""
    response = admin_client.get("/admin/audit/")
    filters = response.context["filters"]
    assert filters.windowed is True
    assert filters.date_from == dt.date.today() - dt.timedelta(days=30)
    # A default window is not a filter the reader set.
    assert filters.active is False
    assert filters.describe() == "all"


def test_tc17_an_entry_older_than_the_window_is_hidden_until_asked_for(
    admin_client, administrator
):
    long_ago = timezone.now() - dt.timedelta(days=90)
    with mock.patch("django.utils.timezone.now", return_value=long_ago):
        record(administrator, "upload_accepted", "batch:ancient")

    assert admin_client.get("/admin/audit/", {"target": "ancient"}).context["total"] == 0
    lifted = admin_client.get("/admin/audit/", {"target": "ancient", "all": "1"})
    assert lifted.context["total"] == 1
    assert lifted.context["filters"].windowed is False


def test_tc17_no_match_says_so(admin_client, log):
    response = admin_client.get("/admin/audit/", {"target": "nothing-like-this"})
    assert response.context["total"] == 0
    assert "No entries match this filter." in response.content.decode()


def test_tc17_every_action_is_offered_as_a_filter(admin_client, log):
    offered = admin_client.get("/admin/audit/").context["actions"]
    assert list(offered) == list(ACTIONS)
    assert "retrain_failed" in offered
    assert "flag_note_added" in offered


# --- TC17: the CSV export ------------------------------------------------

def test_tc17_export_returns_the_whole_filtered_log(admin_client, log):
    response = admin_client.get("/admin/audit/", {"export": "csv"})
    assert response["Content-Type"] == "text/csv"
    assert "audit_log.csv" in response["Content-Disposition"]
    rows = rows_of(response)
    # The export's own entry is written after the rows are generated.
    assert len(rows) == AuditEntry.objects.exclude(action="export").count()
    assert list(rows[0]) == ["timestamp", "user", "role", "action", "target"]


def test_tc17_export_honours_the_filters_on_screen(admin_client, log):
    on_screen = admin_client.get("/admin/audit/", {"action": "flag_created"})
    exported = rows_of(
        admin_client.get("/admin/audit/", {"action": "flag_created", "export": "csv"})
    )
    assert len(exported) == on_screen.context["total"] == 1
    assert exported[0]["action"] == "flag_created"


def test_tc17_export_is_recorded_with_the_filters_that_produced_it(
    admin_client, log
):
    admin_client.get(
        "/admin/audit/", {"action": "login", "role": "advisor", "export": "csv"}
    )
    entry = AuditEntry.objects.filter(action="export").latest("timestamp")
    assert entry.target == "audit:action=login,role=advisor"


def test_tc17_an_unfiltered_export_is_recorded_as_all(admin_client, log):
    admin_client.get("/admin/audit/", {"export": "csv"})
    entry = AuditEntry.objects.filter(action="export").latest("timestamp")
    assert entry.target == "audit:all"


def test_tc17_export_writes_exactly_one_entry(admin_client, log):
    before = AuditEntry.objects.filter(action="export").count()
    admin_client.get("/admin/audit/", {"export": "csv"})
    assert AuditEntry.objects.filter(action="export").count() == before + 1


def test_tc17_an_anonymous_entry_exports_with_an_empty_user(admin_client, log):
    rows = rows_of(admin_client.get("/admin/audit/", {"action": "login_failed", "export": "csv"}))
    assert rows[0]["user"] == ""
    assert rows[0]["target"] == "user:ghost"


# --- TC17: the log cannot be changed -------------------------------------

def test_tc17_the_view_offers_no_way_to_change_an_entry(admin_client, log):
    body = admin_client.get("/admin/audit/").content.decode()
    # Nothing on the page submits back to the log, and POST is not an accepted
    # method on it at all. (The page chrome's logout form is not the log's.)
    assert 'action="/admin/audit/"' not in body
    assert admin_client.post("/admin/audit/").status_code == 405


def test_tc17_entries_resist_update_and_delete(log, administrator):
    entry = AuditEntry.objects.first()
    with pytest.raises(AppendOnly):
        entry.delete()
    with pytest.raises(AppendOnly):
        entry.save()
    with pytest.raises(AppendOnly):
        AuditEntry.objects.all().delete()
    with pytest.raises(AppendOnly):
        AuditEntry.objects.all().update(action="login")


def test_tc17_the_django_admin_refuses_add_change_and_delete(db):
    from django.contrib import admin as django_admin

    from audit.models import AuditEntry as Model

    registered = django_admin.site._registry[Model]
    assert registered.has_add_permission(None) is False
    assert registered.has_change_permission(None) is False
    assert registered.has_delete_permission(None) is False


# --- filters module ------------------------------------------------------

def test_parse_filters_reports_whether_anything_is_active():
    assert parse_filters({}).active is False
    assert parse_filters({"action": "login"}).active is True
    assert parse_filters({"action": "nonsense"}).active is False


def test_describe_names_every_filter_in_order():
    filters = parse_filters(
        {
            "action": "login", "role": "advisor", "username": "a",
            "target": "flag", "date_from": "2026-01-01", "date_to": "2026-01-31",
        }
    )
    assert filters.describe() == (
        "action=login,role=advisor,user=a,target~flag,"
        "from=2026-01-01,to=2026-01-31"
    )
