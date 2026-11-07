"""Generated data vs the separate expected_results.csv labels (evaluation only)."""
import hashlib
from pathlib import Path

import pandas as pd
import pytest

import generate_data
from src.compare import compare_runs

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample"


def _check(env, folder, rows_expected):
    env.cfg.output_dir = str(env.tmp / "out")
    exp = pd.read_csv(Path(folder) / "expected_results.csv", dtype=str, keep_default_na=False)
    summaries = {}
    for v in ("v1", "v2"):
        s = env.run(str(Path(folder) / "incoming_records.csv"), version=v,
                    reference=str(Path(folder) / f"reference_codes_v{v[1]}.csv"),
                    check_entities=True)
        summaries[v] = s
        out = pd.DataFrame(env.q("SELECT row_num, outcome, COALESCE(reason,'') AS reason FROM run_outcomes WHERE run_id=?",
                                 s["run_id"]))
        out["row_num"] = out["row_num"].astype(str)
        m = exp.merge(out, on="row_num", how="left")
        bad = m[(m[f"expected_{v}_outcome"] != m["outcome"]) | (m[f"expected_{v}_reason"] != m["reason"])]
        assert bad.empty, f"{v}: {len(bad)} rows differ from expected labels:\n{bad.head(10).to_string()}"
        assert len(out) == rows_expected
    return summaries


def _load_entities(env, folder):
    from src.reference_loader import load_entities
    load_entities(env.conn, str(Path(folder) / "entities.csv"))


def test_sample_dataset_matches_expected_labels(env):
    _load_entities(env, SAMPLE)
    s = _check(env, SAMPLE, rows_expected=48)
    # v2 refresh: some v1 rejects become valid, nothing valid in v1 becomes rejected
    cmp = compare_runs(env.conn, s["v1"]["run_id"], s["v2"]["run_id"])
    assert cmp["categories"].get("rejected_now_valid", 0) > 0
    assert "valid_now_rejected" not in cmp["categories"]
    assert cmp["categories"].get("valid_now_deprecated", 0) > 0


def test_5k_generated_dataset_matches_expected_labels(env, tmp_path):
    folder = tmp_path / "gen"
    generate_data.generate(5000, 42, str(folder))
    _load_entities(env, folder)
    s = _check(env, folder, rows_expected=5000)
    for v in ("v1", "v2"):
        assert all(c["passed"] for c in s[v]["reconciliation"])
        assert s[v]["input_count"] == s[v]["valid_count"] + s[v]["rejected_count"]
    # current-state target holds each valid record_id exactly once: union of v1-valid and v2-valid keys
    ids = env.q("SELECT COUNT(DISTINCT record_id) AS n FROM run_outcomes WHERE outcome='VALID'")[0]["n"]
    assert env.count("validated_records") == ids


def test_generator_is_deterministic_and_seed_sensitive(tmp_path):
    def digest(seed, d):
        generate_data.generate(500, seed, str(tmp_path / d))
        return hashlib.sha256((tmp_path / d / "incoming_records.csv").read_bytes()).hexdigest()
    assert digest(1, "a") == digest(1, "b")
    assert digest(1, "a") != digest(2, "c")


def test_pipeline_input_never_contains_labels(tmp_path):
    generate_data.generate(200, 1, str(tmp_path))
    header = (tmp_path / "incoming_records.csv").read_text().splitlines()[0]
    assert header == "record_id,entity_id,product_code,event_date,amount"
