"""The authorization service is the only gate on student data. TC5.

Proves that a user assigned to presentation A is refused presentation B by
direct URL, and that every denial is recorded. This is the design document's
TC5, Permission denied on ranking, at the service level; the route-level half
is in test_phase5_usecases.py, which drives the ranking, the dashboard, the
export and a student detail page in an unassigned presentation.
"""

import pytest
from django.core.exceptions import PermissionDenied

from accounts.authz import assert_can_view, is_admin, presentations_for
from audit.models import AuditEntry


def test_presentations_for_returns_only_assignments(advisor_a, presentation_a, presentation_b):
    visible = presentations_for(advisor_a)
    assert list(visible) == [presentation_a]
    assert presentation_b not in visible


def test_presentations_for_admin_sees_all(administrator, presentation_a, presentation_b):
    visible = set(presentations_for(administrator))
    assert visible == {presentation_a, presentation_b}
    assert is_admin(administrator) is True


def test_assert_can_view_allows_assigned(advisor_a, presentation_a):
    assert assert_can_view(advisor_a, presentation_a) is None
    assert not AuditEntry.objects.filter(action="denied").exists()


def test_assert_can_view_denies_unassigned_and_audits(advisor_a, presentation_b):
    with pytest.raises(PermissionDenied):
        assert_can_view(advisor_a, presentation_b)
    entry = AuditEntry.objects.get(action="denied")
    assert entry.user == advisor_a
    assert entry.target == f"presentation:{presentation_b.pk}"


def test_anonymous_user_sees_nothing(db, presentation_a):
    from django.contrib.auth.models import AnonymousUser

    assert presentations_for(AnonymousUser()).count() == 0
    assert is_admin(AnonymousUser()) is False


def test_direct_url_to_unassigned_presentation_is_403(client, advisor_a, presentation_b, password):
    """A user assigned to A gets 403 on B by direct URL, not a redirect."""
    client.login(username=advisor_a.username, password=password)
    response = client.post("/presentations/", {"presentation": presentation_b.pk})
    assert response.status_code == 403
    assert AuditEntry.objects.filter(
        action="denied", target=f"presentation:{presentation_b.pk}"
    ).exists()


def test_audit_log_is_append_only(advisor_a):
    from audit.models import AppendOnly
    from audit.services import record

    entry = record(advisor_a, "login", "user:advisor_a")
    with pytest.raises(AppendOnly):
        entry.save()
    with pytest.raises(AppendOnly):
        entry.delete()
    with pytest.raises(AppendOnly):
        AuditEntry.objects.all().delete()


def test_the_403_page_states_which_refusal_it_is(client, advisor_a, presentation_b, password):
    """UC03's exception flow requires the message, not just the status.

    Django's default 403 body is the single word "Forbidden", so asserting the
    PermissionDenied message alone passes while the user sees nothing.
    """
    client.login(username=advisor_a.username, password=password)
    response = client.get(f"/presentations/{presentation_b.pk}/ranking/")
    assert response.status_code == 403

    body = response.content.decode()
    assert "You do not have access to this presentation." in body
    # And still discloses nothing about the presentation it refused.
    assert presentation_b.code_module not in body
    assert "probability" not in body.lower()


def test_the_admin_refusal_also_states_itself(client, advisor_a, password):
    client.login(username=advisor_a.username, password=password)
    response = client.get("/admin/upload/")
    assert response.status_code == 403
    assert "Administrator role required." in response.content.decode()
