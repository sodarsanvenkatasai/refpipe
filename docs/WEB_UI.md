# Web UI (Django)

A local web console for the reference-code validation pipeline. It is a **thin layer over the
existing `src` package** — exactly like `src/cli.py` is a thin layer for the terminal. All business
logic (validation rules, publishing transaction, reconciliation, comparison) stays in the pipeline
modules; the UI adds uploads, background execution, browsing and charts.

No network services, no CDN, no webfonts: templates, CSS and SVG charts are served locally.

## Quick start

```bash
# from the repository root, with the project venv active
pip install -r requirements.txt          # now includes Django
python manage.py migrate                 # framework bookkeeping only (output/webui.sqlite3)
python manage.py runserver 127.0.0.1:8000
```

Open <http://127.0.0.1:8000/>. First visit shows an onboarding card with a one-click
**“Run the 48-row sample”** button (loads `sample_v1` + sample entities, runs the bundled sample).

The pipeline database is the same one the CLI uses (`output/pipeline.db`, configurable via
`config.toml` / `REFPIPE_DB_PATH` / `REFPIPE_OUTPUT_DIR`). Runs started in the UI appear in
`python -m src.cli runs` and vice versa.

## Pages

| Page | URL | What you can do |
|---|---|---|
| Dashboard | `/` | KPIs, outcome donut, per-run throughput bars, rejection-reason bars, recent runs, one-click sample |
| New run | `/runs/new/` | Upload incoming CSV; pick a loaded reference version **or** upload a new immutable snapshot; optional entities CSV; deprecated policy, strict columns, event-date backfill range, custom run id |
| Runs | `/runs/` | History with status/version/search filters and pagination |
| Run detail | `/runs/<id>/` | Live progress (polls until SUCCESS/FAILED), counts, tabs: overview / rejects (raw payload) / row outcomes / reconciliation checks; downloads of `run_summary.json`, `reconciliation.json`, `rejects.csv` |
| Compare | `/compare/` | Pick two runs → category cards, transitions, changed product codes, row-level changed records |
| Versions | `/reference/` | Immutable snapshots with checksums; load a new version; browse codes with status filter/search; download any version as `reference_codes_<version>.csv` |
| Entities | `/entities/` | Load entity ids, browse/search them |
| Validated records | `/data/validated/` | Target table browser (search, version filter, deprecated-only) |
| Quarantine | `/data/quarantine/` | Append-only rejects browser (search, reason/run filters, raw payload) |

## How a web run executes

1. `POST /runs/new/` validates the form, stores uploads under `output/uploads/<run_id>/`,
   and loads any new reference/entities **synchronously** so file errors appear inline on the form
   (same `PipelineError` codes as the CLI, e.g. `REFERENCE_IMMUTABLE`, `REFERENCE_INVALID`).
2. `services.start_run()` launches `src.pipeline.run_pipeline` in a background thread and returns
   immediately. A process-wide lock serializes runs because SQLite is single-writer.
3. The run page polls `/runs/<id>/status.json` once per second and reloads when the run finishes.
   Failures are recorded as `FAILED` by the pipeline itself; the target table stays untouched
   (publish is one transaction), so retrying is safe.

## Configuration

| Setting | Default | Override |
|---|---|---|
| Pipeline DB | `output/pipeline.db` | `REFPIPE_DB_PATH` (same env var as the CLI) |
| Reports dir | `output/` | `REFPIPE_OUTPUT_DIR` |
| Uploads | `output/uploads/` | follows the output dir |
| Django DB | `output/webui.sqlite3` | framework bookkeeping only |
| Debug / hosts | debug on, localhost | `REFPIPE_WEB_DEBUG`, `REFPIPE_WEB_ALLOWED_HOSTS` |
| Secret key | local dev key | `REFPIPE_WEB_SECRET_KEY` (set one if you ever expose this beyond localhost) |

`config.toml` defaults (deprecated policy, engine, strict columns) are honoured; the run form
overrides them per run, just like CLI flags.

## Tests

```bash
python manage.py test webapp.ui     # 21 end-to-end UI tests (isolated temp DB per test)
python -m pytest -q                 # the 72 pipeline tests, unchanged
```

The UI tests exercise real pipeline runs on the bundled sample: sample run, uploads, idempotent
rerun, failed-run rollback, reference immutability, compare categories, downloads, filters and 404s.

## Screenshots

[`docs/ui_screenshots/`](ui_screenshots/) — onboarding, run detail, reconciliation, compare,
dashboard and the target-table browser (narrow viewport; the layout is responsive).

## Notes and limitations

* Local tool by design: no authentication. Do not expose it to a network without adding auth,
  a real secret key and `DEBUG=0`.
* Runs execute in-process (background thread). A server restart leaves a `RUNNING` row behind,
  same known limitation as the CLI; the run page stops polling after ~30 minutes.
* Reference CSVs that embed a `reference_version` column must be loaded under that same name
  (the loader keeps only matching rows). The form help text says so; the sample button handles
  it by storing a relabelled copy as `sample_v1`.
* Reference CSVs must contain the five reference columns only — `product_code, description,
  status, effective_from, effective_to` — plus optional `reference_version`. Missing or extra
  columns are rejected with `REFERENCE_INVALID` before anything is stored.