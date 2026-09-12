# EarlyAlert: Design Document

Architecture and Detailed Design

Isit Pokharel
Harrisburg University of Science and Technology
CISC 699: Applied Project in Computer Science
Dr. Mike Shahine
September 14, 2026

*Revision 2. Revised against the built system on 2026-09-12: component names, routes, schema, message text and algorithms now describe what exists, and the placeholders in Sections 3.4 and 6 are replaced by measurements. Sections 2.4 and 3.6 record where the build departed from Revision 1 and why.*

# 1. Purpose and scope

EarlyAlert is a web application that predicts student withdrawal from online course presentations and supports the advisor workflow that follows a prediction. It ingests a cohort in Open University Learning Analytics Dataset (OULAD) format, ranks the students in a presentation by predicted withdrawal probability, shows the features driving each score, and records the outreach an advisor performs in response. Models are trained and evaluated at week 4, week 8, and week 12 of the term.

The application exists so that an experimental result has a workflow around it. That result is a grid of 36 cells — four feature sets by three horizons by three classifiers — described in Section 3.10 and produced by a harness that runs without the web application at all.

This document is the build blueprint. It specifies the components and their dependencies, the database schema down to field types and constraints, the interfaces between modules, and the algorithms carrying the two expensive jobs. Section 1.1 restates the requirements the design answers to, so the traceability table in Section 4 can be read without another document open.

EarlyAlert does not send messages to students, does not integrate with a live virtual learning environment, and does not make or recommend enrollment decisions.

## 1.1 Requirements the design answers to

Table 1. Requirements the design implements.

| ID | Requirement |
|---|---|
| FR-1 | Authenticate users by username and password. |
| FR-2 | Enforce three roles: instructor, advisor, administrator. |
| FR-3 | Restrict instructors and advisors to presentations assigned to them. |
| FR-4 | Display students in a presentation ranked by predicted withdrawal probability. |
| FR-5 | Display, for each scored student, the three features contributing most to the score. |
| FR-6 | Allow an advisor to flag a student for outreach. |
| FR-7 | Allow an advisor to record an intervention with type, date, and notes. |
| FR-8 | Allow an advisor to record an intervention outcome. |
| FR-9 | Display cohort-level risk distribution and subgroup breakdown. |
| FR-10 | Suppress any subgroup statistic computed from fewer than 20 students. |
| FR-11 | Accept upload of the seven OULAD-format CSV files. |
| FR-12 | Validate uploaded files against the expected schema before ingestion. |
| FR-13 | Reject uploads exceeding 500 MB per file. |
| FR-14 | Compute weekly engagement and assessment features from uploaded data. |
| FR-15 | Train a classifier for each configured horizon on demand. |
| FR-16 | Store evaluation metrics with each trained model version. |
| FR-17 | Log every flag, intervention, upload, and retrain with user and timestamp. |
| FR-18 | Export a risk ranking as CSV. |
| NFR-1 | Ranking pages load in under 3 seconds for presentations up to 2,000 students. |
| NFR-2 | A full feature rebuild completes in under 10 minutes on the reference machine. |
| NFR-3 | Passwords are stored using a salted adaptive hash. |
| NFR-4 | No user can view student data outside their assigned presentations. |
| NFR-5 | Audit entries are retained for the life of the deployment. |
| NFR-6 | Student identifiers are pseudonymous integers; no name or contact field is stored. |
| NFR-7 | The system runs on a single machine with 8 GB of RAM. |

# 2. Architecture

## 2.1 Architectural style

EarlyAlert is a three-tier monolith. A browser client renders server-generated HTML. A Django application server holds the views, the authorization layer, and the analytics pipeline. A relational database holds cohort data, derived features, model versions, scores, flags, and the audit log. Trained estimators are written to the application server filesystem and referenced by row in the ModelVersion table.

The whole system runs in one process on one machine. NFR-7 fixes that, and the constraint shapes most of what follows.

The pipeline package sits inside the application but imports nothing from Django, which is what lets it be tested without a server and run as a standalone experiment (Sections 2.2 and 3.10).

## 2.2 Component decomposition

Six Django apps, one project package, and one framework-independent package. The rightmost column gives the dependency rule for each.

Table 2. Component responsibilities and permitted imports.

| Component | Responsibility | May import |
|---|---|---|
| `pipeline` | Schema validation, CSV reading, weekly feature aggregation, cohort construction, training, evaluation, subgroup metrics, and the experiment harness. Plain Python, pandas, scikit-learn, matplotlib. | Nothing in this project. No Django imports. |
| `earlyalert` | Project package: settings, urls, wsgi, and `joblock.py`, the lock that serializes the two long jobs. | Django only. |
| `accounts` | Profile carrying role and the presentation assignment, login and lockout views, and `authz.py`, the single authorization service (Section 3.2). | cohorts, audit |
| `cohorts` | Presentation, Student, DataUpload. Upload handling, validation and ingest. The student detail view and the flag-control rules it displays. | pipeline, accounts, scoring, interventions, audit |
| `features` | WeeklyFeatures and the rebuild job. | pipeline, accounts, cohorts, audit |
| `scoring` | ModelVersion, RiskScore. The retrain job, the ranking page, CSV export, and the cohort dashboard. | pipeline, accounts, features, cohorts, interventions, audit |
| `interventions` | Flag, Intervention, InterventionOutcome and the flag state machine. | accounts, cohorts, audit |
| `audit` | AuditEntry, the append-only writer, and the filtered log view. | Nothing in this project. |

Two changes from Revision 1. The authorization service is a module inside `accounts` rather than an app of its own, because it has no models and an app with no models is a directory with a config class in it. The read-only views planned as a `dashboard` app live with the models they read: ranking, export and the cohort dashboard in `scoring`, student detail in `cohorts`. An app whose only job is to read other apps' tables adds a dependency edge without adding a boundary.

The `pipeline` package is the important boundary. It has no Django imports, so aggregation and training run under plain pytest with no database and no server. A pull request that adds `from django` anywhere under `pipeline/` should fail review.

## 2.3 Deployment view

One machine, no external service reachable at runtime.

- The Django development server in development and for every test case, as specified in SRS 3.3.1. A production deployment would put gunicorn behind nginx terminating HTTPS; neither is installed and no test exercises that path.
- SQLite in development and for all testing. PostgreSQL is named in the SRS as a deployment option; no driver is installed and no test exercises it. All access goes through the Django ORM, which keeps the two interchangeable.
- Trained estimators as joblib files under `BASE_DIR/artifacts/`, named by horizon, classifier and ModelVersion primary key.
- Uploaded CSVs under `BASE_DIR/uploads/<batch_id>/`, retained after ingestion so a rebuild can be rerun without re-uploading 433 MB.
- Job lock and progress logs under `BASE_DIR/jobs/`.
- No outbound network calls: no hosted model API, no telemetry, no external lookup.

## 2.4 Architecturally significant decisions

**Authorization in one service layer.**
The alternative was a per-view decorator. That is what the Django documentation shows and what most projects do, and it is easier to read at the point of use. I went the other way because one missed decorator is one open route, and thirteen views in this system touch student data. A single check that every query passes through fails closed; a decorator scheme fails open.

**No worker process.**
Celery with a broker and a separate worker would be the standard answer for the rebuild and the training job, and it would keep the web tier responsive during a long run. NFR-7 fixes deployment at one 8 GB machine. A second process and a message broker double the environment someone has to recreate, for a job that runs perhaps six times a term. Jobs are triggered by an administrator and guarded by a lock (below).

**A framework-independent pipeline package.**
Aggregation and training could sit inside the Django apps as model methods or management commands. Fewer files, and no import discipline to maintain. Testability decided it. The feature builder is the component most likely to be wrong and the hardest to debug through a browser. Keeping it importable without a settings module means it can be run against a 50-student fixture in under a second — and, as it turned out, means the whole experiment in Section 3.10 runs with no database at all.

**A file lock rather than a database row.**
Revision 1 specified a database-backed lock: one row with a unique constraint. The build uses a lock file created with `O_CREAT|O_EXCL`, for two reasons. The SRS fixes the data model at ten entities, and a lock table is an eleventh that carries no domain meaning. And a lock row inside a transaction that the job itself may roll back is a lock that can vanish while the job is still running. A file outside the transaction cannot. Staleness is handled by recording the holder's process id and breaking a lock whose process is gone, which a database row would have needed anyway.

# 3. Detailed design

## 3.1 Data model

Ten entities. Field types are given in Django terms. Every table carries an implicit auto-increment primary key unless stated. A Student row is one registration, not one person.

Table 3. Profile and presentation assignment fields.

| Field | Type | Notes |
|---|---|---|
| `User.username` | CharField(150), unique | Django's built-in user. Login identifier (FR-1). |
| `User.password` | CharField(128) | Django salted PBKDF2-SHA256 hash (NFR-3). |
| `Profile.user` | OneToOneField User | Role and lockout state hang off the built-in user rather than replacing it, so `django.contrib.auth` and the Django admin work unmodified. |
| `Profile.role` | CharField(20), choices | instructor \| advisor \| administrator (FR-2). |
| `Profile.assignments` | ManyToManyField Presentation | The relation FR-3 is enforced against. Administrators hold no assignments and see every presentation. |
| `Profile.failed_attempts` | IntegerField, default 0 | Reset on a successful login. |
| `Profile.is_locked` | BooleanField, default False | Set at five consecutive failures; cleared only by an administrator. |

Table 4. Presentation and Student fields.

| Field | Type | Notes |
|---|---|---|
| `Presentation.code_module` | CharField(3) | For example AAA. |
| `Presentation.code_presentation` | CharField(5) | For example 2013J. |
| `Presentation.start_date` | DateField | Derived from the presentation code: B is the February intake, J the October one. No OULAD file carries a calendar start date; day offsets are always relative to day 0, so only the year is read anywhere. |
| `Presentation.length_days` | IntegerField | `module_presentation_length` as published. Stored in days rather than weeks so the week grid is derived in one place. |
| | | unique constraint (code_module, code_presentation). |
| `Student.id_student` | IntegerField, indexed | Pseudonymous. No name, email, address, or telephone column exists (NFR-6). |
| `Student.presentation` | FK Presentation | One row is one registration, not one person. |
| `Student.gender, region, highest_education, imd_band, age_band` | CharField | Subgroup dimensions for the cohort dashboard (FR-9). The literal string `?` in the source is mapped to NULL on read. `imd_band` is normalized on ingest: OULAD writes `10-20` without the `%` every other band carries. |
| `Student.disability` | BooleanField | `Y`/`N` in the source. |
| `Student.num_prev_attempts, studied_credits` | IntegerField | |
| `Student.date_registration` | IntegerField, null | Integer days from day 0; negative before the start. |
| `Student.date_unregistration` | IntegerField, null | Null means no withdrawal. The null is the signal and is never filled. |
| `Student.final_result` | CharField(12) | The model label; Withdrawn is the positive class. |
| | | unique constraint (presentation, id_student). |

Table 5. WeeklyFeatures fields.

| Field | Type | Notes |
|---|---|---|
| `student` | FK Student | |
| `week` | IntegerField | Signed. Week 1 is days 0-6; pre-start activity falls in week 0 and below (Section 3.4). |
| `total_clicks` | IntegerField | Sum of `sum_click` in the week. |
| `active_days` | IntegerField | Distinct dates with a click, deduplicated across chunk boundaries. |
| `clicks_per_active_day` | FloatField | 0.0 when `active_days` is 0. Revision 1 specified NULL; the column is consumed by estimators that do not accept missing values, and a separate presence indicator would carry the same information at the cost of a column. |
| `days_since_last_activity` | IntegerField | Measured from the last day of the week, carried forward through quiet weeks. |
| `assessments_submitted` | IntegerField | Cumulative count of assessments due by this week that were submitted. |
| `assessments_late` | IntegerField | Of those, the count submitted after the due day. |
| `mean_score` | FloatField, null | Unweighted mean over assessments due to date; unsubmitted past due counts as 0; not yet due contributes nothing. Null until the first assessment falls due. |
| `weighted_score_to_date` | FloatField, null | Weighted by assessment weight and divided by the weight due to date, so it reads as a running average out of 100. |
| `activity_type_shares` | JSONField | Share of the week's clicks per VLE activity type. Empty for a week with no activity. |
| | | unique constraint (student, week); index on (student, week). |

Table 6. DataUpload, ModelVersion, and RiskScore fields.

| Field | Type | Notes |
|---|---|---|
| `DataUpload.filename` | CharField(60) | One of the seven expected names. |
| `DataUpload.checksum` | CharField(64) | SHA-256 hex digest. |
| `DataUpload.row_count` | IntegerField | Displayed for verification. |
| `DataUpload.uploaded_by` | FK User, PROTECT | |
| `DataUpload.batch_id` | UUIDField | Seven rows share one batch id; a batch is accepted or rejected whole. |
| `DataUpload.superseded` | BooleanField | Rows are never deleted. A replacement sets this on the previous batch. |
| `ModelVersion.horizon_week` | IntegerField | 4, 8, or 12. |
| `ModelVersion.feature_set, classifier` | CharField | Named, so a version is reproducible. |
| `ModelVersion.auc_roc, auc_pr, brier, recall_at_p50` | FloatField | Overall metrics, as separate columns so the ranking page and the admin can sort and filter on them without unpacking JSON. |
| `ModelVersion.subgroup_metrics` | JSONField | One nested object per dimension and level. A suppressed level is present by name with `{"suppressed": true, "n": n}` and no metric, never dropped. |
| `ModelVersion.artifact_path` | CharField | joblib file, relative to BASE_DIR. |
| `ModelVersion.training_rows, test_rows` | IntegerField | |
| `ModelVersion.is_current` | BooleanField | One current version per horizon. Superseded versions are kept. |
| `RiskScore.student, .model_version` | FK, FK | unique constraint (student, model_version). |
| `RiskScore.probability` | FloatField | Indexed with model_version, descending; this index is what holds NFR-1. |
| `RiskScore.top_features` | JSONField | Three objects: feature, value, direction, and the behavior phrasing shown to the user. |

The random seed is fixed at 42 as a constant in `pipeline/train.py` and is not a stored column, and the attribution method is determined by the estimator type rather than recorded per version. Revision 1 specified both as fields. Section 6 keeps the question open; nothing currently varies either one between versions.

Table 7. Outreach and audit fields.

| Field | Type | Notes |
|---|---|---|
| `Flag.student, .raised_by` | FK Student, FK User | |
| `Flag.reason` | TextField, blank | Optional at creation. Notes are appended to it with a timestamp and username; nothing already written is overwritten. |
| `Flag.status` | CharField, choices | See Section 3.6. |
| `Flag.created_at, .closed_at` | DateTimeField | |
| `Intervention.flag` | FK Flag | Several per flag are permitted. |
| `Intervention.intervention_type` | CharField, choices | email \| phone \| meeting \| referral. |
| `Intervention.date` | DateField | Constrained to the window [flag.created_at.date(), today], inclusive at both ends. |
| `Intervention.notes` | TextField | |
| `InterventionOutcome.intervention` | OneToOneField Intervention | At most one; corrections are new interventions. |
| `InterventionOutcome.outcome` | CharField, choices | re_engaged \| responded_withdrew \| no_response \| referred. |
| `AuditEntry.timestamp, .user, .role, .action, .target` | Mixed | Append-only (Section 3.7); indexed on timestamp and on action. `user` is nullable, for a failed login against an unknown username. |

## 3.2 Authorization service

One module, `accounts/authz.py`, called by every view that touches student data. NFR-4 is enforced here and nowhere else.

```
def presentations_for(user) -> QuerySet[Presentation]
def assert_can_view(user, presentation) -> None
def assert_is_admin(user) -> None
def is_admin(user) -> bool
```

`assert_can_view` raises `PermissionDenied` when the presentation is absent from `presentations_for`. Django renders that as HTTP 403. The service writes an audit entry with action `denied` and the target identifier **before** raising, so a probing attempt is recorded whether or not the caller sees anything.

Every queryset that reaches a template is filtered by the result of `presentations_for`, never by a value taken from the session or the URL. The session holds only the currently selected presentation, and selecting it goes through the same check. `assert_is_admin` guards the four administrative routes on the same pattern.

## 3.3 Routes and views

Table 8. Routes, views, roles, and requirements served.

| Route | View | Role | Workflow | Requirements |
|---|---|---|---|---|
| `/login/`, `/logout/` | `login_view`, `logout_view` | any | Sign in and out | FR-1, FR-2, NFR-3 |
| `/presentations/` | `presentation_selector` | any | Select presentation and horizon | FR-3 |
| `/presentations/<id>/ranking/?horizon=8` | `ranking_view` | assigned | Risk ranking | FR-4, NFR-1, NFR-4 |
| `/presentations/<id>/ranking/export.csv` | `ranking_export` | assigned | Export ranking | FR-18, FR-17 |
| `/students/<id>/` | `student_detail` | assigned | Student detail | FR-5, NFR-6 |
| `/students/<id>/flag/` | `create_flag_view` | advisor | Raise flag | FR-6, FR-17 |
| `/flags/<id>/intervention/` | `record_intervention_view` | advisor | Record intervention, add note | FR-7, FR-17 |
| `/interventions/<id>/outcome/` | `record_outcome_view` | advisor | Record outcome | FR-8, FR-17 |
| `/presentations/<id>/dashboard/?dim=imd_band` | `dashboard_view` | assigned | Cohort dashboard | FR-9, FR-10 |
| `/admin/upload/` | `upload_view` | administrator | Upload cohort | FR-11, FR-12, FR-13 |
| `/admin/rebuild/` | `rebuild_view` | administrator | Rebuild features | FR-14, NFR-2, NFR-7 |
| `/admin/retrain/` | `retrain_view` | administrator | Retrain model | FR-15, FR-16, FR-10 |
| `/admin/audit/`, `?export=csv` | `audit_view` | administrator | Audit log and log export | FR-17, NFR-5 |
| `/django-admin/` | Django admin | administrator | User accounts, roles, assignments | FR-2, FR-3 |

The student detail route is flat rather than nested under a presentation. The presentation is reached through the student, and a nested route would carry a presentation identifier that the view must either ignore or check for agreement with the student's own — a second identifier that can disagree with the first.

## 3.4 Feature construction

The rebuild decides whether NFR-7 is survivable. `studentVle.csv` holds 10,655,280 rows in a file of 433 MB, so the read is chunked at 500,000 rows.

Per-week aggregation needs every row for a student to land in the same bucket, and chunk boundaries cut across students. Two ways out: sort the file first, or accumulate partial sums per chunk and merge at the end. The second is chosen; sorting a 433 MB file costs more than the aggregation.

**Week numbering.** Week w covers days 7(w-1) to 7w-1, so week 1 is days 0-6 and pre-start activity falls in week 0 or below. The mapping is therefore `week = date // 7 + 1`. The SRS gives `week = date // 7`, which contradicts both its own statement of the week boundaries and its own statement that negative dates map to week 0 or below. The convention is isolated in a single constant, `WEEK_OFFSET`. This shifts every horizon by seven days and is the one open item in Section 6 that changes every reported number.

**Algorithm.**

- Acquire the job lock (Section 2.4). A second rebuild request finds the lock held and is refused, not queued.
- For each chunk of studentVle: map date to week; group by (module, presentation, student, week) and accumulate three partial results — the click sum, the distinct (student, date) pairs, and the click sum per VLE activity type. Each accumulator is re-aggregated after every chunk, so its size is bounded by the number of distinct groups rather than by the rows read.
- Distinct dates are deduplicated rather than counted per chunk, because one student-day appears once per site visited that day and can straddle a chunk boundary.
- After the last chunk, derive `active_days`, `clicks_per_active_day`, and `days_since_last_activity` from the latest activity day against the week boundary, carried forward through quiet weeks.
- Build a dense grid from each student's first recorded week to the last week of the presentation, so a week with no activity is present and carries zeros. A student with no studentVle rows at all gets no rows, which is what the detail view reports as "No engagement data recorded yet."
- Join studentAssessment to assessments on id_assessment. An assessment whose due date has passed with no submission row contributes a score of 0; one not yet due contributes nothing; one with no due date recorded is never due and is excluded.
- Write WeeklyFeatures in a single transaction, deleting the previous rows first, in batches of 20,000 model instances so a full rebuild never holds a million of them at once.
- Release the lock. Write an audit entry carrying the row count, and display the progress log.

A failure at any step rolls back the write, releases the lock, and leaves the previous rebuild in place.

**Measured.** Against the full dataset on the reference machine: 22 chunks, 21.6 s, peak resident set 1.55 GB, producing 1,136,133 rows for 29,228 students; the database write is a further 21.4 s. NFR-2 allows ten minutes and NFR-7 allows 8 GB. The 500,000-row chunk figure was an estimate in Revision 1 and is now measured; it is comfortably conservative and is left alone.

## 3.5 Training and scoring

Triggered on demand by an administrator, under the same lock, one current model per horizon (FR-15).

- **Build the cohort.** Exclude any student whose `date_unregistration` falls on or before day 7w; a student who has already left is not a prediction target, and keeping them lets the model read the label from data recorded after the fact.
- **Assemble features.** One matrix carries all three sets, prefixed `d__`, `a__` and `e__`, so D, A, E and All share identical one-hot columns and differ only in which columns are selected. Missing IMD band is its own one-hot level. An assessment score that does not yet exist is accompanied by a presence indicator rather than silently filled.
- **Split by presentation year**, training on 2013 and testing on 2014. Never random. No student registration appears on both sides.
- **Exclude modules with no training year** from the test split before any metric is computed. Module CCC has only 2014 presentations, so a model scored on it measures generalization to an unseen module rather than to an unseen year. CCC is 21.5 percent of the week 8 test set. Its students are still scored, because an advisor assigned to CCC/2014B needs a ranking; only the metrics exclude them.
- **Refuse to proceed** if the cohort holds fewer than 500 rows or fewer than 50 withdrawals, or if the training split holds fewer than 500 rows. Both floors are set so a 20-student subgroup can exist inside the test split; neither comes from a power calculation. A refusal writes `retrain_failed` with the reason, and the whole run is one transaction, so a refusal at week 8 undoes week 4.
- **Fit and evaluate** four classifiers — a majority baseline, logistic regression with balanced class weights and a scaler, a 300-tree random forest, and histogram gradient boosting — reporting AUC-ROC, AUC-PR, Brier score, recall at precision 0.50, and calibration bins. `random_state=42` throughout. A split left with one class raises `DegenerateSplit` and is skipped with a reason rather than crashing.
- **Select the best** non-baseline model by AUC-PR, mark it current for that horizon, store every candidate so the baseline stays on the record beside the models that beat it, and joblib the selected estimator.
- **Compute subgroup metrics** on the selected model across IMD band, disability, age band, highest education and gender. A level under 20 is recorded by name with `suppressed` and its count, never dropped.
- **Score every student of the test year** and write RiskScore rows carrying the probability and the top three contributing features, translated into the behavior phrasing the detail view shows.

Feature attribution uses the fitted coefficients for logistic regression and permutation importance computed once on the test set for the tree models. The two are not comparable. Permutation importance is unsigned, so the direction shown to an advisor is taken from the sign of the feature's correlation with the label in training against the student's own position relative to the training median — an approximation, stated as one.

## 3.6 Flag lifecycle

An advisor closes a flag by recording one of four outcomes, and each implies different behavior the next time someone opens that student, so there are four closure states.

Table 9. Flag states and re-flag behavior.

| State | Entered when | Re-flag permitted? |
|---|---|---|
| `open` | Advisor creates the flag (FR-6). | No. The flag's own page carries the note form, and the detail view links to it. |
| `closed_resolved` | Outcome is student re-engaged. | No, for the rest of the presentation. The reason is shown in place of the control. |
| `closed_no_response` | Outcome is no response. | Yes, with no duplicate warning. |
| `closed_referred` | Outcome is referred elsewhere. | Yes. |
| `closed_withdrew` | Outcome is student responded and withdrew. | Not in practice: such a student carries a `date_unregistration`, which disables the control and shows the withdrawal day. |

Revision 1 placed an "Add note to existing flag" control on the student detail page. The build puts the note form on the flag's own page instead, which is where the interventions and outcomes already are, and links to it from the detail page — one place where a case is worked rather than two.

Revision 1 also had `closed_referred` show "a referral is outstanding" on re-flagging. Not built; the open referral is visible in the flag history on the detail page.

Transitions are one-way and nothing reopens. A correction is recorded as a new intervention with its own outcome, which keeps the history append-only.

## 3.7 Audit writer

One function, `audit.services.record(user, action, target)`. It is the only code permitted to create an AuditEntry, and the audit write for a rejected action sits outside the transaction that rolls back, so a refusal is recorded rather than undone with the work.

Three mechanisms enforce append-only. `AuditEntry.save` raises on any call carrying a primary key. `delete` is overridden to raise, on the instance and on the queryset, and `update` raises on the queryset. In a PostgreSQL deployment the database user would additionally hold INSERT and SELECT on the audit table and neither UPDATE nor DELETE; that is a deployment step and is not exercised by any test case, so only the two application-level mechanisms are currently in force.

Actions written: `login`, `login_failed`, `login_locked`, `denied`, `flag_created`, `flag_note_added`, `intervention_recorded`, `outcome_recorded`, `upload_accepted`, `upload_rejected`, `rebuild_started`, `rebuild_completed`, `retrain_started`, `retrain_completed`, `retrain_failed`, `export`. These strings are constants in one module and are not written as literals anywhere else.

Every state-changing action writes exactly one entry. A refused rebuild writes none, because the action list has `rebuild_started` and `rebuild_completed` and no `rebuild_failed`, and a refusal changes no state; a refused retrain writes `retrain_failed`, because the list does have one. That asymmetry is inherited from the SRS and is asserted in TC14 and TC16 so it cannot drift silently.

`flag_note_added` appears in the SRS action list but no endpoint in the SRS endpoint table can produce it. The note form posts to `/flags/<id>/intervention/` and is distinguished by the field submitted, rather than adding a route the SRS does not have.

## 3.8 User-facing messages

Collected here because more than one view can produce several of them, and the test cases assert on the exact text.

Table 10. User-facing message text.

| Condition | Message |
|---|---|
| Bad credentials or unknown username | `Credentials rejected.` |
| Account locked | `This account is locked. Contact an administrator to unlock it.` |
| Presentation not assigned | `You are not assigned to this presentation.` |
| Administrative route, non-administrator | `Administrator role required.` |
| No assignment at all | `No presentations are assigned to you. An administrator assigns presentations in the Django admin.` |
| No model at this horizon | `No model is current at week N. An administrator needs to retrain before a ranking exists.` |
| No scores for this presentation | `No students in this presentation have a stored risk score at week N. Models are trained on the 2013 presentations and score the 2014 ones.` |
| Student has no engagement data | `No engagement data recorded yet.` |
| Student withdrawn | `this student unregistered on day N` |
| Future intervention date | `the date cannot be in the future (today is DATE)` |
| Intervention dated before its flag | `the date cannot be before the flag was raised on DATE` |
| Unknown intervention type | `unknown intervention type 'X'` |
| Subgroup under threshold | `suppressed (n < 20)` |
| Every subgroup under threshold | `Every group in this breakdown has fewer than 20 students, so the table is suppressed in full.` |
| Cohort too small | `This presentation has N scored students, fewer than the 10 needed for any aggregate. No breakdown is shown.` |
| Schema mismatch | `studentVle: expected column sum_click at position 6, found clicks` |
| Empty filter result | `No entries match these filters.` |

The first message is identical for a wrong password and an unknown username, so the form does not reveal which accounts exist. Revision 1 additionally required the login view to pad its response to a fixed time floor. That is not built. Django's authentication backend runs a dummy password hash for an unknown username, which equalizes the common case, but a locked account is refused before any hash is computed and therefore returns measurably sooner. Section 6 keeps it open.

## 3.9 Repository layout

```
earlyalert/
  manage.py
  requirements.txt          pinned to exact versions
  README.md                 setup, endpoints, measured performance
  DECISIONS.md              evidence on the open issues; SRS-versus-data gaps
  earlyalert/               settings, urls, wsgi, joblock.py
  pipeline/                 NO DJANGO IMPORTS
    loader.py               read_csv wrappers, na_values="?"
    validate.py             schema validator
    features.py             chunked weekly aggregation
    train.py                cohort, split, fit, evaluate, attribution
    fairness.py             subgroup metrics with suppression
    experiment.py           the 36-cell grid and the figures
  accounts/                 Profile, authz.py, login and lockout, seed_accounts
  cohorts/                  Presentation, Student, DataUpload, upload, ingest, detail
  features/                 WeeklyFeatures, rebuild job
  scoring/                  ModelVersion, RiskScore, retrain, ranking, export, dashboard
  interventions/            Flag, Intervention, InterventionOutcome, workflow rules
  audit/                    AuditEntry, append-only writer, filtered log view
  templates/                base.html plus one directory per app
  tests/                    287 tests; see Table 12 for the TC mapping
    fixtures/               synthetic 50-student seven-file set and its generator
  results/                  experiment outputs; grid.csv committed, the rest ignored
  docs/                     this document and the project synopsis
```

Broken-upload variants are not stored as files. Each validation test copies the fixture set and corrupts one header in place, so the expected error message and the file that produced it sit together in the test. The fixture set is produced by a deterministic generator, `tests/fixtures/generate.py`, whose docstring states the rules the expected values in the tests are computed from. No real OULAD rows appear in any test.

## 3.10 Experiment harness

The application answers "who should an advisor contact"; the experiment answers "how well can that be known, and how early". `pipeline/experiment.py` runs standalone:

```
python -m pipeline.experiment --data /path/to/oulad --out results
```

It builds the weekly features, then fits every cell of a 4 x 3 x 3 grid — feature sets D, A, E and All, horizons 4, 8 and 12, classifiers logistic regression, random forest and histogram gradient boosting — and writes `grid.csv`, `baselines.csv`, one subgroup CSV per horizon, three figures, and a note explaining how to read them. 36 cells in 32 seconds, with no database and no server.

The majority baseline is not a cell of the grid. It ignores the features, so it has one value per horizon rather than one per cell, and it is written separately.

**Every row carries the baseline it sits on and the ratio to it, and that is not decoration.** AUC-PR's floor is the positive rate, and the positive rate falls at every horizon because the cohort filter removes students who have already unregistered, from 0.167 at week 4 to 0.117 at week 12. Raw AUC-PR therefore falls with the horizon even where the model is improving. Horizons must be compared on the ratio; within one horizon, raw AUC-PR compares cells correctly.

# 4. Requirements traceability

Table 11. Requirements traced to components.

| Requirement | Component | Section |
|---|---|---|
| FR-1, FR-2 | `accounts.views.login_view`, `accounts.Profile.role` | 3.1, 3.3 |
| FR-3 | `accounts.authz.assert_can_view`, `Profile.assignments` | 3.2 |
| FR-4 | `scoring.views.ranking_view`, `scoring.RiskScore` | 3.1, 3.3 |
| FR-5 | `RiskScore.top_features`, `pipeline.train.top_features` | 3.5 |
| FR-6, FR-7, FR-8 | `interventions.services`, Flag, Intervention, InterventionOutcome | 3.6 |
| FR-9 | `scoring.views.dashboard_view`, `scoring.summary` | 3.3 |
| FR-10 | `pipeline.fairness.subgroup_metrics` and `scoring.summary.subgroup_summary`; applied in the dashboard and in evaluation | 3.5, 3.10 |
| FR-11, FR-12, FR-13 | `cohorts.uploads.accept_upload`, `pipeline.validate` | 3.3 |
| FR-14 | `pipeline.features`, `features.rebuild` | 3.4 |
| FR-15, FR-16 | `pipeline.train`, `scoring.retrain`, `scoring.ModelVersion` | 3.5 |
| FR-17 | `audit.services.record` | 3.7 |
| FR-18 | `scoring.views.ranking_export` | 3.3 |
| NFR-1 | RiskScore (model_version, -probability) index; pre-computed rows read at request time | 3.1 |
| NFR-2 | Chunked rebuild at 500,000 rows | 3.4 |
| NFR-3 | Django PBKDF2-SHA256 password hasher | 3.1 |
| NFR-4 | `accounts.authz` service layer | 3.2 |
| NFR-5 | AuditEntry save/delete/update overrides; database grants in deployment | 3.7 |
| NFR-6 | Student schema carries no direct identifier | 3.1 |
| NFR-7 | Single-process deployment; chunked reads; batched writes | 2.3, 3.4 |

# 5. Verification

## 5.1 Test strategy

Twenty test cases cover the design at three levels. Unit tests exercise the pipeline package under pytest with no database and no server. That is what the framework independence in Section 2.2 is for. Integration tests exercise the pipeline against the database without a browser. System tests exercise the running application through Django's test client over the full request cycle, including authentication, authorization and template rendering.

Every functional and non-functional requirement in Section 1.1 is verified at least once. The twenty cases are executed by 287 automated tests; none is executed manually. Revision 1 recorded TC1 and TC2 as manual browser cases; both are automated.

Two cases produce a number as well as a verdict. TC4 records page load time against the 3-second budget in NFR-1, and TC13 records elapsed time and peak memory against NFR-2 and NFR-7. Both numbers are now measured and appear in the tables below.

## 5.2 Test case inventory

Table 12. Test case inventory and the modules implementing each.

| TC | Title | Level | Requirements | Implemented in |
|---|---|---|---|---|
| TC1 | Login and session (main flow) | System | FR-1, FR-2, FR-17, NFR-3 | `test_phase1_usecases.py` |
| TC2 | Login rejected and account lockout | System | FR-1, FR-17, NFR-3 | `test_phase1_usecases.py` |
| TC3 | Select presentation | System | FR-3, NFR-4 | `test_phase1_usecases.py` |
| TC4 | View risk ranking | System | FR-4, NFR-1 | `test_phase5_usecases.py` |
| TC5 | Permission denied on ranking | System | FR-3, NFR-4, FR-17 | `test_authz.py`, `test_phase5_usecases.py` |
| TC6 | View student detail | System | FR-5, NFR-6 | `test_phase5_usecases.py` |
| TC7 | Flag student for outreach | System | FR-6, FR-17 | `test_phase6_usecases.py` |
| TC8 | Record intervention | System | FR-7, FR-17 | `test_phase6_usecases.py` |
| TC9 | Record outcome and close flag | System | FR-8, FR-17 | `test_phase6_usecases.py` |
| TC10 | Cohort dashboard and subgroup suppression | System | FR-9, FR-10 | `test_phase5_usecases.py` |
| TC11 | Upload cohort data | System | FR-11, FR-12, FR-13, FR-17 | `test_phase2_usecases.py` |
| TC12 | Upload rejected | System | FR-11, FR-12, FR-13, FR-17 | `test_phase2_usecases.py` |
| TC13 | Rebuild features | Integration | FR-14, FR-17, NFR-2, NFR-7 | `test_phase3_usecases.py` |
| TC14 | Rebuild blocked | Integration | FR-14 | `test_phase3_usecases.py` |
| TC15 | Retrain model and store metrics | Integration | FR-15, FR-16, FR-10, FR-17 | `test_phase4_usecases.py` |
| TC16 | Retrain blocked | Integration | FR-15 | `test_phase4_usecases.py` |
| TC17 | Audit log review and filter | System | FR-17, NFR-5 | `test_phase7_usecases.py` |
| TC18 | Export ranking as CSV | System | FR-18, FR-17 | `test_phase5_usecases.py` |
| TC19 | Schema validator unit tests | Unit | FR-12 | `test_validate.py`, `test_loader.py` |
| TC20 | Feature builder and suppression rule unit tests | Unit | FR-14, FR-10 | `test_features.py`, `test_fairness.py`, `test_train.py`, `test_experiment.py` |

## 5.3 TC1: Login and session (system, main flow)

Table 13. TC1 test case record.

| | |
|---|---|
| Test Case Number: | TC1 |
| Revision: | Rev. 2 |
| Author: | Isit Pokharel |
| Date Conducted: | 2026-09-12 |
| Test Conductor: | Isit Pokharel |
| Customer Representative: | Isit Pokharel |
| Description: | Tests authentication with valid credentials, session role assignment, audit logging of the login, and redirection to the requested page. |
| Pre-Test Setup: | EarlyAlert on the reference machine with a fresh database migrated to the current schema. Test accounts exist: instructor1 (instructor), advisor1 (advisor), admin1 (administrator). advisor1 and instructor1 are assigned to one presentation only. |
| Use Case: | Sign in |
| Flow: | Main Flow |
| Test Level: | System |
| Requirements Verified: | FR-1, FR-2, FR-17, NFR-3 |

Table 14. TC1 steps and expected results.

| User Action | Expected Results | P/F | Comment |
|---|---|---|---|
| 1. Request `/presentations/` without an active session. | Redirected to `/login/`. Login form is displayed with username and password fields; the password field is masked. | Pass | FR-1 |
| 2. Enter advisor1 and the correct password; submit. | Redirected to `/presentations/`. Header shows the username and the role. | Pass | FR-1, FR-2 |
| 3. Repeat for instructor1 and admin1. | Each reaches `/presentations/` and the header shows that account's role. | Pass | FR-2 |
| 4. Read the audit log. | Exactly one entry per sign-in with action `login`, the correct user, and target `user:<username>`. | Pass | FR-17 |
| 5. Inspect the stored password for advisor1. | Value begins with `pbkdf2_sha256$`. No plaintext is present. | Pass | NFR-3 |
| 6. Log out from the header. | Login page is displayed. Requesting `/presentations/` again redirects to the login form. | Pass | FR-1 |

Post-conditions: a session exists for advisor1 until logout in step 6, after which none does. One audit entry with action `login` per sign-in is retained.

## 5.4 TC5: Permission denied on ranking (system, exception flow)

Table 15. TC5 test case record.

| | |
|---|---|
| Test Case Number: | TC5 |
| Revision: | Rev. 2 |
| Author: | Isit Pokharel |
| Date Conducted: | 2026-09-12 |
| Test Conductor: | Isit Pokharel |
| Customer Representative: | Isit Pokharel |
| Description: | Tests that a user cannot view a presentation they are not assigned to, by URL manipulation. This is the check the authorization service in Section 3.2 exists to enforce. |
| Pre-Test Setup: | As TC1. advisor1 is assigned to presentation A and not to presentation B. |
| Use Case: | Risk ranking |
| Flow: | Exception Flow |
| Test Level: | System |
| Requirements Verified: | FR-3, NFR-4, FR-17 |

Table 16. TC5 steps and expected results.

| User Action | Expected Results | P/F | Comment |
|---|---|---|---|
| 1. Log in as advisor1 and read the identifier of presentation B from the database. | Login succeeds. Presentation B does not appear in the selector and is absent from `presentations_for`. | Pass | FR-3 |
| 2. Request `/presentations/<B>/ranking/` directly. | HTTP 403. Message reads "You are not assigned to this presentation." | Pass | NFR-4 |
| 3. Inspect the full response payload. | No pseudonymous id, probability, or feature value is present. No count of students in presentation B is disclosed. | Pass | NFR-4 |
| 4. Repeat for the dashboard, the CSV export, a student detail page in B, and selecting B in the selector. | Each returns 403 and discloses nothing. The export writes no `export` entry. | Pass | NFR-4 |
| 5. Read the audit log. | An entry with action `denied`, user advisor1, and target `presentation:<B>` exists for each attempt, timestamped to the request. | Pass | FR-17 |

Post-conditions: no records are created and advisor1's session context is unchanged. One audit entry with action `denied` per attempt is retained.

## 5.5 TC13: Rebuild features (integration, main flow)

Table 17. TC13 test case record.

| | |
|---|---|
| Test Case Number: | TC13 |
| Revision: | Rev. 2 |
| Author: | Isit Pokharel |
| Date Conducted: | 2026-09-12 |
| Test Conductor: | Isit Pokharel |
| Customer Representative: | Isit Pokharel |
| Description: | Tests weekly feature construction from a complete upload, the rows written, and elapsed time and peak memory against NFR-2 and NFR-7. Exercises the chunked algorithm in Section 3.4. |
| Pre-Test Setup: | As TC1, with a valid seven-file OULAD upload in place and not superseded. |
| Use Case: | Rebuild features |
| Flow: | Main Flow |
| Test Level: | Integration |
| Requirements Verified: | FR-14, FR-17, NFR-2, NFR-7 |

Table 18. TC13 steps and expected results.

| User Action | Expected Results | P/F | Comment |
|---|---|---|---|
| 1. As admin1, start "Rebuild features." Start a wall clock and a memory monitor. | Job lock is taken. Progress log shows chunk number, rows read, student-weeks accumulated, and elapsed time. | Pass | FR-14 |
| 2. Watch the run to completion. | **21.6 s to build and 21.4 s to write, against the 10-minute target. Peak resident set 1.55 GB, against the 8 GB limit.** 22 chunks of 500,000 rows; the file is never held whole. | Pass | NFR-2, NFR-7 |
| 3. Read the completion summary. | **1,136,133 rows written for 29,228 students.** Rows run from each student's first recorded week to the end of the presentation; the 3,365 students with no studentVle activity correctly have none. | Pass | FR-14 |
| 4. Pick one student and one week. Compute total_clicks directly from studentVle.csv with pandas and compare against the stored row. | The two values are equal. The comparison imports nothing from the feature builder. | Pass | FR-14 |
| 5. Re-run the builder with a different chunk size against the fixture cohort. | The output frame is identical, including active-day counts that span a chunk boundary. | Pass | FR-14 |
| 6. Find an assessment unsubmitted past its due date. | The zero is carried into `mean_score`: the fixture student's mean falls from 40 to 20 in the week the unsubmitted second assessment falls due. | Pass | FR-14 |
| 7. Read the audit log. | One `rebuild_started` and one `rebuild_completed`, the latter carrying the row count. | Pass | FR-17 |

Post-conditions: WeeklyFeatures rows exist for every student with recorded activity, from their first active week to the end of the presentation. The job lock is released and two audit entries record the run.

## 5.6 TC19: Schema validator (unit)

Table 19. TC19 test case record.

| | |
|---|---|
| Test Case Number: | TC19 |
| Revision: | Rev. 2 |
| Author: | Isit Pokharel |
| Date Conducted: | 2026-09-12 |
| Test Conductor: | Isit Pokharel |
| Customer Representative: | Isit Pokharel |
| Description: | Unit tests for the schema validator in isolation, run with pytest against fixture files rather than through the browser. Exercises `pipeline.validate`, which has no Django imports. |
| Pre-Test Setup: | pytest installed. No database and no server. Each test copies the seven-file fixture set to a temporary directory and corrupts it in place, so no stored broken variant is needed and the expected message sits beside the corruption that produces it. |
| Use Case: | Upload cohort |
| Flow: | Main Flow; Exception Flow |
| Test Level: | Unit |
| Requirements Verified: | FR-12, FR-13 |

Table 20. TC19 steps and expected results.

| User Action | Expected Results | P/F | Comment |
|---|---|---|---|
| 1. `pytest -k the_fixture_cohort_validates` | `validate_upload` returns a result with `ok` true and `error` None, and one FileStats per required file. Checks return a result rather than raising, so the caller decides what a failure means. | Pass | FR-12 |
| 2. `pytest -k missing_file_is_named` | `ok` is false and the message names every missing file. No statistics are returned for a rejected upload. | Pass | FR-12 |
| 3. `pytest -k extra_trailing_column` | `ok` is false; the message gives the expected and found column counts and names the first extra column. | Pass | FR-12 |
| 4. `pytest -k renamed_column_reports_name_and_position` | Message is exactly `studentVle: expected column sum_click at position 6, found clicks`, the form given in Section 3.8. Positions are 1-indexed. | Pass | FR-12 |
| 5. `pytest -k unexpected_file_is_rejected` | `ok` is false and the message names the file that does not belong to the set. | Pass | FR-12 |
| 6. `pytest -k file_over_the_limit` | `ok` is false and the message gives the size against the 500 MB per-file limit. | Pass | FR-13 |
| 7. `pytest -k validation_stores_nothing` | The temporary directory is unchanged after validation. | Pass | FR-12 |

Post-conditions: all tests pass. The validator writes nothing and the fixture directory is unchanged.

## 5.7 Measured non-functional results

Table 21. NFR measurements on the reference machine.

| Requirement | Budget | Measured |
|---|---|---|
| NFR-1 ranking page, 2,000 students | under 3 s | **4 ms** median server render on the largest scored presentation (1,880 students, 50-row page); 16 ms on the last page; 32 ms for the full 1,880-row CSV export |
| NFR-2 full feature rebuild | under 10 min | **21.6 s** to build, 21.4 s to write |
| NFR-7 single machine, 8 GB | 8 GB | **1.55 GB** peak resident set during the rebuild |

NFR-1 is held by the design rather than by tuning: the ranking reads pre-computed RiskScore rows through a (model_version, -probability) index and scores nothing at request time.

# 6. Open issues

Design questions not settled. Each changes either a stored value or a reported result, and none should be closed by whichever choice the code happens to make first. Evidence gathered during the build is recorded in `DECISIONS.md`.

- **Week numbering.** The SRS gives `week = date // 7`, which contradicts its own two other statements about week boundaries. The build uses `date // 7 + 1`, which satisfies both. This shifts every horizon by seven days and therefore changes every number in the results. It must be confirmed against the SRS before anything is quoted. This is the only open issue that changes all of them.
- **Module CCC.** Now dropped from evaluation and the exclusion reported, which is the SRS default. Reporting it separately as a transfer case remains the alternative. Evidence: CCC is 21.5 percent of the week 8 test set, and including it changed which classifier won at every horizon and reversed the ordering of the demographic and engagement feature sets.
- **The n < 20 suppression floor.** Evidence says the two places it applies are not alike. It never binds on the whole test year, where the smallest IMD group holds 596 students, and binds constantly on the per-presentation dashboard, where 6 of the 2014 presentations suppress at least one group. Whether the dashboard wants a floor that scales with the presentation is open.
- **Feature attribution method.** Coefficients and permutation importance are not comparable, so a version trained with one cannot be compared against a version trained with the other, and the method is not currently stored on the ModelVersion row. Fixing one method for all versions would be simpler and is probably right; recording the method is the alternative and is two fields.
- **Storing the random seed.** Fixed at 42 as a module constant and not stored per version. Nothing currently varies it, so the stored field would be a constant column; it becomes worth having the moment a version is trained with anything else.
- **Login response timing.** The failure message is identical for a wrong password and an unknown username, but a locked account is refused before any password hash is computed and so returns sooner. A fixed time floor on the failure path would close it.
- **Week 4 and late registrants.** A student registering on day 20 has one week of clickstream at the first horizon. Scoring from the registration date instead of the presentation start is the obvious fix and it complicates the feature schema. Evidence: week 4 has the lowest lift and the lowest AUC-ROC of the three horizons, so it is the weakest at ranking even though its higher base rate makes a precise queue easier to fill.
- **Probability calibration.** With the SRS-mandated balanced class weights, logistic regression's output is systematically too high — its mean Brier score across the grid is worse than predicting the base rate for everyone — while its ranking is unaffected. The ranking page labels the column "probability". Calibrating the selected model before scoring, or relabelling the column, are the two ways out.

# References

Arnold, K. E., & Pistilli, M. D. (2012). Course Signals at Purdue: Using learning analytics to increase student success. In *Proceedings of the 2nd International Conference on Learning Analytics and Knowledge* (pp. 267-270). ACM. https://doi.org/10.1145/2330601.2330666

Django Software Foundation. (n.d.). *Django documentation* (Version 5.2). https://docs.djangoproject.com/

Kuzilek, J., Hlosta, M., & Zdrahal, Z. (2017). Open University Learning Analytics dataset. *Scientific Data, 4*, Article 170171. https://doi.org/10.1038/sdata.2017.171

Pedregosa, F., Varoquaux, G., Gramfort, A., Michel, V., Thirion, B., & Grisel, O. (2011). Scikit-learn: Machine learning in Python. *Journal of Machine Learning Research, 12*, 2825-2830.

Wolff, A., Zdrahal, Z., Nikolov, A., & Pantucek, M. (2013). Improving retention: Predicting at-risk students by analysing clicking behaviour in a virtual learning environment. In *Proceedings of the 3rd International Conference on Learning Analytics and Knowledge* (pp. 145-149). ACM. https://doi.org/10.1145/2460296.2460324
