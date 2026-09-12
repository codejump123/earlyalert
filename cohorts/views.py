"""Presentation selector (UC02).

Lists only the presentations the authorization service returns for this user
and records the chosen one in the session for the fixed header.
"""

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from accounts.authz import assert_can_view, presentations_for


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
