# Design decisions and trade-offs

The brief fixes the problem (versioned reference validation) but not the business rules; the choices below are
assumptions I made, applied consistently and covered by tests.

| # | Decision | Why / alternative rejected |
|---|---|---|
| 1 | **Explicit reference version per run** (`--reference-version`, required). | Silent "latest" selection makes results irreproducible. Alternative: pick by an effective-date rule – more magic, harder to audit. |
| 2 | **Immutable versions**, checksum recorded; identical reload is a no-op, different content under the same name is an error. | Preserves reproducibility of old runs. Fixing a snapshot means publishing a new version. |
| 3 | **Inclusive** window bounds, calendar dates, no time zone; blank `effective_to` = open-ended. | Simple to explain; boundary tests on both ends. A real system with timestamps would need an explicit time zone. |
| 4 | **Deprecated policy** `allow_historical` (default) / `reject`. Deprecated codes are valid for events inside their window (flagged), expired afterwards. | A deprecated code means "don't use for new business", not "history is wrong". `reject` is available for strict consumers. `INACTIVE` is always rejected. |
| 5 | **First failure wins**, fixed order. | Deterministic and cheap to vectorize; one reason per reject keeps reports simple. Cost: a row with two problems shows only one. |
| 6 | **Duplicate = same `record_id`; keep first in file order.** The check runs after field-level checks, so an invalid copy never shadows a later valid copy; but once a copy passes field checks it "owns" the key even if a later reference check rejects it. | Documented, deterministic. Alternative (reject all copies) loses data; "last wins" depends on file order semantics the source may not guarantee. |
| 7 | **Target = current-state upsert on `record_id`**, plus full history in `run_outcomes` + append-only `rejected_records`. | Gives idempotent reruns and an audit trail without an unbounded versioned target. A valid→rejected change after a refresh is *reported*, not auto-deleted from the target (deleting analytical data automatically is risky). |
| 8 | **Cross-run duplicates are not rejects**: same key + same content/version = `unchanged`; changed content or version = `updated`. | A rerun/retry must be safe; rejecting redelivered rows would make retries fail. |
| 9 | **Staging + one publish transaction** (rejects, outcomes, upsert, reconciliation, SUCCESS). Staging is separately committed. | SUCCESS can never be recorded unless reconciliation passed; a crash leaves target untouched; staged rows survive for diagnosis. |
| 10 | **Reconciliation inside the transaction**; failure → rollback. | Prevents publishing numbers that do not add up. |
| 11 | **Two engines**: readable pure/row-wise reference implementation (`validate_rowwise`) and a vectorized pandas engine, tested for equivalence on 2,000 generated rows under both policies. | The slow one is the spec and the honest benchmark baseline; the fast one is production. |
| 12 | **SQLite + CSV**, no framework/UI. | Zero setup; the brief explicitly allows a simple local target. Trade-off: single writer. |
| 13 | **Unexpected extra columns: warn and ignore** (strict flag to fail). Missing required column / empty file / bad encoding fail the batch. | Producers add columns routinely; losing a required one is silent corruption. |
| 14 | **Entity check is opt-in**. | Parent data may not be available in every environment; the integrity mechanism is demonstrated and tested. Footgun: the flag changes outcomes, so it is stored in `config_json`. |
| 15 | **Amounts as exact cents** (regex-parsed, no float arithmetic for control totals). | Avoids float drift in reconciliation. |
| 16 | **Malformed lines are quarantined, not fatal.** | One bad line should not block a batch; counts still reconcile. |
| 17 | **Late/out-of-order data**: outcomes depend only on `event_date` vs. the window, never on position or run time. Tested by reversing row order. | Matches the "effective date" requirement. |

## Failure handling matrix

| Failure | Result |
|---|---|
| zero-byte / header-less / non-UTF-8 / missing column | run `FAILED`, `SCHEMA_MISMATCH`, nothing published |
| unknown or invalid reference version/file | run `FAILED`, nothing published |
| crash after staging / before commit / reconciliation mismatch | publish rolled back, run `FAILED`, staging retained; retry is safe |
| process killed mid-run | run row stays `RUNNING` (limitation); target unaffected because publish is atomic |

## Not done (deliberately)
Dashboard/UI, orchestration (Airflow), Docker, distributed processing, per-rule multi-failure reporting, automatic
retraction of target rows when a newer version rejects them.
