"""The advisor's intervention workflow (UC05, UC06, UC07).

Each view resolves the student behind the object, puts it through the
authorization service, then hands the request to interventions.services, which
holds the rules. A rule failure comes back as a message on the page the
advisor was already on, never as a 500.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.authz import assert_can_view

from .models import (
    INTERVENTION_TYPE_CHOICES,
    OUTCOME_CHOICES,
    Flag,
    Intervention,
)
from .services import (
    WorkflowError,
    add_note,
    create_flag,
    parse_date,
    record_intervention,
    record_outcome,
)


@login_required
@require_http_methods(["POST"])
def create_flag_view(request, student_id):
    """Raise a flag on one student (UC05)."""
    from cohorts.models import Student

    student = get_object_or_404(
        Student.objects.select_related("presentation"), pk=student_id
    )
    assert_can_view(request.user, student.presentation)
    try:
        flag = create_flag(request.user, student, request.POST.get("reason", ""))
    except WorkflowError as error:
        messages.error(request, str(error))
        return redirect("student_detail", student_id=student.pk)
    messages.success(request, "Flag raised.")
    return redirect("record_intervention", flag_id=flag.pk)


@login_required
@require_http_methods(["GET", "POST"])
def record_intervention_view(request, flag_id):
    """The flag's own page: record contact, add a note, close it (UC06).

    Also carries the note form, because the SRS lists flag_note_added as an
    action but gives it no endpoint; the two are told apart by the submitted
    form's name rather than by adding a URL the SRS does not have.
    """
    flag = get_object_or_404(
        Flag.objects.select_related("student__presentation", "raised_by"), pk=flag_id
    )
    assert_can_view(request.user, flag.student.presentation)

    if request.method == "POST":
        try:
            if "note" in request.POST:
                add_note(request.user, flag, request.POST.get("note", ""))
                messages.success(request, "Note added.")
                if request.POST.get("next") == "detail":
                    return redirect("student_detail", student_id=flag.student_id)
            else:
                record_intervention(
                    request.user,
                    flag,
                    request.POST.get("intervention_type", ""),
                    parse_date(request.POST.get("date", "")),
                    request.POST.get("notes", ""),
                )
                messages.success(request, "Intervention recorded.")
            return redirect("record_intervention", flag_id=flag.pk)
        except WorkflowError as error:
            messages.error(request, str(error))

    interventions = (
        flag.interventions.select_related("advisor")
        .prefetch_related("outcome")
        .order_by("-date", "-id")
    )
    return render(
        request,
        "interventions/flag.html",
        {
            "flag": flag,
            "student": flag.student,
            "interventions": interventions,
            "intervention_types": INTERVENTION_TYPE_CHOICES,
            "outcome_choices": OUTCOME_CHOICES,
            "today": timezone.localdate(),
        },
    )


@login_required
@require_http_methods(["POST"])
def record_outcome_view(request, intervention_id):
    """Record what came of one intervention, closing the flag (UC07)."""
    intervention = get_object_or_404(
        Intervention.objects.select_related("flag__student__presentation"),
        pk=intervention_id,
    )
    assert_can_view(request.user, intervention.flag.student.presentation)
    try:
        record_outcome(request.user, intervention, request.POST.get("outcome", ""))
    except WorkflowError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Outcome recorded; the flag is now closed.")
    return redirect("record_intervention", flag_id=intervention.flag_id)
