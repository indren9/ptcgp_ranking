from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
import tempfile
from typing import Iterable, Sequence

from domain.expansions import Expansion
from sources.limitless.client import make_session
from sources.limitless.pages.sets import fetch_expansions_http


DEFAULT_CATALOG_URL = "https://play.limitlesstcg.com/decks?game=POCKET&format=standard"
DEFAULT_OUTPUT_PATH = Path("public") / "expansions_pocket_standard.csv"
DEFAULT_MANUAL_PATH = Path("config") / "manual_expansions_pocket.csv"
POCKET_CODE_RE = re.compile(r"^([A-Z]+)(\d+)([a-z]?)$", re.IGNORECASE)


def fetch_catalog(url: str = DEFAULT_CATALOG_URL, *, timeout: int = 20) -> list[Expansion]:
    session = make_session(timeout=timeout)
    try:
        return fetch_expansions_http(session, decks_url=url)
    finally:
        session.close()


def load_catalog_cache(path: Path) -> list[Expansion]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [Expansion(**item) for item in payload.get("expansions", [])]


def load_manual_catalog(path: Path) -> list[Expansion]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["code", "name"]:
            raise ValueError(f"Manual catalog must have exactly the columns code,name: {path}")
        return [Expansion(code=row["code"], name=row["name"]) for row in reader]


def expansion_code_sort_key(code: str) -> tuple:
    match = POCKET_CODE_RE.fullmatch(code)
    if not match:
        return (1, code.upper(), 0, "")
    prefix, number, suffix = match.groups()
    return (0, prefix.upper(), int(number), suffix.lower())


def merge_catalogs(primary: Iterable[Expansion], manual: Iterable[Expansion]) -> list[Expansion]:
    merged: dict[str, Expansion] = {}
    for source_name, expansions in (("Limitless", primary), ("manual", manual)):
        source_codes: set[str] = set()
        for expansion in expansions:
            code = str(expansion.code or "").strip()
            key = code.casefold()
            if key in source_codes:
                raise ValueError(f"Duplicate expansion code in {source_name} catalog: {code}")
            source_codes.add(key)
            merged[key] = expansion
    return list(merged.values())


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
    return sorted(rows, key=lambda row: expansion_code_sort_key(row[0]))


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
        "--manual-csv",
        type=Path,
        default=DEFAULT_MANUAL_PATH,
        help="Optional CSV containing fixed code,name rows to merge with the Limitless catalog.",
    )
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
    expansions = merge_catalogs(expansions, load_manual_catalog(args.manual_csv))
    count = write_catalog_csv(args.output, expansions)
    print(f"Published {count} expansions to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
