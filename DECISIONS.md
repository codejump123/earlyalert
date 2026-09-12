# Decisions recorded during the build

Evidence the build produced on the open items in SRS §3.3.7, and gaps between
the SRS and the data that the build had to settle to proceed. The SRS remains
authoritative; anything here that it later contradicts gets corrected.

## Open items (SRS §3.3.7)

### 5. How module CCC is handled under the year split

**2026-09-12 — confirmed against courses.csv, and now implemented.** CCC has
exactly two presentations, 2014B and 2014J. Under the train-on-2013 /
test-on-2014 split it therefore contributes no training rows at all, only test
rows. Verified on the real dataset after ingest:

```
CCC presentations: ['2014B', '2014J']
```

The default is implemented: `pipeline.train.drop_untrained_modules` removes
such a module's rows from the test split before any metric is computed, in both
the experiment and the application's retrain. It is not a silent drop — the
count appears in the log, in `results/README.txt`, and in the retrain's result.

The application still *scores* CCC students (19,676 risk scores across the three
horizons), because an advisor assigned to CCC/2014B needs a ranking. Only the
metrics exclude them, which is what "exclude from evaluation" asks for. An
advisor should know those scores come from a model that never saw the module;
the ModelVersion's metrics do not cover it.

This mattered more than expected: CCC is 21.5% of the week 8 test set, and
including it changed which classifier won at every horizon and reversed the
ordering of the D and E feature sets.

### 2. Whether week 4 is a meaningful horizon given late registrants

**2026-09-12 — evidence from the full grid.** Week 4 is meaningful, but it is
the weakest horizon at telling students apart, and it only looks like the
strongest if AUC-PR is read raw.

| Horizon | Best AUC-PR | Baseline | Lift | AUC-ROC | Recall @ P50 |
|---|---|---|---|---|---|
| week 4 | 0.277 | 0.167 | x1.66 | 0.646 | 0.044 |
| week 8 | 0.287 | 0.143 | x2.01 | 0.682 | 0.076 |
| week 12 | 0.235 | 0.117 | x2.01 | 0.686 | 0.007 |

Week 4 has the lowest lift and the lowest AUC-ROC: the model is separating
students least well there, and its higher raw AUC-PR is the base rate rather
than the model. Week 8 is where both discrimination and a usable precision
queue arrive, and week 12 adds nothing to either (AUC-ROC 0.682 to 0.686, lift
flat at x2.01, recall at precision 0.50 collapsing to 0.007).

So week 4 is meaningful as an early warning but weak as a ranking, and the
honest reading is that the horizon worth operating on is week 8.

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

## Decisions the SRS left to the build (2026-09-12)

Two things the SRS requires and does not define. Both are recorded here because
both change a reported result.

### The fairness metric

The SRS names two, and they are not the same measurement. Section 1 promises
"per-subgroup false negative rate". UC11 step 6 specifies "AUC-ROC, AUC-PR, and
Brier score overall and again for each subgroup". Neither mentions recall at
precision 0.50, which the project brief added and which the build reported
until now.

**Both are reported.** The false-negative rate is the headline, because a false
negative is a student who withdrew and was never put in front of an advisor,
which is the harm Section 1 is written about. AUC-ROC, AUC-PR and Brier are
reported beside it, because they are threshold-free and show whether a gap is
about ranking or about calibration.

The false-negative rate needs an operating threshold and the SRS gives none.
Recall at precision 0.50 cannot supply one: precision 0.50 is barely reachable
at these base rates and the recall behind it falls to 0.007 by week 12. So the
threshold is set by **capacity** — flag the top 10 percent of the test cohort,
one threshold shared by every subgroup — which is both stable across horizons
and the number an advisory team actually has. A per-subgroup threshold would
give each group its own bar and make the rates incomparable, which is the only
reason to compute them.

Ten percent is a convention, not a measurement. If a real advisory capacity is
known it belongs in `pipeline.fairness.CAPACITY`, and every number below moves
with it.

### What the false-negative rate shows, and why it was worth the change

Week 8, best model (All/hgb), IMD band, at 10 percent capacity:

| IMD band | n | withdrew | flagged | FNR | AUC-ROC |
|---|---|---|---|---|---|
| 0-10% | 1172 | 187 | 156 | 0.701 | 0.663 |
| 30-40% | 1293 | 188 | 119 | 0.782 | 0.663 |
| 60-70% | 1100 | 160 | 98 | 0.812 | 0.657 |
| 90-100% | 927 | 104 | 70 | 0.798 | 0.729 |
| not recorded | 379 | 48 | 31 | 0.625 | 0.805 |

AUC-ROC gap 0.156; false-negative-rate gap 0.188.

Two things are visible here that AUC alone did not show.

The first is the operational truth: **at a capacity of 10 percent the system
misses roughly three quarters of all withdrawers**, in every band. An AUC of
0.69 sounds moderate; "70 to 81 percent of the students who left were never
flagged" is the same fact stated in the units the project cares about, and it
is the honest headline for Chapter 4.

The second runs opposite to the fear Section 1 raises. **The most deprived band
is missed least** — 0.701 against 0.798 in the least deprived — because its
higher withdrawal rate puts more of its students above a shared bar. The same
holds for disability: 0.508 for students with a declared disability against
0.789 without, a gap of 0.281 in their favour. On this evidence the system does
not under-serve the groups Section 1 was written to protect. That is a finding,
and it is not the one an AUC table would have produced: by AUC the most
deprived band looks slightly *worse* served (0.663 against 0.729), which is the
opposite conclusion.

### The risk band

The SRS requires a risk band on four screens — the ranking row, the sort
control, the CSV export, the student detail view — and a count of students per
band on the dashboard. It defines it nowhere: no entry in Section 3.1.3, no
thresholds anywhere in the document.

Defined in `scoring/bands.py` as a position within the cohort, not a fixed
probability cutoff. The base rate moves with the horizon, so a fixed cutoff
would band the same student differently at week 4 and week 12 for a reason that
has nothing to do with that student; and under the SRS-mandated balanced class
weights, predicted probabilities are inflated enough that an absolute "High
above 0.70" would put a third of a cohort in the top band.

High is the top 10 percent, Medium the next 20, Low the rest. The High share is
the same figure as the capacity threshold above, so "in the High band" and
"reached by an advisor" describe the same students.

## Findings from the full 4 x 3 x 3 grid (2026-09-12)

36 cells on the real dataset, train 2013 / test 2014, in 32 seconds.
`results/grid.csv` is the table Chapter 4 is written from.

**These numbers supersede an earlier run.** The first pass named CCC as having
no training year but did not remove it from the test split, so every cell was
scored on a test set that was a fifth an unseen module (3,239 of 15,092 rows at
week 8). The SRS default is to exclude it. Corrected, and the correction moved
the headline result — see the feature-set section below.

### Assessment behaviour carries the signal, and clicks are worth less than demographics

Lift over the per-horizon baseline, best classifier per cell:

| Feature set | week 4 | week 8 | week 12 |
|---|---|---|---|
| D demographic | x1.41 | x1.40 | x1.35 |
| A assessment | x1.44 | x1.91 | x1.84 |
| E engagement | x1.21 | x1.29 | x1.29 |
| All | x1.66 | **x2.01** | **x2.01** |

The result worth writing up: **engagement is the weakest feature set at every
horizon, below demographics throughout.** Clickstream volume — the bulk of what
a VLE log contains, and 10.6 million rows of this dataset — predicts withdrawal
less well than knowing a student's IMD band, age and prior attempts. What
predicts withdrawal is whether assessments are being submitted and what they
score.

(With CCC wrongly included, D and E looked equal at x1.32 and x1.31. Removing an
unseen module from the test set separated them: D rose to x1.40, E stayed at
x1.29. A model relying on demographics generalizes across years within a module
it has seen; the engagement features were being flattered by a module it had
not.)

At week 4, D and A are level (x1.41 against x1.44): before any assessment has
fallen due there is little assessment signal to have. A pulls away at week 8
once the first deadlines have passed.

### More weeks stop helping after week 8

AUC-ROC for All: 0.646 at week 4, 0.686 at week 8, 0.686 at week 12. The gain is
entirely between weeks 4 and 8; week 12 adds nothing measurable. Lift is
likewise flat at x2.01 across weeks 8 and 12. Week 8 is the horizon to defend.

### Precision 0.50 is barely reachable at these base rates

recall_at_p50 for the best cell: 0.044, 0.076, 0.007 across the horizons. At a
13-20% base rate a queue at 50% precision is nearly empty, and by week 12 it is
empty. This column reads as near-zeroes in the grid; that is the finding, not a
defect. If the SRS wants an operational threshold, precision 0.30 or a fixed
queue length would say more about whether an advisor's day can be filled.

### Calibration separates the classifiers where AUC does not

Mean Brier across the grid: hgb 0.119, rf 0.174, logreg 0.235, against baselines
of 0.117 to 0.159. Logistic regression is beaten by predicting the base rate for
everyone, while ranking about as well as anything.
`results/fig_calibration_w8.png` shows the whole curve sitting below the
diagonal: at a predicted 0.92 the observed withdrawal rate is 0.51.

Two classifiers indistinguishable on AUC are far apart on whether their output
can be shown to a person as a probability. That is the argument for the Brier
column, and after the correction hgb wins every horizon on all four metrics at
once.

### The model is close to uniform across IMD bands

At week 8 every IMD level clears the n < 20 floor and AUC-ROC runs roughly 0.66
to 0.75, a best-minus-worst gap of about 0.09. The widest band is `not recorded`
— students with no IMD band are predicted slightly better than anyone else. No
band is badly served.

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

### The week numbering, and where the conflicting formula actually came from

**2026-09-12, corrected the same day after reading the SRS.** Three statements
in the project brief (CLAUDE.md) cannot all hold:

1. Data: "Week w covers days 7(w-1) to 7w-1." — week 1 is days 0-6.
2. features.py: "compute week = `date // 7`" — week 1 would be days 7-13.
3. features.py, same sentence: "negative dates map to week <= 0."

`date // 7` satisfies neither (1) nor (3): it puts day 0 in week 0, and maps
day -1 to week -1 rather than week 0. `date // 7 + 1` satisfies both, and also
matches vle.csv, whose `week_from`/`week_to` are 1-based.

**The conflicting formula is not in the SRS.** The SRS specifies no week
formula at all: UC10 says only that the builder "aggregates each chunk to
per-student, per-week totals", and Section 3.2.5 fixes day 0 as the start with
negative values before it. `week = date // 7` appears only in the project
brief. So there is no contradiction with the authoritative document to resolve,
and the implemented numbering stands unopposed rather than pending a ruling.
This was first recorded here as an SRS contradiction; that attribution was
wrong.

Implemented as `date // 7 + 1`, in `pipeline.features.week_of`, with the
convention isolated in the constant `WEEK_OFFSET` so a change is one line.
Week 1 is therefore days 0-6, week 8 ends on day 55, and pre-start activity
falls in week 0 and below.

It still matters which is used: it moves every horizon by seven days. Under the
implemented numbering the week 8 horizon sees days 0-55; under `date // 7` it
would see days 0-62. But with the SRS silent and the brief self-contradictory,
`date // 7 + 1` is the only reading that satisfies every statement made about
weeks anywhere, and the numbers reported from it do not depend on a pending
decision.

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
