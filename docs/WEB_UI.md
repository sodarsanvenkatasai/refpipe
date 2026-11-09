# Web UI (Django)

A local web console for the reference-code validation pipeline. It is a **thin layer over the
existing `src` package** — exactly like `src/cli.py` is a thin layer for the terminal. All business
logic (validation rules, publishing transaction, reconciliation, comparison) stays in the pipeline
modules; the UI adds uploads, background execution, browsing and charts.

No network services and no CDN: templates, CSS, JavaScript, the inline SVG donut and sparklines and the three self-hosted
`woff2` fonts (Space Grotesk, Inter, JetBrains Mono) are all served from `webapp/ui/static/`. The console works
with the machine offline.

## Quick start

```bash
# from the repository root, with the project venv active
pip install -r requirements.txt          # now includes Django
python manage.py migrate                 # framework bookkeeping only (output/webui.sqlite3)
python manage.py runserver 127.0.0.1:8000
```

On Windows, `start.bat` does all three steps and opens the browser (it checks the port first, so running it
twice is harmless).

Open <http://127.0.0.1:8000/>. First visit shows an onboarding card with a one-click
**“Run the 48-row sample”** button (loads `sample_v1` + sample entities, runs the bundled sample).

The pipeline database is the same one the CLI uses (`output/pipeline.db`, configurable via
`config.toml` / `REFPIPE_DB_PATH` / `REFPIPE_OUTPUT_DIR`). Runs started in the UI appear in
`python -m src.cli runs` and vice versa.

## Interface

The console uses a fixed left rail (navigation + run status) instead of a top navbar, a cobalt/violet/magenta
palette on warm and cool neutrals, and one shared component sheet (`static/ui/css/app.css`). Light and dark
themes are both first-class and the choice persists in `localStorage`; a motion toggle disables the entrance
animations for anyone who prefers a still page. Tables sort client-side with `aria-sort` announced, `Ctrl+K`
opens a command palette that jumps to any page or run. The donut and sparklines are inline SVG generated server-side
by `templatetags/ui_extras.py`; the bar charts are semantic HTML painted with severity-keyed CSS gradients (rose for
hard errors, amber for warnings, cobalt for reference issues) with counts and share-of-rejects beside each bar — no
chart library, no canvas, no network. Below 1100 px the rail becomes a slide-in
drawer behind a burger button; the layout was verified in a real browser at 1440 px and at 390 px. Registry rows open
a second, right-hand drawer for quick inspection and rerun; scrollbars, focus rings and the reduced-motion path all
follow the same sheet.

## Pages

| Page | URL | What you can do |
|---|---|---|
| Dashboard | `/` | KPIs, outcome donut, per-run throughput bars, rejection-reason bars, recent runs, one-click sample |
| New run | `/runs/new/` | Upload incoming CSV; pick a loaded reference version **or** upload a new immutable snapshot; optional entities CSV; deprecated policy, strict columns, event-date backfill range, custom run id |
| Runs | `/runs/` | History with status/version/search filters and pagination; failed runs show their recorded error inline (full text on hover); every row opens a **quick view** drawer with the full error, counts and **Rerun safely** without leaving the list |
| Run detail | `/runs/<id>/` | Live progress (polls until SUCCESS/FAILED), counts, error banner on failure, tabs: overview / rejects (raw payload) / row outcomes / reconciliation checks; downloads of `run_summary.json`, `reconciliation.json`, `rejects.csv`; **Rerun safely** re-executes the stored input with the recorded options |
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
   Failures are recorded as `FAILED` by the pipeline itself with the error message; the target table stays
   untouched (publish is one transaction), so retrying is safe.
4. **Rerun safely** — `POST /runs/<id>/rerun/` starts a new run from the finished run's stored upload and its
   recorded `config_json` (version, entity check, deprecated policy, strict columns, backfill range). It refuses
   a run that is still in flight and refuses a run whose stored file is gone, telling you to upload it again.
   Because publishing is one idempotent upsert, the rerun reports `published 0` and `unchanged = valid_count`
   instead of duplicating rows. A rerun of a failed run fails again with the same recorded error, which is the
   point: nothing is hidden and nothing is corrupted. The action is reachable from the run page and from the
   registry's quick view.
5. **Quick view drawer** — clicking a registry row (or its panel button) slides in a right-hand drawer fed by the
   same `/runs/<id>/status.json` endpoint: status badge, the **full untruncated error** for a failed run, the four
   count KPIs with `new · upd · same`, source file and start/end times, plus **Rerun safely** and **Open full run**.
   It polls once a second while the run is still in flight, hides the rerun button until the run finishes, is
   keyboard-dismissible (`Esc`, focus returned to the row) and full-width below 760 px. All values coming from the
   database are escaped before insertion.
6. **Startup recovery** — `UiConfig.ready()` closes any `RUNNING` row left behind by a killed server process as
   `FAILED` with `RECOVERED: process ended before the run finished`, so the registry never shows a run that will
   never finish and a retry is unambiguous. The CLI equivalent is `python -m src.cli recover`.

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
python manage.py test webapp.ui     # 31 end-to-end UI tests (isolated temp DB per test)
python -m pytest -q                 # the 72 pipeline tests, unchanged
```

The UI tests exercise real pipeline runs on the bundled sample: sample run, uploads, idempotent
rerun, failed-run rollback, reference immutability, compare categories, downloads, filters and 404s.
Eight of them cover rerun safety and the registry specifically: the rerun endpoint reuses the stored input and
publishes nothing, a rerun of a failed run records the same error again (and it is visible on both the run page and
the registry), a missing stored file creates no run, the endpoint requires POST and a known run id,
stale `RUNNING` rows are recovered as `FAILED`, `status.json` carries the drawer summary, the registry exposes the
quick-view trigger and the rerun form, and a batch that fails before row counting still reaches the drawer with its
`SCHEMA_MISMATCH` error.

## Screenshots

[`docs/ui_screenshots/`](ui_screenshots/) — onboarding, run detail, reconciliation, compare,
dashboard, the target-table browser (narrow viewport; the layout is responsive), and the runs registry with the
quick-view drawer open on a failed run at desktop and mobile widths plus a successful run's count KPIs.

## Notes and limitations

* Local tool by design: no authentication. Do not expose it to a network without adding auth,
  a real secret key and `DEBUG=0`.
* Runs execute in-process (background thread). A server restart closes any `RUNNING` row left by the killed
  process as `FAILED` (see *Startup recovery* above), so the registry never shows a run that will never finish;
  the run page stops polling after ~30 minutes.
* Reference CSVs that embed a `reference_version` column must be loaded under that same name
  (the loader keeps only matching rows). The form help text says so; the sample button handles
  it by storing a relabelled copy as `sample_v1`.
* Reference CSVs must contain the five reference columns only — `product_code, description,
  status, effective_from, effective_to` — plus optional `reference_version`. Missing or extra
  columns are rejected with `REFERENCE_INVALID` before anything is stored.

Requirement-by-requirement evidence for the whole project (pipeline + console):
[REQUIREMENTS_COVERAGE.md](REQUIREMENTS_COVERAGE.md).