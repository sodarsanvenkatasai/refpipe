# Performance measurement

Command: `python scripts/benchmark.py --rows 100000 --seed 42 --repeat 3` (fastest of 3 runs each, fresh SQLite DB
per run, same generated file, reference v2, entity check on). The script aborts if the two engines produce
different counts, so both rows below validated the same 100,000 records.

Machine: Windows 11, 12 logical CPUs, Python 3.14.6, pandas 3.0.5, local SSD.

| engine | total s | rows/s | validate s | transform s | stage s | publish s |
|---|---|---|---|---|---|---|
| baseline: row-by-row SQL lookup | 27.85 | 3,590 | 8.53 | 2.57 | 2.74 | 12.59 |
| optimized: vectorized join | 7.71 | 12,965 | 0.59 | 0.82 | 1.28 | 4.57 |

* **End-to-end speed-up 3.6x; validation stage 14.4x.**
* **Optimization:** load the chosen reference snapshot once into memory and validate the whole batch with a
  vectorized `merge` (one join per batch) instead of one `SELECT` per row inside a Python loop
  (`--engine rowwise` keeps the old behaviour for comparison and as the executable specification).
* Other measures already in the design: composite primary key / index on `(reference_version, product_code[, effective_from])`
  (it is what makes even the baseline lookups cheap), batch `executemany` inserts in a single transaction, one connection per run.
* **Caveats / honesty:** the transform/stage/publish stages run the same code in both engines, so the differences in
  those columns (notably `publish` 12.6 s vs 4.6 s) are measurement noise from disk/OS activity, not an effect of the
  engine; the validation-stage column is the clean comparison. After the optimization the remaining time is mostly
  SQLite writes (staging, outcomes, rejects) - the next candidates would be `PRAGMA synchronous=NORMAL`, skipping
  per-row JSON for rejects, or a columnar target (Parquet) at the cost of SQL upserts.
* CLI measurement of a normal run on the same 100k file (README step 7): 6.6 s, ~15k rows/s; rerun of the same input: 6.9 s with 0 rows written.

## Second measurement (clean Python 3.11 venv, pandas 3.1.1, Windows 11, `--repeat 3`)

| engine | total s | rows/s | validate s | transform s | stage s | publish s |
|---|---|---|---|---|---|---|
| baseline: row-by-row SQL lookup | 7.71 | 12,973 | 2.21 | 0.69 | 1.01 | 3.34 |
| optimized: vectorized join | 6.37 | 15,702 | 0.79 | 0.72 | 1.02 | 3.36 |

End-to-end 1.2x, validation stage 2.8x. This run is less noisy than the first table (transform/stage/publish are
now equal across engines, as expected); the vectorized join's gain is smaller than first reported but the
direction and the validation-stage win hold. Prefer these numbers; results vary by machine.
