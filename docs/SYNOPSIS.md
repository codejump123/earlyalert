Isit Pokharel
CISC 699: Applied Project in Computer Science

# Project Title

EarlyAlert: An Early Warning System for Student Withdrawal in Online Higher Education

# Project Synopsis

In the Open University Learning Analytics Dataset (OULAD), 10,156 of 32,593 student registrations end in withdrawal (Kuzilek et al., 2017), and the rate is not even across the cohort: 37.2 percent of registrations in the most deprived IMD decile withdraw, against 25.9 percent in the least deprived. EarlyAlert is a Django web application that ranks the students in a course presentation by predicted withdrawal probability, shows the three features driving each score in plain behavioral terms, and records the outreach an advisor performs in response. An administrator uploads a seven-file OULAD cohort, the system derives weekly engagement and assessment features from 10,655,280 clickstream rows, and classifiers are trained and evaluated at week 4, week 8, and week 12 of the term.

The experimental result is a grid of 36 cells: four feature sets (demographic, assessment, engagement, and all three combined) by three horizons by three classifiers (logistic regression, random forest, histogram gradient boosting), each scored on AUC-ROC, AUC-PR, and Brier score against a majority-class baseline fitted at the same horizon. The best cell at each horizon is then broken down by IMD band, disability, age band, highest education, and gender. Each group is reported with AUC-ROC, AUC-PR and Brier score, and with the false-negative rate at a shared capacity threshold — the share of that group's withdrawers who were never flagged when an advisor's capacity is the top 10 percent of the cohort. That last figure is the one the project's argument turns on, and any group of fewer than 20 students is suppressed.

The point of separating the three horizons is to measure the cost of predicting early instead of assuming it. Published early warning systems, including Course Signals at Purdue (Arnold & Pistilli, 2012) and OU Analyse at the Open University (Wolff et al., 2013), report one model at one point in the term scored with aggregate metrics. A system that lowers the overall withdrawal rate while the gap between deprivation bands stays the same has not helped the students who were most likely to leave, so the evaluation is built to make that visible. EarlyAlert does not message students, does not integrate with a live virtual learning environment, and does not make enrollment recommendations.

One measurement result shapes how the grid must be read. AUC-PR has a floor equal to the positive rate, and the positive rate falls at every horizon because the cohort filter removes students who have already unregistered, from 0.167 at week 4 to 0.117 at week 12. Raw AUC-PR therefore falls with the horizon even where the model is improving. Every row of the results table carries the baseline it sits on and the ratio to it, and horizons are compared on that ratio.

# Relationship to GRAD 695

This project is not a continuation of my GRAD 695 work. That course covered a different topic, and EarlyAlert was started from nothing for CISC 699. No code, design, or written material carries over.

# For Project Development

**Integrated Development Environment (IDE):** Visual Studio Code, with the Python and Django extensions

**Platform:** Apple silicon (arm64), single machine

**Language:** Python 3.12.14 (CPython), installed via Homebrew

**OS with version number:** macOS 26.4.1 (build 25E253)

**Compiler information:** None. Python is interpreted. The compiled C extensions in NumPy, pandas, scikit-learn, and matplotlib are installed as prebuilt macosx arm64 wheels from PyPI, so no local toolchain is required.

## Libraries used (all open source)

Table 1. Open source libraries, as installed and pinned in requirements.txt on 2026-09-12.

| Package | Version | Purpose |
|---|---|---|
| Django | 5.2.17 | Web framework, ORM, auth, admin |
| pandas | 3.0.5 | Chunked CSV reads and weekly aggregation |
| NumPy | 2.5.3 | Numeric backing for pandas and scikit-learn |
| SciPy | 1.18.1 | Required by scikit-learn |
| scikit-learn | 1.9.1 | Classifiers, metrics, permutation importance |
| joblib | 1.6.0 | Serializing fitted estimators to disk |
| matplotlib | 3.11.2 | The three result figures |
| pytest | 9.1.1 | Unit, integration and system tests |
| pytest-django | 4.14.0 | Django fixtures for pytest |

SQLite is used in development and for every test, through the Python standard library, so it is not a dependency. PostgreSQL is named in the SRS as a deployment option; no driver is installed and no test exercises it.

# Brief Build / Installation Instructions

From a clean checkout on the reference machine:

- `git clone` the repository and `cd` into `earlyalert/`
- `python3.12 -m venv .venv`
- `.venv/bin/python -m pip install -r requirements.txt`
- `.venv/bin/python manage.py migrate`
- `.venv/bin/python manage.py seed_accounts` to create `instructor1`, `advisor1`, and `admin1`, all with the password `earlyalert-dev`
- `.venv/bin/python manage.py runserver`
- Sign in as `admin1`, upload the seven OULAD CSV files at `/admin/upload/`, run Rebuild features at `/admin/rebuild/`, then Retrain at `/admin/retrain/`. Only then does the ranking page have anything to show.
- A newly seeded account has no presentation assignments. Assign presentations to `instructor1` and `advisor1` in the Django admin at `/django-admin/` before signing in as either.

`SECRET_KEY` is read from the environment variable `EARLYALERT_SECRET_KEY` and falls back to a development value; a deployment must set it. There is no `.env` file and no `DATABASE_URL`: the database is configured directly in settings.

The experiment that produces the result grid runs without the server or the database, because the `pipeline` package imports no Django:

```
.venv/bin/python -m pipeline.experiment --data /path/to/oulad --out results
```

# Version Number of Software

EarlyAlert 0.1.0. Dependency versions are pinned to exact releases in `requirements.txt`, the train-test split is by presentation year rather than at random, and every estimator is constructed with `random_state=42`, so a result reported in the final paper can be regenerated from a clean checkout and the same upload. The seed is a constant in `pipeline/train.py` rather than a stored field on each model version.

# Product Inventory (List of Files)

Table 2. Product inventory.

| Path | Contents |
|---|---|
| `earlyalert/` | settings, urls, wsgi, and `joblock.py`, the file lock that serializes the long jobs |
| `pipeline/` | `loader.py`, `validate.py`, `features.py`, `train.py`, `fairness.py`, `experiment.py`. Plain Python, pandas and scikit-learn, no Django imports. |
| `accounts/` | Role and presentation-assignment profile, the login and lockout views, and `authz.py`, the single authorization service enforcing NFR-4 |
| `cohorts/` | Presentation, Student, DataUpload; upload, validation and ingest; the student detail view and its flag-control rules |
| `features/` | WeeklyFeatures model and the rebuild job |
| `scoring/` | ModelVersion, RiskScore; the retrain job, the ranking page, CSV export and the cohort dashboard |
| `interventions/` | Flag, Intervention, InterventionOutcome; the flag state machine |
| `audit/` | AuditEntry, the append-only writer, and the filtered log view with CSV export |
| `templates/` | `base.html` plus one directory per app |
| `tests/` | 287 tests. `test_validate.py`, `test_loader.py`, `test_features.py`, `test_train.py`, `test_fairness.py`, `test_experiment.py` run against the pipeline with no database. `test_authz.py` and `test_phase1..7_usecases.py` cover TC1 through TC18 against the application. |
| `tests/fixtures/` | A synthetic 50-student, two-presentation, seven-file OULAD-shaped set, with `generate.py` that produces it deterministically. No real OULAD rows appear in any test. |
| `results/` | Experiment outputs. `grid.csv` is committed; the subgroup CSVs and figures are not. |
| `requirements.txt` | Pinned dependency versions |
| `README.md` | Setup, the endpoint table, and measured performance |
| `DECISIONS.md` | Evidence recorded against the SRS open items, and the gaps between the SRS and the data that the build had to settle |

Broken-upload variants are not stored as files. Each validation test copies the fixture set and corrupts one header in place, so the expected error message and the file that produced it sit together in the test.

# Known Issues

- **Week numbering.** The SRS gives `week = date // 7`, which contradicts its own statement that week w covers days 7(w-1) to 7w-1 and its own statement that negative dates map to week 0 or below. The build uses `date // 7 + 1`, which satisfies both, isolated in a single constant. This shifts every horizon by seven days and must be confirmed against the SRS before the results are quoted.
- **Module CCC** has no 2013 presentation, so the year-based split leaves it with no training data. Its rows are removed from the test split before any metric is computed, and the count is reported. Its students are still scored, because an advisor assigned to CCC/2014B needs a ranking; only the metrics exclude them. CCC is 21.5 percent of the week 8 test set, and including it changed which classifier won at every horizon.
- **The n < 20 suppression floor is fixed.** It never binds on the whole test year, where the smallest IMD group holds 596 students, and binds constantly on the per-presentation dashboard, where 6 of the 2014 presentations suppress at least one group. Whether the threshold should scale with cohort size is open.
- **Feature attribution** uses coefficients for logistic regression and permutation importance for the tree models. The two are not comparable, and the method used is not currently stored on the ModelVersion row, so model versions trained under different methods cannot be told apart from the database alone.
- **Collinear engagement features.** Total clicks, mean weekly clicks and max weekly clicks move together, so a linear model splits large opposing coefficients between them. Across the cohort the explanations are sensible, but for a student with extreme click counts the three shown can be click volume with contradictory directions.
- **Probability calibration.** With the SRS-mandated balanced class weights, logistic regression's output is systematically too high: its mean Brier score across the grid is 0.235, worse than predicting the base rate for everyone. Its ranking is unaffected. The ranking page labels the column "probability".
- **Week 4 and late registrants.** A student who registers on day 20 has one week of clickstream at the first horizon. Scoring from the registration date rather than the presentation start would fix it and would complicate the feature schema.
- **Partial and incremental uploads are not supported.** A cohort arrives as a complete seven-file set or not at all.

# Test Environment

Everything below runs on one machine. NFR-7 fixes the deployment target at a single host with 8 GB of RAM, which is what forces chunked reads in the feature pipeline. The development machine has more memory than that; the constraint is met by measurement rather than by the hardware, with a peak resident set of 1.55 GB during the full feature build.

Table 3. Test environment components and versions.

| Component | Version and detail |
|---|---|
| OS | macOS 26.4.1 (build 25E253) |
| CPU and RAM | Apple M5, 10 cores, 16 GB |
| Python | 3.12.14 (CPython), Homebrew |
| Django | 5.2.17 |
| Database (development and test) | SQLite 3.53.4, bundled with Python. All test cases execute against SQLite. |
| Database (deployment) | PostgreSQL, not exercised by the test cases; listed as the deployment option in the SRS |
| Web server | Django development server (`manage.py runserver`) over HTTP on localhost, as specified in SRS 3.3.1 |
| Browser | Safari and Chrome at 1920x1080 |
| Test runner | pytest 9.1.1 with pytest-django 4.14.0 |
| Data under test | OULAD (Kuzilek et al., 2017): 7 CSV files, 32,593 registrations, 10,655,280 studentVle rows, studentVle.csv 433 MB uncompressed |

Table 4. Measured performance on the reference machine, against the full dataset.

| Step | Target | Measured |
|---|---|---|
| Validate seven files, including SHA-256 of 433 MB | — | 0.4 s |
| Ingest 32,593 registrations | — | 0.8 s |
| Build weekly features from 10,655,280 rows | under 10 min | 21.6 s, peak RSS 1.55 GB |
| Write 1,136,133 WeeklyFeatures rows | — | 21.4 s |
| Retrain three horizons and rescore | — | 13 s |
| Full 36-cell experiment, including features and figures | — | 32 s |

**Test levels.** Unit tests run under pytest against the `pipeline` package with no database and no server. Integration tests run the pipeline against the database through the Django test client. System tests exercise the running application over HTTP. Eighteen test cases, TC1 through TC18, cover the functional requirements; 287 assertions-level tests execute them and the pipeline behaviour beneath them. All are automated; none is executed manually.

**Reproducibility.** Dependencies are pinned to exact versions, the train-test split is by presentation year and never random, every estimator is constructed with `random_state=42`, and the audit log records each upload, rebuild, and retrain with a timestamp and row count and cannot be edited or deleted. A clean checkout plus the same seven CSV files reproduces any reported result; the experiment is verified to produce an identical grid on a second run.

# References

Arnold, K. E., & Pistilli, M. D. (2012). Course Signals at Purdue: Using learning analytics to increase student success. In *Proceedings of the 2nd International Conference on Learning Analytics and Knowledge* (pp. 267-270). ACM. https://doi.org/10.1145/2330601.2330666

Django Software Foundation. (n.d.). *Django documentation* (Version 5.2). https://docs.djangoproject.com/

Kuzilek, J., Hlosta, M., & Zdrahal, Z. (2017). Open University Learning Analytics dataset. *Scientific Data, 4*, Article 170171. https://doi.org/10.1038/sdata.2017.171

Wolff, A., Zdrahal, Z., Nikolov, A., & Pantucek, M. (2013). Improving retention: Predicting at-risk students by analysing clicking behaviour in a virtual learning environment. In *Proceedings of the 3rd International Conference on Learning Analytics and Knowledge* (pp. 145-149). ACM. https://doi.org/10.1145/2460296.2460324
