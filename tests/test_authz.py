"""The authorization service is the only gate on student data.

Proves that a user assigned to presentation A is refused presentation B by
direct URL, and that every denial is recorded.
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
