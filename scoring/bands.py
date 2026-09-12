"""Risk bands.

The SRS requires a risk band on the ranking row, the student detail view, the
CSV export and the dashboard, and never says what one is. This module is the
definition.

Bands are positions within the cohort, not fixed probability cutoffs. Two
reasons. The base rate moves with the horizon — a fifth of the week 4 cohort
withdraws against an eighth of the week 12 cohort — so a fixed cutoff would
band the same student differently at different horizons for no reason to do
with that student. And the SRS mandates balanced class weights, under which a
model's probabilities are systematically inflated: an absolute "High = 0.70"
would put a third of a cohort in the top band and tell an advisor nothing.

The High band is the top CAPACITY of the cohort, the same operating point the
false-negative rate in pipeline.fairness is measured at, so "students in the
High band" and "students an advisor is expected to reach" are the same set.
"""

from __future__ import annotations

from pipeline.fairness import CAPACITY

HIGH = "High"
MEDIUM = "Medium"
LOW = "Low"
BANDS = (HIGH, MEDIUM, LOW)

# Top 10% High, next 20% Medium, remaining 70% Low.
HIGH_SHARE = CAPACITY
MEDIUM_SHARE = 0.30


def band_for_rank(rank: int, cohort_size: int) -> str:
    """Input: a student's 0-based rank by descending probability, and the size
    of the scored cohort. Output: one of BANDS.

    Rank 0 is the most at risk. A cohort of one is a High band of one, which is
    what "the top tenth, at least one student" has to mean.
    """
    if cohort_size <= 0:
        return LOW
    position = (rank + 1) / cohort_size
    if position <= HIGH_SHARE or rank == 0:
        return HIGH
    if position <= MEDIUM_SHARE:
        return MEDIUM
    return LOW


def band_counts(bands) -> dict[str, int]:
    """Input: an iterable of band names. Output: a count per band, in order."""
    counts = {name: 0 for name in BANDS}
    for name in bands:
        counts[name] = counts.get(name, 0) + 1
    return counts


def rank_of(score, scores_queryset) -> int:
    """Input: one RiskScore and the queryset of its cohort at that version.
    Output: the 0-based rank of the score by descending probability.

    Ties are broken by student id, matching the ranking page's ordering, so a
    student's band is the same whichever page it is read from.
    """
    return scores_queryset.filter(probability__gt=score.probability).count() + (
        scores_queryset.filter(
            probability=score.probability,
            student__id_student__lt=score.student.id_student,
        ).count()
    )
