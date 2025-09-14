# =============================================
# PTCGP – Refactor D1: scraper + cache + routing
# Target tree (already agreed)
# ptcgp_ranking/
#   config/{config.yaml, alias_map.json}
#   scraper/{browser.py, session.py, decklist.py, matchups.py}
#   core/{...}
#   utils/{io.py, log.py, parse.py, timeit.py}
#   outputs/...  (Decklists, MatchupData, Matrices)
#   cache/requests
#   logs/
# =============================================

# ──────────────────────────────────────────────────────────────────────────────
# utils/io.py — routing & versioned writes (CSV + plots)
# ──────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
import json, hashlib, time
import pandas as pd

# Minimal logger (fallback); in runtime you likely have a global logger already.
import logging
log = logging.getLogger("ptcgp") if logging.getLogger("ptcgp").handlers else logging.getLogger(__name__)

# Base dirs (call init_paths at program start)
@dataclass
class Paths:
    base: Path
    outputs: Path
    cache: Path
    logs: Path


def init_paths(base_dir: Path) -> Paths:
    """
    Crea (idempotentemente) la struttura minima di cartelle usata dal progetto.
    """
    base = Path(base_dir)
    out = base / "outputs"
    cache = base / "cache" / "requests"
    logs = base / "logs"
    # Create required dirs idempotently
    for d in [
        out / "Decklists" / "raw",
        out / "Decklists" / "top_meta",
        out / "MatchupData" / "raw",
        out / "MatchupData" / "flat",
        out / "Matrices" / "winrate",
        out / "Matrices" / "volumes",
        out / "Matrices" / "heatmap",
        out / "RankingData" / "MARS",             # ← NEW
        out / "RankingData" / "MARS" / "archives",# ← NEW
        cache,
        logs,
    ]:
        d.mkdir(parents=True, exist_ok=True)

    return Paths(base=base, outputs=out, cache=cache, logs=logs)


# ---- routing
ROUTES = {
    # CSV contract (esistenti)
    "decklist_raw": ("Decklists", "raw"),
    "top_meta_decklist": ("Decklists", "top_meta"),
    "matchup_score_table": ("MatchupData", "flat"),
    "filtered_wr": ("Matrices", "winrate"),
    "n_dir": ("Matrices", "volumes"),

    # (esistente) top-meta già post-alias
    "top_meta_post_alias": ("Decklists", "top_meta"),

    # Plots
    "heatmap_topN": ("Matrices", "heatmap"),

    # ── MARS outputs (SNELLITI) ───────────────────────────────────────────────
    # Unico route ammesso: SOLO il ranking
    "mars_ranking": ("RankingData", "MARS"),
}


def _dest(paths: Paths, prefix: str) -> Path:
    """
    Risolve il percorso di destinazione per un dato 'prefix' di ROUTES.
    """
    if prefix not in ROUTES:
        log.warning("[route] Prefix sconosciuto '%s' — invio a outputs/", prefix)
        return paths.outputs
    top, sub = ROUTES[prefix]
    return paths.outputs / top / sub


def _run_stamp() -> str:
    """
    Timestamp run-wide in formato YYYYmmdd_HHMMSS.
    """
    return time.strftime("%Y%m%d_%H%M%S")


# Simple content hash helper (to detect changes of dataframe content)
def _df_content_hash(df: pd.DataFrame) -> str:
    """
    Hash stabile del contenuto di un DataFrame (CSV bytes senza index).
    Utile per decidere se scrivere una copia versionata.
    """
    as_csv = df.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(as_csv).hexdigest()[:16]


# ---- CSV writer: always update *_latest.csv, and add timestamped copy when changed == True
def write_csv_versioned(
    df: pd.DataFrame,
    base_dir: Path,
    prefix: str,
    *,
    changed: bool,
    index: bool = False
) -> Path:
    """
    Scrive sempre <prefix>_latest.csv e, se changed=True, aggiunge anche
    una copia versionata <prefix>_<timestamp>.csv.
    Ritorna il Path del file scritto più recentemente (versionato se created, altrimenti latest).
    """
    dest_dir = Path(base_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    latest_path = dest_dir / f"{prefix}_latest.csv"
    df.to_csv(latest_path, index=index, encoding="utf-8")
    log.info("CSV aggiornato: %s", latest_path)

    if changed:
        ts = _run_stamp()
        ts_path = dest_dir / f"{prefix}_{ts}.csv"
        df.to_csv(ts_path, index=index, encoding="utf-8")
        log.info("CSV versionato (changed=True): %s", ts_path)
        return ts_path

    return latest_path


# ---- plot writers ------------------------------------------------------------

def save_plot_timestamped(fig, base_dir: Path, prefix: str, *, fmt: str = "png", dpi: int = 300) -> Path:
    """
    Salva SEMPRE un'immagine con timestamp: <prefix>_<timestamp>.<fmt>
    (Manteniamo per retro-compatibilità.)
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    ts = _run_stamp()
    path = base_dir / f"{prefix}_{ts}.{fmt}"
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    # Abbassiamo a DEBUG per evitare “doppio output” in notebook
    log.debug("Plot salvato (timestamped): %s", path)
    return path


def save_plot_dual(fig, base_dir: Path, prefix: str, tag: str, *, fmt: str = "png", dpi: int = 300) -> tuple[Path, Path]:
    """
    Salva DUE copie del plot:
      1) <prefix>_latest.<fmt>              (sovrascritto ad ogni run)
      2) <prefix>_<tag>_<timestamp>.<fmt>   (versionato con timestamp)

    Esempio:
      save_plot_dual(fig, out_dir, "wr_heatmap", tag="T15")
      → wr_heatmap_latest.png
        wr_heatmap_T15_20250101_120000.png

    Ritorna (ts_path, latest_path).
    """
    base_dir = Path(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    ts = _run_stamp()
    ts_path = base_dir / f"{prefix}_{tag}_{ts}.{fmt}"
    latest_path = base_dir / f"{prefix}_latest.{fmt}"

    fig.savefig(ts_path, dpi=dpi, bbox_inches="tight")
    fig.savefig(latest_path, dpi=dpi, bbox_inches="tight")

    # ↓ abbassa a DEBUG per evitare il “doppio” log insieme a quello del notebook
    log.info("Plot salvato (timestamp + latest): %s | latest: %s", ts_path, latest_path)
    return ts_path, latest_path

