"""End-to-end orchestration: ingest -> validate -> transform -> stage -> publish -> reconcile -> report."""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd

from . import reasons as R
from .config import Config
from .ingestion import read_batch
from .metrics import RunTracker, new_run_id, now_iso
from .publishing import stage_valid, upsert_from_stage
from .quarantine import build_rejects, write_rejects
from .reconciliation import reconcile
from .reference_loader import get_entity_ids, get_reference, sql_lookup, store_reference
from .transformation import transform_valid
from .validation import valid_iso_dates, validate_rowwise, validate_vectorized, windows_by_code

log = logging.getLogger("refpipe")


class InjectedFailure(RuntimeError):
    """Raised on purpose by `fail_at` to exercise the partial-failure / retry path."""


def _validate(cfg: Config, conn, version, ref, data, entity_ids):
    if cfg.engine == "rowwise":  # slow baseline: one SQL lookup per row
        return validate_rowwise(data, sql_lookup(conn, version), cfg.deprecated_policy, entity_ids)
    return validate_vectorized(data, ref, cfg.deprecated_policy, entity_ids)


def _empty_results() -> pd.DataFrame:
    cols = ["row_num", "record_id", "entity_id", "product_code", "event_date",
            "amount", "amount_cents", "reason", "is_deprecated"]
    return pd.DataFrame({c: pd.Series(dtype="object") for c in cols})


def run_pipeline(
    conn: sqlite3.Connection,
    cfg: Config,
    input_path: str,
    reference_version: str,
    reference_path: Optional[str] = None,
    check_entities: bool = False,
    event_from: Optional[str] = None,
    event_to: Optional[str] = None,
    run_id: Optional[str] = None,
    fail_at: Optional[str] = None,
    write_reports: bool = True,
) -> dict:
    """Run one pipeline execution and return its summary dict.

    A run always uses exactly ONE reference_version, chosen explicitly by the caller.
    On any error the run row is marked FAILED (with the error message), the target table is
    untouched (publish is a single transaction) and the exception is re-raised.

    fail_at: None | 'after_stage' | 'before_commit' - test hooks that raise InjectedFailure.
    """
    run_id = run_id or new_run_id()
    source_file = Path(input_path).as_posix()
    tracker = RunTracker(conn, run_id, source_file, reference_version,
                         {**cfg.as_dict(), "check_entities": check_entities, "event_from": event_from,
                          "event_to": event_to, "reference_path": reference_path})
    counts: dict = {}
    warnings: list[str] = []
    checksum = None
    log.info("run_id=%s start input=%s reference_version=%s engine=%s", run_id, source_file,
             reference_version, cfg.engine)
    try:
        for label, val in (("event_from", event_from), ("event_to", event_to)):
            if val and not valid_iso_dates(pd.Series([val])).iloc[0]:
                raise R.PipelineError(R.SCHEMA_MISMATCH, f"{label} must be an ISO date YYYY-MM-DD, got {val!r}")

        with tracker.stage("load_reference"):
            if reference_path:
                status = store_reference(conn, reference_version, reference_path)
                log.info("run_id=%s reference %s: %s", run_id, reference_version, status)
            ref = get_reference(conn, reference_version)
        entity_ids = get_entity_ids(conn) if check_entities else None

        with tracker.stage("ingest"):
            batch = read_batch(input_path, cfg.strict_columns)
            checksum = batch.checksum
            warnings += batch.warnings
        data = batch.data

        skipped = 0
        if (event_from or event_to) and len(data):
            ed = data["event_date"].str.strip()
            ok = valid_iso_dates(ed)
            out_of_range = ok & ((ed < event_from) if event_from else False) | \
                ok & ((ed > event_to) if event_to else False)
            skipped = int(out_of_range.sum())
            data = data[~out_of_range].reset_index(drop=True)

        with tracker.stage("validate"):
            results = _validate(cfg, conn, reference_version, ref, data, entity_ids) if len(data) \
                else _empty_results()

        with tracker.stage("transform"):
            ts = now_iso()
            valid = results[results["reason"].isna()]
            staged = transform_valid(valid, run_id, reference_version, source_file)
            rejects = build_rejects(data, results, batch.malformed, batch.header, run_id, source_file,
                                    reference_version, ts)

        outcomes = [(run_id, int(r), rid or f"row:{r}", code, R.VALID if why is None else R.REJECTED, why,
                     int(dep), reference_version)
                    for r, rid, code, why, dep in zip(results["row_num"], results["record_id"],
                                                      results["product_code"], results["reason"],
                                                      results["is_deprecated"])]
        outcomes += [(run_id, n, f"row:{n}", "", R.REJECTED, R.MALFORMED_ROW, 0, reference_version)
                     for n, _ in batch.malformed]

        n_valid = len(staged)
        n_rejected = len(rejects)
        counts = {
            "input_count": len(results) + len(batch.malformed),
            "valid_count": n_valid,
            "rejected_count": n_rejected,
            "duplicate_count": int((results["reason"] == R.DUPLICATE_RECORD).sum()),
            "skipped_out_of_range": skipped,
        }
        reasons_breakdown = {}
        for _, _, _, _, _, why, _, _ in outcomes:
            if why:
                reasons_breakdown[why] = reasons_breakdown.get(why, 0) + 1

        with tracker.stage("stage"):
            stage_valid(conn, run_id, staged)
        if fail_at == "after_stage":
            raise InjectedFailure("injected failure after staging")

        with tracker.stage("publish"):
            conn.execute("BEGIN")
            try:
                write_rejects(conn, rejects)
                conn.executemany(
                    "INSERT INTO run_outcomes(run_id,row_num,record_id,product_code,outcome,reason,"
                    "is_deprecated_code,reference_version) VALUES (?,?,?,?,?,?,?,?)", outcomes)
                pub = upsert_from_stage(conn, run_id, now_iso())
                counts.update({k: pub[k] for k in ("inserted_count", "updated_count", "unchanged_count",
                                                   "published_count")})
                if fail_at == "before_commit":
                    raise InjectedFailure("injected failure before commit")
                checks = reconcile(conn, run_id, reference_version, counts, pub)
                tracker.finalize_sql("SUCCESS", counts, None, warnings, checksum)
                conn.execute("DELETE FROM stage_valid_records WHERE run_id=?", (run_id,))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    except BaseException as exc:  # record the failure, then re-raise
        msg = str(exc) or exc.__class__.__name__
        log.error("run_id=%s FAILED: %s", run_id, msg)
        tracker.finalize_sql("FAILED", counts, msg, warnings, checksum)
        raise

    summary = {
        "run_id": run_id, "status": "SUCCESS", "source_file": source_file,
        "source_checksum": checksum, "reference_version": reference_version,
        "deprecated_policy": cfg.deprecated_policy, "engine": cfg.engine,
        "event_from": event_from, "event_to": event_to,
        **counts, "rejects_by_reason": dict(sorted(reasons_breakdown.items())),
        "duration_seconds": tracker.elapsed(), "stage_seconds": tracker.timings,
        "rows_per_second": round(counts["input_count"] / tracker.elapsed(), 1) if tracker.elapsed() else None,
        "warnings": warnings, "reconciliation": checks,
    }
    log.info("run_id=%s SUCCESS input=%d valid=%d rejected=%d duplicates=%d published=%d in %.2fs",
             run_id, counts["input_count"], counts["valid_count"], counts["rejected_count"],
             counts["duplicate_count"], counts["published_count"], tracker.elapsed())
    if write_reports:
        write_run_reports(conn, cfg.output_dir, summary)
    return summary


def write_run_reports(conn: sqlite3.Connection, output_dir: str, summary: dict) -> Path:
    """Write run_summary.json, reconciliation.json and rejects.csv to output/<run_id>/."""
    out = Path(output_dir) / summary["run_id"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "reconciliation.json").write_text(json.dumps(summary["reconciliation"], indent=2), encoding="utf-8")
    rej = pd.read_sql_query(
        "SELECT run_id, source_file, source_record_id, row_num, rejection_reason, reference_version, raw_payload"
        " FROM rejected_records WHERE run_id=? ORDER BY row_num", conn, params=(summary["run_id"],))
    rej.to_csv(out / "rejects.csv", index=False)
    return out
