"""Deterministic transformation of validated rows into the target shape."""
from __future__ import annotations

import hashlib

import pandas as pd


def row_hash(record_id: str, entity_id: str, product_code: str, event_date: str, amount_cents) -> str:
    """Stable content fingerprint used to tell an unchanged re-delivery from a changed record."""
    amt = "" if pd.isna(amount_cents) else str(int(amount_cents))
    payload = "|".join([record_id, entity_id, product_code, event_date, amt])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def transform_valid(valid: pd.DataFrame, run_id: str, reference_version: str, source_file: str) -> pd.DataFrame:
    """Add lineage + derived fields. Input rows are already normalized by validation.

    Derived: event_year_month (YYYY-MM), row_hash, is_deprecated_code; lineage: run_id,
    reference_version, source_file. Pure function of its inputs (no clock, no randomness).
    """
    out = valid.copy()
    out["event_year_month"] = out["event_date"].str.slice(0, 7)
    out["row_hash"] = [
        row_hash(a, b, c, d, e)
        for a, b, c, d, e in zip(out["record_id"], out["entity_id"], out["product_code"],
                                 out["event_date"], out["amount_cents"])
    ]
    out["run_id"] = run_id
    out["reference_version"] = reference_version
    out["source_file"] = source_file
    out["is_deprecated_code"] = out["is_deprecated"].astype(int)
    return out
