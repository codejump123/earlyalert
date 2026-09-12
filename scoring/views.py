"""Ranking, CSV export, dashboard (UC03, UC08) and the retrain trigger (UC11).

Every view that touches a student passes through accounts.authz first.
"""

import csv

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from accounts.authz import assert_can_view, assert_is_admin
from audit.services import record
from cohorts.models import Presentation
from interventions.models import STATUS_OPEN, Flag
from earlyalert.joblock import JOB_LOG_LINES, current_job, read_progress
from pipeline.train import SUBGROUP_DIMS

from .models import ModelVersion, RiskScore
from .bands import BANDS, band_counts, band_for_rank, rank_of
from .ranking import flag_state, ranked_scores, resolve_horizon
from .retrain import JOB_NAME, features_are_stale, retrain
from .summary import (
    DASHBOARD_DIMS,
    dimension_label,
    model_subgroup_levels,
    subgroup_summary,
)


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


@login_required
@require_http_methods(["GET"])
def ranking_view(request, presentation_id):
    """Students ranked by stored risk score (UC03).

    Reads RiskScore rows only. Nothing is scored at request time.
    """
    presentation = get_object_or_404(Presentation, pk=presentation_id)
    assert_can_view(request.user, presentation)

    horizon = resolve_horizon(
        request.GET.get("horizon", request.session.get("active_horizon"))
    )
    model, scores = ranked_scores(presentation, horizon)
    open_flags, resolved = flag_state(presentation)

    paginator = Paginator(scores, settings.RANKING_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    cohort_size = paginator.count
    start = page.start_index() - 1 if cohort_size else 0
    rows = [
        {
            "score": score,
            "student": score.student,
            "band": band_for_rank(start + offset, cohort_size),
            "reason": (score.top_features or [{}])[0].get("text", ""),
            "has_open_flag": score.student_id in open_flags,
            "resolved": score.student_id in resolved,
        }
        for offset, score in enumerate(page.object_list)
    ]
    return render(
        request,
        "scoring/ranking.html",
        {
            "presentation": presentation,
            "horizon": horizon,
            "model": model,
            "page": page,
            "rows": rows,
            "total": paginator.count,
            "horizons_with_models": sorted(
                ModelVersion.objects.filter(is_current=True)
                .values_list("horizon_week", flat=True)
            ),
        },
    )


@login_required
@require_http_methods(["GET"])
def ranking_export(request, presentation_id):
    """The whole ranking as CSV (UC03), not just the page on screen.

    Writes one 'export' audit entry: who took a copy of the cohort's risk
    scores, and when, is exactly the kind of thing the log exists for.
    """
    presentation = get_object_or_404(Presentation, pk=presentation_id)
    assert_can_view(request.user, presentation)

    horizon = resolve_horizon(
        request.GET.get("horizon", request.session.get("active_horizon"))
    )
    model, scores = ranked_scores(presentation, horizon)

    response = HttpResponse(content_type="text/csv")
    filename = f"ranking_{presentation.code_module}_{presentation.code_presentation}_w{horizon}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "id_student", "code_module", "code_presentation", "horizon_week",
            "probability", "risk_band", "final_result", "date_unregistration",
            "reason_1", "reason_2", "reason_3",
        ]
    )
    cohort_size = scores.count()
    for rank, score in enumerate(scores.iterator(chunk_size=500)):
        reasons = [item.get("text", "") for item in (score.top_features or [])]
        reasons += [""] * (3 - len(reasons))
        writer.writerow(
            [
                score.student.id_student,
                presentation.code_module,
                presentation.code_presentation,
                horizon,
                f"{score.probability:.6f}",
                band_for_rank(rank, cohort_size),
                score.student.final_result,
                score.student.date_unregistration
                if score.student.date_unregistration is not None
                else "",
                *reasons[:3],
            ]
        )
    record(
        request.user,
        "export",
        f"ranking:{presentation.code_module}/{presentation.code_presentation}:w{horizon}",
    )
    return response


@login_required
@require_http_methods(["GET"])
def dashboard_view(request, presentation_id):
    """Risk broken down by one demographic dimension (UC08).

    Every level with fewer than MIN_SUBGROUP_N students is named but not
    described. A presentation with fewer than MIN_DASHBOARD_SCORED scored
    students gets no aggregate at all.
    """
    presentation = get_object_or_404(Presentation, pk=presentation_id)
    assert_can_view(request.user, presentation)

    horizon = resolve_horizon(
        request.GET.get("horizon", request.session.get("active_horizon"))
    )
    dimension = request.GET.get("dim", DASHBOARD_DIMS[0])
    if dimension not in DASHBOARD_DIMS:
        dimension = DASHBOARD_DIMS[0]

    model, scores = ranked_scores(presentation, horizon)
    summary = subgroup_summary(scores, dimension)
    scored_total = sum(entry["n"] for entry in summary)

    probabilities = list(scores.values_list("probability", flat=True))
    counts = band_counts(
        band_for_rank(rank, len(probabilities))
        for rank in range(len(probabilities))
    )
    open_flags, _ = flag_state(presentation)
    closed_flags = Flag.objects.filter(
        student__presentation=presentation
    ).exclude(status=STATUS_OPEN).count()
    histogram = _histogram(probabilities)

    too_few = scored_total < settings.MIN_DASHBOARD_SCORED
    reportable = [e for e in summary if not e["suppressed"]]
    all_suppressed = bool(summary) and not reportable

    return render(
        request,
        "scoring/dashboard.html",
        {
            "presentation": presentation,
            "horizon": horizon,
            "model": model,
            "dimension": dimension,
            "dimension_label": dimension_label(dimension),
            "dimensions": [(d, dimension_label(d)) for d in DASHBOARD_DIMS],
            "summary": summary,
            "scored_total": scored_total,
            "too_few": too_few,
            "all_suppressed": all_suppressed,
            "min_n": settings.MIN_SUBGROUP_N,
            "min_scored": settings.MIN_DASHBOARD_SCORED,
            "model_levels": model_subgroup_levels(model, dimension),
            "band_counts": counts,
            "bands": BANDS,
            "open_flags": len(open_flags),
            "closed_flags": closed_flags,
            "histogram": histogram,
            "histogram_max": max((b["n"] for b in histogram), default=1) or 1,
        },
    )


def _histogram(probabilities, bins: int = 10) -> list[dict]:
    """Input: predicted probabilities. Output: one {low, high, n, share} per
    decile of probability, for the distribution UC08 step 4 calls for."""
    total = len(probabilities)
    if not total:
        return []
    counts = [0] * bins
    for value in probabilities:
        index = min(int(value * bins), bins - 1)
        counts[index] += 1
    return [
        {
            "low": index / bins,
            "high": (index + 1) / bins,
            "n": count,
            "share": count / total,
        }
        for index, count in enumerate(counts)
    ]
