"""Weekly per-student feature construction from the seven OULAD files.

No Django imports. studentVle.csv is read in chunks and never held whole.

Week numbering
--------------
Week w covers days 7(w-1) to 7w-1, so week 1 is days 0-6 and pre-start
activity falls in week 0 or below. That makes the mapping

    week = date // 7 + 1

The SRS text for this module says ``week = date // 7``, which disagrees with
its own two other statements about weeks: it would put day 0 in week 0 rather
than week 1, and would map negative dates to week <= -1 rather than the
"week <= 0" the same paragraph calls for. The definition above is the one that
satisfies both, and it is also what vle.csv's 1-based week_from/week_to assume.
Recorded in DECISIONS.md; if the SRS means the other thing, WEEK_OFFSET is the
single place to change.

Grid
----
One row per (code_module, code_presentation, id_student, week), for every week
from the student's first week of recorded activity through the last week of
the presentation. Weeks after a student goes quiet are therefore present and
carry zero clicks, which is the point: a rising days_since_last_activity is
the signal. A student with no studentVle rows at all gets no rows here, which
is what the detail view's "No engagement data recorded yet" reports on.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .loader import (
    VLE_CHUNK_ROWS,
    iter_student_vle,
    read_assessments,
    read_courses,
    read_student_assessment,
    read_vle,
)

logger = logging.getLogger("pipeline.features")

# week = date // 7 + WEEK_OFFSET. See the module docstring.
WEEK_OFFSET = 1

KEY = ["code_module", "code_presentation", "id_student", "week"]
PRESENTATION_KEY = ["code_module", "code_presentation"]
STUDENT_KEY = ["code_module", "code_presentation", "id_student"]

OUTPUT_COLUMNS = [
    *KEY,
    "total_clicks",
    "active_days",
    "clicks_per_active_day",
    "days_since_last_activity",
    "assessments_submitted",
    "assessments_late",
    "mean_score",
    "weighted_score_to_date",
    "activity_type_shares",
    "cum_clicks",
    "cum_active_days",
]


def week_of(date: pd.Series | int):
    """Input: day offsets from presentation start. Output: week numbers.

    Floor division, so day -1 and day -7 both fall in week 0 and day 0 in
    week 1.
    """
    if isinstance(date, pd.Series):
        return (date // 7 + WEEK_OFFSET).astype("int64")
    return date // 7 + WEEK_OFFSET


def week_end_day(week: pd.Series | int):
    """Output: the last day offset belonging to each week."""
    return 7 * week - 1


def build_weekly_features(
    upload_dir: Path,
    presentations: list[tuple[str, str]] | None = None,
) -> pd.DataFrame:
    """Input: a directory of the seven validated OULAD files, and optionally
    the (code_module, code_presentation) pairs to restrict to.

    Output: a DataFrame with one row per (code_module, code_presentation,
    id_student, week), carrying the columns in OUTPUT_COLUMNS.

    Progress — chunk number, rows read, elapsed seconds — is logged as it
    goes; that log is what the rebuild view shows.
    """
    upload_dir = Path(upload_dir)
    started = time.monotonic()
    wanted = set(presentations) if presentations else None

    courses = read_courses(upload_dir / "courses.csv")
    if wanted:
        courses = courses[
            courses.apply(
                lambda r: (r.code_module, r.code_presentation) in wanted, axis=1
            )
        ]
    if courses.empty:
        logger.warning("no presentations selected; nothing to build")
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    sites = _site_activity_types(upload_dir / "vle.csv", wanted)
    clicks, days, types = _aggregate_vle(upload_dir / "studentVle.csv", wanted, sites)
    if clicks.empty:
        logger.warning("no VLE activity found; nothing to build")
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    grid = _dense_grid(clicks, courses)
    grid = _engagement_columns(grid, clicks, days)
    grid = grid.merge(_activity_shares(types), on=KEY, how="left")
    grid["activity_type_shares"] = grid["activity_type_shares"].where(
        grid["activity_type_shares"].notna(), None
    )
    grid["activity_type_shares"] = [
        {} if shares is None else shares for shares in grid["activity_type_shares"]
    ]

    grid = _assessment_columns(grid, upload_dir, wanted)

    grid = grid[OUTPUT_COLUMNS].sort_values(KEY, ignore_index=True)
    logger.info(
        "features built: %d rows for %d students in %.1fs",
        len(grid),
        grid.groupby(STUDENT_KEY, observed=True).ngroups,
        time.monotonic() - started,
    )
    return grid


# --- studentVle: the chunked pass ----------------------------------------

def _site_activity_types(vle_path: Path, wanted) -> pd.DataFrame:
    """Output: (code_module, code_presentation, id_site, activity_type)."""
    sites = read_vle(vle_path)[
        ["code_module", "code_presentation", "id_site", "activity_type"]
    ]
    if wanted:
        sites = _restrict(sites, wanted)
    return sites


def _restrict(frame: pd.DataFrame, wanted: set[tuple[str, str]]) -> pd.DataFrame:
    pairs = pd.MultiIndex.from_frame(frame[PRESENTATION_KEY])
    return frame[pairs.isin(wanted)]


def _aggregate_vle(
    path: Path, wanted, sites: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Read studentVle.csv in chunks and accumulate three partial sums.

    Output:
      clicks  (KEY, total_clicks)
      days    (KEY, active_days, last_activity_day)
      types   (KEY + activity_type, clicks)

    Each accumulator is re-aggregated after every chunk, so its size is bounded
    by the number of distinct groups rather than by the number of rows read.
    The file is never held whole; peak memory is one chunk plus the three
    accumulators.
    """
    clicks_acc: pd.DataFrame | None = None
    days_acc: pd.DataFrame | None = None
    types_acc: pd.DataFrame | None = None
    rows_read = 0
    started = time.monotonic()

    for number, chunk in enumerate(iter_student_vle(path, VLE_CHUNK_ROWS), start=1):
        rows_read += len(chunk)
        if wanted:
            chunk = _restrict(chunk, wanted)
        if chunk.empty:
            continue
        chunk["week"] = week_of(chunk["date"])

        part = chunk.groupby(KEY, observed=True, as_index=False)["sum_click"].sum()
        part = part.rename(columns={"sum_click": "total_clicks"})
        clicks_acc = _accumulate(clicks_acc, part, KEY, {"total_clicks": "sum"})

        # Distinct days must be deduplicated across chunk boundaries: one
        # student-day appears once per site visited that day.
        seen = chunk[[*STUDENT_KEY, "week", "date"]].drop_duplicates()
        days_acc = _accumulate_days(days_acc, seen)

        typed = chunk.merge(
            sites, on=["code_module", "code_presentation", "id_site"], how="left"
        )
        typed["activity_type"] = typed["activity_type"].fillna("unknown")
        part = typed.groupby(
            [*KEY, "activity_type"], observed=True, as_index=False
        )["sum_click"].sum()
        types_acc = _accumulate(
            types_acc, part, [*KEY, "activity_type"], {"sum_click": "sum"}
        )

        logger.info(
            "chunk %d: %d rows read, %d student-weeks so far, %.1fs elapsed",
            number,
            rows_read,
            len(clicks_acc),
            time.monotonic() - started,
        )

    empty = pd.DataFrame(columns=KEY)
    if clicks_acc is None:
        return empty, empty, empty

    days_acc = (
        days_acc.groupby(KEY, observed=True)
        .agg(active_days=("date", "size"), last_activity_day=("date", "max"))
        .reset_index()
    )
    logger.info(
        "studentVle read: %d rows in %d chunks, %.1fs",
        rows_read,
        number,
        time.monotonic() - started,
    )
    return clicks_acc, days_acc, types_acc


def _accumulate(acc, part, keys, aggs):
    if acc is None:
        return part
    combined = pd.concat([acc, part], ignore_index=True)
    return combined.groupby(keys, observed=True, as_index=False).agg(aggs)


def _accumulate_days(acc, seen):
    if acc is None:
        return seen
    return pd.concat([acc, seen], ignore_index=True).drop_duplicates()


# --- the dense weekly grid ------------------------------------------------

def _dense_grid(clicks: pd.DataFrame, courses: pd.DataFrame) -> pd.DataFrame:
    """Input: the per-student-week click totals and courses.csv.
    Output: KEY rows for every week from each student's first recorded week
    through the last week of their presentation."""
    last_week = courses.assign(
        last_week=week_of(courses["module_presentation_length"] - 1)
    )[[*PRESENTATION_KEY, "last_week"]]

    span = (
        clicks.groupby(STUDENT_KEY, observed=True, as_index=False)["week"]
        .min()
        .rename(columns={"week": "first_week"})
        .merge(last_week, on=PRESENTATION_KEY, how="left")
    )
    # A student whose activity runs past the published length still gets rows
    # for those weeks.
    observed_last = (
        clicks.groupby(STUDENT_KEY, observed=True, as_index=False)["week"]
        .max()
        .rename(columns={"week": "observed_last_week"})
    )
    span = span.merge(observed_last, on=STUDENT_KEY, how="left")
    span["last_week"] = span[["last_week", "observed_last_week"]].max(axis=1)

    counts = (span["last_week"] - span["first_week"] + 1).astype("int64")
    grid = span.loc[span.index.repeat(counts)].copy()
    grid["week"] = grid["first_week"] + grid.groupby(STUDENT_KEY, observed=True).cumcount()
    return grid[KEY].reset_index(drop=True)


def _engagement_columns(
    grid: pd.DataFrame, clicks: pd.DataFrame, days: pd.DataFrame
) -> pd.DataFrame:
    """Add total_clicks, active_days, clicks_per_active_day,
    days_since_last_activity and the cumulative click columns."""
    grid = grid.merge(clicks, on=KEY, how="left").merge(days, on=KEY, how="left")
    grid["total_clicks"] = grid["total_clicks"].fillna(0).astype("int64")
    grid["active_days"] = grid["active_days"].fillna(0).astype("int64")
    grid["clicks_per_active_day"] = np.where(
        grid["active_days"] > 0,
        grid["total_clicks"] / grid["active_days"].replace(0, 1),
        0.0,
    )

    # Carry the most recent activity day forward into quiet weeks, then measure
    # the gap from the end of the week.
    grid = grid.sort_values(KEY, ignore_index=True)
    by_student = grid.groupby(STUDENT_KEY, observed=True)
    last_seen = by_student["last_activity_day"].ffill()
    grid["days_since_last_activity"] = (
        week_end_day(grid["week"]) - last_seen
    ).astype("int64")

    grid["cum_clicks"] = by_student["total_clicks"].cumsum()
    grid["cum_active_days"] = by_student["active_days"].cumsum()
    return grid.drop(columns=["last_activity_day"])


def _activity_shares(types: pd.DataFrame) -> pd.DataFrame:
    """Input: clicks per (KEY, activity_type).
    Output: KEY rows with activity_type_shares, a {activity_type: share} dict
    whose values sum to 1 for any week with clicks."""
    if types.empty:
        return pd.DataFrame(columns=[*KEY, "activity_type_shares"])
    totals = types.groupby(KEY, observed=True)["sum_click"].transform("sum")
    types = types.assign(share=(types["sum_click"] / totals).round(4))
    types = types[types["sum_click"] > 0]
    shares = (
        types.groupby(KEY, observed=True)
        .apply(
            lambda part: dict(zip(part["activity_type"], part["share"])),
            include_groups=False,
        )
        .rename("activity_type_shares")
        .reset_index()
    )
    return shares


# --- assessments ----------------------------------------------------------

def _assessment_columns(
    grid: pd.DataFrame, upload_dir: Path, wanted
) -> pd.DataFrame:
    """Add assessments_submitted, assessments_late, mean_score and
    weighted_score_to_date, each cumulative to the end of the week.

    An assessment whose due date has passed with no submission counts as a
    score of 0, so mean_score falls when work is skipped rather than staying
    silent. An assessment with no due date in the file (the exam, in some
    presentations) is never "due by week w" and is excluded.

    mean_score is the unweighted mean over assessments due to date;
    weighted_score_to_date divides by the weight due to date, so it reads as a
    running weighted average out of 100 and is comparable across weeks.
    """
    assessments = read_assessments(upload_dir / "assessments.csv")
    if wanted:
        assessments = _restrict(assessments, wanted)
    assessments = assessments[assessments["date"].notna()].copy()
    assessments["due_day"] = assessments["date"].astype("int64")
    assessments["due_week"] = week_of(assessments["due_day"])

    submissions = read_student_assessment(upload_dir / "studentAssessment.csv")

    students = grid[STUDENT_KEY].drop_duplicates()
    # Every student owes every assessment of their presentation.
    due = students.merge(
        assessments[[*PRESENTATION_KEY, "id_assessment", "due_day", "due_week", "weight"]],
        on=PRESENTATION_KEY,
        how="inner",
    )
    due = due.merge(
        submissions[["id_assessment", "id_student", "date_submitted", "score"]],
        on=["id_assessment", "id_student"],
        how="left",
    )
    due["submitted"] = due["date_submitted"].notna().astype("int64")
    due["late"] = (
        due["date_submitted"].notna() & (due["date_submitted"] > due["due_day"])
    ).astype("int64")
    # Unsubmitted counts as zero, and so does a submission with no recorded score.
    due["score_filled"] = due["score"].fillna(0.0)
    due["weighted"] = due["score_filled"] * due["weight"]

    per_due_week = (
        due.groupby([*STUDENT_KEY, "due_week"], observed=True)
        .agg(
            n_submitted=("submitted", "sum"),
            n_late=("late", "sum"),
            n_due=("id_assessment", "size"),
            sum_score=("score_filled", "sum"),
            sum_weighted=("weighted", "sum"),
            sum_weight=("weight", "sum"),
        )
        .reset_index()
        .sort_values([*STUDENT_KEY, "due_week"])
    )
    cumulative = per_due_week.groupby(STUDENT_KEY, observed=True)[
        ["n_submitted", "n_late", "n_due", "sum_score", "sum_weighted", "sum_weight"]
    ].cumsum()
    per_due_week = pd.concat(
        [per_due_week[[*STUDENT_KEY, "due_week"]], cumulative], axis=1
    )

    # For each grid week, take the newest due_week that is not in the future.
    grid = grid.sort_values("week", ignore_index=True)
    per_due_week = per_due_week.sort_values("due_week", ignore_index=True)
    merged = pd.merge_asof(
        grid,
        per_due_week,
        left_on="week",
        right_on="due_week",
        by=STUDENT_KEY,
        direction="backward",
    )

    merged["assessments_submitted"] = merged["n_submitted"].fillna(0).astype("int64")
    merged["assessments_late"] = merged["n_late"].fillna(0).astype("int64")
    merged["mean_score"] = np.where(
        merged["n_due"].fillna(0) > 0, merged["sum_score"] / merged["n_due"], np.nan
    )
    merged["weighted_score_to_date"] = np.where(
        merged["sum_weight"].fillna(0) > 0,
        merged["sum_weighted"] / merged["sum_weight"],
        np.nan,
    )
    return merged.drop(
        columns=[
            "due_week", "n_submitted", "n_late", "n_due",
            "sum_score", "sum_weighted", "sum_weight",
        ]
    )
