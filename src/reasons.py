"""Stable rejection reason codes and shared constants."""

# Row-level rejection reasons, listed in evaluation order (first failure wins).
MALFORMED_ROW = "MALFORMED_ROW"
MISSING_RECORD_ID = "MISSING_RECORD_ID"
MISSING_CODE = "MISSING_CODE"
MISSING_EVENT_DATE = "MISSING_EVENT_DATE"
INVALID_DATE = "INVALID_DATE"
INVALID_AMOUNT = "INVALID_AMOUNT"
DUPLICATE_RECORD = "DUPLICATE_RECORD"
UNKNOWN_CODE = "UNKNOWN_CODE"
INACTIVE_CODE = "INACTIVE_CODE"
DEPRECATED_CODE = "DEPRECATED_CODE"
CODE_NOT_YET_EFFECTIVE = "CODE_NOT_YET_EFFECTIVE"
CODE_EXPIRED = "CODE_EXPIRED"
ORPHAN_ENTITY = "ORPHAN_ENTITY"

REASON_ORDER = [
    MALFORMED_ROW, MISSING_RECORD_ID, MISSING_CODE, MISSING_EVENT_DATE,
    INVALID_DATE, INVALID_AMOUNT, DUPLICATE_RECORD, UNKNOWN_CODE,
    INACTIVE_CODE, DEPRECATED_CODE, CODE_NOT_YET_EFFECTIVE, CODE_EXPIRED,
    ORPHAN_ENTITY,
]

# File / batch-level failure
SCHEMA_MISMATCH = "SCHEMA_MISMATCH"

VALID = "VALID"
REJECTED = "REJECTED"

REQUIRED_COLUMNS = ["record_id", "entity_id", "product_code", "event_date", "amount"]
OPTIONAL_COLUMNS = ["source_file"]
REFERENCE_COLUMNS = ["product_code", "description", "status", "effective_from", "effective_to"]
REFERENCE_STATUSES = ["ACTIVE", "DEPRECATED", "INACTIVE"]
DEPRECATED_POLICIES = ["allow_historical", "reject"]


class PipelineError(Exception):
    """A failure that marks the run FAILED with a clear message."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
