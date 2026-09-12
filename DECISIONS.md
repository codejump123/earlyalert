# Decisions recorded during the build

Evidence the build produced on the open items in SRS §3.3.7, and gaps between
the SRS and the data that the build had to settle to proceed. The SRS remains
authoritative; anything here that it later contradicts gets corrected.

## Open items (SRS §3.3.7)

### 5. How module CCC is handled under the year split

**2026-09-12 — confirmed, as the brief asked, against courses.csv.** CCC has
exactly two presentations, 2014B and 2014J. Under the train-on-2013 /
test-on-2014 split it therefore contributes no training rows at all, only test
rows. Verified on the real dataset after ingest:

```
CCC presentations: ['2014B', '2014J']
```

The default stands: exclude CCC from evaluation and note it. Revisit in Phase 4
when the cohort is actually built — the alternative is to keep CCC in the test
set and report its metrics separately, which measures generalization to an
unseen module rather than to an unseen year, and answers a different question
than the one the experiment asks.

### 2. Whether week 4 is a meaningful horizon given late registrants

**2026-09-12 — evidence from the full grid.** Week 4 is meaningful, but it is
the weakest horizon at telling students apart, and it only looks like the
strongest if AUC-PR is read raw.

| Horizon | Best AUC-PR | Baseline | Lift | AUC-ROC | Recall @ P50 |
|---|---|---|---|---|---|
| week 4 | 0.346 | 0.197 | x1.75 | 0.664 | 0.134 |
| week 8 | 0.307 | 0.163 | x1.88 | 0.694 | 0.036 |
| week 12 | 0.258 | 0.135 | x1.91 | 0.688 | 0.007 |

Week 4 has the highest raw AUC-PR and by far the best recall at precision
0.50, because a fifth of the week 4 cohort withdraws and a high-precision
queue is therefore easy to fill. It has the lowest lift and the lowest
AUC-ROC: the model at week 4 is separating students less well, and is carried
by the base rate.

The practical reading is the opposite of the usual worry. Week 4 is worth
keeping, not because it discriminates well, but because it is when a queue at
usable precision can actually be filled. Week 8 is where discrimination
arrives, and week 12 adds nothing to it (AUC-ROC 0.694 to 0.688).

### 1. Whether the n < 20 threshold should scale with cohort size

**2026-09-12 — evidence, though not a decision.** The floor never binds at the
level the fairness report is computed on: across the whole 2014 test year,
every IMD band holds between 596 and 1,636 students and nothing is suppressed.
It binds constantly one level down, on the per-presentation dashboard, where 6
of the 2014 presentations suppress at least one band — AAA/2014J suppresses
both `0-10%` (n=17) and `not recorded` (n=8).

So the two places the rule applies are not alike, and a single constant serves
one of them. Whether the dashboard wants a floor that scales with the
presentation is a live question; this build does not change it.

Items 3 and 4 are untouched; no evidence yet.

## Findings from the full 4 x 3 x 3 grid (2026-09-12)

36 cells on the real dataset, train 2013 / test 2014, in 32 seconds.
`results/grid.csv` is the table Chapter 4 is written from.

### Assessment behaviour carries the signal; clicks barely beat demographics

Lift over the per-horizon baseline, best classifier per cell:

| Feature set | week 4 | week 8 | week 12 |
|---|---|---|---|
| D demographic | x1.31 | x1.32 | x1.28 |
| A assessment | x1.63 | x1.79 | x1.78 |
| E engagement | x1.22 | x1.31 | x1.30 |
| All | x1.75 | x1.88 | x1.91 |

The result worth writing up is that **E barely beats D**. Clickstream
engagement, the thing a VLE log is mostly made of, is worth about as much as
knowing a student's IMD band and age. What predicts withdrawal is whether
assessments are being submitted and what they score — which is close to
tautological but is exactly the kind of claim the grid exists to test. D and E
together are still well short of A, and All beats A by roughly 0.1 of lift,
so the sets are not redundant.

### More weeks stop helping after week 8

AUC-ROC for All: 0.664 at week 4, 0.694 at week 8, 0.688 at week 12. The gain
is between weeks 4 and 8; week 12 gives none of it back. Combined with the
earliness reading in open item 2, week 8 is the horizon to defend.

### Precision 0.50 is barely reachable at these base rates

recall_at_p50 for the best cell falls 0.134, 0.036, 0.007 across the horizons.
At a 13-20% base rate, a queue at 50% precision is nearly empty by week 12.
This column will read as near-zeroes in the grid; that is the finding, not a
defect. If the SRS wants an operational threshold, precision 0.30 or a fixed
queue length would say more.

### Calibration separates the classifiers where AUC does not

Mean Brier across the grid: hgb 0.134, rf 0.185, logreg 0.241 — against
baselines of 0.117 to 0.159. Logistic regression is beaten by predicting the
base rate for everyone, while ranking as well as anything (mean AUC-PR 0.244
against hgb's 0.245). `results/fig_calibration_w8.png` shows the whole curve
sitting below the diagonal.

Two classifiers that are indistinguishable on AUC are far apart on whether
their output can be shown to a person as a probability. That is the argument
for the Brier column.

### The model is close to uniform across IMD bands

At week 8, all 11 IMD levels clear the n < 20 floor (596 to 1,636 students)
and AUC-ROC runs 0.66 to 0.75, a best-minus-worst gap of 0.087. The widest
band is `not recorded` at 0.75 — the students with no IMD band are predicted
slightly *better* than anyone else. No band is badly served by the model.

## Findings from the first full training run (2026-09-12)

Measured on the real dataset, feature set All, train 2013 / test 2014.

### AUC-PR is not comparable across horizons, and the headline reverses

| Horizon | Base rate (majority AUC-PR) | Best | AUC-PR | Lift over baseline |
|---|---|---|---|---|
| week 4 | 0.197 | hgb | 0.346 | x1.75 |
| week 8 | 0.163 | logreg | 0.307 | x1.88 |
| week 12 | 0.135 | rf | 0.258 | x1.91 |

Raw AUC-PR **falls** as the horizon gets later, which reads as "predicting
earlier is easier". It is not. AUC-PR's floor is the positive rate, and the
positive rate falls at every horizon because the cohort filter removes
students who have already unregistered: 0.197 at week 4 down to 0.135 at
week 12. Measured against that moving floor, the models get **better** with
more data, not worse: lift rises from x1.75 to x1.91.

This bears directly on the question the project asks — how prediction quality
trades against earliness. Chapter 4 must report AUC-PR against the per-horizon
baseline, or the grid will support the opposite conclusion to the true one.
The majority baseline is stored as a ModelVersion at every horizon precisely so
that comparison is always available.

### Logistic regression's probabilities are ranks, not probabilities

At week 12, Brier by classifier: hgb 0.110, majority 0.117, rf 0.123,
**logreg 0.236**. Logistic regression is beaten on Brier by predicting the base
rate for everyone, while still ranking well (AUC-PR 0.246 against the
baseline's 0.135).

This is `class_weight="balanced"`, which the SRS fixes. Re-weighting the
classes shifts the intercept so the model behaves as if withdrawal were a
50/50 event, and the output is systematically too high. The ordering is
unharmed, which is why AUC-ROC and AUC-PR look fine.

Consequences: the ranking page displays `probability`, and for a logreg model
that number should be read as a position in the queue, not as a chance of
withdrawing. Worth either calibrating the selected model before scoring or
labelling the column as a risk score rather than a probability. Not changed
here, because the SRS fixes both the class weighting and the field.

### Collinear engagement features can distort a single student's explanation

total_clicks, mean_weekly_clicks and max_weekly_clicks move together, so
logistic regression splits large opposing coefficients between them. Across
the cohort the explanations are sensible — mean_score, weighted_score_to_date
and mean_weekly_clicks are the three most-cited features at week 8 — but for a
student with extreme click counts the top three can come back as click volume
with contradictory directions. The magnitudes are real; the per-feature
attribution between near-duplicate columns is not stable.

## Gaps between the SRS and the data

### flag_note_added is an action with no endpoint

**2026-09-12.** The SRS action list includes `flag_note_added`, but the
endpoint table has no URL that could produce it: the three workflow endpoints
are `/students/<id>/flag/`, `/flags/<id>/intervention/` and
`/interventions/<id>/outcome/`.

Rather than invent a URL the SRS does not have, the note form posts to the
flag's own page, `/flags/<id>/intervention/`, and is told apart from the
intervention form by the field submitted. Notes append to `Flag.reason` with a
timestamp and the advisor's username; nothing already written is overwritten,
and notes are refused once the flag is closed.

If the SRS intends a dedicated endpoint, this is the one place to change.

### The SRS gives two incompatible week numberings

**2026-09-12.** Three statements in the brief cannot all hold:

1. Data: "Week w covers days 7(w-1) to 7w-1." — week 1 is days 0-6.
2. features.py: "compute week = `date // 7`" — week 1 would be days 7-13.
3. features.py, same sentence: "negative dates map to week <= 0."

`date // 7` satisfies neither (1) nor (3): it puts day 0 in week 0, and maps
day -1 to week -1 rather than week 0. `date // 7 + 1` satisfies both, and also
matches vle.csv, whose `week_from`/`week_to` are 1-based.

Implemented as `date // 7 + 1`, in `pipeline.features.week_of`, with the
convention isolated in the constant `WEEK_OFFSET` so a change is one line.
Week 1 is therefore days 0-6, week 8 ends on day 55, and pre-start activity
falls in week 0 and below.

This matters beyond naming: it moves every horizon by seven days. Under the
implemented numbering the week 8 horizon sees days 0-55; under `date // 7` it
would see days 0-62. Worth confirming against the SRS before Chapter 4 quotes
any horizon.

### An assessment with no due date is never "due by week w"

**2026-09-12.** Some presentations record the exam in assessments.csv with a
missing `date`. Such an assessment is excluded from the due schedule rather
than being counted as due at the end, so it never contributes a zero to
`mean_score`. Counting it as due would penalize every student in those
presentations at whatever week was chosen for it.

Related to open item 4 (whether zero-for-unsubmitted biases against students
with extensions): the same mechanism is at work, and the same evidence will
bear on both.

### Presentation.start_date has no column in courses.csv

**2026-09-12.** The data model gives Presentation a `start_date` DateField, but
courses.csv carries only `code_module`, `code_presentation` and
`module_presentation_length`. No OULAD file holds a calendar start date.

Resolved by deriving it from the presentation code, which is how OULAD names
the intake: the four digits are the year, `B` is the February presentation and
`J` the October one. `cohorts.ingest.presentation_start_date` maps `2013J` to
2013-10-01 and `2014B` to 2014-02-01.

The day of month is a convention, not data. Nothing in the pipeline depends on
it: every date in OULAD is an integer day offset from day 0, and the only parts
of `start_date` that are read anywhere are the year (for the train/test split,
which actually reads `code_presentation`) and the ordering. If the SRS intends
a real calendar date, it has to come from outside the dataset.
