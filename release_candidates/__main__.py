"""Offline operator review utility, not a production entry point."""
import argparse
import json
from pathlib import Path

from .model import Candidate
from .reporting import report_json, report_markdown, write_review
from .validation import ParentSnapshot, validate_candidates


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate NON-AUTHORITATIVE release candidates offline")
    parser.add_argument("input", type=Path, help="JSON array including any superseded predecessors")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--save-review", action="store_true", help="Save only to data/candidates/releases/reviews")
    args = parser.parse_args(argv)
    data = json.loads(args.input.read_text(encoding="utf-8"))
    if type(data) is not list or not data:
        raise ValueError("input must be a nonempty candidate array")
    candidates = [Candidate.from_dict(row) for row in data]
    root = Path(__file__).resolve().parents[1]
    parents = {game: ParentSnapshot.from_bytes((root / "data" / "reference" / name).read_bytes())
               for game, name in (("POCKET", "pocket_releases.json"), ("PTCG", "ptcg_live_windows.json"))}
    results = validate_candidates(candidates, parents)
    if args.save_review:
        write_review(results)
    print((report_json if args.format == "json" else report_markdown)(results), end="")
    return 1 if any(r["validation_errors"] or r["conflict_status"] == "AUTHORITATIVE" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
