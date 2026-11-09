# Requirements coverage

Point-by-point map of the challenge brief to the code that implements it and the test that proves it.
Verified on this machine on 2026-11-09: **72 pipeline tests + 31 Django UI tests = 103 passing**, zero failures.

The four supplied documents (the *72 Hours Code Challenge* brief, the *Submission Guidelines*, the *Candidate FAQ*
and `YottaFlex_72_Hour_Challenge_Implementation_Guide.docx`) were provided in chat and are the organiser's material,
so they are not committed here. The requirement wording below is the working restatement used throughout
[`README.md`](../README.md) and [`DESIGN_DECISIONS.md`](DESIGN_DECISIONS.md); every row is checkable in the repo
without them.

Status key: **Done** = implemented and tested · **Done (beyond brief)** = not required, added anyway ·
**Deviation** = deliberately handled differently, with the reason recorded.

## 1. Ingestion and schema handling

| Requirement | Implementation | Proof | Status |
|---|---|---|---|
| Read incoming records from CSV, keep raw values for audit | `src/ingestion.py` (raw strings retained, no coercion at read time) | `test_malformed_rows_quarantined_with_raw_fields_and_good_rows_still_load` | Done |
| One malformed line must not abort the batch | row-level quarantine, batch continues | same test; `test_missing_values_rejected_with_reason_and_raw_payload` | Done |
| Missing required column / empty file / non-UTF-8 → clear failure | `src/schema_validation.py`, `PipelineError("SCHEMA_MISMATCH")` | `test_missing_required_column_fails_batch`, `test_zero_byte_file_fails_run_with_clear_message`, `test_non_utf8_file_fails_cleanly` | Done |
| Header-only file is a successful no-op | `src/pipeline.py` | `test_empty_batch_header_only_is_successful_noop` | Done |
| Unexpected extra columns | warn + ignore by default, `--strict-columns` fails the batch | `test_unexpected_column_warns_by_default_and_fails_in_strict_mode` | Done |
| Changed value types / formats must not be silently coerced | text amounts and non-ISO dates are quarantined, never cast | `test_changed_column_types_are_detected_and_nothing_is_silently_corrupted`, `test_whole_file_with_changed_types_is_fully_quarantined_not_published` | Done |

## 2. Versioned reference data

| Requirement | Implementation | Proof | Status |
|---|---|---|---|
| Load and store reference snapshots as named versions | `src/reference_loader.py`, tables `reference_versions` / `reference_codes` | `test_load_valid_snapshot_and_open_ended_window` | Done |
| Versions are immutable; a refresh is a new version | SHA-256 checksum; same name + same content = no-op, same name + different content = `REFERENCE_IMMUTABLE` | `test_versions_are_immutable_and_old_rows_preserved`, `test_changing_reference_content_under_same_version_is_refused`, UI `test_immutability_enforced_in_form`, `test_same_content_reload_is_noop` | Done |
| Validate the reference file itself | status enum, ISO dates, `from <= to`, non-blank code, no duplicate/overlapping windows, exact column set | `test_invalid_reference_rows_rejected` (5 cases), `test_duplicate_and_overlapping_windows_rejected`, `test_missing_column_and_empty_file`, `test_extra_columns_rejected` | Done |
| A code may have several windows, with gaps | composite key `(reference_version, product_code, effective_from)` | `test_gap_between_windows_is_expired_and_second_window_is_valid` | Done |
| Open-ended window (blank `effective_to`) | treated as unbounded | `test_load_valid_snapshot_and_open_ended_window` | Done |
| A run uses exactly one version, chosen explicitly | required `--reference-version`; unknown version fails before any write | `test_run_against_unloaded_version_fails_without_side_effects`, `test_unknown_version_lookup_fails` | Done |
| Multi-version file filtered by its `reference_version` column | loader keeps matching rows only | `test_version_column_filters_rows` | Done |

## 3. Record validation rules

| Requirement | Implementation | Proof | Status |
|---|---|---|---|
| Unknown product code | `UNKNOWN_CODE` | `test_unknown_code`, `test_reference_rules_end_to_end` | Done |
| `INACTIVE` never valid | `INACTIVE_CODE` | `test_inactive_always_rejected` | Done |
| Deprecated codes | `allow_historical` (default, flagged `is_deprecated_code`) / `reject` (`DEPRECATED_CODE`) | `test_deprecated_policies`, `test_deprecated_policy_reject` | Done |
| Effective-date semantics, inclusive bounds | `event_date < from` → `CODE_NOT_YET_EFFECTIVE`; `> to` → `CODE_EXPIRED` | `test_effective_date_boundaries` (4 cases: day before, both bounds, day after) | Done |
| Late / out-of-order arrivals | outcome depends only on `event_date`, never on row position or run time | `test_row_order_does_not_change_outcome_for_late_arriving_data` | Done |
| Duplicates inside a batch | key = trimmed `record_id`, first copy wins, later copies → `DUPLICATE_RECORD` | `test_duplicate_in_batch_keep_first`, `test_first_copy_wins_even_if_first_copy_is_rejected_by_reference`, `test_invalid_first_copy_does_not_shadow_later_valid_copy` | Done |
| Missing / malformed id, code, date, amount | `MISSING_*`, `INVALID_DATE`, `INVALID_AMOUNT`; amounts parsed to exact cents, no float drift | `test_parse_iso_date` (6), `test_parse_amount` (9), `test_missing_values_rejected_with_reason_and_raw_payload` | Done |
| Referential integrity to a parent entity list | opt-in `ORPHAN_ENTITY` check, `entities` table | `test_orphan_entity_check`, UI `test_check_entities_without_loaded_entities`, `test_entities_load` | Done |
| Deterministic reason when a row has several problems | first failure wins, fixed documented order | `test_first_failure_wins_and_order[rowwise,vectorized]` | Deviation (only the first reason is stored; see §8) |

## 4. Transformation, quarantine, target

| Requirement | Implementation | Proof | Status |
|---|---|---|---|
| Transform valid rows (normalize, derive, hash) | `src/transformation.py` → derived `event_year_month`, `row_hash`, `amount_cents`, `is_deprecated_code`; lineage `run_id`, `reference_version`, `source_file`, `published_at` | `test_happy_path_published_exactly_once`, [`DATA_DICTIONARY.md`](DATA_DICTIONARY.md) | Done |
| Rejected rows kept, never dropped | append-only `rejected_records` with raw payload, reason, `run_id`, `source_file`, `reference_version` | `test_malformed_rows_quarantined_with_raw_fields_and_good_rows_still_load`; UI quarantine browser test | Done |
| Quarantine is append-only across refreshes | a v2 run never deletes the v1 rejects | `test_refresh_unknown_in_v1_valid_in_v2_keeps_v1_rejection` | Done |
| Target = current state, keyed, no duplicates | `validated_records` upsert on `record_id` | `test_rerun_same_input_creates_no_duplicates_but_new_audit_run` | Done |
| Full per-run history retained | `run_outcomes` (every input row's outcome per run) | reconciliation check "outcomes persisted = input" | Done |

## 5. Reruns, retries, refresh and backfill

| Requirement | Implementation | Proof | Status |
|---|---|---|---|
| Rerunning the same input must be safe (no duplicate target rows) | upsert compares `row_hash` + version → `inserted` / `updated` / `unchanged` | `test_rerun_same_input_creates_no_duplicates_but_new_audit_run`, `test_rerun_with_changed_content_updates_not_duplicates`, UI `test_idempotent_rerun_publishes_nothing`, `test_rerun_endpoint_reuses_input_and_publishes_nothing` | Done |
| A rerun is still auditable | new `pipeline_runs` + `run_outcomes` rows, zero target writes | same tests (`published_count = 0`, `unchanged_count = 22/28`) | Done |
| Partial failure must not corrupt the target; retry must work | staging in its own transaction; publish (rejects + outcomes + upsert + reconcile + status) in **one** transaction | `test_partial_failure_leaves_target_untouched_and_retry_does_not_duplicate[after_stage,before_commit]`, `test_failure_after_target_has_data_does_not_corrupt_it`, UI `test_failed_run_marks_failed_and_keeps_target` | Done |
| Reprocess after a reference refresh | same input, new `--reference-version`; old history preserved | `test_refresh_unknown_in_v1_valid_in_v2_keeps_v1_rejection`, UI `test_compare_v1_v2` | Done |
| Report what changed between two runs/versions | `src/compare.py`: `rejected_now_valid`, `valid_now_rejected`, `valid_now_deprecated`, `reason_changed`, changed codes, changed rows | `test_compare_v1_v2`, `test_compare_same_run_rejected`; frozen example [`sample_output/compare_v1_vs_v2.json`](sample_output/compare_v1_vs_v2.json) | Done |
| Backfill a date range | `--event-from/--event-to`, stored in `config_json`, skipped rows counted | `test_backfill_event_range_is_recorded_and_applied` | Done |
| Recover a run whose process was killed | `python -m src.cli recover`; the web console also recovers stale `RUNNING` rows automatically at server start (`webapp/ui/apps.py`) | `test_recover_marks_stuck_running_run_failed_and_retry_works`, UI `test_stale_running_rows_recovered_as_failed` | Done |
| Rerun from the console without re-uploading | `POST /runs/<id>/rerun/` reuses the stored input and recorded config; refuses in-flight runs and missing files; reachable from the run page and from the registry's quick-view drawer | UI `test_rerun_endpoint_reuses_input_and_publishes_nothing`, `test_rerun_of_failed_run_records_error_again`, `test_rerun_missing_source_does_not_create_run`, `test_rerun_requires_post_and_known_run`, `test_registry_offers_quick_view_and_rerun` | Done (beyond brief) |

## 6. Reconciliation, metrics and reports

| Requirement | Implementation | Proof | Status |
|---|---|---|---|
| Numbers must add up before a run can be called successful | `src/reconciliation.py`, **10 checks inside the publish transaction**; any failure → rollback + `FAILED` | `test_happy_path_published_exactly_once` (10/10), failure-injection tests; UI reconciliation tab | Done |
| Control total, not just row counts | exact-cents amount total staged = target | check "amount control total (cents), staged = target" | Done |
| No duplicate target keys; version consistency | checks "target duplicate keys" = 0 and "rows written by this run use this reference_version" = 0 | same | Done |
| Record run status, row counts, rejects and errors | `src/metrics.py` `RunTracker`: `RUNNING → SUCCESS/FAILED`, input/valid/rejected/duplicate/inserted/updated/unchanged/skipped counts, duration, `error_message`, warnings, full `config_json` | `test_reports_written`, `test_recover_marks_stuck_running_run_failed_and_retry_works`, UI `test_failed_run_marks_failed_and_keeps_target`, `test_rerun_of_failed_run_records_error_again` | Done |
| Errors visible to the operator, not only in a log | run-detail error banner + rose error line in the runs registry (full text on hover) + the registry quick-view drawer showing the untruncated error, counts and rerun action | UI `test_rerun_of_failed_run_records_error_again` (asserts `banner-error` and `cell-error`), `test_failed_run_error_reaches_registry_drawer`, `test_status_json_carries_drawer_summary` | Done |
| Machine-readable reports per run | `output/<run_id>/run_summary.json`, `reconciliation.json`, `rejects.csv` | `test_reports_written`, UI `test_downloads`; frozen copies in [`sample_output/`](sample_output/) | Done |
| Structured log | `output/pipeline.log`, one line per run start/finish with counts and duration | visible in [`TEST_EVIDENCE.txt`](TEST_EVIDENCE.txt) | Done |

## 7. Data, tests, performance and documentation

| Requirement | Implementation | Proof | Status |
|---|---|---|---|
| Small hand-inspectable sample covering every category | `data/sample/` — 48 rows, v1 + v2 references, entities | `test_sample_dataset_matches_expected_labels` | Done |
| Expected results for evaluation, never read by the pipeline | `data/sample/expected_results.csv` | `test_pipeline_input_never_contains_labels` | Done |
| Larger deterministic dataset | `scripts/generate_data.py --rows N --seed S` (byte-identical for identical args) | `test_generator_is_deterministic_and_seed_sensitive`, `test_5k_generated_dataset_matches_expected_labels` | Done |
| Automated tests for rules and edge cases | `tests/` — 72 tests, both engines, failure injection, boundary dates, labels | `python -m pytest -q` → 72 passed | Done |
| Two independent engines agreeing | pure row-wise reference engine vs vectorized pandas engine | `test_engines_agree_on_generated_data[allow_historical,reject]`, `test_rowwise_baseline_engine_gives_same_results` | Done (beyond brief) |
| Measure and improve performance | `scripts/benchmark.py --repeat 3`, aborts if engines disagree | [`PERFORMANCE.md`](PERFORMANCE.md): validation stage 2.8x faster, end-to-end 1.2x on the clean 3.11 venv; 100k rows in ~6.4 s | Done |
| README with setup and run commands | [`README.md`](../README.md) | every quick-start command re-run on this machine on 2026-11-09; the counts came back exactly as documented (see §9) | Done |
| Data dictionary (schemas, types, keys, grain, nullability) | [`DATA_DICTIONARY.md`](DATA_DICTIONARY.md) | matches `src/db.py` | Done |
| Design decisions / assumptions / trade-offs | [`DESIGN_DECISIONS.md`](DESIGN_DECISIONS.md) — 17 numbered decisions + failure matrix | reviewed against the code | Done |
| Known limitations stated, not hidden | README "Known limitations", `DESIGN_DECISIONS.md` "Not done (deliberately)" | see §8 | Done |
| Version control with meaningful history | GitHub repo `sodarsanvenkatasai/refpipe`, 9 commits from skeleton → pipeline → tests → generator → web console → docs → redesign | `git log --oneline` | Done |
| Runs locally with no services, credentials or network | stdlib SQLite + pandas + Django; fonts, CSS and charts served locally | `pip install -r requirements.txt` then the README commands | Done |
| AI usage disclosure + full prompt history | [`ai/AI_USAGE.md`](ai/AI_USAGE.md), [`ai/PROMPT_HISTORY.md`](ai/PROMPT_HISTORY.md) | every prompt quoted verbatim, in order, per tool | Done |

## 8. Deliberate deviations and what is not done

These are choices, not gaps; each is documented where a reviewer will look for it.

| Item | Position | Reason |
|---|---|---|
| Only the first failure per row is recorded | Deviation | deterministic, cheap to vectorize, one reason per reject keeps reports readable. All-failures reporting would need a per-rule matrix. |
| Target rows are not auto-retracted when a newer version rejects them | Deviation | automatically deleting analytical data is riskier than reporting the change; `compare` + `run_outcomes` make it visible. |
| Entity (parent) check is opt-in per run | Deviation | parent data is not always available; the flag is stored in `config_json` so outcomes stay reproducible. |
| SQLite, single writer | Limitation | zero setup, explicitly allowed for a local target. Runs are serialized by a process-wide lock in the web console. |
| Whole batch processed in memory | Limitation | fine to millions of rows; chunking is the next step for much larger files. |
| No authentication on the web console | Limitation | localhost tool by design; README and `WEB_UI.md` both say to add auth, a real secret key and `DEBUG=0` before exposing it. |
| No orchestrator, Docker or distributed processing | Not done | out of scope for a local 72-hour deliverable. |
| Edit / delete / undo of published rows or rejects in the UI | Not done, on purpose | the target is an idempotent upsert and the quarantine is append-only; a delete button would break the audit guarantees the brief asks for. Corrections happen by publishing a new reference version and rerunning. |

## 9. Re-verify everything yourself

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q                              # 72 passed
python manage.py migrate
python manage.py test webapp.ui                  # Ran 31 tests ... OK
python -m src.cli run --input data/sample/incoming_records.csv \
    --reference data/sample/reference_codes_v1.csv --reference-version v1 \
    --entities data/sample/entities.csv --run-id demo_v1
python -m src.cli run --input data/sample/incoming_records.csv \
    --reference data/sample/reference_codes_v2.csv --reference-version v2 --check-entities --run-id demo_v2
python -m src.cli compare --run-a demo_v1 --run-b demo_v2
python -m src.cli run --input data/sample/incoming_records.csv --reference-version v2 --check-entities
python scripts/benchmark.py --rows 100000 --seed 42 --repeat 3
python manage.py runserver 127.0.0.1:8000        # or double-click start.bat on Windows
```

Observed on this machine, 2026-11-09 (Python 3.14.6, pandas 3.1.1): v1 → 48 in / 22 valid / 26 rejected
(2 duplicates), all 10 reconciliation checks passed; v2 → 28 valid / 20 rejected, 6 inserted + 22 updated;
compare → 6 rejected-now-valid, 1 valid-now-deprecated, 1 reason-changed, 8 changed codes;
rerun → `inserted 0, updated 0, unchanged 28`, all 10 checks passed.
Frozen copies of exactly these outputs are in [`sample_output/`](sample_output/).

Observed in the running console (1440 px light/dark and 390 px, real Chromium): a registry row for a batch that
failed on `SCHEMA_MISMATCH` opens the quick-view drawer with the full error and the four count KPIs suppressed in
favour of "the batch failed before row counting — nothing was published"; submitting **Rerun safely** from the
drawer created a new run that failed again with the same error and left the target untouched; a successful run's
drawer showed 48 in / 28 valid / 20 rejected / 28 published with `6 new · 22 upd · 0 same`; a synthetic `RUNNING`
row inserted into `pipeline_runs` was closed as `FAILED` with `RECOVERED: process ended before the run finished` on
the next server start and appeared that way in the registry. Screenshots:
[`ui_screenshots/07_runs_quickview.png`](ui_screenshots/07_runs_quickview.png),
[`ui_screenshots/08_runs_quickview_mobile.png`](ui_screenshots/08_runs_quickview_mobile.png) and
[`ui_screenshots/09_runs_quickview_counts.png`](ui_screenshots/09_runs_quickview_counts.png).