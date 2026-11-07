"""Deterministic synthetic data generator.

    python scripts/generate_data.py --rows 100000 --seed 42 --out-dir data/generated

Writes to --out-dir:
  incoming_records.csv       pipeline input (never contains labels)
  reference_codes_v1.csv     reference snapshot v1
  reference_codes_v2.csv     reference snapshot v2 (adds codes, extends windows, deprecates one class)
  entities.csv               parent entity ids (for the ORPHAN_ENTITY check)
  expected_results.csv       EVALUATION ONLY: expected outcome per row_num for v1 and v2

The same --rows/--seed always produces byte-identical files. Expected labels assume the default
policy (deprecated_policy=allow_historical) and the entity check enabled.
All data is synthetic; no personal or confidential data is used.
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

# ---- reference design (see docs/DATA_DICTIONARY.md) -------------------------------------------
# class prefix -> (v1 window, v1 status, v2 window, v2 status); None window = absent in that version
D = date.fromisoformat
CLASSES = {
    "ACT":  (("2024-01-01", "2025-12-31", "ACTIVE"),     ("2024-01-01", "2026-12-31", "ACTIVE")),
    "NEW":  (None,                                       ("2025-06-01", "", "ACTIVE")),
    "DEP":  (("2024-01-01", "2025-06-30", "DEPRECATED"), ("2024-01-01", "2025-06-30", "DEPRECATED")),
    "OLD":  (("2024-01-01", "2025-03-31", "ACTIVE"),     ("2024-01-01", "2025-12-31", "ACTIVE")),
    "INA":  (("2024-01-01", "2025-12-31", "INACTIVE"),   ("2024-01-01", "2025-12-31", "INACTIVE")),
    "FUT":  (("2026-01-01", "2026-12-31", "ACTIVE"),     ("2026-01-01", "2026-12-31", "ACTIVE")),
    "EXP":  (("2023-01-01", "2024-12-31", "ACTIVE"),     ("2023-01-01", "2024-12-31", "ACTIVE")),
    "DEPN": (("2024-01-01", "2025-12-31", "ACTIVE"),     ("2024-01-01", "2025-12-31", "DEPRECATED")),
}
# MULT has two non-overlapping windows with a gap, identical in both versions
MULT_WINDOWS = [("2024-01-01", "2024-06-30", "ACTIVE"), ("2025-01-01", "2025-12-31", "ACTIVE")]

V, REJ = "VALID", "REJECTED"
# category -> (weight, expected_v1, expected_v2) ; expected = (outcome, reason)
OK = (V, "")
CATEGORIES = {
    "valid_active":        (30, OK, OK),
    "valid_normalized":    (4, OK, OK),            # ' act0001 ' -> ACT0001
    "valid_blank_amount":  (2, OK, OK),            # amount is nullable
    "valid_multi_window":  (2, OK, OK),
    "boundary_from":       (3, OK, OK),            # event_date == effective_from (inclusive)
    "boundary_to":         (3, OK, OK),            # event_date == effective_to   (inclusive)
    "boundary_before":     (3, (REJ, "CODE_NOT_YET_EFFECTIVE"), (REJ, "CODE_NOT_YET_EFFECTIVE")),
    "valid_in_v2_only":    (3, (REJ, "CODE_EXPIRED"), OK),        # ACT date in v2's extension
    "new_in_v2":           (4, (REJ, "UNKNOWN_CODE"), OK),        # code added by refresh
    "new_in_v2_early":     (1, (REJ, "UNKNOWN_CODE"), (REJ, "CODE_NOT_YET_EFFECTIVE")),
    "expired_in_v1_only":  (3, (REJ, "CODE_EXPIRED"), OK),        # OLD window extended in v2
    "deprecated_historic": (4, OK, OK),            # allow_historical: accepted, flagged
    "deprecated_after":    (2, (REJ, "CODE_EXPIRED"), (REJ, "CODE_EXPIRED")),
    "deprecated_in_v2":    (3, OK, OK),            # ACTIVE in v1, DEPRECATED (flagged) in v2
    "inactive":            (2, (REJ, "INACTIVE_CODE"), (REJ, "INACTIVE_CODE")),
    "not_yet_effective":   (2, (REJ, "CODE_NOT_YET_EFFECTIVE"), (REJ, "CODE_NOT_YET_EFFECTIVE")),
    "expired":             (2, (REJ, "CODE_EXPIRED"), (REJ, "CODE_EXPIRED")),
    "window_gap":          (1, (REJ, "CODE_EXPIRED"), (REJ, "CODE_EXPIRED")),
    "unknown_code":        (3, (REJ, "UNKNOWN_CODE"), (REJ, "UNKNOWN_CODE")),
    "orphan_entity":       (2, (REJ, "ORPHAN_ENTITY"), (REJ, "ORPHAN_ENTITY")),
    "missing_record_id":   (2, (REJ, "MISSING_RECORD_ID"), (REJ, "MISSING_RECORD_ID")),
    "missing_code":        (2, (REJ, "MISSING_CODE"), (REJ, "MISSING_CODE")),
    "missing_event_date":  (2, (REJ, "MISSING_EVENT_DATE"), (REJ, "MISSING_EVENT_DATE")),
    "invalid_date":        (2, (REJ, "INVALID_DATE"), (REJ, "INVALID_DATE")),
    "invalid_amount":      (2, (REJ, "INVALID_AMOUNT"), (REJ, "INVALID_AMOUNT")),
    "malformed_row":       (2, (REJ, "MALFORMED_ROW"), (REJ, "MALFORMED_ROW")),
    "duplicate":           (5, (REJ, "DUPLICATE_RECORD"), (REJ, "DUPLICATE_RECORD")),
}


def _rand_date(rng: random.Random, start: str, end: str) -> str:
    a, b = D(start), D(end)
    return (a + timedelta(days=rng.randint(0, (b - a).days))).isoformat()


def _write_reference(path: Path, version: str, k: int) -> None:
    rows = []
    for prefix, (w1, w2) in CLASSES.items():
        w = w1 if version == "v1" else w2
        if w is None:
            continue
        for i in range(1, k + 1):
            rows.append((version, f"{prefix}{i:04d}", f"{prefix} product {i}", w[2], w[0], w[1]))
    for i in range(1, k + 1):
        for j, (f, t, s) in enumerate(MULT_WINDOWS, start=1):
            rows.append((version, f"MULT{i:04d}", f"MULT product {i} window {j}", s, f, t))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["reference_version", "product_code", "description", "status", "effective_from", "effective_to"])
        w.writerows(rows)


def generate(rows: int, seed: int, out_dir: str, codes_per_class: int | None = None) -> dict:
    rng = random.Random(seed)
    k = codes_per_class or (5 if rows <= 1000 else 50)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    n_entities = max(20, rows // 10)
    entities = [f"E{i:06d}" for i in range(1, n_entities + 1)]

    def code(prefix: str) -> str:
        return f"{prefix}{rng.randint(1, k):04d}"

    # --- choose a category for every row (one of each first, then weighted) ----------------
    names = list(CATEGORIES)
    weights = [CATEGORIES[c][0] for c in names]
    cats = list(names) if rows >= len(names) else []
    cats = cats[:rows]
    cats += rng.choices(names, weights=weights, k=rows - len(cats))
    rng.shuffle(cats)

    def build(cat: str, i: int) -> dict:
        rec = {"record_id": f"R{i:08d}", "entity_id": rng.choice(entities), "product_code": "",
               "event_date": "", "amount": f"{rng.uniform(1, 5000):.2f}"}
        if cat in ("valid_active", "duplicate", "valid_normalized", "valid_blank_amount", "orphan_entity",
                   "missing_record_id", "missing_event_date", "invalid_date", "invalid_amount", "malformed_row"):
            rec["product_code"] = code("ACT")
            rec["event_date"] = _rand_date(rng, "2024-01-01", "2025-12-31")
        if cat == "valid_normalized":
            rec["product_code"] = rng.choice([" {} ", "{}", " {}"]).format(rec["product_code"].lower())
        elif cat == "valid_blank_amount":
            rec["amount"] = ""
        elif cat == "valid_multi_window":
            rec["product_code"] = code("MULT")
            rec["event_date"] = rng.choice([_rand_date(rng, "2024-01-01", "2024-06-30"),
                                            _rand_date(rng, "2025-01-01", "2025-12-31")])
        elif cat == "boundary_from":
            rec["product_code"], rec["event_date"] = code("ACT"), "2024-01-01"
        elif cat == "boundary_to":
            rec["product_code"], rec["event_date"] = code("ACT"), "2025-12-31"
        elif cat == "boundary_before":
            rec["product_code"], rec["event_date"] = code("ACT"), "2023-12-31"
        elif cat == "valid_in_v2_only":
            rec["product_code"], rec["event_date"] = code("ACT"), _rand_date(rng, "2026-01-01", "2026-12-31")
        elif cat == "new_in_v2":
            rec["product_code"], rec["event_date"] = code("NEW"), _rand_date(rng, "2025-06-01", "2026-06-30")
        elif cat == "new_in_v2_early":
            rec["product_code"], rec["event_date"] = code("NEW"), "2025-05-31"
        elif cat == "expired_in_v1_only":
            rec["product_code"], rec["event_date"] = code("OLD"), _rand_date(rng, "2025-04-01", "2025-12-31")
        elif cat == "deprecated_historic":
            rec["product_code"], rec["event_date"] = code("DEP"), _rand_date(rng, "2024-01-01", "2025-06-30")
        elif cat == "deprecated_after":
            rec["product_code"], rec["event_date"] = code("DEP"), _rand_date(rng, "2025-07-01", "2025-12-31")
        elif cat == "deprecated_in_v2":
            rec["product_code"], rec["event_date"] = code("DEPN"), _rand_date(rng, "2024-01-01", "2025-12-31")
        elif cat == "inactive":
            rec["product_code"], rec["event_date"] = code("INA"), _rand_date(rng, "2024-01-01", "2025-12-31")
        elif cat == "not_yet_effective":
            rec["product_code"], rec["event_date"] = code("FUT"), _rand_date(rng, "2025-01-01", "2025-12-31")
        elif cat == "expired":
            rec["product_code"], rec["event_date"] = code("EXP"), _rand_date(rng, "2025-01-01", "2025-12-31")
        elif cat == "window_gap":
            rec["product_code"], rec["event_date"] = code("MULT"), _rand_date(rng, "2024-07-01", "2024-12-31")
        elif cat == "unknown_code":
            rec["product_code"] = f"UNK{rng.randint(1, 9999):04d}"
            rec["event_date"] = _rand_date(rng, "2024-01-01", "2025-12-31")
        elif cat == "orphan_entity":
            rec["entity_id"] = "E999999"
        elif cat == "missing_record_id":
            rec["record_id"] = ""
        elif cat == "missing_code":
            rec["product_code"], rec["event_date"] = rng.choice(["", "   "]), _rand_date(rng, "2024-01-01", "2025-12-31")
        elif cat == "missing_event_date":
            rec["event_date"] = ""
        elif cat == "invalid_date":
            rec["event_date"] = rng.choice(["2025-02-30", "31/12/2025", "2025-1-5", "not-a-date", "2025-13-01"])
        elif cat == "invalid_amount":
            rec["amount"] = rng.choice(["abc", "-5.00", "1,200.50", "12.345", "NaN"])
        return rec

    base: list[tuple[str, dict]] = []   # (category, record) for non-duplicates
    dup_slots: list[int] = []
    for i, cat in enumerate(cats, start=1):
        if cat == "duplicate":
            dup_slots.append(i)
        else:
            base.append((cat, build(cat, i)))
    originals = [r for c, r in base if c == "valid_active"]
    if not originals and dup_slots:  # tiny --rows: no original available, make the first one valid
        for j, i in enumerate(dup_slots):
            base.append(("valid_active", build("valid_active", i)))
        dup_slots = []
        originals = [r for c, r in base if c == "valid_active"]

    # --- final order: shuffle base rows; each duplicate lands AFTER its original -------------
    rng.shuffle(base)
    keyed = [(float(pos), cat, rec, None) for pos, (cat, rec) in enumerate(base)]
    pos_of = {id(rec): pos for pos, (_, rec) in enumerate(base)}
    for _ in dup_slots:
        orig = rng.choice(originals)
        p = pos_of[id(orig)]
        keyed.append((p + rng.uniform(0.01, max(0.02, len(base) - p)), "duplicate", dict(orig), "dup"))
    keyed.sort(key=lambda t: t[0])

    incoming, expected = [], []
    with open(out / "incoming_records.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["record_id", "entity_id", "product_code", "event_date", "amount"])
        for row_num, (_, cat, rec, _tag) in enumerate(keyed, start=1):
            vals = [rec["record_id"], rec["entity_id"], rec["product_code"], rec["event_date"], rec["amount"]]
            if cat == "malformed_row":
                vals = vals + ["unexpected_extra"] if rng.random() < 0.5 else vals[:3]
            w.writerow(vals)
            _, e1, e2 = CATEGORIES[cat]
            expected.append((row_num, rec["record_id"], cat, e1[0], e1[1], e2[0], e2[1]))

    with open(out / "expected_results.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["row_num", "record_id", "category", "expected_v1_outcome", "expected_v1_reason",
                    "expected_v2_outcome", "expected_v2_reason"])
        w.writerows(expected)
    with open(out / "entities.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["entity_id"])
        w.writerows([[e] for e in entities])
    _write_reference(out / "reference_codes_v1.csv", "v1", k)
    _write_reference(out / "reference_codes_v2.csv", "v2", k)
    return {"rows": len(keyed), "out_dir": str(out), "codes_per_class": k}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-dir", default="data/generated")
    ap.add_argument("--codes-per-class", type=int)
    a = ap.parse_args()
    print(generate(a.rows, a.seed, a.out_dir, a.codes_per_class))
