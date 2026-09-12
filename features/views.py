"""Rebuild trigger and progress (UC10). Administrators only."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from accounts.authz import assert_is_admin
from cohorts.uploads import current_batch
from earlyalert.joblock import JOB_LOG_LINES, current_job, read_progress

from .models import WeeklyFeatures
from .rebuild import JOB_NAME, rebuild


@login_required
@require_http_methods(["GET", "POST"])
def rebuild_view(request):
    assert_is_admin(request.user)
    result = None
    if request.method == "POST":
        result = rebuild(request.user)
    running = current_job(JOB_NAME)
    return render(
        request,
        "features/rebuild.html",
        {
            "result": result,
            "running": running,
            "progress": read_progress(JOB_NAME, JOB_LOG_LINES),
            "current": current_batch(),
            "feature_rows": WeeklyFeatures.objects.count(),
        },
    )
