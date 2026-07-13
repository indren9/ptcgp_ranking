#!/usr/bin/env python3
# ------------------------------------------------------------
# Quick commands from the repository root:
#   1) Generate the tree including the ".\" root line:
#      python -m utils.make_project_tree . --include-root
#
#   2) Ignore .gitignore completely and use only built-in rules:
#      python -m utils.make_project_tree . --include-root --no-gitignore
#
#   3) Force inclusion of excluded/ignored top-level directories:
#      python -m utils.make_project_tree . --include-root --force-include outputs config data
#
#   4) Use "/" as directory suffix instead of "\":
#      python -m utils.make_project_tree . --include-root --dir-suffix "/"
#
# Notes:
# - outputs/ is excluded by default; use --force-include outputs only when needed.
# - Timestamped files such as *_YYYYMMDD_HHMMSS.ext are hidden; *_latest.* files remain.
# - project_tree.txt and project_tree_*.txt variants are always excluded from the tree.
# ------------------------------------------------------------

"""
Generate project_tree.txt as an ASCII tree.

Features:
- Always excludes: .git/, .venv/, __pycache__/, .vscode/, .pytest_cache/,
  .pytest_tmp/, .agents/, .codex/, cache/, outputs/
- Excludes files: .gitignore, __init__.py, make_project_tree.py
- Also excludes project_tree.txt and project_tree_*.txt
- Hides timestamped version files (_YYYYMMDD_HHMMSS.*) and keeps only *_latest.*
- Optionally applies .gitignore rules through pathspec when installed
- outputs/ is excluded by default and can be included with --force-include outputs

Optional dependency for .gitignore support:
  pip install pathspec
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

try:
    import pathspec  # type: ignore
except Exception:  # pragma: no cover
    pathspec = None

DEFAULT_EXCLUDE_DIRS = {
    ".agents",
    ".codex",
    ".git",
    ".pytest_cache",
    ".pytest_tmp",
    ".venv",
    ".vscode",
    "__pycache__",
    "cache",
    "outputs",
}

DEFAULT_EXCLUDE_FILES = {
    ".gitignore",
    "__init__.py",
    "make_project_tree.py",
    "project_tree.txt",
}

EXCLUDE_NAME_PATTERNS = [
    re.compile(r"^project_tree_.*\.txt$", re.IGNORECASE),
]

VERSIONED_RE = re.compile(r".*_\d{8}_\d{6}\.[^.]+$", re.IGNORECASE)
LATEST_RE = re.compile(r".*_latest\.[^.]+$", re.IGNORECASE)

SELF_PATH: Path | None = None
ROOT_PATH: Path | None = None
GITIGNORE_SPEC = None


def _is_versioned(name: str) -> bool:
    return bool(VERSIONED_RE.match(name))


def _is_latest(name: str) -> bool:
    return bool(LATEST_RE.match(name))


def _name_matches_any_pattern(name: str) -> bool:
    return any(pat.match(name) for pat in EXCLUDE_NAME_PATTERNS)


def _rel_posix(p: Path) -> str:
    assert ROOT_PATH is not None
    return p.resolve().relative_to(ROOT_PATH).as_posix()


def _is_gitignored(p: Path, force_include: set[str]) -> bool:
    """Return True when p is ignored by .gitignore, unless force-included."""
    if not pathspec or not GITIGNORE_SPEC:
        return False
    parts = p.resolve().relative_to(ROOT_PATH).parts  # type: ignore[arg-type]
    if parts and parts[0] in force_include:
        return False
    rel = _rel_posix(p)
    return GITIGNORE_SPEC.match_file(rel)


def _filtered_children(
    dir_path: Path,
    exclude_dirs_ci: set[str],
    exclude_files_exact: set[str],
    force_include: set[str],
) -> list[Path]:
    """Filter children using built-in exclusions, .gitignore, and version rules."""
    items: list[Path] = []
    dirs: list[Path] = []
    files: list[Path] = []
    force_include_ci = {name.lower() for name in force_include}

    for child in dir_path.iterdir():
        name = child.name

        if SELF_PATH is not None:
            try:
                if child.resolve() == SELF_PATH:
                    continue
            except FileNotFoundError:
                pass

        if _is_gitignored(child, force_include):
            continue

        if child.is_dir():
            if name.lower() in exclude_dirs_ci and name.lower() not in force_include_ci:
                continue
            dirs.append(child)
        else:
            if name in exclude_files_exact or _name_matches_any_pattern(name):
                continue
            files.append(child)

    kept_files: list[Path] = []
    for f in files:
        fname = f.name
        if _is_versioned(fname) and not _is_latest(fname):
            continue
        kept_files.append(f)

    items.extend(sorted(dirs, key=lambda x: x.name.lower()))
    items.extend(sorted(kept_files, key=lambda x: x.name.lower()))
    return items


def _walk(
    root: Path,
    prefix: str,
    lines: list[str],
    dir_suffix: str,
    exclude_dirs_ci: set[str],
    exclude_files_exact: set[str],
    force_include: set[str],
):
    children = _filtered_children(root, exclude_dirs_ci, exclude_files_exact, force_include)
    n = len(children)
    for i, ch in enumerate(children):
        is_last = i == n - 1
        branch = "`-- " if is_last else "|-- "
        if ch.is_dir():
            lines.append(f"{prefix}{branch}{ch.name}{dir_suffix}")
            _walk(
                ch,
                prefix + ("    " if is_last else "|   "),
                lines,
                dir_suffix,
                exclude_dirs_ci,
                exclude_files_exact,
                force_include,
            )
        else:
            lines.append(f"{prefix}{branch}{ch.name}")


def _load_gitignore(root: Path):
    """Load .gitignore as a pathspec, or return None when unavailable."""
    if not pathspec:
        return None
    gi = root / ".gitignore"
    if not gi.exists():
        return None
    text = gi.read_text(encoding="utf-8", errors="ignore")
    return pathspec.PathSpec.from_lines("gitwildmatch", text.splitlines())


def main():
    global SELF_PATH, ROOT_PATH, GITIGNORE_SPEC

    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=".", help="Root folder (default: .)")
    ap.add_argument("--out", default="project_tree.txt", help="Output filename")
    ap.add_argument("--include-root", action="store_true", help='Show the root line (".\\") at the top')
    ap.add_argument("--dir-suffix", default="\\", help='Directory suffix (default: "\\", use "/" if preferred)')
    ap.add_argument("--no-gitignore", action="store_true", help="Do not use .gitignore even when present")
    ap.add_argument("--force-include", nargs="*", default=[], help="Top-level directories to include anyway")
    ap.add_argument("--extra-exclude-dirs", nargs="*", default=[], help="Additional directories to exclude")
    ap.add_argument("--extra-exclude-files", nargs="*", default=[], help="Additional files to exclude")
    args = ap.parse_args()

    try:
        SELF_PATH = Path(__file__).resolve()
    except NameError:
        SELF_PATH = None

    ROOT_PATH = Path(args.root).resolve()

    if not args.no_gitignore:
        GITIGNORE_SPEC = _load_gitignore(ROOT_PATH)
        if GITIGNORE_SPEC is None and pathspec is None:
            print("[INFO] pathspec is not installed: .gitignore will not be applied (pip install pathspec to enable it).")

    exclude_dirs_ci = {d.lower() for d in DEFAULT_EXCLUDE_DIRS}
    exclude_dirs_ci.update({d.lower() for d in args.extra_exclude_dirs})
    exclude_files_exact = set(DEFAULT_EXCLUDE_FILES)
    exclude_files_exact.update(args.extra_exclude_files)
    force_include = set(args.force_include)

    lines: list[str] = []
    if args.include_root:
        lines.append(f".{args.dir_suffix}")

    _walk(ROOT_PATH, "" if not args.include_root else "", lines, args.dir_suffix, exclude_dirs_ci, exclude_files_exact, force_include)

    out_path = ROOT_PATH / args.out
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Created {out_path} ({len(lines)} lines).")


if __name__ == "__main__":
    main()
