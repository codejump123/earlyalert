"""Shared reading of stored risk scores.

Nothing here scores anything: the ranking page and the dashboard both read
RiskScore rows written by the last retrain. A presentation with no current
model, or no scores under it, shows a message rather than an empty table.
"""

from __future__ import annotations

from django.conf import settings

from interventions.models import STATUS_OPEN, STATUS_RESOLVED, Flag

from .models import ModelVersion, RiskScore


def resolve_horizon(value) -> int:
    """Input: a horizon from the query string or session, possibly nonsense.
    Output: a horizon the SRS recognizes, defaulting to week 8."""
    try:
        horizon = int(value)
    except (TypeError, ValueError):
        return settings.DEFAULT_HORIZON_WEEK
    if horizon not in settings.HORIZON_WEEKS:
        return settings.DEFAULT_HORIZON_WEEK
    return horizon


def current_model(horizon_week: int) -> ModelVersion | None:
    """Output: the model marked current at this horizon, or None."""
    return ModelVersion.objects.filter(
        horizon_week=horizon_week, is_current=True
    ).first()


def ranked_scores(presentation, horizon_week: int):
    """Input: a Presentation and a horizon.
    Output: (model, queryset of RiskScore ordered most at risk first).

    The queryset is empty when no current model exists for the horizon.
    """
    model = current_model(horizon_week)
    if model is None:
        return None, RiskScore.objects.none()
    scores = (
        RiskScore.objects.filter(
            student__presentation=presentation, model_version=model
        )
        .select_related("student")
        .order_by("-probability", "student__id_student")
    )
    return model, scores


def flag_state(presentation) -> tuple[set[int], set[int]]:
    """Output: (student ids with an open flag, student ids with a flag closed
    as resolved) for this presentation.

    Both sets are read once for a whole page rather than per row.
    """
    flags = Flag.objects.filter(student__presentation=presentation)
    open_flags = set(
        flags.filter(status=STATUS_OPEN).values_list("student_id", flat=True)
    )
    resolved = set(
        flags.filter(status=STATUS_RESOLVED).values_list("student_id", flat=True)
    )
    return open_flags, resolved
