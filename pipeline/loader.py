"""read_csv wrappers for the seven OULAD files.

No Django imports. Every call passes na_values="?" because that is how OULAD
writes a missing value; without it imd_band and date_unregistration come back
as the string "?" and silently poison every downstream comparison.

Dates are integer days relative to presentation start (day 0) and may be
negative. They are read as nullable Int64 so that a missing date stays missing
rather than becoming NaN in a float column.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pandas as pd

# OULAD's missing-value token, everywhere.
NA_VALUES = "?"

# Default chunk size for studentVle.csv (10.6 M rows). Never read whole.
VLE_CHUNK_ROWS = 500_000

_KEY_DTYPES = {
    "code_module": "string",
    "code_presentation": "string",
    "id_student": "int64",
}


def _read(path: Path, dtype: dict, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path, na_values=NA_VALUES, dtype=dtype, **kwargs)


def read_courses(path: Path) -> pd.DataFrame:
    """Input: courses.csv. Output: 22 rows x
    (code_module, code_presentation, module_presentation_length)."""
    return _read(
        path,
        {
            "code_module": "string",
            "code_presentation": "string",
            "module_presentation_length": "int64",
        },
    )


def read_assessments(path: Path) -> pd.DataFrame:
    """Input: assessments.csv. Output: ~200 rows x (code_module,
    code_presentation, id_assessment, assessment_type, date, weight).

    date is the due day and is missing for the exam in some presentations.
    """
    return _read(
        path,
        {
            "code_module": "string",
            "code_presentation": "string",
            "id_assessment": "int64",
            "assessment_type": "string",
            "date": "Int64",
            "weight": "float64",
        },
    )


def read_vle(path: Path) -> pd.DataFrame:
    """Input: vle.csv. Output: ~6,300 rows x (id_site, code_module,
    code_presentation, activity_type, week_from, week_to)."""
    return _read(
        path,
        {
            "id_site": "int64",
            "code_module": "string",
            "code_presentation": "string",
            "activity_type": "string",
            "week_from": "Int64",
            "week_to": "Int64",
        },
    )


def read_student_info(path: Path) -> pd.DataFrame:
    """Input: studentInfo.csv. Output: 32,593 rows, one per registration.

    imd_band is normalized on read: OULAD writes the second band as '10-20'
    with no '%' suffix while every other band has one.
    """
    frame = _read(
        path,
        _KEY_DTYPES
        | {
            "gender": "string",
            "region": "string",
            "highest_education": "string",
            "imd_band": "string",
            "age_band": "string",
            "num_of_prev_attempts": "int64",
            "studied_credits": "int64",
            "disability": "string",
            "final_result": "string",
        },
    )
    frame["imd_band"] = normalize_imd_band(frame["imd_band"])
    return frame


def read_student_registration(path: Path) -> pd.DataFrame:
    """Input: studentRegistration.csv. Output: 32,593 rows x (code_module,
    code_presentation, id_student, date_registration, date_unregistration).

    date_unregistration is missing for students who never unregistered; that
    missingness is the signal, so it is preserved as <NA>, not filled.
    """
    return _read(
        path,
        _KEY_DTYPES
        | {"date_registration": "Int64", "date_unregistration": "Int64"},
    )


def read_student_assessment(path: Path) -> pd.DataFrame:
    """Input: studentAssessment.csv. Output: ~174,000 rows x (id_assessment,
    id_student, date_submitted, is_banked, score).

    Carries no module or presentation: join to assessments on id_assessment.
    """
    return _read(
        path,
        {
            "id_assessment": "int64",
            "id_student": "int64",
            "date_submitted": "Int64",
            "is_banked": "int64",
            "score": "float64",
        },
    )


def iter_student_vle(
    path: Path, chunk_rows: int = VLE_CHUNK_ROWS
) -> Iterator[pd.DataFrame]:
    """Input: studentVle.csv (10,655,280 rows). Output: an iterator of frames
    of at most chunk_rows rows x (code_module, code_presentation, id_student,
    id_site, date, sum_click).

    This is the only supported way to read studentVle. There is deliberately
    no read_student_vle(): the file is never loaded whole, not even to check
    something.
    """
    reader = pd.read_csv(
        path,
        na_values=NA_VALUES,
        dtype=_KEY_DTYPES | {"id_site": "int64", "date": "int64", "sum_click": "int64"},
        chunksize=chunk_rows,
    )
    yield from reader


def normalize_imd_band(series: pd.Series) -> pd.Series:
    """Give every non-missing IMD band a '%' suffix.

    OULAD writes '10-20' where it writes '0-10%', '20-30%' and so on. Left
    alone this makes the band an extra one-hot level and breaks any join on
    band. Missing values stay missing.
    """
    return series.where(series.isna() | series.str.endswith("%"), series + "%")
