"""Read the incoming CSV safely, preserving the original raw values for lineage."""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .reasons import REQUIRED_COLUMNS, SCHEMA_MISMATCH, PipelineError
from .schema_validation import check_header

COLUMNS = REQUIRED_COLUMNS


@dataclass
class Batch:
    path: str
    checksum: str
    header: list[str]
    data: pd.DataFrame                      # well-formed rows: row_num + REQUIRED_COLUMNS (+ extras), all str
    malformed: list[tuple[int, list[str]]]  # (row_num, raw fields) for rows with wrong field count
    warnings: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return len(self.data) + len(self.malformed)


def file_checksum(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_batch(path: str, strict_columns: bool = False) -> Batch:
    """Parse the file. Raises PipelineError(SCHEMA_MISMATCH) for file-level problems.

    row_num is the 1-based index of non-blank data rows (the header is not counted).
    Rows whose field count differs from the header are returned in `malformed`, not dropped.
    """
    p = Path(path)
    if not p.is_file():
        raise PipelineError(SCHEMA_MISMATCH, f"input file not found: {path}")
    checksum = file_checksum(path)
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh, strict=True)
            try:
                header = next(reader)
            except StopIteration:
                raise PipelineError(SCHEMA_MISMATCH, "file is empty (no header row)")
            warnings = check_header(header, strict_columns)
            header = [h.strip() for h in header]
            width = len(header)
            rows: list[list[str]] = []
            malformed: list[tuple[int, list[str]]] = []
            n = 0
            for fields in reader:
                if not fields:  # blank line
                    continue
                n += 1
                if len(fields) != width:
                    malformed.append((n, fields))
                else:
                    rows.append(fields)
                    rows[-1].insert(0, str(n))
    except UnicodeDecodeError as exc:
        raise PipelineError(SCHEMA_MISMATCH, f"file is not valid UTF-8: {exc}")
    except csv.Error as exc:
        raise PipelineError(SCHEMA_MISMATCH, f"unreadable CSV: {exc}")

    df = pd.DataFrame(rows, columns=["row_num"] + header, dtype="object")
    df["row_num"] = df["row_num"].astype(int)
    for col in header:
        df[col] = df[col].astype(str)
    if malformed:
        warnings.append(f"{len(malformed)} malformed row(s) (wrong field count) quarantined")
    return Batch(path=path, checksum=checksum, header=header, data=df, malformed=malformed, warnings=warnings)
