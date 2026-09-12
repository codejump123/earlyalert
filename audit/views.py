"""The audit log (UC12). Administrators only.

The log is append-only and this view is read-only: there is no form here that
changes an entry, and the Django admin registration refuses add, change and
delete as well.

Taking a copy of the log is itself recorded, with the filters that produced
it, so an export can be tied to what it actually contained.
"""

import csv

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from accounts.authz import assert_is_admin

from .filters import ROLES, filtered_entries, known_usernames, parse_filters
from .models import ACTIONS
from .services import record

PAGE_SIZE = 50


@login_required
@require_http_methods(["GET"])
def audit_view(request):
    assert_is_admin(request.user)
    filters = parse_filters(request.GET)
    entries = filtered_entries(filters)

    if request.GET.get("export") == "csv":
        return _export(request, filters, entries)

    paginator = Paginator(entries, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "audit/log.html",
        {
            "page": page,
            "total": paginator.count,
            "filters": filters,
            "actions": ACTIONS,
            "roles": ROLES,
            "usernames": known_usernames(),
            "querystring": query.urlencode(),
        },
    )


def _export(request, filters, entries) -> HttpResponse:
    """The filtered log as CSV, and one 'export' entry naming the filters."""
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="audit_log.csv"'
    writer = csv.writer(response)
    writer.writerow(["timestamp", "user", "role", "action", "target"])
    for entry in entries.iterator(chunk_size=500):
        writer.writerow(
            [
                entry.timestamp.isoformat(),
                entry.user.username if entry.user else "",
                entry.role,
                entry.action,
                entry.target,
            ]
        )
    record(request.user, "export", f"audit:{filters.describe()}")
    return response
