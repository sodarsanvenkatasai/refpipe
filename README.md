# Reference-Code Validation Pipeline

A local, tested data-engineering pipeline that validates incoming records against **versioned reference
datasets** (YottaFlex 72-hour challenge). It handles effective dates, unknown codes, deprecated codes and
reference-data refreshes, makes reruns safe, and produces measurable, reconciled outputs.

* Python 3.11–3.13 recommended (verified on 3.11; see Troubleshooting), `pandas`, SQLite (stdlib). No services, no credentials, no network.
* Domain used: a generic *product-code* example (`record_id, entity_id, product_code, event_date, amount`);
  the brief does not prescribe an industry.

## Quick start

```bash
# 1. environment (Windows: py -m venv .venv && .venv\Scripts\activate)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. automated tests (72 tests, ~5 s)
python -m pytest -q

# 3. validate the 48-row sample against reference v1 (loads v1 + entities on first use)
python -m src.cli run --input data/sample/incoming_records.csv \
    --reference data/sample/reference_codes_v1.csv --reference-version v1 \
    --entities data/sample/entities.csv --run-id demo_v1

# 4. reference refresh: same input, new version v2 (a NEW run; v1 history is kept)
python -m src.cli run --input data/sample/incoming_records.csv \
    --reference data/sample/reference_codes_v2.csv --reference-version v2 --check-entities --run-id demo_v2

# 5. what changed between the two versions?
python -m src.cli compare --run-a demo_v1 --run-b demo_v2

# 6. idempotent rerun: no new target rows (published_count = 0, unchanged_count = 28)
python -m src.cli run --input data/sample/incoming_records.csv --reference-version v2 --check-entities

# 7. larger deterministic dataset (100,000 rows) and benchmark
python scripts/generate_data.py --rows 100000 --seed 42 --out-dir data/generated
python -m src.cli run --input data/generated/incoming_records.csv \
    --reference data/generated/reference_codes_v1.csv --reference-version v1 --entities data/generated/entities.csv
python scripts/benchmark.py --rows 100000 --seed 42
```

Outputs land in `output/<run_id>/` (`run_summary.json`, `reconciliation.json`, `rejects.csv`), the SQLite DB
is `output/pipeline.db`, the log is `output/pipeline.log`. `output/` and `*.db` are git-ignored; frozen
examples are committed under [`docs/sample_output/`](docs/sample_output).

Useful commands: `python -m src.cli runs` (run history), `recover` (close runs killed mid-flight), `load-reference`, `load-entities`, `--help`.

## Web UI (Django)

A local web console wraps the same engine — dashboard with charts, run uploads with live progress,
run detail with reconciliation checks and report downloads, run comparison, reference-version and
entity management, and searchable browsers for the target table and quarantine.

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
# open http://127.0.0.1:8000/ and click "Run the 48-row sample"
```

It shares `output/pipeline.db` with the CLI (runs appear in both). Full guide:
[docs/WEB_UI.md](docs/WEB_UI.md) · screenshots: [docs/ui_screenshots/](docs/ui_screenshots/) ·
UI tests: `python manage.py test webapp.ui` (23 tests).

## What it does

```
incoming_records.csv ─┐                       reference_codes_<version>.csv ─► reference_versions / reference_codes
                      ▼                                   (immutable, checksummed, one version chosen per run)
              INGEST (csv, raw values kept) ─► batch-level SCHEMA check ─► FAIL run on file problems
                      ▼
        row validation, first failure wins:  malformed → missing id/code/date → invalid date/amount
                      ▼                        → duplicate → unknown code → status → effective date → orphan entity
        ┌─────────────┴──────────────┐
   VALID rows                   REJECTED rows
   transform (normalize,        quarantine: raw payload + reason + run_id + source_file + reference_version
   derive, hash)                (append-only rejected_records)
        ▼                            │
   stage_valid_records ──► ONE TRANSACTION: write rejects + run_outcomes, upsert target,
                           RECONCILE, mark run SUCCESS  (any failure → rollback, run FAILED)
                      ▼
        validated_records (current state)  +  output/<run_id>/ report files
```

Modules (`src/`): `cli`, `config`, `db` (schema), `ingestion`, `schema_validation`, `reference_loader`,
`validation` (pure rules + vectorized engine), `transformation`, `quarantine`, `publishing`,
`reconciliation`, `metrics` (run lifecycle), `pipeline` (orchestrator), `compare`, `reasons`.
Business logic lives in the modules; `cli.py` only parses arguments.

## Key assumptions (full list with rationale: [docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md))

| Topic | Decision |
|---|---|
| Reference selection | **Explicit** `--reference-version` per run; never "latest". A run uses exactly one version. |
| Versions | Immutable. Same name + same checksum = no-op; same name + different content = refused. |
| Date semantics | Calendar dates, no time zone; `effective_from` and `effective_to` both **inclusive**; blank `effective_to` = open-ended. |
| Unknown code | `UNKNOWN_CODE` |
| Deprecated codes | Default `allow_historical`: accepted (flagged `is_deprecated_code=1`) if the event is inside the code's window; events outside → `CODE_EXPIRED`/`CODE_NOT_YET_EFFECTIVE`. `--deprecated-policy reject` rejects all with `DEPRECATED_CODE`. `INACTIVE` is always rejected. |
| Duplicates | Key = `record_id` (after trimming). In a batch the **first** copy wins, later copies → `DUPLICATE_RECORD`. Across runs the target is an **upsert** on `record_id` (see idempotency). |
| Multiple failures | Only the **first** failure in the documented order is recorded. |
| Unexpected columns / types | Warn and ignore extra columns (`--strict-columns` fails the batch). Missing required column / empty file / non-UTF-8 → run FAILED. Changed value types (text amount, other date format) → row quarantined as `INVALID_AMOUNT`/`INVALID_DATE`, never silently coerced (tested). |
| Accounting | `input = valid + rejected`; duplicates are a *subset* of rejected, reported separately. |

## Idempotency, retry, backfill

* **Rerun of the same input**: target key is `record_id`; the upsert compares a content `row_hash` + reference
  version. Identical rows are `unchanged` (nothing written), changed rows are `updated`, new ones `inserted`.
  A rerun adds an audit row to `pipeline_runs` and `run_outcomes`, but zero rows to `validated_records`.
* **Partial failure**: validated rows are staged (own transaction); the publish step (rejects, outcomes, upsert,
  reconciliation, run status) is **one transaction**. A crash or reconciliation failure rolls it all back, the
  run is marked `FAILED` with the error message, and a retry simply re-runs the same command.
  Hidden test hooks `--fail-at after_stage|before_commit` demonstrate this (see `tests/test_pipeline.py`).
* **Reprocessing after a refresh**: run the same input again with the new version. The earlier run's rejects
  stay in `rejected_records` (append-only); `compare` shows `rejected_now_valid`, `valid_now_rejected`,
  `valid_now_deprecated`, `reason_changed`, … and which product codes changed.
* **Backfill**: `--event-from/--event-to` restrict processing to an event-date range; range and version are
  stored in the run's `config_json`; skipped rows are reported as `skipped_out_of_range` (rows with an
  unparseable date are *not* skipped – they are rejected).

## Data: sample, generator, labels

* `data/sample/` – 48 hand-inspectable rows covering every category, plus `reference_codes_v1/v2.csv`,
  `entities.csv`, and **`expected_results.csv` (labels, evaluation only – never read by the pipeline)**.
* `scripts/generate_data.py --rows N --seed S` – deterministic (same args → byte-identical files), includes
  valid, boundary dates, blank/invalid values, duplicates, unknown/deprecated/inactive/expired/not-yet-effective
  codes, a code with two windows and a gap, orphan entities, malformed lines, and out-of-order rows.
  Tests verify the pipeline's v1 *and* v2 outcome for every row against the labels (sample + 5,000 generated rows).
* All data is synthetic.

## Results (this machine, Windows 11, Python 3.14, pandas 3.0.5)

* Tests: `72 passed` (+ 23 Django UI tests) – evidence in [docs/TEST_EVIDENCE.txt](docs/TEST_EVIDENCE.txt).
* Sample, v1: 48 in → 22 valid, 26 rejected (2 duplicates), reconciliation 10/10 checks pass.
  Sample, v2: 28 valid / 20 rejected; compare: 6 rejected→valid, 1 valid→now-deprecated, 1 reason changed, 0 valid→rejected.
  Rerun of v2: `inserted 0, updated 0, unchanged 28`.
* 100,000 rows: ~6.6 s end-to-end (~15k rows/s). Baseline vs optimized: see [docs/PERFORMANCE.md](docs/PERFORMANCE.md).

## Reconciliation (runs inside the publish transaction)

input = valid + rejected · outcomes persisted = input · rejects persisted = rejected · duplicates ⊆ rejected ·
staged = valid · staged = inserted + updated + unchanged · every staged key present in target with identical content ·
amount control total (cents) staged = target · no duplicate target keys · rows written by the run carry the run's
reference version. If any check fails the run is rolled back and marked `FAILED`.

## Documentation

* [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md) – schemas, types, keys, grain, nullability, derived fields, naming.
* [docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md) – trade-offs and alternatives.
* [docs/PERFORMANCE.md](docs/PERFORMANCE.md) – measurements and the optimization.
* [docs/ai/](docs/ai) – AI usage disclosure and prompt history.

## Known limitations / next steps

* SQLite is single-writer; a concurrent second run would wait/fail on the lock. A crash that kills the process
  leaves the run row in `RUNNING`; `python -m src.cli recover` marks such runs `FAILED` (safe: publishing is one transaction) so a retry is clearly allowed.
* Whole batch is processed in memory (fine to millions of rows; chunking would be next for much larger files).
* Only the first failure per row is recorded (all-failures reporting would need a per-rule matrix).
* The current-state target keeps a v1-valid row if a v2 run rejects it; the change is visible via `compare`
  and `run_outcomes`, but the target is not auto-retracted (a deliberate, documented choice).
* The `ORPHAN_ENTITY` check is opt-in per run (`--entities`/`--check-entities`).
* No external orchestrator. A local Django web console is included ([docs/WEB_UI.md](docs/WEB_UI.md));
  it is a thin layer over `src` and adds no business logic.

## Troubleshooting

* **Tests or imports hang for minutes** – seen with a system-wide Python 3.14 + pandas 3.0 install on one Windows machine
  (not reproducible in a fresh venv). Use a fresh virtual environment with Python 3.11–3.13; in that setup the
  suite runs in ~5 s.
* **`output/` / `pipeline.db` missing** – they are created on first run and are git-ignored by design.
