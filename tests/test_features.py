"""Weekly feature construction. Pure pipeline: no Django, no database.

Every expected value below is worked out by hand from the rules in
tests/fixtures/generate.py, not copied from a run. The arithmetic is spelled
out in the comments so a wrong number is a visible wrong step.

The fixture cohort in brief: student k of 25 (ids 1001+k in 2013J, 1026+k in
2014J) is active in weeks 1-12 on days 7(w-1)+(k%3) and the day after, with
5+k clicks each day, except k%5==4 which stops after week 2. k<8 withdraws.
k%4==3 never submits an assessment; a withdrawing student submits only the
first. Assessments are due on days 28, 56 and 229 with weights 30, 30, 40.
"""

from pathlib import Path

import pandas as pd
import pytest

from pipeline.features import WEEK_OFFSET, build_weekly_features, week_end_day, week_of

FIXTURES = Path(__file__).parent / "fixtures"

# Presentation length 268 days -> last day 267 -> week 267//7 + 1 = 39.
LAST_WEEK = 39


@pytest.fixture(scope="module")
def features():
    return build_weekly_features(FIXTURES)


def row(features, id_student, week):
    match = features[
        (features.id_student == id_student) & (features.week == week)
    ]
    assert len(match) == 1, f"expected one row for {id_student} week {week}"
    return match.iloc[0]


# --- week numbering -------------------------------------------------------

def test_week_1_is_days_0_to_6():
    """'Week w covers days 7(w-1) to 7w-1', so day 0 opens week 1."""
    assert week_of(0) == 1
    assert week_of(6) == 1
    assert week_of(7) == 2
    assert week_of(13) == 2


def test_pre_start_activity_lands_at_week_0_or_below():
    """The SRS calls for negative dates to map to week <= 0."""
    assert week_of(-1) == 0
    assert week_of(-7) == 0
    assert week_of(-8) == -1
    assert all(week_of(day) <= 0 for day in range(-40, 0))


def test_week_end_day_is_the_last_day_of_the_week():
    assert week_end_day(1) == 6
    assert week_end_day(2) == 13
    assert week_of(week_end_day(5)) == 5
    assert week_of(week_end_day(5) + 1) == 6


def test_week_offset_is_the_single_place_the_convention_lives():
    assert WEEK_OFFSET == 1


# --- grid shape -----------------------------------------------------------

def test_one_row_per_student_week(features):
    assert not features.duplicated(
        ["code_module", "code_presentation", "id_student", "week"]
    ).any()


def test_grid_runs_to_the_end_of_the_presentation(features):
    """50 students x weeks 1..39, since every student is active in week 1."""
    assert len(features) == 50 * LAST_WEEK
    assert features.week.min() == 1
    assert features.week.max() == LAST_WEEK
    assert features.id_student.nunique() == 50


def test_quiet_weeks_are_present_and_zero(features):
    """Student 1005 (k=4) stops after week 2 but keeps rows to week 39."""
    quiet = features[(features.id_student == 1005) & (features.week > 2)]
    assert len(quiet) == LAST_WEEK - 2
    assert (quiet.total_clicks == 0).all()
    assert (quiet.active_days == 0).all()
    assert (quiet.clicks_per_active_day == 0).all()


def test_a_student_with_no_vle_rows_gets_no_features(features):
    """The detail view's 'no engagement data recorded yet' case."""
    assert 9999 not in set(features.id_student)


# --- clicks and active days ----------------------------------------------

def test_clicks_and_active_days_for_a_known_student(features):
    """k=0: two active days a week at 5 clicks each."""
    week3 = row(features, 1001, 3)
    assert week3.total_clicks == 10        # 5 + 5
    assert week3.active_days == 2          # days 14 and 15
    assert week3.clicks_per_active_day == 5.0


def test_click_volume_scales_with_the_student_index(features):
    """k=10 clicks 15 times a day, so 30 in a week."""
    week3 = row(features, 1011, 3)
    assert week3.total_clicks == 30        # (5 + 10) * 2
    assert week3.active_days == 2
    assert week3.clicks_per_active_day == 15.0


def test_active_days_counts_distinct_days_not_rows(features):
    """Two rows a week, on two distinct days, from one site each."""
    assert (features[features.week.between(1, 2)].active_days == 2).all()


def test_cumulative_clicks_accumulate(features):
    """k=0 adds 10 clicks a week for 12 weeks, then stops."""
    assert row(features, 1001, 1).cum_clicks == 10
    assert row(features, 1001, 4).cum_clicks == 40
    assert row(features, 1001, 12).cum_clicks == 120
    assert row(features, 1001, 39).cum_clicks == 120
    assert row(features, 1001, 12).cum_active_days == 24


# --- days since last activity --------------------------------------------

def test_gap_is_measured_from_the_end_of_the_week(features):
    """k=0 is active on days 0 and 1; week 1 ends on day 6, so the gap is 5."""
    assert row(features, 1001, 1).days_since_last_activity == 5


def test_gap_reflects_the_students_day_offset(features):
    """k=2 is active on days 2 and 3 of each week, so its gap is 3."""
    assert row(features, 1003, 1).days_since_last_activity == 3


def test_gap_grows_once_a_student_goes_quiet(features):
    """k=4 is active on days 7(w-1)+1 and +2, so its last day is 9, in week 2.
    Week w ends on day 7w-1, and the gap grows by 7 a week after that."""
    assert row(features, 1005, 2).days_since_last_activity == 4     # 13 - 9
    assert row(features, 1005, 3).days_since_last_activity == 11    # 20 - 9
    assert row(features, 1005, 4).days_since_last_activity == 18    # 27 - 9
    assert row(features, 1005, 39).days_since_last_activity == 263  # 272 - 9


# --- assessments ----------------------------------------------------------

def test_nothing_is_due_before_the_first_assessment_week(features):
    """Day 28 falls in week 28//7 + 1 = 5, so weeks 1-4 have no assessment."""
    for week in range(1, 5):
        early = row(features, 1001, week)
        assert early.assessments_submitted == 0
        assert pd.isna(early.mean_score)
        assert pd.isna(early.weighted_score_to_date)


def test_first_assessment_counts_from_week_5(features):
    """k=0 submits on day 27, one day early, scoring 40."""
    week5 = row(features, 1001, 5)
    assert week5.assessments_submitted == 1
    assert week5.assessments_late == 0
    assert week5.mean_score == 40.0
    assert week5.weighted_score_to_date == 40.0   # 40*30 / 30


def test_a_late_submission_is_counted_late(features):
    """k=2 submits on day 28 + (2%3) - 1 = 29, one day after the due day."""
    week5 = row(features, 1003, 5)
    assert week5.assessments_submitted == 1
    assert week5.assessments_late == 1


def test_an_unsubmitted_assessment_scores_zero(features):
    """k=0 withdraws, so the day-56 assessment (week 9) is never submitted.
    Two assessments due, scores 40 and 0, weights 30 and 30."""
    week9 = row(features, 1001, 9)
    assert week9.assessments_submitted == 1          # still only the first
    assert week9.mean_score == 20.0                  # (40 + 0) / 2
    assert week9.weighted_score_to_date == 20.0      # (40*30 + 0*30) / 60


def test_a_student_who_never_submits_scores_zero_throughout(features):
    """k=3 submits nothing at all."""
    week5 = row(features, 1004, 5)
    assert week5.assessments_submitted == 0
    assert week5.assessments_late == 0
    assert week5.mean_score == 0.0
    assert week5.weighted_score_to_date == 0.0


def test_scores_carry_forward_between_due_weeks(features):
    """Nothing falls due between weeks 5 and 8, so the value holds."""
    for week in range(5, 9):
        assert row(features, 1001, week).mean_score == 40.0


def test_the_exam_falls_due_in_week_33(features):
    """Day 229 -> week 229//7 + 1 = 33. k=8 submits all three, scoring 56."""
    assert row(features, 1009, 32).mean_score == 56.0            # 2 due
    week33 = row(features, 1009, 33)
    assert week33.assessments_submitted == 3
    assert week33.mean_score == 56.0                             # 3 due, all 56
    assert week33.weighted_score_to_date == 56.0                 # weights 30/30/40


def test_the_exam_pulls_a_withdrawn_students_mean_down(features):
    """k=0 has one score of 40 and two zeroes once the exam falls due."""
    week33 = row(features, 1001, 33)
    assert week33.mean_score == pytest.approx(40 / 3)
    assert week33.weighted_score_to_date == pytest.approx(40 * 30 / 100)


# --- activity type shares -------------------------------------------------

def test_shares_name_the_activity_type_and_sum_to_one(features):
    """Week w uses site SITE_IDS[w % 6]; week 1 is site 101, the homepage."""
    week1 = row(features, 1001, 1)
    assert week1.activity_type_shares == {"homepage": 1.0}
    assert sum(week1.activity_type_shares.values()) == 1.0

    # Week 6 wraps to SITE_IDS[0], forumng.
    assert row(features, 1001, 6).activity_type_shares == {"forumng": 1.0}


def test_a_quiet_week_has_no_shares(features):
    assert row(features, 1005, 10).activity_type_shares == {}


# --- chunking -------------------------------------------------------------

def test_chunk_size_does_not_change_the_result(monkeypatch):
    """The accumulators must be identical however the file is split, including
    a distinct-day count that spans a chunk boundary."""
    baseline = build_weekly_features(FIXTURES)
    monkeypatch.setattr("pipeline.features.VLE_CHUNK_ROWS", 7)
    chunked = build_weekly_features(FIXTURES)
    pd.testing.assert_frame_equal(baseline, chunked)


def test_presentations_filter_restricts_the_output(features):
    only_2013 = build_weekly_features(FIXTURES, [("AAA", "2013J")])
    assert set(only_2013.code_presentation) == {"2013J"}
    assert only_2013.id_student.nunique() == 25
    assert len(only_2013) == 25 * LAST_WEEK


def test_an_unknown_presentation_yields_nothing():
    empty = build_weekly_features(FIXTURES, [("ZZZ", "2099J")])
    assert empty.empty
