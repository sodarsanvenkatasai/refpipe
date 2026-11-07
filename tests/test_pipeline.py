"""End-to-end behaviour: run lifecycle, rejects, idempotency, refresh, failure and retry."""
import json

import pytest

from conftest import IN_HEADER
from src.compare import compare_runs
from src.pipeline import InjectedFailure
from src.reasons import PipelineError


def rejects(env, run_id):
    return {r["source_record_id"]: r["rejection_reason"]
            for r in env.q("SELECT * FROM rejected_records WHERE run_id=?", run_id)}


# ------------------------------------------------------------------ happy path
def test_happy_path_published_exactly_once(env):
    s = env.run(env.incoming(["R1,E1,PRD100,2026-05-01,10.50"]), reference=env.ref_v1)
    assert (s["input_count"], s["valid_count"], s["rejected_count"], s["published_count"]) == (1, 1, 0, 1)
    row = env.q("SELECT * FROM validated_records")[0]
    assert row["record_id"] == "R1" and row["reference_version"] == "v1" and row["run_id"] == s["run_id"]
    assert row["amount_cents"] == 1050 and row["event_year_month"] == "2026-05"
    run = env.q("SELECT * FROM pipeline_runs")[0]
    assert run["status"] == "SUCCESS" and run["ended_at"] and run["error_message"] is None
    assert all(c["passed"] for c in s["reconciliation"])


# ------------------------------------------------------------------ empty / malformed input
def test_empty_batch_header_only_is_successful_noop(env):
    s = env.run(env.incoming([]), reference=env.ref_v1)
    assert (s["input_count"], s["valid_count"], s["rejected_count"], s["published_count"]) == (0, 0, 0, 0)
    assert env.count("validated_records") == 0
    assert env.q("SELECT status FROM pipeline_runs")[0]["status"] == "SUCCESS"


def test_zero_byte_file_fails_run_with_clear_message(env):
    with pytest.raises(PipelineError, match="empty"):
        env.run(env.write("e.csv", ""), reference=env.ref_v1)
    run = env.q("SELECT * FROM pipeline_runs")[0]
    assert run["status"] == "FAILED" and "SCHEMA_MISMATCH" in run["error_message"]


def test_missing_required_column_fails_batch(env):
    p = env.incoming(["R1,E1,PRD100,2026-05-01"], header="record_id,entity_id,product_code,event_date\n")
    with pytest.raises(PipelineError, match="missing required column"):
        env.run(p, reference=env.ref_v1)
    assert env.count("validated_records") == 0
    assert env.q("SELECT status FROM pipeline_runs")[0]["status"] == "FAILED"


def test_unexpected_column_warns_by_default_and_fails_in_strict_mode(env):
    p = env.incoming(["R1,E1,PRD100,2026-05-01,1.00,surprise"], header=IN_HEADER.strip() + ",new_col\n")
    s = env.run(p, reference=env.ref_v1)
    assert s["valid_count"] == 1 and any("new_col" in w for w in s["warnings"])
    env.cfg.strict_columns = True
    with pytest.raises(PipelineError, match="strict"):
        env.run(p, reference=env.ref_v1)


def test_non_utf8_file_fails_cleanly(env):
    path = env.tmp / "bin.csv"
    path.write_bytes(b"\xff\xfe\x00\x01 not utf8 \x80\x81")
    with pytest.raises(PipelineError):
        env.run(str(path), reference=env.ref_v1)
    assert env.q("SELECT status FROM pipeline_runs")[0]["status"] == "FAILED"


def test_malformed_rows_quarantined_with_raw_fields_and_good_rows_still_load(env):
    p = env.incoming(["R1,E1,PRD100,2026-05-01,1.00", "R2,E1,PRD100", "R3,E1,PRD100,2026-05-01,1,extra",
                      "", "R4,E1,PRD100,2026-05-02,2.00"])
    s = env.run(p, reference=env.ref_v1)
    assert (s["input_count"], s["valid_count"], s["rejected_count"]) == (4, 2, 2)  # blank line is not a row
    rej = env.q("SELECT * FROM rejected_records ORDER BY row_num")
    assert [r["rejection_reason"] for r in rej] == ["MALFORMED_ROW"] * 2
    assert json.loads(rej[0]["raw_payload"])["raw_fields"] == ["R2", "E1", "PRD100"]
    assert [r["source_record_id"] for r in rej] == ["row:2", "row:3"]


# ------------------------------------------------------------------ row-level rejects keep lineage
def test_missing_values_rejected_with_reason_and_raw_payload(env):
    p = env.incoming(["R1,E1,,2026-05-01,1", ",E1,PRD100,2026-05-01,1", "R3,E1,PRD100,,1", "R4,E1,PRD100,nope,1",
                      "R5,E1,PRD100,2026-05-01,abc"])
    s = env.run(p, reference=env.ref_v1)
    assert s["valid_count"] == 0 and s["rejected_count"] == 5
    rows = env.q("SELECT * FROM rejected_records ORDER BY row_num")
    assert [r["rejection_reason"] for r in rows] == ["MISSING_CODE", "MISSING_RECORD_ID", "MISSING_EVENT_DATE",
                                                     "INVALID_DATE", "INVALID_AMOUNT"]
    for r in rows:
        assert r["run_id"] == s["run_id"] and r["source_file"].endswith("in.csv") and r["reference_version"] == "v1"
    assert json.loads(rows[0]["raw_payload"])["record_id"] == "R1"        # original payload preserved
    assert rows[1]["source_record_id"] == "row:2"                          # no id -> traceable by row


def test_reference_rules_end_to_end(env):
    p = env.incoming([
        "U,E1,NOPE,2026-05-01,1",           # unknown
        "D1,E1,PRD200,2026-03-31,1",        # deprecated, in window (boundary) -> accepted
        "D2,E1,PRD200,2026-04-01,1",        # deprecated, after window -> expired
        "I,E1,PRD400,2026-05-01,1",         # inactive
        "N,E1,PRD100,2025-12-31,1",         # not yet effective
        "X,E1,PRD100,2027-01-01,1",         # expired
        "B1,E1,PRD100,2026-01-01,1",        # boundary from
        "B2,E1,prd100,2026-12-31,1",        # boundary to (+ normalization)
    ])
    s = env.run(p, reference=env.ref_v1)
    assert rejects(env, s["run_id"]) == {"U": "UNKNOWN_CODE", "D2": "CODE_EXPIRED", "I": "INACTIVE_CODE",
                                         "N": "CODE_NOT_YET_EFFECTIVE", "X": "CODE_EXPIRED"}
    pub = {r["record_id"]: r["is_deprecated_code"] for r in env.q("SELECT * FROM validated_records")}
    assert pub == {"D1": 1, "B1": 0, "B2": 0}   # deprecated historical event accepted and flagged


def test_deprecated_policy_reject(env):
    env.cfg.deprecated_policy = "reject"
    s = env.run(env.incoming(["D1,E1,PRD200,2026-01-15,1"]), reference=env.ref_v1)
    assert rejects(env, s["run_id"]) == {"D1": "DEPRECATED_CODE"}


def test_orphan_entity_check(env):
    env.write("ent.csv", "entity_id\nE1\n")
    from src.reference_loader import load_entities
    load_entities(env.conn, str(env.tmp / "ent.csv"))
    s = env.run(env.incoming(["R1,E1,PRD100,2026-05-01,1", "R2,E2,PRD100,2026-05-01,1", "R3,,PRD100,2026-05-01,1"]),
                reference=env.ref_v1, check_entities=True)
    assert rejects(env, s["run_id"]) == {"R2": "ORPHAN_ENTITY"}   # blank entity is nullable


# ------------------------------------------------------------------ duplicates and idempotency
def test_duplicate_in_batch_keep_first(env):
    p = env.incoming(["R1,E1,PRD100,2026-05-01,10.00", "R1,E1,PRD100,2026-05-02,99.00", "R1,E1,PRD100,2026-05-01,10.00"])
    s = env.run(p, reference=env.ref_v1)
    assert (s["valid_count"], s["rejected_count"], s["duplicate_count"]) == (1, 2, 2)
    assert env.count("validated_records") == 1
    assert env.q("SELECT amount_cents FROM validated_records")[0]["amount_cents"] == 1000   # first copy kept
    assert s["input_count"] == s["valid_count"] + s["rejected_count"]


def test_rerun_same_input_creates_no_duplicates_but_new_audit_run(env):
    p = env.incoming(["R1,E1,PRD100,2026-05-01,1", "R2,E1,PRD100,2026-05-02,2", "bad,E1,NOPE,2026-05-02,2"])
    first = env.run(p, reference=env.ref_v1)
    second = env.run(p)
    assert (first["inserted_count"], second["inserted_count"], second["unchanged_count"]) == (2, 0, 2)
    assert second["published_count"] == 0
    assert env.count("validated_records") == 2
    assert env.count("pipeline_runs") == 2 and first["run_id"] != second["run_id"]
    assert env.count("rejected_records") == 2        # rejection history retained for both runs


def test_rerun_with_changed_content_updates_not_duplicates(env):
    env.run(env.incoming(["R1,E1,PRD100,2026-05-01,1.00"]), reference=env.ref_v1)
    s = env.run(env.incoming(["R1,E1,PRD100,2026-05-01,2.00"], name="in2.csv"))
    assert (s["updated_count"], s["inserted_count"]) == (1, 0)
    assert env.count("validated_records") == 1
    assert env.q("SELECT amount_cents FROM validated_records")[0]["amount_cents"] == 200


def test_row_order_does_not_change_outcome_for_late_arriving_data(env):
    rows = ["R1,E1,PRD100,2026-12-31,1", "R2,E1,PRD100,2026-01-01,1", "R3,E1,PRD100,2026-06-01,1"]
    a = env.run(env.incoming(rows, name="a.csv"), reference=env.ref_v1)
    b = env.run(env.incoming(rows[::-1], name="b.csv"))
    assert a["valid_count"] == b["valid_count"] == 3 and b["unchanged_count"] == 3


# ------------------------------------------------------------------ reference refresh
def test_refresh_unknown_in_v1_valid_in_v2_keeps_v1_rejection(env):
    p = env.incoming(["R1,E1,PRD100,2026-07-01,1", "R2,E1,PRD300,2026-07-01,1", "R3,E1,PRD300,2026-01-01,1"])
    r1 = env.run(p, version="v1", reference=env.ref_v1)
    assert rejects(env, r1["run_id"]) == {"R2": "UNKNOWN_CODE", "R3": "UNKNOWN_CODE"}
    r2 = env.run(p, version="v2", reference=env.ref_v2)
    assert rejects(env, r2["run_id"]) == {"R3": "CODE_NOT_YET_EFFECTIVE"}   # PRD300 starts 2026-06-01
    assert rejects(env, r1["run_id"]) == {"R2": "UNKNOWN_CODE", "R3": "UNKNOWN_CODE"}   # v1 history intact
    assert {r["record_id"]: r["reference_version"] for r in env.q("SELECT * FROM validated_records")} == \
        {"R1": "v2", "R2": "v2"}   # R1 re-validated under v2 (current-state target)
    assert env.count("validated_records") == 2
    # every run records exactly one reference version
    assert {r["run_id"]: r["reference_version"] for r in env.q("SELECT * FROM pipeline_runs")} == \
        {r1["run_id"]: "v1", r2["run_id"]: "v2"}

    cmp = compare_runs(env.conn, r1["run_id"], r2["run_id"])
    assert cmp["categories"] == {"rejected_now_valid": 1, "reason_changed": 1, "unchanged_valid": 1}
    assert cmp["changed_codes"] == {"PRD300": 2}
    assert {"from": "REJECTED:UNKNOWN_CODE", "to": "VALID:", "records": 1} in cmp["transitions"]


def test_run_against_unloaded_version_fails_without_side_effects(env):
    with pytest.raises(PipelineError, match="not loaded"):
        env.run(env.incoming(["R1,E1,PRD100,2026-05-01,1"]), version="v7")
    assert env.count("validated_records") == 0
    assert env.q("SELECT status FROM pipeline_runs")[0]["status"] == "FAILED"


def test_changing_reference_content_under_same_version_is_refused(env):
    p = env.incoming(["R1,E1,PRD100,2026-05-01,1"])
    env.run(p, reference=env.ref_v1)
    with pytest.raises(PipelineError, match="immutable"):
        env.run(p, version="v1", reference=env.ref_v2.replace("v2", "v2"))   # v2 file content under name v1
    assert env.count("reference_codes", "reference_version='v1'") == 3


# ------------------------------------------------------------------ partial failure and retry
@pytest.mark.parametrize("point", ["after_stage", "before_commit"])
def test_partial_failure_leaves_target_untouched_and_retry_does_not_duplicate(env, point):
    p = env.incoming(["R1,E1,PRD100,2026-05-01,1", "R2,E1,PRD100,2026-05-02,2", "X,E1,NOPE,2026-05-02,2"])
    with pytest.raises(InjectedFailure):
        env.run(p, reference=env.ref_v1, fail_at=point)
    run = env.q("SELECT * FROM pipeline_runs")[0]
    assert run["status"] == "FAILED" and "injected failure" in run["error_message"]
    assert env.count("validated_records") == 0                  # nothing published
    assert env.count("rejected_records") == 0                   # publish transaction rolled back as a unit
    assert env.count("run_outcomes") == 0
    s = env.run(p)                                              # retry
    assert (s["published_count"], s["inserted_count"]) == (2, 2)
    assert env.count("validated_records") == 2
    assert env.count("stage_valid_records") == 0                # staging cleaned (failed run's too)
    assert [r["status"] for r in env.q("SELECT status FROM pipeline_runs ORDER BY started_at")] == ["FAILED", "SUCCESS"]
    s2 = env.run(p)                                             # and a further rerun is still a no-op
    assert env.count("validated_records") == 2 and s2["unchanged_count"] == 2


def test_failure_after_target_has_data_does_not_corrupt_it(env):
    env.run(env.incoming(["R1,E1,PRD100,2026-05-01,1"]), reference=env.ref_v1)
    with pytest.raises(InjectedFailure):
        env.run(env.incoming(["R1,E1,PRD100,2026-05-01,9", "R2,E1,PRD100,2026-05-01,9"], name="b.csv"),
                fail_at="before_commit")
    assert env.q("SELECT record_id, amount_cents FROM validated_records") == [{"record_id": "R1", "amount_cents": 100}]


# ------------------------------------------------------------------ backfill window
def test_backfill_event_range_is_recorded_and_applied(env):
    p = env.incoming(["R1,E1,PRD100,2026-02-01,1", "R2,E1,PRD100,2026-06-01,1", "R3,E1,PRD100,2026-09-01,1",
                      "R4,E1,PRD100,not-a-date,1"])
    s = env.run(p, reference=env.ref_v1, event_from="2026-05-01", event_to="2026-07-31")
    assert (s["input_count"], s["valid_count"], s["skipped_out_of_range"]) == (2, 1, 2)
    assert rejects(env, s["run_id"]) == {"R4": "INVALID_DATE"}   # unparseable dates are not silently skipped
    cfg = json.loads(env.q("SELECT config_json FROM pipeline_runs")[0]["config_json"])
    assert (cfg["event_from"], cfg["event_to"]) == ("2026-05-01", "2026-07-31")
    with pytest.raises(PipelineError):
        env.run(p, event_from="05/01/2026")


# ------------------------------------------------------------------ engines + reports
def test_rowwise_baseline_engine_gives_same_results(env):
    rows = ["R1,E1,PRD100,2026-05-01,1", "R2,E1,PRD200,2026-01-01,1", "R3,E1,NOPE,2026-05-01,1",
            "R1,E1,PRD100,2026-05-01,1", "R4,E1,PRD400,2026-05-01,1", "R5,E1,PRD100,2027-05-01,1"]
    a = env.run(env.incoming(rows), reference=env.ref_v1)
    env.cfg.engine = "rowwise"
    b = env.run(env.incoming(rows, name="in_b.csv"))
    keys = ("input_count", "valid_count", "rejected_count", "duplicate_count", "rejects_by_reason")
    assert {k: a[k] for k in keys} == {k: b[k] for k in keys}


def test_reports_written(env):
    s = env.run(env.incoming(["R1,E1,PRD100,2026-05-01,1", "R2,E1,NOPE,2026-05-01,1"]), reference=env.ref_v1,
                write_reports=True)
    out = env.tmp / "out" / s["run_id"]
    assert json.loads((out / "run_summary.json").read_text())["rejects_by_reason"] == {"UNKNOWN_CODE": 1}
    assert "UNKNOWN_CODE" in (out / "rejects.csv").read_text()
    assert all(c["passed"] for c in json.loads((out / "reconciliation.json").read_text()))


# ------------------------------------------------------------------ unexpected schema change (changed types)
def test_changed_column_types_are_detected_and_nothing_is_silently_corrupted(env):
    """Same columns, but the producer changed formats: amount became text, dates became DD/MM/YYYY."""
    p = env.incoming(["R1,E1,PRD100,2026-05-01,10.00",      # still fine
                      "R2,E1,PRD100,2026-05-01,ten",         # amount type changed
                      "R3,E1,PRD100,01/05/2026,10.00"])      # date format changed
    s = env.run(p, reference=env.ref_v1)
    assert rejects(env, s["run_id"]) == {"R2": "INVALID_AMOUNT", "R3": "INVALID_DATE"}
    assert [r["record_id"] for r in env.q("SELECT record_id FROM validated_records")] == ["R1"]
    assert (s["input_count"], s["valid_count"], s["rejected_count"]) == (3, 1, 2)
    assert all(c["passed"] for c in s["reconciliation"])


def test_whole_file_with_changed_types_is_fully_quarantined_not_published(env):
    s = env.run(env.incoming(["R1,E1,PRD100,05/01/2026,abc", "R2,E1,PRD100,05/02/2026,xyz"]), reference=env.ref_v1)
    assert (s["valid_count"], s["rejected_count"], s["published_count"]) == (0, 2, 0)
    assert env.count("validated_records") == 0 and env.count("rejected_records") == 2


# ------------------------------------------------------------------ crash recovery
def test_recover_marks_stuck_running_run_failed_and_retry_works(env):
    from src.metrics import recover_stale_runs
    env.conn.execute("INSERT INTO pipeline_runs(run_id, started_at, status, source_file, reference_version)"
                     " VALUES ('stuck','2026-01-01T00:00:00Z','RUNNING','x.csv','v1')")
    env.conn.commit()
    assert recover_stale_runs(env.conn) == ["stuck"]
    row = env.q("SELECT status, error_message FROM pipeline_runs WHERE run_id='stuck'")[0]
    assert row["status"] == "FAILED" and "RECOVERED" in row["error_message"]
    s = env.run(env.incoming(["R1,E1,PRD100,2026-05-01,1.00"]), reference=env.ref_v1)
    assert s["published_count"] == 1
