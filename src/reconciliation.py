"""Reconciliation checks. Run inside the publish transaction; any failure rolls the run back."""
from __future__ import annotations

import sqlite3

from .reasons import PipelineError

RECONCILIATION_FAILED = "RECONCILIATION_FAILED"


def reconcile(conn: sqlite3.Connection, run_id: str, reference_version: str, counts: dict, pub: dict) -> list[dict]:
    """Return the list of checks; raise PipelineError if any check fails.

    Accounting equation (duplicates are a SUBSET of rejects, never counted twice):
        input_count = valid_count + rejected_count
        rejected_count = sum(rejected by reason)   (DUPLICATE_RECORD is one of the reasons)
    """
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    checks: list[dict] = []

    def check(name: str, expected, actual, note: str = ""):
        checks.append({"check": name, "expected": expected, "actual": actual,
                       "passed": expected == actual, "note": note})

    check("input = valid + rejected", counts["input_count"], counts["valid_count"] + counts["rejected_count"])
    check("outcomes persisted = input", counts["input_count"],
          one("SELECT COUNT(*) FROM run_outcomes WHERE run_id=?", run_id))
    check("rejects persisted = rejected", counts["rejected_count"],
          one("SELECT COUNT(*) FROM rejected_records WHERE run_id=?", run_id))
    check("duplicates <= rejected", True, counts["duplicate_count"] <= counts["rejected_count"],
          "duplicate_count is a subset of rejected_count")
    check("staged = valid", counts["valid_count"], pub["staged_count"])
    check("staged = inserted + updated + unchanged", pub["staged_count"],
          pub["inserted_count"] + pub["updated_count"] + pub["unchanged_count"])
    check("every staged key is in target with same content", pub["staged_count"], one(
        "SELECT COUNT(*) FROM stage_valid_records s JOIN validated_records v ON v.record_id=s.record_id"
        " WHERE s.run_id=? AND v.row_hash=s.row_hash", run_id))
    staged_cents = one("SELECT COALESCE(SUM(amount_cents),0) FROM stage_valid_records WHERE run_id=?", run_id)
    target_cents = one(
        "SELECT COALESCE(SUM(v.amount_cents),0) FROM stage_valid_records s JOIN validated_records v"
        " ON v.record_id=s.record_id WHERE s.run_id=?", run_id)
    check("amount control total (cents), staged = target", staged_cents, target_cents)
    check("target duplicate keys", 0, one(
        "SELECT COUNT(*) FROM (SELECT record_id FROM validated_records GROUP BY record_id HAVING COUNT(*)>1)"))
    check("rows written by this run use this reference_version", 0, one(
        "SELECT COUNT(*) FROM validated_records WHERE run_id=? AND reference_version<>?", run_id, reference_version))
    failed = [c for c in checks if not c["passed"]]
    if failed:
        raise PipelineError(RECONCILIATION_FAILED,
                            "; ".join(f"{c['check']}: expected {c['expected']} got {c['actual']}" for c in failed))
    return checks
