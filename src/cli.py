"""Command line interface:  python -m src.cli <command> [options]"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .compare import compare_runs
from .config import load_config
from .db import connect
from .metrics import recover_stale_runs
from .pipeline import run_pipeline
from .reasons import PipelineError
from .reference_loader import load_entities, store_reference


def _setup_logging(output_dir: str) -> None:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(Path(output_dir) / "pipeline.log", encoding="utf-8")])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.cli", description=__doc__)
    p.add_argument("--config", default="config.toml")
    p.add_argument("--db", help="SQLite file (default from config.toml)")
    p.add_argument("--output-dir")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="validate an incoming CSV against ONE reference version")
    r.add_argument("--input", required=True)
    r.add_argument("--reference-version", required=True, help="explicit version to validate against (e.g. v1)")
    r.add_argument("--reference", help="reference CSV to load as that version first (no-op if already loaded)")
    r.add_argument("--entities", help="entities CSV to load; enables the ORPHAN_ENTITY check")
    r.add_argument("--check-entities", action="store_true", help="enable ORPHAN_ENTITY using entities already loaded")
    r.add_argument("--event-from", help="backfill: only process rows with event_date >= this ISO date")
    r.add_argument("--event-to", help="backfill: only process rows with event_date <= this ISO date")
    r.add_argument("--deprecated-policy", choices=["allow_historical", "reject"])
    r.add_argument("--engine", choices=["vectorized", "rowwise"])
    r.add_argument("--strict-columns", action="store_true", default=None)
    r.add_argument("--run-id")
    r.add_argument("--fail-at", choices=["after_stage", "before_commit"], help=argparse.SUPPRESS)

    lr = sub.add_parser("load-reference", help="validate and store a reference snapshot as a new version")
    lr.add_argument("--reference", required=True)
    lr.add_argument("--reference-version", required=True)

    le = sub.add_parser("load-entities", help="load parent entity ids")
    le.add_argument("--entities", required=True)

    c = sub.add_parser("compare", help="compare two runs (e.g. same input under v1 and v2)")
    c.add_argument("--run-a", required=True)
    c.add_argument("--run-b", required=True)

    sub.add_parser("runs", help="list pipeline runs")
    sub.add_parser("recover", help="mark runs stuck in RUNNING (killed process) as FAILED so they can be retried")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config, db_path=args.db, output_dir=args.output_dir,
                      deprecated_policy=getattr(args, "deprecated_policy", None),
                      engine=getattr(args, "engine", None),
                      strict_columns=getattr(args, "strict_columns", None))
    _setup_logging(cfg.output_dir)
    conn = connect(cfg.db_path)
    try:
        if args.cmd == "load-reference":
            print(store_reference(conn, args.reference_version, args.reference))
        elif args.cmd == "load-entities":
            print(f"loaded {load_entities(conn, args.entities)} entity ids")
        elif args.cmd == "run":
            if args.entities:
                load_entities(conn, args.entities)
            summary = run_pipeline(
                conn, cfg, args.input, args.reference_version, reference_path=args.reference,
                check_entities=bool(args.entities or args.check_entities), event_from=args.event_from,
                event_to=args.event_to, run_id=args.run_id, fail_at=args.fail_at)
            show = {k: v for k, v in summary.items() if k != "reconciliation"}
            show["reconciliation"] = "all %d checks passed (see reconciliation.json)" % len(summary["reconciliation"])
            print(json.dumps(show, indent=2))
            print(f"\nreports: {Path(cfg.output_dir) / summary['run_id']}")
        elif args.cmd == "compare":
            print(json.dumps(compare_runs(conn, args.run_a, args.run_b), indent=2))
        elif args.cmd == "recover":
            ids = recover_stale_runs(conn)
            print(f"marked {len(ids)} stale run(s) FAILED: {', '.join(ids) or '-'}")
        elif args.cmd == "runs":
            for r in conn.execute("SELECT run_id, status, reference_version, input_count, valid_count,"
                                  " rejected_count, published_count, error_message FROM pipeline_runs"
                                  " ORDER BY started_at"):
                print(" | ".join("" if v is None else str(v) for v in tuple(r)))
        return 0
    except PipelineError as exc:
        print(f"ERROR {exc.code}: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:  # run row is already marked FAILED by run_pipeline
        print(f"ERROR {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
