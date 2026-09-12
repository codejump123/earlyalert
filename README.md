# EarlyAlert

Predicts which students in an online course presentation are likely to
withdraw, early enough that a support advisor can reach them. Applied project
for CISC 699, Harrisburg University, by Isit Pokharel.

The system is a Django application (three roles, upload path, intervention
workflow, audit log) wrapped around an analytics pipeline that builds weekly
per-student features from the Open University Learning Analytics Dataset
(OULAD), trains classifiers at three prediction horizons, and evaluates them
overall and per demographic subgroup.

The SRS is the authoritative source for behavior. `CLAUDE.md` summarizes it
for build purposes.

## Environment

Installed 2026-09-12 on macOS 15 (arm64).

| Component | Version |
|---|---|
| Python | 3.12.14 (Homebrew `python@3.12`) |
| Django | 5.2.17 |
| pandas | 3.0.5 |
| scikit-learn | 1.9.1 |
| numpy | 2.5.3 |
| scipy | 1.18.1 |
| joblib | 1.6.0 |
| matplotlib | 3.11.2 |
| pytest | 9.1.1 |
| pytest-django | 4.14.0 |

Database is SQLite in development (`db.sqlite3`); PostgreSQL is optional in
deployment. Both are reached through the Django ORM only. There is no Celery,
no broker, no separate worker, and no outbound network call at runtime.

## Build and run

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_accounts
.venv/bin/python manage.py runserver
```

`seed_accounts` creates one account per role, all with the password
`earlyalert-dev` (override with `--password`):

| Username | Role |
|---|---|
| `instructor1` | instructor |
| `advisor1` | advisor |
| `admin1` | administrator |

Only `admin1` is a Django superuser, so only it reaches `/django-admin/`,
where an administrator manages users and assigns presentations.

A newly seeded account has no presentation assignments, so the selector is
empty until an administrator assigns presentations in the Django admin.

## Endpoints

| URL | Purpose | Role | UC |
|---|---|---|---|
| `/login/`, `/logout/` | sign in and out | any | UC01 |
| `/presentations/` | pick a presentation and horizon | any | UC02 |
| `/presentations/<id>/ranking/?horizon=8` | students ranked by stored risk | assigned | UC03 |
| `/presentations/<id>/ranking/export.csv` | the whole ranking as CSV | assigned | UC03 |
| `/students/<id>/` | one student's demographics, engagement, risk | assigned | UC04 |
| `/students/<id>/flag/` | raise a flag | advisor | UC05 |
| `/flags/<id>/intervention/` | the flag's page: notes and interventions | advisor | UC06 |
| `/interventions/<id>/outcome/` | record an outcome, closing the flag | advisor | UC07 |
| `/presentations/<id>/dashboard/?dim=imd_band` | risk by demographic group | assigned | UC08 |
| `/admin/upload/` | upload the seven OULAD files | administrator | UC09 |
| `/admin/rebuild/` | rebuild weekly features | administrator | UC10 |
| `/admin/retrain/` | retrain and rescore | administrator | UC11 |
| `/admin/audit/` | the audit log, filtered; `?export=csv` for CSV | administrator | UC12 |

`/django-admin/` handles user accounts, roles and presentation assignments.

Two endpoints in this table are not in the SRS endpoint table and are marked
in `DECISIONS.md`: the note form on `/flags/<id>/intervention/`, because
`flag_note_added` is an action with no URL of its own, and `?export=csv` on
the audit log, because Phase 7 requires a CSV export the table does not list.
Neither adds a URL.

## Tests

```sh
.venv/bin/python -m pytest
```

Pipeline tests run without a server, because `pipeline/` imports no Django.
Test data is a synthetic 50-student cohort in `tests/fixtures/`; no real OULAD
rows appear in any test.

## What the audit log records

Every state-changing action writes exactly one entry, and the log has no
update or delete path: `AuditEntry.save()` refuses to rewrite an existing row,
and both the instance and the queryset refuse `delete()`. The Django admin
registration refuses add, change and delete as well.

`login`, `login_failed`, `login_locked`, `denied`, `flag_created`,
`flag_note_added`, `intervention_recorded`, `outcome_recorded`,
`upload_accepted`, `upload_rejected`, `rebuild_started`, `rebuild_completed`,
`retrain_started`, `retrain_completed`, `retrain_failed`, `export`.

A refused rebuild writes nothing, because the SRS action list has no
`rebuild_failed` and a refusal changes no state. A refused retrain writes
`retrain_failed` with the reason, because the SRS does list one.

## Data

OULAD, UCI Machine Learning Repository dataset 349 (CC BY 4.0). Seven CSV
files uploaded together; nothing is stored unless all seven pass validation.
Missing values are the literal string `?`. The largest file,
`studentVle.csv`, is ~433 MB and is never read into memory whole.

## The experiment

```sh
.venv/bin/python -m pipeline.experiment --data /path/to/oulad --out results
```

Runs standalone — `pipeline/` imports no Django, so this needs no database and
no server. 36 cells (4 feature sets x 3 horizons x 3 classifiers) in about 32
seconds on the reference machine, including building the weekly features.

Writes `results/grid.csv` (one row per cell), `results/baselines.csv`,
`results/subgroups_w{4,8,12}.csv`, three figures, and a `README.txt` explaining
how to read them. Only `grid.csv` is committed.

A module with no presentation in the training year — CCC, whose two
presentations are both 2014 — is removed from the test split before any metric
is computed, per the SRS default, and the count is stated in the log and in
`results/README.txt`. The application still scores those students, since their
advisors need a ranking; only the metrics exclude them.

**Every row of the grid carries `baseline_auc_pr` and `auc_pr_lift`, and they
matter.** AUC-PR's floor is the positive rate, and the positive rate falls at
every horizon because the cohort filter removes students who have already
unregistered. Raw AUC-PR therefore falls with the horizon even where the model
is improving. Compare cells at different horizons on lift; within one horizon,
raw AUC-PR compares them correctly. See `DECISIONS.md`.

## Measured performance

On the reference machine (Apple silicon, 8 GB), against the full dataset:

| Step | Target | Actual |
|---|---|---|
| Validate seven files, incl. SHA-256 of 433 MB | — | 0.4 s |
| Ingest 32,593 students | — | 0.8 s |
| Build weekly features from 10,655,280 rows | under 10 min | **21.6 s**, peak RSS 1.55 GB |
| Write 1,136,133 WeeklyFeatures rows | — | 21.4 s |
| Retrain: 3 horizons x 4 classifiers, 45,411 scores | — | 25.8 s, peak RSS 1.06 GB |
| Experiment: the full 36-cell grid and figures | — | 31.9 s |

The feature build reads studentVle.csv in 22 chunks of 500,000 rows and holds
one chunk plus three accumulators, never the file.

## Build status

| Phase | Scope | Tests | Status |
|---|---|---|---|
| 1 | Scaffold: models, roles, auth, selector, authorization service | TC1, TC2, TC3, TC5, `test_authz.py` | done |
| 2 | Ingest: upload view, validation, loader | TC11, TC12, `test_validate.py` | done |
| 3 | Features: chunked weekly aggregation, rebuild view | TC13, TC14, `test_features.py` | done |
| 4 | Training: cohort, split, fit, fairness, retrain view | TC15, TC16, `test_fairness.py` | done |
| 5 | Advisor views: ranking, export, detail, dashboard | TC4, TC6, TC10, TC18 | done |
| 6 | Intervention workflow | TC7, TC8, TC9 | done |
| 7 | Audit view and polish | TC17 | done |
| 8 | Experiment: the 36-cell grid and figures | — | done |
