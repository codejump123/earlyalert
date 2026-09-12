"""Risk bands. The SRS requires one on four screens and never defines it."""

import pytest

from pipeline.fairness import CAPACITY
from scoring.bands import BANDS, HIGH, LOW, MEDIUM, band_counts, band_for_rank


def test_the_high_band_is_the_capacity_the_fnr_is_measured_at():
    """"In the High band" and "an advisor is expected to reach them" have to be
    the same set, or the fairness number describes a different system."""
    from scoring.bands import HIGH_SHARE

    assert HIGH_SHARE == CAPACITY == 0.10


def test_bands_split_a_cohort_ten_twenty_seventy():
    bands = [band_for_rank(rank, 100) for rank in range(100)]
    counts = band_counts(bands)
    assert counts == {HIGH: 10, MEDIUM: 20, LOW: 70}


def test_the_most_at_risk_student_is_always_high():
    for size in (1, 2, 7, 50, 2000):
        assert band_for_rank(0, size) == HIGH


def test_bands_are_ordered_down_the_ranking():
    ranks = [band_for_rank(rank, 1000) for rank in range(1000)]
    assert ranks[0] == HIGH
    assert ranks[99] == HIGH
    assert ranks[100] == MEDIUM
    assert ranks[299] == MEDIUM
    assert ranks[300] == LOW
    assert ranks[-1] == LOW


def test_an_empty_cohort_does_not_divide_by_zero():
    assert band_for_rank(0, 0) == LOW


def test_band_counts_names_every_band_even_at_zero():
    assert band_counts([]) == {HIGH: 0, MEDIUM: 0, LOW: 0}
    assert list(band_counts([])) == list(BANDS)
