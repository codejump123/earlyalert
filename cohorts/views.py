"""Presentation selector (UC02), student detail (UC04) and upload (UC09).

Lists only the presentations the authorization service returns for this user
and records the chosen one in the session for the fixed header.
"""

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.authz import assert_can_view, assert_is_admin, presentations_for
from pipeline.validate import REQUIRED_FILES

from scoring.models import RiskScore
from scoring.bands import band_for_rank, rank_of
from scoring.ranking import current_model, resolve_horizon

from .detail import flag_control_for
from .models import Student
from .uploads import UploadResult, accept_upload, current_batch


@login_required
@require_http_methods(["GET", "POST"])
def presentation_selector(request):
    presentations = presentations_for(request.user).order_by(
        "code_module", "code_presentation"
    )
    if request.method == "POST":
        pk = request.POST.get("presentation")
        horizon = request.POST.get("horizon", settings.DEFAULT_HORIZON_WEEK)
        # Selection goes through the same check as any other student-data view.
        assert_can_view(request.user, pk)
        request.session["active_presentation_id"] = int(pk)
        try:
            horizon = int(horizon)
        except (TypeError, ValueError):
            horizon = settings.DEFAULT_HORIZON_WEEK
        if horizon not in settings.HORIZON_WEEKS:
            horizon = settings.DEFAULT_HORIZON_WEEK
        request.session["active_horizon"] = horizon
        return redirect("presentation_selector")
    return render(
        request,
        "cohorts/selector.html",
        {"presentations": presentations},
    )


@login_required
@require_http_methods(["GET", "POST"])
def upload_view(request):
    """Upload the seven OULAD files together (UC09). Administrators only."""
    assert_is_admin(request.user)
    result = None
    if request.method == "POST":
        files = request.FILES.getlist("files")
        if not files:
            result = UploadResult(ok=False, error="no files were selected")
        else:
            result = accept_upload(files, request.user)
    return render(
        request,
        "cohorts/upload.html",
        {
            "result": result,
            "required_files": REQUIRED_FILES,
            "current": current_batch(),
            "max_mb": settings.MAX_UPLOAD_BYTES // (1024 * 1024),
        },
    )


@login_required
@require_http_methods(["GET"])
def student_detail(request, student_id):
    """One student's demographics, engagement and current risk (UC04).

    A student with no weekly rows gets demographics and a message. No
    probability is shown for them, because none was computed.
    """
    student = get_object_or_404(
        Student.objects.select_related("presentation"), pk=student_id
    )
    assert_can_view(request.user, student.presentation)

    horizon = resolve_horizon(
        request.GET.get("horizon", request.session.get("active_horizon"))
    )
    weekly = list(
        student.weekly_features.filter(week__lte=horizon).order_by("week")
    )
    has_engagement = bool(weekly)

    model = current_model(horizon)
    score = None
    band = None
    if has_engagement and model is not None:
        score = RiskScore.objects.filter(student=student, model_version=model).first()
        if score is not None:
            cohort = RiskScore.objects.filter(
                student__presentation=student.presentation, model_version=model
            )
            band = band_for_rank(rank_of(score, cohort), cohort.count())

    return render(
        request,
        "cohorts/detail.html",
        {
            "student": student,
            "presentation": student.presentation,
            "horizon": horizon,
            "weekly": weekly,
            "has_engagement": has_engagement,
            "model": model,
            "score": score,
            "band": band,
            "flags": student.flags.select_related("raised_by").order_by("-created_at"),
            "flag_control": flag_control_for(request.user, student),
        },
    )
