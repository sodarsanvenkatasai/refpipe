"""Staging and idempotent publishing to the target table."""
from __future__ import annotations

import sqlite3

import pandas as pd

_STAGE_COLS = ["run_id", "record_id", "row_hash", "reference_version", "source_file", "row_num", "entity_id",
               "product_code", "event_date", "event_year_month", "amount", "amount_cents", "is_deprecated_code"]


def _py(v):
    """Convert pandas/numpy scalars (incl. NA) to plain Python values for sqlite3."""
    if v is None or v is pd.NA:
        return None
    if isinstance(v, float) and v != v:
        return None
    return v.item() if hasattr(v, "item") else v


def stage_valid(conn: sqlite3.Connection, run_id: str, staged: pd.DataFrame) -> None:
    """Write validated rows to the staging table in their own committed transaction.

    Old staging left behind by FAILED runs is cleared first, so a retry starts clean.
    """
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM stage_valid_records WHERE run_id IN "
                     "(SELECT run_id FROM pipeline_runs WHERE status='FAILED') OR run_id=?", (run_id,))
        rows = [tuple(_py(x) for x in r) for r in staged[_STAGE_COLS].itertuples(index=False, name=None)]
        conn.executemany(
            f"INSERT INTO stage_valid_records({','.join(_STAGE_COLS)}) VALUES ({','.join('?' * len(_STAGE_COLS))})",
            rows)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def upsert_from_stage(conn: sqlite3.Connection, run_id: str, published_at: str) -> dict:
    """Upsert this run's staged rows into `validated_records`. Must run inside a transaction.

    Conflict policy (current-state upsert, key = record_id):
      * new record_id                                  -> inserted
      * same record_id, same row_hash + same version   -> unchanged (nothing written)
      * same record_id, different content or version  -> updated (latest validated run wins)
    Re-running identical input therefore produces zero new target rows.
    """
    q = lambda sql: conn.execute(sql, (run_id,)).fetchone()[0]  # noqa: E731
    total = q("SELECT COUNT(*) FROM stage_valid_records WHERE run_id=?")
    inserted = q("SELECT COUNT(*) FROM stage_valid_records s LEFT JOIN validated_records v ON v.record_id=s.record_id"
                 " WHERE s.run_id=? AND v.record_id IS NULL")
    unchanged = q("SELECT COUNT(*) FROM stage_valid_records s JOIN validated_records v ON v.record_id=s.record_id"
                  " WHERE s.run_id=? AND v.row_hash=s.row_hash AND v.reference_version=s.reference_version")
    conn.execute(
        """INSERT INTO validated_records(record_id,row_hash,run_id,reference_version,source_file,entity_id,
               product_code,event_date,event_year_month,amount,amount_cents,is_deprecated_code,published_at)
           SELECT record_id,row_hash,run_id,reference_version,source_file,entity_id,product_code,event_date,
                  event_year_month,amount,amount_cents,is_deprecated_code,? FROM stage_valid_records
           WHERE run_id=? AND true
           ON CONFLICT(record_id) DO UPDATE SET
               row_hash=excluded.row_hash, run_id=excluded.run_id, reference_version=excluded.reference_version,
               source_file=excluded.source_file, entity_id=excluded.entity_id, product_code=excluded.product_code,
               event_date=excluded.event_date, event_year_month=excluded.event_year_month, amount=excluded.amount,
               amount_cents=excluded.amount_cents, is_deprecated_code=excluded.is_deprecated_code,
               published_at=excluded.published_at
           WHERE validated_records.row_hash <> excluded.row_hash
              OR validated_records.reference_version <> excluded.reference_version""",
        (published_at, run_id))
    return {"staged_count": total, "inserted_count": inserted, "unchanged_count": unchanged,
            "updated_count": total - inserted - unchanged,
            "published_count": total - unchanged}
