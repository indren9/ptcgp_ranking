from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
from typing import Sequence

from pipelines.deck_ranking import run_deck_ranking


log = logging.getLogger("ptcgp")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cli.deck_ranking",
        description="Run the PTCGP deck ranking pipeline.",
    )
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="Run the deck ranking pipeline.")
    run.add_argument("--base-dir", default=".", help="Project root. Default: current directory.")
    run.add_argument("--config", default="config/config.yaml", help="Config YAML path, relative to base-dir if needed.")
    run.add_argument("--heatmap-top-n", type=int, default=10, help="Number of decks to include in the WR heatmap.")
    run.add_argument("--skip-scrape", action="store_true", help="Skip scraping and read existing contract files.")
    run.add_argument("--skip-core", action="store_true", help="Skip core matrix rebuild.")
    run.add_argument("--skip-mars", action="store_true", help="Skip MARS ranking.")
    run.add_argument("--skip-heatmap", action="store_true", help="Skip WR heatmap generation.")
    run.add_argument("--skip-report", action="store_true", help="Skip Excel report generation.")
    run.add_argument("--progress", action="store_true", help="Show a tqdm progress bar during matchup scraping.")
    run.set_defaults(func=_run_command)

    return parser


def _run_command(args: argparse.Namespace) -> int:
    base_dir = Path(args.base_dir).resolve()
    result = run_deck_ranking(
        base_dir=base_dir,
        config_path=args.config,
        run_scrape=not args.skip_scrape,
        run_core=False if args.skip_core else None,
        run_mars=not args.skip_mars,
        run_heatmap=not args.skip_heatmap,
        run_report=not args.skip_report,
        heatmap_top_n=args.heatmap_top_n,
        show_progress=args.progress,
    )
    log.info("[DONE] outputs=%d | frames=%d", len(result.outputs), len(result.frames))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] not in {"run", "-h", "--help"}:
        raw_args.insert(0, "run")
    args = parser.parse_args(raw_args)
    if args.command is None:
        args = parser.parse_args(["run"])

    try:
        return int(args.func(args))
    except Exception:
        log.exception("[FAILED] deck ranking pipeline")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
