"""The experiment script. Pure pipeline: no Django, no database.

Run against the synthetic fixture cohort, which is deliberately small: it has
withdrawals at the week 4 horizon only, so it also exercises the path where a
later horizon has no usable split.
"""

import csv
from pathlib import Path

import pandas as pd

from pipeline.experiment import (
    CLASSIFIERS,
    GRID_COLUMNS,
    SETS,
    run_experiment,
)

FIXTURES = Path(__file__).parent / "fixtures"


def read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def run(tmp_path, **kwargs):
    return run_experiment(FIXTURES, tmp_path, **kwargs), tmp_path


def test_the_grid_has_one_row_per_cell(tmp_path):
    grid, _ = run(tmp_path, horizons=(4,))
    assert len(grid) == len(SETS) * len(CLASSIFIERS)
    assert set(grid.feature_set) == set(SETS)
    assert set(grid.classifier) == set(CLASSIFIERS)
    assert not grid.duplicated(["feature_set", "horizon_week", "classifier"]).any()


def test_the_majority_baseline_is_not_a_cell_of_the_grid(tmp_path):
    """It ignores the features, so it has one value per horizon, not per cell."""
    grid, out = run(tmp_path, horizons=(4,))
    assert "majority" not in set(grid.classifier)
    baselines = read_rows(out / "baselines.csv")
    assert len(baselines) == 1
    assert baselines[0]["classifier"] == "majority"


def test_grid_csv_is_written_with_the_documented_columns(tmp_path):
    _, out = run(tmp_path, horizons=(4,))
    rows = read_rows(out / "grid.csv")
    assert list(rows[0]) == GRID_COLUMNS
    assert len(rows) == len(SETS) * len(CLASSIFIERS)


def test_every_cell_carries_its_baseline_and_lift(tmp_path):
    """The Phase 4 finding, enforced: AUC-PR alone cannot be compared across
    horizons, so no row is written without the floor it sits on."""
    grid, _ = run(tmp_path, horizons=(4,))
    assert grid["baseline_auc_pr"].notna().all()
    assert grid["auc_pr_lift"].notna().all()
    expected = grid["auc_pr"] / grid["baseline_auc_pr"]
    pd.testing.assert_series_equal(
        grid["auc_pr_lift"], expected, check_names=False
    )


def test_the_all_set_is_the_sum_of_the_others(tmp_path):
    grid, _ = run(tmp_path, horizons=(4,))
    widths = grid.groupby("feature_set")["n_features"].first()
    assert widths["All"] == widths["D"] + widths["A"] + widths["E"]


def test_a_horizon_with_no_usable_split_is_skipped_not_fatal(tmp_path):
    """The fixture's withdrawals are all gone by week 8, leaving one class."""
    grid, out = run(tmp_path, horizons=(4, 8))
    assert set(grid.horizon_week) == {4}
    assert len(grid) == len(SETS) * len(CLASSIFIERS)
    note = (out / "README.txt").read_text()
    assert "Horizons with no usable split" in note
    assert "week 8" in note


def test_subgroup_csv_is_written_per_horizon_with_suppression(tmp_path):
    _, out = run(tmp_path, horizons=(4,))
    rows = read_rows(out / "subgroups_w4.csv")
    assert rows
    assert list(rows[0]) == [
        "dimension", "level", "n", "suppressed", "positives", "positive_rate",
        "auc_roc", "recall_at_p50", "dimension_gap",
    ]
    # The fixture is tiny, so every level is below the floor of 20.
    assert {row["suppressed"] for row in rows} == {"true"}
    for row in rows:
        assert row["auc_roc"] == ""
        assert row["positive_rate"] == ""


def test_the_figures_are_written(tmp_path):
    _, out = run(tmp_path, horizons=(4,))
    figure = out / "fig_aucpr_vs_horizon.png"
    assert figure.exists()
    assert figure.stat().st_size > 5000


def test_the_readme_explains_how_to_compare_horizons(tmp_path):
    _, out = run(tmp_path, horizons=(4,))
    note = (out / "README.txt").read_text()
    assert "Compare horizons on auc_pr_lift, not on" in note
    assert "Best cell by AUC-PR at each horizon" in note
    assert "train on 2013" in note.lower()


def test_modules_with_no_training_year_are_named(tmp_path):
    """The fixture has both years for AAA, so nothing should be listed."""
    _, out = run(tmp_path, horizons=(4,))
    assert "no training data" not in (out / "README.txt").read_text()


def test_the_experiment_is_reproducible(tmp_path):
    """Every metric is identical between runs. fit_seconds is excluded: it is a
    measurement of this machine, not a result of the experiment."""
    first, _ = run(tmp_path / "a", horizons=(4,))
    second, _ = run(tmp_path / "b", horizons=(4,))
    columns = [c for c in GRID_COLUMNS if c != "fit_seconds"]
    pd.testing.assert_frame_equal(first[columns], second[columns])


def test_a_module_with_no_training_year_is_excluded_from_evaluation(tmp_path):
    """The SRS default for CCC, enforced.

    A module that appears only in the test year is scored by a model that never
    saw it, so its rows measure generalization to an unseen module rather than
    to an unseen year. It is dropped before any metric is computed, and the
    exclusion is stated beside the results.
    """
    import shutil

    import pandas as pd

    from pipeline.validate import REQUIRED_FILES

    data = tmp_path / "data"
    data.mkdir()
    for name in REQUIRED_FILES:
        shutil.copy(FIXTURES / name, data / name)

    # Relabel the 2014 presentation as a second module, so it has no 2013 half.
    for name in ("courses.csv", "studentInfo.csv", "studentRegistration.csv",
                 "assessments.csv", "vle.csv", "studentVle.csv"):
        frame = pd.read_csv(data / name)
        if "code_presentation" in frame.columns:
            frame.loc[frame.code_presentation == "2014J", "code_module"] = "ZZZ"
            frame.to_csv(data / name, index=False)

    out = tmp_path / "out"
    grid = run_experiment(data, out, horizons=(4,))
    note = (out / "README.txt").read_text()

    assert "Excluded from evaluation" in note
    assert "ZZZ" in note
    # Every test row belonged to the excluded module, so no horizon survives.
    assert grid.empty or (grid["test_rows"] == 0).all()
