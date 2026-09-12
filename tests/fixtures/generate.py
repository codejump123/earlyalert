"""Generate the synthetic 50-student fixture cohort.

Run with `python tests/fixtures/generate.py` to rewrite the seven CSV files in
this directory. The output is fully deterministic — no randomness, no seed —
so expected values in the tests can be worked out by hand from the rules
below rather than copied from a previous run.

No real OULAD rows appear here. The shape imitates OULAD (same columns, same
'?' for missing, same day-0-relative integer dates) at 1/650th the size.

Cohort
    Two presentations of one module: AAA/2013J (length 268) and AAA/2014J
    (length 269), so the train-on-2013 / test-on-2014 split has both sides.
    Ids 1001-1025 sit in 2013J, 1026-1050 in 2014J. Within a presentation a
    student is addressed by its index k = 0..24.

Withdrawal
    k < 8 withdraws (8 of 25 = 32% per presentation, near OULAD's 31.2%).
    A withdrawing student unregisters on day 7*(k+1), so unregistration lands
    on days 7..56 and straddles the week 4, 8 and 12 horizons.

Engagement
    k % 5 == 4 is the disengaged pattern: activity in weeks 1-2 only.
    Everyone else is active in weeks 1-12, on two days per week:
    7*(w-1) + (k % 3) and the day after, with 5 + k clicks each day, on site
    100 + (w % 6).

Assessments
    Three per presentation: TMA due day 28 (weight 30), TMA due day 56
    (weight 30), exam due day 229 (weight 40).
    k % 4 == 3 never submits anything. A withdrawing student submits only the
    first assessment. Everyone else submits all three, on due day + (k % 3) -
    1, so k % 3 == 2 is one day late. Score is 40 + 2*k.
"""

from __future__ import annotations

import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent

PRESENTATIONS = [("AAA", "2013J", 268, 1001), ("AAA", "2014J", 269, 1026)]
STUDENTS_PER_PRESENTATION = 25

REGIONS = ["East Anglian Region", "Scotland", "London Region"]
EDUCATION = ["HE Qualification", "A Level or Equivalent", "Lower Than A Level"]
AGE_BANDS = ["0-35", "35-55", "55<="]
IMD_BANDS = [
    "0-10%", "10-20", "20-30%", "30-40%", "40-50%",
    "50-60%", "60-70%", "70-80%", "80-90%", "90-100%",
]
NON_WITHDRAWN = ["Pass", "Fail", "Distinction"]

ACTIVITY_TYPES = ["forumng", "homepage", "oucontent", "resource", "subpage", "url"]
SITE_IDS = [100, 101, 102, 103, 104, 105]

ASSESSMENT_PLAN = [("TMA", 28, 30.0), ("TMA", 56, 30.0), ("Exam", 229, 40.0)]
WEEKS = 12
MISSING = "?"


def withdraws(k: int) -> bool:
    return k < 8


def disengaged(k: int) -> bool:
    return k % 5 == 4


def submits(k: int) -> bool:
    return k % 4 != 3


def _write(name: str, header: list[str], rows: list[list]) -> None:
    with (HERE / name).open("w", newline="", encoding="utf-8") as handle:
        # LF, not csv's default CRLF, so the files are byte-identical
        # on every platform and their checksums are stable.
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
    print(f"{name}: {len(rows)} rows")


def build_courses():
    return [[m, p, length] for m, p, length, _ in PRESENTATIONS]


def build_assessments():
    rows = []
    assessment_id = 1
    for module, presentation, _, _ in PRESENTATIONS:
        for kind, due, weight in ASSESSMENT_PLAN:
            rows.append([module, presentation, assessment_id, kind, due, weight])
            assessment_id += 1
    return rows


def build_vle():
    rows = []
    for module, presentation, _, _ in PRESENTATIONS:
        for offset, (site, activity) in enumerate(zip(SITE_IDS, ACTIVITY_TYPES)):
            rows.append([site, module, presentation, activity, 1, WEEKS])
    return rows


def build_student_info():
    rows = []
    for module, presentation, _, first_id in PRESENTATIONS:
        for k in range(STUDENTS_PER_PRESENTATION):
            # k == 7 has no IMD band, so the '?' path is exercised.
            imd = MISSING if k == 7 else IMD_BANDS[k % 10]
            result = "Withdrawn" if withdraws(k) else NON_WITHDRAWN[k % 3]
            rows.append([
                module, presentation, first_id + k,
                "M" if k % 2 == 0 else "F",
                REGIONS[k % 3],
                EDUCATION[k % 3],
                imd,
                AGE_BANDS[k % 3],
                k % 3,
                60 + 30 * (k % 3),
                "Y" if k % 5 == 0 else "N",
                result,
            ])
    return rows


def build_student_registration():
    rows = []
    for module, presentation, _, first_id in PRESENTATIONS:
        for k in range(STUDENTS_PER_PRESENTATION):
            unregistration = 7 * (k + 1) if withdraws(k) else MISSING
            rows.append([
                module, presentation, first_id + k, -30 + k, unregistration
            ])
    return rows


def build_student_assessment():
    rows = []
    assessment_id = 1
    for _, _, _, first_id in PRESENTATIONS:
        ids = list(range(assessment_id, assessment_id + len(ASSESSMENT_PLAN)))
        assessment_id += len(ASSESSMENT_PLAN)
        for k in range(STUDENTS_PER_PRESENTATION):
            if not submits(k):
                continue
            # A withdrawing student gets no further than the first assessment.
            taken = ids[:1] if withdraws(k) else ids
            for index, aid in enumerate(taken):
                due = ASSESSMENT_PLAN[index][1]
                rows.append([
                    aid, first_id + k, due + (k % 3) - 1, 0, min(40 + 2 * k, 100)
                ])
    return rows


def build_student_vle():
    rows = []
    for module, presentation, _, first_id in PRESENTATIONS:
        for k in range(STUDENTS_PER_PRESENTATION):
            last_week = 2 if disengaged(k) else WEEKS
            for week in range(1, last_week + 1):
                site = SITE_IDS[week % len(SITE_IDS)]
                first_day = 7 * (week - 1) + (k % 3)
                for day in (first_day, first_day + 1):
                    rows.append([
                        module, presentation, first_id + k, site, day, 5 + k
                    ])
    return rows


def main() -> None:
    _write("courses.csv",
           ["code_module", "code_presentation", "module_presentation_length"],
           build_courses())
    _write("assessments.csv",
           ["code_module", "code_presentation", "id_assessment",
            "assessment_type", "date", "weight"],
           build_assessments())
    _write("vle.csv",
           ["id_site", "code_module", "code_presentation", "activity_type",
            "week_from", "week_to"],
           build_vle())
    _write("studentInfo.csv",
           ["code_module", "code_presentation", "id_student", "gender", "region",
            "highest_education", "imd_band", "age_band", "num_of_prev_attempts",
            "studied_credits", "disability", "final_result"],
           build_student_info())
    _write("studentRegistration.csv",
           ["code_module", "code_presentation", "id_student",
            "date_registration", "date_unregistration"],
           build_student_registration())
    _write("studentAssessment.csv",
           ["id_assessment", "id_student", "date_submitted", "is_banked", "score"],
           build_student_assessment())
    _write("studentVle.csv",
           ["code_module", "code_presentation", "id_student", "id_site", "date",
            "sum_click"],
           build_student_vle())


if __name__ == "__main__":
    main()
