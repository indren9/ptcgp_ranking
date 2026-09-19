"""Offline checks/regeneration only; deliberately no network or runner execution."""
import argparse
import json

from historical_rebuild.store import atomic_write
from .catalog import LEDGER, artifacts, load_human_adjudications


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write fixed unreviewed candidate/preview/review paths only")
    args = parser.parse_args(argv)
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    outputs = artifacts(ledger, load_human_adjudications())
    for path, text in outputs.items():
        if path.resolve() != path.absolute():
            raise ValueError("candidate outputs cannot traverse symlinks/junctions")
        if args.write:
            atomic_write(path, text.encode("utf-8"))
        elif path.read_text(encoding="utf-8") != text:
            raise ValueError("committed artifact differs from deterministic ledger derivation: " + str(path))
    print("Historical candidate integrity/schema checks PASS; see preview for readiness. reviewed=false; no rebuild executed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
