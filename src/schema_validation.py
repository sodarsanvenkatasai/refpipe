"""File-level (batch) schema checks. A failure here marks the whole run FAILED."""
from __future__ import annotations

from .reasons import OPTIONAL_COLUMNS, REQUIRED_COLUMNS, SCHEMA_MISMATCH, PipelineError


def check_header(header: list[str], strict_columns: bool = False) -> list[str]:
    """Validate the CSV header; return a list of warnings or raise PipelineError.

    * missing required column      -> fail the batch
    * duplicate column name        -> fail the batch
    * unexpected extra column      -> warning and ignored (fail if strict_columns)
    """
    cleaned = [h.strip() for h in header]
    dupes = sorted({h for h in cleaned if cleaned.count(h) > 1})
    if dupes:
        raise PipelineError(SCHEMA_MISMATCH, f"duplicate column name(s): {dupes}")
    missing = [c for c in REQUIRED_COLUMNS if c not in cleaned]
    if missing:
        raise PipelineError(SCHEMA_MISMATCH, f"missing required column(s): {missing}; found {cleaned}")
    extra = [c for c in cleaned if c not in REQUIRED_COLUMNS and c not in OPTIONAL_COLUMNS]
    if extra and strict_columns:
        raise PipelineError(SCHEMA_MISMATCH, f"unexpected column(s) in strict mode: {extra}")
    return [f"unexpected column(s) ignored: {extra}"] if extra else []
