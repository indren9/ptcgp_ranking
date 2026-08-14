from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import tempfile
from typing import Iterable, Sequence

from domain.expansions import Expansion
from sources.limitless.client import make_session
from sources.limitless.pages.sets import fetch_expansions_http


DEFAULT_CATALOG_URL = "https://play.limitlesstcg.com/decks?game=POCKET&format=standard"
DEFAULT_OUTPUT_PATH = Path("public") / "expansions_pocket_standard.csv"


def fetch_catalog(url: str = DEFAULT_CATALOG_URL, *, timeout: int = 20) -> list[Expansion]:
    session = make_session(timeout=timeout)
    try:
        return fetch_expansions_http(session, decks_url=url)
    finally:
        session.close()


def load_catalog_cache(path: Path) -> list[Expansion]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [Expansion(**item) for item in payload.get("expansions", [])]


def catalog_rows(expansions: Iterable[Expansion]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for expansion in expansions:
        code = str(expansion.code or "").strip()
        name = str(expansion.name or "").strip()
        if not code or not name:
            raise ValueError("Every published expansion must have both code and name.")
        if code in seen:
            raise ValueError(f"Duplicate expansion code in catalog: {code}")
        seen.add(code)
        rows.append((code, name))
    if not rows:
        raise ValueError("The expansion catalog is empty; refusing to replace the public CSV.")
    return rows


def write_catalog_csv(path: Path, expansions: Iterable[Expansion]) -> int:
    rows = catalog_rows(expansions)
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(("code", "name"))
            writer.writerows(rows)
        temp_path.replace(path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return len(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish the Limitless Pocket expansion catalog as a Google Sheets-ready CSV."
    )
    parser.add_argument("--url", default=DEFAULT_CATALOG_URL, help="Limitless decks catalog URL.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Destination CSV path.")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds.")
    parser.add_argument(
        "--cache-json",
        type=Path,
        default=None,
        help="Read an existing expansion cache instead of making an HTTP request.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    expansions = load_catalog_cache(args.cache_json) if args.cache_json else fetch_catalog(args.url, timeout=args.timeout)
    count = write_catalog_csv(args.output, expansions)
    print(f"Published {count} expansions to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
