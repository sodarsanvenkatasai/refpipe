"""Pure validation rules + equivalence of the two engines."""
import pandas as pd
import pytest

from src import reasons as R
from src.validation import (Window, decide_reference, parse_amount_cents, parse_iso_date, validate_rowwise,
                            validate_vectorized, windows_by_code)

ACTIVE = [Window("2026-01-01", "2026-12-31", "ACTIVE")]


@pytest.mark.parametrize("s,ok", [("2026-02-28", True), ("2026-02-30", False), ("2026-1-5", False),
                                  ("31/12/2026", False), ("", False), ("2026-13-01", False)])
def test_parse_iso_date(s, ok):
    assert (parse_iso_date(s) is not None) is ok


@pytest.mark.parametrize("s,cents", [("0", 0), ("12.5", 1250), ("12.50", 1250), ("0.29", 29),
                                     ("-1", None), ("1.234", None), ("abc", None), ("1,000", None), ("NaN", None)])
def test_parse_amount(s, cents):
    assert parse_amount_cents(s) == cents


# ---- domain edge cases: effective-date boundaries are INCLUSIVE ------------------------------
@pytest.mark.parametrize("d,expected", [
    ("2025-12-31", R.CODE_NOT_YET_EFFECTIVE),   # day before effective_from
    ("2026-01-01", None),                       # == effective_from -> valid
    ("2026-12-31", None),                       # == effective_to   -> valid
    ("2027-01-01", R.CODE_EXPIRED),             # day after effective_to
])
def test_effective_date_boundaries(d, expected):
    assert decide_reference(d, ACTIVE, "allow_historical")[0] == expected


def test_unknown_code():
    assert decide_reference("2026-05-05", [], "allow_historical") == (R.UNKNOWN_CODE, False)


def test_inactive_always_rejected():
    w = [Window("2026-01-01", "2026-12-31", "INACTIVE")]
    assert decide_reference("2026-05-05", w, "allow_historical")[0] == R.INACTIVE_CODE


def test_deprecated_policies():
    w = [Window("2025-01-01", "2026-03-31", "DEPRECATED")]
    assert decide_reference("2026-01-15", w, "allow_historical") == (None, True)   # accepted, flagged
    assert decide_reference("2026-01-15", w, "reject")[0] == R.DEPRECATED_CODE
    assert decide_reference("2026-04-01", w, "allow_historical")[0] == R.CODE_EXPIRED


def test_gap_between_windows_is_expired_and_second_window_is_valid():
    w = [Window("2024-01-01", "2024-06-30", "ACTIVE"), Window("2025-01-01", "2025-12-31", "ACTIVE")]
    assert decide_reference("2024-09-01", w, "allow_historical")[0] == R.CODE_EXPIRED
    assert decide_reference("2025-03-01", w, "allow_historical")[0] is None
    assert decide_reference("2023-12-31", w, "allow_historical")[0] == R.CODE_NOT_YET_EFFECTIVE


# ---- decision order ---------------------------------------------------------------------------
def _df(rows):
    return pd.DataFrame([(i + 1, *r) for i, r in enumerate(rows)],
                        columns=["row_num", "record_id", "entity_id", "product_code", "event_date", "amount"])


REF = pd.DataFrame([("PRD100", "ACTIVE", "2026-01-01", "2026-12-31"),
                    ("PRD200", "DEPRECATED", "2025-01-01", "2026-03-31")],
                   columns=["product_code", "status", "effective_from", "effective_to"])


@pytest.mark.parametrize("engine", ["rowwise", "vectorized"])
def test_first_failure_wins_and_order(engine):
    rows = [
        ("", "E1", "", "", "x"),                      # everything wrong -> MISSING_RECORD_ID first
        ("A", "E1", "", "", "x"),                     # MISSING_CODE
        ("B", "E1", "PRD100", "", "x"),               # MISSING_EVENT_DATE
        ("C", "E1", "PRD100", "2026-02-30", "x"),     # INVALID_DATE (before amount)
        ("D", "E1", "PRD100", "2026-02-01", "x"),     # INVALID_AMOUNT
        ("E", "E1", "PRD100", "2026-02-01", "5.00"),  # valid (first copy)
        ("E", "E1", "PRD100", "2026-02-01", "5.00"),  # DUPLICATE_RECORD
        ("F", "E1", "zzz", "2026-02-01", "5.00"),     # UNKNOWN_CODE
        ("G", "E9", "prd100 ", "2026-02-01", ""),     # normalized code; ORPHAN_ENTITY
    ]
    df = _df(rows)
    if engine == "rowwise":
        lk = windows_by_code(REF)
        res = validate_rowwise(df, lambda c: lk.get(c, []), "allow_historical", {"E1"})
    else:
        res = validate_vectorized(df, REF, "allow_historical", {"E1"})
    assert list(res["reason"].fillna("OK")) == [
        R.MISSING_RECORD_ID, R.MISSING_CODE, R.MISSING_EVENT_DATE, R.INVALID_DATE, R.INVALID_AMOUNT,
        "OK", R.DUPLICATE_RECORD, R.UNKNOWN_CODE, R.ORPHAN_ENTITY]


def test_first_copy_wins_even_if_first_copy_is_rejected_by_reference():
    df = _df([("A", "E1", "zzz", "2026-02-01", "1"), ("A", "E1", "PRD100", "2026-02-01", "1")])
    res = validate_vectorized(df, REF, "allow_historical")
    assert list(res["reason"]) == [R.UNKNOWN_CODE, R.DUPLICATE_RECORD]


def test_invalid_first_copy_does_not_shadow_later_valid_copy():
    df = _df([("A", "E1", "PRD100", "bad-date", "1"), ("A", "E1", "PRD100", "2026-02-01", "1")])
    res = validate_vectorized(df, REF, "allow_historical")
    assert list(res["reason"].fillna("OK")) == [R.INVALID_DATE, "OK"]


@pytest.mark.parametrize("policy", ["allow_historical", "reject"])
def test_engines_agree_on_generated_data(tmp_path, policy):
    import generate_data
    from src.ingestion import read_batch
    from src.reference_loader import read_reference_file

    generate_data.generate(2000, 3, str(tmp_path))
    batch = read_batch(str(tmp_path / "incoming_records.csv"))
    ref = read_reference_file(str(tmp_path / "reference_codes_v2.csv"), "v2")
    ents = set(pd.read_csv(tmp_path / "entities.csv", dtype=str)["entity_id"])
    lk = windows_by_code(ref)
    a = validate_rowwise(batch.data, lambda c: lk.get(c, []), policy, ents)
    b = validate_vectorized(batch.data, ref, policy, ents)
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True), check_dtype=False)
