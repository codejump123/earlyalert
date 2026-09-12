"""Reading the stored cohort back out of the ORM for training.

pipeline/ takes DataFrames and knows nothing about Django; this is the seam.
The column names match what pipeline.loader produces from the CSV files, so
build_cohort cannot tell which side the data came from.
"""

from __future__ import annotations

import pandas as pd

from cohorts.models import Student
from features.models import WeeklyFeatures

STUDENT_COLUMNS = [
    "code_module",
    "code_presentation",
    "id_student",
    "gender",
    "region",
    "highest_education",
    "imd_band",
    "age_band",
    "num_of_prev_attempts",
    "studied_credits",
    "disability",
    "final_result",
]


def students_frame() -> pd.DataFrame:
    """Output: one row per registration, shaped like studentInfo.csv.

    disability comes back as 'Y'/'N' rather than a boolean so that the frame
    is interchangeable with the one read from the file.
    """
    rows = Student.objects.values_list(
        "presentation__code_module",
        "presentation__code_presentation",
        "id_student",
        "gender",
        "region",
        "highest_education",
        "imd_band",
        "age_band",
        "num_prev_attempts",
        "studied_credits",
        "disability",
        "final_result",
    )
    frame = pd.DataFrame(rows, columns=STUDENT_COLUMNS)
    if not frame.empty:
        frame["disability"] = frame["disability"].map({True: "Y", False: "N"})
    return frame


def registrations_frame() -> pd.DataFrame:
    """Output: one row per registration, shaped like studentRegistration.csv."""
    rows = Student.objects.values_list(
        "presentation__code_module",
        "presentation__code_presentation",
        "id_student",
        "date_registration",
        "date_unregistration",
    )
    frame = pd.DataFrame(
        rows,
        columns=[
            "code_module",
            "code_presentation",
            "id_student",
            "date_registration",
            "date_unregistration",
        ],
    )
    for column in ("date_registration", "date_unregistration"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    return frame


def features_frame(max_week: int) -> pd.DataFrame:
    """Input: the largest horizon that will be trained.
    Output: WeeklyFeatures up to that week, shaped like build_weekly_features.

    Only the weeks a horizon can see are read: at week 12 that is a tenth of
    the stored rows.
    """
    rows = WeeklyFeatures.objects.filter(week__lte=max_week).values_list(
        "student__presentation__code_module",
        "student__presentation__code_presentation",
        "student__id_student",
        "week",
        "total_clicks",
        "active_days",
        "clicks_per_active_day",
        "days_since_last_activity",
        "assessments_submitted",
        "assessments_late",
        "mean_score",
        "weighted_score_to_date",
    )
    return pd.DataFrame(
        rows,
        columns=[
            "code_module",
            "code_presentation",
            "id_student",
            "week",
            "total_clicks",
            "active_days",
            "clicks_per_active_day",
            "days_since_last_activity",
            "assessments_submitted",
            "assessments_late",
            "mean_score",
            "weighted_score_to_date",
        ],
    )


def student_pk_lookup() -> dict[tuple[str, str, int], int]:
    """Output: {(code_module, code_presentation, id_student): Student.pk}."""
    return {
        (module, presentation, id_student): pk
        for pk, module, presentation, id_student in Student.objects.values_list(
            "pk",
            "presentation__code_module",
            "presentation__code_presentation",
            "id_student",
        )
    }
