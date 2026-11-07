"""Reject quarantine: keep the original row, the reason, and run/source lineage."""
from __future__ import annotations

import json
import sqlite3

import pandas as pd


def build_rejects(batch_rows: pd.DataFrame, results: pd.DataFrame, malformed: list[tuple[int, list[str]]],
                  header: list[str], run_id: str, source_file: str, reference_version: str,
                  rejected_at: str) -> list[tuple]:
    """Return rows for `rejected_records` (append-only; never deleted on reprocessing)."""
    rows: list[tuple] = []
    bad = results[results["reason"].notna()]
    if len(bad):
        subset = batch_rows[batch_rows["row_num"].isin(bad["row_num"])]
        raw = {rec["row_num"]: {k: v for k, v in rec.items() if k != "row_num"}
               for rec in subset.to_dict("records")}
        for row_num, rid, reason in zip(bad["row_num"], bad["record_id"], bad["reason"]):
            payload = raw[row_num]
            rows.append((f"{run_id}:{row_num}", run_id, source_file, rid or f"row:{row_num}", int(row_num),
                         json.dumps(payload, ensure_ascii=False), reason, reference_version, rejected_at))
    for row_num, fields in malformed:
        rows.append((f"{run_id}:{row_num}", run_id, source_file, f"row:{row_num}", int(row_num),
                     json.dumps({"raw_fields": fields, "expected_columns": header}, ensure_ascii=False),
                     "MALFORMED_ROW", reference_version, rejected_at))
    return rows


def write_rejects(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
        "INSERT INTO rejected_records(reject_id, run_id, source_file, source_record_id, row_num, raw_payload,"
        " rejection_reason, reference_version, rejected_at) VALUES (?,?,?,?,?,?,?,?,?)", rows)
