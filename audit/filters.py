"""Filtering the audit log.

Kept out of the view so the same filters apply to the page and to the CSV
export, and so the export cannot quietly cover a different set of rows than
the one on screen.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from django.contrib.auth import get_user_model

from .models import ACTIONS, AuditEntry

ROLES = ["instructor", "advisor", "administrator"]


@dataclass
class AuditFilters:
    """The filters as submitted, cleaned. Empty strings mean no filter."""

    action: str = ""
    role: str = ""
    username: str = ""
    target: str = ""
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    # True when date_from was supplied by the default window rather than typed.
    windowed: bool = False

    @property
    def active(self) -> bool:
        """True when the reader narrowed the log themselves.

        The default 30-day window does not count: a reader who typed nothing
        has not filtered anything, and telling them "no entries match this
        filter" for a log they never filtered would be wrong.
        """
        if self.windowed:
            return any([self.action, self.role, self.username, self.target,
                        self.date_to])
        return any([self.action, self.role, self.username, self.target,
                    self.date_from, self.date_to])

    def describe(self) -> str:
        """Output: the filters as one line, for the audit entry an export writes."""
        parts = []
        if self.action:
            parts.append(f"action={self.action}")
        if self.role:
            parts.append(f"role={self.role}")
        if self.username:
            parts.append(f"user={self.username}")
        if self.target:
            parts.append(f"target~{self.target}")
        if self.date_from and not self.windowed:
            parts.append(f"from={self.date_from}")
        if self.date_to:
            parts.append(f"to={self.date_to}")
        return ",".join(parts) if parts else "all"


def _date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def parse_filters(params, default_window_days: int | None = None) -> AuditFilters:
    """Input: request.GET, and optionally the default window in days.
    Output: AuditFilters.

    An unrecognized action or role is dropped rather than applied, so a typo in
    the query string shows the whole log instead of silently showing none of it.

    With no date given at all, the window defaults to the last
    default_window_days. Passing `all=1` lifts it, so the whole log is still
    reachable without typing a date from before the deployment existed.
    """
    action = (params.get("action") or "").strip()
    role = (params.get("role") or "").strip()
    date_from = _date(params.get("date_from"))
    date_to = _date(params.get("date_to"))
    windowed = False
    if (
        default_window_days
        and date_from is None
        and date_to is None
        and not params.get("all")
    ):
        date_from = dt.date.today() - dt.timedelta(days=default_window_days)
        windowed = True
    return AuditFilters(
        action=action if action in ACTIONS else "",
        role=role if role in ROLES else "",
        username=(params.get("username") or "").strip(),
        target=(params.get("target") or "").strip(),
        date_from=date_from,
        date_to=date_to,
        windowed=windowed,
    )


def filtered_entries(filters: AuditFilters):
    """Input: AuditFilters. Output: a queryset, newest first.

    date_to is inclusive of the whole day: a reader asking for "to the 12th"
    means the end of the 12th, not its first instant.
    """
    entries = AuditEntry.objects.select_related("user")
    if filters.action:
        entries = entries.filter(action=filters.action)
    if filters.role:
        entries = entries.filter(role=filters.role)
    if filters.username:
        entries = entries.filter(user__username=filters.username)
    if filters.target:
        entries = entries.filter(target__icontains=filters.target)
    if filters.date_from:
        entries = entries.filter(timestamp__date__gte=filters.date_from)
    if filters.date_to:
        entries = entries.filter(timestamp__date__lte=filters.date_to)
    return entries


def known_usernames() -> list[str]:
    """Output: usernames that appear in the log, for the filter dropdown."""
    ids = AuditEntry.objects.exclude(user=None).values_list("user_id", flat=True)
    return sorted(
        get_user_model()
        .objects.filter(pk__in=set(ids))
        .values_list("username", flat=True)
    )
