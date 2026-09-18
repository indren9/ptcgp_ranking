"""python -m historical_rebuild --boundaries reviewed.json status|next|all|window ID"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .model import RebuildError, load_windows
from .runner import Runner, status_text

BASE = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = BASE / "outputs" / "TCG" / "Rebuild"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Isolated TCG historical rebuild. Never publishes outputs.")
    parser.add_argument("--boundaries", required=True, type=Path, help="separately reviewed ordered canonical boundary JSON")
    parser.add_argument("--config", type=Path, default=BASE / "config" / "tcg.yaml")
    sub = parser.add_subparsers(dest="operation", required=True)
    sub.add_parser("status", help="offline integrity/status view; no network or recomputation")
    sub.add_parser("next", help="resume exactly one next incomplete window")
    sub.add_parser("all", help="chronological execution; stop at first failure")
    sub.add_parser("window", help="execute/resume only the requested window").add_argument("window_id")
    args = parser.parse_args(argv)
    backend = None
    try:
        import yaml
        from .production import ProductionBackend
        if OUTPUT_ROOT.resolve() != OUTPUT_ROOT.absolute():
            raise RebuildError("rebuild output root must not traverse symlinks/junctions")
        windows = load_windows(json.loads(args.boundaries.read_text(encoding="utf-8")))
        backend = ProductionBackend(yaml.safe_load(args.config.read_text(encoding="utf-8")))
        runner = Runner(OUTPUT_ROOT, windows, backend)
        if args.operation != "status":
            runner.run(args.operation, getattr(args, "window_id", None))
        print(status_text(runner.status()))
        return 0
    except (RebuildError, ValueError, KeyError, TypeError, OSError) as exc:
        print(f"TCG HISTORICAL REBUILD: {exc}", file=sys.stderr)
        return 2
    finally:
        if backend is not None:
            backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
