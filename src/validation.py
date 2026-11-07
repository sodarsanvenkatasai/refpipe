"""Row validation: one pure reference implementation and one vectorized engine.

Decision order (first failure wins, deterministic):
  MALFORMED_ROW (ingestion) -> MISSING_RECORD_ID -> MISSING_CODE -> MISSING_EVENT_DATE
  -> INVALID_DATE -> INVALID_AMOUNT -> DUPLICATE_RECORD -> UNKNOWN_CODE
  -> INACTIVE_CODE -> DEPRECATED_CODE (policy=reject only)
  -> CODE_NOT_YET_EFFECTIVE / CODE_EXPIRED -> ORPHAN_ENTITY

Both engines return a DataFrame with the same columns so they can be tested for equivalence:
  row_num, record_id, entity_id, product_code, event_date, amount, amount_cents,
  reason (None when valid), is_deprecated (0/1)

Normalization rules (see docs/DESIGN_DECISIONS.md): all text fields are whitespace-stripped;
product_code is additionally upper-cased; dates must be strict ISO YYYY-MM-DD; amount is an
optional non-negative decimal with at most 2 fraction digits.
Dates are plain calendar dates (no time zone); effective_from/effective_to are INCLUSIVE.
ISO date strings compare correctly as strings, so all date logic is string comparison.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Callable, Iterable, NamedTuple, Optional

import numpy as np
import pandas as pd

from . import reasons as R

OPEN_END = "9999-12-31"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_AMOUNT_RE = re.compile(r"^\d{1,12}(\.\d{1,2})?$")


class Window(NamedTuple):
    effective_from: str
    effective_to: str  # OPEN_END when unbounded
    status: str


# ---------------------------------------------------------------- scalar helpers
def parse_iso_date(s: str) -> Optional[str]:
    """Return the ISO date string if `s` is a strict, real calendar date, else None."""
    if not _DATE_RE.match(s):
        return None
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None
    return s


def parse_amount_cents(s: str) -> Optional[int]:
    """Exact amount in cents, or None if not a valid non-negative 2-dp decimal."""
    if not _AMOUNT_RE.match(s):
        return None
    return int((Decimal(s) * 100).to_integral_value())


def decide_reference(event_date: str, windows: list[Window], policy: str) -> tuple[Optional[str], bool]:
    """Apply reference lookup, status and effective-date rules.

    `windows` must be sorted by effective_from. The window containing event_date is used; if none
    contains it, the earliest window is used to decide NOT_YET_EFFECTIVE vs EXPIRED (a date in a
    gap between windows counts as EXPIRED). Returns (reason_or_None, is_deprecated_flag).
    """
    if not windows:
        return R.UNKNOWN_CODE, False
    chosen = next((w for w in windows if w.effective_from <= event_date <= w.effective_to), None)
    in_window = chosen is not None
    if chosen is None:
        chosen = windows[0]
    if chosen.status == "INACTIVE":
        return R.INACTIVE_CODE, False
    if chosen.status == "DEPRECATED" and policy == "reject":
        return R.DEPRECATED_CODE, False
    if not in_window:
        return (R.CODE_NOT_YET_EFFECTIVE if event_date < chosen.effective_from else R.CODE_EXPIRED), False
    return None, chosen.status == "DEPRECATED"


# ---------------------------------------------------------------- row-wise engine
def validate_rowwise(
    df: pd.DataFrame,
    lookup: Callable[[str], list[Window]],
    policy: str,
    entity_ids: Optional[set] = None,
) -> pd.DataFrame:
    """Reference implementation: one Python iteration (and one `lookup` call) per row.

    Used as the readable specification, for equivalence tests, and as the slow baseline in the
    benchmark when `lookup` performs a database query per row.
    """
    out = []
    seen: set[str] = set()
    for row_num, rid, ent, code, ed, amt in zip(
        df["row_num"], df["record_id"], df["entity_id"], df["product_code"],
        df["event_date"], df["amount"],
    ):
        rid, ent, code, ed, amt = rid.strip(), ent.strip(), code.strip().upper(), ed.strip(), amt.strip()
        reason: Optional[str] = None
        cents: Optional[int] = parse_amount_cents(amt) if amt != "" else None
        deprecated = False
        if rid == "":
            reason = R.MISSING_RECORD_ID
        elif code == "":
            reason = R.MISSING_CODE
        elif ed == "":
            reason = R.MISSING_EVENT_DATE
        elif parse_iso_date(ed) is None:
            reason = R.INVALID_DATE
        else:
            if amt != "" and cents is None:
                reason = R.INVALID_AMOUNT
            if reason is None:
                if rid in seen:
                    reason = R.DUPLICATE_RECORD
                else:
                    seen.add(rid)
                    reason, deprecated = decide_reference(ed, lookup(code), policy)
                    if reason is None and entity_ids is not None and ent != "" and ent not in entity_ids:
                        reason = R.ORPHAN_ENTITY
        out.append((row_num, rid, ent, code, ed, amt, cents, reason, int(deprecated and reason is None)))
    res = pd.DataFrame(out, columns=["row_num", "record_id", "entity_id", "product_code",
                                     "event_date", "amount_raw", "amount_cents", "reason", "is_deprecated"])
    res["reason"] = pd.Series([o[7] for o in out], dtype=object)  # None (not NaN) when valid, like the other engine
    res["amount"] = res["amount_cents"].astype("float64") / 100.0
    res["amount_cents"] = res["amount_cents"].astype("Int64")
    return res[_OUT_COLS].reset_index(drop=True)


_OUT_COLS = ["row_num", "record_id", "entity_id", "product_code", "event_date",
             "amount", "amount_cents", "reason", "is_deprecated"]


# ---------------------------------------------------------------- vectorized engine
def valid_iso_dates(s: pd.Series) -> pd.Series:
    """Boolean mask: strict ISO format AND a real calendar date."""
    fmt_ok = s.str.fullmatch(r"\d{4}-\d{2}-\d{2}").fillna(False).astype(bool)
    parsed = pd.to_datetime(s.where(fmt_ok, None), format="%Y-%m-%d", errors="coerce")
    return fmt_ok & parsed.notna()


def validate_vectorized(
    df: pd.DataFrame,
    ref: pd.DataFrame,
    policy: str,
    entity_ids: Optional[set] = None,
) -> pd.DataFrame:
    """Validate a whole batch with set-based operations.

    `ref` has columns product_code, status, effective_from, effective_to (ISO strings, one reference
    version only). The reference snapshot is joined once, instead of looked up once per row.
    """
    n = len(df)
    rid = df["record_id"].str.strip().reset_index(drop=True)
    ent = df["entity_id"].str.strip().reset_index(drop=True)
    code = df["product_code"].str.strip().str.upper().reset_index(drop=True)
    ed = df["event_date"].str.strip().reset_index(drop=True)
    amt = df["amount"].str.strip().reset_index(drop=True)

    reason = np.full(n, None, dtype=object)
    open_ = np.ones(n, dtype=bool)

    def flag(mask, why):
        nonlocal open_
        hit = open_ & np.asarray(mask, dtype=bool)
        reason[hit] = why
        open_ = open_ & ~hit

    date_ok = valid_iso_dates(ed).to_numpy()
    amt_fmt_ok = amt.str.fullmatch(r"\d{1,12}(\.\d{1,2})?").fillna(False).to_numpy(dtype=bool)
    amt_blank = (amt == "").to_numpy()

    flag(rid == "", R.MISSING_RECORD_ID)
    flag(code == "", R.MISSING_CODE)
    flag(ed == "", R.MISSING_EVENT_DATE)
    flag(~date_ok, R.INVALID_DATE)
    flag(~(amt_blank | amt_fmt_ok), R.INVALID_AMOUNT)

    # duplicates: keep first occurrence among rows that survived the field-level checks
    dup = np.zeros(n, dtype=bool)
    if open_.any():
        idx = np.flatnonzero(open_)
        dup[idx] = rid.iloc[idx].duplicated(keep="first").to_numpy()
    flag(dup, R.DUPLICATE_RECORD)

    deprecated = np.zeros(n, dtype=bool)
    if open_.any():
        idx = np.flatnonzero(open_)
        cand = pd.DataFrame({"i": idx, "product_code": code.iloc[idx].to_numpy(),
                             "event_date": ed.iloc[idx].to_numpy()})
        m = cand.merge(ref, on="product_code", how="left")
        known = m["status"].notna()
        m["in_win"] = known & (m["event_date"] >= m["effective_from"]) & (m["event_date"] <= m["effective_to"])
        # chosen window per row: the matching one, else the earliest
        m = m.sort_values(["i", "in_win", "effective_from"], ascending=[True, False, True])
        m = m.drop_duplicates("i", keep="first")
        m_known = m["status"].notna().to_numpy()
        status = m["status"].fillna("").to_numpy()
        in_win = m["in_win"].to_numpy()
        ed_m = m["event_date"].to_numpy()
        from_m = m["effective_from"].fillna("").to_numpy()
        i_m = m["i"].to_numpy()

        r = np.full(len(m), None, dtype=object)
        undecided = np.ones(len(m), dtype=bool)

        def dec(mask, why):
            nonlocal undecided
            hit = undecided & mask
            r[hit] = why
            undecided = undecided & ~hit

        dec(~m_known, R.UNKNOWN_CODE)
        dec(status == "INACTIVE", R.INACTIVE_CODE)
        if policy == "reject":
            dec(status == "DEPRECATED", R.DEPRECATED_CODE)
        dec(~in_win & (ed_m < from_m), R.CODE_NOT_YET_EFFECTIVE)
        dec(~in_win, R.CODE_EXPIRED)
        reason[i_m] = r
        open_[i_m[r != None]] = False  # noqa: E711 - elementwise comparison on object array
        deprecated[i_m] = (r == None) & (status == "DEPRECATED")  # noqa: E711

    if entity_ids is not None:
        orphan = (ent != "").to_numpy() & ~ent.isin(entity_ids).to_numpy()
        flag(orphan, R.ORPHAN_ENTITY)

    cents = pd.Series(pd.NA, index=range(n), dtype="Int64")
    ok_amt = amt_fmt_ok & ~amt_blank
    if ok_amt.any():
        vals = pd.to_numeric(amt[ok_amt]).to_numpy(dtype="float64")
        cents.iloc[np.flatnonzero(ok_amt)] = np.rint(vals * 100).astype("int64")
    amount = cents.astype("float64") / 100.0

    res = pd.DataFrame({
        "row_num": df["row_num"].to_numpy(),
        "record_id": rid, "entity_id": ent, "product_code": code, "event_date": ed,
        "amount": amount, "amount_cents": cents,
        "reason": pd.Series(reason, dtype=object),
        "is_deprecated": (deprecated & pd.isna(pd.Series(reason, dtype=object)).to_numpy()).astype(int),
    })
    return res[_OUT_COLS]


def windows_by_code(ref: pd.DataFrame) -> dict[str, list[Window]]:
    """In-memory lookup structure equivalent to the reference snapshot (for tests/rowwise)."""
    out: dict[str, list[Window]] = {}
    for c, f, t, s in zip(ref["product_code"], ref["effective_from"], ref["effective_to"], ref["status"]):
        out.setdefault(c, []).append(Window(f, t, s))
    for v in out.values():
        v.sort()
    return out
