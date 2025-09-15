#!/usr/bin/env python3
# ------------------------------------------------------------
# COMANDI RAPIDI (da eseguire dal root della repo):
#   1) Genera l'albero includendo la riga di root ".\" e forzando 'outputs/':
#      python -m utils.make_project_tree . --include-root
#
#   2) (Facoltativo) ignora completamente il .gitignore e usa solo le regole interne:
#      python -m utils.make_project_tree . --include-root --no-gitignore
#
#   3) (Facoltativo) forza l'inclusione di altre directory top-level oltre a 'outputs':
#      python -m utils.make_project_tree . --include-root --force-include outputs config data
#
#   4) (Facoltativo) usa "/" come suffisso per le directory invece di "\":
#      python -m utils.make_project_tree . --include-root --dir-suffix "/"
#
# Note:
# - 'outputs/' è SEMPRE incluso per default anche se è nel .gitignore.
# - I file con timestamp tipo *_YYYYMMDD_HHMMSS.ext vengono nascosti; rimangono i *_latest.*.
# - 'project_tree.txt' e varianti 'project_tree_*.txt' sono sempre esclusi dall'albero.
# ------------------------------------------------------------

"""
Genera project_tree.txt con alberatura ASCII (stile richiesto).

Caratteristiche:
- Esclude SEMPRE: .git/, .venv/, __pycache__/, .vscode/, cache/
- Esclude file: .gitignore, __init__.py, make_project_tree.py
- Esclude anche: project_tree.txt e pattern project_tree_*.txt
- Nasconde i file "versionati" con timestamp (_YYYYMMDD_HHMMSS.*) e lascia solo i '*_latest.*'
- (Opzionale) Usa le regole del .gitignore tramite 'pathspec' (se installato)
- 'outputs/' è incluso di default anche se gitignore lo esclude (forzato via --force-include)

Installazione opzionale per .gitignore:
  pip install pathspec
"""

from __future__ import annotations
import argparse
import re
from pathlib import Path

# Prova ad usare pathspec per interpretare .gitignore
try:
    import pathspec  # type: ignore
except Exception:  # pragma: no cover
    pathspec = None  # fallback senza .gitignore

# Dir escluse di default (case-insensitive sul nome)
DEFAULT_EXCLUDE_DIRS = {".git", ".venv", "__pycache__", ".vscode", "cache"}

# File esclusi (match per nome esatto)
DEFAULT_EXCLUDE_FILES = {
    ".gitignore",
    "__init__.py",
    "make_project_tree.py",
    "project_tree.txt",
}

# Pattern di nomi file da escludere (regex)
EXCLUDE_NAME_PATTERNS = [
    re.compile(r"^project_tree_.*\.txt$", re.IGNORECASE),  # es. project_tree_20250914_105417.txt
]

# File "versionati" con timestamp, es: foo_20250914_105417.csv
VERSIONED_RE = re.compile(r".*_\d{8}_\d{6}\.[^.]+$", re.IGNORECASE)
# File "latest", es: foo_latest.csv
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
    """True se p è ignorato da .gitignore; False altrimenti o se pathspec non disponibile.
       Se p (o un suo antenato) è tra i force_include, NON viene ignorato."""
    if not pathspec or not GITIGNORE_SPEC:
        return False
    parts = p.resolve().relative_to(ROOT_PATH).parts  # type: ignore[arg-type]
    if parts and parts[0] in force_include:
        return False
    rel = _rel_posix(p)
    return GITIGNORE_SPEC.match_file(rel)

def _filtered_children(dir_path: Path, exclude_dirs_ci: set[str], exclude_files_exact: set[str],
                       force_include: set[str]) -> list[Path]:
    """Filtra figli: rimuove dir/file esclusi, applica .gitignore (se attivo),
       scarta i timestamp e tiene i *_latest.*."""
    items: list[Path] = []
    dirs: list[Path] = []
    files: list[Path] = []

    for child in dir_path.iterdir():
        name = child.name

        # Escludi lo script stesso
        if SELF_PATH is not None:
            try:
                if child.resolve() == SELF_PATH:
                    continue
            except FileNotFoundError:
                pass

        # Applica .gitignore (se attivo) — con override via --force-include
        if _is_gitignored(child, force_include):
            continue

        if child.is_dir():
            if name.lower() in exclude_dirs_ci:
                continue
            dirs.append(child)
        else:
            if name in exclude_files_exact or _name_matches_any_pattern(name):
                continue
            files.append(child)

    # Regole versioning: scarta i timestamp, tieni *_latest.* e file normali
    kept_files: list[Path] = []
    for f in files:
        fname = f.name
        if _is_versioned(fname) and not _is_latest(fname):
            continue
        kept_files.append(f)

    # Ordina: directory poi file
    items.extend(sorted(dirs, key=lambda x: x.name.lower()))
    items.extend(sorted(kept_files, key=lambda x: x.name.lower()))
    return items

def _walk(root: Path, prefix: str, lines: list[str], dir_suffix: str,
          exclude_dirs_ci: set[str], exclude_files_exact: set[str], force_include: set[str]):
    children = _filtered_children(root, exclude_dirs_ci, exclude_files_exact, force_include)
    n = len(children)
    for i, ch in enumerate(children):
        is_last = (i == n - 1)
        branch = "└── " if is_last else "├── "
        if ch.is_dir():
            lines.append(f"{prefix}{branch}{ch.name}{dir_suffix}")
            _walk(ch, prefix + ("    " if is_last else "│   "), lines, dir_suffix,
                  exclude_dirs_ci, exclude_files_exact, force_include)
        else:
            lines.append(f"{prefix}{branch}{ch.name}")

def _load_gitignore(root: Path):
    """Carica .gitignore come pathspec; ritorna None se non disponibile."""
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
    ap.add_argument("root", nargs="?", default=".", help="Cartella root (default: .)")
    ap.add_argument("--out", default="project_tree.txt", help="Nome file output")
    ap.add_argument("--include-root", action="store_true",
                    help='Mostra la riga di root (".\\") come testa dell\'albero')
    ap.add_argument("--dir-suffix", default="\\",
                    help='Suffisso directory (default: "\\", usa "/" se preferisci)')
    ap.add_argument("--no-gitignore", action="store_true",
                    help="Non usare .gitignore anche se presente")
    ap.add_argument("--force-include", nargs="*", default=["outputs"],
                    help="Directory top-level da includere comunque (default: ['outputs'])")
    ap.add_argument("--extra-exclude-dirs", nargs="*", default=[],
                    help="Directory aggiuntive da escludere (nomi esatti)")
    ap.add_argument("--extra-exclude-files", nargs="*", default=[],
                    help="File aggiuntivi da escludere (nomi esatti)")
    args = ap.parse_args()

    # Percorso dello script e root
    try:
        SELF_PATH = Path(__file__).resolve()
    except NameError:
        SELF_PATH = None

    ROOT_PATH = Path(args.root).resolve()

    # Carica .gitignore se richiesto e disponibile
    if not args.no_gitignore:
        GITIGNORE_SPEC = _load_gitignore(ROOT_PATH)
        if GITIGNORE_SPEC is None and pathspec is None:
            print("[INFO] 'pathspec' non installato: .gitignore non verrà applicato "
                  "(pip install pathspec per abilitarlo).")

    # Esclusioni base + extra
    exclude_dirs_ci = {d.lower() for d in DEFAULT_EXCLUDE_DIRS}
    exclude_dirs_ci.update({d.lower() for d in args.extra_exclude_dirs})
    exclude_files_exact = set(DEFAULT_EXCLUDE_FILES)
    exclude_files_exact.update(args.extra_exclude_files)
    force_include = set(args.force_include)

    lines: list[str] = []
    if args.include_root:
        lines.append(f".{args.dir_suffix}")

    _walk(ROOT_PATH, "" if not args.include_root else "", lines, args.dir_suffix,
          exclude_dirs_ci, exclude_files_exact, force_include)

    out_path = ROOT_PATH / args.out
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Creato {out_path} ({len(lines)} righe).")

if __name__ == "__main__":
    main()
