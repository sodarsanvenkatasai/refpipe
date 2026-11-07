"""Compare two runs (typically the same input under two reference versions)."""
from __future__ import annotations

import sqlite3

from .reasons import PipelineError

_PRIMARY = """
SELECT record_id, product_code, outcome, COALESCE(reason,'') AS reason, is_deprecated_code, reference_version
FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY record_id ORDER BY row_num) AS rn
      FROM run_outcomes WHERE run_id=? AND record_id NOT LIKE 'row:%')
WHERE rn = 1
"""


def compare_runs(conn: sqlite3.Connection, run_a: str, run_b: str) -> dict:
    """Classify every record_id by what happened between run A and run B.

    Each record_id is represented by its FIRST occurrence in the file (later copies are
    DUPLICATE_RECORD rejects and are not a business outcome). Categories:
      unchanged_valid, unchanged_rejected, rejected_now_valid, valid_now_rejected, reason_changed,
      valid_now_deprecated, deprecated_flag_cleared, only_in_a, only_in_b
    """
    for r in (run_a, run_b):
        row = conn.execute("SELECT status, reference_version FROM pipeline_runs WHERE run_id=?", (r,)).fetchone()
        if not row:
            raise PipelineError("RUN_NOT_FOUND", f"unknown run_id {r!r}")
    meta = {r: dict(conn.execute("SELECT run_id, reference_version, status, input_count, valid_count,"
                                 " rejected_count FROM pipeline_runs WHERE run_id=?", (r,)).fetchone())
            for r in (run_a, run_b)}
    sql = f"""
    SELECT a.record_id AS record_id, COALESCE(a.product_code, b.product_code) AS product_code,
           a.outcome AS a_outcome, a.reason AS a_reason, a.is_deprecated_code AS a_dep,
           b.outcome AS b_outcome, b.reason AS b_reason, b.is_deprecated_code AS b_dep
    FROM ({_PRIMARY}) a LEFT JOIN ({_PRIMARY}) b ON a.record_id = b.record_id
    UNION ALL
    SELECT b.record_id, b.product_code, NULL, NULL, NULL, b.outcome, b.reason, b.is_deprecated_code
    FROM ({_PRIMARY}) b WHERE b.record_id NOT IN (SELECT record_id FROM run_outcomes WHERE run_id=?)
    """
    rows = conn.execute(sql, (run_a, run_b, run_b, run_a)).fetchall()
    cats: dict[str, int] = {}
    transitions: dict[tuple, int] = {}
    changed_codes: dict[str, int] = {}
    for r in rows:
        if r["a_outcome"] is None:
            cat = "only_in_b"
        elif r["b_outcome"] is None:
            cat = "only_in_a"
        elif r["a_outcome"] == "VALID" and r["b_outcome"] == "VALID":
            cat = ("valid_now_deprecated" if (not r["a_dep"] and r["b_dep"]) else
                   "deprecated_flag_cleared" if (r["a_dep"] and not r["b_dep"]) else "unchanged_valid")
        elif r["a_outcome"] == "REJECTED" and r["b_outcome"] == "VALID":
            cat = "rejected_now_valid"
        elif r["a_outcome"] == "VALID":
            cat = "valid_now_rejected"
        else:
            cat = "unchanged_rejected" if r["a_reason"] == r["b_reason"] else "reason_changed"
        cats[cat] = cats.get(cat, 0) + 1
        if cat not in ("unchanged_valid", "unchanged_rejected"):
            key = (r["a_outcome"], r["a_reason"] or "", r["b_outcome"], r["b_reason"] or "")
            transitions[key] = transitions.get(key, 0) + 1
            changed_codes[r["product_code"]] = changed_codes.get(r["product_code"], 0) + 1
    return {
        "run_a": meta[run_a], "run_b": meta[run_b],
        "categories": dict(sorted(cats.items())),
        "transitions": [{"from": f"{k[0]}:{k[1]}" if k[0] else "(absent)", "to": f"{k[2]}:{k[3]}" if k[2] else "(absent)",
                         "records": v} for k, v in sorted(transitions.items(), key=lambda kv: -kv[1])],
        "changed_codes": dict(sorted(changed_codes.items(), key=lambda kv: (-kv[1], kv[0]))),
    }
