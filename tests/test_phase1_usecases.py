"""Phase 1 test cases: TC1, TC2, TC3, TC5.

The SRS holds the authoritative wording of each case. The intent assumed here:

  TC1  Each of the three roles signs in and reaches the presentation selector,
       and the sign-in is audited.
  TC2  A wrong password and an unknown username are rejected with the same
       message, so the form never reveals which usernames exist.
  TC3  Five consecutive failures lock the account; a locked account is refused
       even with the correct password, and only an administrator clears it.
  TC5  The selector lists only assigned presentations and the chosen one, with
       the horizon, appears in the fixed header.

If the SRS says otherwise, the SRS wins and these tests get corrected.
"""

import pytest

from accounts.models import ROLE_ADMIN, ROLE_ADVISOR, ROLE_INSTRUCTOR
from accounts.views import ACCOUNT_LOCKED, CREDENTIALS_REJECTED
from audit.models import AuditEntry


# --- TC1: sign in as each role -------------------------------------------

@pytest.mark.parametrize(
    "fixture_name,expected_role",
    [
        ("instructor_a", ROLE_INSTRUCTOR),
        ("advisor_a", ROLE_ADVISOR),
        ("administrator", ROLE_ADMIN),
    ],
)
def test_tc1_each_role_signs_in(client, request, password, fixture_name, expected_role):
    user = request.getfixturevalue(fixture_name)
    response = client.post(
        "/login/", {"username": user.username, "password": password}, follow=True
    )
    assert response.status_code == 200
    assert response.redirect_chain[-1][0] == "/presentations/"
    assert user.profile.role == expected_role
    assert AuditEntry.objects.filter(
        action="login", target=f"user:{user.username}"
    ).count() == 1


def test_tc1_logout_clears_the_session(client, advisor_a, password):
    client.login(username=advisor_a.username, password=password)
    response = client.post("/logout/", follow=True)
    assert response.redirect_chain[-1][0] == "/login/"
    # The selector now bounces to login rather than rendering.
    assert client.get("/presentations/").status_code == 302


def test_tc1_selector_requires_authentication(client, db):
    response = client.get("/presentations/")
    assert response.status_code == 302
    assert response["Location"].startswith("/login/")


# --- TC2: rejection and lockout -----------------------------------------

def test_tc2_wrong_password_and_unknown_username_match(client, advisor_a):
    wrong = client.post(
        "/login/", {"username": advisor_a.username, "password": "not-the-password"}
    )
    unknown = client.post(
        "/login/", {"username": "no-such-user", "password": "not-the-password"}
    )
    assert wrong.context["error"] == CREDENTIALS_REJECTED
    assert unknown.context["error"] == CREDENTIALS_REJECTED
    assert wrong.status_code == unknown.status_code == 200
    assert AuditEntry.objects.filter(action="login_failed").count() == 2


def test_tc2_failed_login_does_not_authenticate(client, advisor_a):
    client.post("/login/", {"username": advisor_a.username, "password": "wrong"})
    assert client.get("/presentations/").status_code == 302


# --- TC2 continued: five consecutive failures lock the account -----------

def test_tc2_five_failures_lock_the_account(client, advisor_a, password):
    for _ in range(5):
        client.post("/login/", {"username": advisor_a.username, "password": "wrong"})
    advisor_a.profile.refresh_from_db()
    assert advisor_a.profile.is_locked is True
    assert advisor_a.profile.failed_attempts == 5
    assert AuditEntry.objects.filter(action="login_locked").count() == 1

    # Correct password is still refused while locked.
    response = client.post(
        "/login/", {"username": advisor_a.username, "password": password}
    )
    assert response.context["error"] == ACCOUNT_LOCKED
    assert client.get("/presentations/").status_code == 302


def test_tc2_four_failures_do_not_lock(client, advisor_a, password):
    for _ in range(4):
        client.post("/login/", {"username": advisor_a.username, "password": "wrong"})
    advisor_a.profile.refresh_from_db()
    assert advisor_a.profile.is_locked is False
    response = client.post(
        "/login/", {"username": advisor_a.username, "password": password}, follow=True
    )
    assert response.redirect_chain[-1][0] == "/presentations/"
    # A success resets the counter, so failures must be consecutive to lock.
    advisor_a.profile.refresh_from_db()
    assert advisor_a.profile.failed_attempts == 0


def test_tc2_admin_clears_the_lock(client, advisor_a, password):
    for _ in range(5):
        client.post("/login/", {"username": advisor_a.username, "password": "wrong"})
    advisor_a.profile.refresh_from_db()
    advisor_a.profile.unlock()
    response = client.post(
        "/login/", {"username": advisor_a.username, "password": password}, follow=True
    )
    assert response.redirect_chain[-1][0] == "/presentations/"


# --- TC3: presentation selector ------------------------------------------

def test_tc3_selector_lists_only_assigned(client, advisor_a, presentation_a, presentation_b, password):
    client.login(username=advisor_a.username, password=password)
    response = client.get("/presentations/")
    assert response.status_code == 200
    listed = list(response.context["presentations"])
    assert listed == [presentation_a]
    body = response.content.decode()
    assert "AAA" in body
    assert "BBB" not in body


def test_tc3_admin_sees_every_presentation(client, administrator, presentation_a, presentation_b, password):
    client.login(username=administrator.username, password=password)
    response = client.get("/presentations/")
    assert set(response.context["presentations"]) == {presentation_a, presentation_b}


def test_tc3_selection_shows_in_the_fixed_header(client, advisor_a, presentation_a, password):
    client.login(username=advisor_a.username, password=password)
    client.post("/presentations/", {"presentation": presentation_a.pk, "horizon": 12})
    response = client.get("/presentations/")
    assert response.context["active_presentation"] == presentation_a
    assert response.context["active_horizon"] == 12
    body = response.content.decode()
    assert "AAA/2013J" in body
    assert "week 12" in body


def test_tc3_default_horizon_is_week_8(client, advisor_a, presentation_a, password):
    client.login(username=advisor_a.username, password=password)
    response = client.get("/presentations/")
    assert response.context["active_horizon"] == 8


# --- TC5: permission denied ----------------------------------------------

def test_tc5_unassigned_presentation_is_not_selectable(client, advisor_a, presentation_b, password):
    client.login(username=advisor_a.username, password=password)
    response = client.post("/presentations/", {"presentation": presentation_b.pk})
    assert response.status_code == 403
    assert client.session.get("active_presentation_id") is None
