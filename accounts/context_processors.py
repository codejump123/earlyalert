"""Template context for the fixed header: active presentation and horizon."""

from django.conf import settings

from .authz import presentations_for


def active_context(request):
    """Expose active_presentation, active_horizon and horizon_weeks."""
    presentation = None
    pk = request.session.get("active_presentation_id")
    if pk and getattr(request.user, "is_authenticated", False):
        presentation = (
            presentations_for(request.user).filter(pk=pk).first()
        )
        if presentation is None:
            # Assignment was revoked since the presentation was selected.
            request.session.pop("active_presentation_id", None)
    horizon = request.session.get("active_horizon", settings.DEFAULT_HORIZON_WEEK)
    try:
        horizon = int(request.GET.get("horizon", horizon))
    except (TypeError, ValueError):
        horizon = settings.DEFAULT_HORIZON_WEEK
    if horizon not in settings.HORIZON_WEEKS:
        horizon = settings.DEFAULT_HORIZON_WEEK
    return {
        "active_presentation": presentation,
        "active_horizon": horizon,
        "horizon_weeks": settings.HORIZON_WEEKS,
        "user_role": getattr(getattr(request.user, "profile", None), "role", ""),
    }
