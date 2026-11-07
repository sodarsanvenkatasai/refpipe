import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src.config import Config  # noqa: E402
from src.db import connect  # noqa: E402
from src.pipeline import run_pipeline  # noqa: E402

REF_HEADER = "reference_version,product_code,description,status,effective_from,effective_to\n"
IN_HEADER = "record_id,entity_id,product_code,event_date,amount\n"

# Small hand-written reference used by most tests
REF_V1 = REF_HEADER + "\n".join([
    "v1,PRD100,active product,ACTIVE,2026-01-01,2026-12-31",
    "v1,PRD200,deprecated product,DEPRECATED,2025-01-01,2026-03-31",
    "v1,PRD400,inactive product,INACTIVE,2025-01-01,2026-12-31",
]) + "\n"
REF_V2 = REF_HEADER + "\n".join([
    "v2,PRD100,active product,ACTIVE,2026-01-01,2027-12-31",
    "v2,PRD300,new product,ACTIVE,2026-06-01,2027-12-31",
]) + "\n"


class Env:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.cfg = Config(db_path=str(tmp_path / "t.db"), output_dir=str(tmp_path / "out"))
        self.conn = connect(self.cfg.db_path)

    def write(self, name: str, text: str) -> str:
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return str(p)

    def incoming(self, rows, name="in.csv", header=IN_HEADER) -> str:
        return self.write(name, header + "".join(r + "\n" for r in rows))

    def run(self, input_path, version="v1", reference=None, **kw):
        kw.setdefault("write_reports", False)
        return run_pipeline(self.conn, self.cfg, input_path, version, reference_path=reference, **kw)

    def q(self, sql, *args):
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def count(self, table, where="1=1", *args):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[0]


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    e.ref_v1 = e.write("ref_v1.csv", REF_V1)
    e.ref_v2 = e.write("ref_v2.csv", REF_V2)
    yield e
    e.conn.close()
