"""The experiment: every (feature set, horizon, classifier) cell.

No Django imports. Runs standalone against a directory of the seven OULAD
files:

    python -m pipeline.experiment --data /path/to/oulad --out results

Writes
    results/grid.csv                one row per cell, 4 x 3 x 3 = 36
    results/baselines.csv           the majority baseline at each horizon
    results/subgroups_w{h}.csv      the best classifier's subgroup metrics
    results/fig_aucpr_vs_horizon.png
    results/fig_calibration_w8.png
    results/fig_subgroup_auc_imd_w8.png

Chapter 4 is written from these.

A note on reading grid.csv
--------------------------
AUC-PR's floor is the positive rate, and the positive rate falls at every
horizon because the cohort filter removes students who have already
unregistered. Raw AUC-PR therefore falls with the horizon even where the model
is getting better. Every row carries `baseline_auc_pr` and `auc_pr_lift` for
that reason: compare cells at different horizons on lift, not on AUC-PR.
"""

from __future__ import annotations

import argparse
import csv
import logging
import time
from pathlib import Path

import pandas as pd

from .fairness import MIN_N, subgroup_metrics
from .features import build_weekly_features
from .loader import read_student_info, read_student_registration
from .train import (
    FEATURE_SETS,
    SUBGROUP_DIMS,
    DegenerateSplit,
    drop_untrained_modules,
    build_cohort,
    fit_and_evaluate,
    modules_without_training_data,
    select_set,
    split_by_year,
)

logger = logging.getLogger("pipeline.experiment")

HORIZONS = (4, 8, 12)
SETS = ("D", "A", "E", "All")
# The grid is 4 x 3 x 3. The majority baseline is not a cell of it: it ignores
# the features entirely, so it has one value per horizon, not one per cell.
CLASSIFIERS = ("logreg", "rf", "hgb")
BASELINE = "majority"

CALIBRATION_HORIZON = 8
SUBGROUP_FIGURE_DIM = "imd_band"

GRID_COLUMNS = [
    "feature_set", "horizon_week", "classifier",
    "auc_roc", "auc_pr", "brier", "recall_at_p50",
    "baseline_auc_pr", "auc_pr_lift",
    "training_rows", "test_rows",
    "train_positive_rate", "test_positive_rate",
    "n_features", "fit_seconds",
]


def run_experiment(
    upload_dir: Path,
    results_dir: Path,
    horizons=HORIZONS,
    feature_sets=SETS,
    classifiers=CLASSIFIERS,
) -> pd.DataFrame:
    """Input: a directory of the seven OULAD files and a directory to write to.
    Output: the grid as a DataFrame, also written to results_dir/grid.csv.
    """
    upload_dir = Path(upload_dir)
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    logger.info("building weekly features from %s", upload_dir)
    features = build_weekly_features(upload_dir)
    students = read_student_info(upload_dir / "studentInfo.csv")
    registrations = read_student_registration(upload_dir / "studentRegistration.csv")

    rows: list[dict] = []
    baselines: list[dict] = []
    best_by_horizon: dict[int, dict] = {}
    excluded: list[str] = []
    skipped: list[tuple[int, str]] = []
    dropped_rows: dict[int, int] = {}

    for horizon in horizons:
        X, y, groups = build_cohort(features, students, registrations, horizon)
        for module in modules_without_training_data(groups):
            if module not in excluded:
                excluded.append(module)
        X_tr, X_te, y_tr, y_te, _, groups_te = split_by_year(X, y, groups)

        # SRS default: a module with no training year is excluded from
        # evaluation and the exclusion is stated.
        untrained = modules_without_training_data(groups)
        X_te, y_te, groups_te, dropped = drop_untrained_modules(
            X_te, y_te, groups_te, untrained
        )
        if dropped:
            logger.info(
                "week %d: %d test rows dropped for %s (no training year)",
                horizon, dropped, ", ".join(untrained),
            )
            dropped_rows[horizon] = dropped
        if len(X_te) == 0:
            reason = "every test row belonged to a module with no training year"
            logger.warning("week %d skipped: %s", horizon, reason)
            skipped.append((horizon, reason))
            continue

        try:
            baseline = fit_and_evaluate(
                select_set(X_tr, "All"), y_tr, select_set(X_te, "All"), y_te, BASELINE
            )
        except DegenerateSplit as degenerate:
            # A horizon whose cohort filter leaves one side of the split with no
            # withdrawals at all supports no cell. Say so and move on rather
            # than write rows that mean nothing.
            logger.warning("week %d skipped: %s", horizon, degenerate)
            skipped.append((horizon, str(degenerate)))
            continue
        baselines.append(
            {
                "horizon_week": horizon,
                "classifier": BASELINE,
                "auc_roc": baseline.auc_roc,
                "auc_pr": baseline.auc_pr,
                "brier": baseline.brier,
                "recall_at_p50": baseline.recall_at_p50,
                "training_rows": baseline.training_rows,
                "test_rows": baseline.test_rows,
                "train_positive_rate": baseline.train_positive_rate,
                "test_positive_rate": baseline.test_positive_rate,
            }
        )
        logger.info(
            "week %d: %d train / %d test rows, baseline AUC-PR %.3f "
            "(test positive rate %.3f)",
            horizon, baseline.training_rows, baseline.test_rows,
            baseline.auc_pr, baseline.test_positive_rate,
        )

        for feature_set in feature_sets:
            columns_tr = select_set(X_tr, feature_set)
            columns_te = select_set(X_te, feature_set)
            for classifier in classifiers:
                clock = time.monotonic()
                evaluation = fit_and_evaluate(
                    columns_tr, y_tr, columns_te, y_te, classifier
                )
                elapsed = time.monotonic() - clock
                rows.append(
                    {
                        "feature_set": feature_set,
                        "horizon_week": horizon,
                        "classifier": classifier,
                        "auc_roc": evaluation.auc_roc,
                        "auc_pr": evaluation.auc_pr,
                        "brier": evaluation.brier,
                        "recall_at_p50": evaluation.recall_at_p50,
                        "baseline_auc_pr": baseline.auc_pr,
                        "auc_pr_lift": evaluation.auc_pr / baseline.auc_pr
                        if baseline.auc_pr
                        else float("nan"),
                        "training_rows": evaluation.training_rows,
                        "test_rows": evaluation.test_rows,
                        "train_positive_rate": evaluation.train_positive_rate,
                        "test_positive_rate": evaluation.test_positive_rate,
                        "n_features": columns_tr.shape[1],
                        "fit_seconds": round(elapsed, 2),
                    }
                )
                logger.info(
                    "  %-3s %-6s w%-2d  AUC-ROC %.3f  AUC-PR %.3f (x%.2f)  "
                    "Brier %.3f  R@P50 %.3f  [%.1fs]",
                    feature_set, classifier, horizon,
                    evaluation.auc_roc, evaluation.auc_pr,
                    rows[-1]["auc_pr_lift"], evaluation.brier,
                    evaluation.recall_at_p50, elapsed,
                )
                current = best_by_horizon.get(horizon)
                if current is None or evaluation.auc_pr > current["evaluation"].auc_pr:
                    best_by_horizon[horizon] = {
                        "evaluation": evaluation,
                        "feature_set": feature_set,
                        "y_te": y_te,
                        "groups_te": groups_te,
                        "baseline_brier": baseline.brier,
                    }

    grid = pd.DataFrame(rows, columns=GRID_COLUMNS)
    grid.to_csv(results_dir / "grid.csv", index=False)
    pd.DataFrame(baselines).to_csv(results_dir / "baselines.csv", index=False)

    for horizon, best in best_by_horizon.items():
        _write_subgroups(results_dir, horizon, best)

    _figures(results_dir, grid, baselines, best_by_horizon)
    _write_readme(
        results_dir, grid, baselines, best_by_horizon, excluded, skipped, dropped_rows
    )

    logger.info(
        "experiment complete: %d cells in %.1fs; wrote %s",
        len(grid), time.monotonic() - started, results_dir,
    )
    return grid


def _write_subgroups(results_dir: Path, horizon: int, best: dict) -> None:
    """One CSV per horizon, for the best cell at that horizon."""
    evaluation = best["evaluation"]
    report = subgroup_metrics(
        best["y_te"], evaluation.y_prob, best["groups_te"], SUBGROUP_DIMS
    )
    path = results_dir / f"subgroups_w{horizon}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "dimension", "level", "n", "suppressed", "positives",
                "positive_rate", "auc_roc", "recall_at_p50", "dimension_gap",
            ]
        )
        for dimension in SUBGROUP_DIMS:
            gap = report[dimension]["gap"]
            for level in sorted(report[dimension]["levels"]):
                entry = report[dimension]["levels"][level]
                if entry.get("suppressed"):
                    writer.writerow(
                        [dimension, level, entry["n"], "true", "", "", "", "", gap]
                    )
                    continue
                writer.writerow(
                    [
                        dimension, level, entry["n"], "false", entry["positives"],
                        f"{entry['positive_rate']:.4f}",
                        "" if entry["auc_roc"] is None else f"{entry['auc_roc']:.4f}",
                        "" if entry["recall_at_p50"] is None
                        else f"{entry['recall_at_p50']:.4f}",
                        "" if gap is None else f"{gap:.4f}",
                    ]
                )
    logger.info(
        "week %d subgroups written for %s/%s",
        horizon, best["feature_set"], evaluation.classifier,
    )


# --- figures --------------------------------------------------------------

def _figures(results_dir: Path, grid, baselines, best_by_horizon) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _figure_aucpr_vs_horizon(plt, results_dir, grid, baselines)
    best8 = best_by_horizon.get(CALIBRATION_HORIZON)
    if best8:
        _figure_calibration(plt, results_dir, best8)
        _figure_subgroup_auc(plt, results_dir, best8)


def _figure_aucpr_vs_horizon(plt, results_dir: Path, grid, baselines) -> None:
    """AUC-PR against horizon, one line per feature set.

    The baseline is drawn on the same axes because AUC-PR's floor moves with
    the horizon; without it the left panel reads as the opposite of what it
    means. The right panel is the same data divided by that floor.
    """
    base = {row["horizon_week"]: row["auc_pr"] for row in baselines}
    figure, (left, right) = plt.subplots(1, 2, figsize=(11, 4.2))

    for feature_set in SETS:
        part = grid[grid.feature_set == feature_set]
        if part.empty:
            continue
        curve = part.groupby("horizon_week")["auc_pr"].max().sort_index()
        left.plot(curve.index, curve.to_numpy(), marker="o", label=feature_set)
        lift = curve / pd.Series(base).reindex(curve.index)
        right.plot(lift.index, lift.to_numpy(), marker="o", label=feature_set)

    horizons = sorted(base)
    left.plot(
        horizons, [base[h] for h in horizons],
        linestyle="--", color="0.4", marker="s", label="majority baseline",
    )
    left.set_xlabel("prediction horizon (week)")
    left.set_ylabel("AUC-PR")
    left.set_title("AUC-PR by feature set\n(best classifier per cell)")
    left.set_xticks(horizons)
    left.grid(alpha=0.3)
    left.legend(fontsize=8)

    right.axhline(1.0, linestyle="--", color="0.4", label="baseline")
    right.set_xlabel("prediction horizon (week)")
    right.set_ylabel("AUC-PR / baseline AUC-PR")
    right.set_title("Lift over the majority baseline\n(the comparable measure)")
    right.set_xticks(horizons)
    right.grid(alpha=0.3)
    right.legend(fontsize=8)

    figure.tight_layout()
    figure.savefig(results_dir / "fig_aucpr_vs_horizon.png", dpi=150)
    plt.close(figure)


def _figure_calibration(plt, results_dir: Path, best: dict) -> None:
    """Predicted against observed, decile by decile.

    Bins below the same n < MIN_N floor the subgroup report uses are drawn open
    and left out of the line. A decile holding one student is an observed rate
    of 0.0 or 1.0 by arithmetic, and plotted like any other point it dominates
    the shape of the curve while saying nothing.
    """
    evaluation = best["evaluation"]
    bins = evaluation.calibration
    reliable = [b for b in bins if b["n"] >= MIN_N]
    thin = [b for b in bins if b["n"] < MIN_N]

    figure, axes = plt.subplots(figsize=(5.6, 5.2))
    axes.plot([0, 1], [0, 1], linestyle="--", color="0.5", label="perfect calibration")
    axes.plot(
        [b["mean_predicted"] for b in reliable],
        [b["observed"] for b in reliable],
        marker="o",
        color="#4a7ab5",
        label=f"{best['feature_set']} / {evaluation.classifier}",
    )
    # Area in proportion to the students behind each point.
    axes.scatter(
        [b["mean_predicted"] for b in reliable],
        [b["observed"] for b in reliable],
        s=[max(20.0, 220.0 * b["n"] / max(x["n"] for x in reliable)) for b in reliable],
        color="#4a7ab5", alpha=0.35, zorder=2,
    )
    if thin:
        axes.scatter(
            [b["mean_predicted"] for b in thin],
            [b["observed"] for b in thin],
            facecolors="none", edgecolors="0.55", s=45, zorder=2,
            label=f"bin with n < {MIN_N} (not fitted)",
        )
    for entry in bins:
        axes.annotate(
            f"n={entry['n']}",
            (entry["mean_predicted"], entry["observed"]),
            textcoords="offset points", xytext=(5, -10), fontsize=7, color="0.35",
        )
    axes.set_xlabel("mean predicted probability")
    axes.set_ylabel("observed withdrawal rate")
    axes.set_title(
        f"Calibration at week {CALIBRATION_HORIZON}\n"
        f"{best['feature_set']} / {evaluation.classifier}, "
        f"Brier {evaluation.brier:.3f} "
        f"(baseline {best['baseline_brier']:.3f})"
    )
    axes.set_xlim(0, 1)
    axes.set_ylim(0, 1)
    axes.grid(alpha=0.3)
    axes.legend(fontsize=8, loc="lower right")
    figure.tight_layout()
    figure.savefig(results_dir / f"fig_calibration_w{CALIBRATION_HORIZON}.png", dpi=150)
    plt.close(figure)


def _figure_subgroup_auc(plt, results_dir: Path, best: dict) -> None:
    evaluation = best["evaluation"]
    report = subgroup_metrics(
        best["y_te"], evaluation.y_prob, best["groups_te"], [SUBGROUP_FIGURE_DIM]
    )
    levels = report[SUBGROUP_FIGURE_DIM]["levels"]
    names, values, notes = [], [], []
    for level in sorted(levels):
        entry = levels[level]
        names.append(level)
        if entry.get("suppressed") or entry.get("auc_roc") is None:
            values.append(0.0)
            notes.append(f"suppressed (n={entry['n']} < {MIN_N})")
        else:
            values.append(entry["auc_roc"])
            notes.append(f"n={entry['n']}")

    figure, axes = plt.subplots(figsize=(8.5, 4.4))
    bars = axes.bar(range(len(names)), values, color="#4a7ab5")
    for index, (bar, note) in enumerate(zip(bars, notes)):
        if note.startswith("suppressed"):
            bar.set_color("#d9d9d9")
            axes.text(index, 0.02, note, rotation=90, fontsize=7, va="bottom")
        else:
            axes.text(
                index, bar.get_height() + 0.01, note,
                ha="center", fontsize=7, color="0.35",
            )
    gap = report[SUBGROUP_FIGURE_DIM]["gap"]
    axes.axhline(0.5, linestyle="--", color="0.5", label="no better than chance")
    axes.set_xticks(range(len(names)))
    axes.set_xticklabels(names, rotation=45, ha="right", fontsize=8)
    axes.set_ylabel("AUC-ROC")
    axes.set_ylim(0, 1)
    axes.set_title(
        f"AUC-ROC by IMD band at week {CALIBRATION_HORIZON} "
        f"({best['feature_set']} / {evaluation.classifier})"
        + (f" — best-worst gap {gap:.3f}" if gap is not None else "")
    )
    axes.grid(alpha=0.3, axis="y")
    axes.legend(fontsize=8)
    figure.tight_layout()
    figure.savefig(
        results_dir / f"fig_subgroup_auc_imd_w{CALIBRATION_HORIZON}.png", dpi=150
    )
    plt.close(figure)


def _write_readme(
    results_dir: Path, grid, baselines, best_by_horizon, excluded, skipped,
    dropped_rows
) -> None:
    """A plain-text note beside the outputs, so a reader of results/ alone
    knows what the numbers are and how to compare them."""
    lines = [
        "Experiment outputs",
        "==================",
        "",
        f"Grid: {len(grid)} cells "
        f"({grid.feature_set.nunique()} feature sets x "
        f"{grid.horizon_week.nunique()} horizons x "
        f"{grid.classifier.nunique()} classifiers).",
        "Train on 2013 presentations, test on 2014. random_state=42 throughout.",
        "",
        "Best cell by AUC-PR at each horizon:",
    ]
    for horizon in sorted(best_by_horizon):
        best = best_by_horizon[horizon]
        evaluation = best["evaluation"]
        baseline = next(b for b in baselines if b["horizon_week"] == horizon)
        lines.append(
            f"  week {horizon:>2}: {best['feature_set']}/{evaluation.classifier}  "
            f"AUC-PR {evaluation.auc_pr:.3f} against a baseline of "
            f"{baseline['auc_pr']:.3f} (lift x{evaluation.auc_pr / baseline['auc_pr']:.2f}), "
            f"AUC-ROC {evaluation.auc_roc:.3f}, Brier {evaluation.brier:.3f}"
        )
    lines += [
        "",
        "Reading AUC-PR across horizons",
        "------------------------------",
        "AUC-PR's floor is the positive rate, and the positive rate falls at",
        "every horizon because the cohort filter removes students who have",
        "already unregistered. Raw AUC-PR therefore falls with the horizon even",
        "where the model is improving. Compare horizons on auc_pr_lift, not on",
        "auc_pr. Within one horizon, raw AUC-PR compares cells correctly.",
        "",
        f"Subgroup levels with fewer than {MIN_N} students are suppressed.",
    ]
    if skipped:
        lines += ["", "Horizons with no usable split:"]
        lines += [f"  week {horizon}: {reason}" for horizon, reason in skipped]
    if excluded:
        lines += [
            "",
            "Excluded from evaluation",
            "------------------------",
            f"Modules with no 2013 presentation, so no training data: "
            f"{', '.join(excluded)}.",
            "Their rows are removed from the test split before any metric is",
            "computed, because a model that never saw the module measures",
            "generalization to an unseen module rather than to an unseen year.",
        ]
        for horizon in sorted(dropped_rows):
            lines.append(f"  week {horizon}: {dropped_rows[horizon]} test rows removed")
    (results_dir / "README.txt").write_text("\n".join(lines) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True, type=Path,
                        help="directory holding the seven OULAD CSV files")
    parser.add_argument("--out", default=Path("results"), type=Path,
                        help="directory to write results into")
    parser.add_argument("--horizons", default=",".join(str(h) for h in HORIZONS),
                        help="comma-separated horizon weeks")
    arguments = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S"
    )
    run_experiment(
        arguments.data,
        arguments.out,
        horizons=tuple(int(h) for h in arguments.horizons.split(",")),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
