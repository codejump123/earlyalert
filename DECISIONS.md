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

Items 1-4 are untouched so far; no evidence yet.

## Gaps between the SRS and the data

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
