"""Load, validate and store versioned reference snapshots (immutable once loaded)."""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from .ingestion import file_checksum
from .reasons import PipelineError, REFERENCE_COLUMNS, REFERENCE_STATUSES
from .validation import OPEN_END, parse_iso_date, windows_by_code

REFERENCE_ERROR = "REFERENCE_INVALID"
REFERENCE_IMMUTABLE = "REFERENCE_IMMUTABLE"
REFERENCE_NOT_FOUND = "REFERENCE_NOT_FOUND"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_reference_file(path: str, version: str) -> pd.DataFrame:
    """Read + validate a reference CSV for `version`. Raises PipelineError listing every problem.

    If the file has a `reference_version` column only the rows for `version` are used, so one file
    may carry several versions. Without that column every row belongs to `version`.
    Only the five reference columns plus optional `reference_version` are accepted;
    any other column is rejected.
    Open-ended windows (blank effective_to) are stored as 9999-12-31.
    """
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    except FileNotFoundError:
        raise PipelineError(REFERENCE_ERROR, f"reference file not found: {path}")
    except (UnicodeDecodeError, csv.Error) as exc:
        raise PipelineError(REFERENCE_ERROR, f"unreadable reference file: {exc}")
    if not rows:
        raise PipelineError(REFERENCE_ERROR, "reference file has no data rows")
    cols = [c.strip() for c in rows[0].keys() if c is not None]
    missing = [c for c in REFERENCE_COLUMNS if c not in cols]
    if missing:
        raise PipelineError(REFERENCE_ERROR, f"missing reference column(s): {missing}")
    allowed = set(REFERENCE_COLUMNS) | {"reference_version"}
    unexpected = [c for c in cols if c not in allowed]
    if unexpected:
        raise PipelineError(REFERENCE_ERROR, f"unexpected reference column(s): {unexpected}; "
                            f"allowed: {REFERENCE_COLUMNS + ['reference_version']}")

    errors: list[str] = []
    out: list[dict] = []
    for n, raw in enumerate(rows, start=1):
        r = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        if "reference_version" in r and r["reference_version"] != version:
            continue
        code = r["product_code"].upper()
        status = r["status"].upper()
        ef, et = parse_iso_date(r["effective_from"]), r["effective_to"]
        if not code:
            errors.append(f"row {n}: blank product_code")
        if status not in REFERENCE_STATUSES:
            errors.append(f"row {n}: invalid status {r['status']!r} (expected {REFERENCE_STATUSES})")
        if ef is None:
            errors.append(f"row {n}: invalid effective_from {r['effective_from']!r}")
        if et == "":
            et = OPEN_END
        elif parse_iso_date(et) is None:
            errors.append(f"row {n}: invalid effective_to {r['effective_to']!r}")
        if ef is not None and parse_iso_date(et) is not None and ef > et:
            errors.append(f"row {n}: effective_from {ef} is after effective_to {et}")
        out.append({"product_code": code, "description": r.get("description", ""),
                    "status": status, "effective_from": ef or "", "effective_to": et})
    if not out:
        raise PipelineError(REFERENCE_ERROR, f"no reference rows found for version {version!r}")
    df = pd.DataFrame(out)

    dup = df[df.duplicated(["product_code", "effective_from"], keep=False)]
    for (code, ef), _ in dup.groupby(["product_code", "effective_from"]):
        errors.append(f"duplicate (product_code, effective_from) = ({code}, {ef})")
    # overlapping windows for the same code are not allowed
    for code, g in df.sort_values(["product_code", "effective_from"]).groupby("product_code"):
        g = g.reset_index(drop=True)
        for i in range(1, len(g)):
            if g.loc[i, "effective_from"] <= g.loc[i - 1, "effective_to"] and \
               g.loc[i, "effective_from"] != g.loc[i - 1, "effective_from"]:
                errors.append(f"overlapping effective windows for {code}")
                break
    if errors:
        shown = errors[:20]
        more = f" (+{len(errors) - 20} more)" if len(errors) > 20 else ""
        raise PipelineError(REFERENCE_ERROR, "; ".join(shown) + more)
    return df


def store_reference(conn: sqlite3.Connection, version: str, path: str) -> str:
    """Validate and store a snapshot. Returns 'loaded' or 'already_loaded'.

    Versions are immutable: re-loading the same version with identical file content is a no-op;
    different content for an existing version is refused (load it as a new version instead).
    """
    checksum = file_checksum(path)
    existing = conn.execute("SELECT checksum FROM reference_versions WHERE reference_version=?",
                            (version,)).fetchone()
    if existing:
        if existing["checksum"] == checksum:
            return "already_loaded"
        raise PipelineError(
            REFERENCE_IMMUTABLE,
            f"reference version {version!r} already exists with different content; "
            f"versions are immutable - load the new content under a new version name")
    df = read_reference_file(path, version)
    conn.execute("BEGIN")
    try:
        conn.execute(
            "INSERT INTO reference_versions(reference_version, loaded_at, source_file, checksum, row_count)"
            " VALUES (?,?,?,?,?)", (version, now_iso(), str(path), checksum, len(df)))
        conn.executemany(
            "INSERT INTO reference_codes(reference_version, product_code, description, code_status,"
            " effective_from, effective_to) VALUES (?,?,?,?,?,?)",
            [(version, r.product_code, r.description, r.status, r.effective_from, r.effective_to)
             for r in df.itertuples()])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return "loaded"


def get_reference(conn: sqlite3.Connection, version: str) -> pd.DataFrame:
    """Return the stored snapshot (product_code, status, effective_from, effective_to)."""
    if not conn.execute("SELECT 1 FROM reference_versions WHERE reference_version=?", (version,)).fetchone():
        raise PipelineError(REFERENCE_NOT_FOUND,
                            f"reference version {version!r} is not loaded; load it with --reference <file>")
    rows = conn.execute(
        "SELECT product_code, code_status AS status, effective_from, effective_to"
        " FROM reference_codes WHERE reference_version=?", (version,)).fetchall()
    return pd.DataFrame([tuple(r) for r in rows],
                        columns=["product_code", "status", "effective_from", "effective_to"], dtype="object")


def sql_lookup(conn: sqlite3.Connection, version: str):
    """Per-row database lookup used only by the slow `rowwise` baseline engine."""
    from .validation import Window

    def lookup(code: str):
        cur = conn.execute(
            "SELECT effective_from, effective_to, code_status FROM reference_codes"
            " WHERE reference_version=? AND product_code=? ORDER BY effective_from", (version, code))
        return [Window(*tuple(r)) for r in cur.fetchall()]
    return lookup


def load_entities(conn: sqlite3.Connection, path: str) -> int:
    """Load parent entity ids (column `entity_id`) for the referential-integrity check."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "entity_id" not in [f.strip() for f in reader.fieldnames]:
            raise PipelineError(REFERENCE_ERROR, "entities file needs an 'entity_id' column")
        ids = sorted({(r["entity_id"] or "").strip() for r in reader} - {""})
    conn.execute("BEGIN")
    conn.executemany("INSERT OR IGNORE INTO entities(entity_id, loaded_at) VALUES (?,?)",
                     [(i, now_iso()) for i in ids])
    conn.execute("COMMIT")
    return len(ids)


def get_entity_ids(conn: sqlite3.Connection) -> Optional[set]:
    return {r[0] for r in conn.execute("SELECT entity_id FROM entities")}
