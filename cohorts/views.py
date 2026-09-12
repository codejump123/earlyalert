"""Presentation selector (UC02) and OULAD upload (UC09).

Lists only the presentations the authorization service returns for this user
and records the chosen one in the session for the fixed header.
"""

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from accounts.authz import assert_can_view, assert_is_admin, presentations_for
from pipeline.validate import REQUIRED_FILES

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
