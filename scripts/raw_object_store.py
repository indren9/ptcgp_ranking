from __future__ import annotations

import argparse
import json
from pathlib import Path

from sources.limitless.tournament_api.object_store import (
    LocalObjectStoreBackend,
    S3ObjectStoreBackend,
    persist_canonical_raw_run,
    restore_canonical_raw_run,
)


def _backend(args: argparse.Namespace):
    if args.backend == "local":
        if not args.local_root:
            raise SystemExit("--local-root is required for --backend local")
        return LocalObjectStoreBackend(args.local_root)
    return S3ObjectStoreBackend.from_env()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Persist/restore private canonical Limitless Tournament API raw evidence."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("persist", "restore"):
        item = sub.add_parser(command)
        item.add_argument("--run-id", required=True)
        item.add_argument("--raw-root", default="data/raw/limitless_api")
        item.add_argument("--backend", choices=("s3", "local"), default="s3")
        item.add_argument("--local-root")
        item.add_argument("--key-prefix", default="limitless-api/v1")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    backend = _backend(args)
    if args.command == "persist":
        result = persist_canonical_raw_run(
            Path(args.raw_root),
            args.run_id,
            backend,
            key_prefix=args.key_prefix,
        )
    else:
        result = restore_canonical_raw_run(
            Path(args.raw_root),
            args.run_id,
            backend,
            key_prefix=args.key_prefix,
        )
    print(
        json.dumps(
            {
                "action": args.command,
                "run_id": result.run_id,
                "manifest_key": result.manifest_key,
                "source_manifest_sha256": result.source_manifest_sha256,
                "file_count": result.file_count,
                "total_bytes": result.total_bytes,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
