"""Retrain trigger and progress (UC11). Administrators only."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from accounts.authz import assert_is_admin
from earlyalert.joblock import JOB_LOG_LINES, current_job, read_progress

from .models import ModelVersion, RiskScore
from .retrain import JOB_NAME, features_are_stale, retrain


@login_required
@require_http_methods(["GET", "POST"])
def retrain_view(request):
    assert_is_admin(request.user)
    result = None
    if request.method == "POST":
        result = retrain(request.user)
    return render(
        request,
        "scoring/retrain.html",
        {
            "result": result,
            "running": current_job(JOB_NAME),
            "progress": read_progress(JOB_NAME, JOB_LOG_LINES),
            "blocked": features_are_stale(),
            "current_models": ModelVersion.objects.filter(is_current=True).order_by(
                "horizon_week"
            ),
            "score_count": RiskScore.objects.count(),
        },
    )
