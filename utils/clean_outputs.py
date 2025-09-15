#!/usr/bin/env python3
# ------------------------------------------------------------
# COMANDI RAPIDI (da lanciare dal root della repo):
#   1) Dry-run (NON cancella niente, solo anteprima):
#      python -m utils.clean_outputs
    #   2) Applica la pulizia (cancella i file timestampati):
    #      python -m utils.clean_outputs --apply
#      # Opzioni utili: --prune-empty-dirs  --include-archives
# ------------------------------------------------------------

"""
Pulisce la cartella 'outputs/' eliminando i file "versionati" con timestamp
(es: foo_YYYYMMDD_HHMMSS.ext), mantenendo i '*_latest.*' e i file non-timestamp.

- Dry-run di default: mostra cosa verrebbe cancellato
- Cancella davvero solo con: --apply
- Opzionale: --prune-empty-dirs per rimuovere le directory rimaste vuote
- Per default NON tocca 'archives/' (per prudenza). Usa --include-archives per pulire anche lì.

Esempi:
  python utils/clean_outputs.py
  python utils/clean_outputs.py --apply --prune-empty-dirs
  python -m utils.clean_outputs --root outputs --include-archives
"""
from __future__ import annotations
import argparse
import re
from pathlib import Path

# Riconosce file "versionati" con timestamp finale (es: foo_20250914_105417.csv)
VERSIONED_RE = re.compile(r".*_\d{8}_\d{6}\.[^.]+$", re.IGNORECASE)
# Riconosce file "latest" (es: foo_latest.csv)
LATEST_RE = re.compile(r".*_latest\.[^.]+$", re.IGNORECASE)

# Directory che per prudenza NON si toccano a meno di flag esplicito
SAFE_DIR_NAMES = {"archives"}

def is_versioned(path: Path) -> bool:
    return bool(VERSIONED_RE.match(path.name))

def is_latest(path: Path) -> bool:
    return bool(LATEST_RE.match(path.name))

def should_skip_dir(d: Path, include_archives: bool) -> bool:
    name = d.name.lower()
    if not include_archives and name in SAFE_DIR_NAMES:
        return True
    return False

def collect_deletions(root: Path, include_archives: bool) -> list[Path]:
    to_delete: list[Path] = []
    if not root.exists():
        return to_delete
    for p in root.rglob("*"):
        if p.is_dir():
            # opzionalmente salta intere dir "archives/"
            if should_skip_dir(p, include_archives):
                pass
            continue
        # Solo file: elimina quelli timestampati che NON sono latest
        if is_versioned(p) and not is_latest(p):
            # se è dentro una dir archives/ ed include_archives=False, salta
            if not include_archives and any(part.lower() in SAFE_DIR_NAMES for part in p.parts):
                continue
            to_delete.append(p)
    return to_delete

def prune_empty_dirs(root: Path, include_archives: bool) -> list[Path]:
    pruned: list[Path] = []
    # cammina bottom-up
    for d in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda x: len(x.parts), reverse=True):
        if should_skip_dir(d, include_archives):
            continue
        try:
            if not any(d.iterdir()):
                d.rmdir()
                pruned.append(d)
        except OSError:
            # non vuota o permessi mancanti
            pass
    return pruned

def main():
    ap = argparse.ArgumentParser(description="Pulisce 'outputs/' mantenendo solo i file *_latest.*")
    ap.add_argument("--root", default="outputs", help="Cartella da pulire (default: outputs)")
    ap.add_argument("--apply", action="store_true", help="Esegue davvero la cancellazione (altrimenti dry-run)")
    ap.add_argument("--prune-empty-dirs", action="store_true", help="Elimina le directory rimaste vuote")
    ap.add_argument("--include-archives", action="store_true", help="Pulisce anche dentro 'archives/'")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.exists() or not root.is_dir():
        print(f"[ERRORE] Cartella non trovata: {root}")
        return

    deletions = collect_deletions(root, include_archives=args.include_archives)

    if not deletions:
        print(f"Nessun file da rimuovere in {root} (dry-run).")
    else:
        print(f"Trovati {len(deletions)} file da rimuovere:")
        for p in deletions:
            print("  -", p.relative_to(root))

    if args.apply:
        for p in deletions:
            try:
                p.unlink()
            except OSError as e:
                print(f"[WARN] Impossibile rimuovere {p}: {e}")
        print(f"Rimossi {len(deletions)} file.")

        if args.prune_empty_dirs:
            pruned = prune_empty_dirs(root, include_archives=args.include_archives)
            if pruned:
                print(f"Eliminate {len(pruned)} directory vuote:")
                for d in pruned:
                    print("  -", d.relative_to(root))
            else:
                print("Nessuna directory vuota da eliminare.")
    else:
        print("\nDry-run completato. Aggiungi --apply per confermare la cancellazione.")

if __name__ == "__main__":
    main()
