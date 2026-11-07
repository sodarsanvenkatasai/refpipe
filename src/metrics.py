"""Run lifecycle (pipeline_runs) and stage timing."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"run_{ts}_{uuid.uuid4().hex[:6]}"


def recover_stale_runs(conn: sqlite3.Connection) -> list[str]:
    """Mark runs left in RUNNING (process was killed) as FAILED so a retry is clearly safe.

    Safe because publishing is one transaction: a killed run never wrote target rows.
    Call only when no run is active (SQLite is single-writer).
    """
    ids = [r[0] for r in conn.execute("SELECT run_id FROM pipeline_runs WHERE status='RUNNING'")]
    conn.execute(
        "UPDATE pipeline_runs SET status='FAILED', ended_at=?,"
        " error_message='RECOVERED: process ended before the run finished' WHERE status='RUNNING'", (now_iso(),))
    conn.commit()
    return ids


class RunTracker:
    """Writes the run record at start and finalizes it as SUCCESS or FAILED."""

    def __init__(self, conn: sqlite3.Connection, run_id: str, source_file: str, reference_version: str, config: dict):
        self.conn, self.run_id = conn, run_id
        self.t0 = time.perf_counter()
        self.timings: dict[str, float] = {}
        self.started_at = now_iso()
        conn.execute(
            "INSERT INTO pipeline_runs(run_id, started_at, status, source_file, reference_version, config_json)"
            " VALUES (?,?,?,?,?,?)",
            (run_id, self.started_at, "RUNNING", source_file, reference_version, json.dumps(config, sort_keys=True)))

    @contextmanager
    def stage(self, name: str):
        t = time.perf_counter()
        try:
            yield
        finally:
            self.timings[name] = round(time.perf_counter() - t, 4)

    def elapsed(self) -> float:
        return round(time.perf_counter() - self.t0, 4)

    def finalize_sql(self, status: str, counts: dict, error: str | None, warnings: list[str], checksum: str | None):
        """Update the run row. Call inside the publish transaction for SUCCESS."""
        self.conn.execute(
            "UPDATE pipeline_runs SET ended_at=?, status=?, source_checksum=?, input_count=?, valid_count=?,"
            " rejected_count=?, duplicate_count=?, skipped_out_of_range=?, inserted_count=?, updated_count=?,"
            " unchanged_count=?, published_count=?, duration_seconds=?, error_message=?, warnings=? WHERE run_id=?",
            (now_iso(), status, checksum, counts.get("input_count"), counts.get("valid_count"),
             counts.get("rejected_count"), counts.get("duplicate_count"), counts.get("skipped_out_of_range"),
             counts.get("inserted_count"), counts.get("updated_count"), counts.get("unchanged_count"),
             counts.get("published_count"), self.elapsed(), error, json.dumps(warnings), self.run_id))
