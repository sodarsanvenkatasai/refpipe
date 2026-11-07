"""Benchmark the baseline (row-by-row SQL lookup) against the optimized (vectorized join) engine.

    python scripts/benchmark.py --rows 100000 --seed 42

Both engines process the SAME generated file on a fresh database each, and must produce identical
counts (otherwise the benchmark aborts). Results are printed as a markdown table.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import generate_data  # noqa: E402
import pandas as pd  # noqa: E402
from src.config import Config  # noqa: E402
from src.db import connect  # noqa: E402
from src.pipeline import run_pipeline  # noqa: E402
from src.reference_loader import load_entities  # noqa: E402


def run_once(engine: str, data_dir: Path, tmp: Path, repeat: int):
    best = None
    for i in range(repeat):
        db = tmp / f"{engine}_{i}.db"
        cfg = Config(db_path=str(db), output_dir=str(tmp / "out"), engine=engine)
        conn = connect(cfg.db_path)
        load_entities(conn, str(data_dir / "entities.csv"))
        s = run_pipeline(conn, cfg, str(data_dir / "incoming_records.csv"), "v2",
                         reference_path=str(data_dir / "reference_codes_v2.csv"), check_entities=True,
                         write_reports=False)
        conn.close()
        if best is None or s["duration_seconds"] < best["duration_seconds"]:
            best = s
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=100_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--repeat", type=int, default=3, help="runs per engine; the fastest is reported")
    ap.add_argument("--json-out")
    a = ap.parse_args()

    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        data = tmp / "data"
        generate_data.generate(a.rows, a.seed, str(data))
        results = {e: run_once(e, data, tmp, a.repeat) for e in ("rowwise", "vectorized")}

    keys = ("input_count", "valid_count", "rejected_count", "duplicate_count", "rejects_by_reason")
    base, opt = results["rowwise"], results["vectorized"]
    assert {k: base[k] for k in keys} == {k: opt[k] for k in keys}, "engines disagree - benchmark invalid"

    print(f"machine: {platform.platform()} | cpu_count={os.cpu_count()} | python {platform.python_version()} | "
          f"pandas {pd.__version__}")
    print(f"rows={a.rows} seed={a.seed} repeat={a.repeat} (fastest run reported)\n")
    print("| engine | total s | rows/s | validate s | transform s | stage s | publish s |")
    print("|---|---|---|---|---|---|---|")
    for name, s in (("baseline: row-by-row SQL lookup", base), ("optimized: vectorized join", opt)):
        st = s["stage_seconds"]
        print(f"| {name} | {s['duration_seconds']:.2f} | {s['rows_per_second']:,.0f} | {st['validate']:.2f} | "
              f"{st['transform']:.2f} | {st['stage']:.2f} | {st['publish']:.2f} |")
    print(f"\nend-to-end speed-up: {base['duration_seconds'] / opt['duration_seconds']:.1f}x; "
          f"validation-stage speed-up: {base['stage_seconds']['validate'] / opt['stage_seconds']['validate']:.1f}x")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
