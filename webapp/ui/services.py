"""Bridge between the Django views and the ``src`` pipeline package.

No business logic lives here: every operation delegates to the same modules
the CLI uses (``run_pipeline``, ``store_reference``, ``compare_runs``, ...).
This module only adds web concerns: upload storage, background execution with
a single-writer lock, pagination and read-only queries for the browsers.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings

from src.compare import compare_runs
from src.config import Config, load_config
from src.db import connect
from src.metrics import new_run_id
from src.pipeline import run_pipeline
from src.reasons import REASON_ORDER
from src.reference_loader import load_entities, store_reference

log = logging.getLogger("refpipe.web")

# SQLite is single-writer: serialize pipeline runs started from the web UI.
_run_lock = threading.Lock()


def get_cfg() -> Config:
    return load_config(
        str(settings.PIPELINE_CONFIG),
        db_path=str(settings.PIPELINE_DB_PATH),
        output_dir=str(settings.PIPELINE_OUTPUT_DIR),
    )


@contextmanager
def pipeline_conn():
    cfg = get_cfg()
    conn = connect(cfg.db_path)
    try:
        yield conn
    finally:
        conn.close()


def paged(conn, select: str, params=(), page: int = 1, per_page: int = 20) -> dict:
    """Run a SELECT with COUNT + LIMIT/OFFSET pagination (no full-table fetch)."""
    total = conn.execute(f"SELECT COUNT(*) FROM ({select})", params).fetchone()[0]
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, page), pages)
    rows = [dict(r) for r in conn.execute(
        select + " LIMIT ? OFFSET ?", (*params, per_page, (page - 1) * per_page))]
    return {
        "rows": rows, "total": total, "page": page, "pages": pages,
        "has_prev": page > 1, "has_next": page < pages,
        "prev_page": page - 1, "next_page": page + 1,
        "start_index": (page - 1) * per_page + 1 if total else 0,
        "end_index": min(total, page * per_page),
        "page_range": range(max(1, page - 2), min(pages, page + 2) + 1),
    }


def _like(q: str) -> str:
    return f"%{q}%"


# --- Runs ------------------------------------------------------------------

def run_exists(run_id: str) -> bool:
    with pipeline_conn() as conn:
        return conn.execute("SELECT 1 FROM pipeline_runs WHERE run_id=?", (run_id,)).fetchone() is not None


def get_run(run_id: str) -> dict | None:
    with pipeline_conn() as conn:
        row = conn.execute("SELECT * FROM pipeline_runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None


def list_runs(status: str = "", version: str = "", q: str = "", page: int = 1) -> dict:
    where, params = [], []
    if status:
        where.append("status=?")
        params.append(status)
    if version:
        where.append("reference_version=?")
        params.append(version)
    if q:
        where.append("(run_id LIKE ? OR source_file LIKE ?)")
        params += [_like(q), _like(q)]
    sql = "SELECT * FROM pipeline_runs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY started_at DESC, run_id DESC"
    with pipeline_conn() as conn:
        return paged(conn, sql, params, page, 15)


def run_choices() -> list[tuple[str, str]]:
    with pipeline_conn() as conn:
        rows = conn.execute(
            "SELECT run_id, reference_version, status, started_at FROM pipeline_runs"
            " ORDER BY started_at DESC, run_id DESC").fetchall()
    return [(r["run_id"], f"{r['run_id']}  ·  {r['reference_version']}  ·  {r['status']}") for r in rows]


def version_choices() -> list[tuple[str, str]]:
    with pipeline_conn() as conn:
        rows = conn.execute(
            "SELECT reference_version, row_count FROM reference_versions"
            " ORDER BY loaded_at DESC, reference_version").fetchall()
    return [(r["reference_version"], f"{r['reference_version']}  ({r['row_count']} codes)") for r in rows]


def start_run(*, input_path: str, reference_version: str, check_entities: bool = False,
              deprecated_policy: str | None = None, strict_columns: bool | None = None,
              event_from: str | None = None, event_to: str | None = None,
              run_id: str | None = None) -> str:
    """Launch a pipeline run in a background thread; returns the run_id immediately.

    The run row is created by RunTracker as RUNNING, so the detail page can poll
    ``run_status`` until it becomes SUCCESS or FAILED. Any exception is already
    recorded as FAILED by ``run_pipeline`` itself.
    """
    cfg = get_cfg()
    if deprecated_policy:
        cfg.deprecated_policy = deprecated_policy
    if strict_columns is not None:
        cfg.strict_columns = strict_columns
    cfg.validate()
    run_id = run_id or new_run_id()

    def worker():
        with _run_lock:
            conn = connect(cfg.db_path)
            try:
                run_pipeline(conn, cfg, input_path, reference_version,
                             check_entities=check_entities, event_from=event_from,
                             event_to=event_to, run_id=run_id)
            except Exception:
                log.exception("web run %s failed", run_id)
            finally:
                conn.close()

    threading.Thread(target=worker, name=f"refpipe-{run_id}", daemon=True).start()
    return run_id


def run_status(run_id: str) -> dict:
    run = get_run(run_id)
    if run is None:
        return {"run_id": run_id, "status": "STARTING"}
    return {"run_id": run_id, "status": run["status"], "error_message": run["error_message"]}


def rejects_for_run(run_id: str, reason: str = "", q: str = "", page: int = 1) -> dict:
    where, params = ["run_id=?"], [run_id]
    if reason:
        where.append("rejection_reason=?")
        params.append(reason)
    if q:
        where.append("(source_record_id LIKE ? OR raw_payload LIKE ?)")
        params += [_like(q), _like(q)]
    sql = ("SELECT * FROM rejected_records WHERE " + " AND ".join(where) + " ORDER BY row_num")
    with pipeline_conn() as conn:
        return paged(conn, sql, params, page, 25)


def outcomes_for_run(run_id: str, outcome: str = "", page: int = 1) -> dict:
    where, params = ["run_id=?"], [run_id]
    if outcome:
        where.append("outcome=?")
        params.append(outcome)
    sql = "SELECT * FROM run_outcomes WHERE " + " AND ".join(where) + " ORDER BY row_num"
    with pipeline_conn() as conn:
        return paged(conn, sql, params, page, 25)


def reasons_for_run(run_id: str) -> list[dict]:
    with pipeline_conn() as conn:
        rows = conn.execute(
            "SELECT rejection_reason AS reason, COUNT(*) AS n FROM rejected_records"
            " WHERE run_id=? GROUP BY rejection_reason", (run_id,)).fetchall()
    counts = {r["reason"]: r["n"] for r in rows}
    return [{"reason": r, "count": counts[r]} for r in REASON_ORDER if r in counts] + [
        {"reason": r, "count": n} for r, n in sorted(counts.items()) if r not in REASON_ORDER]


def report_path(run_id: str, filename: str) -> Path | None:
    if filename not in ("run_summary.json", "reconciliation.json", "rejects.csv"):
        return None
    path = Path(get_cfg().output_dir) / run_id / filename
    return path if path.is_file() else None


# --- Reference data ----------------------------------------------------------

def reference_versions() -> list[dict]:
    with pipeline_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT v.*, (SELECT COUNT(*) FROM pipeline_runs r WHERE r.reference_version=v.reference_version)"
            " AS run_count FROM reference_versions v ORDER BY loaded_at DESC, reference_version")]


def get_version(version: str) -> dict | None:
    with pipeline_conn() as conn:
        row = conn.execute("SELECT * FROM reference_versions WHERE reference_version=?",
                           (version,)).fetchone()
        return dict(row) if row else None


def reference_export_rows(version: str) -> list[dict]:
    with pipeline_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT product_code, description, code_status, effective_from, effective_to"
            " FROM reference_codes WHERE reference_version=? ORDER BY product_code, effective_from",
            (version,))]


def reference_codes(version: str, status: str = "", q: str = "", page: int = 1) -> dict:
    where, params = ["reference_version=?"], [version]
    if status:
        where.append("code_status=?")
        params.append(status)
    if q:
        where.append("(product_code LIKE ? OR description LIKE ?)")
        params += [_like(q), _like(q)]
    sql = ("SELECT * FROM reference_codes WHERE " + " AND ".join(where)
           + " ORDER BY product_code, effective_from")
    with pipeline_conn() as conn:
        return paged(conn, sql, params, page, 25)


def load_reference_file(version: str, path: str) -> str:
    with pipeline_conn() as conn:
        return store_reference(conn, version, path)


def entities_list(q: str = "", page: int = 1) -> dict:
    sql = "SELECT * FROM entities"
    params: tuple = ()
    if q:
        sql += " WHERE entity_id LIKE ?"
        params = (_like(q),)
    sql += " ORDER BY entity_id"
    with pipeline_conn() as conn:
        return paged(conn, sql, params, page, 30)


def load_entities_file(path: str) -> int:
    with pipeline_conn() as conn:
        return load_entities(conn, path)


def entity_count() -> int:
    with pipeline_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]


# --- Data browsers -------------------------------------------------------------

def validated_records(q: str = "", version: str = "", deprecated_only: bool = False,
                      page: int = 1) -> dict:
    where, params = [], []
    if q:
        where.append("(record_id LIKE ? OR product_code LIKE ? OR entity_id LIKE ?)")
        params += [_like(q)] * 3
    if version:
        where.append("reference_version=?")
        params.append(version)
    if deprecated_only:
        where.append("is_deprecated_code=1")
    sql = "SELECT * FROM validated_records"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY record_id"
    with pipeline_conn() as conn:
        return paged(conn, sql, params, page, 25)


def quarantine(q: str = "", reason: str = "", run_id: str = "", page: int = 1) -> dict:
    where, params = [], []
    if q:
        where.append("(source_record_id LIKE ? OR raw_payload LIKE ? OR run_id LIKE ?)")
        params += [_like(q)] * 3
    if reason:
        where.append("rejection_reason=?")
        params.append(reason)
    if run_id:
        where.append("run_id=?")
        params.append(run_id)
    sql = "SELECT * FROM rejected_records"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY rejected_at DESC, row_num"
    with pipeline_conn() as conn:
        return paged(conn, sql, params, page, 25)


# --- Dashboard -----------------------------------------------------------------

def dashboard_data() -> dict:
    with pipeline_conn() as conn:
        totals = dict(conn.execute("""
            SELECT COUNT(*) AS runs,
                   SUM(status='SUCCESS') AS success,
                   SUM(status='FAILED') AS failed,
                   SUM(status='RUNNING') AS running,
                   COALESCE(SUM(input_count),0) AS input_rows,
                   COALESCE(SUM(valid_count),0) AS valid_rows,
                   COALESCE(SUM(rejected_count),0) AS rejected_rows
            FROM pipeline_runs""").fetchone())
        totals["validated_records"] = conn.execute("SELECT COUNT(*) FROM validated_records").fetchone()[0]
        totals["quarantined"] = conn.execute("SELECT COUNT(*) FROM rejected_records").fetchone()[0]
        totals["versions"] = conn.execute("SELECT COUNT(*) FROM reference_versions").fetchone()[0]
        totals["entities"] = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        recent = [dict(r) for r in conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY started_at DESC, run_id DESC LIMIT 8")]
        reasons = [dict(r) for r in conn.execute(
            "SELECT rejection_reason AS reason, COUNT(*) AS n FROM rejected_records"
            " GROUP BY rejection_reason ORDER BY n DESC")]
        trend = [dict(r) for r in conn.execute(
            "SELECT run_id, reference_version, status, COALESCE(valid_count,0) AS valid_count,"
            " COALESCE(rejected_count,0) AS rejected_count, started_at FROM pipeline_runs"
            " WHERE status IN ('SUCCESS','FAILED') ORDER BY started_at DESC, run_id DESC LIMIT 12")]
        trend.reverse()
    total_rows = totals["valid_rows"] + totals["rejected_rows"]
    totals["valid_pct"] = round(100 * totals["valid_rows"] / total_rows, 1) if total_rows else 0
    totals["success_pct"] = round(100 * (totals["success"] or 0) / totals["runs"], 1) if totals["runs"] else 0
    max_reason = max((r["n"] for r in reasons), default=0)
    for r in reasons:
        r["pct"] = round(100 * r["n"] / max_reason, 1) if max_reason else 0
    max_trend = max((t["valid_count"] + t["rejected_count"] for t in trend), default=0)
    for t in trend:
        n = t["valid_count"] + t["rejected_count"]
        t["valid_pct"] = round(100 * t["valid_count"] / n, 1) if n else 0
        t["height_pct"] = round(100 * n / max_trend, 1) if max_trend else 0
    return {"totals": totals, "recent": recent, "reasons": reasons, "trend": trend}


# --- Compare ---------------------------------------------------------------------

def compare(run_a: str, run_b: str) -> dict:
    with pipeline_conn() as conn:
        result = compare_runs(conn, run_a, run_b)
        result["changed_rows"] = _changed_rows(conn, run_a, run_b)
    return result


def _changed_rows(conn, run_a: str, run_b: str, limit: int = 300) -> list[dict]:
    """Row-level detail behind the aggregate categories (first occurrence per record)."""
    primary = """
        SELECT record_id, product_code, outcome, COALESCE(reason,'') AS reason,
               is_deprecated_code, reference_version
        FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY record_id ORDER BY row_num) AS rn
              FROM run_outcomes WHERE run_id=? AND record_id NOT LIKE 'row:%')
        WHERE rn = 1
    """
    sql = f"""
        SELECT COALESCE(a.record_id, b.record_id) AS record_id,
               COALESCE(a.product_code, b.product_code) AS product_code,
               a.outcome AS a_outcome, a.reason AS a_reason, a.is_deprecated_code AS a_dep,
               b.outcome AS b_outcome, b.reason AS b_reason, b.is_deprecated_code AS b_dep
        FROM ({primary}) a LEFT JOIN ({primary}) b ON a.record_id = b.record_id
        UNION ALL
        SELECT b.record_id, b.product_code, NULL, NULL, NULL, b.outcome, b.reason, b.is_deprecated_code
        FROM ({primary}) b WHERE b.record_id NOT IN (SELECT record_id FROM run_outcomes WHERE run_id=?)
    """
    rows = conn.execute(sql, (run_a, run_b, run_b, run_a)).fetchall()
    changed = []
    for r in rows:
        d = dict(r)
        same = (d["a_outcome"] == d["b_outcome"] and d["a_reason"] == d["b_reason"]
                and d["a_dep"] == d["b_dep"]
                and d["a_outcome"] is not None and d["b_outcome"] is not None)
        if not same:
            changed.append(d)
    changed.sort(key=lambda d: d["record_id"])
    return changed[:limit]