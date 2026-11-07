"""SQLite schema and connection helper."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id            TEXT PRIMARY KEY,
    started_at        TEXT NOT NULL,
    ended_at          TEXT,
    status            TEXT NOT NULL CHECK (status IN ('RUNNING','SUCCESS','FAILED')),
    source_file       TEXT NOT NULL,
    source_checksum   TEXT,
    reference_version TEXT NOT NULL,
    config_json       TEXT,
    input_count       INTEGER,
    valid_count       INTEGER,
    rejected_count    INTEGER,
    duplicate_count   INTEGER,
    skipped_out_of_range INTEGER,
    inserted_count    INTEGER,
    updated_count     INTEGER,
    unchanged_count   INTEGER,
    published_count   INTEGER,
    duration_seconds  REAL,
    error_message     TEXT,
    warnings          TEXT
);

CREATE TABLE IF NOT EXISTS reference_versions (
    reference_version TEXT PRIMARY KEY,
    loaded_at         TEXT NOT NULL,
    source_file       TEXT NOT NULL,
    checksum          TEXT NOT NULL,
    row_count         INTEGER NOT NULL,
    status            TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS reference_codes (
    reference_version TEXT NOT NULL REFERENCES reference_versions(reference_version),
    product_code      TEXT NOT NULL,
    description       TEXT,
    code_status       TEXT NOT NULL CHECK (code_status IN ('ACTIVE','DEPRECATED','INACTIVE')),
    effective_from    TEXT NOT NULL,
    effective_to      TEXT,
    PRIMARY KEY (reference_version, product_code, effective_from)
);
CREATE INDEX IF NOT EXISTS ix_reference_lookup ON reference_codes (reference_version, product_code);

CREATE TABLE IF NOT EXISTS entities (
    entity_id TEXT PRIMARY KEY,
    loaded_at TEXT NOT NULL
);

-- Per-run staging of rows that passed validation (removed once the run succeeds).
CREATE TABLE IF NOT EXISTS stage_valid_records (
    run_id            TEXT NOT NULL,
    record_id         TEXT NOT NULL,
    row_hash          TEXT NOT NULL,
    reference_version TEXT NOT NULL,
    source_file       TEXT NOT NULL,
    row_num           INTEGER NOT NULL,
    entity_id         TEXT,
    product_code      TEXT NOT NULL,
    event_date        TEXT NOT NULL,
    event_year_month  TEXT NOT NULL,
    amount            REAL,
    amount_cents      INTEGER,
    is_deprecated_code INTEGER NOT NULL,
    PRIMARY KEY (run_id, record_id)
);

-- Target: current-state table, one row per record_id (idempotent upsert).
CREATE TABLE IF NOT EXISTS validated_records (
    record_id         TEXT PRIMARY KEY,
    row_hash          TEXT NOT NULL,
    run_id            TEXT NOT NULL,
    reference_version TEXT NOT NULL,
    source_file       TEXT NOT NULL,
    entity_id         TEXT,
    product_code      TEXT NOT NULL,
    event_date        TEXT NOT NULL,
    event_year_month  TEXT NOT NULL,
    amount            REAL,
    amount_cents      INTEGER,
    is_deprecated_code INTEGER NOT NULL,
    published_at      TEXT NOT NULL
);

-- Quarantine: append-only, never erased on reprocessing.
CREATE TABLE IF NOT EXISTS rejected_records (
    reject_id         TEXT PRIMARY KEY,           -- '<run_id>:<row_num>'
    run_id            TEXT NOT NULL,
    source_file       TEXT NOT NULL,
    source_record_id  TEXT NOT NULL,              -- record_id, or 'row:<n>' if unreadable
    row_num           INTEGER NOT NULL,
    raw_payload       TEXT NOT NULL,              -- original row as JSON (or raw fields if malformed)
    rejection_reason  TEXT NOT NULL,
    reference_version TEXT NOT NULL,
    rejected_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rejected_run ON rejected_records (run_id);
CREATE INDEX IF NOT EXISTS ix_rejected_src ON rejected_records (source_record_id);

-- Outcome of every processed row in every run (history used for version comparison).
CREATE TABLE IF NOT EXISTS run_outcomes (
    run_id            TEXT NOT NULL,
    row_num           INTEGER NOT NULL,
    record_id         TEXT NOT NULL,
    product_code      TEXT NOT NULL DEFAULT '',
    outcome           TEXT NOT NULL CHECK (outcome IN ('VALID','REJECTED')),
    reason            TEXT,
    is_deprecated_code INTEGER NOT NULL DEFAULT 0,
    reference_version TEXT NOT NULL,
    PRIMARY KEY (run_id, row_num)
);
CREATE INDEX IF NOT EXISTS ix_outcomes_record ON run_outcomes (run_id, record_id);
"""


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: we manage transactions explicitly (BEGIN/COMMIT/ROLLBACK).
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn
