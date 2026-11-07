"""Reference snapshot loading, validation and immutability."""
import pytest

from conftest import REF_HEADER
from src.reasons import PipelineError
from src.reference_loader import get_reference, read_reference_file, store_reference


def ref_file(env, *rows, name="r.csv"):
    return env.write(name, REF_HEADER + "".join(r + "\n" for r in rows))


def test_load_valid_snapshot_and_open_ended_window(env):
    p = ref_file(env, "v9,A1,d,ACTIVE,2026-01-01,", "v9,B1,d,deprecated,2026-01-01,2026-02-01")
    assert store_reference(env.conn, "v9", p) == "loaded"
    df = get_reference(env.conn, "v9").sort_values("product_code")
    assert list(df["effective_to"]) == ["9999-12-31", "2026-02-01"]
    assert list(df["status"]) == ["ACTIVE", "DEPRECATED"]  # status normalized


@pytest.mark.parametrize("row,fragment", [
    ("v9,A1,d,BOGUS,2026-01-01,2026-12-31", "invalid status"),
    ("v9,A1,d,ACTIVE,2026-13-01,2026-12-31", "invalid effective_from"),
    ("v9,A1,d,ACTIVE,2026-01-01,garbage", "invalid effective_to"),
    ("v9,A1,d,ACTIVE,2026-12-31,2026-01-01", "after effective_to"),
    ("v9,,d,ACTIVE,2026-01-01,2026-12-31", "blank product_code"),
])
def test_invalid_reference_rows_rejected(env, row, fragment):
    with pytest.raises(PipelineError, match=fragment):
        read_reference_file(ref_file(env, row), "v9")


def test_duplicate_and_overlapping_windows_rejected(env):
    with pytest.raises(PipelineError, match="duplicate"):
        read_reference_file(ref_file(env, "v9,A1,d,ACTIVE,2026-01-01,2026-06-30",
                                     "v9,A1,d,ACTIVE,2026-01-01,2026-12-31"), "v9")
    with pytest.raises(PipelineError, match="overlapping"):
        read_reference_file(ref_file(env, "v9,A1,d,ACTIVE,2026-01-01,2026-06-30",
                                     "v9,A1,d,ACTIVE,2026-06-30,2026-12-31"), "v9")


def test_missing_column_and_empty_file(env):
    with pytest.raises(PipelineError, match="missing reference column"):
        read_reference_file(env.write("x.csv", "product_code,status\nA,ACTIVE\n"), "v9")
    with pytest.raises(PipelineError, match="no data rows"):
        read_reference_file(env.write("y.csv", REF_HEADER), "v9")


def test_extra_columns_rejected(env):
    header = REF_HEADER.rstrip("\n") + ",notes\n"
    with pytest.raises(PipelineError, match="unexpected reference column"):
        read_reference_file(env.write("extra.csv", header + "v9,A1,d,ACTIVE,2026-01-01,,x\n"), "v9")


def test_version_column_filters_rows(env):
    p = ref_file(env, "v1,A1,d,ACTIVE,2026-01-01,", "v2,B2,d,ACTIVE,2026-01-01,")
    assert list(read_reference_file(p, "v2")["product_code"]) == ["B2"]
    with pytest.raises(PipelineError, match="no reference rows"):
        read_reference_file(p, "v3")


def test_versions_are_immutable_and_old_rows_preserved(env):
    store_reference(env.conn, "v1", env.ref_v1)
    store_reference(env.conn, "v2", env.ref_v2)
    assert store_reference(env.conn, "v1", env.ref_v1) == "already_loaded"   # same checksum: no-op
    changed = ref_file(env, "v1,PRD100,d,ACTIVE,2026-01-01,2030-12-31", name="changed.csv")
    with pytest.raises(PipelineError, match="immutable"):
        store_reference(env.conn, "v1", changed)
    # v1 untouched by loading v2 or by the refused overwrite
    assert env.count("reference_codes", "reference_version='v1'") == 3
    assert env.count("reference_codes", "reference_version='v2'") == 2


def test_unknown_version_lookup_fails(env):
    with pytest.raises(PipelineError, match="not loaded"):
        get_reference(env.conn, "v404")
