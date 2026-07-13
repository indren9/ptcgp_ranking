#!/usr/bin/env python3
# ------------------------------------------------------------
# Quick commands from the repository root:
#   1) Dry-run, preview only:
#      python -m utils.clean_outputs
#
#   2) Apply cleanup, deleting timestamped files:
#      python -m utils.clean_outputs --apply
#
#   Useful options: --prune-empty-dirs  --include-archives
# ------------------------------------------------------------

"""
Clean the outputs/ folder by deleting timestamped version files such as
foo_YYYYMMDD_HHMMSS.ext, while keeping *_latest.* and non-timestamped files.

- Default mode is a dry-run showing what would be deleted.
- Files are actually deleted only with --apply.
- Optional: --prune-empty-dirs removes empty directories left behind.
- By default, archives/ is not touched. Use --include-archives to clean it too.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

VERSIONED_RE = re.compile(r".*_\d{8}_\d{6}\.[^.]+$", re.IGNORECASE)
LATEST_RE = re.compile(r".*_latest\.[^.]+$", re.IGNORECASE)

SAFE_DIR_NAMES = {"archives"}


def is_versioned(path: Path) -> bool:
    return bool(VERSIONED_RE.match(path.name))


def is_latest(path: Path) -> bool:
    return bool(LATEST_RE.match(path.name))


def should_skip_dir(d: Path, include_archives: bool) -> bool:
    name = d.name.lower()
    return bool(not include_archives and name in SAFE_DIR_NAMES)


def collect_deletions(root: Path, include_archives: bool) -> list[Path]:
    to_delete: list[Path] = []
    if not root.exists():
        return to_delete
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        if is_versioned(p) and not is_latest(p):
            if not include_archives and any(part.lower() in SAFE_DIR_NAMES for part in p.parts):
                continue
            to_delete.append(p)
    return to_delete


def prune_empty_dirs(root: Path, include_archives: bool) -> list[Path]:
    pruned: list[Path] = []
    dirs = sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda x: len(x.parts), reverse=True)
    for d in dirs:
        if should_skip_dir(d, include_archives):
            continue
        try:
            if not any(d.iterdir()):
                d.rmdir()
                pruned.append(d)
        except OSError:
            pass
    return pruned


def main():
    ap = argparse.ArgumentParser(description="Clean outputs/ while keeping only *_latest.* version outputs.")
    ap.add_argument("--root", default="outputs", help="Folder to clean (default: outputs)")
    ap.add_argument("--apply", action="store_true", help="Actually delete files; otherwise run as dry-run")
    ap.add_argument("--prune-empty-dirs", action="store_true", help="Remove empty directories left behind")
    ap.add_argument("--include-archives", action="store_true", help="Also clean inside archives/")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.exists() or not root.is_dir():
        print(f"[ERROR] Folder not found: {root}")
        return

    deletions = collect_deletions(root, include_archives=args.include_archives)

    if not deletions:
        print(f"No files to remove in {root} (dry-run).")
    else:
        print(f"Found {len(deletions)} files to remove:")
        for p in deletions:
            print("  -", p.relative_to(root))

    if args.apply:
        for p in deletions:
            try:
                p.unlink()
            except OSError as e:
                print(f"[WARN] Could not remove {p}: {e}")
        print(f"Removed {len(deletions)} files.")

        if args.prune_empty_dirs:
            pruned = prune_empty_dirs(root, include_archives=args.include_archives)
            if pruned:
                print(f"Removed {len(pruned)} empty directories:")
                for d in pruned:
                    print("  -", d.relative_to(root))
            else:
                print("No empty directories to remove.")
    else:
        print("\nDry-run complete. Add --apply to confirm deletion.")


if __name__ == "__main__":
    main()
