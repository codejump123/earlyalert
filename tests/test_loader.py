"""Reading the seven files. Pure pipeline: no Django, no database.

The point of these is the '?' handling: OULAD writes missing values as the
literal string '?', and a read that misses it turns 1,111 missing IMD bands
into a category and every missing unregistration date into a value.
"""

from pathlib import Path

import pandas as pd

from pipeline import loader

FIXTURES = Path(__file__).parent / "fixtures"


def test_question_mark_becomes_missing_not_a_category():
    info = loader.read_student_info(FIXTURES / "studentInfo.csv")
    assert "?" not in set(info["imd_band"].dropna())
    # k == 7 in each of the two presentations has no band.
    assert int(info["imd_band"].isna().sum()) == 2


def test_imd_band_10_20_gets_its_missing_percent_sign():
    """OULAD writes '10-20' where it writes '0-10%' for every other band."""
    raw = pd.read_csv(FIXTURES / "studentInfo.csv")
    assert "10-20" in set(raw["imd_band"])

    info = loader.read_student_info(FIXTURES / "studentInfo.csv")
    bands = set(info["imd_band"].dropna())
    assert "10-20" not in bands
    assert "10-20%" in bands
    assert all(band.endswith("%") for band in bands)


def test_normalize_imd_band_leaves_missing_missing():
    series = pd.Series(["10-20", "0-10%", None], dtype="string")
    out = loader.normalize_imd_band(series)
    assert out.tolist()[:2] == ["10-20%", "0-10%"]
    assert pd.isna(out.iloc[2])


def test_unregistration_stays_missing_and_nullable():
    registration = loader.read_student_registration(
        FIXTURES / "studentRegistration.csv"
    )
    assert str(registration["date_unregistration"].dtype) == "Int64"
    # 8 withdrawals in each of the two presentations leave 34 never-unregistered.
    assert int(registration["date_unregistration"].notna().sum()) == 16
    assert int(registration["date_unregistration"].isna().sum()) == 34


def test_negative_registration_dates_are_preserved():
    registration = loader.read_student_registration(
        FIXTURES / "studentRegistration.csv"
    )
    assert int(registration["date_registration"].min()) == -30


def test_student_vle_is_only_available_in_chunks():
    assert not hasattr(loader, "read_student_vle")

    chunks = list(loader.iter_student_vle(FIXTURES / "studentVle.csv", chunk_rows=300))
    assert [len(c) for c in chunks] == [300, 300, 300, 100]
    assert sum(len(c) for c in chunks) == 1000
    assert list(chunks[0].columns) == [
        "code_module", "code_presentation", "id_student", "id_site", "date",
        "sum_click",
    ]


def test_assessment_and_vle_read_back_whole():
    assessments = loader.read_assessments(FIXTURES / "assessments.csv")
    assert len(assessments) == 6
    assert assessments["weight"].sum() == 200.0

    vle = loader.read_vle(FIXTURES / "vle.csv")
    assert len(vle) == 12
    assert set(vle["activity_type"]) == {
        "forumng", "homepage", "oucontent", "resource", "subpage", "url",
    }

    submissions = loader.read_student_assessment(
        FIXTURES / "studentAssessment.csv"
    )
    assert len(submissions) == 90
