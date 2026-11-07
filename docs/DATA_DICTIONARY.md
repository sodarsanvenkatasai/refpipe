# Data dictionary

Naming: `snake_case` tables/columns, ISO-8601 dates (`YYYY-MM-DD`) and UTC timestamps, explicit `run_id` /
`source_file` lineage columns. Run ids look like `run_20261107T061729_280b2c` (UTC time + random suffix, or
caller-supplied `--run-id`). Reject ids are deterministic: `<run_id>:<row_num>`.

## Source: `incoming_records.csv`

Grain: **one row = one business event/record**, identified by `record_id`.

| column | type | null? | rules |
|---|---|---|---|
| `record_id` | text | **no** (blank → `MISSING_RECORD_ID`) | business/primary key; trimmed; unique per batch (first wins) |
| `entity_id` | text | yes | parent key; if the entity check is on and the value is non-blank it must exist in `entities` (`ORPHAN_ENTITY`) |
| `product_code` | text | **no** (`MISSING_CODE`) | trimmed + upper-cased before lookup |
| `event_date` | date | **no** (`MISSING_EVENT_DATE`) | strict `YYYY-MM-DD` real calendar date, else `INVALID_DATE` |
| `amount` | decimal | yes | non-negative, ≤ 12 integer digits, ≤ 2 decimals, no thousands separators, else `INVALID_AMOUNT` |
| `source_file` | text | yes | optional column, ignored – lineage uses the real input path |

Required header columns: `record_id, entity_id, product_code, event_date, amount` (missing → run `FAILED`,
`SCHEMA_MISMATCH`). Other columns → warning, ignored (or failure with `--strict-columns`). Blank lines are skipped;
`row_num` = 1-based index of non-blank data rows. Rows with the wrong number of fields → `MALFORMED_ROW`.

## Reference: `reference_codes_<version>.csv`

Columns: `[reference_version,] product_code, description, status, effective_from, effective_to`.
`status` ∈ `ACTIVE | DEPRECATED | INACTIVE`; blank `effective_to` = open-ended (stored `9999-12-31`).
Loader rejects: missing or unexpected columns (only the five reference columns plus optional
`reference_version` are accepted), bad status/dates, `from > to`, duplicate `(product_code, effective_from)`,
overlapping windows for one code. A code may have several non-overlapping windows. If a `reference_version` column exists only the requested
version's rows are used.

## Target database (SQLite, `output/pipeline.db`)

| table | key | purpose |
|---|---|---|
| `pipeline_runs` | `run_id` | one row per execution: status `RUNNING/SUCCESS/FAILED`, `started_at`, `ended_at`, `duration_seconds`, `source_file`, `source_checksum`, `reference_version`, `config_json` (policy, engine, backfill range…), counts (`input/valid/rejected/duplicate/skipped_out_of_range/inserted/updated/unchanged/published`), `error_message`, `warnings` |
| `reference_versions` | `reference_version` | immutable snapshot header: `loaded_at`, `source_file`, `checksum` (sha256), `row_count` |
| `reference_codes` | (`reference_version`, `product_code`, `effective_from`) FK→`reference_versions` | snapshot rows; `code_status`, `effective_to`; index on (`reference_version`,`product_code`) |
| `entities` | `entity_id` | parent keys for referential integrity |
| `stage_valid_records` | (`run_id`, `record_id`) | validated rows awaiting publish; deleted when the run succeeds, kept after failure for diagnosis, cleared on retry |
| `validated_records` | `record_id` | **target**, current-state: one row per record. Columns: cleaned `entity_id, product_code, event_date, amount, amount_cents`; derived `event_year_month`, `is_deprecated_code`, `row_hash`; lineage `run_id, reference_version, source_file, published_at` |
| `rejected_records` | `reject_id` | **quarantine**, append-only: `run_id, source_file, source_record_id` (or `row:<n>`), `row_num`, `raw_payload` (original row JSON; raw fields for malformed), `rejection_reason`, `reference_version`, `rejected_at` |
| `run_outcomes` | (`run_id`, `row_num`) | outcome (`VALID/REJECTED`), `reason`, `is_deprecated_code`, `product_code` for **every** row of every run → history for version comparison |

Relationships: `reference_codes.reference_version → reference_versions`; `validated_records.run_id`,
`rejected_records.run_id`, `run_outcomes.run_id → pipeline_runs.run_id`; `entity_id → entities` (logical, enforced by
validation when enabled).

### Derived fields
* `amount_cents` = exact integer cents of `amount` (used for the control total); `amount` = cents / 100.
* `event_year_month` = first 7 chars of `event_date`.
* `is_deprecated_code` = 1 when the matched window's status is `DEPRECATED` (accepted under `allow_historical`).
* `row_hash` = sha256 of `record_id|entity_id|product_code|event_date|amount_cents` (deterministic).

### Rejection reasons (stable codes, evaluation order)
`MALFORMED_ROW`, `MISSING_RECORD_ID`, `MISSING_CODE`, `MISSING_EVENT_DATE`, `INVALID_DATE`, `INVALID_AMOUNT`,
`DUPLICATE_RECORD`, `UNKNOWN_CODE`, `INACTIVE_CODE`, `DEPRECATED_CODE` (policy `reject` only),
`CODE_NOT_YET_EFFECTIVE`, `CODE_EXPIRED`, `ORPHAN_ENTITY`. Batch-level: `SCHEMA_MISMATCH` (run `FAILED`).

## Sample queries

```sql
-- outcome counts per run and reason
SELECT run_id, outcome, COALESCE(reason,'-') AS reason, COUNT(*) FROM run_outcomes GROUP BY 1,2,3;
-- reconciliation: target rows per reference version and control total
SELECT reference_version, COUNT(*), SUM(amount_cents)/100.0 FROM validated_records GROUP BY 1;
-- records rejected under v1 that are valid under v2
SELECT a.record_id FROM run_outcomes a JOIN run_outcomes b ON a.record_id=b.record_id
WHERE a.run_id='demo_v1' AND b.run_id='demo_v2' AND a.outcome='REJECTED' AND b.outcome='VALID';
-- uniqueness of target keys (expects 0 rows)
SELECT record_id FROM validated_records GROUP BY 1 HAVING COUNT(*)>1;
```
